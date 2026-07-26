import pytest

from chatroom_api import mock_dynamo, mock_event_store
from chatroom_api.cursors import InvalidCursorError
from chatroom_api.event_store import ConditionalWriteFailed


@pytest.fixture(autouse=True)
def reset_store():
    mock_dynamo.reset()


def _event(timestamp: int, content: str) -> dict:
    return {
        "type": "message",
        "role": "human",
        "session_id": "session_one",
        "sender": "One",
        "content": content,
        "timestamp": timestamp,
    }


def _create() -> None:
    mock_event_store.create_conversation(
        {
            "conversation_id": "conv_one",
            "status": "active",
            "participants": [],
        },
        [_event(100, "first"), _event(100, "second")],
        "create_batch",
    )


def test_create_and_append_are_idempotent_and_detect_conflicts():
    _create()
    first_page = mock_event_store.query_live_after("conv_one", None, 100, 10)
    assert [event["content"] for event in first_page["events"]] == ["first", "second"]

    mock_event_store.create_conversation(
        mock_dynamo.get_conversation("conv_one"),
        [_event(100, "first"), _event(100, "second")],
        "create_batch",
    )
    with pytest.raises(ConditionalWriteFailed):
        mock_event_store.create_conversation(
            mock_dynamo.get_conversation("conv_one"),
            [_event(100, "changed")],
            "create_batch",
        )

    appended = mock_event_store.append_history_batch(
        "conv_one", [_event(150, "third")], "append_batch", expected_status="active"
    )
    assert appended[0]["event_id"] == "append_batch#0"
    assert mock_event_store.append_history_batch(
        "conv_one", [_event(150, "third")], "append_batch", expected_status="active"
    ) == appended


def test_cursor_pages_history_and_future_pending():
    _create()
    mock_event_store.append_history_batch(
        "conv_one",
        [_event(200, "third"), _event(300, "future")],
        "later_batch",
    )

    first = mock_event_store.query_live_after("conv_one", None, 200, 2)
    assert [event["content"] for event in first["events"]] == ["first", "second"]
    assert first["has_more"] is True

    second = mock_event_store.query_live_after(
        "conv_one", first["next_after"], 200, 2
    )
    assert [event["content"] for event in second["events"]] == ["third"]
    assert second["has_more"] is False
    assert mock_event_store.query_next_pending(
        "conv_one", second["next_after"], 200
    ) == 300

    newest = mock_event_store.query_history_before("conv_one", None, 300, 2)
    assert [event["content"] for event in newest["events"]] == ["third", "future"]
    assert newest["has_more"] is True
    older = mock_event_store.query_history_before(
        "conv_one", newest["next_before"], 300, 2
    )
    assert [event["content"] for event in older["events"]] == ["first", "second"]


def test_status_condition_and_cursor_scope_are_enforced():
    _create()
    with pytest.raises(ConditionalWriteFailed):
        mock_event_store.append_history_batch(
            "conv_one", [_event(200, "late")], "late_batch", expected_status="ended"
        )

    cursor = mock_event_store.query_live_after("conv_one", None, 100, 1)["next_after"]
    with pytest.raises(InvalidCursorError):
        mock_event_store.query_live_after("conv_two", cursor, 100, 1)


def test_tick_projection_does_not_refresh_history_timestamps():
    _create()
    room = mock_dynamo.get_conversation("conv_one")
    old_updated_at = room["updated_at"]
    old_ttl = room["ttl"]

    mock_event_store.update_tick_projection(
        "conv_one",
        "ai_one",
        {"last_result": "silent", "next_actionable_at": 250},
        expected_status="active",
    )

    room = mock_dynamo.get_conversation("conv_one")
    assert room["updated_at"] == old_updated_at
    assert room["ttl"] == old_ttl
    assert room["next_actionable_tick_at"] == 250
