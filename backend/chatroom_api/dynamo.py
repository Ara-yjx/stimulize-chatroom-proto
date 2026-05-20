"""DynamoDB conversation store.

Provides the same interface as ``mock_dynamo.py`` so the rest of the codebase
can swap implementations via the ``USE_MOCK_DYNAMO`` env var.

Beta delta (task 3.1):

- The conversation row carries the tick-model fields (``status``,
  ``started_at``, ``last_tick_at``, ``last_speak_at_by_session``) in addition
  to the chatroom snapshot and participants list. They are written on the
  first ``put_item`` only; ``append_events`` never re-writes them on a
  subsequent call. The tick handler mutates ``last_tick_at`` and
  ``last_speak_at_by_session`` through dedicated update functions below.
- Every event passed to ``append_events`` goes through ``_ensure_visible_at``,
  which back-fills ``visible_at = timestamp`` for older callers. This keeps
  the schema forward-compatible without requiring every call site to migrate.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Attr

from chatroom_api import config

_table = None

TTL_SECONDS = 78_840_000  # 2.5 years


def _get_table():
    """Lazy-init the DynamoDB Table resource."""
    global _table
    if _table is None:
        dynamodb = boto3.resource("dynamodb")
        _table = dynamodb.Table(config.DYNAMODB_TABLE)
    return _table


def _ensure_visible_at(event: dict) -> dict:
    """Return *event* with ``visible_at`` populated.

    Defaults to ``timestamp`` when the caller didn't set ``visible_at``. This
    keeps the v2 callers (which only set ``timestamp``) working unchanged
    while letting tick-model writers stack a typing delay on top.
    """
    if "visible_at" in event:
        return event
    return {**event, "visible_at": event.get("timestamp", 0)}


def get_events(conversation_id: str, after: int = 0) -> list[dict]:
    """Return events for *conversation_id* where timestamp > *after*."""
    table = _get_table()
    resp = table.get_item(Key={"conversation_id": conversation_id})
    item = resp.get("Item")
    if item is None:
        return []
    events = item.get("events", [])
    return [e for e in events if e["timestamp"] > after]


def get_conversation_config(conversation_id: str) -> Optional[dict]:
    """Return the chatroom_setting stored for a conversation, or None."""
    table = _get_table()
    resp = table.get_item(
        Key={"conversation_id": conversation_id},
        ProjectionExpression="chatroom_setting",
    )
    item = resp.get("Item")
    if item is None:
        return None
    return item.get("chatroom_setting")


def get_participants(conversation_id: str) -> Optional[list[dict]]:
    """Return the participants list for a conversation, or None."""
    table = _get_table()
    resp = table.get_item(
        Key={"conversation_id": conversation_id},
        ProjectionExpression="participants",
    )
    item = resp.get("Item")
    if item is None:
        return None
    return item.get("participants")


def get_conversation(conversation_id: str) -> Optional[dict]:
    """Return the full conversation row, or None.

    Tick handler (task 2.3) needs the full row to read ``status``,
    ``started_at``, ``last_tick_at``, ``last_speak_at_by_session``, plus
    the chatroom setting and participants in one shot.
    """
    table = _get_table()
    resp = table.get_item(Key={"conversation_id": conversation_id})
    return resp.get("Item")


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

    On first creation, *chatroom_setting*, *participants*, and the tick-model
    fields (``status``, ``started_at``, ``last_tick_at``,
    ``last_speak_at_by_session``) are stored. The tick-model fields default to
    ``status="active"``, ``started_at=now_iso``, ``last_tick_at=0``,
    ``last_speak_at_by_session={}`` when the caller doesn't pass them. On
    subsequent calls, those fields are *not* updated — mutations go through
    the dedicated update helpers below.
    """
    table = _get_table()
    now = datetime.now(timezone.utc).isoformat()
    ttl = int(time.time()) + TTL_SECONDS

    normalized = [_ensure_visible_at(e) for e in events]

    try:
        table.update_item(
            Key={"conversation_id": conversation_id},
            UpdateExpression=(
                "SET events = list_append(events, :evts), "
                "updated_at = :now, #ttl = :ttl"
            ),
            ConditionExpression=Attr("conversation_id").exists(),
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={
                ":evts": normalized,
                ":now": now,
                ":ttl": ttl,
            },
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        item = {
            "conversation_id": conversation_id,
            "chatroom_id": chatroom_id,
            "events": normalized,
            "status": status if status is not None else "active",
            "started_at": started_at if started_at is not None else now,
            "last_tick_at": int(last_tick_at) if last_tick_at is not None else 0,
            "last_speak_at_by_session": (
                last_speak_at_by_session
                if last_speak_at_by_session is not None
                else {}
            ),
            "created_at": now,
            "updated_at": now,
            "ttl": ttl,
        }
        if chatroom_setting is not None:
            item["chatroom_setting"] = chatroom_setting
        if participants is not None:
            item["participants"] = participants
        table.put_item(Item=item)


def update_last_tick_at_conditional(
    conversation_id: str,
    now_ms: int,
    dedupe_window_ms: int,
) -> bool:
    """Idempotency guard for the tick handler.

    Updates ``last_tick_at = now_ms`` only when ``last_tick_at`` is missing
    or older than ``now_ms - dedupe_window_ms``. Returns True on a winning
    update, False if another tick fired too recently.
    """
    table = _get_table()
    threshold = now_ms - dedupe_window_ms
    try:
        table.update_item(
            Key={"conversation_id": conversation_id},
            UpdateExpression="SET last_tick_at = :now",
            ConditionExpression=(
                "attribute_not_exists(last_tick_at) OR last_tick_at < :stale"
            ),
            ExpressionAttributeValues={
                ":now": int(now_ms),
                ":stale": int(threshold),
            },
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True


def update_status(conversation_id: str, new_status: str) -> bool:
    """Set ``status = new_status`` unconditionally. Returns True (always
    treats the operation as effective; the underlying ``UpdateItem`` is a
    pure SET with no condition)."""
    table = _get_table()
    table.update_item(
        Key={"conversation_id": conversation_id},
        UpdateExpression="SET #status = :s, updated_at = :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":s": new_status,
            ":now": datetime.now(timezone.utc).isoformat(),
        },
    )
    return True


def update_last_speak_at(
    conversation_id: str,
    session_id: str,
    now_ms: int,
) -> bool:
    """Set ``last_speak_at_by_session[session_id] = now_ms`` on the row.

    ``session_id`` is supplied via ``ExpressionAttributeNames`` because it
    can carry characters that conflict with DynamoDB reserved words or path
    syntax. Returns True on success.
    """
    table = _get_table()
    table.update_item(
        Key={"conversation_id": conversation_id},
        UpdateExpression=(
            "SET last_speak_at_by_session.#sid = :now, updated_at = :u"
        ),
        ExpressionAttributeNames={"#sid": session_id},
        ExpressionAttributeValues={
            ":now": int(now_ms),
            ":u": datetime.now(timezone.utc).isoformat(),
        },
    )
    return True
