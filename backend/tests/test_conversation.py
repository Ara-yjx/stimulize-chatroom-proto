"""Tests for conversation history → Bedrock message mapping.

The function under test is ``build_bedrock_messages(conv, ai_session_id,
now)``. See ``docs/design.md`` "Algorithmic Pseudocode → Bedrock history
mapping" for the spec.
"""

from chatroom_api.conversation import build_bedrock_messages


# Sentinel "now" set far in the future so events with ``visible_at`` based
# on small timestamps are always considered visible.
NOW_FAR = 10**12


def _msg(session_id, sender, content, *, timestamp=1000, visible_at=None):
    """Build a beta-schema message event."""
    event = {
        "type": "message",
        "session_id": session_id,
        "sender": sender,
        "content": content,
        "timestamp": timestamp,
        "visible_at": timestamp if visible_at is None else visible_at,
    }
    return event


def _conv(events, participants=None):
    return {
        "events": events,
        "participants": participants
        or [
            {"session_id": "h1", "nickname": "Alice"},
            {"session_id": "h2", "nickname": "Bob"},
            {"session_id": "ai_001", "nickname": "Sam"},
            {"session_id": "ai_002", "nickname": "Eve"},
        ],
    }


class TestSingleUserSingleAI:
    """Basic case: one human, one AI."""

    def test_simple_exchange(self):
        events = [
            _msg("h1", "Alice", "hey"),
            _msg("ai_001", "Sam", "hello!"),
        ]
        result = build_bedrock_messages(_conv(events), "ai_001", NOW_FAR)

        assert result == [
            {"role": "user", "content": [{"text": "[Alice] hey"}]},
            {"role": "assistant", "content": [{"text": "hello!"}]},
        ]


class TestMultiUserMultiAI:
    """Multi-participant scenario with consecutive message merging."""

    def test_design_example(self):
        """Mirrors the example documented for the new mapping rule."""
        events = [
            _msg("h1", "Alice", "hey what's up"),
            _msg("ai_001", "Sam", "not much, just chilling"),
            _msg("h2", "Bob", "anyone else here?"),
            _msg("ai_002", "Eve", "yeah I'm here too"),
            _msg("h1", "Alice", "cool"),
        ]
        result = build_bedrock_messages(_conv(events), "ai_001", NOW_FAR)

        assert len(result) == 3
        assert result[0] == {
            "role": "user",
            "content": [{"text": "[Alice] hey what's up"}],
        }
        assert result[1] == {
            "role": "assistant",
            "content": [{"text": "not much, just chilling"}],
        }
        assert result[2] == {
            "role": "user",
            "content": [
                {
                    "text": (
                        "[Bob] anyone else here?\n"
                        "[Eve] yeah I'm here too\n"
                        "[Alice] cool"
                    )
                }
            ],
        }

    def test_consecutive_user_messages_merged(self):
        events = [
            _msg("h1", "Alice", "hi"),
            _msg("h2", "Bob", "hey"),
        ]
        result = build_bedrock_messages(_conv(events), "ai_001", NOW_FAR)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"][0]["text"] == "[Alice] hi\n[Bob] hey"

    def test_consecutive_assistant_messages_merged(self):
        """Two messages from the same AI merge into one assistant turn."""
        events = [
            _msg("h1", "Alice", "tell me two things"),
            _msg("ai_001", "Sam", "first thing"),
            _msg("ai_001", "Sam", "second thing"),
        ]
        result = build_bedrock_messages(_conv(events), "ai_001", NOW_FAR)

        assert len(result) == 2
        assert result[1]["role"] == "assistant"
        assert result[1]["content"][0]["text"] == "first thing\nsecond thing"


class TestEmptyAndAllFiltered:
    def test_empty_events_returns_empty(self):
        assert build_bedrock_messages(_conv([]), "ai_001", NOW_FAR) == []

    def test_all_filtered_out_by_visible_at(self):
        events = [
            _msg("h1", "Alice", "hi", timestamp=500, visible_at=500),
            _msg("ai_001", "Sam", "hello", timestamp=600, visible_at=600),
        ]
        # now < every visible_at, so nothing should appear.
        assert build_bedrock_messages(_conv(events), "ai_001", now=100) == []


