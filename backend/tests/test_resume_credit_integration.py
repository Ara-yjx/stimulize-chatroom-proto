"""Unit coverage for resumable tick fencing combined with prepaid credits."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chatroom_api import config, mock_dynamo, mock_lobby, mock_rds, resumable
from chatroom_api import tick_handler


CHATROOM = {
    "id": "scid_resume_credit_test",
    "owner_id": "owner_resume_credit_test",
    "status": "active",
    "setting": {
        "resumable": True,
        "human_count": 1,
        "ai_count": 1,
        "mimic_human": False,
        "model_id": "global.anthropic.claude-sonnet-4-6",
        "max_duration_seconds": 30,
        "ai_personas": [{
            "internal_name": "coach",
            "nickname": "Alex",
            "persona": "Help the participant reflect.",
        }],
    },
}


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setattr(config, "USE_MOCK_DYNAMO", True)
    monkeypatch.setattr(config, "USE_MOCK_RDS", True)
    monkeypatch.setattr(config, "USE_MOCK_LOBBY", True)
    monkeypatch.setattr(config, "STIMULIZE_API_URL", "https://stimulize.example.com")
    monkeypatch.setattr(config, "STIMULIZE_API_TOKEN", "test-token")
    mock_dynamo.reset()
    mock_lobby.reset()
    mock_rds._usage_records.clear()


def _seed_resumable_conversation():
    with patch("chatroom_api.jwt_utils.create_token", return_value="token"):
        status, body = resumable.create_or_resume(
            deepcopy(CHATROOM),
            "Participant.1",
        )
    assert status == 200
    mock_rds._chatrooms[CHATROOM["id"]] = deepcopy(CHATROOM)
    conversation = mock_dynamo.get_conversation(body["conversation_id"])
    ai_participant_id = next(
        participant["ai_participant_id"]
        for participant in conversation["participants"]
        if participant["role"] == "ai"
    )
    return body["conversation_id"], ai_participant_id


def _gate_for(ai_participant_id):
    return SimpleNamespace(
        skip=False,
        reason=None,
        candidate_session_id=ai_participant_id,
        candidate_nickname="Alex",
    )


def test_insufficient_credits_skip_resumable_inference():
    conversation_id, ai_participant_id = _seed_resumable_conversation()

    with patch.object(tick_handler, "run_gate", return_value=_gate_for(ai_participant_id)), \
         patch.object(tick_handler.credits_client, "check_credits", return_value=False) as check, \
         patch.object(tick_handler.credits_client, "debit_usage") as debit, \
         patch.object(tick_handler, "invoke_speak_tool") as invoke:
        result = tick_handler.handle_tick({"conversation_id": conversation_id})

    assert result == {"status": "skipped", "reason": "insufficient_credits"}
    check.assert_called_once_with(CHATROOM["owner_id"])
    invoke.assert_not_called()
    debit.assert_not_called()
    assert mock_rds._usage_records == []
    assert mock_dynamo.get_conversation(conversation_id)["status"] == "active"


def test_allowed_credits_write_resumable_message_usage_and_debit_once():
    conversation_id, ai_participant_id = _seed_resumable_conversation()

    with patch.object(tick_handler, "run_gate", return_value=_gate_for(ai_participant_id)), \
         patch.object(tick_handler, "_requires_response_after_human", return_value=False), \
         patch.object(tick_handler, "pick_delays_ms", return_value=[0]), \
         patch.object(tick_handler.credits_client, "check_credits", return_value=True), \
         patch.object(tick_handler.credits_client, "debit_usage") as debit, \
         patch.object(tick_handler, "invoke_speak_tool", return_value={
             "messages": ["Let us reflect on that."],
             "input_tokens": 10,
             "output_tokens": 5,
         }):
        result = tick_handler.handle_tick({"conversation_id": conversation_id})

    assert result["status"] == "spoke"
    assert len(mock_rds._usage_records) == 1
    usage = mock_rds._usage_records[0]
    debit.assert_called_once()
    assert debit.call_args.kwargs["owner_id"] == CHATROOM["owner_id"]
    assert debit.call_args.kwargs["usage_event_id"] == usage["usage_event_id"]
    messages = [
        event for event in mock_dynamo._history[conversation_id]
        if event.get("role") == "ai"
    ]
    assert [message["content"] for message in messages] == [
        "Let us reflect on that."
    ]
    assert messages[0]["episode_number"] == 1


def test_stale_resumable_output_is_dropped_after_usage_and_debit():
    conversation_id, ai_participant_id = _seed_resumable_conversation()

    def end_episode_during_delay(_seconds):
        conversation = mock_dynamo.get_conversation(conversation_id)
        assert resumable.end_episode(conversation)

    with patch.object(tick_handler, "run_gate", return_value=_gate_for(ai_participant_id)), \
         patch.object(tick_handler, "_requires_response_after_human", return_value=False), \
         patch.object(tick_handler, "pick_delays_ms", return_value=[1000]), \
         patch.object(tick_handler.time, "sleep", side_effect=end_episode_during_delay), \
         patch.object(tick_handler.credits_client, "check_credits", return_value=True), \
         patch.object(tick_handler.credits_client, "debit_usage") as debit, \
         patch.object(tick_handler, "invoke_speak_tool", return_value={
             "messages": ["This output is stale."],
             "input_tokens": 10,
             "output_tokens": 5,
         }):
        result = tick_handler.handle_tick({"conversation_id": conversation_id})

    assert result["status"] == "dropped_stale_tick"
    assert len(mock_rds._usage_records) == 1
    debit.assert_called_once()
    assert debit.call_args.kwargs["usage_event_id"] == (
        mock_rds._usage_records[0]["usage_event_id"]
    )
    assert mock_dynamo.get_conversation(conversation_id)["status"] == "inactive"
    assert all(
        event.get("content") != "This output is stale."
        for event in mock_dynamo._history[conversation_id]
    )
