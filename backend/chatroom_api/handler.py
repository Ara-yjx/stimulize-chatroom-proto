"""Lambda entry point — route API Gateway proxy events to handlers."""

from __future__ import annotations

import json
import logging
from typing import Optional

import jwt

from chatroom_api import auth, chat, jwt_utils
from chatroom_api.errors import LobbyAbortedException

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}


def _response(status_code: int, body: dict) -> dict:
    """Build an API Gateway proxy response."""
    return {
        "statusCode": status_code,
        "headers": _CORS_HEADERS,
        "body": json.dumps(body),
    }


def _get_bearer_token(headers: dict) -> Optional[str]:
    """Extract Bearer token from Authorization header (case-insensitive)."""
    if not headers:
        return None
    # API Gateway may normalise header keys; check case-insensitively
    for key, value in headers.items():
        if key.lower() == "authorization":
            parts = value.split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1]
            return None
    return None


def lambda_handler(event: dict, context) -> dict:
    """Route an API Gateway proxy event to the appropriate handler."""
    try:
        http_method = event.get("httpMethod", "")
        path = event.get("path", "")

        # CORS preflight
        if http_method == "OPTIONS":
            return _response(200, {})

        body = json.loads(event.get("body") or "{}")
        query_params = event.get("queryStringParameters") or {}
        headers = event.get("headers") or {}

        # --- public route ---
        if http_method == "POST" and path == "/auth/token":
            status, resp = auth.handle_auth_token(body)
            return _response(status, resp)

        # --- protected /chat/* routes ---
        if path.startswith("/chat/"):
            token = _get_bearer_token(headers)
            if not token:
                return _response(401, {"error": "missing token"})

            try:
                claims = jwt_utils.verify_token(token)
            except jwt.ExpiredSignatureError:
                return _response(401, {"error": "token expired"})
            except jwt.InvalidTokenError:
                return _response(401, {"error": "invalid token"})

            if http_method == "POST" and path == "/chat/send":
                status, resp = chat.handle_chat_send(body, claims)
                return _response(status, resp)

            if http_method == "GET" and path == "/chat/messages":
                try:
                    status, resp = chat.handle_chat_messages(
                        query_params, claims, headers=headers
                    )
                except LobbyAbortedException:
                    return _response(410, {"error": "lobby aborted"})
                return _response(status, resp)

        # --- fallback ---
        return _response(404, {"error": "not found"})

    except Exception:
        logger.exception("Unhandled error in lambda_handler")
        return _response(500, {"error": "internal server error"})
