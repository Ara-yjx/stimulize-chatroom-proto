"""Tests for chatroom_api.chat send, live polling, and history paging.

Per beta tasks 3.3, 3.4, 3.5:
- ``/chat/send`` never invokes Bedrock directly; it appends the human's
  message event and returns the same shape as ``/chat/messages``. Only the
  single-human, single-AI, non-mimic preset wakes the tick handler directly.
- ``/chat/messages`` returns available history and surfaces a ``lobby`` block
  while the conversation row is missing.
- Aborted lobbies bubble up as ``LobbyAbortedException`` (mapped to 410 by
  ``handler.py``).
"""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest

from chatroom_api import (
    chat as chat_mod,
    config,
    mock_dynamo,
    mock_lobby,
)
from chatroom_api.errors import LobbyAbortedException
from chatroom_api.cursors import encode_cursor, make_event_key


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


def _seed_active_conversation(
    events=None,
    status="active",
    chatroom_setting=None,
) -> None:
    """Seed an active 1-on-1-like conversation with Alice + Sam."""
    mock_dynamo.append_events(
        CONVERSATION_ID,
        CHATROOM_ID,
        events or [],
        chatroom_setting=chatroom_setting or {"mode": "one_on_one"},
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
        # The single appended event is the canonical human message.
        assert len(body["events"]) == 1
        evt = body["events"][0]
        assert evt["type"] == "message"
        assert evt["sender"] == "Alice"
        assert evt["role"] == "human"
        assert evt["content"] == "hello"
        assert evt["timestamp"] == 1_000_000
        assert "visible_at" not in evt
        assert evt["event_id"]
        assert evt["avatar"] == {"emojiText": "🐱"}

        # The store actually has the new event.
        stored = mock_dynamo.get_events(CONVERSATION_ID)
        assert len(stored) == 1
        assert stored[0]["content"] == "hello"

    def test_send_does_not_invoke_bedrock_directly(self) -> None:
        """The API handler may wake a tick but never imports Bedrock."""
        _seed_active_conversation()

        # If anything tries to import bedrock_client from chat.py, the test
        # would have failed at import time; we also assert no symbol lookup.
        assert not hasattr(chat_mod, "bedrock_client")

        status, body = chat_mod.handle_chat_send({"message": "hi"}, CLAIMS)
        assert status == 200, body

    def test_send_triggers_tick_only_for_single_non_mimic_assistant(self) -> None:
        _seed_active_conversation(chatroom_setting={
            "human_count": 1,
            "ai_count": 1,
            "mimic_human": False,
        })
        lambda_client = Mock()

        with patch.object(
            config,
            "TICK_HANDLER_LAMBDA",
            "chatroom-tick-handler",
        ), patch.object(
            chat_mod,
            "_get_lambda_client",
            return_value=lambda_client,
        ):
            status, body = chat_mod.handle_chat_send({"message": "hi"}, CLAIMS)

        assert status == 200, body
        lambda_client.invoke.assert_called_once()
        invoke = lambda_client.invoke.call_args.kwargs
        assert invoke["FunctionName"] == "chatroom-tick-handler"
        assert invoke["InvocationType"] == "Event"
        assert json.loads(invoke["Payload"]) == {
            "conversation_id": CONVERSATION_ID,
        }

    @pytest.mark.parametrize(
        "chatroom_setting",
        [
            {"human_count": 1, "ai_count": 1, "mimic_human": True},
            {"human_count": 1, "ai_count": 2, "mimic_human": False},
            {"human_count": 2, "ai_count": 1, "mimic_human": False},
        ],
    )
    def test_send_does_not_trigger_tick_for_other_rooms(
        self,
        chatroom_setting,
    ) -> None:
        _seed_active_conversation(chatroom_setting=chatroom_setting)
        lambda_client = Mock()

        with patch.object(
            config,
            "TICK_HANDLER_LAMBDA",
            "chatroom-tick-handler",
        ), patch.object(
            chat_mod,
            "_get_lambda_client",
            return_value=lambda_client,
        ):
            status, body = chat_mod.handle_chat_send({"message": "hi"}, CLAIMS)

        assert status == 200, body
        lambda_client.invoke.assert_not_called()

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

    def test_ended_conversation_reports_future_scheduled_history(self) -> None:
        _seed_active_conversation(
            status="ended",
            events=[
                {"type": "message", "role": "human", "session_id": HUMAN_SESSION, "sender": "Alice", "content": "now", "timestamp": 100},
                {"type": "message", "role": "ai", "session_id": "ai_abc", "sender": "Sam", "content": "later", "timestamp": 5000},
            ],
        )
        with patch.object(chat_mod, "_now_ms", return_value=1000):
            status, body = chat_mod.handle_chat_messages({"after": "0"}, CLAIMS)

        assert status == 200
        assert [event["content"] for event in body["events"]] == ["now"]
        assert body["conversation_status"] == "ended"
        assert body["next_pending_at"] == 5000

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

    def test_tick_events_are_never_returned_from_history(self) -> None:
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
        assert ticks == []

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


class TestHandleChatHistory:
    def setup_method(self) -> None:
        _setup_mocks()

    def test_returns_newest_page_in_display_order(self) -> None:
        _seed_active_conversation(events=[
            {
                "type": "message",
                "role": "human",
                "session_id": HUMAN_SESSION,
                "sender": "Alice",
                "content": str(index),
                "timestamp": index,
            }
            for index in range(1, 5)
        ])
        with patch.object(chat_mod, "_now_ms", return_value=10):
            status, body = chat_mod.handle_chat_history({"limit": "2"}, CLAIMS)

        assert status == 200
        assert [event["content"] for event in body["events"]] == ["3", "4"]
        assert body["has_more"] is True
        assert body["next_before"]
        assert body["latest_cursor"]

    def test_rejects_cross_conversation_cursor(self) -> None:
        _seed_active_conversation()
        other_cursor = encode_cursor(
            "conv_other",
            make_event_key(1, "batch", 0),
        )
        status, body = chat_mod.handle_chat_history(
            {"before": other_cursor},
            CLAIMS,
        )

        assert status == 400
        assert body == {"error": "invalid_cursor"}
