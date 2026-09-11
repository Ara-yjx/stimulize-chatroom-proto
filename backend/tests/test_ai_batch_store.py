from chatroom_api.ai_batch import store


def test_commit_terminal_turn_composes_batch_counter_transaction(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(store.config, "AI_BATCH_TABLE", "batch-table")

    def append(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(store.event_store, "append_history_batch", append)
    conversation = {
        "conversation_id": "conversation",
        "batch_job_id": "batch",
        "next_turn": 3,
        "state_version": 3,
        "message_count": 3,
        "total_chars": 10,
    }
    event = {
        "content": "hello",
        "ai_participant_id": "ai-1",
        "turn_write_id": "write-id",
    }

    store.commit_turn(
        conversation,
        "lease",
        event,
        terminal_status="completed",
    )

    kwargs = captured["kwargs"]
    assert kwargs["metadata_updates"]["next_turn"] == 4
    assert kwargs["metadata_updates"]["message_count"] == 4
    assert kwargs["metadata_updates"]["total_chars"] == 15
    assert kwargs["expected_metadata"]["worker_lease_id"] == "lease"
    batch_update = kwargs["extra_transact_actions"][0]["Update"]
    assert batch_update["TableName"] == "batch-table"
    assert "completed_count" in batch_update["UpdateExpression"]
