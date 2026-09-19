"""Build downloadable archives for terminal AI conversation batches."""

from __future__ import annotations

import io
import json
import logging
import zipfile
from decimal import Decimal
from uuid import uuid4

from chatroom_api import config
from chatroom_api.ai_batch import store
from chatroom_api.ai_batch.contracts import (
    BATCH_TERMINAL_STATUSES,
    EXPORT_LEASE_MS,
    conversation_id,
    parse_queue_body,
)


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _render_text(events: list[dict]) -> str:
    lines = []
    for event in events:
        if event.get("type") == "message":
            sender = event.get("sender") or "Participant"
            internal_name = event.get("internal_name")
            label = f"{sender} ({internal_name})" if internal_name else sender
        else:
            label = "System"
        lines.append(f"{label}: {event.get('content', '')}")
    return "\n".join(lines) + ("\n" if lines else "")


def build_export_archive(batch: dict) -> tuple[bytes, dict]:
    buffer = io.BytesIO()
    included = []
    omitted = []
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        reference = store.read_prompt_reference(batch)
        # Preserve old immutable references as TXT; new references are Markdown.
        extension = 'md' if reference.startswith(b'# AI Conversation Prompt Reference') else 'txt'
        archive.writestr(f"info/prompt.{extension}", reference)
        for index in range(int(batch["batch_count"])):
            conv_id = conversation_id(batch["batch_job_id"], index)
            conversation = store.get_conversation(conv_id)
            if not conversation or conversation.get("status") != "completed":
                omitted.append({
                    "batch_index": index,
                    "status": (conversation or {}).get("status", "missing"),
                })
                continue
            # Legacy batch records may contain avatars; omit them without rewriting history.
            events = [{k: v for k, v in event.items() if k != "avatar"}
                      for event in store.query_history(conv_id)]
            prefix = f"conversations/{index + 1:04d}"
            archive.writestr(
                f"{prefix}.json",
                json.dumps(
                    {
                        "batch_index": index,
                        "conversation_id": conv_id,
                        "participants": [{k: v for k, v in participant.items() if k != "avatar"}
                                         for participant in conversation.get("participants", [])],
                        "events": events,
                    },
                    default=_json_default,
                    indent=2,
                    ensure_ascii=False,
                ),
            )
            archive.writestr(f"{prefix}.txt", _render_text(events))
            included.append(index)
        manifest = {
            "version": 1,
            "prompt_reference_sha256": batch.get("prompt_reference_sha256"),
            "batch_job_id": batch["batch_job_id"],
            "chatroom_id": batch["chatroom_id"],
            "status": batch["status"],
            "batch_count": int(batch["batch_count"]),
            "included_count": len(included),
            "omitted_count": len(omitted),
            "included_batch_indexes": included,
            "omitted": omitted,
            "outcome_counts": {
                key: int(batch.get(key, 0) or 0)
                for key in (
                    "completed_count",
                    "failed_count",
                    "timed_out_count",
                    "unfinished_count",
                )
            },
        }
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, indent=2, ensure_ascii=False),
        )
    return buffer.getvalue(), manifest


def _export_batch(batch_job_id: str, export_job_id: str) -> dict:
    if not config.AI_BATCH_ENABLED:
        raise RuntimeError("AI batch runtime is disabled")
    batch = store.get_batch(batch_job_id)
    if batch is None:
        raise ValueError("batch not found")
    if batch.get("status") not in BATCH_TERMINAL_STATUSES:
        raise ValueError("batch is not terminal")
    if batch.get("export_job_id") != export_job_id:
        return {"status": "noop", "reason": "stale_export_job"}
    if batch.get("export_status") == "ready" and batch.get("export_s3_key"):
        if store.export_object_exists(batch["export_s3_key"]):
            return {"status": "ready", "object_key": batch["export_s3_key"]}

    lease_id = uuid4().hex
    if not store.acquire_export_lease(
        batch_job_id,
        export_job_id,
        lease_id,
        store.now_ms(),
        EXPORT_LEASE_MS,
    ):
        return {"status": "leased"}
    try:
        archive, manifest = build_export_archive(batch)
        object_key = (
            f"owners/{batch['owner_id']}/batches/{batch_job_id}/"
            f"{export_job_id}.zip"
        )
        store.put_export_object(object_key, archive)
        if not store.finish_export(
            batch_job_id, export_job_id, lease_id, object_key
        ):
            raise RuntimeError("export lease was lost before completion")
    except Exception as exc:
        store.fail_export(batch_job_id, export_job_id, lease_id, str(exc))
        raise
    return {
        "status": "ready",
        "object_key": object_key,
        "included_count": manifest["included_count"],
    }


def lambda_handler(event: dict, context=None) -> dict:
    failures = []
    results = []
    for record in event.get("Records", []):
        message_id = record.get("messageId", "")
        try:
            body = parse_queue_body(
                record.get("body", ""),
                ("batch_job_id", "export_job_id"),
            )
            results.append(_export_batch(
                body["batch_job_id"], body["export_job_id"]
            ))
        except Exception:
            logger.exception("AI batch export failed")
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures, "results": results}
