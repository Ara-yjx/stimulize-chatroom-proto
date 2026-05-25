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
# Usage records: one row per billable model invocation. Kept flat so the
# usage endpoints can filter and aggregate by time period.
# ---------------------------------------------------------------------------

USAGE: list[dict] = []


def accumulate_usage(
    *,
    owner_id: str = "owner_default",
    chatroom_id: str,
    conversation_id: str | None = None,
    session_id: str | None = None,
    provider: str = "bedrock",
    model_id: str = "global.anthropic.claude-sonnet-4-6",
    pricing_key: str = "bedrock_claude_sonnet_4_6_global_standard",
    input_tokens: int,
    output_tokens: int,
    estimated_cost_usd: float = 0.0,
    created_at: str | None = None,
) -> None:
    """Append a usage record for a chatroom."""
    USAGE.append({
        "usage_event_id": f"usage_{uuid.uuid4()}",
        "owner_id": owner_id,
        "chatroom_id": chatroom_id,
        "conversation_id": conversation_id,
        "session_id": session_id,
        "provider": provider,
        "model_id": model_id,
        "pricing_key": pricing_key,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "invoked_at": created_at or datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def generate_chatroom_id() -> str:
    """Generate a chatroom ID: scid_ + uuid4."""
    return f"scid_{uuid.uuid4()}"
