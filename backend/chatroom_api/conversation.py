"""Multi-participant conversation → Bedrock message mapping."""

from __future__ import annotations


def build_bedrock_messages(
    room_messages: list[dict], ai_participant_id: str
) -> list[dict]:
    """Convert DynamoDB messages into Bedrock user/assistant format for a
    specific AI participant.

    Returns a list of ``{"role": "user"|"assistant", "content": [{"text": "..."}]}``
    dicts ready for the Bedrock Converse API.
    """
    bedrock_msgs: list[dict] = []

    for msg in room_messages:
        # Skip system events — they're not part of the conversation
        if msg.get("type") == "system" or msg.get("role") == "system":
            continue

        if msg["role"] == "ai" and msg.get("ai_participant_id") == ai_participant_id:
            role = "assistant"
            text = msg["content"]
        else:
            role = "user"
            text = f"[{msg['sender']}] {msg['content']}"

        # Merge consecutive same-role messages
        if bedrock_msgs and bedrock_msgs[-1]["role"] == role:
            bedrock_msgs[-1]["content"][0]["text"] += f"\n{text}"
        else:
            bedrock_msgs.append({"role": role, "content": [{"text": text}]})

    return bedrock_msgs
