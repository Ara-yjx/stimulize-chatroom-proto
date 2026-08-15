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


class MigrationValidationError(ValueError):
    pass


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


def _conversation_id(conversation: dict) -> str:
    conversation_id = conversation.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise MigrationValidationError("conversation_id must be a non-empty string")
    return conversation_id


def migrate_history(conversation: dict) -> tuple[list[dict], dict]:
    conversation_id = _conversation_id(conversation)
    legacy_events = conversation.get("events", []) or []
    if not isinstance(legacy_events, list):
        raise MigrationValidationError("events must be a list")
    grouped: dict[int, list[dict]] = defaultdict(list)
    dropped = defaultdict(int)
    for index, event in enumerate(legacy_events):
        if not isinstance(event, dict):
            raise MigrationValidationError(f"events[{index}] must be an object")
        event_type = event.get("type")
        if event_type not in VISIBLE_TYPES:
            dropped[str(event_type or "unknown")] += 1
            continue
        try:
            migrated = _canonical_history_event(event)
        except (TypeError, ValueError) as exc:
            raise MigrationValidationError(
                f"events[{index}] has invalid timestamp"
            ) from exc
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


def migrate_metadata(conversation: dict, cutover_at_ms: int) -> dict:
    if cutover_at_ms <= 0:
        raise MigrationValidationError("cutover_at_ms must be positive")
    _conversation_id(conversation)
    migrated = _plain(conversation)
    participants = migrated.get("participants", []) or []
    if not isinstance(participants, list):
        raise MigrationValidationError("participants must be a list")
    states: dict[str, dict] = {}
    for index, participant in enumerate(participants):
        if not isinstance(participant, dict):
            raise MigrationValidationError(f"participants[{index}] must be an object")
        if participant.get("role") == "ai":
            ai_id = participant.get("ai_participant_id") or participant.get("session_id")
            if ai_id:
                participant["ai_participant_id"] = ai_id

    legacy_events = migrated.get("events", []) or []
    if not isinstance(legacy_events, list):
        raise MigrationValidationError("events must be a list")
    for index, event in enumerate(legacy_events):
        if not isinstance(event, dict):
            raise MigrationValidationError(f"events[{index}] must be an object")
        if event.get("type") != "tick":
            continue
        ai_id = event.get("ai_participant_id") or event.get("session_id")
        if not ai_id:
            continue
        try:
            timestamp = int(event.get("timestamp", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise MigrationValidationError(
                f"events[{index}] has invalid tick timestamp"
            ) from exc
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
            "next_actionable_at": cutover_at_ms,
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


def build_plan(source_table, cutover_at_ms: int) -> dict:
    if cutover_at_ms <= 0:
        raise MigrationValidationError("cutover_at_ms must be positive")
    conversations = []
    issues = []
    source_rows = _scan_all(source_table)
    for source in sorted(
        source_rows,
        key=lambda item: str(item.get("conversation_id", "")) if isinstance(item, dict) else "",
    ):
        conversation_id = (
            str(source.get("conversation_id", "<missing>"))
            if isinstance(source, dict)
            else "<invalid-row>"
        )
        try:
            if not isinstance(source, dict):
                raise MigrationValidationError("conversation row must be an object")
            events, dropped = migrate_history(source)
            metadata = migrate_metadata(source, cutover_at_ms)
        except (MigrationValidationError, TypeError, ValueError) as exc:
            issues.append({
                "conversation_id": conversation_id,
                "error": str(exc),
            })
            continue
        conversations.append({
            "conversation_id": _conversation_id(source),
            "source": _plain(source),
            "metadata": metadata,
            "events": events,
            "event_hash": _hash(events),
            "dropped": dropped,
        })
    digest_input = {
        "cutover_at_ms": cutover_at_ms,
        "issues": issues,
        "conversations": [{
            "conversation_id": item["conversation_id"],
            "metadata_hash": _hash(item["metadata"]),
            "event_hash": item["event_hash"],
            "event_count": len(item["events"]),
            "dropped": item["dropped"],
        } for item in conversations],
    }
    return {
        "plan_hash": _hash(digest_input),
        "cutover_at_ms": cutover_at_ms,
        "conversations": conversations,
        "issues": issues,
        "source_row_count": len(source_rows),
    }


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


def verify_plan(plan: dict, metadata_table, event_table) -> dict:
    if plan.get("issues"):
        raise RuntimeError("plan contains malformed source rows")

    expected_by_id = {
        item["conversation_id"]: item for item in plan["conversations"]
    }
    actual_event_rows = _scan_all(event_table)
    actual_event_ids = {
        str(item.get("conversation_id", "")) for item in actual_event_rows
    }
    expected_ids = set(expected_by_id)
    extra_ids = sorted(actual_event_ids - expected_ids)
    if extra_ids:
        raise RuntimeError(
            f"verification found extra event partitions: {', '.join(extra_ids)}"
        )

    verified_events = 0
    for conversation_id, item in expected_by_id.items():
        actual_events = sorted(
            (
                event for event in actual_event_rows
                if str(event.get("conversation_id", "")) == conversation_id
            ),
            key=lambda event: str(event.get("event_key", "")),
        )
        if _hash(actual_events) != item["event_hash"]:
            raise RuntimeError(f"event verification failed for {conversation_id}")
        verified_events += len(actual_events)

        actual_metadata = metadata_table.get_item(
            Key={"conversation_id": conversation_id},
            ConsistentRead=True,
        ).get("Item")
        if actual_metadata is None:
            raise RuntimeError(f"metadata missing for {conversation_id}")
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
        if _hash(actual_metadata.get("events", [])) != _hash(
            item["source"].get("events", [])
        ):
            raise RuntimeError(
                f"metadata verification failed for {conversation_id}: legacy events"
            )

    metadata_ids = {
        str(item.get("conversation_id", "")) for item in _scan_all(metadata_table)
    }
    if metadata_ids != expected_ids:
        missing = sorted(expected_ids - metadata_ids)
        extra = sorted(metadata_ids - expected_ids)
        raise RuntimeError(
            "metadata ID verification failed: "
            f"missing={missing}, extra={extra}"
        )

    return {
        "conversation_count": len(expected_ids),
        "event_count": verified_events,
        "legacy_events_preserved": True,
    }


def apply_plan(plan: dict, metadata_table, event_table, checkpoint: Path) -> None:
    if plan.get("issues"):
        raise RuntimeError("plan contains malformed source rows")
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
        "cutover_at_ms": plan["cutover_at_ms"],
        "source_row_count": plan["source_row_count"],
        "conversation_count": len(plan["conversations"]),
        "event_count": sum(len(item["events"]) for item in plan["conversations"]),
        "dropped": {
            key: sum(item["dropped"].get(key, 0) for item in plan["conversations"])
            for key in sorted({key for item in plan["conversations"] for key in item["dropped"]})
        },
        "issue_count": len(plan.get("issues", [])),
    }


def _write_report(path: str | None, plan: dict, mode: str, verification=None) -> None:
    if not path:
        return
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_canonical_json({
        **_summary(plan, mode),
        "issues": plan.get("issues", []),
        **({"verification": verification} if verification else {}),
    }) + "\n")


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-table", required=True)
    parser.add_argument("--target-metadata-table", required=True)
    parser.add_argument("--target-event-table", required=True)
    parser.add_argument("--region", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--cutover-at-ms", required=True, type=int)
    parser.add_argument("--confirm-plan")
    parser.add_argument("--report-json")
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
        plan = build_plan(source, args.cutover_at_ms)
        mode = "verify" if args.verify else ("apply" if args.apply else "dry-run")
        print(_canonical_json(_summary(plan, mode)))
        _write_report(args.report_json, plan, mode)
        if plan.get("issues"):
            print("migration plan contains malformed source rows", file=sys.stderr)
            return 1
        if not args.apply and not args.verify:
            return 0
        if args.confirm_plan != plan["plan_hash"]:
            print("--confirm-plan must equal the full dry-run plan_hash", file=sys.stderr)
            return 2
        if args.apply:
            apply_plan(plan, metadata, events, Path(args.checkpoint))
        verification = verify_plan(plan, metadata, events)
        _write_report(args.report_json, plan, "verified", verification)
        print(_canonical_json({
            **_summary(plan, "verified"),
            "verification": verification,
        }))
        return 0
    except (ClientError, RuntimeError, ValueError) as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
