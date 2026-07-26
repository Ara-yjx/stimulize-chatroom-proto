"""Embedded-history adapter used until a stack explicitly enables event storage."""

from __future__ import annotations

import copy
import time
from datetime import datetime, timezone
from typing import Optional

from chatroom_api import dynamo
from chatroom_api.cursors import decode_cursor, encode_cursor, event_key_timestamp, make_event_key
from chatroom_api.event_store import ConditionalWriteFailed, normalize_history_events


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _events(conversation_id: str) -> list[dict]:
    conversation = dynamo.get_conversation(conversation_id) or {}
    canonical = []
    for index, source in enumerate(conversation.get("events", []) or []):
        event = copy.deepcopy(source)
        timestamp = int(event.get("visible_at", event.get("timestamp", 0)) or 0)
        event["timestamp"] = timestamp
        event.pop("visible_at", None)
        if not event.get("event_key"):
            event["event_key"] = make_event_key(timestamp, "legacy", index)
        event.setdefault("event_id", f"legacy#{index}")
        canonical.append(event)
    return sorted(canonical, key=lambda item: item["event_key"])


def create_conversation(metadata: dict, history_events: list[dict], batch_id: str) -> list[dict]:
    conversation_id = metadata["conversation_id"]
    items = normalize_history_events(conversation_id, history_events, batch_id)
    now = _now_iso()
    row = dynamo._to_dynamodb_safe({
        **metadata,
        "creation_batch_id": batch_id,
        "events": [{**item, "visible_at": item["timestamp"]} for item in items],
        "event_batch_ids": [batch_id],
        "created_at": metadata.get("created_at") or now,
        "updated_at": metadata.get("updated_at") or now,
        "ttl": metadata.get("ttl") or int(time.time()) + dynamo.TTL_SECONDS,
    })
    table = dynamo._get_table()
    try:
        table.put_item(
            Item=row,
            ConditionExpression="attribute_not_exists(conversation_id)",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException as exc:
        existing = dynamo.get_conversation(conversation_id)
        if existing is None or existing.get("creation_batch_id") != batch_id:
            raise ConditionalWriteFailed("conversation create conflict") from exc
    return items


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

    names = {"#events": "events", "#ttl": "ttl", "#updated": "updated_at"}
    values = {
        ":events": dynamo._to_dynamodb_safe([
            {**item, "visible_at": item["timestamp"]} for item in items
        ]),
        ":empty": [],
        ":batch": [batch_id],
        ":batch_id": batch_id,
        ":updated": _now_iso(),
        ":ttl": int(time.time()) + dynamo.TTL_SECONDS,
    }
    sets = [
        "#events = list_append(if_not_exists(#events, :empty), :events)",
        "event_batch_ids = list_append(if_not_exists(event_batch_ids, :empty), :batch)",
        "#updated = :updated",
        "#ttl = :ttl",
    ]
    for index, (key, value) in enumerate((metadata_updates or {}).items()):
        name = f"#u{index}"
        token = f":u{index}"
        names[name] = key
        values[token] = dynamo._to_dynamodb_safe(value)
        sets.append(f"{name} = {token}")

    condition = (
        "attribute_exists(conversation_id) AND "
        "(attribute_not_exists(event_batch_ids) OR NOT contains(event_batch_ids, :batch_id))"
    )
    if expected_status is not None:
        names["#status"] = "status"
        values[":status"] = expected_status
        condition += " AND #status = :status"

    table = dynamo._get_table()
    try:
        table.update_item(
            Key={"conversation_id": conversation_id},
            UpdateExpression="SET " + ", ".join(sets),
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException as exc:
        existing = dynamo.get_conversation(conversation_id) or {}
        if batch_id not in (existing.get("event_batch_ids") or []):
            raise ConditionalWriteFailed("history append precondition failed") from exc
    return items


def update_tick_projection(
    conversation_id: str,
    ai_participant_id: str,
    tick_state: dict,
    expected_status: Optional[str] = None,
    next_actionable_tick_at: Optional[int] = None,
) -> None:
    conversation = dynamo.get_conversation(conversation_id)
    if conversation is None or (
        expected_status is not None and conversation.get("status") != expected_status
    ):
        return
    states = dict(conversation.get("ai_tick_state_by_participant_id") or {})
    states[ai_participant_id] = copy.deepcopy(tick_state)
    dynamo._get_table().update_item(
        Key={"conversation_id": conversation_id},
        UpdateExpression=(
            "SET ai_tick_state_by_participant_id = :states, "
            "next_actionable_tick_at = :next"
        ),
        ExpressionAttributeValues={
            ":states": dynamo._to_dynamodb_safe(states),
            ":next": int(
                next_actionable_tick_at
                if next_actionable_tick_at is not None
                else tick_state.get("next_actionable_at", 0) or 0
            ),
        },
    )


def _ascending_page(events: list[dict], lower: Optional[str], upper: str, limit: int) -> dict:
    eligible = [
        event for event in events
        if (lower is None or event["event_key"] > lower) and event["event_key"] <= upper
    ]
    page = eligible[:limit]
    return {
        "events": page,
        "next_after": encode_cursor(page[-1]["conversation_id"], page[-1]["event_key"])
        if page else None,
        "has_more": len(eligible) > limit,
    }


def query_live_after(
    conversation_id: str, after_cursor: Optional[str], now_ms: int, limit: int
) -> dict:
    lower = decode_cursor(after_cursor, conversation_id)["event_key"] if after_cursor else None
    events = _events(conversation_id)
    for event in events:
        event["conversation_id"] = conversation_id
    page = _ascending_page(events, lower, f"H#T{int(now_ms):016d}#~", limit)
    if not page["events"]:
        page["next_after"] = after_cursor
    return page


def query_live_after_timestamp(
    conversation_id: str, after_timestamp: int, now_ms: int, limit: int
) -> dict:
    events = _events(conversation_id)
    for event in events:
        event["conversation_id"] = conversation_id
    return _ascending_page(
        events,
        f"H#T{int(after_timestamp):016d}#~",
        f"H#T{int(now_ms):016d}#~",
        limit,
    )


def query_history_before(
    conversation_id: str, before_cursor: Optional[str], now_ms: int, limit: int
) -> dict:
    upper = f"H#T{int(now_ms):016d}#~"
    if before_cursor:
        upper = min(upper, decode_cursor(before_cursor, conversation_id)["event_key"])
    eligible = [event for event in _events(conversation_id) if event["event_key"] < upper]
    page = list(reversed(eligible))[:limit]
    items = list(reversed(page))
    return {
        "events": items,
        "next_before": encode_cursor(conversation_id, items[0]["event_key"])
        if items else before_cursor,
        "latest_cursor": encode_cursor(conversation_id, items[-1]["event_key"])
        if items else None,
        "has_more": len(eligible) > limit,
    }


def query_prompt_events(conversation_id: str, now_ms: int) -> list[dict]:
    return [event for event in _events(conversation_id) if int(event["timestamp"]) <= now_ms]


def query_next_pending(
    conversation_id: str, after_cursor: Optional[str], now_ms: int
) -> Optional[int]:
    lower = decode_cursor(after_cursor, conversation_id)["event_key"] if after_cursor else None
    for event in _events(conversation_id):
        if int(event["timestamp"]) > now_ms and (lower is None or event["event_key"] > lower):
            return event_key_timestamp(event["event_key"])
    return None
