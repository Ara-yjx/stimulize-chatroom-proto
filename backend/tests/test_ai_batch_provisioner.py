from chatroom_api import config
from chatroom_api.ai_batch import provisioner


def _batch() -> dict:
    return {
        "batch_job_id": "aib_test",
        "owner_id": "9",
        "chatroom_id": "scid_test",
        "status": "queued",
        "batch_count": 2,
        "created_at": "2026-09-12T00:00:00+00:00",
        "deadline_at": 1789238400000,
        "settings_snapshot": {
            "human_count": 0,
            "ai_count": 2,
            "model_id": "model",
            "temperature": 0.7,
            "ai_personas": [
                {"internal_name": "a", "persona": "A"},
                {"internal_name": "b", "persona": "B"},
            ],
        },
    }


def test_provisioner_creates_deterministic_conversations_and_dispatches(monkeypatch) -> None:
    batch = _batch()
    created = []
    dispatched = []
    monkeypatch.setattr(config, "AI_BATCH_ENABLED", True)
    monkeypatch.setattr(provisioner.store, "get_batch", lambda _batch_id: batch)
    monkeypatch.setattr(provisioner.store, "now_ms", lambda: 1000)
    monkeypatch.setattr(provisioner.store, "acquire_provision_lease", lambda *args: True)
    monkeypatch.setattr(provisioner.store, "create_conversation", lambda *args: created.append(args))
    monkeypatch.setattr(provisioner.store, "send_work", lambda *args: dispatched.append(args))
    monkeypatch.setattr(provisioner.store, "finish_provision", lambda *args: True)
    monkeypatch.setattr(provisioner.store, "record_batch_error", lambda *args: None)
    monkeypatch.setattr(provisioner.store, "release_provision_lease", lambda *args: True)

    result = provisioner._provision_batch(batch["batch_job_id"])

    assert result["status"] == "running"
    assert len(created) == 2
    assert len(dispatched) == 2
    assert created[0][0]["conversation_id"] != created[1][0]["conversation_id"]
    assert created[0][0]["participants"][0]["ai_participant_id"].startswith("ai_")
    assert dispatched[0][2] == 0


def test_provisioner_duplicate_delivery_without_lease_is_retried(monkeypatch) -> None:
    monkeypatch.setattr(config, "AI_BATCH_ENABLED", True)
    monkeypatch.setattr(provisioner.store, "get_batch", lambda _batch_id: _batch())
    monkeypatch.setattr(provisioner.store, "now_ms", lambda: 1000)
    monkeypatch.setattr(provisioner.store, "acquire_provision_lease", lambda *args: False)

    try:
        provisioner._provision_batch("aib_test")
    except RuntimeError as exc:
        assert "lease" in str(exc)
    else:
        raise AssertionError("held lease must keep the queue delivery retryable")


def test_provisioner_releases_lease_after_retryable_failure(monkeypatch) -> None:
    released = []
    monkeypatch.setattr(config, "AI_BATCH_ENABLED", True)
    monkeypatch.setattr(provisioner.store, "get_batch", lambda _batch_id: _batch())
    monkeypatch.setattr(provisioner.store, "now_ms", lambda: 1000)
    monkeypatch.setattr(provisioner.store, "acquire_provision_lease", lambda *args: True)
    monkeypatch.setattr(
        provisioner.store,
        "create_conversation",
        lambda *args: (_ for _ in ()).throw(RuntimeError("temporary failure")),
    )
    monkeypatch.setattr(provisioner.store, "record_batch_error", lambda *args: None)
    monkeypatch.setattr(
        provisioner.store,
        "release_provision_lease",
        lambda *args: released.append(args) or True,
    )

    try:
        provisioner._provision_batch("aib_test", receive_count=1)
    except RuntimeError as exc:
        assert "temporary failure" in str(exc)
    else:
        raise AssertionError("provisioning failure must be retried")
    assert released and released[0][0] == "aib_test"
