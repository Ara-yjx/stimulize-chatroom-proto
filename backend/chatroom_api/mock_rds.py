"""In-memory mock of the RDS chatroom + usage tables.

Provides the same interface as rds.py so the rest of the codebase can
swap implementations via the USE_MOCK_RDS env var.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from chatroom_api.constants import DEFAULT_PROMPT

# --- Seed data ---

_chatrooms = {
    "scid_test-chatroom-001": {
        "id": "scid_test-chatroom-001",
        "owner_id": "user_001",
        "name": "College Chat",
        "status": "active",
        "setting": {
            "mode": "one_on_one",
            "mimic_human": True,
            "system_prompt": DEFAULT_PROMPT,
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
    chatroom_id: str,
    conversation_id: str,
    session_id: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Append a usage record to the in-memory store."""
    _usage_records.append({
        "chatroom_id": chatroom_id,
        "conversation_id": conversation_id,
        "session_id": session_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
