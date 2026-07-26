#!/usr/bin/env python3
"""Plan, apply, and verify the embedded-history event-table migration."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import boto3
from botocore.exceptions import ClientError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from chatroom_api.event_store import normalize_history_events


PROTECTED_TABLES = {
    "chatroom-conversations",
    "chatroom-conversation-events",
}
VISIBLE_TYPES = {"message", "system", "error"}
EVENT_FIELDS = {
    "type", "subtype", "audience", "role", "session_id", "participant_id",
    "ai_participant_id", "sender", "internal_name", "avatar", "content",
    "timestamp", "authored_at", "created_at", "message_kind",
}


def _plain(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else str(value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _canonical_json(value) -> str:
    return json.dumps(_plain(value), separators=(",", ":"), sort_keys=True)


def _hash(value) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _batch_id(conversation_id: str, timestamp: int, chunk: int) -> str:
    return uuid5(
        NAMESPACE_URL,
        f"stimulize:event-migration:v1:{conversation_id}:{timestamp}:{chunk}",
    ).hex


def _canonical_history_event(event: dict) -> dict:
    old_timestamp = int(event.get("timestamp", 0) or 0)
    timestamp = int(event.get("visible_at", old_timestamp) or 0)
    migrated = {key: _plain(value) for key, value in event.items() if key in EVENT_FIELDS}
    migrated["timestamp"] = timestamp
    migrated["audience"] = "conversation"
    migrated.pop("visible_at", None)
    if old_timestamp != timestamp:
        migrated["authored_at"] = old_timestamp

    role = migrated.get("role")
    if role == "ai":
        ai_id = migrated.get("ai_participant_id") or migrated.get("session_id")
        if ai_id:
            migrated["ai_participant_id"] = ai_id
        migrated.pop("session_id", None)
    elif role == "system" or migrated.get("type") in {"system", "error"}:
        migrated["role"] = "system"
        migrated.pop("session_id", None)
        migrated.pop("participant_id", None)
        migrated.pop("ai_participant_id", None)
    return migrated


def migrate_history(conversation: dict) -> tuple[list[dict], dict]:
    conversation_id = str(conversation["conversation_id"])
    grouped: dict[int, list[dict]] = defaultdict(list)
    dropped = defaultdict(int)
    for event in conversation.get("events", []) or []:
        event_type = event.get("type")
        if event_type not in VISIBLE_TYPES:
            dropped[str(event_type or "unknown")] += 1
            continue
        migrated = _canonical_history_event(event)
        grouped[int(migrated["timestamp"])].append(migrated)

    items: list[dict] = []
    for timestamp in sorted(grouped):
        same_time = grouped[timestamp]
        for offset in range(0, len(same_time), 25):
            chunk = offset // 25
            batch = same_time[offset:offset + 25]
            items.extend(normalize_history_events(
                conversation_id,
                batch,
                _batch_id(conversation_id, timestamp, chunk),
            ))
    return items, dict(sorted(dropped.items()))


def migrate_metadata(conversation: dict) -> dict:
    migrated = _plain(conversation)
    states: dict[str, dict] = {}
    for participant in migrated.get("participants", []) or []:
        if participant.get("role") == "ai":
            ai_id = participant.get("ai_participant_id") or participant.get("session_id")
            if ai_id:
                participant["ai_participant_id"] = ai_id

    for event in migrated.get("events", []) or []:
        if event.get("type") != "tick":
            continue
        ai_id = event.get("ai_participant_id") or event.get("session_id")
        if not ai_id:
            continue
        timestamp = int(event.get("timestamp", 0) or 0)
        previous = states.get(ai_id)
        if previous and int(previous["last_evaluated_at"]) > timestamp:
            continue
        result = "error" if event.get("error_type") else (
            "spoke" if event.get("ai_decision") == "speak" else "silent"
        )
        states[ai_id] = {
            "last_completed_tick_id": event.get("tick_id") or _batch_id(
                str(migrated["conversation_id"]), timestamp, 0
            ),
            "last_evaluated_at": timestamp,
            "last_result": result,
            "next_actionable_at": int(event.get("visible_at", timestamp) or timestamp),
        }

    migrated["event_storage_version"] = 1
    migrated["ai_tick_state_by_participant_id"] = states
    migrated["next_actionable_tick_at"] = min(
        (state["next_actionable_at"] for state in states.values()),
        default=0,
    )
    return migrated


def _scan_all(table) -> list[dict]:
    items: list[dict] = []
    kwargs = {}
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        kwargs["ExclusiveStartKey"] = last_key


def build_plan(source_table) -> dict:
    conversations = []
    for source in sorted(_scan_all(source_table), key=lambda item: item["conversation_id"]):
        events, dropped = migrate_history(source)
        metadata = migrate_metadata(source)
        conversations.append({
            "conversation_id": str(source["conversation_id"]),
            "source": _plain(source),
            "metadata": metadata,
            "events": events,
            "event_hash": _hash(events),
            "dropped": dropped,
        })
    digest_input = [{
        "conversation_id": item["conversation_id"],
        "metadata_hash": _hash(item["metadata"]),
        "event_hash": item["event_hash"],
        "event_count": len(item["events"]),
        "dropped": item["dropped"],
    } for item in conversations]
    return {"plan_hash": _hash(digest_input), "conversations": conversations}


def _put_metadata(target, item: dict) -> None:
    try:
        target.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(conversation_id)",
        )
    except target.meta.client.exceptions.ConditionalCheckFailedException:
        pass


def _put_event(target, expected: dict) -> None:
    try:
        target.put_item(
            Item=expected,
            ConditionExpression=(
                "attribute_not_exists(conversation_id) AND "
                "attribute_not_exists(event_key)"
            ),
        )
    except target.meta.client.exceptions.ConditionalCheckFailedException:
        existing = target.get_item(
            Key={
                "conversation_id": expected["conversation_id"],
                "event_key": expected["event_key"],
            },
            ConsistentRead=True,
        ).get("Item")
        if _hash(existing) != _hash(expected):
            raise RuntimeError(
                f"event payload conflict: {expected['conversation_id']} {expected['event_key']}"
            )


def _target_events(target, conversation_id: str) -> list[dict]:
    items: list[dict] = []
    kwargs = {
        "KeyConditionExpression": "conversation_id = :conversation_id",
        "ExpressionAttributeValues": {":conversation_id": conversation_id},
        "ConsistentRead": True,
    }
    while True:
        response = target.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        kwargs["ExclusiveStartKey"] = last_key


def apply_plan(plan: dict, metadata_table, event_table, checkpoint: Path) -> None:
    completed = set()
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text())
        if saved.get("plan_hash") != plan["plan_hash"]:
            raise RuntimeError("checkpoint belongs to a different migration plan")
        completed.update(saved.get("completed", []))

    for item in plan["conversations"]:
        conversation_id = item["conversation_id"]
        if conversation_id in completed:
            continue
        # For separate rehearsal tables, first copy the untouched legacy row.
        # The version marker is written only after all event items verify.
        _put_metadata(metadata_table, item["source"])
        for event in item["events"]:
            _put_event(event_table, event)
        actual = _target_events(event_table, conversation_id)
        if _hash(actual) != item["event_hash"]:
            raise RuntimeError(f"verification failed for {conversation_id}")
        metadata_table.update_item(
            Key={"conversation_id": conversation_id},
            UpdateExpression=(
                "SET event_storage_version = :version, "
                "ai_tick_state_by_participant_id = :states, "
                "next_actionable_tick_at = :next, participants = :participants"
            ),
            ConditionExpression="attribute_exists(conversation_id)",
            ExpressionAttributeValues={
                ":version": 1,
                ":states": item["metadata"]["ai_tick_state_by_participant_id"],
                ":next": item["metadata"]["next_actionable_tick_at"],
                ":participants": item["metadata"].get("participants", []),
            },
        )
        actual_metadata = metadata_table.get_item(
            Key={"conversation_id": conversation_id}, ConsistentRead=True
        ).get("Item")
        if actual_metadata is None:
            raise RuntimeError(f"metadata verification failed for {conversation_id}")
        for field in (
            "event_storage_version",
            "ai_tick_state_by_participant_id",
            "next_actionable_tick_at",
            "participants",
        ):
            if _hash(actual_metadata.get(field)) != _hash(item["metadata"].get(field)):
                raise RuntimeError(
                    f"metadata verification failed for {conversation_id}: {field}"
                )
        if _hash(actual_metadata.get("events", [])) != _hash(item["source"].get("events", [])):
            raise RuntimeError(
                f"metadata verification failed for {conversation_id}: legacy events"
            )
        completed.add(conversation_id)
        checkpoint.write_text(_canonical_json({
            "plan_hash": plan["plan_hash"],
            "completed": sorted(completed),
        }) + "\n")


def _summary(plan: dict, mode: str) -> dict:
    return {
        "mode": mode,
        "plan_hash": plan["plan_hash"],
        "conversation_count": len(plan["conversations"]),
        "event_count": sum(len(item["events"]) for item in plan["conversations"]),
        "dropped": {
            key: sum(item["dropped"].get(key, 0) for item in plan["conversations"])
            for key in sorted({key for item in plan["conversations"] for key in item["dropped"]})
        },
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-table", required=True)
    parser.add_argument("--target-metadata-table", required=True)
    parser.add_argument("--target-event-table", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-plan")
    parser.add_argument("--checkpoint", default=".conversation-event-migration.json")
    parser.add_argument("--allow-protected-table", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    names = {args.source_table, args.target_metadata_table, args.target_event_table}
    protected = sorted(names & PROTECTED_TABLES)
    if protected and not args.allow_protected_table:
        print(f"refusing protected table(s): {', '.join(protected)}", file=sys.stderr)
        return 2

    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    source = dynamodb.Table(args.source_table)
    metadata = dynamodb.Table(args.target_metadata_table)
    events = dynamodb.Table(args.target_event_table)
    try:
        plan = build_plan(source)
        print(_canonical_json(_summary(plan, "apply" if args.apply else "dry-run")))
        if not args.apply:
            return 0
        if args.confirm_plan != plan["plan_hash"]:
            print("--confirm-plan must equal the full dry-run plan_hash", file=sys.stderr)
            return 2
        apply_plan(plan, metadata, events, Path(args.checkpoint))
        print(_canonical_json(_summary(plan, "verified")))
        return 0
    except (ClientError, RuntimeError, ValueError) as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
