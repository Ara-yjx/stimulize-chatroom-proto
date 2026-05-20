"""Tests for chatroom_api.chat — beta /chat/send and /chat/messages.

Per beta tasks 3.3, 3.4, 3.5:
- ``/chat/send`` no longer invokes Bedrock; it appends the human's message
  event and returns the same shape as ``/chat/messages``.
- ``/chat/messages`` filters by ``visible_at <= now`` and ``type != tick``;
  surfaces a ``lobby`` block while the conversation row is missing; the
  admin-token gate controls ``?include_ticks=true``.
- Aborted lobbies bubble up as ``LobbyAbortedException`` (mapped to 410 by
  ``handler.py``).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from chatroom_api import (
    chat as chat_mod,
    config,
    mock_dynamo,
    mock_lobby,
)
from chatroom_api.errors import LobbyAbortedException


HUMAN_SESSION = "sess_human_1"
CONVERSATION_ID = "conv_test_1"
CHATROOM_ID = "scid_test_chat"

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
    "session_id": "ai_abc",
    "nickname": "Sam",
    "avatar": {"emojiText": "🐶"},
    "role": "ai",
    "persona": "test",
}


def _setup_mocks() -> None:
    config.USE_MOCK_DYNAMO = True
    config.USE_MOCK_RDS = True
    config.USE_MOCK_LOBBY = True
    mock_dynamo.reset()
    mock_lobby.reset()


def _seed_active_conversation(events=None, status="active") -> None:
    """Seed an active 1-on-1-like conversation with Alice + Sam."""
    mock_dynamo.append_events(
        CONVERSATION_ID,
        CHATROOM_ID,
        events or [],
        chatroom_setting={"mode": "one_on_one"},
        participants=[HUMAN, AI],
        status=status,
        started_at="2025-01-01T00:00:00+00:00",
        last_tick_at=0,
    )


# ---------------------------------------------------------------------------
# /chat/send
# ---------------------------------------------------------------------------


class TestHandleChatSend:
    def setup_method(self) -> None:
        _setup_mocks()

    def test_appends_human_message_and_returns_visible_events(self) -> None:
        _seed_active_conversation()
        with patch.object(chat_mod, "_now_ms", return_value=1_000_000):
            status, body = chat_mod.handle_chat_send({"message": "hello"}, CLAIMS)

        assert status == 200, body
        assert "events" in body
        # The single appended event is the human message; visible_at == ts.
        assert len(body["events"]) == 1
        evt = body["events"][0]
        assert evt["type"] == "message"
        assert evt["sender"] == "Alice"
        assert evt["role"] == "human"
        assert evt["content"] == "hello"
        assert evt["timestamp"] == 1_000_000
        assert evt["visible_at"] == 1_000_000
        assert evt["avatar"] == {"emojiText": "🐱"}

        # The store actually has the new event.
        stored = mock_dynamo.get_events(CONVERSATION_ID)
        assert len(stored) == 1
        assert stored[0]["content"] == "hello"

    def test_send_does_not_invoke_bedrock(self) -> None:
        """Beta delta — ``/chat/send`` is no longer a Bedrock call site."""
        _seed_active_conversation()

        # If anything tries to import bedrock_client from chat.py, the test
        # would have failed at import time; we also assert no symbol lookup.
        assert not hasattr(chat_mod, "bedrock_client")

        status, body = chat_mod.handle_chat_send({"message": "hi"}, CLAIMS)
        assert status == 200, body

    def test_send_filters_by_after_param(self) -> None:
        # Existing event at timestamp 500 should be excluded by after=500.
        _seed_active_conversation(
            events=[{
                "type": "message",
                "session_id": HUMAN_SESSION,
                "sender": "Alice",
                "role": "human",
                "content": "old",
                "timestamp": 500,
                "visible_at": 500,
            }]
        )
        with patch.object(chat_mod, "_now_ms", return_value=1_000):
            status, body = chat_mod.handle_chat_send(
                {"message": "new", "after": 500}, CLAIMS
            )

        assert status == 200, body
        # Only the new event (visible_at = 1000 > after = 500) is returned.
        assert len(body["events"]) == 1
        assert body["events"][0]["content"] == "new"

    def test_send_returns_409_when_conversation_missing_lobby_phase(self) -> None:
        # No conversation row seeded ⇒ lobby phase from /chat/send's POV.
        status, body = chat_mod.handle_chat_send({"message": "hi"}, CLAIMS)
        assert status == 409
        assert "not started" in body["error"]

    def test_send_returns_409_when_ended(self) -> None:
        _seed_active_conversation(status="ended")
        status, body = chat_mod.handle_chat_send({"message": "hi"}, CLAIMS)
        assert status == 409
        assert "ended" in body["error"]

    def test_send_returns_403_when_session_not_in_conversation(self) -> None:
        _seed_active_conversation()
        bad_claims = {**CLAIMS, "session_id": "sess_not_in_conv"}
        status, body = chat_mod.handle_chat_send({"message": "hi"}, bad_claims)
        assert status == 403

    def test_send_returns_400_when_message_empty(self) -> None:
        _seed_active_conversation()
        status, body = chat_mod.handle_chat_send({"message": ""}, CLAIMS)
        assert status == 400


# ---------------------------------------------------------------------------
# /chat/messages
# ---------------------------------------------------------------------------


class TestHandleChatMessages:
    def setup_method(self) -> None:
        _setup_mocks()

    def test_returns_visible_events_with_avatar_and_status(self) -> None:
        # ``visible_at = 200`` is in the past; ``visible_at = 5000`` is pending.
        _seed_active_conversation(
            events=[
                {
                    "type": "message",
                    "session_id": HUMAN_SESSION,
                    "sender": "Alice",
                    "role": "human",
                    "content": "hello",
                    "timestamp": 100,
                    "visible_at": 200,
                },
                {
                    "type": "message",
                    "session_id": "ai_abc",
                    "sender": "Sam",
                    "role": "ai",
                    "content": "hi (pending)",
                    "timestamp": 1_000,
                    "visible_at": 5_000,
                },
            ]
        )
        with patch.object(chat_mod, "_now_ms", return_value=1_500):
            status, body = chat_mod.handle_chat_messages({}, CLAIMS)

        assert status == 200
        assert body["conversation_status"] == "active"
        assert body["lobby"] is None
        # Only the visible event should come back (pending is filtered).
        assert len(body["events"]) == 1
        evt = body["events"][0]
        assert evt["sender"] == "Alice"
        assert evt["avatar"] == {"emojiText": "🐱"}

    def test_filters_tick_events_without_admin(self) -> None:
        _seed_active_conversation(
            events=[
                {
                    "type": "tick",
                    "session_id": "ai_abc",
                    "sender": None,
                    "role": "system",
                    "content": "",
                    "timestamp": 100,
                    "visible_at": 100,
                    "gate_decision": "skip",
                    "skip_reason": "min_silence_not_elapsed",
                },
                {
                    "type": "message",
                    "session_id": HUMAN_SESSION,
                    "sender": "Alice",
                    "role": "human",
                    "content": "hi",
                    "timestamp": 200,
                    "visible_at": 200,
                },
            ]
        )
        with patch.object(chat_mod, "_now_ms", return_value=1_000):
            status, body = chat_mod.handle_chat_messages(
                {"include_ticks": "true"}, CLAIMS, headers={}
            )

        assert status == 200
        # No admin token configured ⇒ the flag is ignored regardless of header.
        assert all(e["type"] != "tick" for e in body["events"])
        assert len(body["events"]) == 1
        assert body["events"][0]["sender"] == "Alice"

    def test_admin_token_enables_tick_events(self) -> None:
        _seed_active_conversation(
            events=[
                {
                    "type": "tick",
                    "session_id": "ai_abc",
                    "sender": None,
                    "role": "system",
                    "content": "",
                    "timestamp": 100,
                    "visible_at": 100,
                    "gate_decision": "skip",
                    "skip_reason": "min_silence_not_elapsed",
                    "bedrock_invoked": False,
                },
            ]
        )
        original = config.ADMIN_TOKEN
        config.ADMIN_TOKEN = "secret-admin"
        try:
            with patch.object(chat_mod, "_now_ms", return_value=1_000):
                status, body = chat_mod.handle_chat_messages(
                    {"include_ticks": "true"},
                    CLAIMS,
                    headers={"X-Admin-Token": "secret-admin"},
                )
        finally:
            config.ADMIN_TOKEN = original

        assert status == 200
        ticks = [e for e in body["events"] if e["type"] == "tick"]
        assert len(ticks) == 1
        assert ticks[0]["gate_decision"] == "skip"
        assert ticks[0]["skip_reason"] == "min_silence_not_elapsed"

    def test_admin_token_mismatch_strips_tick_events(self) -> None:
        _seed_active_conversation(
            events=[
                {
                    "type": "tick",
                    "session_id": "ai_abc",
                    "sender": None,
                    "role": "system",
                    "content": "",
                    "timestamp": 100,
                    "visible_at": 100,
                    "gate_decision": "skip",
                },
            ]
        )
        original = config.ADMIN_TOKEN
        config.ADMIN_TOKEN = "secret-admin"
        try:
            with patch.object(chat_mod, "_now_ms", return_value=1_000):
                status, body = chat_mod.handle_chat_messages(
                    {"include_ticks": "true"},
                    CLAIMS,
                    headers={"X-Admin-Token": "wrong"},
                )
        finally:
            config.ADMIN_TOKEN = original

        assert status == 200
        assert all(e["type"] != "tick" for e in body["events"])

    def test_lobby_block_when_conversation_missing(self) -> None:
        # Pre-allocated conversation_id with an open lobby; no conv row.
        now_ms = 1_000_000
        lobby = mock_lobby.create_open_lobby(
            CHATROOM_ID,
            {
                "target_human_count": 3,
                "ai_join_strategy": "fixed_ai_count",
                "ai_strategy_value": 1,
                "max_wait_seconds": 60,
            },
            CONVERSATION_ID,
            now_ms,
        )
        # Manually bump actual_human_count so the lobby block is non-trivial.
        with mock_lobby._lock:
            mock_lobby._lobbies[lobby["lobby_id"]]["actual_human_count"] = 1
            mock_lobby._lobbies[lobby["lobby_id"]]["participants"] = [
                {"session_id": HUMAN_SESSION, "nickname": "Alice"}
            ]

        with patch.object(chat_mod, "_now_ms", return_value=now_ms + 1_000):
            status, body = chat_mod.handle_chat_messages({}, CLAIMS)

        assert status == 200
        assert body["events"] == []
        assert body["lobby"] is not None
        assert body["lobby"]["status"] == "open"
        assert body["lobby"]["actual_human_count"] == 1
        assert body["lobby"]["target_human_count"] == 3
        assert body["lobby"]["deadline_at"] == lobby["deadline_at"]

    def test_aborted_lobby_raises(self) -> None:
        now_ms = 1_000_000
        lobby = mock_lobby.create_open_lobby(
            CHATROOM_ID,
            {
                "target_human_count": 2,
                "ai_join_strategy": "fixed_ai_count",
                "ai_strategy_value": 1,
                "max_wait_seconds": 60,
            },
            CONVERSATION_ID,
            now_ms,
        )
        # Walk the lobby to "aborted" the same way ``close_lobby`` would.
        mock_lobby.update_lobby_status(
            lobby["lobby_id"],
            from_status="open",
            to_status="closing",
            now_ms=now_ms,
        )
        mock_lobby.set_lobby_aborted(lobby["lobby_id"], now_ms)

        with pytest.raises(LobbyAbortedException):
            chat_mod.handle_chat_messages({}, CLAIMS)

    def test_no_lobby_no_conversation_returns_404(self) -> None:
        status, body = chat_mod.handle_chat_messages({}, CLAIMS)
        assert status == 404
