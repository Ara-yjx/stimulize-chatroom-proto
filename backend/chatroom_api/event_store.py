"""DynamoDB-backed participant-visible conversation history."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from chatroom_api import config
from chatroom_api.cursors import (
    decode_cursor,
    encode_cursor,
    event_key_timestamp,
    make_event_key,
)
from chatroom_api.dynamo import TTL_SECONDS, _to_dynamodb_safe


MAX_BATCH_EVENTS = 25
_event_table = None
_metadata_table = None
_client = None
_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


class ConditionalWriteFailed(RuntimeError):
    """A storage precondition failed and no history was committed."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_from_timestamp_ms(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).isoformat()


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("dynamodb")
    return _client


def _get_event_table():
    global _event_table
    if _event_table is None:
        _event_table = boto3.resource("dynamodb").Table(config.DYNAMODB_EVENT_TABLE)
    return _event_table


def _get_metadata_table():
    global _metadata_table
    if _metadata_table is None:
        _metadata_table = boto3.resource("dynamodb").Table(config.DYNAMODB_TABLE)
    return _metadata_table


def _serialize_item(item: dict) -> dict:
    return {key: _serializer.serialize(_to_dynamodb_safe(value)) for key, value in item.items()}


def _deserialize_item(item: dict) -> dict:
    return {key: _deserializer.deserialize(value) for key, value in item.items()}


def _without_none(value: dict) -> dict:
    return {key: item for key, item in value.items() if item is not None}


def normalize_history_events(
    conversation_id: str,
    history_events: list[dict],
    batch_id: str,
) -> list[dict]:
    """Return canonical event-table items for one logical write."""
    if not history_events:
        return []
    if len(batch_id) > 36:
        raise ValueError("batch_id exceeds DynamoDB idempotency-token limit")
    if len(history_events) > MAX_BATCH_EVENTS:
        raise ValueError(f"history batch exceeds {MAX_BATCH_EVENTS} events")

    normalized: list[dict] = []
    for index, event in enumerate(history_events):
        timestamp = int(event.get("timestamp", 0) or 0)
        if timestamp < 0:
            raise ValueError("history event timestamp must be non-negative")
        audience = event.get("audience", "conversation")
        if audience != "conversation":
            raise ValueError("only conversation audience is supported")
        event_key = make_event_key(timestamp, batch_id, index)
        item = {
            **event,
            "conversation_id": conversation_id,
            "event_key": event_key,
            "event_id": f"{batch_id}#{index}",
            "event_stream": "history",
            "schema_version": 1,
            "audience": audience,
            # The fallback must be deterministic so retrying the same batch ID
            # produces byte-for-byte identical event items.
            "created_at": event.get("created_at") or _iso_from_timestamp_ms(timestamp),
        }
        item.pop("visible_at", None)
        normalized.append(_without_none(item))
    return normalized


def _metadata_update_action(
    conversation_id: str,
    metadata_updates: Optional[dict],
    expected_status: Optional[str],
    expected_active_tick_id: Optional[str],
    *,
    refresh_history: bool,
) -> dict:
    updates = dict(metadata_updates or {})
    names = {"#updated_at": "updated_at"}
    values = {":updated_at": _now_iso()}
    sets = ["#updated_at = :updated_at"]
    if refresh_history:
        names["#ttl"] = "ttl"
        values[":ttl"] = int(time.time()) + TTL_SECONDS
        sets.append("#ttl = :ttl")
    for index, (key, value) in enumerate(updates.items()):
        name_key = f"#u{index}"
        value_key = f":u{index}"
        names[name_key] = key
        values[value_key] = value
        sets.append(f"{name_key} = {value_key}")

    condition = "attribute_exists(conversation_id)"
    if expected_status is not None:
        names["#status"] = "status"
        values[":expected_status"] = expected_status
        condition += " AND #status = :expected_status"
    if expected_active_tick_id is not None:
        names["#active_tick_id"] = "active_tick_id"
        values[":expected_active_tick_id"] = expected_active_tick_id
        condition += " AND #active_tick_id = :expected_active_tick_id"

    return {
        "Update": {
            "TableName": config.DYNAMODB_TABLE,
            "Key": _serialize_item({"conversation_id": conversation_id}),
            "UpdateExpression": "SET " + ", ".join(sets),
            "ConditionExpression": condition,
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": _serialize_item(values),
        }
    }


