"""In-memory mock of the DynamoDB conversation store.

Provides the same interface as dynamo.py so the rest of the codebase can
swap implementations via the USE_MOCK_DYNAMO env var.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

_rooms: dict = {}


def get_events(conversation_id: str, after: int = 0) -> list[dict]:
    """Return events for *conversation_id* where timestamp > *after*."""
    room = _rooms.get(conversation_id)
    if room is None:
        return []
    return [e for e in room["events"] if e["timestamp"] > after]


def get_conversation_config(conversation_id: str) -> Optional[dict]:
    """Return the chatroom_setting stored for a conversation, or None."""
    room = _rooms.get(conversation_id)
    if room is None:
        return None
    return room.get("chatroom_setting")


def get_participants(conversation_id: str) -> Optional[list[dict]]:
    """Return the participants list for a conversation, or None."""
    room = _rooms.get(conversation_id)
    if room is None:
        return None
    return room.get("participants")


def append_events(
    conversation_id: str,
    chatroom_id: str,
    events: list[dict],
    chatroom_setting: Optional[dict] = None,
    participants: Optional[list[dict]] = None,
) -> None:
    """Append *events* to the conversation, creating the entry if needed.

    On first creation, *chatroom_setting* and *participants* are stored.
    """
    now = datetime.now(timezone.utc).isoformat()

    if conversation_id not in _rooms:
        _rooms[conversation_id] = {
            "conversation_id": conversation_id,
            "chatroom_id": chatroom_id,
            "chatroom_setting": chatroom_setting,
            "participants": participants,
            "events": [],
            "created_at": now,
            "updated_at": now,
        }

    room = _rooms[conversation_id]
    room["events"].extend(events)
    room["updated_at"] = now
