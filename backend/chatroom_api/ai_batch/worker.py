"""FIFO SQS handler that advances one accepted AI-only turn."""

from __future__ import annotations

import logging
from uuid import uuid4

from chatroom_api import config
from chatroom_api.ai_batch import store
from chatroom_api.ai_batch.contracts import (
    BATCH_EXECUTABLE_STATUSES,
    CONVERSATION_TERMINAL_STATUSES,
    WORKER_LEASE_MS,
    parse_queue_body,
    turn_write_id,
)
from chatroom_api.ai_batch.scheduler import candidates_for_turn, forced_candidate
from chatroom_api.bedrock_client import BedrockInferenceError, invoke_speak_tool
from chatroom_api.conversation import build_bedrock_messages
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


def _history_block(events: list[dict]) -> str:
    lines = []
    for event in events:
        if event.get("type") != "message":
            continue
        lines.append(
            f"> {event.get('sender') or 'Participant'}: {event.get('content') or ''}"
        )
    return "\n".join(lines) if lines else "(empty)"


def _trigger_message(*, require_message: bool, correction: bool = False) -> dict:
    if correction:
        text = (
            "Your previous output exceeded the configured message length. "
            "Send exactly one shorter non-empty message now."
        )
    elif require_message:
        text = "Continue the conversation now with exactly one non-empty message."
    else:
        text = (
            "Decide whether you should contribute the next message. Call speak "
            "with one message, or an empty array to stay silent."
        )
    return {"role": "user", "content": [{"text": text}]}


def _build_request(
    conversation: dict,
    setting: dict,
    participant: dict,
    history: list[dict],
    *,
    require_message: bool,
    correction: bool = False,
) -> tuple[str, float, list[dict], list[dict]]:
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
    if correction or not messages or messages[-1]["role"] == "assistant":
        messages = [*messages, _trigger_message(
            require_message=require_message,
            correction=correction,
        )]
    return model_id, float(temperature), system, messages


def _invoke_candidate(
    batch: dict,
    conversation: dict,
    participant: dict,
    history: list[dict],
    *,
    require_message: bool,
    attempt: int,
) -> str | None:
    owner_id = str(batch["owner_id"])
    if not credits_allow(owner_id):
        raise TerminalConversationError("insufficient credits")
    setting = dict(batch["settings_snapshot"])
    max_chars = int(setting["max_message_chars"])
    model_id, temperature, system, messages = _build_request(
        conversation,
        setting,
        participant,
        history,
        require_message=require_message,
    )
    invoked_at = store.now_ms()
    invocation_id = uuid4().hex
    result = invoke_speak_tool(
        model_id,
        system,
        messages,
        temperature=temperature,
        require_message=require_message,
        max_message_chars=max_chars,
        max_messages=1,
    )
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
        },
    )
    if not resolved_messages:
        if require_message:
            raise TerminalConversationError("model returned silence when a message was required")
        return None
    if len(resolved_messages) != 1:
        raise TerminalConversationError("model returned more than one message")
    text = resolved_messages[0]
    if len(text) <= max_chars:
        return text

    if not credits_allow(owner_id):
        raise TerminalConversationError("insufficient credits before correction")
    model_id, temperature, system, messages = _build_request(
        conversation,
        setting,
        participant,
        history,
        require_message=True,
        correction=True,
    )
    correction_at = store.now_ms()
    correction_id = uuid4().hex
    corrected = invoke_speak_tool(
        model_id,
        system,
        messages,
        temperature=temperature,
        require_message=True,
        max_message_chars=max_chars,
        max_messages=1,
    )
    corrected_messages = [
        str(message).strip()
        for message in (corrected.get("messages") or [])
        if str(message).strip()
    ]
    record_bedrock_usage(
        usage_event_id=correction_id,
        owner_id=owner_id,
        chatroom_id=str(batch["chatroom_id"]),
        conversation_id=conversation["conversation_id"],
        ai_participant_id=participant_id(participant) or "",
        model_id=model_id,
        result=corrected,
        invoked_at_ms=correction_at,
        extra_raw_usage={
            "ai_batch": True,
            "batch_job_id": batch["batch_job_id"],
            "expected_turn": int(conversation["next_turn"]),
            "candidate_attempt": attempt,
            "length_correction": True,
            "messages_count": len(corrected_messages),
            "temperature": temperature,
        },
    )
    if len(corrected_messages) != 1 or len(corrected_messages[0]) > max_chars:
        raise TerminalConversationError("model exceeded message length after correction")
    return corrected_messages[0]


def _choose_message(batch: dict, conversation: dict, history: list[dict]) -> tuple[dict, str]:
    participants = list(conversation.get("participants") or [])
    candidates = candidates_for_turn(
        participants,
        conversation.get("last_speaker_id"),
    )
    if len(participants) == 2:
        participant = candidates[0]
        text = _invoke_candidate(
            batch,
            conversation,
            participant,
            history,
            require_message=True,
            attempt=1,
        )
        if text is None:
            raise TerminalConversationError("two-AI turn produced no message")
        return participant, text

    for attempt, participant in enumerate(candidates, start=1):
        text = _invoke_candidate(
            batch,
            conversation,
            participant,
            history,
            require_message=False,
            attempt=attempt,
        )
        if text is not None:
            return participant, text
    participant = forced_candidate(candidates)
    text = _invoke_candidate(
        batch,
        conversation,
        participant,
        history,
        require_message=True,
        attempt=len(candidates) + 1,
    )
    if text is None:
        raise TerminalConversationError("forced AI turn produced no message")
    return participant, text