def _event_put_actions(items: list[dict]) -> list[dict]:
    return [
        {
            "Put": {
                "TableName": config.DYNAMODB_EVENT_TABLE,
                "Item": _serialize_item(item),
                "ConditionExpression": (
                    "attribute_not_exists(conversation_id) AND "
                    "attribute_not_exists(event_key)"
                ),
            }
        }
        for item in items
    ]


def _canonical(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    return value


def _existing_batch_matches(items: list[dict]) -> bool:
    table = _get_event_table()
    for expected in items:
        response = table.get_item(
            Key={
                "conversation_id": expected["conversation_id"],
                "event_key": expected["event_key"],
            },
            ConsistentRead=True,
        )
        if _canonical(response.get("Item")) != _canonical(_to_dynamodb_safe(expected)):
            return False
    return bool(items)


def create_conversation(metadata: dict, history_events: list[dict], batch_id: str) -> list[dict]:
    conversation_id = metadata["conversation_id"]
    items = normalize_history_events(conversation_id, history_events, batch_id)
    now = _now_iso()
    metadata_item = _without_none({
        **metadata,
        "event_storage_version": 1,
        "creation_batch_id": batch_id,
        "ai_tick_state_by_participant_id": metadata.get(
            "ai_tick_state_by_participant_id", {}
        ),
        "next_actionable_tick_at": metadata.get("next_actionable_tick_at", 0),
        "created_at": metadata.get("created_at") or now,
        "updated_at": metadata.get("updated_at") or now,
        "ttl": metadata.get("ttl") or int(time.time()) + TTL_SECONDS,
    })
    actions = [{
        "Put": {
            "TableName": config.DYNAMODB_TABLE,
            "Item": _serialize_item(metadata_item),
            "ConditionExpression": "attribute_not_exists(conversation_id)",
        }
    }, *_event_put_actions(items)]
    try:
        _get_client().transact_write_items(
            TransactItems=actions,
            ClientRequestToken=batch_id,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
            raise
        existing = _get_metadata_table().get_item(
            Key={"conversation_id": conversation_id}, ConsistentRead=True
        ).get("Item")
        if (
            existing is None
            or existing.get("creation_batch_id") != batch_id
            or not _existing_batch_matches(items)
        ):
            raise ConditionalWriteFailed("conversation create conflict") from exc
    return items


def append_history_batch(
    conversation_id: str,
    history_events: list[dict],
    batch_id: str,
    metadata_updates: Optional[dict] = None,
    expected_status: Optional[str] = None,
    expected_active_tick_id: Optional[str] = None,
) -> list[dict]:
    items = normalize_history_events(conversation_id, history_events, batch_id)
    if not items:
        raise ValueError("append_history_batch requires at least one event")
    actions = [
        _metadata_update_action(
            conversation_id,
            metadata_updates,
            expected_status,
            expected_active_tick_id,
            refresh_history=True,
        ),
        *_event_put_actions(items),
    ]
    try:
        _get_client().transact_write_items(
            TransactItems=actions,
            ClientRequestToken=batch_id,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
            raise
        if not _existing_batch_matches(items):
            raise ConditionalWriteFailed("history append precondition failed") from exc
    return items


def update_tick_projection(
    conversation_id: str,
    ai_participant_id: str,
    tick_state: dict,
    expected_status: Optional[str] = None,
    expected_active_tick_id: Optional[str] = None,
    next_actionable_tick_at: Optional[int] = None,
) -> bool:
    table = _get_metadata_table()
    names = {
        "#states": "ai_tick_state_by_participant_id",
        "#ai": ai_participant_id,
        "#next": "next_actionable_tick_at",
    }
    values = {
        ":state": _to_dynamodb_safe(tick_state),
        ":next": int(
            next_actionable_tick_at
            if next_actionable_tick_at is not None
            else tick_state.get("next_actionable_at", 0) or 0
        ),
    }
    condition = "attribute_exists(conversation_id)"
    if expected_status is not None:
        names["#status"] = "status"
        values[":expected"] = expected_status
        condition += " AND #status = :expected"
    if expected_active_tick_id is not None:
        names["#active_tick_id"] = "active_tick_id"
        values[":expected_active_tick_id"] = expected_active_tick_id
        condition += " AND #active_tick_id = :expected_active_tick_id"
    try:
        table.update_item(
            Key={"conversation_id": conversation_id},
            UpdateExpression="SET #states.#ai = :state, #next = :next",
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True


def _query_ascending(
    conversation_id: str,
    lower_key: Optional[str],
    upper_key: str,
    limit: int,
) -> tuple[list[dict], bool]:
    if lower_key:
        # DynamoDB allows only one sort-key condition. Event keys are complete,
        # fixed-shape values, so appending "#" is the smallest possible value
        # strictly after the cursor while keeping one BETWEEN expression.
        range_condition = Key("event_key").between(f"{lower_key}#", upper_key)
    else:
        range_condition = Key("event_key").lte(upper_key)
    condition = Key("conversation_id").eq(conversation_id) & range_condition
    response = _get_event_table().query(
        KeyConditionExpression=condition,
        ScanIndexForward=True,
        ConsistentRead=True,
        Limit=limit,
    )
    return response.get("Items", []), "LastEvaluatedKey" in response


def query_live_after(
    conversation_id: str,
    after_cursor: Optional[str],
    now_ms: int,
    limit: int,
) -> dict:
    lower = decode_cursor(after_cursor, conversation_id)["event_key"] if after_cursor else None
    upper = f"H#T{int(now_ms):016d}#~"
    items, has_more = _query_ascending(conversation_id, lower, upper, limit)
    next_after = encode_cursor(conversation_id, items[-1]["event_key"]) if items else after_cursor
    return {"events": items, "next_after": next_after, "has_more": has_more}


def query_live_after_timestamp(
    conversation_id: str,
    after_timestamp: int,
    now_ms: int,
    limit: int,
) -> dict:
    lower = f"H#T{int(after_timestamp):016d}#~"
    upper = f"H#T{int(now_ms):016d}#~"
    items, has_more = _query_ascending(conversation_id, lower, upper, limit)
    next_after = encode_cursor(conversation_id, items[-1]["event_key"]) if items else None
    return {"events": items, "next_after": next_after, "has_more": has_more}


def query_history_before(
    conversation_id: str,
    before_cursor: Optional[str],
    now_ms: int,
    limit: int,
) -> dict:
    upper = f"H#T{int(now_ms):016d}#~"
    if before_cursor:
        upper = min(upper, decode_cursor(before_cursor, conversation_id)["event_key"])
        comparison = Key("event_key").lt(upper)
    else:
        comparison = Key("event_key").lte(upper)
    response = _get_event_table().query(
        KeyConditionExpression=(Key("conversation_id").eq(conversation_id) & comparison),
        ScanIndexForward=False,
        ConsistentRead=True,
        Limit=limit,
    )
    descending = response.get("Items", [])
    items = list(reversed(descending))
    return {
        "events": items,
        "next_before": (
            encode_cursor(conversation_id, items[0]["event_key"]) if items else before_cursor
        ),
        "latest_cursor": (
            encode_cursor(conversation_id, items[-1]["event_key"]) if items else None
        ),
        "has_more": "LastEvaluatedKey" in response,
    }


def query_prompt_events(conversation_id: str, now_ms: int) -> list[dict]:
    table = _get_event_table()
    upper = f"H#T{int(now_ms):016d}#~"
    kwargs = {
        "KeyConditionExpression": (
            Key("conversation_id").eq(conversation_id) & Key("event_key").lte(upper)
        ),
        "ScanIndexForward": True,
        "ConsistentRead": True,
    }
    items: list[dict] = []
    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        kwargs["ExclusiveStartKey"] = last_key


def query_next_pending(
    conversation_id: str,
    after_cursor: Optional[str],
    now_ms: int,
) -> Optional[int]:
    lower = f"H#T{int(now_ms):016d}#~"
    if after_cursor:
        lower = max(lower, decode_cursor(after_cursor, conversation_id)["event_key"])
    response = _get_event_table().query(
        KeyConditionExpression=(
            Key("conversation_id").eq(conversation_id) & Key("event_key").gt(lower)
        ),
        ScanIndexForward=True,
        ConsistentRead=True,
        Limit=1,
    )
    items = response.get("Items", [])
    return event_key_timestamp(items[0]["event_key"]) if items else None
