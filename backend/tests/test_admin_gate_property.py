"""Property tests for the ``/chat/messages`` admin tick-events gate.

Validates: Correctness Properties §3.8 from
``.kiro/specs/stimulize-chatroom-beta/tasks.md``:

  Given any (admin_token, bearer, include_ticks_flag), tick events appear
  in the response **iff**
  ``bearer == admin_token AND include_ticks_flag == true AND
  admin_token != ""``.

The implementation in ``chat.py`` short-circuits when ``ADMIN_TOKEN`` is
empty, so an empty configured token must never gate on the header.
"""

from __future__ import annotations

from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from chatroom_api import chat as chat_mod
from chatroom_api import config, mock_dynamo


HUMAN_SESSION = "sess_human_1"
AI_SESSION = "ai_abc"
CONVERSATION_ID = "conv_pbt_admin"
CHATROOM_ID = "scid_pbt_admin"

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


# Token alphabet — three values: empty (admin disabled), the correct
# secret, and a wrong value. Hypothesis picks one for the env config and
# one for the request header independently.
_TOKEN_VALUES = ["", "secret", "wrong"]


def _setup_mocks() -> None:
    config.USE_MOCK_DYNAMO = True
    config.USE_MOCK_RDS = True
    config.USE_MOCK_LOBBY = True
    mock_dynamo.reset()


def _seed_one_tick_one_message(now_ms: int = 1_000) -> None:
    """Seed a conversation with one tick event + one message event.

    Both have ``visible_at < 5_000`` so they fall within the visible
    window for our test ``now_ms = 5_000``.
    """
    mock_dynamo.append_events(
        CONVERSATION_ID,
        CHATROOM_ID,
        [
            {
                "type": "tick",
                "session_id": AI_SESSION,
                "sender": None,
                "role": "system",
                "content": "tick_event",
                "timestamp": now_ms,
                "visible_at": now_ms,
                "gate_decision": "skip",
                "skip_reason": "min_silence_not_elapsed",
            },
            {
                "type": "message",
                "session_id": HUMAN_SESSION,
                "sender": "Alice",
                "role": "human",
                "content": "hello",
                "timestamp": now_ms,
                "visible_at": now_ms,
            },
        ],
        chatroom_setting={"mode": "group"},
        participants=[HUMAN, AI],
        status="active",
        started_at="2025-01-01T00:00:00+00:00",
        last_tick_at=0,
    )


@settings(max_examples=50, deadline=None)
@given(
    admin_token=st.sampled_from(_TOKEN_VALUES),
    bearer=st.sampled_from(_TOKEN_VALUES),
    include_ticks_flag=st.booleans(),
)
def test_admin_gate_iff_admin_token_and_bearer_and_flag(
    admin_token: str,
    bearer: str,
    include_ticks_flag: bool,
) -> None:
    """Validates: Tasks 3.8 — admin gate iff condition.

    Tick events appear iff
    ``admin_token != "" AND bearer == admin_token AND include_ticks_flag``.
    """
    _setup_mocks()
    _seed_one_tick_one_message()

    expected_visible = (
        admin_token != "" and bearer == admin_token and include_ticks_flag
    )

    query_params = {"include_ticks": "true" if include_ticks_flag else "false"}
    headers = {"X-Admin-Token": bearer}

    original_admin_token = config.ADMIN_TOKEN
    config.ADMIN_TOKEN = admin_token
    try:
        with patch.object(chat_mod, "_now_ms", return_value=5_000):
            status, body = chat_mod.handle_chat_messages(
                query_params, CLAIMS, headers=headers
            )
    finally:
        config.ADMIN_TOKEN = original_admin_token

    assert status == 200, body

    has_tick = any(e["type"] == "tick" for e in body["events"])

    assert has_tick == expected_visible, (
        f"admin gate mismatch:\n"
        f"  admin_token={admin_token!r}, bearer={bearer!r}, "
        f"include_ticks_flag={include_ticks_flag!r}\n"
        f"  expected ticks visible: {expected_visible}\n"
        f"  actual ticks visible:   {has_tick}\n"
        f"  events: {body['events']!r}"
    )

    # The message event must always be present regardless of admin gate.
    has_message = any(e["type"] == "message" for e in body["events"])
    assert has_message, (
        f"message event must always be visible (events={body['events']!r})"
    )
