"""DynamoDB, SQS, and S3 primitives for the AI batch handlers."""

from __future__ import annotations

import json
import hashlib
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
    BATCH_TERMINAL_STATUSES,
    conversation_id,
    terminal_batch_status,
    turn_write_id,
)
from chatroom_api.dynamo import _to_dynamodb_safe


_batch_table = None
_metadata_table = None
_client = None
_s3 = None
_sfn = None
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


def fail_validation(batch_job_id: str, lease_id: str, error: str, batch_count: int) -> None:
    # No conversations exist on this path. Settle all requested slots atomically.
    _get_batch_table().update_item(
        Key={'batch_job_id': batch_job_id},
        UpdateExpression=('SET #status = :failed, last_error = :error, updated_at = :updated, '
                          'queued_count = :zero, running_count = :zero, unfinished_count = :zero, '
                          'failed_count = :count REMOVE provision_lease_id, provision_lease_until'),
        ConditionExpression=('#status = :provisioning AND provision_lease_id = :lease '
                             'AND attribute_not_exists(prompt_reference_key)'),
        ExpressionAttributeNames={'#status': 'status'},
        ExpressionAttributeValues={':failed': 'validation_failed', ':error': error,
            ':updated': now_iso(), ':zero': 0, ':count': batch_count,
            ':provisioning': 'provisioning', ':lease': lease_id},
    )


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
        # Very short conversations can finish before fan-out itself returns.
        latest = get_batch(batch_job_id)
        return bool(latest and latest.get("status") in BATCH_TERMINAL_STATUSES
                    and int(latest.get("unfinished_count", 0)) == 0)
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
    batch = get_batch(batch_job_id)
    if not batch or batch.get("provision_lease_id") != lease_id:
        return False
    # Some workflows may already have completed while fan-out failed. Never
    # overwrite their counters with batch_count or erase unfinished work.
    for index in range(int(batch["batch_count"])):
        conv_id = conversation_id(batch_job_id, index)
        if get_conversation(conv_id) is None:
            create_conversation({
                "conversation_id": conv_id, "batch_job_id": batch_job_id,
                "batch_index": index, "conversation_type": "ai_batch",
                "owner_id": batch["owner_id"], "chatroom_id": batch["chatroom_id"],
                "created_at": batch["created_at"], "deadline_at": batch["deadline_at"],
                "status": "queued", "execution_state": "pending", "outcome": None,
                "state_version": 0, "next_turn": 0, "message_count": 0, "total_chars": 0,
            }, [], turn_write_id(conv_id, -1))
        for _ in range(3):
            row = get_conversation(conv_id)
            if row.get("execution_state") == "terminal":
                break
            if mark_conversation_terminal(row, "failed", error=error):
                break
        else:
            raise RuntimeError("could not finalize partially provisioned conversation")
    finalize_batch_if_done(batch_job_id)
    return True


def create_conversation(metadata: dict, initial_events: list[dict], write_id: str) -> None:
    try:
        event_store.create_conversation(metadata, initial_events, write_id)
    except event_store.ConditionalWriteFailed:
        existing = get_conversation(metadata["conversation_id"])
        if not existing or existing.get("batch_job_id") != metadata.get("batch_job_id"):
            raise


