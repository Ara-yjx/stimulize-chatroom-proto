"""Tests for the in-memory mock DynamoDB layer (v2: chatroom_setting + participants)."""

import time
from chatroom_api import mock_dynamo


SAMPLE_SETTING = {
    "mode": "one_on_one",
    "topic_instruction": "Be Sam.",
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
        mock_dynamo.reset()

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
        mock_dynamo.reset()

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

    def test_visible_at_defaults_to_timestamp_when_missing(self):
        """Back-compat helper: callers that only set ``timestamp`` get
        ``visible_at`` populated automatically so downstream filters work."""
        mock_dynamo.append_events("r1", "cr1", [_make_message(ts=100)])
        evt = mock_dynamo._rooms["r1"]["events"][0]
        assert evt["visible_at"] == 100

    def test_visible_at_preserved_when_provided(self):
        """Tick-model writers stack a typing delay on top; the helper must
        not clobber an explicit ``visible_at``."""
        evt = _make_message(ts=100)
        evt["visible_at"] = 5_000
        mock_dynamo.append_events("r1", "cr1", [evt])
        assert mock_dynamo._rooms["r1"]["events"][0]["visible_at"] == 5_000


class TestTickModelFieldsOnCreation:
    """Validates: Requirements 4.1 — tick-model fields land on the row at creation."""

    def setup_method(self):
        mock_dynamo.reset()

    def test_defaults_when_not_provided(self):
        mock_dynamo.append_events("r1", "cr1", [_make_message(ts=100)])
        row = mock_dynamo._rooms["r1"]
        assert row["status"] == "active"
        assert row["last_tick_at"] == 0
        assert row["last_speak_at_by_session"] == {}
        assert row["started_at"]  # any non-empty ISO timestamp

    def test_caller_supplied_values_used(self):
        mock_dynamo.append_events(
            "r1", "cr1", [_make_message(ts=100)],
            status="active",
            started_at="2025-01-01T00:00:00+00:00",
            last_tick_at=42,
            last_speak_at_by_session={"ai_001": 999},
        )
        row = mock_dynamo._rooms["r1"]
        assert row["status"] == "active"
        assert row["started_at"] == "2025-01-01T00:00:00+00:00"
        assert row["last_tick_at"] == 42
        assert row["last_speak_at_by_session"] == {"ai_001": 999}

    def test_subsequent_appends_do_not_touch_tick_fields(self):
        """Append-only semantics: the second call leaves the seeded
        tick-model fields untouched. Mutations go through the dedicated
        update helpers below."""
        mock_dynamo.append_events(
            "r1", "cr1", [_make_message(ts=100)],
            last_tick_at=42,
            last_speak_at_by_session={"ai_001": 999},
        )
        mock_dynamo.append_events(
            "r1", "cr1", [_make_message(ts=200)],
            last_tick_at=999_999,  # ignored
            last_speak_at_by_session={"ai_999": 1},  # ignored
        )
        row = mock_dynamo._rooms["r1"]
        assert row["last_tick_at"] == 42
        assert row["last_speak_at_by_session"] == {"ai_001": 999}


class TestUpdateLastTickAtConditional:
    """Validates: Correctness Properties §6.b — tick handler idempotency
    guard (single-conversation, single-thread example tests).

    All ``now_ms`` values are large enough that ``now_ms - dedupe_window_ms``
    exceeds the seeded ``last_tick_at=0`` from creation; this matches
    production reality (epoch ms ≫ window) and avoids tripping the very
    first-tick guard."""

    def setup_method(self):
        mock_dynamo.reset()
        mock_dynamo.append_events("r1", "cr1", [_make_message(ts=100)])

    def test_succeeds_when_no_recent_tick(self):
        ok = mock_dynamo.update_last_tick_at_conditional(
            "r1", now_ms=10_000, dedupe_window_ms=4_000
        )
        assert ok is True
        assert mock_dynamo._rooms["r1"]["last_tick_at"] == 10_000

    def test_fails_when_a_tick_fired_within_window(self):
        """Spec example: call at 3000 then at 5000 with a 4000ms window —
        the second call sees a threshold of 1000 and 3000 ≥ 1000 so it
        rejects."""
        # Seed an explicit recent tick so the first call wins.
        assert mock_dynamo.update_last_tick_at_conditional(
            "r1", now_ms=10_000, dedupe_window_ms=4_000
        )
        # Now another tick within the window — should reject.
        ok = mock_dynamo.update_last_tick_at_conditional(
            "r1", now_ms=12_000, dedupe_window_ms=4_000
        )
        assert ok is False
        assert mock_dynamo._rooms["r1"]["last_tick_at"] == 10_000

    def test_succeeds_after_window_elapsed(self):
        assert mock_dynamo.update_last_tick_at_conditional(
            "r1", now_ms=10_000, dedupe_window_ms=4_000
        )
        ok = mock_dynamo.update_last_tick_at_conditional(
            "r1", now_ms=20_000, dedupe_window_ms=4_000
        )
        assert ok is True
        assert mock_dynamo._rooms["r1"]["last_tick_at"] == 20_000

    def test_returns_false_when_row_missing(self):
        assert mock_dynamo.update_last_tick_at_conditional(
            "no-such-room", now_ms=10_000, dedupe_window_ms=4_000
        ) is False

    def test_spec_verification_example(self):
        """Verification example from task 3.1: ``update(5000, 4000)`` after
        ``update(3000, 4000)`` returns False because 3000 + 4000 = 7000 >
        5000."""
        # Reset so we don't have the seeded last_tick_at=0 from setup.
        mock_dynamo.reset()
        mock_dynamo.append_events(
            "r1", "cr1", [_make_message(ts=100)],
            # last_tick_at intentionally omitted — defaults to 0.
        )
        # Force an explicit tick at 3000 (window=4000 → threshold=-1000;
        # 0 ≥ -1000 ⇒ rejects). Skip that and seed last_tick_at directly.
        mock_dynamo._rooms["r1"]["last_tick_at"] = 3_000
        ok = mock_dynamo.update_last_tick_at_conditional(
            "r1", now_ms=5_000, dedupe_window_ms=4_000
        )
        assert ok is False
        assert mock_dynamo._rooms["r1"]["last_tick_at"] == 3_000


class TestUpdateStatus:
    def setup_method(self):
        mock_dynamo.reset()
        mock_dynamo.append_events("r1", "cr1", [_make_message(ts=100)])

    def test_flips_active_to_ended(self):
        ok = mock_dynamo.update_status("r1", "ended")
        assert ok is True
        assert mock_dynamo._rooms["r1"]["status"] == "ended"

    def test_returns_false_for_missing_row(self):
        assert mock_dynamo.update_status("no-such-room", "ended") is False


class TestUpdateLastSpeakAt:
    def setup_method(self):
        mock_dynamo.reset()
        mock_dynamo.append_events("r1", "cr1", [_make_message(ts=100)])

    def test_writes_per_session_entry(self):
        ok = mock_dynamo.update_last_speak_at("r1", "ai_001", 5_000)
        assert ok is True
        assert mock_dynamo._rooms["r1"]["last_speak_at_by_session"] == {
            "ai_001": 5_000
        }

    def test_overwrites_existing_entry(self):
        mock_dynamo.update_last_speak_at("r1", "ai_001", 5_000)
        mock_dynamo.update_last_speak_at("r1", "ai_001", 9_000)
        assert mock_dynamo._rooms["r1"]["last_speak_at_by_session"]["ai_001"] == 9_000

    def test_independent_keys_coexist(self):
        mock_dynamo.update_last_speak_at("r1", "ai_001", 5_000)
        mock_dynamo.update_last_speak_at("r1", "ai_002", 7_000)
        assert mock_dynamo._rooms["r1"]["last_speak_at_by_session"] == {
            "ai_001": 5_000,
            "ai_002": 7_000,
        }


class TestGetConversation:
    def setup_method(self):
        mock_dynamo.reset()

    def test_returns_full_row_with_tick_fields(self):
        mock_dynamo.append_events(
            "r1", "cr1", [_make_message(ts=100)],
            chatroom_setting=SAMPLE_SETTING,
            participants=SAMPLE_PARTICIPANTS,
            started_at="2025-01-01T00:00:00+00:00",
        )
        conv = mock_dynamo.get_conversation("r1")
        assert conv is not None
        assert conv["conversation_id"] == "r1"
        assert conv["status"] == "active"
        assert conv["started_at"] == "2025-01-01T00:00:00+00:00"
        assert conv["last_tick_at"] == 0
        assert conv["last_speak_at_by_session"] == {}
        assert conv["chatroom_setting"] == SAMPLE_SETTING
        assert conv["participants"] == SAMPLE_PARTICIPANTS

    def test_returns_none_for_missing(self):
        assert mock_dynamo.get_conversation("nope") is None


class TestGetConversationConfig:
    def setup_method(self):
        mock_dynamo.reset()

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
        mock_dynamo.reset()

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
