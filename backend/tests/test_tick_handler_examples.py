"""Example tests for max_duration_seconds enforcement and Bedrock error path."""

from unittest.mock import patch
import uuid
import pytest

from chatroom_api import config, mock_dynamo, mock_lobby, mock_rds
from chatroom_api import tick_handler
from chatroom_api.bedrock_client import BedrockInferenceError


CHATROOM_ID = "scid_pbt-tick-examples"


def _seed(*, max_duration_seconds=None, started_at_ms=None):
    """Reset mocks; seed an active conversation. Returns (conversation_id, started_at_ms_used)."""
    config.USE_MOCK_DYNAMO = True
    config.USE_MOCK_RDS = True
    config.USE_MOCK_LOBBY = True
    mock_dynamo.reset()
    mock_lobby.reset()
    mock_rds._usage_records.clear()

    cid = "conv-" + uuid.uuid4().hex
    started_at_ms = started_at_ms or 1_700_000_000_000
    started_at_iso = "2023-11-14T22:13:20+00:00"  # ~started_at_ms

    setting = {
        "mode": "group",
        "topic_instruction": "test topic",
        "model_id": "test-model",
        "target_human_count": 1,
        "ai_join_strategy": "fixed_ai_count",
        "ai_strategy_value": 1,
        "max_wait_seconds": 0,
    }
    if max_duration_seconds is not None:
        setting["max_duration_seconds"] = max_duration_seconds

    participants = [
        {"session_id": "h1", "nickname": "Earth", "role": "human"},
        {"session_id": "ai_001", "nickname": "Mars", "role": "ai", "persona": "test"},
    ]
    mock_dynamo.append_events(
        cid, CHATROOM_ID, [],
        chatroom_setting=setting,
        participants=participants,
        status="active",
        started_at=started_at_iso,
        last_tick_at=0,
    )
    mock_rds._chatrooms[CHATROOM_ID] = {
        "id": CHATROOM_ID, "owner_id": "u", "name": "test",
        "status": "active", "setting": setting,
    }
    return cid, started_at_ms


def test_max_duration_enforcement_flips_to_ended_and_skips_bedrock():
    cid, started_at_ms = _seed(max_duration_seconds=10)
    # now is far past started_at + 10s
    now_seconds = (started_at_ms / 1000) + 100  # 100s after start

    with patch.object(tick_handler.time, "time", return_value=now_seconds), \
         patch.object(tick_handler, "invoke_speak_tool") as mock_bedrock:
        result = tick_handler.handle_tick({"conversation_id": cid})

    assert result == {"status": "ended"}
    # Bedrock NOT invoked.
    assert mock_bedrock.call_count == 0
    # Status flipped.
    conv = mock_dynamo.get_conversation(cid)
    assert conv["status"] == "ended"
    # A "conversation has ended" system event was appended.
    events = mock_dynamo.get_events(cid)
    assert any(
        e.get("type") == "system" and "ended" in e.get("content", "").lower()
        for e in events
    )


def test_bedrock_fatal_error_appends_tick_and_system_events_and_keeps_active():
    cid, started_at_ms = _seed()
    now_seconds = (started_at_ms / 1000) + 60  # 60s after start

    fatal_err = BedrockInferenceError(
        "ValidationException", "invalid model", retryable=False
    )

    with patch.object(tick_handler.time, "time", return_value=now_seconds), \
         patch.object(tick_handler, "invoke_speak_tool", side_effect=fatal_err):
        result = tick_handler.handle_tick({"conversation_id": cid})

    assert result["status"] == "bedrock_error"
    assert result["error_type"] == "ValidationException"

    conv = mock_dynamo.get_conversation(cid)
    # Conversation continues.
    assert conv["status"] == "active"

    events = mock_dynamo.get_events(cid)
    tick_events = [e for e in events if e.get("type") == "tick"]
    system_events = [e for e in events if e.get("type") == "system"]
    assert len(tick_events) == 1
    assert tick_events[0].get("error") == "ValidationException"
    assert tick_events[0].get("bedrock_invoked") is True
    assert tick_events[0].get("ai_decision") is None

    # One system event with "Chatroom server error".
    assert any("server error" in e.get("content", "").lower() for e in system_events)


def test_bedrock_resource_not_found_falls_back_to_default_model():
    cid, started_at_ms = _seed()
    now_seconds = (started_at_ms / 1000) + 60
    calls: list[str] = []

    def _fake_invoke(model_id, system_prompt, bedrock_messages):
        calls.append(model_id)
        if model_id == "test-model":
            raise BedrockInferenceError(
                "ResourceNotFoundException",
                "model retired",
                retryable=False,
            )
        return {
            "messages": ["fallback worked"],
            "input_tokens": 1,
            "output_tokens": 1,
        }

    with patch.object(tick_handler.time, "time", return_value=now_seconds), \
         patch.object(tick_handler, "invoke_speak_tool", side_effect=_fake_invoke):
        result = tick_handler.handle_tick({"conversation_id": cid})

    assert result["status"] == "spoke"
    assert calls == ["test-model", tick_handler._DEFAULT_MODEL_ID]
    events = mock_dynamo.get_events(cid)
    assert any(e.get("type") == "message" and e.get("content") == "fallback worked" for e in events)
    assert len(mock_rds._usage_records) == 1
    usage = mock_rds._usage_records[0]
    assert usage["chatroom_id"] == CHATROOM_ID
    assert usage["provider"] == "bedrock"
    assert usage["model_id"] == tick_handler._DEFAULT_MODEL_ID
    assert usage["pricing_key"] == "bedrock_claude_sonnet_4_6_global_standard"
    assert usage["input_tokens"] == 1
    assert usage["output_tokens"] == 1
    assert usage["estimated_cost_usd"] > 0


def test_participant_model_id_overrides_chatroom_default():
    cid, started_at_ms = _seed()
    now_seconds = (started_at_ms / 1000) + 60

    mock_dynamo._rooms[cid]["participants"][1]["model_id"] = tick_handler._DEFAULT_MODEL_ID
    mock_dynamo._rooms[cid]["chatroom_setting"]["model_id"] = "test-model"

    seen_models: list[str] = []

    def _fake_invoke(model_id, system_prompt, bedrock_messages):
        seen_models.append(model_id)
        return {
            "messages": ["participant-specific model"],
            "input_tokens": 1,
            "output_tokens": 1,
        }

    with patch.object(tick_handler.time, "time", return_value=now_seconds), \
         patch.object(tick_handler, "invoke_speak_tool", side_effect=_fake_invoke):
        result = tick_handler.handle_tick({"conversation_id": cid})

    assert result["status"] == "spoke"
    assert seen_models == [tick_handler._DEFAULT_MODEL_ID]
