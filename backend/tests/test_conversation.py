"""Tests for conversation history → Bedrock message mapping."""

from chatroom_api.conversation import build_bedrock_messages


def _msg(sender, role, content, ai_participant_id=None):
    """Helper to build a DynamoDB-style message dict."""
    return {
        "session_id": "sess-1",
        "sender": sender,
        "role": role,
        "ai_participant_id": ai_participant_id,
        "content": content,
        "timestamp": 1000,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


class TestSingleUserSingleAI:
    """Basic case: one human, one AI."""

    def test_simple_exchange(self):
        messages = [
            _msg("Alice", "user", "hey"),
            _msg("Sam", "ai", "hello!", ai_participant_id="ai_001"),
        ]
        result = build_bedrock_messages(messages, "ai_001")

        assert len(result) == 2
        assert result[0] == {"role": "user", "content": [{"text": "[Alice] hey"}]}
        assert result[1] == {"role": "assistant", "content": [{"text": "hello!"}]}


class TestMultiUserMultiAI:
    """Multi-participant scenario with consecutive message merging."""

    def test_lld_example(self):
        """Exact example from the low-level design doc."""
        messages = [
            _msg("Alice", "user", "hey what's up"),
            _msg("Sam", "ai", "not much, just chilling", ai_participant_id="ai_001"),
            _msg("Bob", "user", "anyone else here?"),
            _msg("Eve", "ai", "yeah I'm here too", ai_participant_id="ai_002"),
            _msg("Alice", "user", "cool"),
        ]
        result = build_bedrock_messages(messages, "ai_001")

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
        messages = [
            _msg("Alice", "user", "hi"),
            _msg("Bob", "user", "hey"),
        ]
        result = build_bedrock_messages(messages, "ai_001")

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"][0]["text"] == "[Alice] hi\n[Bob] hey"

    def test_consecutive_assistant_messages_merged(self):
        """Two messages from the same AI should merge into one assistant turn."""
        messages = [
            _msg("Alice", "user", "tell me two things"),
            _msg("Sam", "ai", "first thing", ai_participant_id="ai_001"),
            _msg("Sam", "ai", "second thing", ai_participant_id="ai_001"),
        ]
        result = build_bedrock_messages(messages, "ai_001")

        assert len(result) == 2
        assert result[1]["role"] == "assistant"
        assert result[1]["content"][0]["text"] == "first thing\nsecond thing"


class TestEmptyMessages:
    def test_empty_list_returns_empty(self):
        assert build_bedrock_messages([], "ai_001") == []


class TestOnlyAIMessages:
    """All messages from the target AI — should produce only assistant turns."""

    def test_only_assistant_turns(self):
        messages = [
            _msg("Sam", "ai", "hello", ai_participant_id="ai_001"),
            _msg("Sam", "ai", "anyone there?", ai_participant_id="ai_001"),
        ]
        result = build_bedrock_messages(messages, "ai_001")

        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["content"][0]["text"] == "hello\nanyone there?"
