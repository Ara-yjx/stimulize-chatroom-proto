"""POST /auth/token handler — validate chatroom via RDS, return JWT + session info."""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from uuid import uuid4

from chatroom_api import config, jwt_utils
from chatroom_api.constants import EMOJI_POOL
from chatroom_api.errors import ChatroomNotFoundException, InactiveChatroomException


def _get_rds():
    """Return the appropriate RDS module based on config."""
    if config.USE_MOCK_RDS:
        from chatroom_api import mock_rds
        return mock_rds
    from chatroom_api import rds
    return rds


def _get_db():
    """Return the appropriate DynamoDB module based on config."""
    if config.USE_MOCK_DYNAMO:
        from chatroom_api import mock_dynamo
        return mock_dynamo
    from chatroom_api import dynamo
    return dynamo


def _generate_nickname(exclude=None) -> str:
    """Generate 'Participant' + 4-digit random, avoiding collisions."""
    exclude = exclude or set()
    while True:
        name = f"Participant{random.randint(1000, 9999)}"
        if name not in exclude:
            return name


def _pick_avatar(exclude=None) -> dict:
    """Pick a random emoji avatar, avoiding collisions when possible."""
    exclude = exclude or set()
    available = [e for e in EMOJI_POOL if e not in exclude]
    if not available:
        available = list(EMOJI_POOL)
    emoji = random.choice(available)
    return {"emojiText": emoji}


def handle_auth_token(body: dict) -> tuple[int, dict]:
    """Exchange a chatroom_id for a signed JWT + session info.

    Request body: { "chatroom_id": "scid_..." }

    Returns (status_code, response_body).
    """
    chatroom_id = body.get("chatroom_id", "")
    if not chatroom_id:
        return (400, {"error": "chatroom_id is required"})

    rds_mod = _get_rds()

    # 1. Read chatroom from RDS
    chatroom = rds_mod.get_chatroom(chatroom_id)
    if chatroom is None:
        return (404, {"error": "chatroom not found"})

    # 2. Check status
    if chatroom.get("status") != "active":
        return (401, {"error": "chatroom is inactive"})

    chatroom_setting = chatroom["setting"]

    # 3. Generate session_id and conversation_id
    session_id = str(uuid4())
    conversation_id = str(uuid4())

    # 4. Auto-generate human nickname + avatar
    human_nickname = _generate_nickname()
    human_avatar = _pick_avatar()

    # 5. Auto-generate AI nickname + avatar (no collision with human)
    ai_id = f"ai_{uuid4().hex[:8]}"
    ai_nickname = _generate_nickname(exclude={human_nickname})
    ai_avatar = _pick_avatar(exclude={human_avatar["emojiText"]})

    # 6. Build participants list
    participants = [
        {
            "session_id": session_id,
            "nickname": human_nickname,
            "avatar": human_avatar,
            "role": "human",
        },
        {
            "session_id": ai_id,
            "nickname": ai_nickname,
            "avatar": ai_avatar,
            "role": "ai",
        },
    ]

    # 7. Store conversation in DynamoDB with chatroom_setting snapshot + participants + join events
    _store_conversation(
        session_id, conversation_id, chatroom_id,
        chatroom_setting, participants, human_nickname, ai_id, ai_nickname,
    )

    # 8. Sign JWT
    token = jwt_utils.create_token(session_id, conversation_id, chatroom_id)

    # 9. Return session info
    return (200, {
        "token": token,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "nickname": human_nickname,
        "avatar": human_avatar,
        "chatroom_setting": chatroom_setting,
    })


def _store_conversation(
    session_id: str,
    conversation_id: str,
    chatroom_id: str,
    chatroom_setting: dict,
    participants: list[dict],
    human_nickname: str,
    ai_id: str,
    ai_nickname: str,
) -> None:
    """Create conversation in DynamoDB with join events."""
    db = _get_db()

    now_ms = int(time.time() * 1000)
    now_iso = datetime.now(timezone.utc).isoformat()

    events = [
        {
            "type": "system",
            "session_id": session_id,
            "sender": "System",
            "role": "system",
            "ai_participant_id": None,
            "content": f"{human_nickname} joined the chatroom",
            "timestamp": now_ms,
            "created_at": now_iso,
        },
        {
            "type": "system",
            "session_id": session_id,
            "sender": "System",
            "role": "system",
            "ai_participant_id": ai_id,
            "content": f"{ai_nickname} joined the chatroom",
            "timestamp": now_ms + 1,
            "created_at": now_iso,
        },
    ]

    db.append_events(
        conversation_id, chatroom_id, events,
        chatroom_setting=chatroom_setting,
        participants=participants,
    )
