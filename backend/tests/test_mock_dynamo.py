"""Tests for the in-memory mock DynamoDB layer (v2: chatroom_setting + participants)."""

import time
from chatroom_api import mock_dynamo


SAMPLE_SETTING = {
    "mode": "one_on_one",
    "mimic_human": True,
    "system_prompt": "Be Sam.",
    "model_id": "anthropic.claude-sonnet-4-6",
    "simulate_pairing_seconds": 3,
    "timer_min_minutes": 5,
    "timer_max_minutes": 10,
}

SAMPLE_PARTICIPANTS = [
    {"session_id": "sess-1", "nickname": "Alice", "avatar": {"emojiText": "🐱"}, "role": "human"},
    {"session_id": "ai_001", "nickname": "Sam", "avatar": {"emojiText": "🐶"}, "role": "ai"},
]


def _make_message(sender="Alice", role="user", content="hello", ts=None):
    return {
        "session_id": "sess-1",
        "sender": sender,
        "role": role,
        "ai_participant_id": None,
        "content": content,
        "timestamp": ts or int(time.time() * 1000),
        "created_at": "2024-01-01T00:00:00+00:00",
    }


class TestGetEvents:
    def setup_method(self):
        mock_dynamo._rooms.clear()

    def test_returns_empty_for_nonexistent_room(self):
        assert mock_dynamo.get_events("no-such-room") == []

    def test_returns_all_messages_when_after_is_zero(self):
        mock_dynamo.append_events("r1", "cr1", [_make_message(ts=100)])
        assert len(mock_dynamo.get_events("r1", after=0)) == 1

    def test_filters_by_after_timestamp(self):
        mock_dynamo.append_events("r1", "cr1", [
            _make_message(ts=100),
            _make_message(ts=200),
            _make_message(ts=300),
        ])
        msgs = mock_dynamo.get_events("r1", after=150)
        assert len(msgs) == 2


class TestAppendEvents:
    def setup_method(self):
        mock_dynamo._rooms.clear()

    def test_creates_room_on_first_append(self):
        mock_dynamo.append_events("r1", "cr1", [_make_message(ts=100)])
        room = mock_dynamo._rooms["r1"]
        assert room["conversation_id"] == "r1"
        assert room["chatroom_id"] == "cr1"
        assert len(room["events"]) == 1

    def test_stores_chatroom_setting_and_participants(self):
        mock_dynamo.append_events(
            "r1", "cr1", [_make_message(ts=100)],
            chatroom_setting=SAMPLE_SETTING,
            participants=SAMPLE_PARTICIPANTS,
        )
        room = mock_dynamo._rooms["r1"]
        assert room["chatroom_setting"] == SAMPLE_SETTING
        assert room["participants"] == SAMPLE_PARTICIPANTS

    def test_appends_to_existing_room(self):
        mock_dynamo.append_events("r1", "cr1", [_make_message(ts=100)])
        mock_dynamo.append_events("r1", "cr1", [_make_message(ts=200)])
        assert len(mock_dynamo._rooms["r1"]["events"]) == 2


class TestGetConversationConfig:
    def setup_method(self):
        mock_dynamo._rooms.clear()

    def test_returns_none_for_nonexistent_room(self):
        assert mock_dynamo.get_conversation_config("no-such-room") is None

    def test_returns_setting_when_stored(self):
        mock_dynamo.append_events(
            "r1", "cr1", [_make_message(ts=100)],
            chatroom_setting=SAMPLE_SETTING,
        )
        cfg = mock_dynamo.get_conversation_config("r1")
        assert cfg["mode"] == "one_on_one"


class TestGetParticipants:
    def setup_method(self):
        mock_dynamo._rooms.clear()

    def test_returns_none_for_nonexistent_room(self):
        assert mock_dynamo.get_participants("no-such-room") is None

    def test_returns_participants_when_stored(self):
        mock_dynamo.append_events(
            "r1", "cr1", [_make_message(ts=100)],
            chatroom_setting=SAMPLE_SETTING,
            participants=SAMPLE_PARTICIPANTS,
        )
        parts = mock_dynamo.get_participants("r1")
        assert len(parts) == 2
        assert parts[0]["role"] == "human"
        assert parts[1]["role"] == "ai"
        assert "emojiText" in parts[0]["avatar"]
