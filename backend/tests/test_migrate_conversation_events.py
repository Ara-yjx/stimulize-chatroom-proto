import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "migrate_conversation_events.py"
SPEC = importlib.util.spec_from_file_location("migrate_conversation_events", SCRIPT)
migration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migration)


def _conversation():
    return {
        "conversation_id": "conv_one",
        "status": "active",
        "participants": [
            {"role": "human", "session_id": "human_one", "nickname": "One"},
            {"role": "ai", "session_id": "ai_old", "nickname": "AI"},
        ],
        "events": [
            {
                "type": "message",
                "role": "human",
                "session_id": "human_one",
                "sender": "One",
                "content": "hello",
                "timestamp": 100,
                "visible_at": 100,
            },
            {
                "type": "tick",
                "role": "system",
                "session_id": "ai_old",
                "timestamp": 110,
                "ai_decision": "silent",
            },
            {
                "type": "message",
                "role": "ai",
                "session_id": "ai_old",
                "sender": "AI",
                "content": "hi",
                "timestamp": 120,
                "visible_at": 200,
            },
            {
                "type": "system",
                "role": "system",
                "session_id": "human_one",
                "sender": "System",
                "content": "ended",
                "timestamp": 200,
            },
        ],
    }


def test_history_migration_drops_audit_and_normalizes_authors_and_times():
    items, dropped = migration.migrate_history(_conversation())

    assert dropped == {"tick": 1}
    assert [item["content"] for item in items] == ["hello", "hi", "ended"]
    ai = items[1]
    assert ai["timestamp"] == 200
    assert ai["authored_at"] == 120
    assert ai["ai_participant_id"] == "ai_old"
    assert "session_id" not in ai
    system = items[2]
    assert "session_id" not in system
    assert items[1]["event_key"] < items[2]["event_key"]


def test_migration_is_deterministic_and_derives_tick_projection():
    first, _ = migration.migrate_history(_conversation())
    second, _ = migration.migrate_history(_conversation())
    assert first == second

    metadata = migration.migrate_metadata(_conversation())
    assert metadata["participants"][1]["ai_participant_id"] == "ai_old"
    assert metadata["ai_tick_state_by_participant_id"]["ai_old"]["last_result"] == "silent"
    assert metadata["event_storage_version"] == 1
    assert metadata["events"] == _conversation()["events"]


def test_protected_beta_tables_are_rejected_before_aws_access(capsys):
    result = migration.main([
        "--source-table", "chatroom-conversations",
        "--target-metadata-table", "safe-dev-metadata",
        "--target-event-table", "safe-dev-events",
        "--region", "us-east-2",
    ])
    assert result == 2
    assert "refusing protected table" in capsys.readouterr().err
