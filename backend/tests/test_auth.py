"""Tests for chatroom_api.auth (v2: chatroom_id only, no management API)."""

from unittest.mock import patch, MagicMock
from chatroom_api.auth import handle_auth_token

SAMPLE_CHATROOM = {
    "id": "scid_test-chatroom-001",
    "owner_id": "user_001",
    "name": "College Chat",
    "status": "active",
    "setting": {
        "mode": "one_on_one",
        "topic_instruction": "Talk about your college life.",
        "model_id": "global.anthropic.claude-sonnet-4-6",
        "simulate_pairing_seconds": 3,
        "timer_min_minutes": 5,
        "timer_max_minutes": 10,
    },
}


def _mocks():
    rds = MagicMock()
    rds.get_chatroom.return_value = SAMPLE_CHATROOM
    db = MagicMock()
    return rds, db


class TestHandleAuthTokenV2:

    def test_valid_chatroom_returns_session_info(self):
        rds, db = _mocks()
        with patch("chatroom_api.auth._get_rds", return_value=rds), \
             patch("chatroom_api.auth._get_db", return_value=db), \
             patch("chatroom_api.auth.jwt_utils") as jwt_mock:
            jwt_mock.create_token.return_value = "fake-token"
            s, b = handle_auth_token({"chatroom_id": "scid_test-chatroom-001"})
        assert s == 200
        assert b["token"] == "fake-token"
        assert b["nickname"].startswith("Participant")
        assert "emojiText" in b["avatar"]
        assert b["chatroom_setting"]["mode"] == "one_on_one"

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
        rds, db = _mocks()
        with patch("chatroom_api.auth._get_rds", return_value=rds), \
             patch("chatroom_api.auth._get_db", return_value=db), \
             patch("chatroom_api.auth.jwt_utils") as jwt_mock:
            jwt_mock.create_token.return_value = "t"
            handle_auth_token({"chatroom_id": "scid_test-chatroom-001"})
        parts = db.append_events.call_args.kwargs["participants"]
        assert len(parts) == 2
        assert {p["role"] for p in parts} == {"human", "ai"}

    def test_nicknames_differ(self):
        rds, db = _mocks()
        with patch("chatroom_api.auth._get_rds", return_value=rds), \
             patch("chatroom_api.auth._get_db", return_value=db), \
             patch("chatroom_api.auth.jwt_utils") as jwt_mock:
            jwt_mock.create_token.return_value = "t"
            handle_auth_token({"chatroom_id": "scid_test-chatroom-001"})
        parts = db.append_events.call_args.kwargs["participants"]
        assert parts[0]["nickname"] != parts[1]["nickname"]
