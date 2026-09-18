"""In-memory mock of the RDS chatroom + usage tables.

Provides the same interface as rds.py so the rest of the codebase can
swap implementations via the USE_MOCK_RDS env var.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
import os
import json


def _fixture_rooms():
    """Optional server-written fixtures for an isolated deployed integration test."""
    key = os.environ.get('DEV_CHATROOM_FIXTURES_KEY', '')
    if not key:
        return None
    from chatroom_api import config
    if not config.USE_MOCK_RDS or not config.PROMPT_ATTACHMENT_BUCKET.startswith('stimulize-attachment-dev-') or key != 'fixtures/rooms.json':
        raise RuntimeError('Refusing non-isolated fixture configuration')
    import boto3
    body = boto3.client('s3').get_object(Bucket=config.PROMPT_ATTACHMENT_BUCKET, Key=key)['Body']
    try:
        raw = body.read(1000001)
    finally:
        body.close()
    if len(raw) > 1000000:
        raise ValueError('Fixture manifest too large')
    return json.loads(raw)

# --- Seed data ---

_chatrooms = {
    "scid_test-chatroom-001": {
        "id": "scid_test-chatroom-001",
        "owner_id": "user_001",
        "name": "College Chat",
        "status": "active",
        "setting": {
            "topic_instruction": "Anything about your college life.",
            "model_id": "global.anthropic.claude-sonnet-4-6",
            "mimic_human": False,
            "simulate_pairing_seconds": 3,
            "timer_min_minutes": None,
            "timer_max_minutes": 0.5,
            "max_duration_seconds": 45,
            "human_count": 1,
            "ai_count": 1,
            "replace_human_with_ai": False,
        },
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
    },
}

_usage_records: list[dict] = []


def get_chatroom(chatroom_id: str) -> Optional[dict]:
    """Return a chatroom dict by ID, or None if not found."""
    rooms = _fixture_rooms()
    return (rooms if rooms is not None else _chatrooms).get(chatroom_id)


def resolve_prompt_assets(chatroom):
    rooms = _fixture_rooms()
    if rooms is None:
        raise RuntimeError('Mock attachment fixtures are not configured')
    return rooms[chatroom['id']]['setting']['_prompt_asset_manifest']


def write_usage(
    *,
    usage_event_id: str,
    owner_id: int | str,
    chatroom_id: str,
    conversation_id: str,
    session_id: str,
    provider: str,
    model_id: str,
    pricing_key: str,
    input_tokens: int,
    output_tokens: int,
    estimated_cost_usd,
    invoked_at: datetime | None = None,
    raw_usage_json: dict | None = None,
) -> None:
    """Append a usage record to the in-memory store."""
    _usage_records.append({
        "usage_event_id": usage_event_id,
        "owner_id": owner_id,
        "chatroom_id": chatroom_id,
        "conversation_id": conversation_id,
        "session_id": session_id,
        "provider": provider,
        "model_id": model_id,
        "pricing_key": pricing_key,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": float(estimated_cost_usd),
        "currency": "USD",
        "invoked_at": (invoked_at or datetime.now(timezone.utc)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "raw_usage_json": raw_usage_json,
    })
