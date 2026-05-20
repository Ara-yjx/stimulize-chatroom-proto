"""In-memory mock of the DynamoDB conversation store.

Provides the same interface as ``dynamo.py`` so the rest of the codebase can
swap implementations via the ``USE_MOCK_DYNAMO`` env var.

Beta delta (task 3.1):

- The conversation row carries the tick-model fields (``status``,
  ``started_at``, ``last_tick_at``, ``last_speak_at_by_session``) in addition
  to the chatroom snapshot and participants list. They are written on first
  creation only; subsequent ``append_events`` calls leave them alone — the
  tick handler uses dedicated update functions for the mutable ones.
- Every event written goes through ``_ensure_visible_at``, which back-fills
  ``visible_at = timestamp`` for older callers. This keeps the schema
  forward-compatible without requiring every call site to migrate at once.
- All mutating functions hold ``_lock`` so the property-based tests (and the
  local heartbeat thread in dev mode) see atomic check-then-update behavior
  matching the real DDB conditional updates.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_rooms: dict = {}
_lock = threading.Lock()

# Dev-mode dump directory. When set (typically by ``dev_server.py``), every
# mutation flushes the full row to ``<dir>/<conversation_id>.json`` so the
# operator can tail the file. Empty / unset disables dumping (the default,
# so tests and the real DDB backend stay clean).
_DUMP_DIR_ENV = "DEV_DUMP_CONVERSATIONS_DIR"


def _dump_filename_prefix(room: dict) -> str:
    """Compute a sortable, human-readable timestamp prefix for the dump file.

    Preference order, picking the earliest on-row timestamp so files sort
    chronologically by when the cohort started:

    1. The first ``lobby_created`` audit event's ``created_at`` (group mode).
    2. The row's ``started_at`` (one_on_one mode, or rows missing the audit
       event).
    3. The row's ``created_at`` (defensive fallback).

    Returns a string like ``2026-05-16T13-25-02Z`` — stable across mutations,
    safe across filesystems (no colons/slashes), sorts the same way ``ls``
    sorts ASCII so the latest cohort lands at the bottom of the listing.
    """
    iso = ""
    for evt in room.get("events") or []:
        if evt.get("type") == "lobby_created":
            iso = evt.get("created_at") or ""
            break
    if not iso:
        iso = room.get("started_at") or room.get("created_at") or ""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _maybe_dump(conversation_id: str, room: dict) -> None:
    """Write *room* to ``<DEV_DUMP_CONVERSATIONS_DIR>/<prefix>__<conversation_id>.json``.

    The prefix encodes the cohort start time so ``ls`` sorts chronologically;
    see ``_dump_filename_prefix``. Best-effort: any I/O error is logged and
    swallowed so the in-memory store stays consistent. Caller must hold
    ``_lock``.
    """
    dump_dir = os.environ.get(_DUMP_DIR_ENV, "")
    if not dump_dir:
        return
    try:
        os.makedirs(dump_dir, exist_ok=True)
        prefix = _dump_filename_prefix(room)
        name = f"{prefix}__{conversation_id}.json" if prefix else f"{conversation_id}.json"
        path = os.path.join(dump_dir, name)
        # Atomic-ish write: tempfile then rename. Avoids torn reads if the
        # operator tails the file with `jq -r .` while we're mid-flush.
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(room, fh, indent=2, default=str, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        logger.warning(
            "mock_dynamo dev dump failed for %s", conversation_id, exc_info=True
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_visible_at(event: dict) -> dict:
    """Return *event* with ``visible_at`` populated.

    Defaults to ``timestamp`` when the caller didn't set ``visible_at``. This
    keeps the v2 callers (which only set ``timestamp``) working unchanged
    while letting tick-model writers stack a typing delay on top.
    """
    if "visible_at" in event:
        return event
    return {**event, "visible_at": event.get("timestamp", 0)}


def reset() -> None:
    """Clear all in-memory rooms. Tests call this in ``setup_method``."""
    with _lock:
        _rooms.clear()


def get_events(conversation_id: str, after: int = 0) -> list[dict]:
    """Return events for *conversation_id* where timestamp > *after*."""
    with _lock:
        room = _rooms.get(conversation_id)
        if room is None:
            return []
        # Deep-copy to keep callers from mutating the in-memory store.
        return [copy.deepcopy(e) for e in room["events"] if e["timestamp"] > after]


def get_conversation_config(conversation_id: str) -> Optional[dict]:
    """Return the chatroom_setting stored for a conversation, or None."""
    with _lock:
        room = _rooms.get(conversation_id)
        if room is None:
            return None
        return copy.deepcopy(room.get("chatroom_setting"))


def get_participants(conversation_id: str) -> Optional[list[dict]]:
    """Return the participants list for a conversation, or None."""
    with _lock:
        room = _rooms.get(conversation_id)
        if room is None:
            return None
        return copy.deepcopy(room.get("participants"))


def get_conversation(conversation_id: str) -> Optional[dict]:
    """Return a deep copy of the full conversation row, or None.

    Tick handler (task 2.3) needs the full row to read ``status``,
    ``started_at``, ``last_tick_at``, ``last_speak_at_by_session``, plus
    the chatroom setting and participants in one shot.
    """
    with _lock:
        room = _rooms.get(conversation_id)
        if room is None:
            return None
        return copy.deepcopy(room)


def append_events(
    conversation_id: str,
    chatroom_id: str,
    events: list[dict],
    chatroom_setting: Optional[dict] = None,
    participants: Optional[list[dict]] = None,
    *,
    status: Optional[str] = None,
    started_at: Optional[str] = None,
    last_tick_at: Optional[int] = None,
    last_speak_at_by_session: Optional[dict] = None,
) -> None:
    """Append *events* to the conversation, creating the entry if needed.

    On first creation the tick-model fields are populated from the keyword
    args, defaulting to ``status="active"``, ``started_at=now_iso``,
    ``last_tick_at=0``, ``last_speak_at_by_session={}``. On subsequent calls
    those fields are *not* updated — mutations to ``last_tick_at`` and
    ``last_speak_at_by_session`` go through their dedicated helpers.
    """
    now = _now_iso()
    normalized = [_ensure_visible_at(e) for e in events]

    with _lock:
        room = _rooms.get(conversation_id)
        if room is None:
            new_row: dict = {
                "conversation_id": conversation_id,
                "chatroom_id": chatroom_id,
                "chatroom_setting": copy.deepcopy(chatroom_setting),
                "participants": copy.deepcopy(participants),
                "events": [],
                "status": status if status is not None else "active",
                "started_at": started_at if started_at is not None else now,
                "last_tick_at": int(last_tick_at) if last_tick_at is not None else 0,
                "last_speak_at_by_session": (
                    copy.deepcopy(last_speak_at_by_session)
                    if last_speak_at_by_session is not None
                    else {}
                ),
                "created_at": now,
                "updated_at": now,
            }
            _rooms[conversation_id] = new_row
            room = new_row

        room["events"].extend(copy.deepcopy(normalized))
        room["updated_at"] = now
        _maybe_dump(conversation_id, room)


def update_last_tick_at_conditional(
    conversation_id: str,
    now_ms: int,
    dedupe_window_ms: int,
) -> bool:
    """Idempotency guard for the tick handler.

    Sets ``last_tick_at = now_ms`` only when the existing value is missing or
    older than ``now_ms - dedupe_window_ms``. Mirrors the conditional
    ``UpdateItem`` in the real DDB implementation. Returns True on a winning
    update, False if another tick fired too recently.
    """
    with _lock:
        room = _rooms.get(conversation_id)
        if room is None:
            return False
        threshold = now_ms - dedupe_window_ms
        existing = room.get("last_tick_at")
        if existing is not None and int(existing) >= threshold:
            return False
        room["last_tick_at"] = int(now_ms)
        room["updated_at"] = _now_iso()
        _maybe_dump(conversation_id, room)
        return True


def update_status(conversation_id: str, new_status: str) -> bool:
    """Set ``status = new_status`` unconditionally. Returns False if the row
    is gone (mirrors a no-op ``UpdateItem`` against a missing key)."""
    with _lock:
        room = _rooms.get(conversation_id)
        if room is None:
            return False
        room["status"] = new_status
        room["updated_at"] = _now_iso()
        _maybe_dump(conversation_id, room)
        return True


def update_last_speak_at(
    conversation_id: str,
    session_id: str,
    now_ms: int,
) -> bool:
    """Set ``last_speak_at_by_session[session_id] = now_ms`` on the row.

    Returns False if the conversation row doesn't exist; True on a successful
    write. ``last_speak_at_by_session`` is initialized to ``{}`` on creation
    so the nested SET is always safe.
    """
    with _lock:
        room = _rooms.get(conversation_id)
        if room is None:
            return False
        speak_map = room.setdefault("last_speak_at_by_session", {})
        speak_map[session_id] = int(now_ms)
        room["updated_at"] = _now_iso()
        _maybe_dump(conversation_id, room)
        return True
