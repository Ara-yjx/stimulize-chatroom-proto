"""Property tests for ``/chat/messages`` event filtering.

Validates: Correctness Properties §3.7 from
``.kiro/specs/stimulize-chatroom-beta/tasks.md``:

  For any conversation snapshot, any ``now`` and any ``after``, the
  returned events (without admin override) equal the set
  ``{ e : e.type != "tick" AND after < e.visible_at <= now }``,
  ordered as the underlying events list.

We seed ``mock_dynamo`` with the snapshot, patch ``chat_mod._now_ms`` to
return our chosen ``now``, and call ``handle_chat_messages`` without an
admin token. Comparison is by content (since ``_decorate_with_avatar``
reshapes events into the wire format).
"""

from __future__ import annotations

from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from chatroom_api import chat as chat_mod
from chatroom_api import config, mock_dynamo


HUMAN_SESSION = "sess_human_1"
AI_SESSION = "ai_abc"
CONVERSATION_ID = "conv_pbt_filter"
CHATROOM_ID = "scid_pbt_filter"

CLAIMS = {
    "session_id": HUMAN_SESSION,
    "conversation_id": CONVERSATION_ID,
    "chatroom_id": CHATROOM_ID,
}

HUMAN = {
    "session_id": HUMAN_SESSION,
    "nickname": "Alice",
    "avatar": {"emojiText": "🐱"},
    "role": "human",
}
AI = {
    "session_id": AI_SESSION,
    "nickname": "Sam",
    "avatar": {"emojiText": "🐶"},
    "role": "ai",
}


def _setup_mocks() -> None:
    config.USE_MOCK_DYNAMO = True
    config.USE_MOCK_RDS = True
    config.USE_MOCK_LOBBY = True
    mock_dynamo.reset()


@st.composite
def _events_strategy(draw):
    """Generate a list of events with distinct sequential ``content``.

    Each event has a random type, random ``visible_at`` and ``timestamp``.
    Content is unique (``event_0``, ``event_1``, ...) so we can compare
    by content equality without worrying about duplicates.
    """
    n = draw(st.integers(min_value=0, max_value=12))
    events: list[dict] = []
    for i in range(n):
        ev_type = draw(st.sampled_from(["message", "system", "tick"]))
        # Pick a session_id appropriate to type so the seeded row is
        # internally consistent (sender used by avatar lookup).
        if ev_type == "message":
            sender_choice = draw(st.sampled_from(["Alice", "Sam"]))
            session_id = HUMAN_SESSION if sender_choice == "Alice" else AI_SESSION
            sender = sender_choice
            role = "human" if sender_choice == "Alice" else "ai"
        elif ev_type == "system":
            session_id = "system"
            sender = "System"
            role = "system"
        else:  # tick
            session_id = AI_SESSION
            sender = None
            role = "system"

        timestamp = draw(st.integers(min_value=0, max_value=10_000))
        visible_at = draw(st.integers(min_value=0, max_value=10_000))
        events.append(
            {
                "type": ev_type,
                "session_id": session_id,
                "sender": sender,
                "role": role,
                "content": f"event_{i}",
                "timestamp": timestamp,
                "visible_at": visible_at,
            }
        )
    return events


@settings(max_examples=50, deadline=None)
@given(
    events=_events_strategy(),
    now=st.integers(min_value=0, max_value=10_000),
    after=st.integers(min_value=0, max_value=10_000),
)
def test_chat_messages_filter_matches_specification(
    events: list[dict],
    now: int,
    after: int,
) -> None:
    """Validates: Tasks 3.7 — /chat/messages filter correctness.

    Returned events == ``{ e : type != "tick" AND after < visible_at <= now }``,
    in the same relative order as the seeded list.
    """
    _setup_mocks()

    # Seed conversation row with the generated events.
    mock_dynamo.append_events(
        CONVERSATION_ID,
        CHATROOM_ID,
        events,
        chatroom_setting={"mode": "group"},
        participants=[HUMAN, AI],
        status="active",
        started_at="2025-01-01T00:00:00+00:00",
        last_tick_at=0,
    )

    # Ensure no admin override is in effect.
    original_admin_token = config.ADMIN_TOKEN
    config.ADMIN_TOKEN = ""
    try:
        with patch.object(chat_mod, "_now_ms", return_value=now):
            status, body = chat_mod.handle_chat_messages(
                {"after": str(after)},
                CLAIMS,
                headers=None,
            )
    finally:
        config.ADMIN_TOKEN = original_admin_token

    assert status == 200, body

    # Compute expected set per the specification.
    expected = [
        e for e in events
        if e.get("type") != "tick"
        and after < int(e.get("visible_at", e.get("timestamp", 0)) or 0) <= now
    ]
    expected_contents = [e["content"] for e in expected]

    actual_contents = [e["content"] for e in body["events"]]

    assert actual_contents == expected_contents, (
        f"filter mismatch:\n"
        f"  events:   {events!r}\n"
        f"  now={now}, after={after}\n"
        f"  expected: {expected_contents!r}\n"
        f"  actual:   {actual_contents!r}"
    )

    # Belt-and-suspenders: no tick events ever appear in the response.
    assert all(e["type"] != "tick" for e in body["events"])
