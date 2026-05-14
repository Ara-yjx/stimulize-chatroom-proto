"""DynamoDB conversation store.

Provides the same interface as mock_dynamo.py so the rest of the codebase can
swap implementations via the USE_MOCK_DYNAMO env var.
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


def append_events(
    conversation_id: str,
    chatroom_id: str,
    events: list[dict],
    chatroom_setting: Optional[dict] = None,
    participants: Optional[list[dict]] = None,
) -> None:
    """Append *events* to the conversation, creating the entry if needed.

    On first creation, *chatroom_setting* and *participants* are stored.
    TTL = created_at + 2.5 years.
    """
    table = _get_table()
    now = datetime.now(timezone.utc).isoformat()
    ttl = int(time.time()) + TTL_SECONDS

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
                ":evts": events,
                ":now": now,
                ":ttl": ttl,
            },
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        item = {
            "conversation_id": conversation_id,
            "chatroom_id": chatroom_id,
            "events": events,
            "created_at": now,
            "updated_at": now,
            "ttl": ttl,
        }
        if chatroom_setting is not None:
            item["chatroom_setting"] = chatroom_setting
        if participants is not None:
            item["participants"] = participants
        table.put_item(Item=item)
