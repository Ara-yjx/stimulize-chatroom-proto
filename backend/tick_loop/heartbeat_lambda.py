"""Scheduled heartbeat Lambda for beta.

Runs an in-process 5-second heartbeat loop for up to ``HEARTBEAT_WINDOW_SEC``
seconds, querying active conversations and async-invoking the tick handler for
each. A separate EventBridge schedule starts a fresh invocation periodically.
"""

from __future__ import annotations

import json
import logging
import os
import time

import boto3

INTERVAL_SEC = int(os.environ.get("HEARTBEAT_INTERVAL_SEC", "5"))
WINDOW_SEC = int(os.environ.get("HEARTBEAT_WINDOW_SEC", "840"))
LAMBDA_NAME = os.environ.get("TICK_HANDLER_LAMBDA", "")
TABLE_NAME = os.environ.get("CONVERSATION_TABLE", "chatroom-conversations")
INDEX_NAME = os.environ.get("CONVERSATION_STATUS_INDEX", "status-index")
MAX_FAILURES = int(os.environ.get("HEARTBEAT_MAX_FAILURES", "3"))

logger = logging.getLogger(__name__)


def handler(event, context):
    if not LAMBDA_NAME:
        raise RuntimeError("TICK_HANDLER_LAMBDA env var is required")

    ddb = boto3.client("dynamodb")
    lam = boto3.client("lambda")
    fail_count = 0
    loop_count = 0
    invoked = 0
    deadline = time.time() + WINDOW_SEC

    if context is not None:
        deadline = min(deadline, time.time() + max(0, context.get_remaining_time_in_millis() - 5000) / 1000.0)

    logger.info(
        "heartbeat lambda starting: interval=%ss window=%ss lambda=%s",
        INTERVAL_SEC,
        WINDOW_SEC,
        LAMBDA_NAME,
    )

    while time.time() < deadline:
        loop_count += 1
        try:
            resp = ddb.query(
                TableName=TABLE_NAME,
                IndexName=INDEX_NAME,
                KeyConditionExpression="#s = :s",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": {"S": "active"}},
            )
            items = resp.get("Items", [])
            fail_count = 0
        except Exception as exc:
            fail_count += 1
            logger.warning(
                "heartbeat ddb query failed (%d/%d): %s",
                fail_count,
                MAX_FAILURES,
                exc,
            )
            if fail_count >= MAX_FAILURES:
                raise
            time.sleep(INTERVAL_SEC)
            continue

        for item in items:
            cid = item.get("conversation_id", {}).get("S")
            if not cid:
                continue
            try:
                lam.invoke(
                    FunctionName=LAMBDA_NAME,
                    InvocationType="Event",
                    Payload=json.dumps({"conversation_id": cid}).encode(),
                )
                invoked += 1
            except Exception as exc:
                logger.warning("heartbeat lambda invoke failed for %s: %s", cid, exc)

        time.sleep(INTERVAL_SEC)

    return {
        "ok": True,
        "loops": loop_count,
        "invocations": invoked,
        "window_sec": WINDOW_SEC,
    }
