"""Example tests for max_duration_seconds enforcement and Bedrock error path."""

from unittest.mock import patch
import uuid
import pytest

from chatroom_api import config, mock_dynamo, mock_lobby, mock_rds
from chatroom_api import tick_handler
from chatroom_api.bedrock_client import BedrockInferenceError


CHATROOM_ID = "scid_pbt-tick-examples"


@pytest.fixture(autouse=True)
def _skip_real_typing_delay(monkeypatch):
    """Keep existing examples fast; delay behavior has fake-clock tests below."""
    monkeypatch.setattr(tick_handler.time, "sleep", lambda _seconds: None)


def _seed(*, max_duration_seconds=None, started_at_ms=None, setting_overrides=None):
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
    setting.update(setting_overrides or {})

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


def test_bedrock_fatal_error_updates_projection_and_appends_system_event():
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
    system_events = [e for e in events if e.get("type") == "system"]
    # One visible system event plus compact server-only tick state.
    assert any("server error" in e.get("content", "").lower() for e in system_events)
    projection = conv["ai_tick_state_by_participant_id"]["ai_001"]
    assert projection["last_result"] == "error"


def test_bedrock_resource_not_found_falls_back_to_default_model():
    cid, started_at_ms = _seed()
    now_seconds = (started_at_ms / 1000) + 60
    calls: list[str] = []

    def _fake_invoke(model_id, system_prompt, bedrock_messages, **kwargs):
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


def test_single_non_mimic_ai_must_reply_to_human_without_typing_delay():
    cid, started_at_ms = _seed(setting_overrides={
        "mimic_human": False,
        "ai_count": 1,
    })
    now_ms = started_at_ms + 60_000
    mock_dynamo.append_events(cid, CHATROOM_ID, [{
        "type": "message",
        "session_id": "h1",
        "sender": "Earth",
        "role": "human",
        "content": "Can you help me think through this?",
        # A generic room would still be inside the 5-second silence gate.
        "timestamp": now_ms - 1_000,
        "visible_at": now_ms - 1_000,
    }])

    def _fake_invoke(_model_id, _system_prompt, _messages, **kwargs):
        assert kwargs["require_message"] is True
        assert "Never stay silent" in _system_prompt[0]["text"]
        return {
            "messages": ["Sure.", "What part should we start with?"],
            "input_tokens": 1,
            "output_tokens": 1,
        }

    with patch.object(tick_handler.time, "time", return_value=now_ms / 1000), \
         patch.object(tick_handler, "invoke_speak_tool", side_effect=_fake_invoke), \
         patch.object(tick_handler, "pick_delays_ms") as mock_delays:
        result = tick_handler.handle_tick({"conversation_id": cid})

    assert result["status"] == "spoke"
    mock_delays.assert_not_called()
    ai_messages = [
        event for event in mock_dynamo.get_events(cid)
        if event.get("type") == "message" and event.get("role") == "ai"
    ]
    assert [event["content"] for event in ai_messages] == [
        "Sure.",
        "What part should we start with?",
    ]
    assert {event["timestamp"] for event in ai_messages} == {now_ms}
    assert all("visible_at" not in event for event in ai_messages)


def test_required_response_only_applies_when_latest_visible_message_is_human():
    setting = {"mimic_human": False, "ai_count": 1}
    conv = {"events": [{
        "type": "message",
        "role": "ai",
        "timestamp": 100,
        "visible_at": 100,
    }]}
    assert not tick_handler._requires_response_after_human(conv, setting, 200)
    assert not tick_handler._requires_response_after_human(
        {"events": [{"type": "message", "role": "human", "visible_at": 100}]},
        {"mimic_human": False, "ai_count": 2},
        200,
    )
    assert not tick_handler._requires_response_after_human(
        {"events": [{"type": "message", "role": "human", "visible_at": 100}]},
        {"mimic_human": True, "ai_count": 1},
        200,
    )
    assert not tick_handler._requires_response_after_human(
        {"events": [{"type": "message", "role": "human", "visible_at": 100}]},
        {"mimic_human": False, "human_count": 2, "ai_count": 1},
        200,
    )


