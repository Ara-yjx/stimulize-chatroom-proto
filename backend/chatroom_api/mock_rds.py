"""In-memory mock of the RDS chatroom + usage tables.

Provides the same interface as rds.py so the rest of the codebase can
swap implementations via the USE_MOCK_RDS env var.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

# --- Seed data ---

_chatrooms = {
    "scid_test-chatroom-001": {
        "id": "scid_test-chatroom-001",
        "owner_id": "user_001",
        "name": "College Chat",
        "status": "active",
        "setting": {
            "mode": "one_on_one",
            "topic_instruction": "Anything about your college life.",
            "model_id": "global.anthropic.claude-sonnet-4-6",
            "simulate_pairing_seconds": 3,
            "timer_min_minutes": 5,
            "timer_max_minutes": 10,
        },
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
    },
}

_usage_records: list[dict] = []


def get_chatroom(chatroom_id: str) -> Optional[dict]:
    """Return a chatroom dict by ID, or None if not found."""
    return _chatrooms.get(chatroom_id)


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
