"""Async-invoked Lambda — runs gate + Bedrock + DDB writes for one tick.

Implements the 5-step procedure in
``docs/low-level-design.md#tick-handler``:

1. **Idempotency guard.** Conditional update on ``last_tick_at``: skip if
   another tick fired within ``TICK_DEDUPE_WINDOW_MS``. The heartbeat
   container's at-least-once invocation, plus Lambda's own retry, both
   funnel through this guard.
2. **Max-duration check.** If ``now > started_at + max_duration_seconds``,
   flip ``status="ended"`` and append a system "conversation ended" event.
3. **Gate.** Pure ``run_gate(conv, now)`` decides whether some AI should
   speak. On skip, append a ``tick`` event recording the reason and exit.
4. **Bedrock.** Build the per-AI system prompt (SCAFFOLD + TOPIC + PERSONA
   + CONVERSATION_CONTEXT) plus the Bedrock messages array, then call
   ``invoke_speak_tool`` (which reuses the shared retry + error
   classification in ``bedrock_client.py``).
5. **Append tick + messages.** Stack typing delays to compute ``visible_at``
   for each AI bubble; record one ``tick`` event plus the message events;
   bump ``last_speak_at_by_session`` on a non-empty turn.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from chatroom_api import config
from chatroom_api.bedrock_client import (
    BedrockInferenceError,
    invoke_speak_tool,
)
from chatroom_api.constants import (
    IDLE_FOLLOW_UP_AFTER_MS,
    MIN_SILENCE_MS,
    TICK_DEDUPE_WINDOW_MS,
)
from chatroom_api.conversation import build_bedrock_messages
from chatroom_api.delays import compute_visible_at, pick_delays_ms
from chatroom_api.gate import run_gate
from chatroom_api.pricing import estimate_cost_usd, is_unknown_pricing_key
from chatroom_api.settings import (
    derive_runtime_mode,
    is_single_human_single_ai_assistant_room,
    normalize_temperature,
)
from chatroom_api.prompts.speech_scaffold import (
    REQUIRED_SPEAK_TOOL_CONFIG,
    SPEAK_TOOL_CONFIG,  # re-exported for callers that want to inspect it
    format_topic_block,
    get_scaffold_for_mode,
)

logger = logging.getLogger(__name__)

# Default model id used when a chatroom row doesn't carry one. Matches the
# value baked into the editor preset and ``experiment/group-poc.js``.
_DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-4-6"


def _invoke_with_model_fallback(
    model_id: str,
    system_prompt: str | list[dict],
    bedrock_messages: list[dict],
    *,
    temperature: float,
    require_message: bool = False,
) -> dict:
    """Invoke Bedrock, falling back to the default model for stale saved ids.

    Local dev and long-lived chatroom rows can carry model ids that have since
    been retired by the provider. If the configured model fails with
    ``ResourceNotFoundException``, retry once with the current default model so
    the chat loop keeps working while the saved config catches up.
    """
    try:
        result = invoke_speak_tool(
            model_id,
            system_prompt,
            bedrock_messages,
            temperature=temperature,
            require_message=require_message,
        )
        result["resolved_model_id"] = model_id
        return result
    except BedrockInferenceError as err:
        if err.error_type != "ResourceNotFoundException" or model_id == _DEFAULT_MODEL_ID:
            raise

        logger.warning(
            "Bedrock model %s is unavailable; falling back to %s",
            model_id,
            _DEFAULT_MODEL_ID,
        )
        result = invoke_speak_tool(
            _DEFAULT_MODEL_ID,
            system_prompt,
            bedrock_messages,
            temperature=temperature,
            require_message=require_message,
        )
        result["resolved_model_id"] = _DEFAULT_MODEL_ID
        return result


# ---------------------------------------------------------------------------
# Backend selectors (mirror auth.py / close_lobby.py).
# ---------------------------------------------------------------------------


def _get_db():
    if config.USE_MOCK_DYNAMO:
        from chatroom_api import mock_dynamo
        return mock_dynamo
    from chatroom_api import dynamo
    return dynamo


def _get_rds():
    from chatroom_api._providers import get_rds_provider
    return get_rds_provider()


# ---------------------------------------------------------------------------
# Time helpers.
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_to_ms(iso: str) -> int:
    """Best-effort ISO 8601 → epoch ms. Returns 0 on parse failure."""
    if not isinstance(iso, str):
        return 0
    try:
        # ``datetime.fromisoformat`` only accepts ``+00:00``, not ``Z``.
        return int(
            datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000
        )
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Event factories.
# ---------------------------------------------------------------------------


def _make_tick_event(
    now_ms: int,
    *,
    chosen_session_id: Optional[str] = None,
    gate_decision: str = "skip",
    skip_reason: Optional[str] = None,
    ai_decision: Optional[str] = None,
    bedrock_invoked: bool = False,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_write_input_tokens: int = 0,
    error: Optional[str] = None,
) -> dict:
    """Build a ``type="tick"`` event for the conversation audit trail.

    Tick events are filtered out by ``/chat/messages`` (see task 3.4) — they
    exist solely for researcher-facing audit via ``?include_ticks=true``.
    """
    return {
        "type": "tick",
        "session_id": None,
        "sender": "System",
        "role": "system",
        "content": "",  # tick events don't render to users
        "timestamp": now_ms,
        "visible_at": now_ms,
        "created_at": _now_iso(),
        "chosen_session_id": chosen_session_id,
        "gate_decision": gate_decision,
        "skip_reason": skip_reason,
        "ai_decision": ai_decision,
        "bedrock_invoked": bedrock_invoked,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cache_read_input_tokens": int(cache_read_input_tokens),
        "cache_write_input_tokens": int(cache_write_input_tokens),
        "error": error,
    }


def _utc_iso_from_ms(value_ms: int) -> str:
    """Render an epoch-millisecond value as second-precision UTC."""
    return (
        datetime.fromtimestamp(value_ms / 1000, timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _visible_message_events(conv: dict, now_ms: int) -> list[dict]:
    return [
        event
        for event in (conv.get("events", []) or [])
        if event.get("type") == "message"
        and int(event.get("visible_at", event.get("timestamp", 0)) or 0) <= now_ms
    ]


def _idle_follow_up_state(
    conv: dict,
    chatroom_setting: dict,
    now_ms: int,
) -> dict:
    """Describe the current unanswered single-AI turn."""
    if not is_single_human_single_ai_assistant_room(chatroom_setting):
        return {"eligible": False}

    messages = _visible_message_events(conv, now_ms)
    if not messages or messages[-1].get("role") not in {"ai", "assistant"}:
        return {"eligible": False}

    latest_human_index = -1
    for index, message in enumerate(messages):
        if message.get("role") in {"human", "user"}:
            latest_human_index = index
    unanswered_messages = messages[latest_human_index + 1:]
    follow_up_sent = any(
        message.get("message_kind") == "idle_follow_up"
        for message in unanswered_messages
    )
    latest = messages[-1]
    visible_at = int(
        latest.get("visible_at", latest.get("timestamp", 0)) or 0
    )
    return {
        "eligible": True,
        "idle_ms": max(0, now_ms - visible_at),
        "follow_up_sent": follow_up_sent,
    }


def _render_history_block(conv: dict, now_ms: int) -> str:
    """Render a textual ``<conversation-history>`` block for the system prompt.

    Mirrors ``experiment/group-poc.js renderHistoryBlock``: drops tick events,
    drops events with ``visible_at > now`` (so the AI sees what users see),
    formats absolute UTC and relative timestamps. Current time is included so
    the model does not need to infer the reference clock.
    """
    lines = [f"Current time (UTC): {_utc_iso_from_ms(now_ms)}"]
    chatroom_setting = conv.get("chatroom_setting") or {}
    idle_state = _idle_follow_up_state(conv, chatroom_setting, now_ms)
    if idle_state.get("eligible"):
        lines.extend([
            (
                "Single-AI unanswered time: "
                f"{round(int(idle_state['idle_ms']) / 1000)} seconds"
            ),
            (
                "Single-AI idle follow-up already sent since the latest human "
                f"message: {'yes' if idle_state['follow_up_sent'] else 'no'}"
            ),
        ])
    audit_types = {"tick", "lobby_created"}
    rendered_event = False
    for event in conv.get("events", []) or []:
        if event.get("type") in audit_types:
            continue
        visible_at = int(event.get("visible_at", event.get("timestamp", 0)) or 0)
        if visible_at > now_ms:
            continue
        rendered_event = True
        ago_sec = max(0, round((now_ms - visible_at) / 1000))
        timing = f"{_utc_iso_from_ms(visible_at)}; {ago_sec} sec ago"
        if event.get("type") == "system":
            lines.append(f"> [{timing}] System: {event.get('content', '')}")
        else:
            sender = event.get("sender") or "Participant"
            lines.append(f"> [{timing}] {sender}: {event.get('content', '')}")
    if not rendered_event:
        lines.append("(empty)")
    return "\n".join(lines)


def _build_static_prefix_block(
    mode: str,
    *,
    mimic_human: bool = True,
    require_response: bool = False,
) -> str:
    """Return the large static scaffold/examples block for this mode."""
    return get_scaffold_for_mode(
        mode,
        mimic_human=mimic_human,
        require_response=require_response,
    )


def _build_semi_static_setup_blocks(
    chatroom_setting: dict,
    persona: str,
    my_nickname: str,
    participant_nicknames: list[str] | None = None,
) -> list[str]:
    """Return the mostly-stable per-chatroom / per-AI setup blocks.

    This intentionally excludes the scaffold/examples block and the dynamic
    conversation-history block. The returned block order matches the current
    prompt shape so this refactor does not change model behavior yet.
    """
    parts: list[str] = []
    topic = format_topic_block(chatroom_setting.get("topic_instruction", ""))
    if topic:
        parts.append(topic)
    if persona:
        parts.append(f"<your-persona>\n{persona}\n</your-persona>")
    if participant_nicknames:
        listed = sorted(set(participant_nicknames))
        rendered = "\n".join(
            f"- {n} (you)" if n == my_nickname else f"- {n}"
            for n in listed
        )
        parts.append(f"<participants>\n{rendered}\n</participants>")
    if is_single_human_single_ai_assistant_room(chatroom_setting):
        parts.append(
            "<single-ai-idle-policy>\n"
            "The backend may ask you to decide whether to speak every few seconds. "
            "If your latest message has no human reply, normally stay silent for "
            "roughly 60 seconds. After that wait, send at most one brief, natural "
            "check-in that helps continue the conversation. If the check-in also "
            "gets no human reply, stay silent until the human speaks again.\n"
            "</single-ai-idle-policy>"
        )
    parts.append(f"<your-name>\n{my_nickname}\n</your-name>")
    return parts


def _build_dynamic_context_block(history_block: str) -> str:
    """Return the current dynamic context block.

    Phase 1 keeps the existing full visible history. Later phases can replace
    this with summary + recent window without changing the outer assembly path.
    """
    return f"<conversation-history>\n{history_block}\n</conversation-history>"


def _build_additional_prompt_block(chatroom_setting: dict) -> str:
    """Return the optional last-mile reminder block."""
    return (chatroom_setting.get("additional_prompt") or "").strip()


def _build_prompt_blocks(
    mode: str,
    chatroom_setting: dict,
    persona: str,
    my_nickname: str,
    history_block: str,
    participant_nicknames: list[str] | None = None,
    require_response: bool = False,
) -> dict[str, str | list[str]]:
    """Return explicit prompt segments for the current tick.

    This is the first step of the token-saver refactor: make the prompt
    structure explicit without changing prompt content yet.
    """
    return {
        "static_prefix": _build_static_prefix_block(
            mode,
            mimic_human=bool(chatroom_setting.get("mimic_human", True)),
            require_response=require_response,
        ),
        "semi_static_setup": _build_semi_static_setup_blocks(
            chatroom_setting,
            persona,
            my_nickname,
            participant_nicknames=participant_nicknames,
        ),
        "dynamic_context": _build_dynamic_context_block(history_block),
        "additional_prompt": _build_additional_prompt_block(chatroom_setting),
    }


_BEDROCK_PROMPT_CACHE_MODEL_IDS = frozenset({
    # Anthropic models listed by the Bedrock prompt-caching guide or their
    # current Bedrock model cards.
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "anthropic.claude-3-7-sonnet-20250219-v1:0",
    "anthropic.claude-opus-4-20250514-v1:0",
    "anthropic.claude-opus-4-5-20251101-v1:0",
    "anthropic.claude-opus-4-6-v1",
    "anthropic.claude-opus-4-7",
    "anthropic.claude-sonnet-4-20250514-v1:0",
    "anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-sonnet-4-6",
    "anthropic.claude-haiku-4-5-20251001-v1:0",
    # Nova text models currently offered by the editor. Bedrock documents
    # prompt caching for Nova text prompts, including explicit cache points.
    "amazon.nova-pro-v1:0",
    "amazon.nova-lite-v1:0",
    "amazon.nova-micro-v1:0",
    "amazon.nova-premier-v1:0",
    "amazon.nova-2-lite-v1:0",
})

_BEDROCK_INFERENCE_PROFILE_PREFIXES = frozenset({
    "global", "us", "eu", "apac", "jp", "au",
})


def _base_bedrock_model_id(model_id: str) -> str:
    """Remove a Bedrock cross-region inference-profile prefix, if present."""
    normalized = (model_id or "").strip()
    prefix, separator, remainder = normalized.partition(".")
    if separator and prefix in _BEDROCK_INFERENCE_PROFILE_PREFIXES:
        return remainder
    return normalized


def _supports_bedrock_prompt_cache(model_id: str) -> bool:
    """Return whether Bedrock prompt caching should be enabled for this model."""
    return _base_bedrock_model_id(model_id) in _BEDROCK_PROMPT_CACHE_MODEL_IDS


def _build_system_prompt(
    mode: str,
    chatroom_setting: dict,
    persona: str,
    my_nickname: str,
    history_block: str,
    participant_nicknames: list[str] | None = None,
    require_response: bool = False,
) -> str:
    """Assemble SCAFFOLD + TOPIC + PERSONA + PARTICIPANTS + CONVERSATION_CONTEXT + ADDITIONAL_PROMPT.

    Sections are joined with single newlines; each section already carries
    its own internal structure (the scaffold ends with reminders, persona is
    XML-tagged, history is XML-tagged). Empty optional sections (no persona,
    empty topic, missing participants, or no additional_prompt) are omitted
    to keep the prompt clean.

    The ``<participants>`` block lists every nickname in the room (without
    role markers — see the "AIs don't know who else is AI" rule in the
    LLD). Without this, an AI might never realize a participant exists if
    they never speak, defeating the inclusivity rules in the scaffold.

    ``additional_prompt`` lands AFTER the conversation history so that
    last-mile reminders (e.g. "stay one-thought-per-turn") are the most
    recent thing the model sees before deciding what to say.
    """
    blocks = _build_prompt_blocks(
        mode,
        chatroom_setting,
        persona,
        my_nickname,
        history_block,
        participant_nicknames=participant_nicknames,
        require_response=require_response,
    )
    parts: list[str] = [str(blocks["static_prefix"])]
    parts.extend(blocks["semi_static_setup"])
    parts.append(str(blocks["dynamic_context"]))
    additional = str(blocks["additional_prompt"])
    if additional:
        parts.append(additional)
    return "\n".join(parts)


def _build_bedrock_system_blocks(
    mode: str,
    chatroom_setting: dict,
    persona: str,
    my_nickname: str,
    history_block: str,
    *,
    model_id: str,
    participant_nicknames: list[str] | None = None,
    require_response: bool = False,
) -> list[dict]:
    """Return Bedrock system blocks.

    For cache-supported Claude tool-use calls, only the large static scaffold
    stays in ``system``. The cache checkpoint itself must live in
    ``messages``; putting it in ``system`` or ``tools`` did not produce cache
    hits in our Bedrock probes.
    """
    if not _supports_bedrock_prompt_cache(model_id):
        return [{
            "text": _build_system_prompt(
                mode,
                chatroom_setting,
                persona,
                my_nickname,
                history_block,
                participant_nicknames=participant_nicknames,
                require_response=require_response,
            )
        }]

    return [{
        "text": _build_static_prefix_block(
            mode,
            mimic_human=bool(chatroom_setting.get("mimic_human", True)),
            require_response=require_response,
        )
    }]


def _build_bedrock_cache_prefix_message(
    mode: str,
    chatroom_setting: dict,
    persona: str,
    my_nickname: str,
    history_block: str,
    *,
    participant_nicknames: list[str] | None = None,
    require_response: bool = False,
) -> dict:
    """Return the leading user message that carries the Bedrock cache point.

    The message content is:
    1. semi-static per-chatroom / per-AI setup blocks
    2. optional additional prompt
    3. the cache checkpoint
    4. the dynamic textual history block

    This shape preserves the existing prompt content while moving the cache
    checkpoint to the one place Bedrock tool-use calls actually honored in our
    live probes: a ``messages[*].content`` block.
    """
    blocks = _build_prompt_blocks(
        mode=mode,
        chatroom_setting=chatroom_setting,
        persona=persona,
        my_nickname=my_nickname,
        history_block=history_block,
        participant_nicknames=participant_nicknames,
        require_response=require_response,
    )
    content: list[dict] = []
    for block in blocks["semi_static_setup"]:
        content.append({"text": block})
    additional = str(blocks["additional_prompt"])
    if additional:
        content.append({"text": additional})
    content.append({"cachePoint": {"type": "default"}})
    content.append({"text": str(blocks["dynamic_context"])})
    return {"role": "user", "content": content}


def _prepend_cache_prefix_message(
    messages: list[dict],
    prefix_message: dict,
) -> list[dict]:
    """Prepend the cache prefix, merging into the first user message when possible."""
    if not messages:
        return [prefix_message]

    merged = [dict(m) for m in messages]
    if merged[0]["role"] == "user":
        merged[0] = {
            "role": "user",
            "content": list(prefix_message["content"]) + list(merged[0]["content"]),
        }
        return merged

    return [prefix_message, *merged]


def _build_tick_trigger_message(
    *,
    require_response: bool = False,
    idle_follow_up: bool = False,
) -> dict:
    """Return the thin user-side trigger appended when history ends on assistant.

    The trigger stays separate from the system prompt so the provider request
    remains well-formed even when visible history is empty.
    """
    if idle_follow_up:
        instruction = (
            "The human has not replied for about a minute. Send one brief, "
            "natural check-in now, such as asking whether they are still there "
            "or gently continuing the topic. Always call the `speak` tool with "
            "at least one non-empty message."
        )
    elif require_response:
        instruction = (
            "Respond to the latest human message now. Always call the `speak` "
            "tool with at least one non-empty message."
        )
    else:
        instruction = (
            "Based on the conversation above, decide whether to speak. "
            "Always call the `speak` tool. If you choose silence, call "
            "it with an empty messages array."
        )
    return {
        "role": "user",
        "content": [{"text": instruction}],
    }


def _requires_response_after_human(
    conv: dict,
    chatroom_setting: dict,
    now_ms: int,
) -> bool:
    """Return whether the single non-mimic AI must answer the latest human."""
    if not is_single_human_single_ai_assistant_room(chatroom_setting):
        return False

    for message in reversed(conv.get("events", []) or []):
        if message.get("type") != "message":
            continue
        visible_at = int(
            message.get("visible_at", message.get("timestamp", 0)) or 0
        )
        if visible_at > now_ms:
            continue
        return message.get("role") in {"human", "user"}
    return False


def _requires_idle_follow_up(
    conv: dict,
    chatroom_setting: dict,
    now_ms: int,
) -> bool:
    """Return whether this unanswered turn is due its one idle follow-up."""
    state = _idle_follow_up_state(conv, chatroom_setting, now_ms)
    return bool(
        state.get("eligible")
        and not state.get("follow_up_sent")
        and int(state.get("idle_ms", 0)) >= IDLE_FOLLOW_UP_AFTER_MS
    )


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def handle_tick(event: dict, context=None) -> Optional[dict]:
    """Tick handler entry — async Lambda invocation target.

    ``event`` shape: ``{"conversation_id": "..."}``. Returns a small dict
    summarizing the outcome for CloudWatch (or ``None`` for a no-op).
    """
    conversation_id = (event or {}).get("conversation_id")
    if not conversation_id:
        logger.warning("tick handler called without conversation_id")
        return None

    db = _get_db()
    rds = _get_rds()
    now_ms = _now_ms()

    # --- Step 1: idempotency guard. ----------------------------------------
    won = db.update_last_tick_at_conditional(
        conversation_id, now_ms, TICK_DEDUPE_WINDOW_MS
    )
    if not won:
        return {"status": "deduped"}

    conv = db.get_conversation(conversation_id)
    if conv is None:
        logger.warning("tick: conversation %s not found", conversation_id)
        return {"status": "not_found"}

    chatroom_id = conv.get("chatroom_id")
    chatroom_setting = conv.get("chatroom_setting") or {}

    # If the conversation already ended, do not tick further. This guards
    # against a slow heartbeat that's still seeing the row in ``status-index``
    # right after another tick flipped it to ``ended``.
    if conv.get("status") == "ended":
        return {"status": "already_ended"}

    # --- Step 2: max-duration check. ---------------------------------------
    max_duration = chatroom_setting.get("max_duration_seconds")
    started_at = conv.get("started_at")
    if max_duration and started_at:
        started_ms = _iso_to_ms(started_at)
        if started_ms and now_ms > started_ms + int(max_duration) * 1000:
            db.update_status(conversation_id, "ended")
            db.append_events(
                conversation_id,
                chatroom_id,
                [{
                    "type": "system",
                    "session_id": None,
                    "sender": "System",
                    "role": "system",
                    "content": "This conversation has ended.",
                    "timestamp": now_ms,
                    "visible_at": now_ms,
                    "created_at": _now_iso(),
                }],
            )
            return {"status": "ended"}

    # --- Step 3: gate. -----------------------------------------------------
    # The exact single-assistant preset promises a reply after each human
    # message. Skip only the generic silence window for that case; every other
    # room keeps the normal gate unchanged.
    require_response = _requires_response_after_human(
        conv,
        chatroom_setting,
        now_ms,
    )
    decision = run_gate(
        conv,
        now_ms,
        min_silence_ms=0 if require_response else MIN_SILENCE_MS,
    )
    if decision.skip:
        db.append_events(
            conversation_id,
            chatroom_id,
            [_make_tick_event(
                now_ms,
                gate_decision="skip",
                skip_reason=decision.reason,
            )],
        )
        return {"status": "skipped", "reason": decision.reason}

    candidate_session_id = decision.candidate_session_id
    candidate_nickname = decision.candidate_nickname or "Participant"

    candidate_participant = next(
        (
            p for p in conv.get("participants", []) or []
            if p.get("session_id") == candidate_session_id
        ),
        None,
    )
    persona = (candidate_participant or {}).get("persona", "") or ""

    # --- Step 4: Bedrock with the speak tool. ------------------------------
    mode = derive_runtime_mode(chatroom_setting)
    history_block = _render_history_block(conv, now_ms)
    participant_nicknames = [
        p.get("nickname") for p in conv.get("participants", []) or []
        if p.get("nickname")
    ]
    idle_follow_up = _requires_idle_follow_up(
        conv,
        chatroom_setting,
        now_ms,
    )

    bedrock_messages = build_bedrock_messages(conv, candidate_session_id, now_ms)
    model_id = (
        (candidate_participant or {}).get("model_id")
        or chatroom_setting.get("model_id")
        or _DEFAULT_MODEL_ID
    )
    temperature = normalize_temperature(
        (candidate_participant or {}).get("temperature"),
        default=normalize_temperature(chatroom_setting.get("temperature"), default=0.7),
    ) or 0.7
    system_prompt = _build_bedrock_system_blocks(
        mode,
        chatroom_setting,
        persona,
        candidate_nickname,
        history_block,
        model_id=model_id,
        participant_nicknames=participant_nicknames,
        require_response=require_response,
    )
    if _supports_bedrock_prompt_cache(model_id):
        prefix_message = _build_bedrock_cache_prefix_message(
            mode,
            chatroom_setting,
            persona,
            candidate_nickname,
            history_block,
            participant_nicknames=participant_nicknames,
            require_response=require_response,
        )
        bedrock_messages = _prepend_cache_prefix_message(
            bedrock_messages,
            prefix_message,
        )

    # Bedrock requires ``messages`` to start with the user role and cannot end
    # with the assistant role. If our visible-message history is empty or
    # ends with the candidate AI's own utterance, append a thin user
    # "trigger" so the call is well-formed and the model has a clear cue to
    # call the speak tool. Mirrors ``experiment/group-poc.js``.
    if not bedrock_messages or bedrock_messages[-1]["role"] == "assistant":
        bedrock_messages = (bedrock_messages or []) + [
            _build_tick_trigger_message(
                require_response=require_response,
                idle_follow_up=idle_follow_up,
            )
        ]

    try:
        result = _invoke_with_model_fallback(
            model_id,
            system_prompt,
            bedrock_messages,
            temperature=temperature,
            require_message=require_response or idle_follow_up,
        )
    except BedrockInferenceError as err:
        # Fatal Bedrock error: append one tick + one system event (so the
        # widget surfaces "Chatroom server error: ..."). Conversation
        # continues — the next tick still fires.
        db.append_events(
            conversation_id,
            chatroom_id,
            [
                _make_tick_event(
                    now_ms,
                    chosen_session_id=candidate_session_id,
                    gate_decision="consider",
                    ai_decision=None,
                    bedrock_invoked=True,
                    error=err.error_type,
                ),
                {
                    "type": "system",
                    "session_id": None,
                    "sender": "System",
                    "role": "system",
                    "content": f"Chatroom server error: {err.error_type}",
                    "timestamp": now_ms,
                    "visible_at": now_ms,
                    "created_at": _now_iso(),
                },
            ],
        )
        return {"status": "bedrock_error", "error_type": err.error_type}

    messages = result.get("messages", []) or []
    input_tokens = result.get("input_tokens", 0)
    output_tokens = result.get("output_tokens", 0)
    cache_read_input_tokens = result.get("cache_read_input_tokens", 0)
    cache_write_input_tokens = result.get("cache_write_input_tokens", 0)
    resolved_model_id = result.get("resolved_model_id") or model_id
    provider = "bedrock"

    try:
        chatroom = rds.get_chatroom(chatroom_id) if chatroom_id else None
        owner_id = (chatroom or {}).get("owner_id")
        if owner_id is None:
            logger.warning("tick: owner_id missing for chatroom %s; skipping usage write", chatroom_id)
        else:
            pricing_key, estimated_cost_usd = estimate_cost_usd(
                provider,
                resolved_model_id,
                input_tokens,
                output_tokens,
                cache_read_input_tokens=cache_read_input_tokens,
                cache_write_input_tokens=cache_write_input_tokens,
                allow_unknown=True,
            )
            pricing_estimated = not is_unknown_pricing_key(pricing_key)
            if not pricing_estimated:
                logger.warning(
                    "tick: unknown pricing for provider=%s model_id=%s; writing token usage with zero estimated cost",
                    provider,
                    resolved_model_id,
                )
            rds.write_usage(
                usage_event_id=f"{conversation_id}:{now_ms}:{candidate_session_id}",
                owner_id=owner_id,
                chatroom_id=chatroom_id,
                conversation_id=conversation_id,
                session_id=candidate_session_id,
                provider=provider,
                model_id=resolved_model_id,
                pricing_key=pricing_key,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_cost_usd,
                invoked_at=datetime.fromtimestamp(now_ms / 1000, timezone.utc),
                raw_usage_json={
                    "bedrock_invoked": True,
                    "messages_count": len(messages),
                    "cache_read_input_tokens": cache_read_input_tokens,
                "cache_write_input_tokens": cache_write_input_tokens,
                "temperature": temperature,
                "pricing_estimated": pricing_estimated,
            },
            )
    except Exception as usage_exc:
        logger.warning("tick: usage write failed for conversation %s: %s", conversation_id, usage_exc)

    # --- Step 5: append tick + AI messages with stacked visible_at. --------
    new_events: list[dict] = [_make_tick_event(
        now_ms,
        chosen_session_id=candidate_session_id,
        gate_decision="consider",
        ai_decision="speak" if messages else "silent",
        bedrock_invoked=True,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_write_input_tokens=cache_write_input_tokens,
    )]

    if messages:
        if require_response or idle_follow_up:
            visible_ats = [now_ms] * len(messages)
        else:
            delays = pick_delays_ms(len(messages))
            visible_ats = compute_visible_at(now_ms, delays)
        avatar = (candidate_participant or {}).get("avatar")
        internal_name = (candidate_participant or {}).get("internal_name")
        for text, visible_at in zip(messages, visible_ats):
            new_events.append({
                "type": "message",
                "session_id": candidate_session_id,
                "sender": candidate_nickname,
                "role": "ai",
                "ai_participant_id": candidate_session_id,
                "internal_name": internal_name,
                "content": text,
                "timestamp": now_ms,
                "visible_at": visible_at,
                "created_at": _now_iso(),
                **({"message_kind": "idle_follow_up"} if idle_follow_up else {}),
                **({"avatar": avatar} if avatar else {}),
            })

    db.append_events(conversation_id, chatroom_id, new_events)

    if messages:
        db.update_last_speak_at(conversation_id, candidate_session_id, now_ms)

    return {
        "status": "spoke" if messages else "silent",
        "ai_decision": "speak" if messages else "silent",
        "candidate_session_id": candidate_session_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cache_write_input_tokens": cache_write_input_tokens,
    }


__all__ = [
    "handle_tick",
    "REQUIRED_SPEAK_TOOL_CONFIG",
    "SPEAK_TOOL_CONFIG",  # re-export for tests/inspection
]