def test_single_non_mimic_ai_still_runs_inference_before_idle_follow_up():
    cid, started_at_ms = _seed(setting_overrides={
        "mimic_human": False,
        "ai_count": 1,
    })
    now_ms = started_at_ms + 30_000
    mock_dynamo.append_events(cid, CHATROOM_ID, [{
        "type": "message",
        "session_id": "ai_001",
        "sender": "Mars",
        "role": "ai",
        "content": "What do you think?",
        "timestamp": now_ms - 10_000,
        "visible_at": now_ms - 10_000,
    }])

    def _fake_invoke(_model_id, system_prompt, _messages, **kwargs):
        assert kwargs["require_message"] is False
        rendered_prompt = system_prompt[0]["text"]
        assert "Single-AI unanswered time: 10 seconds" in rendered_prompt
        assert "idle follow-up already sent" in rendered_prompt
        return {"messages": [], "input_tokens": 1, "output_tokens": 1}

    with patch.object(tick_handler.time, "time", return_value=now_ms / 1000), \
         patch.object(tick_handler, "invoke_speak_tool", side_effect=_fake_invoke) as invoke:
        result = tick_handler.handle_tick({"conversation_id": cid})

    assert result["status"] == "silent"
    invoke.assert_called_once()


def test_idle_follow_up_is_forced_once_without_typing_delay():
    cid, started_at_ms = _seed(setting_overrides={
        "mimic_human": False,
        "ai_count": 1,
    })
    now_ms = started_at_ms + 70_000
    mock_dynamo.append_events(cid, CHATROOM_ID, [{
        "type": "message",
        "session_id": "ai_001",
        "sender": "Mars",
        "role": "ai",
        "content": "What do you think?",
        "timestamp": now_ms - 61_000,
        "visible_at": now_ms - 61_000,
    }])

    def _fake_invoke(_model_id, _system_prompt, messages, **kwargs):
        assert kwargs["require_message"] is True
        assert "human has not replied" in messages[-1]["content"][0]["text"]
        return {
            "messages": ["Still with me?"],
            "input_tokens": 1,
            "output_tokens": 1,
        }

    with patch.object(tick_handler.time, "time", return_value=now_ms / 1000), \
         patch.object(tick_handler, "invoke_speak_tool", side_effect=_fake_invoke), \
         patch.object(tick_handler, "pick_delays_ms") as mock_delays:
        result = tick_handler.handle_tick({"conversation_id": cid})

    assert result["status"] == "spoke"
    mock_delays.assert_not_called()
    conv = mock_dynamo.get_conversation(cid)
    follow_ups = [
        event for event in conv["events"]
        if event.get("message_kind") == "idle_follow_up"
    ]
    assert len(follow_ups) == 1
    assert follow_ups[0]["timestamp"] == now_ms
    assert "visible_at" not in follow_ups[0]
    assert not tick_handler._requires_idle_follow_up(
        conv,
        conv["chatroom_setting"],
        now_ms + 61_000,
    )


def test_participant_model_id_overrides_chatroom_default():
    cid, started_at_ms = _seed()
    now_seconds = (started_at_ms / 1000) + 60

    mock_dynamo._rooms[cid]["participants"][1]["model_id"] = tick_handler._DEFAULT_MODEL_ID
    mock_dynamo._rooms[cid]["chatroom_setting"]["model_id"] = "test-model"

    seen_models: list[str] = []

    def _fake_invoke(model_id, system_prompt, bedrock_messages, **kwargs):
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


