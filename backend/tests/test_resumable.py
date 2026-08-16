"""Lifecycle and identity tests for resumable conversations."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from chatroom_api import chat, mock_dynamo, mock_lobby, mock_rds, resumable, tick_handler
from chatroom_api.auth import handle_auth_token


CHATROOM = {
    "id": "scid_resume_test",
    "status": "active",
    "setting": {
        "resumable": True,
        "human_count": 1,
        "ai_count": 1,
        "mimic_human": False,
        "model_id": "global.anthropic.claude-sonnet-4-6",
        "temperature": 0.5,
        "max_duration_seconds": 30,
        "ai_personas": [{
            "internal_name": "coach",
            "nickname": "Alex",
            "persona": "Help the participant reflect.",
        }],
    },
}


def setup_function() -> None:
    mock_dynamo.reset()
    mock_lobby.reset()


def _auth(participant_id="Case.Sensitive_1", chatroom=None):
    rds = MagicMock()
    rds.get_chatroom.return_value = deepcopy(chatroom or CHATROOM)
    with patch("chatroom_api.auth._get_rds", return_value=rds), patch(
        "chatroom_api.jwt_utils.create_token", return_value="token"
    ):
        return handle_auth_token({
            "chatroom_id": CHATROOM["id"],
            "participant_id": participant_id,
        })


def _claims(body):
    return {
        "session_id": body["session_id"],
        "conversation_id": body["conversation_id"],
        "chatroom_id": CHATROOM["id"],
        "participant_id": body["participant_id"],
        "connection_id": body["connection_id"],
        "episode_number": body["episode_number"],
    }


def test_participant_id_is_trimmed_but_case_sensitive() -> None:
    status, body = _auth("  Case.Sensitive_1  ")
    assert status == 200
    assert body["participant_id"] == "Case.Sensitive_1"
    assert body["conversation_id"] == resumable.conversation_id_for(
        CHATROOM["id"], "Case.Sensitive_1"
    )

    status2, body2 = _auth("case.Sensitive_1")
    assert status2 == 200
    assert body2["conversation_id"] != body["conversation_id"]


def test_missing_or_malformed_participant_id_is_rejected() -> None:
    for value in (None, "", "contains space", "x" * 64):
        status, body = _auth(value)
        assert status == 400
        assert "participant_id" in body["error"]


def test_first_auth_creates_locked_conversation_without_lobby() -> None:
    status, body = _auth()
    assert status == 200
    assert body["episode_number"] == 1
    assert body["resumed"] is False
    assert body["lobby"] is None

    conversation = mock_dynamo.get_conversation(body["conversation_id"])
    assert conversation["started_at"] == conversation["active_episode_started_at"]
    assert conversation["episode_count"] == 1
    assert conversation["participants"][0]["participant_id"] == "Case.Sensitive_1"
    assert conversation["participants"][1]["ai_participant_id"].startswith("ai_")
    assert conversation["participants"][1]["internal_name"] == "coach"
    assert conversation["chatroom_setting"]["temperature"] == 0.5
    assert mock_lobby.query_by_conversation_id(body["conversation_id"]) is None


def test_concurrent_first_auth_converges_on_one_conversation() -> None:
    with patch("chatroom_api.jwt_utils.create_token", return_value="token"):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(
                lambda _index: resumable.create_or_resume(
                    deepcopy(CHATROOM), "Concurrent.User"
                ),
                range(4),
            ))

    assert {status for status, _body in results} == {200}
    conversation_ids = {body["conversation_id"] for _status, body in results}
    assert len(conversation_ids) == 1
    conversation_id = conversation_ids.pop()
    conversation = mock_dynamo.get_conversation(conversation_id)
    assert conversation["episode_count"] == 1
    assert len(conversation["episodes"]) == 1
    assert len([
        event for event in mock_dynamo._history[conversation_id]
        if event.get("subtype") == "conversation_started"
    ]) == 1


def test_active_reauth_rotates_connection_and_supersedes_old_token() -> None:
    _, first = _auth()
    _, second = _auth()
    assert second["conversation_id"] == first["conversation_id"]
    assert second["episode_number"] == 1
    assert second["resumed"] is False
    assert second["connection_id"] != first["connection_id"]

    conversation = mock_dynamo.get_conversation(first["conversation_id"])
    assert "another browser" in resumable.validate_connection(
        conversation, _claims(first)
    )
    assert resumable.validate_connection(conversation, _claims(second)) is None


def test_inactive_auth_resumes_same_conversation_with_new_episode() -> None:
    _, first = _auth()
    conversation = mock_dynamo.get_conversation(first["conversation_id"])
    assert resumable.end_episode(conversation)
    inactive = mock_dynamo.get_conversation(first["conversation_id"])
    assert inactive["status"] == "inactive"
    assert inactive["episodes"][0]["history_end_cursor"]
    assert "active_connection_id" not in inactive
    assert "active_episode_number" not in inactive
    assert resumable.validate_connection(inactive, _claims(first)) is None

    changed = deepcopy(CHATROOM)
    changed["setting"]["temperature"] = 0.9
    _, second = _auth(chatroom=changed)
    resumed = mock_dynamo.get_conversation(first["conversation_id"])
    assert second["conversation_id"] == first["conversation_id"]
    assert second["episode_number"] == 2
    assert second["resumed"] is True
    assert second["history_start_cursor"] == inactive["last_history_end_cursor"]
    assert resumed["status"] == "active"
    assert resumed["chatroom_setting"]["temperature"] == 0.5
    assert [e["status"] for e in resumed["episodes"]] == ["inactive", "active"]


def test_human_event_has_connection_and_stable_author_identity() -> None:
    _, body = _auth()
    claims = _claims(body)
    status, _response = chat.handle_chat_send({"message": "hello"}, claims)
    assert status == 200
    events = mock_dynamo._history[body["conversation_id"]]
    human = next(event for event in events if event.get("role") == "human")
    assert human["session_id"] == body["session_id"]
    assert human["participant_id"] == body["participant_id"]
    assert human["episode_number"] == 1

    _, replacement = _auth()
    status, response = chat.handle_chat_send({"message": "stale"}, claims)
    assert status == 409
    assert response["code"] == "connection_superseded"
    assert replacement["connection_id"] != body["connection_id"]


def test_delayed_ai_output_is_dropped_after_episode_ends() -> None:
    _, body = _auth()
    claims = _claims(body)
    assert chat.handle_chat_send({"message": "hello"}, claims)[0] == 200
    conversation_id = body["conversation_id"]
    mock_rds._chatrooms[CHATROOM["id"]] = {
        **deepcopy(CHATROOM),
        "owner_id": "owner_resume_test",
    }

    def end_during_delay(_seconds: float) -> None:
        conversation = mock_dynamo.get_conversation(conversation_id)
        assert resumable.end_episode(conversation)

    response = {
        "messages": ["This must never become visible."],
        "input_tokens": 1,
        "output_tokens": 1,
    }
    with patch.object(tick_handler, "invoke_speak_tool", return_value=response), \
         patch.object(tick_handler, "_requires_response_after_human", return_value=False), \
         patch.object(tick_handler, "run_gate", return_value=SimpleNamespace(
             skip=False,
             candidate_session_id=next(
                 participant["ai_participant_id"]
                 for participant in mock_dynamo.get_conversation(conversation_id)["participants"]
                 if participant["role"] == "ai"
             ),
             candidate_nickname="Alex",
         )), \
         patch.object(tick_handler, "pick_delays_ms", return_value=[1000]), \
         patch.object(tick_handler.time, "sleep", side_effect=end_during_delay):
        result = tick_handler.handle_tick({"conversation_id": conversation_id})

    assert result["status"] == "dropped_stale_tick"
    conversation = mock_dynamo.get_conversation(conversation_id)
    assert conversation["status"] == "inactive"
    assert all(
        event.get("content") != "This must never become visible."
        for event in mock_dynamo._history[conversation_id]
    )
