"""Tests for chatroom_api.chat — handle_chat_send and handle_chat_messages (v2)."""

from unittest.mock import patch, MagicMock

from chatroom_api.chat import handle_chat_send, handle_chat_messages


CLAIMS = {
    "session_id": "sess_1",
    "conversation_id": "conv_1",
    "chatroom_id": "cr_001",
}

PARTICIPANTS = [
    {"session_id": "sess_1", "nickname": "Alice", "avatar": {"emojiText": "🐱"}, "role": "human"},
    {"session_id": "ai_abc123", "nickname": "Sam", "avatar": {"emojiText": "🐶"}, "role": "ai"},
]

CHATROOM_SETTING = {
    "mode": "one_on_one",
    "mimic_human": True,
    "system_prompt": "You are Sam.",
    "model_id": "anthropic.claude-sonnet-4-6",
    "simulate_pairing_seconds": 3,
    "timer_min_minutes": 5,
    "timer_max_minutes": 10,
}


class TestHandleChatSend:

    @patch("chatroom_api.chat.bedrock_client")
    @patch("chatroom_api.chat.config")
    def test_happy_path_returns_replies(self, mock_config, mock_bedrock):
        mock_config.USE_MOCK_DYNAMO = True
        mock_config.USE_MOCK_RDS = True

        mock_bedrock.invoke.return_value = {
            "text": "Hey Alice!",
            "input_tokens": 10,
            "output_tokens": 5,
        }

        mock_db = MagicMock()
        mock_db.get_events.return_value = []
        mock_db.get_conversation_config.return_value = CHATROOM_SETTING
        mock_db.get_participants.return_value = PARTICIPANTS

        mock_rds = MagicMock()

        with patch("chatroom_api.chat._get_db", return_value=mock_db), \
             patch("chatroom_api.chat._get_rds", return_value=mock_rds):
            status, body = handle_chat_send({"message": "hello"}, CLAIMS)

        assert status == 200
        assert len(body["replies"]) == 1
        assert body["replies"][0]["nickname"] == "Sam"
        assert body["replies"][0]["content"] == "Hey Alice!"
        assert body["replies"][0]["avatar"] == {"emojiText": "🐶"}

        mock_db.append_events.assert_called_once()
        mock_rds.write_usage.assert_called_once()

    @patch("chatroom_api.chat.config")
    def test_missing_conversation_config_returns_500(self, mock_config):
        mock_config.USE_MOCK_DYNAMO = True
        mock_config.USE_MOCK_RDS = True

        mock_db = MagicMock()
        mock_db.get_events.return_value = []
        mock_db.get_conversation_config.return_value = None

        with patch("chatroom_api.chat._get_db", return_value=mock_db):
            status, body = handle_chat_send({"message": "hello"}, CLAIMS)

        assert status == 500
        assert body["error"] == "conversation config not found"

    @patch("chatroom_api.chat.config")
    def test_missing_participants_returns_500(self, mock_config):
        mock_config.USE_MOCK_DYNAMO = True
        mock_config.USE_MOCK_RDS = True

        mock_db = MagicMock()
        mock_db.get_events.return_value = []
        mock_db.get_conversation_config.return_value = CHATROOM_SETTING
        mock_db.get_participants.return_value = None

        with patch("chatroom_api.chat._get_db", return_value=mock_db):
            status, body = handle_chat_send({"message": "hello"}, CLAIMS)

        assert status == 500
        assert body["error"] == "participants not found"

    @patch("chatroom_api.chat.bedrock_client")
    @patch("chatroom_api.chat.config")
    def test_rds_usage_failure_ignored(self, mock_config, mock_bedrock):
        mock_config.USE_MOCK_DYNAMO = True
        mock_config.USE_MOCK_RDS = True

        mock_bedrock.invoke.return_value = {
            "text": "Hi!",
            "input_tokens": 5,
            "output_tokens": 3,
        }

        mock_db = MagicMock()
        mock_db.get_events.return_value = []
        mock_db.get_conversation_config.return_value = CHATROOM_SETTING
        mock_db.get_participants.return_value = PARTICIPANTS

        mock_rds = MagicMock()
        mock_rds.write_usage.side_effect = Exception("RDS down")

        with patch("chatroom_api.chat._get_db", return_value=mock_db), \
             patch("chatroom_api.chat._get_rds", return_value=mock_rds):
            status, body = handle_chat_send({"message": "hey"}, CLAIMS)

        assert status == 200
        assert len(body["replies"]) == 1


class TestHandleChatMessages:

    @patch("chatroom_api.chat.config")
    def test_returns_messages_with_avatar(self, mock_config):
        mock_config.USE_MOCK_DYNAMO = True

        stored = [
            {
                "type": "message",
                "session_id": "sess_1",
                "sender": "Alice",
                "role": "user",
                "ai_participant_id": None,
                "content": "hello",
                "timestamp": 1000,
                "created_at": "2024-01-01T00:00:00",
            },
            {
                "type": "message",
                "session_id": "sess_1",
                "sender": "Sam",
                "role": "ai",
                "ai_participant_id": "ai_abc123",
                "content": "hi there",
                "timestamp": 2000,
                "created_at": "2024-01-01T00:00:01",
            },
        ]

        mock_db = MagicMock()
        mock_db.get_events.return_value = stored
        mock_db.get_participants.return_value = PARTICIPANTS

        with patch("chatroom_api.chat._get_db", return_value=mock_db):
            status, body = handle_chat_messages({}, CLAIMS)

        assert status == 200
        assert len(body["events"]) == 2
        assert body["events"][0]["avatar"] == {"emojiText": "🐱"}
        assert body["events"][1]["avatar"] == {"emojiText": "🐶"}

    @patch("chatroom_api.chat.config")
    def test_after_filter_passed_to_db(self, mock_config):
        mock_config.USE_MOCK_DYNAMO = True

        mock_db = MagicMock()
        mock_db.get_events.return_value = []
        mock_db.get_participants.return_value = []

        with patch("chatroom_api.chat._get_db", return_value=mock_db):
            status, body = handle_chat_messages({"after": "5000"}, CLAIMS)

        assert status == 200
        mock_db.get_events.assert_called_once_with("conv_1", after=5000)
