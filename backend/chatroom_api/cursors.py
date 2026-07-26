"""Opaque history cursor and event-key helpers."""

from __future__ import annotations

import base64
import json
import re


CURSOR_VERSION = 1
HISTORY_STREAM = "history"
_EVENT_KEY_RE = re.compile(
    r"^H#T(?P<timestamp>\d{16})#B(?P<batch_id>[A-Za-z0-9_-]+)#I(?P<index>\d{3,})$"
)


class InvalidCursorError(ValueError):
    """Raised when a public history cursor is malformed or out of scope."""


def make_event_key(timestamp: int, batch_id: str, index: int) -> str:
    if timestamp < 0:
        raise ValueError("timestamp must be non-negative")
    if not batch_id or not re.fullmatch(r"[A-Za-z0-9_-]+", batch_id):
        raise ValueError("batch_id must be URL-safe")
    if index < 0:
        raise ValueError("index must be non-negative")
    return f"H#T{int(timestamp):016d}#B{batch_id}#I{int(index):03d}"


def event_key_timestamp(event_key: str) -> int:
    match = _EVENT_KEY_RE.fullmatch(event_key or "")
    if match is None:
        raise ValueError("invalid history event key")
    return int(match.group("timestamp"))


def encode_cursor(conversation_id: str, event_key: str) -> str:
    if not conversation_id:
        raise ValueError("conversation_id is required")
    event_key_timestamp(event_key)
    payload = {
        "v": CURSOR_VERSION,
        "stream": HISTORY_STREAM,
        "conversation_id": conversation_id,
        "event_key": event_key,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, conversation_id: str) -> dict:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("invalid cursor") from exc

    if not isinstance(payload, dict):
        raise InvalidCursorError("invalid cursor")
    if payload.get("v") != CURSOR_VERSION:
        raise InvalidCursorError("unsupported cursor version")
    if payload.get("stream") != HISTORY_STREAM:
        raise InvalidCursorError("invalid cursor stream")
    if payload.get("conversation_id") != conversation_id:
        raise InvalidCursorError("cursor conversation mismatch")
    try:
        event_key_timestamp(payload.get("event_key", ""))
    except ValueError as exc:
        raise InvalidCursorError("invalid cursor event key") from exc
    return payload
