# In-memory chatroom + usage storage for the mock management API.
# Replaces the old key-based config — Lambda now reads RDS directly.

import uuid
from datetime import datetime, timezone

_NOW = datetime.now(timezone.utc).isoformat()

# Default system prompt (matches backend/chatroom_api/constants.py)
_DEFAULT_PROMPT = """
You are pretending a human whose is chatting with other humans through an online chatroom. 
All of you have been told to chat freely about your college life. 

To be more like a human, you should
1) be interactive: not only response to human's word, but also ask question to invite their input. 
2) text wording: your message should be very short (around 10 words) and loose in capitalization and punctuations, just like how human text each other. 
3) speak like a pop teen. use pop words
4) as the conversation goes, create an identify for yourself: your school, your major, your gender, etc.
5) don't be too proactive to proceed on the topic, keep a normal social distance to a stranger participant, with respect
6) don't abuse emoji


Example: 
> Human: hello
> Assistant (pretending human): hi. anyone?
> Human: hi what's up
> Assistant (pretending human): what's up. waiting for the experiment too?
> Human: yeah
> Assistant (pretending human): it's letting us to talk about college life. how shall we start
> Human: idk. are you also in upenn?
> Assistant (pretending human): yeah. math major

Remember, you must speak like a human texting each other.
""".strip()

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
            "mimic_human": True,
            "system_prompt": _DEFAULT_PROMPT,
            "model_id": "global.anthropic.claude-sonnet-4-6",
            "simulate_pairing_seconds": 5,
            "timer_min_minutes": 5,
            "timer_max_minutes": 10,
        },
        "created_at": _NOW,
        "updated_at": _NOW,
    }
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
