"""Property tests for ``tick_handler.handle_tick`` idempotency.

Validates: Correctness Properties §6 (idempotency) — second clause —
from ``.kiro/specs/stimulize-chatroom-beta/design.md``:

  ``tick_handler`` invoked twice within ``TICK_DEDUPE_WINDOW_MS`` produces
  the same set of new events as a single invocation.

The test patches the Bedrock call (``invoke_speak_tool``) with a fixed
deterministic response and patches ``tick_handler.time.time`` so the two
invocations land within ``TICK_DEDUPE_WINDOW_MS`` (the second call's clock
is offset by a Hypothesis-chosen amount in ``[0, TICK_DEDUPE_WINDOW_MS-1]``).

Hypothesis is restricted to ~20 examples here because each example
exercises the full tick handler (gate + Bedrock mock + DDB writes) which is
heavier than the pure-function PBTs in this file's neighborhood.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from chatroom_api import config, mock_dynamo, mock_lobby, mock_rds
from chatroom_api import tick_handler
from chatroom_api.constants import TICK_DEDUPE_WINDOW_MS


CHATROOM_ID = "scid_pbt-tick-idempotency"


def _seed_active_conversation(now_iso: str = "2025-01-01T00:00:00+00:00") -> str:
    """Reset shared mocks and seed a single active conversation. Returns the id.

    Layout: 1 human + 1 AI participant, ``status="active"``, ``last_tick_at=0``,
    no prior events. ``chatroom_setting`` carries no ``max_duration_seconds``
    so the tick handler doesn't end the conversation on the
    max-duration-check branch.
    """
    config.USE_MOCK_DYNAMO = True
    config.USE_MOCK_RDS = True
    config.USE_MOCK_LOBBY = True

    mock_lobby.reset()
    mock_dynamo.reset()

    conversation_id = "conv-pbt-tick-" + uuid.uuid4().hex
    chatroom_setting = {
        "mode": "group",
        "topic_instruction": "test topic",
        "model_id": "test-model",
        "simulate_pairing_seconds": 0,
        "timer_min_minutes": None,
        "timer_max_minutes": None,
        # Intentionally no ``max_duration_seconds``.
        "target_human_count": 1,
        "ai_join_strategy": "fixed_ai_count",
        "ai_strategy_value": 1,
        "max_wait_seconds": 0,
    }
    participants = [
        {
            "session_id": "human_001",
            "nickname": "Earth",
            "avatar": {"emojiText": "🐱"},
            "role": "human",
        },
        {
            "session_id": "ai_001",
            "nickname": "Mars",
            "avatar": {"emojiText": "🐶"},
            "role": "ai",
            "persona": "test persona",
        },
    ]
    mock_dynamo.append_events(
        conversation_id=conversation_id,
        chatroom_id=CHATROOM_ID,
        events=[],  # start with no events
        chatroom_setting=chatroom_setting,
        participants=participants,
        status="active",
        started_at=now_iso,
        last_tick_at=0,
        last_speak_at_by_session={},
    )

    # Defensive: ensure mock_rds has a matching chatroom (not strictly needed
    # by the tick handler since it doesn't re-read RDS, but keeps the mock
    # state self-consistent).
    mock_rds._chatrooms[CHATROOM_ID] = {
        "id": CHATROOM_ID,
        "owner_id": "user_pbt",
        "name": "PBT Tick Idempotency",
        "status": "active",
        "setting": chatroom_setting,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    return conversation_id


# Deterministic Bedrock response: one short message, fixed token counts.
_FAKE_BEDROCK_RESPONSE = {
    "messages": ["hi"],
    "input_tokens": 100,
    "output_tokens": 5,
    "raw_response": {},
}


def _events_after(conversation_id: str, after_ts: int = 0) -> list[dict]:
    """Get events for *conversation_id* with timestamp > *after_ts*."""
    return mock_dynamo.get_events(conversation_id, after=after_ts)


@settings(max_examples=20, deadline=None)
@given(now_ms_offset=st.integers(min_value=0, max_value=TICK_DEDUPE_WINDOW_MS - 1))
def test_tick_handler_idempotent_within_dedupe_window(now_ms_offset: int) -> None:
    """Validates: Correctness Properties §6 — tick handler idempotency.

    Two ``handle_tick`` invocations within ``TICK_DEDUPE_WINDOW_MS`` produce
    the same set of new events as one invocation, and the second call
    returns ``{"status": "deduped"}``.
    """
    conversation_id = _seed_active_conversation()
    base_seconds = 1_700_000_000  # arbitrary, well past 0; matches a 2023 ts

    with patch.object(
        tick_handler,
        "invoke_speak_tool",
        return_value=_FAKE_BEDROCK_RESPONSE,
    ):
        # First call: ``time.time()`` returns ``base_seconds`` (epoch s).
        # ``_now_ms`` will compute ``base_seconds * 1000``.
        with patch.object(tick_handler.time, "time", return_value=float(base_seconds)):
            first_result = tick_handler.handle_tick({"conversation_id": conversation_id})

        first_events = _events_after(conversation_id, after_ts=0)

        # Second call: clock advanced by ``now_ms_offset`` ms (0 ≤ offset <
        # TICK_DEDUPE_WINDOW_MS), so the dedupe guard must reject it.
        second_clock = base_seconds + (now_ms_offset / 1000.0)
        with patch.object(tick_handler.time, "time", return_value=second_clock):
            second_result = tick_handler.handle_tick({"conversation_id": conversation_id})

        second_events = _events_after(conversation_id, after_ts=0)

    assert first_result is not None
    assert first_result.get("status") in {"spoke", "silent", "skipped"}, (
        f"unexpected first-call status: {first_result!r}"
    )
    assert second_result == {"status": "deduped"}, (
        f"expected dedupe outcome, got {second_result!r} "
        f"(now_ms_offset={now_ms_offset})"
    )
    # Idempotency: the event set after the second call equals the event set
    # after the first.
    assert second_events == first_events, (
        f"event set drifted under dedupe: "
        f"first={len(first_events)} events, second={len(second_events)} events"
    )


def test_tick_handler_first_call_records_one_tick_and_one_message() -> None:
    """Example: a single fresh tick produces one tick event and one message.

    Sanity-check the test scaffold so the property test above isn't
    asserting on a degenerate (e.g. always-empty) event list.
    """
    conversation_id = _seed_active_conversation()
    base_seconds = 1_700_000_000

    with patch.object(
        tick_handler,
        "invoke_speak_tool",
        return_value=_FAKE_BEDROCK_RESPONSE,
    ), patch.object(tick_handler.time, "time", return_value=float(base_seconds)):
        result = tick_handler.handle_tick({"conversation_id": conversation_id})

    assert result is not None
    assert result.get("status") == "spoke", f"unexpected status: {result!r}"

    events = _events_after(conversation_id, after_ts=0)
    tick_events = [e for e in events if e.get("type") == "tick"]
    message_events = [e for e in events if e.get("type") == "message"]
    assert len(tick_events) == 1, (
        f"expected exactly 1 tick event, got {len(tick_events)} "
        f"(events={events!r})"
    )
    assert len(message_events) == 1, (
        f"expected exactly 1 message event (from fake Bedrock response), "
        f"got {len(message_events)}"
    )
