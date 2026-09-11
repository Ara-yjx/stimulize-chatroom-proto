from chatroom_api import config
from chatroom_api.ai_batch import worker


def _participants(count: int) -> list[dict]:
    return [
        {
            "role": "ai",
            "ai_participant_id": f"ai-{index}",
            "nickname": f"AI {index}",
        }
        for index in range(count)
    ]


def _batch(**overrides) -> dict:
    return {
        "batch_job_id": "batch",
        "chatroom_id": "room",
        "owner_id": "9",
        "status": "running",
        "deadline_at": 100_000,
        "settings_snapshot": {
            "model_id": "model",
            "temperature": 0.7,
            "mimic_human": True,
            "max_message_chars": 400,
            "max_total_chars": 20_000,
            "max_turns": 4,
        },
        **overrides,
    }


def _conversation(count: int = 2, **overrides) -> dict:
    return {
        "conversation_id": "conversation",
        "batch_job_id": "batch",
        "status": "queued",
        "next_turn": 0,
        "message_count": 0,
        "total_chars": 0,
        "participants": _participants(count),
        **overrides,
    }


def test_two_ai_choose_message_requires_the_alternating_candidate(monkeypatch) -> None:
    conversation = _conversation(last_speaker_id="ai-0")
    calls = []

    def invoke(_batch, _conversation, participant, _history, **kwargs):
        calls.append((participant["ai_participant_id"], kwargs["require_message"]))
        return "hello"

    monkeypatch.setattr(worker, "_invoke_candidate", invoke)
    participant, text = worker._choose_message(_batch(), conversation, [])

    assert (participant["ai_participant_id"], text) == ("ai-1", "hello")
    assert calls == [("ai-1", True)]


def test_three_ai_force_path_has_at_most_ai_count_invocations(monkeypatch) -> None:
    calls = []

    def invoke(_batch, _conversation, participant, _history, **kwargs):
        calls.append((participant["ai_participant_id"], kwargs["require_message"]))
        return "forced" if kwargs["require_message"] else None

    monkeypatch.setattr(worker, "_invoke_candidate", invoke)
    _participant, text = worker._choose_message(
        _batch(),
        _conversation(3, last_speaker_id="ai-0"),
        [],
    )

    assert text == "forced"
    assert len(calls) == 3
    assert calls[-1][1] is True


def test_process_work_commits_and_requeues_next_turn(monkeypatch) -> None:
    batch = _batch()
    conversation = _conversation()
    committed = []
    requeued = []
    monkeypatch.setattr(config, "AI_BATCH_ENABLED", True)
    monkeypatch.setattr(worker.store, "get_batch", lambda _id: batch)
    monkeypatch.setattr(worker.store, "get_conversation", lambda _id: conversation)
    monkeypatch.setattr(worker.store, "now_ms", lambda: 1000)
    monkeypatch.setattr(worker.store, "now_iso", lambda: "now")
    monkeypatch.setattr(worker.store, "acquire_worker_lease", lambda *args: True)
    monkeypatch.setattr(worker.store, "query_history", lambda _id: [])
    monkeypatch.setattr(worker, "_choose_message", lambda *args: (conversation["participants"][0], "hello"))
    monkeypatch.setattr(worker.store, "commit_turn", lambda *args, **kwargs: committed.append((args, kwargs)))
    monkeypatch.setattr(worker.store, "send_work", lambda *args: requeued.append(args))

    result = worker._process_work({
        "batch_job_id": "batch",
        "conversation_id": "conversation",
        "expected_turn": 0,
    })

    assert result == {"status": "running", "turn": 0}
    assert committed[0][1]["terminal_status"] is None
    assert committed[0][0][2]["content"] == "hello"
    assert requeued == [("batch", "conversation", 1)]


def test_stale_delivery_repairs_missing_next_queue_message(monkeypatch) -> None:
    monkeypatch.setattr(config, "AI_BATCH_ENABLED", True)
    monkeypatch.setattr(worker.store, "get_batch", lambda _id: _batch())
    monkeypatch.setattr(
        worker.store,
        "get_conversation",
        lambda _id: _conversation(status="running", next_turn=2),
    )
    requeued = []
    monkeypatch.setattr(worker.store, "send_work", lambda *args: requeued.append(args))

    result = worker._process_work({
        "batch_job_id": "batch",
        "conversation_id": "conversation",
        "expected_turn": 1,
    })

    assert result == {"status": "requeued", "expected_turn": 2}
    assert requeued == [("batch", "conversation", 2)]
