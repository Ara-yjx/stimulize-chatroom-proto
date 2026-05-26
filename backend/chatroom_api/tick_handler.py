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
from chatroom_api.constants import TICK_DEDUPE_WINDOW_MS
from chatroom_api.conversation import build_bedrock_messages
from chatroom_api.delays import compute_visible_at, pick_delays_ms
from chatroom_api.gate import run_gate
from chatroom_api.pricing import estimate_cost_usd
from chatroom_api.prompts.speech_scaffold import (
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
    system_prompt: str,
    bedrock_messages: list[dict],
) -> dict:
    """Invoke Bedrock, falling back to the default model for stale saved ids.

    Local dev and long-lived chatroom rows can carry model ids that have since
    been retired by the provider. If the configured model fails with
    ``ResourceNotFoundException``, retry once with the current default model so
    the chat loop keeps working while the saved config catches up.
    """
    try:
        result = invoke_speak_tool(model_id, system_prompt, bedrock_messages)
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
        result = invoke_speak_tool(_DEFAULT_MODEL_ID, system_prompt, bedrock_messages)
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
        "error": error,
    }


def _render_history_block(conv: dict, now_ms: int) -> str:
    """Render a textual ``<conversation-history>`` block for the system prompt.

    Mirrors ``experiment/group-poc.js renderHistoryBlock``: drops tick events,
    drops events with ``visible_at > now`` (so the AI sees what users see),
    formats relative timestamps. Returns ``"(empty)"`` if no events qualify.
    """
    lines: list[str] = []
    audit_types = {"tick", "lobby_created"}
    for event in conv.get("events", []) or []:
        if event.get("type") in audit_types:
            continue
        visible_at = int(event.get("visible_at", event.get("timestamp", 0)) or 0)
        if visible_at > now_ms:
            continue
        ago_sec = max(0, round((now_ms - visible_at) / 1000))
        if event.get("type") == "system":
            lines.append(f"> [{ago_sec} sec ago] System: {event.get('content', '')}")
        else:
            sender = event.get("sender") or "Participant"
            lines.append(f"> [{ago_sec} sec ago] {sender}: {event.get('content', '')}")
    return "\n".join(lines) if lines else "(empty)"


def _build_static_prefix_block(mode: str) -> str:
    """Return the large static scaffold/examples block for this mode."""
    return get_scaffold_for_mode(mode)


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
) -> dict[str, str | list[str]]:
    """Return explicit prompt segments for the current tick.

    This is the first step of the token-saver refactor: make the prompt
    structure explicit without changing prompt content yet.
    """
    return {
        "static_prefix": _build_static_prefix_block(mode),
        "semi_static_setup": _build_semi_static_setup_blocks(
            chatroom_setting,
            persona,
            my_nickname,
            participant_nicknames=participant_nicknames,
        ),
        "dynamic_context": _build_dynamic_context_block(history_block),
        "additional_prompt": _build_additional_prompt_block(chatroom_setting),
    }


def _build_system_prompt(
    mode: str,
    chatroom_setting: dict,
    persona: str,
    my_nickname: str,
    history_block: str,
    participant_nicknames: list[str] | None = None,
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
    )
    parts: list[str] = [str(blocks["static_prefix"])]
    parts.extend(blocks["semi_static_setup"])
    parts.append(str(blocks["dynamic_context"]))
    additional = str(blocks["additional_prompt"])
    if additional:
        parts.append(additional)
    return "\n".join(parts)


def _build_tick_trigger_message() -> dict:
    """Return the thin user-side trigger appended when history ends on assistant.

    The trigger stays separate from the system prompt so the provider request
    remains well-formed even when visible history is empty.
    """
    return {
        "role": "user",
        "content": [{
            "text": (
                "Based on the conversation above, decide whether to speak. "
                "Always call the `speak` tool. If you choose silence, call "
                "it with an empty messages array."
            )
        }],
    }


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
    decision = run_gate(conv, now_ms)
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
    mode = chatroom_setting.get("mode", "group")
    history_block = _render_history_block(conv, now_ms)
    participant_nicknames = [
        p.get("nickname") for p in conv.get("participants", []) or []
        if p.get("nickname")
    ]
    system_prompt = _build_system_prompt(
        mode,
        chatroom_setting,
        persona,
        candidate_nickname,
        history_block,
        participant_nicknames=participant_nicknames,
    )

    bedrock_messages = build_bedrock_messages(conv, candidate_session_id, now_ms)
    # Bedrock requires ``messages`` to start with the user role and cannot end
    # with the assistant role. If our visible-message history is empty or
    # ends with the candidate AI's own utterance, prepend a thin user
    # "trigger" so the call is well-formed and the model has a clear cue to
    # call the speak tool. Mirrors ``experiment/group-poc.js``.
    if not bedrock_messages or bedrock_messages[-1]["role"] == "assistant":
        bedrock_messages = (bedrock_messages or []) + [_build_tick_trigger_message()]

    model_id = (
        (candidate_participant or {}).get("model_id")
        or chatroom_setting.get("model_id")
        or _DEFAULT_MODEL_ID
    )

    try:
        result = _invoke_with_model_fallback(model_id, system_prompt, bedrock_messages)
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
    )]

    if messages:
        delays = pick_delays_ms(len(messages))
        visible_ats = compute_visible_at(now_ms, delays)
        avatar = (candidate_participant or {}).get("avatar")
        for text, visible_at in zip(messages, visible_ats):
            new_events.append({
                "type": "message",
                "session_id": candidate_session_id,
                "sender": candidate_nickname,
                "role": "ai",
                "ai_participant_id": candidate_session_id,
                "content": text,
                "timestamp": now_ms,
                "visible_at": visible_at,
                "created_at": _now_iso(),
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
    }


__all__ = [
    "handle_tick",
    "SPEAK_TOOL_CONFIG",  # re-export for tests/inspection
]
