"""Checkpointed conversation slices, invoked by Step Functions Standard."""

from __future__ import annotations

import logging
import time
from uuid import uuid4

from chatroom_api import config
from chatroom_api.ai_batch import store
from chatroom_api.ai_batch.contracts import (
    BATCH_EXECUTABLE_STATUSES,
    WORKER_SLICE_SECONDS,
    WORKER_CALL_HEADROOM_MS,
    turn_write_id,
)
from chatroom_api.ai_batch.scheduler import candidates_for_turn, forced_candidate, next_decision
from chatroom_api.ai_batch.completion import InferenceDecision, early_completion_enabled, limit_reason
from chatroom_api.bedrock_client import BedrockInferenceError, invoke_speak_tool, log_speak_result
from chatroom_api.conversation import build_bedrock_messages
from chatroom_api.diagnostics import is_inference_diagnostic
from chatroom_api.prompt_attachments import attach_for_inference
from chatroom_api.inference_usage import credits_allow, record_bedrock_usage
from chatroom_api.participants import participant_id
from chatroom_api.prompts.construction import (
    build_bedrock_cache_prefix_message,
    build_bedrock_system_blocks,
    prepend_cache_prefix_message,
    supports_bedrock_prompt_cache,
)
from chatroom_api.settings import normalize_temperature


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class TerminalConversationError(RuntimeError):
    pass


class ConversationDeadlineReached(RuntimeError):
    pass


class WorkerSliceComplete(RuntimeError):
    pass


class ConversationAlreadyTerminal(RuntimeError):
    pass


def _check_deadline(batch: dict) -> None:
    if store.now_ms() >= int(batch["deadline_at"]):
        raise ConversationDeadlineReached()


def _history_block(events: list[dict]) -> str:
    lines = []
    for event in events:
        if event.get("type") != "message":
            continue
        lines.append(
            f"> {event.get('sender') or 'Participant'}: {event.get('content') or ''}"
        )
    return "\n".join(lines) if lines else "(empty)"


def _trigger_message(*, require_message: bool, model_id: str = '', allow_agreement: bool = False) -> dict:
    """Current-call restrictions are dynamic, outside the shared cache prefix."""
    from chatroom_api.prompts.speech_protocol import uses_json_speech
    if allow_agreement:
        speech = 'return {"messages":["Your contribution"]}' if uses_json_speech(model_id) else 'call speak with one message'
        agree = 'return {"action":"agree_to_end"}' if uses_json_speech(model_id) else 'call agreeToEnd with no arguments'
        text = (f'Review the latest history. If you have a substantive contribution, {speech}. '
                f'If you have no further contribution and are willing to end, {agree}. '
                'Choose independently; ending does not require consensus. Return exactly one action. ')
        text += ('Temporary silence is not allowed on this call.' if require_message else
                 'You may instead return an empty messages array to wait temporarily, which is not consent to end.')
        return {'role': 'user', 'content': [{'text': text}]}
    if require_message:
        text = "Continue the conversation now with exactly one non-empty message."
    else:
        text = (
            "Decide whether you should contribute the next message. Call speak "
            "with one message, or an empty array to stay silent."
        )
    if uses_json_speech(model_id):
        text = text.replace('Call speak with one message, or an empty array to stay silent.',
                            'Return {"messages": ["Your message"]}, or {"messages": []} to stay silent.')
        if require_message:
            text += ' Return only a JSON object with exactly one non-empty string in its messages array.'
    return {"role": "user", "content": [{"text": text}]}


