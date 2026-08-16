"""Tests for chatroom_api.auth (v2: chatroom_id only, no management API)."""

from unittest.mock import patch, MagicMock
from chatroom_api import mock_dynamo, mock_lobby
from chatroom_api.auth import handle_auth_token

SAMPLE_CHATROOM = {
    "id": "scid_test-chatroom-001",
    "owner_id": "user_001",
    "name": "College Chat",
    "status": "active",
    "setting": {
        "topic_instruction": "Talk about your college life.",
        "model_id": "global.anthropic.claude-sonnet-4-6",
        "simulate_pairing_seconds": 0,
        "timer_min_minutes": 5,
        "timer_max_minutes": 10,
        "human_count": 1,
        "ai_count": 1,
        "replace_human_with_ai": False,
    },
}


def _mocks():
    rds = MagicMock()
    rds.get_chatroom.return_value = SAMPLE_CHATROOM
    db = MagicMock()
    return rds, db


class TestHandleAuthTokenV2:

    @staticmethod
    def _assistant_chatroom(*, ai_nickname="", personas=None):
        return {
            **SAMPLE_CHATROOM,
            "setting": {
                **SAMPLE_CHATROOM["setting"],
                "mimic_human": False,
                "ai_nickname": ai_nickname,
                "ai_personas": personas or [],
            },
        }

    @staticmethod
    def _run_assistant_auth(chatroom):
        mock_lobby.reset()
        mock_dynamo.reset()
        rds = MagicMock()
        rds.get_chatroom.return_value = chatroom
        with patch("chatroom_api.auth._get_rds", return_value=rds), \
             patch("chatroom_api.close_lobby._get_rds", return_value=rds), \
             patch("chatroom_api.auth.jwt_utils") as jwt_mock:
            jwt_mock.create_token.return_value = "t"
            status, body = handle_auth_token({"chatroom_id": chatroom["id"]})
        return status, body, mock_dynamo.get_conversation(body["conversation_id"])

    def test_valid_chatroom_returns_session_info(self):
        mock_lobby.reset()
        mock_dynamo.reset()
        rds, _db = _mocks()
        with patch("chatroom_api.auth._get_rds", return_value=rds), \
             patch("chatroom_api.auth.jwt_utils") as jwt_mock:
            jwt_mock.create_token.return_value = "fake-token"
            s, b = handle_auth_token({"chatroom_id": "scid_test-chatroom-001"})
        assert s == 200
        assert b["token"] == "fake-token"
        assert b["nickname"].startswith("Participant")
        assert "emojiText" in b["avatar"]
        assert "mode" not in b["chatroom_setting"]
        assert b["chatroom_setting"]["human_count"] == 1
        assert b["chatroom_setting"]["ai_count"] == 1

    def test_participant_id_is_ignored_when_resume_is_not_enabled(self):
        """A participant hint must not alter the existing lobby/JWT contract."""
        mock_lobby.reset()
        mock_dynamo.reset()
        rds, _db = _mocks()
        with patch("chatroom_api.auth._get_rds", return_value=rds), \
             patch("chatroom_api.auth.jwt_utils") as jwt_mock:
            jwt_mock.create_token.return_value = "legacy-token"
            status, body = handle_auth_token({
                "chatroom_id": SAMPLE_CHATROOM["id"],
                "participant_id": "must-not-enable-resume",
            })

        assert status == 200
        assert body["token"] == "legacy-token"
        assert body["lobby"]["status"] == "closed"
        assert "participant_id" not in body
        assert "connection_id" not in body
        assert "episode_number" not in body
        jwt_mock.create_token.assert_called_once_with(
            body["session_id"], body["conversation_id"], SAMPLE_CHATROOM["id"]
        )
        conversation = mock_dynamo.get_conversation(body["conversation_id"])
        assert conversation["status"] == "active"
        assert "resumable" not in conversation
        assert "active_episode_number" not in conversation

    def test_not_found_returns_404(self):
        rds = MagicMock()
        rds.get_chatroom.return_value = None
        with patch("chatroom_api.auth._get_rds", return_value=rds):
            s, b = handle_auth_token({"chatroom_id": "scid_nope"})
        assert s == 404

    def test_inactive_returns_401(self):
        rds = MagicMock()
        rds.get_chatroom.return_value = {**SAMPLE_CHATROOM, "status": "inactive"}
        with patch("chatroom_api.auth._get_rds", return_value=rds):
            s, b = handle_auth_token({"chatroom_id": "scid_test-chatroom-001"})
        assert s == 401
        assert "inactive" in b["error"]

    def test_missing_chatroom_id_returns_400(self):
        s, b = handle_auth_token({})
        assert s == 400

    def test_participants_stored(self):
        mock_lobby.reset()
        mock_dynamo.reset()
        rds, _db = _mocks()
        with patch("chatroom_api.auth._get_rds", return_value=rds), \
             patch("chatroom_api.auth.jwt_utils") as jwt_mock:
            jwt_mock.create_token.return_value = "t"
            _status, body = handle_auth_token({"chatroom_id": "scid_test-chatroom-001"})
        parts = mock_dynamo.get_conversation(body["conversation_id"])["participants"]
        assert len(parts) == 2
        assert {p["role"] for p in parts} == {"human", "ai"}

    def test_simulated_wait_keeps_conversation_uncreated_until_lobby_closes(self):
        mock_lobby.reset()
        mock_dynamo.reset()
        chatroom = {
            **SAMPLE_CHATROOM,
            "setting": {
                **SAMPLE_CHATROOM["setting"],
                "mimic_human": True,
                "simulate_pairing_seconds": 15,
            },
        }
        rds = MagicMock()
        rds.get_chatroom.return_value = chatroom
        with patch("chatroom_api.auth._get_rds", return_value=rds), \
             patch("chatroom_api.auth.jwt_utils") as jwt_mock:
            jwt_mock.create_token.return_value = "t"
            status, body = handle_auth_token({"chatroom_id": "scid_test-chatroom-001"})

        assert status == 200
        assert body["lobby"]["status"] == "open"
        assert body["lobby"]["target_human_count"] == 1
        assert mock_dynamo.get_conversation(body["conversation_id"]) is None
        open_lobby = mock_lobby.query_by_conversation_id(body["conversation_id"])
        assert open_lobby is not None
        assert open_lobby["status"] == "open"
        assert open_lobby["max_wait_seconds"] == 15

    def test_nicknames_differ(self):
        mock_lobby.reset()
        mock_dynamo.reset()
        rds, _db = _mocks()
        with patch("chatroom_api.auth._get_rds", return_value=rds), \
             patch("chatroom_api.auth.jwt_utils") as jwt_mock:
            jwt_mock.create_token.return_value = "t"
            _status, body = handle_auth_token({"chatroom_id": "scid_test-chatroom-001"})
        parts = mock_dynamo.get_conversation(body["conversation_id"])["participants"]
        assert parts[0]["nickname"] != parts[1]["nickname"]

    def test_assistant_room_uses_canonical_human_and_default_ai_names(self):
        status, body, conversation = self._run_assistant_auth(
            self._assistant_chatroom()
        )

        assert status == 200
        assert body["nickname"] == "PARTICIPANT"
        names_by_role = {
            participant["role"]: participant["nickname"]
            for participant in conversation["participants"]
        }
        assert names_by_role == {"human": "PARTICIPANT", "ai": "AI"}

    def test_assistant_room_uses_room_ai_nickname_when_persona_name_is_empty(self):
        _status, _body, conversation = self._run_assistant_auth(
            self._assistant_chatroom(ai_nickname="Helper")
        )

        ai = next(p for p in conversation["participants"] if p["role"] == "ai")
        assert ai["nickname"] == "Helper"

    def test_assistant_room_persona_name_has_priority(self):
        _status, _body, conversation = self._run_assistant_auth(
            self._assistant_chatroom(
                ai_nickname="Helper",
                personas=[{
                    "internal_name": "condition_1",
                    "nickname": "Alex",
                    "persona": "Be concise.",
                }],
            )
        )

        ai = next(p for p in conversation["participants"] if p["role"] == "ai")
        assert ai["nickname"] == "Alex"

    def test_assistant_room_reserved_persona_name_falls_back_safely(self):
        _status, _body, conversation = self._run_assistant_auth(
            self._assistant_chatroom(
                ai_nickname="Assistant",
                personas=[{
                    "internal_name": "condition_1",
                    "nickname": " you ",
                    "persona": "Be concise.",
                }],
            )
        )

        ai = next(p for p in conversation["participants"] if p["role"] == "ai")
        assert ai["nickname"] == "Assistant"
