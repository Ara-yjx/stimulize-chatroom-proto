from chatroom_api import config
from chatroom_api.ai_batch import provisioner
import pytest


@pytest.fixture(autouse=True)
def stub_prompt_storage(monkeypatch):
    monkeypatch.setattr(provisioner.store, "save_prompt_reference", lambda *args: None)


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
    monkeypatch.setattr(provisioner.store, "start_execution", lambda *args: dispatched.append(args))
    monkeypatch.setattr(provisioner.store, "finish_provision", lambda *args: True)
    monkeypatch.setattr(provisioner.store, "record_batch_error", lambda *args: None)
    monkeypatch.setattr(provisioner.store, "release_provision_lease", lambda *args: True)

    result = provisioner._provision_batch(batch["batch_job_id"])

    assert result["status"] == "running"
    assert len(created) == 2
    assert len(dispatched) == 2
    assert created[0][0]["conversation_id"] != created[1][0]["conversation_id"]
    assert created[0][0]["participants"][0]["ai_participant_id"].startswith("ai_")
    assert dispatched[0][2] == batch["deadline_at"]
    assert created[0][0]["execution_state"] == "pending"
    assert created[0][0]["outcome"] is None
    for conversation, *_ in created:
        assert all('avatar' not in p for p in conversation['participants'])
        assert [p['nickname'] for p in conversation['participants']] == [
            'Participant_001', 'Participant_002',
        ]


def test_batch_names_preserve_personas_and_human_ai_defaults():
    import random
    from chatroom_api.ai_participants import build_ai_participants

    setting = _batch()['settings_snapshot']
    setting['ai_personas'] = [{'nickname': 'Alex', 'persona': 'Friendly'}]
    participants = build_ai_participants(setting, 3, sequential_nicknames=True)
    assert [p['nickname'] for p in participants] == ['Alex', 'Participant_002', 'Participant_003']
    assert len({p['ai_participant_id'] for p in participants}) == 3
    setting['ai_personas'] = []
    for seed in (1, 2):
        participants = build_ai_participants(setting, 2, rng=random.Random(seed), sequential_nicknames=True)
        assert [p['nickname'] for p in participants] == ['Participant_001', 'Participant_002']
    setting['human_count'] = 2
    participants = build_ai_participants(setting, 2, rng=random.Random(1))
    assert all(p['nickname'].startswith('Participant') and '_' not in p['nickname'] for p in participants)
    assert all(p['avatar']['emojiText'] for p in participants)


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


@pytest.mark.parametrize('invalid', ['personas', 'max_turns', 'max_total_chars', 'batch_count'])
def test_invalid_snapshot_fails_before_any_provisioning_side_effect(monkeypatch, invalid):
    from unittest.mock import Mock
    batch = _batch()
    if invalid == 'personas':
        batch['settings_snapshot']['ai_personas'] = [{'persona': 'Only one'}]
    elif invalid == 'batch_count':
        batch['batch_count'] = 51
    else:
        batch['settings_snapshot'][invalid] = {'max_turns': 1001, 'max_total_chars': 500001}[invalid]
    monkeypatch.setattr(config, 'AI_BATCH_ENABLED', True)
    monkeypatch.setattr(provisioner.store, 'get_batch', lambda _: batch)
    monkeypatch.setattr(provisioner.store, 'acquire_provision_lease', lambda *a: True)
    fail = Mock()
    monkeypatch.setattr(provisioner.store, 'fail_validation', fail)
    for name in ('save_prompt_reference', 'create_conversation', 'start_execution'):
        monkeypatch.setattr(provisioner.store, name, lambda *a: pytest.fail('must validate first'))
    assert provisioner._provision_batch('aib_test')['status'] == 'validation_failed'
    assert fail.call_args.args[0] == 'aib_test'
    assert fail.call_args.args[3] == batch['batch_count']
    batch['status'] = 'validation_failed'
    assert provisioner._provision_batch('aib_test')['status'] == 'noop'
    fail.assert_called_once()


def test_validation_failure_settles_ddb_and_duplicate_sqs_delivery(monkeypatch):
    import boto3
    from moto import mock_aws
    import json
    with mock_aws():
        table = boto3.resource('dynamodb', region_name='us-east-2').create_table(
            TableName='validation-batches', BillingMode='PAY_PER_REQUEST',
            KeySchema=[{'AttributeName': 'batch_job_id', 'KeyType': 'HASH'}],
            AttributeDefinitions=[{'AttributeName': 'batch_job_id', 'AttributeType': 'S'}])
        batch = _batch()
        batch.update(queued_count=2, running_count=0, unfinished_count=2, failed_count=0)
        batch['settings_snapshot']['temperature'] = 0
        batch['settings_snapshot']['ai_personas'] = [{'persona': 'Only one'}]
        table.put_item(Item=batch)
        monkeypatch.setattr(config, 'AI_BATCH_ENABLED', True)
        monkeypatch.setattr(provisioner.store, '_get_batch_table', lambda: table)
        for name in ('save_prompt_reference', 'create_conversation', 'start_execution'):
            monkeypatch.setattr(provisioner.store, name, lambda *a: pytest.fail('no creation allowed'))
        event = {'Records': [{'messageId': 'test', 'body': json.dumps({'version': 1, 'batch_job_id': batch['batch_job_id']})}]}
        for _ in range(2):
            assert provisioner.lambda_handler(event)['batchItemFailures'] == []
        row = table.get_item(Key={'batch_job_id': batch['batch_job_id']})['Item']
        assert row['status'] == 'validation_failed'
        assert row['queued_count'] == row['running_count'] == row['unfinished_count'] == 0
        assert row['failed_count'] == 2
        assert 'at least 2' in row['last_error']
        assert 'provision_lease_id' not in row


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


def test_prompt_save_precedes_dispatch_and_failure_prevents_inference(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "AI_BATCH_ENABLED", True)
    monkeypatch.setattr(provisioner.store, "get_batch", lambda _: _batch())
    monkeypatch.setattr(provisioner.store, "acquire_provision_lease", lambda *args: True)
    monkeypatch.setattr(provisioner.store, "record_batch_error", lambda *args: None)
    monkeypatch.setattr(provisioner.store, "release_provision_lease", lambda *args: True)
    monkeypatch.setattr(provisioner.store, "create_conversation", lambda *args: calls.append("create"))
    monkeypatch.setattr(provisioner.store, "start_execution", lambda *args: calls.append("send"))
    monkeypatch.setattr(provisioner.store, "finish_provision", lambda *args: True)
    monkeypatch.setattr(provisioner.store, "save_prompt_reference", lambda *args: calls.append("save"))
    provisioner._provision_batch("aib_test")
    assert calls == ["save", "create", "send", "create", "send"]
    calls.clear()

    def fail(*args):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(provisioner.store, "save_prompt_reference", fail)
    with pytest.raises(RuntimeError, match="storage unavailable"):
        provisioner._provision_batch("aib_test")
    assert calls == []
