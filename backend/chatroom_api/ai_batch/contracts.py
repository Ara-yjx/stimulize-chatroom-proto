"""Stable IDs, lifecycle values, and queue envelopes for AI batches."""

from __future__ import annotations

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5


CONTRACT_VERSION = 1
BATCH_TIMEOUT_SECONDS = 24 * 60 * 60
WORKER_SLICE_SECONDS = 240
WORKER_CALL_HEADROOM_MS = 120_000
PROVISION_LEASE_MS = 11 * 60 * 1000
EXPORT_LEASE_MS = 16 * 60 * 1000

BATCH_EXECUTABLE_STATUSES = frozenset({"provisioning", "running"})
BATCH_TERMINAL_STATUSES = frozenset({
    "completed", "partial_failure", "failed", "timed_out", "validation_failed",
})
CONVERSATION_TERMINAL_STATUSES = frozenset({
    "completed", "failed", "timed_out",
})


def canonical_request_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def batch_job_id(owner_id: str | int, client_request_id: str) -> str:
    value = uuid5(
        NAMESPACE_URL,
        f"stimulize:ai-batch:{owner_id}:{client_request_id}",
    ).hex
    return f"aib_{value}"


def conversation_id(batch_id: str, batch_index: int) -> str:
    value = uuid5(
        NAMESPACE_URL,
        f"stimulize:ai-batch-conversation:{batch_id}:{batch_index}",
    ).hex
    return f"aic_{value}"


def ai_participant_id(conversation_id_value: str, index: int) -> str:
    value = uuid5(
        NAMESPACE_URL,
        f"stimulize:ai-batch-participant:{conversation_id_value}:{index}",
    ).hex
    return f"ai_{value[:16]}"


def turn_write_id(conversation_id_value: str, expected_turn: int) -> str:
    return uuid5(
        NAMESPACE_URL,
        f"stimulize:ai-batch-turn:{conversation_id_value}:{expected_turn}",
    ).hex


def export_job_id(batch_id: str, generation: int) -> str:
    return uuid5(
        NAMESPACE_URL,
        f"stimulize:ai-batch-export:{batch_id}:{generation}",
    ).hex


def provision_message(batch_id: str) -> dict:
    return {"version": CONTRACT_VERSION, "batch_job_id": batch_id}


def export_message(batch_id: str, job_id: str) -> dict:
    return {
        "version": CONTRACT_VERSION,
        "batch_job_id": batch_id,
        "export_job_id": job_id,
    }


def parse_queue_body(raw: str, required_fields: tuple[str, ...]) -> dict:
    try:
        body = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("queue body must be valid JSON") from exc
    if not isinstance(body, dict) or body.get("version") != CONTRACT_VERSION:
        raise ValueError("unsupported queue contract version")
    missing = [field for field in required_fields if field not in body]
    if missing:
        raise ValueError(f"queue body missing: {', '.join(missing)}")
    return body


def terminal_batch_status(counts: dict) -> str:
    completed = int(counts.get("completed_count", 0) or 0)
    failed = int(counts.get("failed_count", 0) or 0)
    timed_out = int(counts.get("timed_out_count", 0) or 0)
    if completed > 0 and failed == 0 and timed_out == 0:
        return "completed"
    if completed == 0:
        return "timed_out" if timed_out > 0 and failed == 0 else "failed"
    return "partial_failure"
