"""Tests for chatroom_api.handler — routing, auth gating, error handling."""

import json
from unittest.mock import patch

import jwt as pyjwt
import pytest

from chatroom_api import config
from chatroom_api.handler import lambda_handler


def _event(method="GET", path="/", body=None, headers=None, qs=None):
    return {
        "httpMethod": method,
        "path": path,
        "body": json.dumps(body) if body else None,
        "headers": headers or {},
        "queryStringParameters": qs,
    }


def _make_token(expired=False, invalid=False):
    if invalid:
        return "not.a.valid.jwt"
    import time
    payload = {
        "session_id": "s1",
        "conversation_id": "r1",
        "chatroom_id": "cr1",
        "iat": int(time.time()),
        "exp": int(time.time()) + (-10 if expired else 3600),
    }
    return pyjwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


class TestOptionsPreflight:
    def test_options_returns_200(self):
        resp = lambda_handler(_event("OPTIONS", "/chat/send"), None)
        assert resp["statusCode"] == 200
        assert "Access-Control-Allow-Origin" in resp["headers"]


class TestAuthTokenRoute:
    @patch("chatroom_api.handler.auth.handle_auth_token", return_value=(200, {"token": "t"}))
    def test_routes_to_auth(self, mock_auth):
        resp = lambda_handler(
            _event("POST", "/auth/token", body={"chatroom_id": "scid_abc"}),
            None,
        )
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"]) == {"token": "t"}
        mock_auth.assert_called_once()


class TestChatJwtGating:
    def test_missing_token_returns_401(self):
        resp = lambda_handler(_event("POST", "/chat/send", body={"message": "hi"}), None)
        assert resp["statusCode"] == 401

    def test_expired_token_returns_401(self):
        token = _make_token(expired=True)
        resp = lambda_handler(
            _event("POST", "/chat/send", body={"message": "hi"},
                   headers={"Authorization": f"Bearer {token}"}),
            None,
        )
        assert resp["statusCode"] == 401

    def test_invalid_token_returns_401(self):
        resp = lambda_handler(
            _event("POST", "/chat/send", body={"message": "hi"},
                   headers={"Authorization": "Bearer bad.token.here"}),
            None,
        )
        assert resp["statusCode"] == 401


class TestChatSendRoute:
    @patch("chatroom_api.handler.chat.handle_chat_send", return_value=(200, {"replies": []}))
    def test_routes_to_chat_send(self, mock_send):
        token = _make_token()
        resp = lambda_handler(
            _event("POST", "/chat/send", body={"message": "hi"},
                   headers={"Authorization": f"Bearer {token}"}),
            None,
        )
        assert resp["statusCode"] == 200
        mock_send.assert_called_once()


class TestChatMessagesRoute:
    @patch("chatroom_api.handler.chat.handle_chat_messages", return_value=(200, {"events": []}))
    def test_routes_to_chat_messages(self, mock_msgs):
        token = _make_token()
        resp = lambda_handler(
            _event("GET", "/chat/messages",
                   headers={"Authorization": f"Bearer {token}"},
                   qs={"after": "0"}),
            None,
        )
        assert resp["statusCode"] == 200
        mock_msgs.assert_called_once()

    @patch(
        "chatroom_api.handler.chat.handle_chat_messages",
        side_effect=__import__("chatroom_api.errors", fromlist=["LobbyAbortedException"]).LobbyAbortedException("conv-1"),
    )
    def test_aborted_lobby_returns_410(self, _mock_msgs):
        token = _make_token()
        resp = lambda_handler(
            _event("GET", "/chat/messages",
                   headers={"Authorization": f"Bearer {token}"}),
            None,
        )
        assert resp["statusCode"] == 410
        assert json.loads(resp["body"])["error"] == "lobby aborted"


class TestNotFound:
    def test_unknown_path_returns_404(self):
        resp = lambda_handler(_event("GET", "/nope"), None)
        assert resp["statusCode"] == 404


class TestErrorWrapper:
    @patch("chatroom_api.handler.auth.handle_auth_token", side_effect=RuntimeError("boom"))
    def test_unhandled_exception_returns_500(self, _):
        resp = lambda_handler(_event("POST", "/auth/token", body={}), None)
        assert resp["statusCode"] == 500


class TestCaseInsensitiveHeader:
    @patch("chatroom_api.handler.chat.handle_chat_send", return_value=(200, {"replies": []}))
    def test_lowercase_authorization_header(self, mock_send):
        token = _make_token()
        resp = lambda_handler(
            _event("POST", "/chat/send", body={"message": "hi"},
                   headers={"authorization": f"Bearer {token}"}),
            None,
        )
        assert resp["statusCode"] == 200
        mock_send.assert_called_once()
