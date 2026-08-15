"""Async-invoked Lambda — runs gate + Bedrock + DDB writes for one tick.

Implements the 5-step procedure in
``docs/low-level-design.md#tick-handler``:

1. **Tick lease.** Acquire a conversation-level lease before inference so a
   second heartbeat cannot overlap Bedrock work or simulated typing delays.
2. **Max-duration check.** If ``now > started_at + max_duration_seconds``,
   flip ``status="ended"`` and append a system "conversation ended" event.
3. **Gate.** Pure ``run_gate(conv, now)`` decides whether some AI should
   speak. Skips are written to structured logs, not conversation history.
4. **Bedrock.** Build the per-AI system prompt (SCAFFOLD + TOPIC + PERSONA
   + CONVERSATION_CONTEXT) plus the Bedrock messages array, then call
   ``invoke_speak_tool`` (which reuses the shared retry + error
   classification in ``bedrock_client.py``).
5. **Persist result.** Wait out each simulated typing delay, then append the
   message with the current server timestamp and update compact per-AI state.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from chatroom_api import config
from chatroom_api._providers import get_event_store_provider
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
from chatroom_api.delays import pick_delays_ms
from chatroom_api.gate import run_gate
from chatroom_api.event_store import ConditionalWriteFailed
from chatroom_api.participants import participant_id
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
MAX_AI_BUBBLES_PER_TICK = 5
ACTIVE_TICK_LEASE_MS = 125_000


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


def _get_event_store():
    return get_event_store_provider()


def _log_tick(conversation_id: str, status: str, **fields) -> None:
    """Emit redacted structured diagnostics; never include message content."""
    logger.info(
        "tick_decision %s",
        json.dumps(
            {
                "version": 1,
                "conversation_id": conversation_id,
                "status": status,
                **{key: value for key, value in fields.items() if value is not None},
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def _next_actionable_from_states(states: dict, default: int) -> int:
    values = [
        int(state.get("next_actionable_at", 0) or 0)
        for state in states.values()
        if int(state.get("next_actionable_at", 0) or 0) > 0
    ]
    return min(values) if values else int(default)


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


def _handle_owned_tick(conversation_id: str, tick_id: str, now_ms: int) -> dict:
    """Run one tick after ``tick_id`` acquired the conversation lease."""
    db = _get_db()
    history_store = _get_event_store()
    rds = _get_rds()

    conv = db.get_conversation(conversation_id)
    if conv is None:
        logger.warning("tick: conversation %s not found", conversation_id)
        return {"status": "not_found"}

    chatroom_id = conv.get("chatroom_id")
    chatroom_setting = conv.get("chatroom_setting") or {}
    visible_history = history_store.query_prompt_events(conversation_id, now_ms)
    runtime_conv = {**conv, "events": visible_history}
    states = dict(conv.get("ai_tick_state_by_participant_id") or {})
    legacy_last_speak = dict(conv.get("last_speak_at_by_session") or {})
    for ai_id, state in states.items():
        if state.get("last_spoke_at") is not None:
            legacy_last_speak[ai_id] = state["last_spoke_at"]
    runtime_conv["last_speak_at_by_session"] = legacy_last_speak

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
            try:
                history_store.append_history_batch(
                    conversation_id,
                    [{
                    "type": "system",
                    "subtype": "conversation_ended",
                    "sender": "System",
                    "role": "system",
                    "content": "This conversation has ended.",
                    "timestamp": now_ms,
                    "created_at": _now_iso(),
                    }],
                    uuid4().hex,
                    metadata_updates={"status": "ended"},
                    expected_status="active",
                    expected_active_tick_id=tick_id,
                )
            except ConditionalWriteFailed:
                return {"status": "already_ended"}
            _log_tick(conversation_id, "ended")
            return {"status": "ended"}

    # --- Step 3: gate. -----------------------------------------------------
    # The exact single-assistant preset promises a reply after each human
    # message. Skip only the generic silence window for that case; every other
    # room keeps the normal gate unchanged.
    require_response = _requires_response_after_human(
        runtime_conv,
        chatroom_setting,
        now_ms,
    )
    decision = run_gate(
        runtime_conv,
        now_ms,
        min_silence_ms=0 if require_response else MIN_SILENCE_MS,
    )
    if decision.skip:
        _log_tick(conversation_id, "skipped", reason=decision.reason)
        return {"status": "skipped", "reason": decision.reason}

    candidate_session_id = decision.candidate_session_id
    candidate_nickname = decision.candidate_nickname or "Participant"

    candidate_participant = next(
        (
            p for p in conv.get("participants", []) or []
            if participant_id(p) == candidate_session_id
        ),
        None,
    )
    persona = (candidate_participant or {}).get("persona", "") or ""

    # --- Step 4: Bedrock with the speak tool. ------------------------------
    mode = derive_runtime_mode(chatroom_setting)
    history_block = _render_history_block(runtime_conv, now_ms)
    participant_nicknames = [
        p.get("nickname") for p in conv.get("participants", []) or []
        if p.get("nickname")
    ]
    idle_follow_up = _requires_idle_follow_up(
        runtime_conv,
        chatroom_setting,
        now_ms,
    )

    bedrock_messages = build_bedrock_messages(
        runtime_conv, candidate_session_id, now_ms
    )
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
        tick_state = {
            **states.get(candidate_session_id, {}),
            "last_completed_tick_id": uuid4().hex,
            "last_evaluated_at": now_ms,
            "last_result": "error",
            "observed_history_cursor": (
                visible_history[-1].get("event_key") if visible_history else None
            ),
            "consecutive_silent_count": 0,
            "next_actionable_at": now_ms,
        }
        states[candidate_session_id] = tick_state
        try:
            history_store.append_history_batch(
                conversation_id,
                [{
                    "type": "system",
                    "subtype": "inference_error",
                    "sender": "System",
                    "role": "system",
                    "content": f"Chatroom server error: {err.error_type}",
                    "timestamp": now_ms,
                    "created_at": _now_iso(),
                }],
                uuid4().hex,
                metadata_updates={
                    "ai_tick_state_by_participant_id": states,
                    "next_actionable_tick_at": _next_actionable_from_states(
                        states, now_ms
                    ),
                },
                expected_status="active",
                expected_active_tick_id=tick_id,
            )
        except ConditionalWriteFailed:
            _log_tick(
                conversation_id,
                "dropped_stale_tick",
                ai_participant_id=candidate_session_id,
                reason="inference_error_after_tick_lost_ownership",
            )
            return {"status": "dropped_stale_tick"}
        _log_tick(
            conversation_id,
            "bedrock_error",
            ai_participant_id=candidate_session_id,
            error=err.error_type,
        )
        return {"status": "bedrock_error", "error_type": err.error_type}

    authored_at_ms = _now_ms()
    messages = (result.get("messages", []) or [])[:MAX_AI_BUBBLES_PER_TICK]
    if len(result.get("messages", []) or []) > MAX_AI_BUBBLES_PER_TICK:
        logger.warning(
            "tick: truncating AI output for conversation %s to %s bubbles",
            conversation_id,
            MAX_AI_BUBBLES_PER_TICK,
        )
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

    # --- Step 5: wait, then persist output and compact tick projection. -----
    # Delayed AI messages are intentionally absent from DynamoDB until their
    # typing delay has elapsed. This prevents ended/replaced ticks from
    # leaving future-scheduled messages that later appear in the chat.
    persisted_events: list[dict] = []
    avatar = (candidate_participant or {}).get("avatar")
    internal_name = (candidate_participant or {}).get("internal_name")
    turn_id = uuid4().hex if messages else None
    delays = (
        [0] * len(messages)
        if require_response or idle_follow_up
        else pick_delays_ms(len(messages))
    )
    started_ms = _iso_to_ms(started_at) if started_at else None
    end_deadline_ms = (
        started_ms + int(max_duration) * 1000
        if started_ms is not None and max_duration
        else None
    )
    duration_elapsed = False

    for text, delay_ms in zip(messages, delays):
        if delay_ms:
            time.sleep(delay_ms / 1000)
        persisted_at_ms = _now_ms()
        if end_deadline_ms is not None and persisted_at_ms > end_deadline_ms:
            duration_elapsed = True
            _log_tick(
                conversation_id,
                "dropped_delayed_messages",
                ai_participant_id=candidate_session_id,
                reason="conversation_duration_elapsed",
                persisted_count=len(persisted_events),
                dropped_count=len(messages) - len(persisted_events),
            )
            break
        event = {
            "type": "message",
            "sender": candidate_nickname,
            "role": "ai",
            "ai_participant_id": candidate_session_id,
            "internal_name": internal_name,
            "content": text,
            "timestamp": persisted_at_ms,
            **(
                {"authored_at": authored_at_ms}
                if persisted_at_ms != authored_at_ms
                else {}
            ),
            "turn_id": turn_id,
            "created_at": _now_iso(),
            **({"message_kind": "idle_follow_up"} if idle_follow_up else {}),
            **({"avatar": avatar} if avatar else {}),
        }
        try:
            persisted_events.extend(history_store.append_history_batch(
                conversation_id,
                [event],
                uuid4().hex,
                expected_status="active",
                expected_active_tick_id=tick_id,
            ))
        except ConditionalWriteFailed:
            _log_tick(
                conversation_id,
                "dropped_stale_tick",
                ai_participant_id=candidate_session_id,
                persisted_count=len(persisted_events),
                dropped_count=len(messages) - len(persisted_events),
            )
            break

    if duration_elapsed:
        ended_at_ms = _now_ms()
        try:
            history_store.append_history_batch(
                conversation_id,
                [{
                    "type": "system",
                    "subtype": "conversation_ended",
                    "sender": "System",
                    "role": "system",
                    "content": "This conversation has ended.",
                    "timestamp": ended_at_ms,
                    "created_at": _now_iso(),
                }],
                uuid4().hex,
                metadata_updates={"status": "ended"},
                expected_status="active",
                expected_active_tick_id=tick_id,
            )
        except ConditionalWriteFailed:
            return {
                "status": "partially_spoke" if persisted_events else "dropped_stale_tick",
                "messages_persisted": len(persisted_events),
            }
        return {
            "status": "ended",
            "messages_persisted": len(persisted_events),
            "messages_dropped": len(messages) - len(persisted_events),
        }

    prior_state = states.get(candidate_session_id, {})
    final_visible_at = (
        int(persisted_events[-1]["timestamp"])
        if persisted_events
        else authored_at_ms
    )
    tick_state = {
        **prior_state,
        "last_completed_tick_id": uuid4().hex,
        "last_evaluated_at": now_ms,
        "last_result": "spoke" if persisted_events else "silent",
        "observed_history_cursor": (
            visible_history[-1].get("event_key") if visible_history else None
        ),
        "consecutive_silent_count": (
            0
            if persisted_events
            else int(prior_state.get("consecutive_silent_count", 0) or 0) + 1
        ),
        "next_actionable_at": final_visible_at,
        **({"last_spoke_at": final_visible_at} if persisted_events else {}),
    }
    states[candidate_session_id] = tick_state
    projection_updated = history_store.update_tick_projection(
        conversation_id,
        candidate_session_id,
        tick_state,
        expected_status="active",
        expected_active_tick_id=tick_id,
        next_actionable_tick_at=_next_actionable_from_states(states, now_ms),
    )
    if not projection_updated:
        _log_tick(
            conversation_id,
            "dropped_stale_tick",
            ai_participant_id=candidate_session_id,
            persisted_count=len(persisted_events),
            reason="projection_update_lost_ownership",
        )
        return {
            "status": "partially_spoke" if persisted_events else "dropped_stale_tick",
            "messages_persisted": len(persisted_events),
        }

    _log_tick(
        conversation_id,
        "spoke" if persisted_events else "silent",
        ai_participant_id=candidate_session_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_write_input_tokens=cache_write_input_tokens,
    )

    return {
        "status": "spoke" if persisted_events else "silent",
        "ai_decision": "speak" if messages else "silent",
        "messages_persisted": len(persisted_events),
        "candidate_session_id": candidate_session_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cache_write_input_tokens": cache_write_input_tokens,
    }


def handle_tick(event: dict, context=None) -> Optional[dict]:
    """Acquire a tick lease, run one tick, and always release ownership."""
    conversation_id = (event or {}).get("conversation_id")
    if not conversation_id:
        logger.warning("tick handler called without conversation_id")
        return None

    if config.CHATROOM_SERVICE_MODE == "maintenance":
        logger.info("tick skipped during maintenance: %s", conversation_id)
        return {"status": "maintenance"}

    db = _get_db()
    now_ms = _now_ms()
    tick_id = getattr(context, "aws_request_id", None) or uuid4().hex
    if not db.acquire_active_tick(
        conversation_id,
        tick_id,
        now_ms,
        ACTIVE_TICK_LEASE_MS,
        TICK_DEDUPE_WINDOW_MS,
    ):
        return {"status": "deduped"}

    try:
        return _handle_owned_tick(conversation_id, tick_id, now_ms)
    finally:
        if not db.release_active_tick(conversation_id, tick_id, _now_ms()):
            logger.info(
                "tick: lease already lost for conversation %s tick %s",
                conversation_id,
                tick_id,
            )


__all__ = [
    "handle_tick",
    "REQUIRED_SPEAK_TOOL_CONFIG",
    "SPEAK_TOOL_CONFIG",  # re-export for tests/inspection
]
