#!/usr/bin/env python3
"""Guarded live-backup rehearsal for the conversation event migration."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


MIGRATION_SCRIPT = Path(__file__).with_name("migrate_conversation_events.py")
SPEC = importlib.util.spec_from_file_location(
    "conversation_event_migration",
    MIGRATION_SCRIPT,
)
migration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migration)

REHEARSAL_PREFIX = "stimulize-chatroom-event-rehearsal-"
LIVE_SOURCE_TABLE = "chatroom-conversations"


def _canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(value) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def build_manifest(
    *,
    source_table: str,
    region: str,
    run_id: str,
    cutover_at_ms: int,
) -> dict:
    if source_table != LIVE_SOURCE_TABLE:
        raise ValueError(f"source_table must be {LIVE_SOURCE_TABLE}")
    if not run_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in run_id):
        raise ValueError("run_id must contain only lowercase letters, numbers, and hyphens")
    if cutover_at_ms <= 0:
        raise ValueError("cutover_at_ms must be positive")
    prefix = f"{REHEARSAL_PREFIX}{run_id}"
    manifest = {
        "version": 1,
        "source_table": source_table,
        "region": region,
        "run_id": run_id,
        "cutover_at_ms": cutover_at_ms,
        "backup_name": f"{prefix}-backup",
        "metadata_table": f"{prefix}-metadata",
        "event_table": f"{prefix}-events",
        "lobby_table": f"{prefix}-lobbies",
    }
    manifest["manifest_hash"] = _hash(manifest)
    return manifest


def _summary(plan: dict, manifest: dict, stage: str) -> dict:
    return {
        "stage": stage,
        "manifest": manifest,
        "migration": migration._summary(plan, stage),
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(payload) + "\n")


def _require_confirmation(args, manifest: dict, *, plan_hash: str | None = None) -> None:
    if args.confirm_source_table != manifest["source_table"]:
        raise ValueError(
            f"--confirm-source-table must equal {manifest['source_table']}"
        )
    if args.confirm_manifest != manifest["manifest_hash"]:
        raise ValueError("--confirm-manifest must equal the printed manifest_hash")
    if plan_hash is not None and args.confirm_plan != plan_hash:
        raise ValueError("--confirm-plan must equal the dry-run migration plan_hash")


def _find_backup(client, backup_name: str) -> dict | None:
    paginator = client.get_paginator("list_backups")
    for page in paginator.paginate(BackupType="USER"):
        for backup in page.get("BackupSummaries", []):
            if backup.get("BackupName") == backup_name:
                return backup
    return None


def _wait_for_backup(client, backup_arn: str, timeout_seconds: int = 1800) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        details = client.describe_backup(BackupArn=backup_arn)["BackupDescription"][
            "BackupDetails"
        ]
        status = details["BackupStatus"]
        if status == "AVAILABLE":
            return details
        if status not in {"CREATING"}:
            raise RuntimeError(f"backup entered unexpected status {status}")
        time.sleep(5)
    raise TimeoutError("backup did not become AVAILABLE")


def _table_exists(client, table_name: str) -> bool:
    try:
        client.describe_table(TableName=table_name)
        return True
    except client.exceptions.ResourceNotFoundException:
        return False


def _wait_for_table(client, table_name: str) -> dict:
    client.get_waiter("table_exists").wait(
        TableName=table_name,
        WaiterConfig={"Delay": 5, "MaxAttempts": 360},
    )
    return client.describe_table(TableName=table_name)["Table"]


def _tag_table(client, table: dict, manifest: dict) -> None:
    client.tag_resource(
        ResourceArn=table["TableArn"],
        Tags=[
            {"Key": "Purpose", "Value": "event-storage-migration-rehearsal"},
            {"Key": "RunId", "Value": manifest["run_id"]},
            {"Key": "SourceTable", "Value": manifest["source_table"]},
        ],
    )


def _create_event_table(client, manifest: dict) -> dict:
    name = manifest["event_table"]
    if not _table_exists(client, name):
        client.create_table(
            TableName=name,
            KeySchema=[
                {"AttributeName": "conversation_id", "KeyType": "HASH"},
                {"AttributeName": "event_key", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "conversation_id", "AttributeType": "S"},
                {"AttributeName": "event_key", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            DeletionProtectionEnabled=False,
            Tags=[
                {"Key": "Purpose", "Value": "event-storage-migration-rehearsal"},
                {"Key": "RunId", "Value": manifest["run_id"]},
                {"Key": "SourceTable", "Value": manifest["source_table"]},
            ],
        )
    return _wait_for_table(client, name)


def _create_lobby_table(client, manifest: dict) -> dict:
    name = manifest["lobby_table"]
    if not _table_exists(client, name):
        client.create_table(
            TableName=name,
            KeySchema=[{"AttributeName": "lobby_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "lobby_id", "AttributeType": "S"},
                {"AttributeName": "chatroom_id", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "conversation_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "chatroom_id-status-index",
                    "KeySchema": [
                        {"AttributeName": "chatroom_id", "KeyType": "HASH"},
                        {"AttributeName": "status", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "conversation_id-index",
                    "KeySchema": [
                        {"AttributeName": "conversation_id", "KeyType": "HASH"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
            DeletionProtectionEnabled=False,
            Tags=[
                {"Key": "Purpose", "Value": "event-storage-migration-rehearsal"},
                {"Key": "RunId", "Value": manifest["run_id"]},
                {"Key": "SourceTable", "Value": manifest["source_table"]},
            ],
        )
    return _wait_for_table(client, name)


def prepare_resources(client, manifest: dict) -> dict:
    backup = _find_backup(client, manifest["backup_name"])
    if backup is None:
        backup = client.create_backup(
            TableName=manifest["source_table"],
            BackupName=manifest["backup_name"],
        )["BackupDetails"]
    backup = _wait_for_backup(client, backup["BackupArn"])

    metadata_name = manifest["metadata_table"]
    if not _table_exists(client, metadata_name):
        client.restore_table_from_backup(
            TargetTableName=metadata_name,
            BackupArn=backup["BackupArn"],
        )
    metadata = _wait_for_table(client, metadata_name)
    _tag_table(client, metadata, manifest)
    event = _create_event_table(client, manifest)
    lobby = _create_lobby_table(client, manifest)
    return {
        "backup_arn": backup["BackupArn"],
        "metadata_table_arn": metadata["TableArn"],
        "event_table_arn": event["TableArn"],
        "lobby_table_arn": lobby["TableArn"],
    }


def _migration_tables(resource, manifest: dict):
    return (
        resource.Table(manifest["metadata_table"]),
        resource.Table(manifest["event_table"]),
    )


def _build_plan(resource, manifest: dict) -> dict:
    metadata, _events = _migration_tables(resource, manifest)
    return migration.build_plan(metadata, manifest["cutover_at_ms"])


def _cleanup(client, manifest: dict) -> dict:
    deleted = {"tables": [], "backup": None}
    for table_name in (
        manifest["lobby_table"],
        manifest["event_table"],
        manifest["metadata_table"],
    ):
        if _table_exists(client, table_name):
            client.delete_table(TableName=table_name)
            deleted["tables"].append(table_name)
    backup = _find_backup(client, manifest["backup_name"])
    if backup:
        client.delete_backup(BackupArn=backup["BackupArn"])
        deleted["backup"] = backup["BackupArn"]
    return deleted


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("plan", "prepare", "apply", "verify", "cleanup"), default="plan")
    parser.add_argument("--source-table", default=LIVE_SOURCE_TABLE)
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--cutover-at-ms", required=True, type=int)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--confirm-source-table")
    parser.add_argument("--confirm-manifest")
    parser.add_argument("--confirm-plan")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    try:
        manifest = build_manifest(
            source_table=args.source_table,
            region=args.region,
            run_id=args.run_id,
            cutover_at_ms=args.cutover_at_ms,
        )
        print(_canonical_json({"stage": args.stage, "manifest": manifest}))
        if args.stage == "plan":
            return 0

        _require_confirmation(args, manifest)
        client = boto3.client("dynamodb", region_name=args.region)
        resource = boto3.resource("dynamodb", region_name=args.region)
        work_dir = Path(args.work_dir)
        _write_json(work_dir / "manifest.json", manifest)

        if args.stage == "cleanup":
            result = _cleanup(client, manifest)
            _write_json(work_dir / "cleanup.json", result)
            print(_canonical_json(result))
            return 0

        if args.stage == "prepare":
            resources = prepare_resources(client, manifest)
            plan = _build_plan(resource, manifest)
            payload = {
                **_summary(plan, manifest, "dry-run"),
                "resources": resources,
                "started_at": started_at,
                "completed_at": _utc_now(),
                "duration_seconds": round(time.monotonic() - started_monotonic, 3),
            }
            _write_json(work_dir / "dry-run.json", payload)
            print(_canonical_json(payload))
            return 1 if plan.get("issues") else 0

        plan = _build_plan(resource, manifest)
        _require_confirmation(args, manifest, plan_hash=plan["plan_hash"])
        if plan.get("issues"):
            raise RuntimeError("migration plan contains malformed source rows")
        metadata, events = _migration_tables(resource, manifest)
        checkpoint = work_dir / "checkpoint.json"

        if args.stage == "apply":
            migration.apply_plan(plan, metadata, events, checkpoint)
            verification = migration.verify_plan(plan, metadata, events)
            checkpoint.unlink(missing_ok=True)
            migration.apply_plan(plan, metadata, events, checkpoint)
            verification = migration.verify_plan(plan, metadata, events)
        else:
            verification = migration.verify_plan(plan, metadata, events)

        payload = {
            **_summary(plan, manifest, "verified"),
            "verification": verification,
            "idempotent_rerun": args.stage == "apply",
            "started_at": started_at,
            "completed_at": _utc_now(),
            "duration_seconds": round(time.monotonic() - started_monotonic, 3),
        }
        _write_json(work_dir / f"{args.stage}.json", payload)
        print(_canonical_json(payload))
        return 0
    except (ClientError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"rehearsal failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
