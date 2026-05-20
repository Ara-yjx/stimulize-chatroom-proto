"""Heartbeat container for the tick model.

Loops every ``HEARTBEAT_INTERVAL_SEC`` seconds, queries the
``chatroom-conversations`` ``status-index`` for ``status="active"`` rows,
and async-invokes the tick handler Lambda once per active conversation.

Exits with code 1 after ``HEARTBEAT_MAX_FAILURES`` consecutive DDB query
failures so ECS restarts the task. See ``docs/low-level-design.md``
"Heartbeat container".
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

import boto3

INTERVAL_SEC = int(os.environ.get("HEARTBEAT_INTERVAL_SEC", "5"))
LAMBDA_NAME = os.environ.get("TICK_HANDLER_LAMBDA", "")
TABLE_NAME = os.environ.get("CONVERSATION_TABLE", "chatroom-conversations")
INDEX_NAME = os.environ.get("CONVERSATION_STATUS_INDEX", "status-index")
MAX_FAILURES = int(os.environ.get("HEARTBEAT_MAX_FAILURES", "3"))

logger = logging.getLogger(__name__)


def main() -> None:
    if not LAMBDA_NAME:
        logger.error("TICK_HANDLER_LAMBDA env var is required")
        sys.exit(2)

    ddb = boto3.client("dynamodb")
    lam = boto3.client("lambda")
    fail_count = 0

    logger.info(
        "heartbeat starting: interval=%ds lambda=%s",
        INTERVAL_SEC,
        LAMBDA_NAME,
    )

    while True:
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
                logger.error(
                    "heartbeat exiting after %d consecutive failures",
                    MAX_FAILURES,
                )
                sys.exit(1)
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
            except Exception as exc:
                logger.warning(
                    "heartbeat lambda invoke failed for %s: %s", cid, exc
                )
                # don't increment fail_count on per-invoke errors

        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
