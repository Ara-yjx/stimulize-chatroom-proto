"""Smoke test for the group-mode prompt + Bedrock history alternation.

Builds the per-AI system prompt + Bedrock messages array using the same
``tick_handler`` helpers a real tick uses, against a small fake conversation
that mirrors the ``experiment/group-poc.js`` flow. No real Bedrock call.
"""

from __future__ import annotations

from chatroom_api.conversation import build_bedrock_messages
from chatroom_api.tick_handler import _build_system_prompt, _render_history_block


def _conv() -> dict:
    return {
        "participants": [
            {"session_id": "h1", "nickname": "Earth", "role": "human"},
            {
                "session_id": "ai_001",
                "nickname": "Mars",
                "role": "ai",
                "persona": "upenn sophomore",
            },
            {
                "session_id": "ai_002",
                "nickname": "Venus",
                "role": "ai",
                "persona": "ucsd sophomore",
            },
        ],
        "events": [
            {
                "type": "system",
                "session_id": None,
                "sender": "System",
                "role": "system",
                "content": "Chatroom created.",
                "timestamp": 0,
                "visible_at": 0,
            },
            {
                "type": "message",
                "session_id": "h1",
                "sender": "Earth",
                "role": "human",
                "content": "hi",
                "timestamp": 1_000,
                "visible_at": 1_000,
            },
            {
                "type": "message",
                "session_id": "ai_001",
                "sender": "Mars",
                "role": "ai",
                "content": "hi yo",
                "timestamp": 5_000,
                "visible_at": 8_000,
            },
            {
                "type": "message",
                "session_id": "h1",
                "sender": "Earth",
                "role": "human",
                "content": "what's up",
                "timestamp": 12_000,
                "visible_at": 12_000,
            },
        ],
        "chatroom_setting": {
            "mode": "group",
            "topic_instruction": "Chat freely about college life.",
            "model_id": "test-model",
        },
    }


def test_system_prompt_includes_scaffold_topic_persona_and_history() -> None:
    conv = _conv()
    history = _render_history_block(conv, now_ms=20_000)
    prompt = _build_system_prompt(
        mode="group",
        chatroom_setting=conv["chatroom_setting"],
        persona="upenn sophomore",
        my_nickname="Mars",
        history_block=history,
        participant_nicknames=[p["nickname"] for p in conv["participants"]],
    )
    # SCAFFOLD content is verbatim from speech_scaffold.py — sample a unique fragment.
    assert "Output format" in prompt
    assert "speak" in prompt
    # TOPIC block (heading + researcher's text).
    assert "# Chatroom topic" in prompt
    assert "Chat freely about college life." in prompt
    # PERSONA tag.
    assert "<your-persona>" in prompt and "upenn sophomore" in prompt
    # PARTICIPANTS tag — every nickname is listed; the caller is annotated.
    assert "<participants>" in prompt
    assert "- Earth" in prompt
    assert "- Mars (you)" in prompt
    assert "- Venus" in prompt
    # CONTEXT tags.
    assert "<your-name>" in prompt and "Mars" in prompt
    assert "<conversation-history>" in prompt
    # History rendering.
    assert "Earth: hi" in prompt
    assert "Mars: hi yo" in prompt


def test_system_prompt_omits_participants_when_not_passed() -> None:
    conv = _conv()
    history = _render_history_block(conv, now_ms=20_000)
    prompt = _build_system_prompt(
        mode="group",
        chatroom_setting=conv["chatroom_setting"],
        persona="",
        my_nickname="Mars",
        history_block=history,
    )
    assert "<participants>" not in prompt


def test_system_prompt_appends_additional_prompt_after_history() -> None:
    conv = _conv()
    conv["chatroom_setting"]["additional_prompt"] = (
        "Reminder: stay one-thought-per-turn."
    )
    history = _render_history_block(conv, now_ms=20_000)
    prompt = _build_system_prompt(
        mode="group",
        chatroom_setting=conv["chatroom_setting"],
        persona="",
        my_nickname="Mars",
        history_block=history,
    )
    # additional_prompt must land AFTER the history, not before.
    history_pos = prompt.index("</conversation-history>")
    additional_pos = prompt.index("Reminder: stay one-thought-per-turn.")
    assert additional_pos > history_pos


def test_system_prompt_omits_empty_additional_prompt() -> None:
    conv = _conv()
    conv["chatroom_setting"]["additional_prompt"] = "   "  # whitespace only
    history = _render_history_block(conv, now_ms=20_000)
    prompt = _build_system_prompt(
        mode="group",
        chatroom_setting=conv["chatroom_setting"],
        persona="",
        my_nickname="Mars",
        history_block=history,
    )
    # Trailing whitespace from the joined sections is fine, but the prompt
    # should end with the closing history tag — no extra empty section.
    assert prompt.rstrip().endswith("</conversation-history>")


def test_bedrock_messages_form_alternating_roles_for_each_ai() -> None:
    conv = _conv()
    # From Mars's perspective: Earth+Venus are user; Mars is assistant.
    msgs_for_mars = build_bedrock_messages(conv, "ai_001", now=20_000)
    roles = [m["role"] for m in msgs_for_mars]
    # Expect: user (Earth's "hi"), assistant (Mars's "hi yo"), user (Earth's "what's up").
    assert roles == ["user", "assistant", "user"]

    # From Venus's perspective: Earth+Mars are user; Venus has not spoken so no assistant role.
    msgs_for_venus = build_bedrock_messages(conv, "ai_002", now=20_000)
    venus_roles = [m["role"] for m in msgs_for_venus]
    # Earth-hi merged with Mars-hi-yo merged with Earth-what's-up = single user block.
    assert venus_roles == ["user"]


def test_bedrock_messages_pending_ai_message_excluded() -> None:
    """Mars's message has visible_at=8_000; at now=5_000 it should not be visible."""
    conv = _conv()
    msgs_for_venus = build_bedrock_messages(conv, "ai_002", now=5_000)
    # At now=5_000 only Earth's "hi" (visible_at=1_000) is visible.
    # Mars's message hasn't become visible yet, and Earth's "what's up" is in the future.
    assert len(msgs_for_venus) == 1
    assert msgs_for_venus[0]["role"] == "user"
    assert "[Earth] hi" in msgs_for_venus[0]["content"][0]["text"]
