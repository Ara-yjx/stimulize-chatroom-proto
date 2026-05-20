"""POST /chat/send and GET /chat/messages handlers (beta).

Beta delta vs v2 (tasks 3.3, 3.4, 3.5):

- ``/chat/send`` no longer invokes Bedrock. It just appends the human's
  message event and returns the same payload shape as ``/chat/messages``
  (events visible to this caller right now). AI replies arrive on a later
  ``/chat/messages`` poll, driven by the tick handler.
- ``/chat/messages`` filters events by ``visible_at <= now`` AND
  ``type != tick``. When the conversation row hasn't been written yet,
  the response carries a ``lobby`` block describing the open lobby;
  ``aborted`` lobbies surface as ``LobbyAbortedException`` so
  ``handler.py`` can map them to HTTP 410.
- ``?include_ticks=true`` is honored only when the caller carries the
  admin bearer (``X-Admin-Token`` header matching ``config.ADMIN_TOKEN``).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from chatroom_api import close_lobby as close_lobby_mod
from chatroom_api import config
from chatroom_api.errors import LobbyAbortedException

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Pure helpers.
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _filter_visible_events_for_client(
    events: list[dict],
    now_ms: int,
    after: int,
    include_ticks: bool,
) -> list[dict]:
    """Return events visible to a client right now.

    Filters: ``after < visible_at <= now_ms``. When *include_ticks* is False
    (the default for non-admin callers), ``type`` values that are
    server-internal audit (``tick``, ``lobby_created``) are also dropped —
    the widget never renders these.
    """
    audit_types = {"tick", "lobby_created"}
    out: list[dict] = []
    for e in events:
        visible_at = int(e.get("visible_at", e.get("timestamp", 0)) or 0)
        if visible_at > now_ms:
            continue
        if visible_at <= after:
            continue
        if not include_ticks and e.get("type") in audit_types:
            continue
        out.append(e)
    return out


def _decorate_with_avatar(events: list[dict], avatar_map: dict) -> list[dict]:
    """Project events onto the wire shape, looking up avatars by sender."""
    decorated: list[dict] = []
    for e in events:
        sender = e.get("sender")
        item = {
            "type": e.get("type", "message"),
            "sender": sender,
            "role": e.get("role"),
            "content": e.get("content", ""),
            "timestamp": e.get("timestamp", 0),
            "visible_at": e.get("visible_at", e.get("timestamp", 0)),
            "session_id": e.get("session_id"),
            "avatar": avatar_map.get(sender) if sender else None,
        }
        # Pass tick-specific fields through untouched when present (admin path).
        for key in (
            "chosen_session_id",
            "gate_decision",
            "skip_reason",
            "ai_decision",
            "bedrock_invoked",
            "input_tokens",
            "output_tokens",
            "error",
        ):
            if key in e:
                item[key] = e[key]
        decorated.append(item)
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


def _admin_token_from_headers(headers: Optional[dict]) -> str:
    """Return the value of the ``X-Admin-Token`` header (case-insensitive), or empty."""
    if not headers:
        return ""
    for k, v in headers.items():
        if k.lower() == "x-admin-token":
            return v or ""
    return ""


# ---------------------------------------------------------------------------
# /chat/send — append human message; no Bedrock.
# ---------------------------------------------------------------------------


def handle_chat_send(body: dict, claims: dict) -> tuple[int, dict]:
    """Append the human's message event and return the visible event slice.

    Per requirements 5.1 / 5.2 / 5.3:

    - No Bedrock call. The tick handler is the only Bedrock caller.
    - Response shape mirrors ``/chat/messages``: ``{events: [...]}`` with
      events visible to this caller right now (``visible_at <= now``,
      ``type != tick``, ``visible_at > after``).
    - 409 when the conversation is in the lobby phase (no row yet) or has
      ended.
    """
    message = body.get("message", "")
    after = int(body.get("after", 0) or 0)
    session_id = claims["session_id"]
    conversation_id = claims["conversation_id"]
    chatroom_id = claims["chatroom_id"]

    if not isinstance(message, str) or not message.strip():
        return (400, {"error": "message is required"})

    db = _get_db()

    conv = db.get_conversation(conversation_id)
    if conv is None:
        # Lobby phase (or stale JWT pointing nowhere). Either way no message
        # is accepted — the front-end is supposed to keep showing the lobby UI.
        return (409, {"error": "conversation not started yet"})

    if conv.get("status") == "ended":
        return (409, {"error": "conversation has ended"})

    # Resolve the human's nickname from the conversation participants.
    participants = conv.get("participants") or []
    me = next(
        (p for p in participants if p.get("session_id") == session_id),
        None,
    )
    if me is None:
        return (403, {"error": "session not in conversation"})
    nickname = me.get("nickname", "Participant")

    now_ms = _now_ms()
    now_iso = datetime.now(timezone.utc).isoformat()
    user_event = {
        "type": "message",
        "session_id": session_id,
        "sender": nickname,
        "role": "human",
        "ai_participant_id": None,
        "content": message,
        "timestamp": now_ms,
        "visible_at": now_ms,
        "created_at": now_iso,
    }
    db.append_events(conversation_id, chatroom_id, [user_event])

    # Re-read so the response reflects the just-appended event without race
    # against another writer (e.g. a tick handler).
    refreshed = db.get_conversation(conversation_id)
    all_events = (refreshed or {}).get("events", []) or []
    visible = _filter_visible_events_for_client(
        all_events, now_ms=now_ms, after=after, include_ticks=False
    )
    return (200, {"events": _decorate_with_avatar(visible, _avatar_map_for(refreshed))})


# ---------------------------------------------------------------------------
# /chat/messages — visible-event poll + lobby block + admin gate.
# ---------------------------------------------------------------------------


def handle_chat_messages(
    query_params: Optional[dict],
    claims: dict,
    headers: Optional[dict] = None,
) -> tuple[int, dict]:
    """Return events visible to this caller right now.

    Behavior summary (requirements 4.x / 1.6):

    - Conversation row exists: filter events to ``visible_at <= now`` and
      ``type != tick``; include a ``conversation_status`` field; ``lobby`` is
      ``None``.
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
    after = int(qp.get("after", 0) or 0)
    include_ticks_flag = str(qp.get("include_ticks", "")).lower() == "true"
    conversation_id = claims["conversation_id"]
    session_id = claims["session_id"]

    db = _get_db()
    lobby_mod = _get_lobby()

    now_ms = _now_ms()

    # Admin gate — ``include_ticks=true`` is only honored when (a) the env
    # has an admin token configured AND (b) the caller's ``X-Admin-Token``
    # header matches it. Without both, the parameter is silently ignored.
    admin_match = False
    if include_ticks_flag and config.ADMIN_TOKEN:
        admin_match = _admin_token_from_headers(headers) == config.ADMIN_TOKEN
    include_ticks = include_ticks_flag and admin_match

    conv = db.get_conversation(conversation_id)
    if conv is not None:
        all_events = conv.get("events", []) or []
        visible = _filter_visible_events_for_client(
            all_events,
            now_ms=now_ms,
            after=after,
            include_ticks=include_ticks,
        )
        return (200, {
            "events": _decorate_with_avatar(visible, _avatar_map_for(conv)),
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