def test_delayed_messages_are_absent_until_each_delay_finishes(monkeypatch):
    cid, started_at_ms = _seed()

    class FakeClock:
        now_ms = started_at_ms + 60_000

        def time(self):
            return self.now_ms / 1000

        def sleep(self, seconds):
            ai_events = [
                event for event in mock_dynamo.get_events(cid)
                if event.get("type") == "message" and event.get("role") == "ai"
            ]
            assert len(ai_events) == len(sleeps)
            sleeps.append(seconds)
            self.now_ms += int(seconds * 1000)

    clock = FakeClock()
    sleeps: list[float] = []
    monkeypatch.setattr(tick_handler.time, "time", clock.time)
    monkeypatch.setattr(tick_handler.time, "sleep", clock.sleep)
    monkeypatch.setattr(tick_handler, "pick_delays_ms", lambda _count: [2_000, 3_000])
    monkeypatch.setattr(
        tick_handler,
        "invoke_speak_tool",
        lambda *_args, **_kwargs: {
            "messages": ["first bubble", "second bubble"],
            "input_tokens": 1,
            "output_tokens": 1,
        },
    )

    authored_at = clock.now_ms
    result = tick_handler.handle_tick({"conversation_id": cid})

    assert result["status"] == "spoke"
    assert result["messages_persisted"] == 2
    assert sleeps == [2, 3]
    ai_events = [
        event for event in mock_dynamo.get_events(cid)
        if event.get("type") == "message" and event.get("role") == "ai"
    ]
    assert [event["timestamp"] for event in ai_events] == [
        authored_at + 2_000,
        authored_at + 5_000,
    ]
    assert {event["authored_at"] for event in ai_events} == {authored_at}
    assert len({event["turn_id"] for event in ai_events}) == 1
    assert all(event["timestamp"] <= clock.now_ms for event in ai_events)


def test_delayed_message_is_dropped_when_conversation_ends_during_wait(monkeypatch):
    cid, started_at_ms = _seed()
    now_ms = started_at_ms + 60_000

    def end_during_sleep(_seconds):
        mock_dynamo.update_status(cid, "ended")

    monkeypatch.setattr(tick_handler.time, "time", lambda: now_ms / 1000)
    monkeypatch.setattr(tick_handler.time, "sleep", end_during_sleep)
    monkeypatch.setattr(tick_handler, "pick_delays_ms", lambda _count: [2_000])
    monkeypatch.setattr(
        tick_handler,
        "invoke_speak_tool",
        lambda *_args, **_kwargs: {
            "messages": ["must never be stored"],
            "input_tokens": 1,
            "output_tokens": 1,
        },
    )

    result = tick_handler.handle_tick({"conversation_id": cid})

    assert result["status"] == "dropped_stale_tick"
    assert not [
        event for event in mock_dynamo.get_events(cid)
        if event.get("type") == "message" and event.get("role") == "ai"
    ]
    assert len(mock_rds._usage_records) == 1


def test_delayed_message_is_dropped_when_duration_elapses_during_wait(monkeypatch):
    cid, started_at_ms = _seed(max_duration_seconds=62)

    class FakeClock:
        now_ms = started_at_ms + 60_000

        def time(self):
            return self.now_ms / 1000

        def sleep(self, seconds):
            self.now_ms += int(seconds * 1000)

    clock = FakeClock()
    monkeypatch.setattr(tick_handler.time, "time", clock.time)
    monkeypatch.setattr(tick_handler.time, "sleep", clock.sleep)
    monkeypatch.setattr(tick_handler, "pick_delays_ms", lambda _count: [3_000])
    monkeypatch.setattr(
        tick_handler,
        "invoke_speak_tool",
        lambda *_args, **_kwargs: {
            "messages": ["too late"],
            "input_tokens": 1,
            "output_tokens": 1,
        },
    )

    result = tick_handler.handle_tick({"conversation_id": cid})

    assert result == {
        "status": "ended",
        "messages_persisted": 0,
        "messages_dropped": 1,
    }
    conv = mock_dynamo.get_conversation(cid)
    assert conv["status"] == "ended"
    assert not [event for event in conv["events"] if event.get("role") == "ai"]
    assert len([
        event for event in conv["events"]
        if event.get("subtype") == "conversation_ended"
    ]) == 1
    assert len(mock_rds._usage_records) == 1