def _build_request(
    conversation: dict,
    setting: dict,
    participant: dict,
    history: list[dict],
    *,
    require_message: bool,
    allow_agreement: bool = False,
) -> tuple[str, float, list[dict], list[dict]]:
    """Assemble the real per-participant request, shared by workers/local probes."""
    ai_id = participant_id(participant)
    if not ai_id:
        raise TerminalConversationError("AI participant has no identity")
    model_id = str(
        participant.get("model_id") or setting.get("model_id") or ""
    ).strip()
    if not model_id:
        raise TerminalConversationError("AI participant has no model")
    temperature = normalize_temperature(
        participant.get("temperature"),
        default=normalize_temperature(setting.get("temperature"), default=0.7),
    )
    if temperature is None:
        temperature = 0.7

    history = [event for event in history if not is_inference_diagnostic(event)]
    runtime_conv = {**conversation, "events": history}
    history_text = _history_block(history)
    nicknames = [
        item.get("nickname")
        for item in conversation.get("participants", [])
        if item.get("nickname")
    ]
    system = build_bedrock_system_blocks(
        "ai_only",
        setting,
        participant.get("persona", "") or "",
        participant.get("nickname") or "Participant",
        history_text,
        model_id=model_id,
        participant_nicknames=nicknames,
        require_response=require_message,
    )
    messages = build_bedrock_messages(runtime_conv, ai_id, store.now_ms())
    # The first speaker's own history starts with assistant. Gemma and other
    # strict chat templates reject that even though subsequent roles alternate.
    # This request-only frame is not a participant message or a consent signal.
    if messages and messages[0]['role'] == 'assistant':
        messages = [{'role': 'user', 'content': [{'text': 'The discussion so far follows.'}]}, *messages]
    if supports_bedrock_prompt_cache(model_id):
        prefix = build_bedrock_cache_prefix_message(
            "ai_only",
            setting,
            participant.get("persona", "") or "",
            participant.get("nickname") or "Participant",
            history_text,
            participant_nicknames=nicknames,
            require_response=require_message,
        )
        messages = prepend_cache_prefix_message(messages, prefix)
    # Progress changes every accepted message and must stay after the cache prefix.
    trigger = _trigger_message(require_message=require_message, model_id=model_id, allow_agreement=allow_agreement)
    progress = (
        f"Conversation progress: {conversation.get('message_count', 0)}/{setting['max_turns']} messages; "
        f"{conversation.get('total_chars', 0)}/{setting['max_total_chars']} characters used."
    )
    if setting.get('max_message_chars') is not None:
        progress += f" Aim for at most {setting['max_message_chars']} characters in your message."
    trigger['content'].insert(0, {'text': progress})
    if messages and messages[-1]['role'] == 'user':
        messages = [*messages[:-1], {**messages[-1], 'content': [*messages[-1]['content'], *trigger['content']]}]
    else:
        messages = [*messages, trigger]
    messages = attach_for_inference(messages, setting, participant)
    return model_id, float(temperature), system, messages


def _invoke_candidate_once(
    batch: dict,
    conversation: dict,
    participant: dict,
    history: list[dict],
    *,
    require_message: bool,
    attempt: int,
    before_attempt=None,
    recovery_attempt=0,
    allow_agreement: bool = False,
) -> InferenceDecision:
    """Account every paid result before rejecting errors or late decisions."""
    owner_id = str(batch["owner_id"])
    if not credits_allow(owner_id):
        raise TerminalConversationError("insufficient credits")
    setting = dict(batch["settings_snapshot"])
    model_id, temperature, system, messages = _build_request(
        conversation,
        setting,
        participant,
        history,
        require_message=require_message,
        allow_agreement=allow_agreement,
    )
    invoked_at = store.now_ms()
    invocation_id = uuid4().hex
    result = invoke_speak_tool(
        model_id,
        system,
        messages,
        temperature=temperature,
        require_message=require_message,
        max_messages=1,
        before_attempt=before_attempt or (lambda: _check_deadline(batch)),
        allow_agreement=allow_agreement,
    )
    log_speak_result(result, model_id, conversation_id=conversation["conversation_id"],
                     ai_participant_id=participant_id(participant) or "",
                     recovery_attempt=recovery_attempt)
    resolved_messages = [
        str(message).strip()
        for message in (result.get("messages") or [])
        if str(message).strip()
    ]
    record_bedrock_usage(
        usage_event_id=invocation_id,
        owner_id=owner_id,
        chatroom_id=str(batch["chatroom_id"]),
        conversation_id=conversation["conversation_id"],
        ai_participant_id=participant_id(participant) or "",
        model_id=model_id,
        result=result,
        invoked_at_ms=invoked_at,
        extra_raw_usage={
            "ai_batch": True,
            "batch_job_id": batch["batch_job_id"],
            "expected_turn": int(conversation["next_turn"]),
            "candidate_attempt": attempt,
            "messages_count": len(resolved_messages),
            "temperature": temperature,
            "output_error": result.get("output_error"),
            "stop_reason": result.get("stop_reason"),
            "truncated": bool(result.get("truncated")),
            "recovery_attempt": recovery_attempt,
            "decision": result.get('outcome'),
        },
    )
    _check_deadline(batch)
    if result.get("output_error"):
        raise BedrockInferenceError(result["output_error"], "Invalid speak output", retryable=False)
    if result.get('outcome') == 'agree_to_end':
        if not allow_agreement or resolved_messages:
            raise TerminalConversationError('agreement not allowed on this call')
        return InferenceDecision('agree_to_end')
    if not resolved_messages:
        if require_message:
            raise TerminalConversationError("model returned silence when a message was required")
        return InferenceDecision('silence')
    if len(resolved_messages) != 1:
        raise TerminalConversationError("model returned more than one message")
    return InferenceDecision('speech', resolved_messages[0])