def _process_work(body: dict, receive_count: int = 1) -> dict:
    if not config.AI_BATCH_ENABLED:
        raise RuntimeError("AI batch runtime is disabled")
    batch_id = str(body["batch_job_id"])
    conv_id = str(body["conversation_id"])
    expected_turn = int(body["expected_turn"])
    batch = store.get_batch(batch_id)
    conversation = store.get_conversation(conv_id)
    if batch is None or conversation is None:
        raise TerminalConversationError("batch or conversation not found")
    if conversation.get("batch_job_id") != batch_id:
        raise TerminalConversationError("conversation does not belong to batch")
    if conversation.get("status") in CONVERSATION_TERMINAL_STATUSES:
        store.finalize_batch_if_done(batch_id)
        return {"status": "noop", "reason": "terminal"}

    current_turn = int(conversation.get("next_turn", 0) or 0)
    if expected_turn < current_turn:
        store.send_work(batch_id, conv_id, current_turn)
        return {"status": "requeued", "expected_turn": current_turn}
    if expected_turn > current_turn:
        return {"status": "noop", "reason": "future_turn"}

    now_value_ms = store.now_ms()
    if now_value_ms >= int(batch["deadline_at"]):
        store.mark_batch_timed_out(batch_id, now_value_ms)
        store.mark_conversation_terminal(conversation, "timed_out", error="batch deadline elapsed")
        return {"status": "timed_out"}
    if batch.get("status") not in BATCH_EXECUTABLE_STATUSES:
        if batch.get("status") == "timed_out":
            store.mark_conversation_terminal(conversation, "timed_out", error="batch deadline elapsed")
        return {"status": "noop", "reason": "batch_not_executable"}

    setting = dict(batch["settings_snapshot"])
    max_turns = int(setting["max_turns"])
    max_total_chars = int(setting["max_total_chars"])
    if current_turn >= max_turns or int(conversation.get("total_chars", 0) or 0) >= max_total_chars:
        store.mark_conversation_terminal(conversation, "completed")
        store.finalize_batch_if_done(batch_id)
        return {"status": "completed"}

    lease_id = uuid4().hex
    if not store.acquire_worker_lease(
        batch_id,
        conversation,
        expected_turn,
        lease_id,
        now_value_ms,
        WORKER_LEASE_MS,
    ):
        return {"status": "leased"}
    conversation = {**conversation, "status": "running", "worker_lease_id": lease_id}

    try:
        history = store.query_history(conv_id)
        participant, text = _choose_message(batch, conversation, history)
        next_message_count = int(conversation.get("message_count", 0) or 0) + 1
        next_total_chars = int(conversation.get("total_chars", 0) or 0) + len(text)
        terminal = (
            "completed"
            if next_message_count >= max_turns or next_total_chars >= max_total_chars
            else None
        )
        timestamp = store.now_ms()
        store.commit_turn(
            conversation,
            lease_id,
            {
                "type": "message",
                "sender": participant.get("nickname") or "Participant",
                "role": "ai",
                "ai_participant_id": participant_id(participant),
                "internal_name": participant.get("internal_name"),
                "avatar": participant.get("avatar"),
                "content": text,
                "timestamp": timestamp,
                "created_at": store.now_iso(),
                "batch_job_id": batch_id,
                "turn_number": current_turn,
                "turn_write_id": turn_write_id(conv_id, current_turn),
            },
            terminal_status=terminal,
        )
    except BedrockInferenceError as exc:
        if exc.retryable and receive_count < 5:
            store.release_worker_lease(conv_id, lease_id, str(exc))
            raise
        store.mark_conversation_terminal(
            conversation,
            "failed",
            error=str(exc),
            expected_lease_id=lease_id,
        )
        store.finalize_batch_if_done(batch_id)
        if exc.retryable:
            raise
        return {"status": "failed", "error": exc.error_type}
    except TerminalConversationError as exc:
        store.mark_conversation_terminal(
            conversation,
            "failed",
            error=str(exc),
            expected_lease_id=lease_id,
        )
        store.finalize_batch_if_done(batch_id)
        return {"status": "failed", "error": str(exc)}
    except Exception as exc:
        store.release_worker_lease(conv_id, lease_id, str(exc))
        raise

    if terminal:
        store.finalize_batch_if_done(batch_id)
        return {"status": "completed", "turn": current_turn}
    store.send_work(batch_id, conv_id, current_turn + 1)
    return {"status": "running", "turn": current_turn}


def lambda_handler(event: dict, context=None) -> dict:
    failures = []
    results = []
    for record in event.get("Records", []):
        message_id = record.get("messageId", "")
        try:
            body = parse_queue_body(
                record.get("body", ""),
                ("batch_job_id", "conversation_id", "expected_turn"),
            )
            receive_count = int(
                (record.get("attributes") or {}).get("ApproximateReceiveCount", "1")
            )
            results.append(_process_work(body, receive_count))
        except Exception:
            logger.exception("AI batch worker failed")
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures, "results": results}
