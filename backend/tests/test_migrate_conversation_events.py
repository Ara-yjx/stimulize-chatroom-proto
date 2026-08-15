import importlib.util
import json
from pathlib import Path

import pytest


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

    metadata = migration.migrate_metadata(_conversation(), 500)
    assert metadata["participants"][1]["ai_participant_id"] == "ai_old"
    assert metadata["ai_tick_state_by_participant_id"]["ai_old"]["last_result"] == "silent"
    assert metadata["ai_tick_state_by_participant_id"]["ai_old"]["next_actionable_at"] == 500
    assert metadata["next_actionable_tick_at"] == 500
    assert metadata["event_storage_version"] == 1
    assert metadata["events"] == _conversation()["events"]


class ScanTable:
    def __init__(self, items):
        self.items = items
        self.get_calls = 0

    def scan(self, **_kwargs):
        return {"Items": self.items}

    def get_item(self, Key, ConsistentRead=False):
        assert ConsistentRead is True
        self.get_calls += 1
        item = next(
            (
                row for row in self.items
                if row.get("conversation_id") == Key["conversation_id"]
            ),
            None,
        )
        return {"Item": item} if item else {}


class EventWriteTable:
    def __init__(self, items=None):
        self.items = list(items or [])

    def query(self, **_kwargs):
        return {"Items": list(self.items)}

    def batch_writer(self):
        table = self

        class Writer:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def put_item(self, Item):
                table.items.append(Item)

        return Writer()


def test_plan_hash_includes_cutover_and_malformed_rows_block_apply(tmp_path):
    source = ScanTable([
        _conversation(),
        {
            "conversation_id": "conv_bad",
            "participants": [],
            "events": [{"type": "message", "timestamp": "not-a-number"}],
        },
    ])

    first = migration.build_plan(source, 500)
    second = migration.build_plan(source, 600)

    assert first["plan_hash"] != second["plan_hash"]
    assert first["source_row_count"] == 2
    assert first["issues"] == [{
        "conversation_id": "conv_bad",
        "error": "events[0] has invalid timestamp",
    }]
    with pytest.raises(RuntimeError, match="malformed"):
        migration.apply_plan(first, object(), object(), tmp_path / "checkpoint.json")


def test_verify_checks_events_metadata_and_preserves_legacy_list():
    plan = migration.build_plan(ScanTable([_conversation()]), 500)
    metadata = ScanTable([plan["conversations"][0]["metadata"]])
    events = ScanTable(plan["conversations"][0]["events"])

    result = migration.verify_plan(plan, metadata, events)

    assert result == {
        "conversation_count": 1,
        "event_count": 3,
        "legacy_events_preserved": True,
    }
    assert metadata.get_calls == 1


def test_batch_event_write_is_idempotent_and_rejects_conflicts():
    plan = migration.build_plan(ScanTable([_conversation()]), 500)
    expected = plan["conversations"][0]["events"]
    table = EventWriteTable(expected[:1])

    migration._put_events_idempotently(table, "conv_one", expected)
    assert table.items == expected

    migration._put_events_idempotently(table, "conv_one", expected)
    assert table.items == expected

    table.items[0] = {**table.items[0], "content": "conflict"}
    with pytest.raises(RuntimeError, match="event payload conflict"):
        migration._put_events_idempotently(table, "conv_one", expected)


def test_verify_rejects_extra_event_partition():
    plan = migration.build_plan(ScanTable([_conversation()]), 500)
    metadata = ScanTable([plan["conversations"][0]["metadata"]])
    events = ScanTable([
        *plan["conversations"][0]["events"],
        {
            "conversation_id": "conv_extra",
            "event_key": "H#extra",
            "timestamp": 1,
        },
    ])

    with pytest.raises(RuntimeError, match="extra event partitions"):
        migration.verify_plan(plan, metadata, events)


def test_report_contains_aggregate_and_raw_issues(tmp_path):
    plan = migration.build_plan(
        ScanTable([{"conversation_id": "bad", "events": "wrong"}]),
        500,
    )
    report = tmp_path / "report.json"

    migration._write_report(str(report), plan, "dry-run")

    payload = json.loads(report.read_text())
    assert payload["issue_count"] == 1
    assert payload["issues"][0]["conversation_id"] == "bad"


def test_protected_beta_tables_are_rejected_before_aws_access(capsys):
    result = migration.main([
        "--source-table", "chatroom-conversations",
        "--target-metadata-table", "safe-dev-metadata",
        "--target-event-table", "safe-dev-events",
        "--region", "us-east-2",
        "--cutover-at-ms", "500",
    ])
    assert result == 2
    assert "refusing protected table" in capsys.readouterr().err
