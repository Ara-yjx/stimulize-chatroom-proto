"""SQS handler that creates AI-only conversations and fans out work."""

from __future__ import annotations

import logging
import random
from decimal import Decimal
from uuid import uuid4

from chatroom_api import config
from chatroom_api.ai_batch import store
from chatroom_api.ai_batch.contracts import (
    BATCH_TIMEOUT_SECONDS,
    PROVISION_LEASE_MS,
    ai_participant_id,
    conversation_id,
    parse_queue_body,
    turn_write_id,
)
from chatroom_api.ai_participants import build_ai_participants
from chatroom_api.ai_batch.prompt_reference import render_prompt_reference
from chatroom_api.settings import normalize_persona_entries


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _provision_batch(batch_job_id: str, receive_count: int = 1) -> dict:
    if not config.AI_BATCH_ENABLED:
        raise RuntimeError("AI batch runtime is disabled")
    batch = store.get_batch(batch_job_id)
    if batch is None:
        raise ValueError("batch not found")
    if batch.get("status") not in {"queued", "provisioning"}:
        return {"status": "noop", "batch_job_id": batch_job_id}

    lease_id = uuid4().hex
    now_value_ms = store.now_ms()
    if not store.acquire_provision_lease(
        batch_job_id,
        lease_id,
        now_value_ms,
        PROVISION_LEASE_MS,
    ):
        # Keep the delivery available until the active lease expires. Otherwise
        # a crashed invocation could leave the batch stuck in provisioning.
        raise RuntimeError("provision lease is currently held")

    try:
        setting = dict(batch["settings_snapshot"])
        ai_count = int(setting["ai_count"])
        batch_count = int(batch["batch_count"])
        for field, value, maximum in (
            ('batch_count', batch['batch_count'], 50),
            ('max_turns', setting.get('max_turns', 100), 1000),
            ('max_total_chars', setting.get('max_total_chars', 20000), 500000),
        ):
            if (isinstance(value, bool) or not isinstance(value, (int, Decimal))
                    or not 1 <= value <= maximum or value != int(value)):
                error = f'{field} must be an integer between 1 and {maximum}'
                store.fail_validation(batch_job_id, lease_id, error, batch_count)
                return {'status': 'validation_failed', 'batch_job_id': batch_job_id, 'error': error}
        persona_count = len(normalize_persona_entries(setting.get('ai_personas') or [],
            default_model_id=setting.get('model_id', ''), default_temperature=setting.get('temperature')))
        if 0 < persona_count < ai_count:
            error = f'AI-only runs require no personas or at least {ai_count} personas; found {persona_count}.'
            store.fail_validation(batch_job_id, lease_id, error, batch_count)
            return {'status': 'validation_failed', 'batch_job_id': batch_job_id, 'error': error}
        store.save_prompt_reference(batch, lease_id, render_prompt_reference(batch))
        for index in range(batch_count):
            conv_id = conversation_id(batch_job_id, index)
            participants = build_ai_participants(
                setting,
                ai_count,
                ai_id_factory=lambda ai_index, conv=conv_id: ai_participant_id(
                    conv, ai_index
                ),
                rng=random.Random(conv_id),
                sequential_nicknames=True,
                include_avatars=False,
            )
            created_at = str(batch["created_at"])
            created_at_ms = int(batch["deadline_at"]) - BATCH_TIMEOUT_SECONDS * 1000
            store.create_conversation(
                {
                    "conversation_id": conv_id,
                    "conversation_type": "ai_batch",
                    "batch_job_id": batch_job_id,
                    "batch_index": index,
                    "owner_id": str(batch["owner_id"]),
                    "chatroom_id": batch["chatroom_id"],
                    "participants": participants,
                    "status": "queued",
                    "execution_state": "pending",
                    "outcome": None,
                    "deadline_at": int(batch["deadline_at"]),
                    "state_version": 0,
                    "next_turn": 0,
                    "message_count": 0,
                    "total_chars": 0,
                    "created_at": created_at,
                    "updated_at": created_at,
                    "started_at": created_at,
                },
                [{
                    "type": "system",
                    "subtype": "conversation_started",
                    "sender": "System",
                    "role": "system",
                    "content": "Conversation started",
                    "timestamp": created_at_ms,
                    "created_at": created_at,
                    "batch_job_id": batch_job_id,
                }],
                turn_write_id(conv_id, -1),
            )
            store.start_execution(batch_job_id, conv_id, int(batch["deadline_at"]))
        if not store.finish_provision(batch_job_id, lease_id):
            raise RuntimeError("provision lease was lost before completion")
    except Exception as exc:
        store.record_batch_error(batch_job_id, str(exc))
        if receive_count >= 5:
            store.fail_provision(batch_job_id, lease_id, str(exc))
        else:
            store.release_provision_lease(batch_job_id, lease_id)
        raise
    return {
        "status": "running",
        "batch_job_id": batch_job_id,
        "conversation_count": int(batch["batch_count"]),
    }


def lambda_handler(event: dict, context=None) -> dict:
    failures = []
    results = []
    for record in event.get("Records", []):
        message_id = record.get("messageId", "")
        try:
            body = parse_queue_body(record.get("body", ""), ("batch_job_id",))
            receive_count = int(
                (record.get("attributes") or {}).get("ApproximateReceiveCount", "1")
            )
            results.append(_provision_batch(body["batch_job_id"], receive_count))
        except Exception:
            logger.exception("AI batch provisioning failed")
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures, "results": results}
