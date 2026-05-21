# In-memory chatroom + usage storage for the mock management API.
# Replaces the old key-based config — Lambda now reads RDS directly.

import uuid
from datetime import datetime, timezone

_NOW = datetime.now(timezone.utc).isoformat()

# Default topic instruction for new chatrooms. Just the topic — the speech
# scaffold (human-mimicry rules, tool-use mechanics, examples) lives in the
# backend at backend/chatroom_api/prompts/speech_scaffold.py and is wrapped
# around this string at runtime.
_DEFAULT_TOPIC = "Anything about your college life."

# ---------------------------------------------------------------------------
# Chatrooms: id → { id, owner_id, name, status, setting, created_at, updated_at }
# ---------------------------------------------------------------------------

CHATROOMS: dict[str, dict] = {
    "scid_test-chatroom-001": {
        "id": "scid_test-chatroom-001",
        "owner_id": "owner_default",
        "name": "Test Chatroom",
        "status": "active",
        "setting": {
            "mode": "one_on_one",
            "topic_instruction": _DEFAULT_TOPIC,
            "model_id": "global.anthropic.claude-sonnet-4-6",
            "simulate_pairing_seconds": 5,
            "timer_min_minutes": 5,
            "timer_max_minutes": 10,
        },
        "created_at": _NOW,
        "updated_at": _NOW,
    },
    "scid_test-group-2h2ai-001": {
        "id": "scid_test-group-2h2ai-001",
        "owner_id": "owner_default",
        "name": "Local Group 2H 2AI",
        "status": "active",
        "setting": {
            "mode": "group",
            "topic_instruction": _DEFAULT_TOPIC,
            "additional_prompt": "",
            "ai_personas": [],
            "model_id": "global.anthropic.claude-sonnet-4-6",
            "simulate_pairing_seconds": 0,
            "timer_min_minutes": 5,
            "timer_max_minutes": 10,
            "max_duration_seconds": 900,
            "target_human_count": 2,
            "ai_join_strategy": "fixed_ai_count",
            "ai_strategy_value": 2,
            "max_wait_seconds": 30,
        },
        "created_at": _NOW,
        "updated_at": _NOW,
    },
}

# ---------------------------------------------------------------------------
# Usage records: list of dicts with chatroom_id, input_tokens, output_tokens,
# created_at.  Kept as a flat list so the usage endpoint can filter by date.
# ---------------------------------------------------------------------------

USAGE: list[dict] = []


def accumulate_usage(chatroom_id: str, input_tokens: int, output_tokens: int) -> None:
    """Append a usage record for a chatroom."""
    USAGE.append({
        "chatroom_id": chatroom_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def generate_chatroom_id() -> str:
    """Generate a chatroom ID: scid_ + uuid4."""
    return f"scid_{uuid.uuid4()}"
