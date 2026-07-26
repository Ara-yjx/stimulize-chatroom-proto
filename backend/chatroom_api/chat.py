"""POST /chat/send and GET chat history handlers.

Beta delta vs v2 (tasks 3.3, 3.4, 3.5):

- ``/chat/send`` never invokes Bedrock directly. It appends the human's
  message event and returns the same payload shape as ``/chat/messages``.
  For a single-human, single-AI, non-mimic room only, it also async-invokes
  the tick handler so the required reply does not wait for the heartbeat.
- ``/chat/messages`` reads participant-visible events from the history table.
  When the conversation row hasn't been written yet,
  the response carries a ``lobby`` block describing the open lobby;
  ``aborted`` lobbies surface as ``LobbyAbortedException`` so
  ``handler.py`` can map them to HTTP 410.
Tick diagnostics are CloudWatch-only and never returned by this module.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import boto3

from chatroom_api import close_lobby as close_lobby_mod
from chatroom_api import config
from chatroom_api._providers import get_event_store_provider
from chatroom_api.cursors import InvalidCursorError
from chatroom_api.errors import LobbyAbortedException
from chatroom_api.event_store import ConditionalWriteFailed
from chatroom_api.settings import is_single_human_single_ai_assistant_room
from chatroom_api import resumable

logger = logging.getLogger(__name__)
_lambda_client = None


# ---------------------------------------------------------------------------
# Backend selection helpers (mirrors auth.py / close_lobby.py).
# ---------------------------------------------------------------------------


def _get_db():
    """Return the appropriate DynamoDB module based on config."""
    if config.USE_MOCK_DYNAMO:
        from chatroom_api import mock_dynamo
        return mock_dynamo
    from chatroom_api import dynamo
    return dynamo


def _get_lobby():
    """Return the appropriate lobby module based on config."""
    if config.USE_MOCK_LOBBY:
        from chatroom_api import mock_lobby
        return mock_lobby
    from chatroom_api import lobby
    return lobby


def _get_event_store():
    return get_event_store_provider()


def _get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client("lambda")
    return _lambda_client


def _trigger_immediate_assistant_tick(
    conv: dict,
    conversation_id: str,
) -> bool:
    """Best-effort wake-up for the required single-assistant reply only."""
    if not is_single_human_single_ai_assistant_room(
        conv.get("chatroom_setting") or {}
    ):
        return False
    if not config.TICK_HANDLER_LAMBDA:
        return False

    try:
        _get_lambda_client().invoke(
            FunctionName=config.TICK_HANDLER_LAMBDA,
            InvocationType="Event",
            Payload=json.dumps({"conversation_id": conversation_id}).encode(),
        )
        return True
    except Exception as exc:  # noqa: BLE001 - heartbeat remains the fallback
        logger.warning(
            "failed to trigger immediate assistant tick for %s: %s",
            conversation_id,
            exc,
        )
        return False


# ---------------------------------------------------------------------------
# Pure helpers.
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _decorate_with_avatar(events: list[dict], avatar_map: dict) -> list[dict]:
    """Project events onto the wire shape, looking up avatars by sender."""
    decorated: list[dict] = []
    for e in events:
        sender = e.get("sender")
        item = {
            "event_id": e.get("event_id"),
            "type": e.get("type", "message"),
            "subtype": e.get("subtype"),
            "sender": sender,
            "role": e.get("role"),
            "content": e.get("content", ""),
            "timestamp": e.get("timestamp", 0),
            "session_id": e.get("session_id"),
            "participant_id": e.get("participant_id"),
            "ai_participant_id": e.get("ai_participant_id"),
            "avatar": e.get("avatar") or (avatar_map.get(sender) if sender else None),
            "internal_name": e.get("internal_name"),
            "episode_number": e.get("episode_number"),
        }
        decorated.append({key: value for key, value in item.items() if value is not None})
    return decorated


def _avatar_map_for(conv: Optional[dict]) -> dict:
    """Build a nickname → avatar map from a conversation row."""
    if conv is None:
        return {}
    return {
        p["nickname"]: p.get("avatar")
        for p in (conv.get("participants") or [])
        if p.get("nickname")
    }


# ---------------------------------------------------------------------------
# /chat/send — append human message; no direct Bedrock call.
# ---------------------------------------------------------------------------


def handle_chat_send(body: dict, claims: dict) -> tuple[int, dict]:
    """Append the human's message event and return the visible event slice.

    Per requirements 5.1 / 5.2 / 5.3:

    - No direct Bedrock call. The tick handler is the only Bedrock caller.
    - Only a 1-human + 1-AI + non-mimic room async-invokes the tick handler
      immediately after the human message is persisted. Other rooms continue
      to rely exclusively on the heartbeat.
    - Response keeps the ``events`` envelope and adds cursor pagination.
    - 409 when the conversation is in the lobby phase (no row yet) or has
      ended.
    """
    message = body.get("message", "")
    after = int(body.get("after", 0) or 0)
    session_id = claims["session_id"]
    conversation_id = claims["conversation_id"]

    if not isinstance(message, str) or not message.strip():
        return (400, {"error": "message is required"})

    db = _get_db()
    history_store = _get_event_store()

    conv = db.get_conversation(conversation_id)
    if conv is None:
        # Lobby phase (or stale JWT pointing nowhere). Either way no message
        # is accepted — the front-end is supposed to keep showing the lobby UI.
        return (409, {"error": "conversation not started yet"})

    connection_error = resumable.validate_connection(conv, claims)
    if connection_error:
        return (409, {"error": connection_error, "code": "connection_superseded"})
    if conv.get("status") == "ended":
        return (409, {"error": "conversation has ended"})
    if conv.get("status") == "inactive":
        return (409, {"error": "conversation episode is inactive"})

    # Resolve the human's nickname from the conversation participants.
    participants = conv.get("participants") or []
    participant_id = claims.get("participant_id")
    me = next((
        p for p in participants
        if (
            p.get("participant_id") == participant_id
            if participant_id is not None
            else p.get("session_id") == session_id
        )
    ), None)
    if me is None:
        return (403, {"error": "session not in conversation"})
    nickname = me.get("nickname", "Participant")

    now_ms = _now_ms()
    now_iso = datetime.now(timezone.utc).isoformat()
    user_event = {
        "type": "message",
        "session_id": session_id,
        **({"participant_id": participant_id} if participant_id is not None else {}),
        "sender": nickname,
        "role": "human",
        "content": message,
        "timestamp": now_ms,
        "created_at": now_iso,
        **({
            "episode_number": int(claims["episode_number"]),
        } if claims.get("episode_number") is not None else {}),
    }
    try:
        history_store.append_history_batch(
            conversation_id,
            [user_event],
            uuid4().hex,
            expected_status="active",
            expected_metadata=resumable.episode_fence(conv),
        )
    except ConditionalWriteFailed:
        return (409, {"error": "conversation has ended"})
    _trigger_immediate_assistant_tick(conv, conversation_id)

    page = history_store.query_live_after_timestamp(
        conversation_id, after, now_ms, 100
    )
    return (200, {
        "events": _decorate_with_avatar(page["events"], _avatar_map_for(conv)),
        "next_after": page["next_after"],
        "has_more": page["has_more"],
    })


# ---------------------------------------------------------------------------
# /chat/messages — visible-event poll plus lobby block.
# ---------------------------------------------------------------------------


def handle_chat_messages(
    query_params: Optional[dict],
    claims: dict,
    headers: Optional[dict] = None,
) -> tuple[int, dict]:
    """Return events visible to this caller right now.

    Behavior summary:

    - Conversation row exists: query history through the current server time;
      include ``conversation_status`` and a null ``lobby``.
    - Conversation row missing: locate the pre-allocated lobby via
      ``conversation_id-index``.
        - ``open`` and past ``deadline_at``: run the freshness ``close_lobby``
          and re-read.
        - ``open`` and live: best-effort ``update_last_seen_at`` and return
          an empty events list with the lobby block populated.
        - ``closing`` / ``closed``: empty events; the next poll will see the
          conversation row.
        - ``aborted``: raise :class:`LobbyAbortedException` so the handler
          maps it to HTTP 410.
    """
    qp = query_params or {}
    after_raw = str(qp.get("after", "0") or "0")
    try:
        limit = min(max(int(qp.get("limit", 100) or 100), 1), 100)
    except (TypeError, ValueError):
        return (400, {"error": "invalid limit"})
    conversation_id = claims["conversation_id"]
    session_id = claims["session_id"]

    db = _get_db()
    history_store = _get_event_store()
    lobby_mod = _get_lobby()

    now_ms = _now_ms()

    conv = db.get_conversation(conversation_id)
    if conv is not None:
        connection_error = resumable.validate_connection(conv, claims)
        if connection_error:
            return (409, {"error": connection_error, "code": "connection_superseded"})
        try:
            if after_raw.isdigit():
                page = history_store.query_live_after_timestamp(
                    conversation_id, int(after_raw), now_ms, limit
                )
                cursor_for_pending = page.get("next_after")
            else:
                page = history_store.query_live_after(
                    conversation_id, after_raw, now_ms, limit
                )
                cursor_for_pending = page.get("next_after") or after_raw
        except InvalidCursorError:
            return (400, {"error": "invalid_cursor"})
        next_pending_at = None
        if conv.get("status") in {"ended", "inactive"} and not page["has_more"]:
            try:
                next_pending_at = history_store.query_next_pending(
                    conversation_id, cursor_for_pending, now_ms
                )
            except InvalidCursorError:
                return (400, {"error": "invalid_cursor"})
        return (200, {
            "events": _decorate_with_avatar(page["events"], _avatar_map_for(conv)),
            "next_after": page.get("next_after"),
            "has_more": page["has_more"],
            "next_pending_at": next_pending_at,
            "conversation_status": conv.get("status", "active"),
            "lobby": None,
        })

    # No conversation row yet — look up the lobby by pre-allocated id.
    lobby = lobby_mod.query_by_conversation_id(conversation_id)
    if lobby is None:
        return (404, {"error": "conversation not found"})

    status = lobby.get("status")
    deadline_at = int(lobby.get("deadline_at", 0) or 0)

    if status == "open":
        if now_ms >= deadline_at:
            # Freshness path: close, then re-read. ``close_lobby`` either
            # writes the conversation row (so the recursive call sees it)
            # or marks the lobby aborted (so the recursive call raises).
            close_lobby_mod.close_lobby(lobby["lobby_id"], now_ms)
            return handle_chat_messages(query_params, claims, headers)

        # Best-effort heartbeat. Failures are logged but not surfaced —
        # losing a single update doesn't change correctness, and the next
        # poll will retry.
        try:
            lobby_mod.update_last_seen_at(lobby["lobby_id"], session_id, now_ms)
        except Exception:  # noqa: BLE001 — wide net for the heartbeat path
            logger.warning("update_last_seen_at failed", exc_info=True)

        return (200, {
            "events": [],
            "conversation_status": "active",
            "lobby": {
                "status": "open",
                "actual_human_count": int(lobby.get("actual_human_count", 0) or 0),
                "target_human_count": int(lobby.get("target_human_count", 0) or 0),
                "deadline_at": deadline_at,
            },
        })

    if status in ("closing", "closed"):
        # The closer is mid-flight. The conversation row will appear on the
        # next poll; return an empty slice so the widget stays in lobby UI.
        return (200, {
            "events": [],
            "conversation_status": "active",
            "lobby": None,
        })

    if status == "aborted":
        raise LobbyAbortedException(conversation_id)

    # Unknown status — be conservative and treat as not found.
    return (404, {"error": "conversation not found"})


def handle_chat_history(
    query_params: Optional[dict],
    claims: dict,
) -> tuple[int, dict]:
    """Return one backward page of participant-visible history."""
    qp = query_params or {}
    before = str(qp.get("before") or "") or None
    try:
        limit = min(max(int(qp.get("limit", 50) or 50), 1), 100)
    except (TypeError, ValueError):
        return (400, {"error": "invalid limit"})
    conversation_id = claims["conversation_id"]
    conv = _get_db().get_conversation(conversation_id)
    if conv is None:
        return (404, {"error": "conversation not found"})
    connection_error = resumable.validate_connection(conv, claims)
    if connection_error:
        return (409, {"error": connection_error, "code": "connection_superseded"})
    try:
        page = _get_event_store().query_history_before(
            conversation_id, before, _now_ms(), limit
        )
    except InvalidCursorError:
        return (400, {"error": "invalid_cursor"})
    return (200, {
        **page,
        "events": _decorate_with_avatar(page["events"], _avatar_map_for(conv)),
    })
