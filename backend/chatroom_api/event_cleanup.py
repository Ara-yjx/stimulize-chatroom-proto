"""Delete event partitions after DynamoDB TTL removes conversation metadata."""

from __future__ import annotations

import logging
import os

import boto3
from boto3.dynamodb.types import TypeDeserializer


logger = logging.getLogger(__name__)
_deserializer = TypeDeserializer()
_table = None


def _event_table():
    global _table
    if _table is None:
        table_name = os.environ["DYNAMODB_EVENT_TABLE"]
        _table = boto3.resource("dynamodb").Table(table_name)
    return _table


def _conversation_id(record: dict) -> str | None:
    if record.get("eventName") != "REMOVE":
        return None
    raw = record.get("dynamodb", {}).get("Keys", {}).get("conversation_id")
    if not raw:
        return None
    value = _deserializer.deserialize(raw)
    return str(value) if value else None


def delete_event_partition(conversation_id: str) -> int:
    table = _event_table()
    deleted = 0
    query = {
        "KeyConditionExpression": "conversation_id = :conversation_id",
        "ExpressionAttributeValues": {":conversation_id": conversation_id},
        "ProjectionExpression": "conversation_id, event_key",
        "ConsistentRead": True,
    }
    with table.batch_writer() as batch:
        while True:
            response = table.query(**query)
            for item in response.get("Items", []):
                batch.delete_item(Key={
                    "conversation_id": item["conversation_id"],
                    "event_key": item["event_key"],
                })
                deleted += 1
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            query["ExclusiveStartKey"] = last_key
    return deleted


def handler(event: dict, _context) -> dict:
    results = []
    for record in event.get("Records", []):
        conversation_id = _conversation_id(record)
        if not conversation_id:
            continue
        deleted = delete_event_partition(conversation_id)
        logger.info(
            "event partition cleanup complete",
            extra={"conversation_id": conversation_id, "deleted_events": deleted},
        )
        results.append({"conversation_id": conversation_id, "deleted_events": deleted})
    return {"cleaned": results}