class TestFilteringRules:
    def test_skips_system_events(self):
        events = [
            {
                "type": "system",
                "session_id": None,
                "sender": "System",
                "content": "Chatroom created",
                "timestamp": 100,
                "visible_at": 100,
            },
            _msg("h1", "Alice", "hi", timestamp=200),
        ]
        result = build_bedrock_messages(_conv(events), "ai_001", NOW_FAR)

        assert result == [
            {"role": "user", "content": [{"text": "[Alice] hi"}]}
        ]

    def test_skips_tick_events(self):
        events = [
            _msg("h1", "Alice", "hi", timestamp=100),
            {
                "type": "tick",
                "session_id": None,
                "gate_decision": "skip",
                "skip_reason": "min_silence_not_elapsed",
                "timestamp": 150,
                "visible_at": 150,
            },
            _msg("ai_001", "Sam", "hey", timestamp=200),
        ]
        result = build_bedrock_messages(_conv(events), "ai_001", NOW_FAR)

        assert result == [
            {"role": "user", "content": [{"text": "[Alice] hi"}]},
            {"role": "assistant", "content": [{"text": "hey"}]},
        ]

    def test_visible_at_cutoff_filters_pending_events(self):
        events = [
            _msg("h1", "Alice", "hi", timestamp=100, visible_at=100),
            _msg("ai_001", "Sam", "hello", timestamp=110, visible_at=120),
            _msg("h1", "Alice", "yo", timestamp=200, visible_at=200),
        ]
        # now=150 sees Alice's first message and Sam's reply (visible_at=120),
        # but not Alice's "yo" yet.
        result = build_bedrock_messages(_conv(events), "ai_001", now=150)

        assert result == [
            {"role": "user", "content": [{"text": "[Alice] hi"}]},
            {"role": "assistant", "content": [{"text": "hello"}]},
        ]

    def test_visible_at_falls_back_to_timestamp(self):
        events = [
            # No ``visible_at`` field — fallback should be ``timestamp``.
            {
                "type": "message",
                "session_id": "h1",
                "sender": "Alice",
                "content": "hi",
                "timestamp": 100,
            },
        ]
        # now < timestamp: filtered out.
        assert build_bedrock_messages(_conv(events), "ai_001", now=50) == []
        # now >= timestamp: included.
        result = build_bedrock_messages(_conv(events), "ai_001", now=200)
        assert result == [
            {"role": "user", "content": [{"text": "[Alice] hi"}]}
        ]


class TestSenderFallback:
    def test_falls_back_to_participant_nickname_when_sender_missing(self):
        events = [
            {
                "type": "message",
                "session_id": "h1",
                # no ``sender`` key
                "content": "hi",
                "timestamp": 100,
                "visible_at": 100,
            },
        ]
        participants = [
            {"session_id": "h1", "nickname": "Alice"},
            {"session_id": "ai_001", "nickname": "Sam"},
        ]
        result = build_bedrock_messages(
            {"events": events, "participants": participants}, "ai_001", NOW_FAR
        )
        assert result == [
            {"role": "user", "content": [{"text": "[Alice] hi"}]}
        ]

    def test_default_to_participant_when_no_sender_and_no_match(self):
        events = [
            {
                "type": "message",
                "session_id": "unknown",
                "content": "hi",
                "timestamp": 100,
                "visible_at": 100,
            },
        ]
        result = build_bedrock_messages(_conv(events), "ai_001", NOW_FAR)
        assert result == [
            {"role": "user", "content": [{"text": "[Participant] hi"}]}
        ]


class TestPurity:
    """Function must be pure: no I/O, no clock reads, no input mutation."""

    def test_does_not_mutate_input(self):
        events = [
            _msg("h1", "Alice", "hi"),
            _msg("ai_001", "Sam", "hey"),
        ]
        snapshot = [dict(e) for e in events]
        conv = _conv(events)
        build_bedrock_messages(conv, "ai_001", NOW_FAR)
        # Events list and individual events are unchanged.
        assert events == snapshot
        assert conv["events"] is events  # same identity, not replaced

    def test_deterministic_given_inputs(self):
        events = [
            _msg("h1", "Alice", "hi"),
            _msg("ai_001", "Sam", "hey"),
            _msg("h2", "Bob", "yo"),
        ]
        conv = _conv(events)
        a = build_bedrock_messages(conv, "ai_001", NOW_FAR)
        b = build_bedrock_messages(conv, "ai_001", NOW_FAR)
        assert a == b
