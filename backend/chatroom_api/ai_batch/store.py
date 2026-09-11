"""DynamoDB, SQS, and S3 primitives for the AI batch handlers."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from chatroom_api import config, event_store
from chatroom_api.ai_batch.contracts import (
    BATCH_EXECUTABLE_STATUSES,
    BATCH_TERMINAL_STATUSES,
    terminal_batch_status,
    work_message,
)
from chatroom_api.dynamo import _to_dynamodb_safe


_batch_table = None
_metadata_table = None
_client = None
_sqs = None
_s3 = None
_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ms() -> int:
    return int(time.time() * 1000)


def _get_batch_table():
    global _batch_table
    if _batch_table is None:
        _batch_table = boto3.resource("dynamodb").Table(config.AI_BATCH_TABLE)
    return _batch_table


def _get_metadata_table():
    global _metadata_table
    if _metadata_table is None:
        _metadata_table = boto3.resource("dynamodb").Table(config.DYNAMODB_TABLE)
    return _metadata_table


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("dynamodb")
    return _client


def _get_sqs():
    global _sqs
    if _sqs is None:
        _sqs = boto3.client("sqs")
    return _sqs


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _serialize(value: dict) -> dict:
    return {
        key: _serializer.serialize(_to_dynamodb_safe(item))
        for key, item in value.items()
    }


def _deserialize(value: dict) -> dict:
    return {key: _deserializer.deserialize(item) for key, item in value.items()}


def get_batch(batch_job_id: str) -> Optional[dict]:
    return _get_batch_table().get_item(
        Key={"batch_job_id": batch_job_id},
        ConsistentRead=True,
    ).get("Item")


def get_conversation(conversation_id: str) -> Optional[dict]:
    return _get_metadata_table().get_item(
        Key={"conversation_id": conversation_id},
        ConsistentRead=True,
    ).get("Item")


def query_history(conversation_id: str) -> list[dict]:
    return event_store.query_prompt_events(conversation_id, now_ms())


def acquire_provision_lease(
    batch_job_id: str,
    lease_id: str,
    now_value_ms: int,
    lease_ms: int,
) -> bool:
    table = _get_batch_table()
    try:
        table.update_item(
            Key={"batch_job_id": batch_job_id},
            UpdateExpression=(
                "SET #status = :provisioning, provision_lease_id = :lease, "
                "provision_lease_until = :until, updated_at = :updated"
            ),
            ConditionExpression=(
                "#status IN (:queued, :provisioning) AND "
                "(attribute_not_exists(provision_lease_until) OR provision_lease_until < :now)"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":queued": "queued",
                ":provisioning": "provisioning",
                ":lease": lease_id,
                ":until": now_value_ms + lease_ms,
                ":now": now_value_ms,
                ":updated": now_iso(),
            },
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True


def finish_provision(batch_job_id: str, lease_id: str) -> bool:
    table = _get_batch_table()
    try:
        table.update_item(
            Key={"batch_job_id": batch_job_id},
            UpdateExpression=(
                "SET #status = :running, updated_at = :updated "
                "REMOVE provision_lease_id, provision_lease_until"
            ),
            ConditionExpression=(
                "#status = :provisioning AND provision_lease_id = :lease"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":running": "running",
                ":provisioning": "provisioning",
                ":lease": lease_id,
                ":updated": now_iso(),
            },
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True


def release_provision_lease(batch_job_id: str, lease_id: str) -> bool:
    table = _get_batch_table()
    try:
        table.update_item(
            Key={"batch_job_id": batch_job_id},
            UpdateExpression=(
                "SET updated_at = :updated "
                "REMOVE provision_lease_id, provision_lease_until"
            ),
            ConditionExpression=(
                "#status = :provisioning AND provision_lease_id = :lease"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":provisioning": "provisioning",
                ":lease": lease_id,
                ":updated": now_iso(),
            },
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True


def record_batch_error(batch_job_id: str, error: str) -> None:
    _get_batch_table().update_item(
        Key={"batch_job_id": batch_job_id},
        UpdateExpression="SET last_error = :error, updated_at = :updated",
        ExpressionAttributeValues={
            ":error": str(error)[:1000],
            ":updated": now_iso(),
        },
    )


def fail_provision(batch_job_id: str, lease_id: str, error: str) -> bool:
    table = _get_batch_table()
    try:
        table.update_item(
            Key={"batch_job_id": batch_job_id},
            UpdateExpression=(
                "SET #status = :failed, failed_count = batch_count, "
                "unfinished_count = :zero, queued_count = :zero, "
                "last_error = :error, updated_at = :updated "
                "REMOVE provision_lease_id, provision_lease_until"
            ),
            ConditionExpression=(
                "#status = :provisioning AND provision_lease_id = :lease"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":failed": "failed",
                ":provisioning": "provisioning",
                ":lease": lease_id,
                ":zero": 0,
                ":error": str(error)[:1000],
                ":updated": now_iso(),
            },
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True


def create_conversation(metadata: dict, initial_events: list[dict], write_id: str) -> None:
    try:
        event_store.create_conversation(metadata, initial_events, write_id)
    except event_store.ConditionalWriteFailed:
        existing = get_conversation(metadata["conversation_id"])
        if not existing or existing.get("batch_job_id") != metadata.get("batch_job_id"):
            raise


def send_work(batch_job_id: str, conversation_id: str, expected_turn: int) -> None:
    if not config.AI_BATCH_WORK_QUEUE_URL:
        raise RuntimeError("AI_BATCH_WORK_QUEUE_URL is not configured")
    body = work_message(batch_job_id, conversation_id, expected_turn)
    _get_sqs().send_message(
        QueueUrl=config.AI_BATCH_WORK_QUEUE_URL,
        MessageBody=json.dumps(body, separators=(",", ":"), sort_keys=True),
        MessageGroupId=conversation_id,
        MessageDeduplicationId=f"{conversation_id}:{expected_turn}",
    )


def acquire_worker_lease(
    batch_job_id: str,
    conversation: dict,
    expected_turn: int,
    lease_id: str,
    now_value_ms: int,
    lease_ms: int,
) -> bool:
    status = conversation.get("status")
    if status not in {"queued", "running"}:
        return False
    names = {"#status": "status"}
    values = {
        ":batch": batch_job_id,
        ":expected_turn": int(expected_turn),
        ":expected_status": status,
        ":running": "running",
        ":lease": lease_id,
        ":until": now_value_ms + lease_ms,
        ":now": now_value_ms,
        ":updated": now_iso(),
    }
    actions = [{
        "Update": {
            "TableName": config.DYNAMODB_TABLE,
            "Key": _serialize({"conversation_id": conversation["conversation_id"]}),
            "UpdateExpression": (
                "SET #status = :running, worker_lease_id = :lease, "
                "worker_lease_until = :until, updated_at = :updated"
            ),
            "ConditionExpression": (
                "batch_job_id = :batch AND next_turn = :expected_turn AND "
                "#status = :expected_status AND "
                "(attribute_not_exists(worker_lease_until) OR worker_lease_until < :now)"
            ),
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": _serialize(values),
        }
    }]
    if status == "queued":
        actions.append({
            "Update": {
                "TableName": config.AI_BATCH_TABLE,
                "Key": _serialize({"batch_job_id": batch_job_id}),
                "UpdateExpression": (
                    "SET updated_at = :updated "
                    "ADD queued_count :minus_one, running_count :one"
                ),
                "ConditionExpression": (
                    "#status IN (:provisioning, :running) AND queued_count > :zero"
                ),
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": _serialize({
                    ":updated": now_iso(),
                    ":minus_one": -1,
                    ":one": 1,
                    ":zero": 0,
                    ":provisioning": "provisioning",
                    ":running": "running",
                }),
            }
        })
    try:
        _get_client().transact_write_items(
            TransactItems=actions,
            ClientRequestToken=lease_id,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
            return False
        raise
    return True


def release_worker_lease(conversation_id: str, lease_id: str, error: str | None = None) -> bool:
    table = _get_metadata_table()
    expression = "SET updated_at = :updated"
    values = {":updated": now_iso(), ":lease": lease_id}
    if error:
        expression += ", last_error = :error"
        values[":error"] = str(error)[:1000]
    expression += " REMOVE worker_lease_id, worker_lease_until"
    try:
        table.update_item(
            Key={"conversation_id": conversation_id},
            UpdateExpression=expression,
            ConditionExpression="worker_lease_id = :lease",
            ExpressionAttributeValues=values,
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True


def _terminal_batch_action(batch_job_id: str, terminal_status: str) -> dict:
    counter = {
        "completed": "completed_count",
        "failed": "failed_count",
        "timed_out": "timed_out_count",
    }[terminal_status]
    return {
        "Update": {
            "TableName": config.AI_BATCH_TABLE,
            "Key": _serialize({"batch_job_id": batch_job_id}),
            "UpdateExpression": (
                f"SET updated_at = :updated ADD unfinished_count :minus_one, "
                f"running_count :minus_one, {counter} :one"
            ),
            "ConditionExpression": (
                "#status IN (:provisioning, :running, :timed_out) AND unfinished_count > :zero"
            ),
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": _serialize({
                ":updated": now_iso(),
                ":minus_one": -1,
                ":one": 1,
                ":zero": 0,
                ":provisioning": "provisioning",
                ":running": "running",
                ":timed_out": "timed_out",
            }),
        }
    }


def commit_turn(
    conversation: dict,
    lease_id: str,
    event: dict,
    *,
    terminal_status: str | None = None,
) -> None:
    expected_turn = int(conversation["next_turn"])
    next_turn = expected_turn + 1
    content = str(event.get("content") or "")
    metadata_updates = {
        "status": terminal_status or "running",
        "state_version": int(conversation.get("state_version", 0) or 0) + 1,
        "next_turn": next_turn,
        "message_count": int(conversation.get("message_count", 0) or 0) + 1,
        "total_chars": int(conversation.get("total_chars", 0) or 0) + len(content),
        "last_speaker_id": event.get("ai_participant_id"),
    }
    extra = (
        [_terminal_batch_action(conversation["batch_job_id"], terminal_status)]
        if terminal_status
        else []
    )
    event_store.append_history_batch(
        conversation["conversation_id"],
        [event],
        event["turn_write_id"],
        metadata_updates=metadata_updates,
        metadata_remove=["worker_lease_id", "worker_lease_until", "last_error"],
        expected_status="running",
        expected_metadata={
            "batch_job_id": conversation["batch_job_id"],
            "next_turn": expected_turn,
            "worker_lease_id": lease_id,
        },
        extra_transact_actions=extra,
    )


def mark_conversation_terminal(
    conversation: dict,
    terminal_status: str,
    *,
    error: str | None = None,
    expected_lease_id: str | None = None,
) -> bool:
    if terminal_status not in {"failed", "timed_out", "completed"}:
        raise ValueError("invalid conversation terminal status")
    names = {"#status": "status"}
    values = {
        ":terminal": terminal_status,
        ":queued": "queued",
        ":running": "running",
        ":batch": conversation["batch_job_id"],
        ":updated": now_iso(),
    }
    condition = (
        "batch_job_id = :batch AND #status IN (:queued, :running)"
    )
    if expected_lease_id:
        values[":lease"] = expected_lease_id
        condition += " AND worker_lease_id = :lease"
    update_expression = "SET #status = :terminal, updated_at = :updated"
    if error:
        values[":error"] = str(error)[:1000]
        update_expression += ", last_error = :error"
    update_expression += " REMOVE worker_lease_id, worker_lease_until"

    batch_action = _terminal_batch_action(
        conversation["batch_job_id"], terminal_status
    )
    if conversation.get("status") == "queued":
        batch_update = batch_action["Update"]
        batch_update["UpdateExpression"] = batch_update["UpdateExpression"].replace(
            "running_count :minus_one", "queued_count :minus_one"
        )
    actions = [{
        "Update": {
            "TableName": config.DYNAMODB_TABLE,
            "Key": _serialize({"conversation_id": conversation["conversation_id"]}),
            "UpdateExpression": update_expression,
            "ConditionExpression": condition,
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": _serialize(values),
        }
    }, batch_action]
    try:
        _get_client().transact_write_items(TransactItems=actions)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
            return False
        raise
    return True


def finalize_batch_if_done(batch_job_id: str) -> Optional[str]:
    batch = get_batch(batch_job_id)
    if not batch:
        return None
    if batch.get("status") in BATCH_TERMINAL_STATUSES:
        return str(batch["status"])
    if int(batch.get("unfinished_count", 0) or 0) != 0:
        return None
    final_status = terminal_batch_status(batch)
    table = _get_batch_table()
    try:
        table.update_item(
            Key={"batch_job_id": batch_job_id},
            UpdateExpression="SET #status = :final, updated_at = :updated",
            ConditionExpression=(
                "#status IN (:provisioning, :running) AND unfinished_count = :zero"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":final": final_status,
                ":updated": now_iso(),
                ":provisioning": "provisioning",
                ":running": "running",
                ":zero": 0,
            },
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        latest = get_batch(batch_job_id)
        return str(latest["status"]) if latest else None
    return final_status


def mark_batch_timed_out(batch_job_id: str, now_value_ms: int) -> bool:
    table = _get_batch_table()
    try:
        table.update_item(
            Key={"batch_job_id": batch_job_id},
            UpdateExpression="SET #status = :timed_out, updated_at = :updated",
            ConditionExpression=(
                "#status IN (:queued, :provisioning, :running) AND deadline_at <= :now"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":timed_out": "timed_out",
                ":queued": "queued",
                ":provisioning": "provisioning",
                ":running": "running",
                ":now": now_value_ms,
                ":updated": now_iso(),
            },
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True


def put_export_object(key: str, body: bytes) -> None:
    _get_s3().put_object(
        Bucket=config.AI_BATCH_EXPORT_BUCKET,
        Key=key,
        Body=body,
        ContentType="application/zip",
        ServerSideEncryption="AES256",
    )


def export_object_exists(key: str) -> bool:
    try:
        _get_s3().head_object(Bucket=config.AI_BATCH_EXPORT_BUCKET, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
    return True


def acquire_export_lease(
    batch_job_id: str,
    export_job_id: str,
    lease_id: str,
    now_value_ms: int,
    lease_ms: int,
) -> bool:
    table = _get_batch_table()
    try:
        table.update_item(
            Key={"batch_job_id": batch_job_id},
            UpdateExpression=(
                "SET export_status = :building, export_lease_id = :lease, "
                "export_lease_until = :until, updated_at = :updated"
            ),
            ConditionExpression=(
                "#status IN (:completed, :partial, :failed, :timed_out) AND "
                "export_job_id = :job AND export_status IN (:requested, :building) AND "
                "(attribute_not_exists(export_lease_until) OR export_lease_until < :now)"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":completed": "completed",
                ":partial": "partial_failure",
                ":failed": "failed",
                ":timed_out": "timed_out",
                ":requested": "requested",
                ":building": "building",
                ":job": export_job_id,
                ":lease": lease_id,
                ":until": now_value_ms + lease_ms,
                ":now": now_value_ms,
                ":updated": now_iso(),
            },
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True


def finish_export(
    batch_job_id: str,
    export_job_id: str,
    lease_id: str,
    object_key: str,
) -> bool:
    table = _get_batch_table()
    try:
        table.update_item(
            Key={"batch_job_id": batch_job_id},
            UpdateExpression=(
                "SET export_status = :ready, export_s3_key = :key, "
                "updated_at = :updated REMOVE export_lease_id, "
                "export_lease_until, export_error"
            ),
            ConditionExpression=(
                "export_job_id = :job AND export_lease_id = :lease"
            ),
            ExpressionAttributeValues={
                ":ready": "ready",
                ":key": object_key,
                ":updated": now_iso(),
                ":job": export_job_id,
                ":lease": lease_id,
            },
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True


def fail_export(
    batch_job_id: str,
    export_job_id: str,
    lease_id: str,
    error: str,
) -> bool:
    table = _get_batch_table()
    try:
        table.update_item(
            Key={"batch_job_id": batch_job_id},
            UpdateExpression=(
                "SET export_status = :failed, export_error = :error, "
                "updated_at = :updated REMOVE export_lease_id, export_lease_until"
            ),
            ConditionExpression=(
                "export_job_id = :job AND export_lease_id = :lease"
            ),
            ExpressionAttributeValues={
                ":failed": "failed",
                ":error": str(error)[:1000],
                ":updated": now_iso(),
                ":job": export_job_id,
                ":lease": lease_id,
            },
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True