def _invoke_candidate(batch, conversation, participant, history, *, require_message,
                      attempt, before_attempt=None, allow_agreement=False):
    # Accept usable truncated speech as-is. Only an unrecoverable truncated tool
    # payload gets one recovery attempt; both attempts retain their usage records.
    for recovery_attempt in range(2):
        try:
            return _invoke_candidate_once(
                batch, conversation, participant, history,
                require_message=require_message, attempt=attempt,
                before_attempt=before_attempt, recovery_attempt=recovery_attempt,
                allow_agreement=allow_agreement,
            )
        except BedrockInferenceError as exc:
            if exc.error_type != "truncated_without_message" or recovery_attempt:
                raise


def _choose_message(batch: dict, conversation: dict, history: list[dict], *, before_attempt=None) -> tuple[dict, str]:
    participants = list(conversation.get("participants") or [])
    candidates = candidates_for_turn(
        participants,
        conversation.get("last_speaker_id"),
    )
    if len(participants) == 2:
        participant = candidates[0]
        decision = _invoke_candidate(
            batch,
            conversation,
            participant,
            history,
            require_message=True,
            attempt=1,
            before_attempt=before_attempt,
        )
        if decision.message is None:
            raise TerminalConversationError("two-AI turn produced no message")
        return participant, decision.message

    for attempt, participant in enumerate(candidates, start=1):
        decision = _invoke_candidate(
            batch,
            conversation,
            participant,
            history,
            require_message=False,
            attempt=attempt,
            before_attempt=before_attempt,
        )
        if decision.message is not None:
            return participant, decision.message
    participant = forced_candidate(candidates)
    decision = _invoke_candidate(
        batch,
        conversation,
        participant,
        history,
        require_message=True,
        attempt=len(candidates) + 1,
        before_attempt=before_attempt,
    )
    if decision.message is None:
        raise TerminalConversationError("forced AI turn produced no message")
    return participant, decision.message


