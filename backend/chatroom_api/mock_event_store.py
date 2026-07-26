"""In-memory event-store adapter used by tests and local development."""

from __future__ import annotations

import copy
import time
from datetime import datetime, timezone
from typing import Optional

from chatroom_api import mock_dynamo
from chatroom_api.cursors import decode_cursor, encode_cursor, event_key_timestamp
from chatroom_api.dynamo import TTL_SECONDS
from chatroom_api.event_store import ConditionalWriteFailed, normalize_history_events


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sorted(conversation_id: str) -> list[dict]:
    return sorted(
        mock_dynamo._history.get(conversation_id, []),
        key=lambda item: item["event_key"],
    )


def _mirror_legacy(room: dict, items: list[dict]) -> None:
    room.setdefault("events", []).extend(copy.deepcopy(items))


def create_conversation(metadata: dict, history_events: list[dict], batch_id: str) -> list[dict]:
    conversation_id = metadata["conversation_id"]
    items = normalize_history_events(conversation_id, history_events, batch_id)
    with mock_dynamo._lock:
        existing = mock_dynamo._rooms.get(conversation_id)
        if existing is not None:
            current_by_key = {item["event_key"]: item for item in _sorted(conversation_id)}
            if (
                existing.get("creation_batch_id") != batch_id
                or any(current_by_key.get(item["event_key"]) != item for item in items)
            ):
                raise ConditionalWriteFailed("conversation creation payload conflict")
            return copy.deepcopy(items)
        now = _now_iso()
        room = {
            **copy.deepcopy(metadata),
            "event_storage_version": 1,
            "creation_batch_id": batch_id,
            "ai_tick_state_by_participant_id": copy.deepcopy(
                metadata.get("ai_tick_state_by_participant_id", {})
            ),
            "next_actionable_tick_at": int(
                metadata.get("next_actionable_tick_at", 0) or 0
            ),
            "created_at": metadata.get("created_at") or now,
            "updated_at": metadata.get("updated_at") or now,
            "ttl": metadata.get("ttl") or int(time.time()) + TTL_SECONDS,
            "events": [],
        }
        mock_dynamo._rooms[conversation_id] = room
        mock_dynamo._history[conversation_id] = copy.deepcopy(items)
        _mirror_legacy(room, items)
        mock_dynamo._maybe_dump(conversation_id, room)
    return copy.deepcopy(items)


def append_history_batch(
    conversation_id: str,
    history_events: list[dict],
    batch_id: str,
    metadata_updates: Optional[dict] = None,
    expected_status: Optional[str] = None,
) -> list[dict]:
    items = normalize_history_events(conversation_id, history_events, batch_id)
    if not items:
        raise ValueError("append_history_batch requires at least one event")
    with mock_dynamo._lock:
        room = mock_dynamo._rooms.get(conversation_id)
        if room is None:
            raise ValueError("conversation not found")
        if expected_status is not None and room.get("status") != expected_status:
            raise ConditionalWriteFailed("conversation status changed")
        history = mock_dynamo._history.setdefault(conversation_id, [])
        by_key = {event["event_key"]: event for event in history}
        if all(item["event_key"] in by_key for item in items):
            if any(by_key[item["event_key"]] != item for item in items):
                raise ValueError("history batch payload conflict")
            return copy.deepcopy(items)
        if any(item["event_key"] in by_key for item in items):
            raise ValueError("partial history batch conflict")
        history.extend(copy.deepcopy(items))
        room.update(copy.deepcopy(metadata_updates or {}))
        room["updated_at"] = _now_iso()
        room["ttl"] = int(time.time()) + TTL_SECONDS
        _mirror_legacy(room, items)
        mock_dynamo._maybe_dump(conversation_id, room)
    return copy.deepcopy(items)


def update_tick_projection(
    conversation_id: str,
    ai_participant_id: str,
    tick_state: dict,
    expected_status: Optional[str] = None,
    next_actionable_tick_at: Optional[int] = None,
) -> None:
    with mock_dynamo._lock:
        room = mock_dynamo._rooms.get(conversation_id)
        if room is None:
            return
        if expected_status is not None and room.get("status") != expected_status:
            return
        room.setdefault("ai_tick_state_by_participant_id", {})[
            ai_participant_id
        ] = copy.deepcopy(tick_state)
        room["next_actionable_tick_at"] = int(
            next_actionable_tick_at
            if next_actionable_tick_at is not None
            else tick_state.get("next_actionable_at", 0) or 0
        )
        mock_dynamo._maybe_dump(conversation_id, room)


def _page(items: list[dict], limit: int) -> tuple[list[dict], bool]:
    return items[:limit], len(items) > limit


def query_live_after(
    conversation_id: str,
    after_cursor: Optional[str],
    now_ms: int,
    limit: int,
) -> dict:
    lower = decode_cursor(after_cursor, conversation_id)["event_key"] if after_cursor else None
    with mock_dynamo._lock:
        eligible = [
            copy.deepcopy(item)
            for item in _sorted(conversation_id)
            if int(item["timestamp"]) <= now_ms and (lower is None or item["event_key"] > lower)
        ]
    items, has_more = _page(eligible, limit)
    return {
        "events": items,
        "next_after": encode_cursor(conversation_id, items[-1]["event_key"]) if items else after_cursor,
        "has_more": has_more,
    }


def query_live_after_timestamp(
    conversation_id: str,
    after_timestamp: int,
    now_ms: int,
    limit: int,
) -> dict:
    with mock_dynamo._lock:
        eligible = [
            copy.deepcopy(item)
            for item in _sorted(conversation_id)
            if after_timestamp < int(item["timestamp"]) <= now_ms
        ]
    items, has_more = _page(eligible, limit)
    return {
        "events": items,
        "next_after": encode_cursor(conversation_id, items[-1]["event_key"]) if items else None,
        "has_more": has_more,
    }


def query_history_before(
    conversation_id: str,
    before_cursor: Optional[str],
    now_ms: int,
    limit: int,
) -> dict:
    upper = decode_cursor(before_cursor, conversation_id)["event_key"] if before_cursor else None
    with mock_dynamo._lock:
        eligible = [
            copy.deepcopy(item)
            for item in _sorted(conversation_id)
            if int(item["timestamp"]) <= now_ms and (upper is None or item["event_key"] < upper)
        ]
    descending = list(reversed(eligible))
    page, has_more = _page(descending, limit)
    items = list(reversed(page))
    return {
        "events": items,
        "next_before": encode_cursor(conversation_id, items[0]["event_key"]) if items else before_cursor,
        "latest_cursor": encode_cursor(conversation_id, items[-1]["event_key"]) if items else None,
        "has_more": has_more,
    }


def query_prompt_events(conversation_id: str, now_ms: int) -> list[dict]:
    with mock_dynamo._lock:
        return [
            copy.deepcopy(item)
            for item in _sorted(conversation_id)
            if int(item["timestamp"]) <= now_ms
        ]


def query_next_pending(
    conversation_id: str,
    after_cursor: Optional[str],
    now_ms: int,
) -> Optional[int]:
    lower = decode_cursor(after_cursor, conversation_id)["event_key"] if after_cursor else None
    with mock_dynamo._lock:
        for item in _sorted(conversation_id):
            if int(item["timestamp"]) > now_ms and (lower is None or item["event_key"] > lower):
                return event_key_timestamp(item["event_key"])
    return None