def start_execution(batch_job_id: str, conversation_id: str, deadline_at: int) -> str:
    global _sfn
    arn = config.AI_BATCH_STATE_MACHINE_ARN
    if not arn:
        raise RuntimeError("AI_BATCH_STATE_MACHINE_ARN is not configured")
    if _sfn is None:
        _sfn = boto3.client("stepfunctions")
    body = {
        "batch_job_id": batch_job_id,
        "conversation_id": conversation_id,
        "deadline": datetime.fromtimestamp(deadline_at / 1000, timezone.utc).isoformat(),
    }
    # Stable name AND input make retries safe even when the first response was lost.
    execution_arn = arn.replace(":stateMachine:", ":execution:") + ":" + conversation_id
    try:
        _sfn.start_execution(
            stateMachineArn=arn, name=conversation_id,
            input=json.dumps(body, sort_keys=True, separators=(",", ":")),
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ExecutionAlreadyExists":
            raise
    _get_metadata_table().update_item(
        Key={"conversation_id": conversation_id},
        UpdateExpression="SET execution_arn = :arn",
        ConditionExpression="batch_job_id = :batch",
        ExpressionAttributeValues={":arn": execution_arn, ":batch": batch_job_id},
    )
    return execution_arn


def start_conversation(
    batch_job_id: str,
    conversation: dict,
) -> bool:
    status = conversation.get("status")
    if status not in {"queued", "running"}:
        return False
    if status == "running":
        return True
    names = {"#status": "status"}
    values = {
        ":batch": batch_job_id,
        ":expected_turn": int(conversation["next_turn"]),
        ":expected_status": status,
        ":running": "running",
        ":updated": now_iso(),
    }
    actions = [{
        "Update": {
            "TableName": config.DYNAMODB_TABLE,
            "Key": _serialize({"conversation_id": conversation["conversation_id"]}),
            "UpdateExpression": (
                "SET #status = :running, execution_state = :running, updated_at = :updated"
            ),
            "ConditionExpression": (
                "batch_job_id = :batch AND next_turn = :expected_turn AND "
                "#status = :expected_status"
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
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
            return False
        raise
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
    event: dict,
    *,
    terminal_status: str | None = None,
) -> None:
    expected_turn = int(conversation["next_turn"])
    next_turn = expected_turn + 1
    content = str(event.get("content") or "")
    metadata_updates = {
        "status": terminal_status or "running",
        "execution_state": "terminal" if terminal_status else "running",
        "outcome": "succeeded" if terminal_status == "completed" else terminal_status,
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
        metadata_remove=["last_error"],
        expected_status="running",
        expected_metadata={
            "batch_job_id": conversation["batch_job_id"],
            "next_turn": expected_turn,
            "state_version": int(conversation.get("state_version", 0)),
        },
        extra_transact_actions=extra,
    )


def mark_conversation_terminal(
    conversation: dict,
    terminal_status: str,
    *,
    error: str | None = None,
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
        ":execution": "terminal",
        ":outcome": "succeeded" if terminal_status == "completed" else terminal_status,
        ":expected_status": conversation["status"],
        ":version": int(conversation.get("state_version", 0)),
    }
    condition = (
        "batch_job_id = :batch AND #status IN (:queued, :running) "
        "AND #status = :expected_status AND state_version = :version"
    )
    update_expression = (
        "SET #status = :terminal, execution_state = :execution, "
        "outcome = :outcome, updated_at = :updated"
    )
    if error:
        values[":error"] = str(error)[:1000]
        update_expression += ", last_error = :error"

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


def save_prompt_reference(batch: dict, lease_id: str, text: str) -> None:
    key = f"prompt-references/{batch['owner_id']}/{batch['batch_job_id']}/prompt.txt"
    body = text.encode("utf-8")
    try:
        _get_s3().put_object(
            Bucket=config.AI_BATCH_EXPORT_BUCKET, Key=key, Body=body,
            ContentType="text/plain; charset=utf-8", IfNoneMatch="*",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "PreconditionFailed":
            raise
        # A retry must retain the first reference, even after a deployment.
        body = _get_s3().get_object(
            Bucket=config.AI_BATCH_EXPORT_BUCKET, Key=key,
        )["Body"].read()
    _get_batch_table().update_item(
        Key={"batch_job_id": batch["batch_job_id"]},
        UpdateExpression="SET prompt_reference_key = :key, prompt_reference_sha256 = :hash",
        ConditionExpression="provision_lease_id = :lease",
        ExpressionAttributeValues={
            ":key": key, ":hash": hashlib.sha256(body).hexdigest(), ":lease": lease_id,
        },
    )


def read_prompt_reference(batch: dict) -> bytes:
    key = batch.get("prompt_reference_key")
    if not key:
        return (
            "Prompt reference unavailable: this batch has no initialization-time reference.\n"
            "Current templates have not been substituted for historical instructions.\n"
        ).encode("utf-8")
    body = _get_s3().get_object(
        Bucket=config.AI_BATCH_EXPORT_BUCKET, Key=key,
    )["Body"].read()
    if hashlib.sha256(body).hexdigest() != batch.get("prompt_reference_sha256"):
        raise ValueError("prompt reference integrity check failed")
    return body


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
