"""POST /chat/send and GET /chat/messages handlers."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from chatroom_api import bedrock_client, config, conversation

logger = logging.getLogger(__name__)


def _get_db():
    """Return the appropriate DynamoDB module based on config."""
    if config.USE_MOCK_DYNAMO:
        from chatroom_api import mock_dynamo
        return mock_dynamo
    from chatroom_api import dynamo
    return dynamo


def _get_rds():
    """Return the appropriate RDS module based on config."""
    if config.USE_MOCK_RDS:
        from chatroom_api import mock_rds
        return mock_rds
    from chatroom_api import rds
    return rds


def handle_chat_send(body: dict, claims: dict) -> tuple[int, dict]:
    """Process a user message and return AI replies."""
    message = body.get("message", "")
    session_id = claims["session_id"]
    conversation_id = claims["conversation_id"]
    chatroom_id = claims["chatroom_id"]

    db = _get_db()

    # 1. Read conversation history (only message events for Bedrock)
    all_events = db.get_events(conversation_id)
    message_events = [e for e in all_events if e.get("type", "message") == "message"]

    # 2. Read chatroom_setting from DynamoDB conversation (stored at auth time)
    chatroom_setting = db.get_conversation_config(conversation_id)
    if chatroom_setting is None:
        return (500, {"error": "conversation config not found"})

    # 3. Read participants to find AI participant info
    participants = db.get_participants(conversation_id)
    if participants is None:
        return (500, {"error": "participants not found"})

    ai_participants = [p for p in participants if p["role"] == "ai"]
    human_participant = next((p for p in participants if p["role"] == "human"), None)

    # Use nickname from the human participant stored in DynamoDB
    nickname = human_participant["nickname"] if human_participant else "Unknown"

    # 4. Build user message event
    now_ms = int(time.time() * 1000)
    now_iso = datetime.now(timezone.utc).isoformat()

    user_event = {
        "type": "message",
        "session_id": session_id,
        "sender": nickname,
        "role": "user",
        "ai_participant_id": None,
        "content": message,
        "timestamp": now_ms,
        "created_at": now_iso,
    }

    # 5. Call Bedrock for each AI participant
    new_events = [user_event]
    replies = []
    total_input_tokens = 0
    total_output_tokens = 0
    has_error = False

    system_prompt = chatroom_setting.get("system_prompt", "")
    model_id = chatroom_setting.get("model_id", "global.anthropic.claude-sonnet-4-6")

    for ai in ai_participants:
        bedrock_messages = conversation.build_bedrock_messages(
            message_events + [user_event], ai["session_id"]
        )
        try:
            result = bedrock_client.invoke(model_id, system_prompt, bedrock_messages)
        except bedrock_client.BedrockInferenceError as e:
            error_event = {
                "type": "error",
                "session_id": session_id,
                "sender": "System",
                "role": "system",
                "ai_participant_id": ai["session_id"],
                "content": f"Chatroom server error: {e.error_type}",
                "timestamp": int(time.time() * 1000),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            new_events.append(error_event)
            has_error = True
            continue

        ai_event = {
            "type": "message",
            "session_id": session_id,
            "sender": ai["nickname"],
            "role": "ai",
            "ai_participant_id": ai["session_id"],
            "content": result["text"],
            "timestamp": int(time.time() * 1000),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        new_events.append(ai_event)
        replies.append({
            "nickname": ai["nickname"],
            "avatar": ai.get("avatar"),
            "content": result["text"],
        })
        total_input_tokens += result["input_tokens"]
        total_output_tokens += result["output_tokens"]

    # 6. Append all events to DynamoDB
    db.append_events(conversation_id, chatroom_id, new_events)

    # 7. Write usage to RDS
    if total_input_tokens > 0 or total_output_tokens > 0:
        try:
            rds_mod = _get_rds()
            rds_mod.write_usage(
                chatroom_id=chatroom_id,
                conversation_id=conversation_id,
                session_id=session_id,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
            )
        except Exception:
            logger.warning("Failed to write usage to RDS", exc_info=True)

    return (200, {"replies": replies, "error": has_error})


def handle_chat_messages(query_params: dict, claims: dict) -> tuple[int, dict]:
    """Return events for the room after a given timestamp."""
    after = int(query_params.get("after", 0))
    conversation_id = claims["conversation_id"]

    db = _get_db()
    events = db.get_events(conversation_id, after=after)

    # Read participants for avatar lookup
    participants = db.get_participants(conversation_id) or []
    avatar_map = {p["nickname"]: p.get("avatar") for p in participants}

    filtered = [
        {
            "type": e.get("type", "message"),
            "sender": e["sender"],
            "role": e["role"],
            "content": e["content"],
            "timestamp": e["timestamp"],
            "session_id": e.get("session_id"),
            "avatar": avatar_map.get(e["sender"]),
        }
        for e in events
    ]

    return (200, {"events": filtered})
