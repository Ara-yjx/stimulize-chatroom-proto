"""End-to-end smoke for the admin-tick gate via the Lambda handler.

Mirrors a real Qualtrics widget call:
  GET /chat/messages?include_ticks=true
  Authorization: Bearer <session_jwt>
  X-Admin-Token: <admin_token>

Verifies the routing in handler.py + chat.py + the gate in chat.py work together.
"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import jwt as pyjwt

from chatroom_api import chat as chat_mod
from chatroom_api import config, mock_dynamo, mock_lobby
from chatroom_api.handler import lambda_handler


CHATROOM_ID = "scid_smoke_admin"
CONVERSATION_ID = "conv_smoke_admin"
HUMAN_SESSION = "sess_smoke_admin"


def _make_jwt(session_id: str, conversation_id: str, chatroom_id: str) -> str:
    payload = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "chatroom_id": chatroom_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return pyjwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def _seed() -> None:
    config.USE_MOCK_DYNAMO = True
    config.USE_MOCK_LOBBY = True
    config.USE_MOCK_RDS = True
    mock_dynamo.reset()
    mock_lobby.reset()
    mock_dynamo.append_events(
        CONVERSATION_ID,
        CHATROOM_ID,
        [
            {
                "type": "tick",
                "session_id": "ai_001",
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
        ],
        chatroom_setting={"mode": "group"},
        participants=[
            {"session_id": HUMAN_SESSION, "nickname": "Alice", "role": "human"},
            {"session_id": "ai_001", "nickname": "Mars", "role": "ai"},
        ],
        status="active",
        started_at="2025-01-01T00:00:00+00:00",
        last_tick_at=0,
    )


def test_include_ticks_returns_ticks_with_admin_token() -> None:
    _seed()
    original = config.ADMIN_TOKEN
    config.ADMIN_TOKEN = "smoke-admin-token"
    try:
        token = _make_jwt(HUMAN_SESSION, CONVERSATION_ID, CHATROOM_ID)
        event = {
            "httpMethod": "GET",
            "path": "/chat/messages",
            "headers": {
                "Authorization": f"Bearer {token}",
                "X-Admin-Token": "smoke-admin-token",
            },
            "queryStringParameters": {"include_ticks": "true"},
            "body": None,
        }
        with patch.object(chat_mod, "_now_ms", return_value=1_000):
            resp = lambda_handler(event, None)
    finally:
        config.ADMIN_TOKEN = original

    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    assert any(e["type"] == "tick" for e in body["events"])


def test_include_ticks_ignored_without_admin_token() -> None:
    _seed()
    original = config.ADMIN_TOKEN
    config.ADMIN_TOKEN = "smoke-admin-token"
    try:
        token = _make_jwt(HUMAN_SESSION, CONVERSATION_ID, CHATROOM_ID)
        event = {
            "httpMethod": "GET",
            "path": "/chat/messages",
            "headers": {
                "Authorization": f"Bearer {token}",
                # No X-Admin-Token.
            },
            "queryStringParameters": {"include_ticks": "true"},
            "body": None,
        }
        with patch.object(chat_mod, "_now_ms", return_value=1_000):
            resp = lambda_handler(event, None)
    finally:
        config.ADMIN_TOKEN = original

    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    assert all(e["type"] != "tick" for e in body["events"])
