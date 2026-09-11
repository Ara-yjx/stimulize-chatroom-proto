"""Provider-ready prompt construction shared by live and batch inference."""

from __future__ import annotations

from chatroom_api.prompts.speech_scaffold import (
    format_topic_block,
    get_scaffold_for_mode,
)
from chatroom_api.settings import is_single_human_single_ai_assistant_room


BEDROCK_PROMPT_CACHE_MODEL_IDS = frozenset({
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "anthropic.claude-3-7-sonnet-20250219-v1:0",
    "anthropic.claude-opus-4-20250514-v1:0",
    "anthropic.claude-opus-4-5-20251101-v1:0",
    "anthropic.claude-opus-4-6-v1",
    "anthropic.claude-opus-4-7",
    "anthropic.claude-sonnet-4-20250514-v1:0",
    "anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-sonnet-4-6",
    "anthropic.claude-haiku-4-5-20251001-v1:0",
    "amazon.nova-pro-v1:0",
    "amazon.nova-lite-v1:0",
    "amazon.nova-micro-v1:0",
    "amazon.nova-premier-v1:0",
    "amazon.nova-2-lite-v1:0",
})

BEDROCK_INFERENCE_PROFILE_PREFIXES = frozenset({
    "global", "us", "eu", "apac", "jp", "au",
})


def build_static_prefix_block(
    mode: str,
    *,
    mimic_human: bool = True,
    require_response: bool = False,
) -> str:
    return get_scaffold_for_mode(
        mode,
        mimic_human=mimic_human,
        require_response=require_response,
    )


def build_semi_static_setup_blocks(
    chatroom_setting: dict,
    persona: str,
    my_nickname: str,
    participant_nicknames: list[str] | None = None,
) -> list[str]:
    parts: list[str] = []
    topic = format_topic_block(chatroom_setting.get("topic_instruction", ""))
    if topic:
        parts.append(topic)
    if persona:
        parts.append(f"<your-persona>\n{persona}\n</your-persona>")
    if participant_nicknames:
        listed = sorted(set(participant_nicknames))
        rendered = "\n".join(
            f"- {nickname} (you)" if nickname == my_nickname else f"- {nickname}"
            for nickname in listed
        )
        parts.append(f"<participants>\n{rendered}\n</participants>")
    if is_single_human_single_ai_assistant_room(chatroom_setting):
        parts.append(
            "<single-ai-idle-policy>\n"
            "The backend may ask you to decide whether to speak every few seconds. "
            "If your latest message has no human reply, normally stay silent for "
            "roughly 60 seconds. After that wait, send at most one brief, natural "
            "check-in that helps continue the conversation. If the check-in also "
            "gets no human reply, stay silent until the human speaks again.\n"
            "</single-ai-idle-policy>"
        )
    parts.append(f"<your-name>\n{my_nickname}\n</your-name>")
    return parts


def build_dynamic_context_block(history_block: str) -> str:
    return f"<conversation-history>\n{history_block}\n</conversation-history>"


def build_additional_prompt_block(chatroom_setting: dict) -> str:
    return (chatroom_setting.get("additional_prompt") or "").strip()


def build_prompt_blocks(
    mode: str,
    chatroom_setting: dict,
    persona: str,
    my_nickname: str,
    history_block: str,
    participant_nicknames: list[str] | None = None,
    require_response: bool = False,
) -> dict[str, str | list[str]]:
    return {
        "static_prefix": build_static_prefix_block(
            mode,
            mimic_human=bool(chatroom_setting.get("mimic_human", True)),
            require_response=require_response,
        ),
        "semi_static_setup": build_semi_static_setup_blocks(
            chatroom_setting,
            persona,
            my_nickname,
            participant_nicknames=participant_nicknames,
        ),
        "dynamic_context": build_dynamic_context_block(history_block),
        "additional_prompt": build_additional_prompt_block(chatroom_setting),
    }


def base_bedrock_model_id(model_id: str) -> str:
    normalized = (model_id or "").strip()
    prefix, separator, remainder = normalized.partition(".")
    if separator and prefix in BEDROCK_INFERENCE_PROFILE_PREFIXES:
        return remainder
    return normalized


def supports_bedrock_prompt_cache(model_id: str) -> bool:
    return base_bedrock_model_id(model_id) in BEDROCK_PROMPT_CACHE_MODEL_IDS


def build_system_prompt(
    mode: str,
    chatroom_setting: dict,
    persona: str,
    my_nickname: str,
    history_block: str,
    participant_nicknames: list[str] | None = None,
    require_response: bool = False,
) -> str:
    blocks = build_prompt_blocks(
        mode,
        chatroom_setting,
        persona,
        my_nickname,
        history_block,
        participant_nicknames=participant_nicknames,
        require_response=require_response,
    )
    parts: list[str] = [str(blocks["static_prefix"])]
    parts.extend(blocks["semi_static_setup"])
    parts.append(str(blocks["dynamic_context"]))
    additional = str(blocks["additional_prompt"])
    if additional:
        parts.append(additional)
    return "\n".join(parts)


def build_bedrock_system_blocks(
    mode: str,
    chatroom_setting: dict,
    persona: str,
    my_nickname: str,
    history_block: str,
    *,
    model_id: str,
    participant_nicknames: list[str] | None = None,
    require_response: bool = False,
) -> list[dict]:
    if not supports_bedrock_prompt_cache(model_id):
        return [{
            "text": build_system_prompt(
                mode,
                chatroom_setting,
                persona,
                my_nickname,
                history_block,
                participant_nicknames=participant_nicknames,
                require_response=require_response,
            )
        }]
    return [{
        "text": build_static_prefix_block(
            mode,
            mimic_human=bool(chatroom_setting.get("mimic_human", True)),
            require_response=require_response,
        )
    }]


def build_bedrock_cache_prefix_message(
    mode: str,
    chatroom_setting: dict,
    persona: str,
    my_nickname: str,
    history_block: str,
    *,
    completed_history_block: str | None = None,
    participant_nicknames: list[str] | None = None,
    require_response: bool = False,
) -> dict:
    blocks = build_prompt_blocks(
        mode=mode,
        chatroom_setting=chatroom_setting,
        persona=persona,
        my_nickname=my_nickname,
        history_block=history_block,
        participant_nicknames=participant_nicknames,
        require_response=require_response,
    )
    content: list[dict] = [
        {"text": block} for block in blocks["semi_static_setup"]
    ]
    additional = str(blocks["additional_prompt"])
    if additional:
        content.append({"text": additional})
    if completed_history_block is not None:
        content.append({
            "text": (
                "<completed-conversation-history>\n"
                f"{completed_history_block}\n"
                "</completed-conversation-history>"
            )
        })
    content.append({"cachePoint": {"type": "default"}})
    content.append({"text": str(blocks["dynamic_context"])})
    return {"role": "user", "content": content}


def prepend_cache_prefix_message(messages: list[dict], prefix_message: dict) -> list[dict]:
    if not messages:
        return [prefix_message]
    merged = [dict(message) for message in messages]
    if merged[0]["role"] == "user":
        merged[0] = {
            "role": "user",
            "content": list(prefix_message["content"]) + list(merged[0]["content"]),
        }
        return merged
    return [prefix_message, *merged]
