#!/usr/bin/env python3
"""Rehearse the event migration against disposable synthetic DynamoDB tables."""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path

import boto3


SCRIPT = Path(__file__).with_name("migrate_conversation_events.py")
SPEC = importlib.util.spec_from_file_location("conversation_event_migration", SCRIPT)
migration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migration)


def _legacy_rows() -> list[dict]:
    return [
        {
            "conversation_id": "conv_rehearsal_active",
            "chatroom_id": "scid_rehearsal",
            "status": "active",
            "participants": [
                {"role": "human", "session_id": "human_1", "nickname": "One"},
                {"role": "ai", "session_id": "ai_1", "nickname": "AI"},
            ],
            "events": [
                {"type": "message", "role": "human", "session_id": "human_1", "sender": "One", "content": "hello", "timestamp": 1000, "visible_at": 1000},
                {"type": "tick", "role": "system", "session_id": "ai_1", "timestamp": 1100, "ai_decision": "silent"},
                {"type": "message", "role": "ai", "session_id": "ai_1", "sender": "AI", "content": "hi", "timestamp": 1200, "visible_at": 1500},
            ],
        },
        {
            "conversation_id": "conv_rehearsal_ended",
            "chatroom_id": "scid_rehearsal",
            "status": "ended",
            "participants": [],
            "events": [
                {"type": "lobby_created", "timestamp": 1900},
                {"type": "system", "role": "system", "sender": "System", "content": "This conversation has ended.", "timestamp": 2000},
            ],
        },
        {
            "conversation_id": "conv_rehearsal_empty",
            "chatroom_id": "scid_rehearsal",
            "status": "ended",
            "participants": [],
            "events": [],
        },
    ]


def _create_tables(client, names: dict[str, str]) -> None:
    client.create_table(
        TableName=names["source"],
        KeySchema=[{"AttributeName": "conversation_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "conversation_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    client.create_table(
        TableName=names["metadata"],
        KeySchema=[{"AttributeName": "conversation_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "conversation_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    client.create_table(
        TableName=names["events"],
        KeySchema=[
            {"AttributeName": "conversation_id", "KeyType": "HASH"},
            {"AttributeName": "event_key", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "conversation_id", "AttributeType": "S"},
            {"AttributeName": "event_key", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    waiter = client.get_waiter("table_exists")
    for name in names.values():
        waiter.wait(TableName=name)


def _delete_tables(client, names: dict[str, str]) -> None:
    for name in names.values():
        try:
            client.delete_table(TableName=name)
        except client.exceptions.ResourceNotFoundException:
            pass
    waiter = client.get_waiter("table_not_exists")
    for name in names.values():
        waiter.wait(TableName=name)


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--keep", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.prefix.startswith("stimulize-chatroom-event-rehearsal-"):
        raise SystemExit("prefix must start with stimulize-chatroom-event-rehearsal-")
    names = {
        "source": f"{args.prefix}-legacy",
        "metadata": f"{args.prefix}-metadata",
        "events": f"{args.prefix}-events",
    }
    client = boto3.client("dynamodb", region_name=args.region)
    resource = boto3.resource("dynamodb", region_name=args.region)
    started = time.monotonic()
    checkpoint = Path(f"/tmp/{args.prefix}-checkpoint.json")
    try:
        _create_tables(client, names)
        source = resource.Table(names["source"])
        metadata = resource.Table(names["metadata"])
        events = resource.Table(names["events"])
        with source.batch_writer() as batch:
            for row in _legacy_rows():
                batch.put_item(Item=row)

        plan = migration.build_plan(source, cutover_at_ms=3000)
        migration.apply_plan(plan, metadata, events, checkpoint)
        migration.verify_plan(plan, metadata, events)
        checkpoint.unlink(missing_ok=True)
        migration.apply_plan(plan, metadata, events, checkpoint)
        migration.verify_plan(plan, metadata, events)
        verified = all(
            migration._hash(migration._target_events(events, item["conversation_id"]))
            == item["event_hash"]
            for item in plan["conversations"]
        )
        migrated_metadata = [
            metadata.get_item(
                Key={"conversation_id": item["conversation_id"]},
                ConsistentRead=True,
            )["Item"]
            for item in plan["conversations"]
        ]
        metadata_versions = [
            int(item.get("event_storage_version")) for item in migrated_metadata
        ]
        source_by_id = {row["conversation_id"]: row for row in _legacy_rows()}
        embedded_history_preserved = all(
            migration._hash(actual.get("events", []))
            == migration._hash(source_by_id[actual["conversation_id"]].get("events", []))
            for actual in migrated_metadata
        )
        print(json.dumps({
            "ok": (
                verified
                and metadata_versions == [1, 1, 1]
                and embedded_history_preserved
            ),
            "plan_hash": plan["plan_hash"],
            "conversation_count": len(plan["conversations"]),
            "event_count": sum(len(item["events"]) for item in plan["conversations"]),
            "metadata_versions": metadata_versions,
            "embedded_history_preserved": embedded_history_preserved,
            "idempotent_rerun": True,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "tables": names,
            "kept": args.keep,
        }, sort_keys=True))
        return 0 if verified else 1
    finally:
        checkpoint.unlink(missing_ok=True)
        if not args.keep:
            _delete_tables(client, names)


if __name__ == "__main__":
    raise SystemExit(main())