def _process_work(body: dict, context=None) -> dict:
    """Advance accepted decisions until terminal or the next workflow slice.

    Enabled runs checkpoint silence/consent as well as speech. A slice boundary
    never restarts a random round; conflicts are retried by the workflow after
    reloading authoritative progress. Disabled runs retain the legacy policy.
    """
    if not config.AI_BATCH_ENABLED:
        raise RuntimeError("AI batch runtime is disabled")
    batch_id = str(body["batch_job_id"])
    conv_id = str(body["conversation_id"])
    batch = store.get_batch(batch_id)
    if batch is None:
        raise TerminalConversationError("batch not found")
    setting = dict(batch["settings_snapshot"])
    allow_early_completion = early_completion_enabled(setting)
    started = time.monotonic()

    def before_attempt():
        _check_deadline(batch)
        if store.get_conversation(conv_id).get("execution_state") == "terminal":
            raise ConversationAlreadyTerminal()
        if (time.monotonic() - started >= WORKER_SLICE_SECONDS
                or (context and context.get_remaining_time_in_millis() < WORKER_CALL_HEADROOM_MS)):
            raise WorkerSliceComplete()

    while True:
        conversation = store.get_conversation(conv_id)
        if not conversation or conversation.get("batch_job_id") != batch_id:
            raise TerminalConversationError("conversation does not belong to batch")
        if conversation.get("execution_state") == "terminal":
            store.finalize_batch_if_done(batch_id)
            return {"terminal": True, "outcome": conversation["outcome"]}
        terminal = None
        error = None
        reason = limit_reason(int(conversation['message_count']), int(conversation['total_chars']), setting)
        if store.now_ms() >= int(batch["deadline_at"]):
            terminal = "timed_out"
        elif batch.get("status") not in BATCH_EXECUTABLE_STATUSES:
            terminal, error = "failed", "batch_not_executable"
        elif reason:
            terminal = "completed"
        if terminal:
            if not store.mark_conversation_terminal(conversation, terminal, error=error,
                    completion_reason=reason if terminal == 'completed' else None):
                raise RuntimeError("terminal transition conflict")
            continue
        if (time.monotonic() - started >= WORKER_SLICE_SECONDS
                or (context and context.get_remaining_time_in_millis() < WORKER_CALL_HEADROOM_MS)):
            return {"terminal": False}
        if not store.start_conversation(batch_id, conversation):
            raise RuntimeError("start transition conflict")
        conversation = {**conversation, "status": "running", "execution_state": "running"}
        history = store.query_history(conv_id)
        try:
            if allow_early_completion:
                turn = next_decision(conversation)
                if turn.participant is None:
                    raise TerminalConversationError('all-agreed state was not committed as terminal')
                participant = turn.participant
                decision = _invoke_candidate(
                    batch, conversation, participant, history,
                    require_message=turn.require_message, allow_agreement=turn.allow_agreement,
                    attempt=int(conversation.get('state_version', 0)) + 1, before_attempt=before_attempt,
                )
            else:
                participant, text = _choose_message(batch, conversation, history, before_attempt=before_attempt)
                decision = InferenceDecision('speech', text)
            _check_deadline(batch)
        except WorkerSliceComplete:
            return {"terminal": False}
        except ConversationAlreadyTerminal:
            continue
        except ConversationDeadlineReached:
            if not store.mark_conversation_terminal(conversation, "timed_out"):
                raise RuntimeError("timeout transition conflict")
            continue
        except BedrockInferenceError as exc:
            if exc.retryable:
                raise
            terminal = "timed_out" if store.now_ms() >= int(batch["deadline_at"]) else "failed"
            if not store.mark_conversation_terminal(conversation, terminal, error=str(exc)):
                raise RuntimeError("failure transition conflict") from exc
            continue
        except TerminalConversationError as exc:
            terminal = "timed_out" if store.now_ms() >= int(batch["deadline_at"]) else "failed"
            if not store.mark_conversation_terminal(conversation, terminal, error=str(exc)):
                raise RuntimeError("failure transition conflict") from exc
            continue
        if decision.action != 'speech':
            if not store.commit_decision(conversation, participant_id(participant), decision.action):
                raise RuntimeError('decision transition conflict')
            continue
        text = decision.message
        next_message_count = int(conversation.get("message_count", 0) or 0) + 1
        next_total_chars = int(conversation.get("total_chars", 0) or 0) + len(text)
        reason = limit_reason(next_message_count, next_total_chars, setting)
        terminal = 'completed' if reason else None
        timestamp = store.now_ms()
        store.commit_turn(
            conversation,
            {
                "type": "message",
                "sender": participant.get("nickname") or "Participant",
                "role": "ai",
                "ai_participant_id": participant_id(participant),
                "internal_name": participant.get("internal_name"),
                "content": text,
                "timestamp": timestamp,
                "created_at": store.now_iso(),
                "batch_job_id": batch_id,
                "turn_number": int(conversation["next_turn"]),
                "turn_write_id": turn_write_id(conv_id, int(conversation["next_turn"])),
            },
            terminal_status=terminal,
            completion_reason=reason,
        )


def lambda_handler(event: dict, context=None) -> dict:
    return _process_work(event, context)
