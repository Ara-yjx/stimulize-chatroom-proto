import io
import json
import zipfile
from decimal import Decimal

from chatroom_api.ai_batch import exporter


def test_export_includes_only_completed_conversations(monkeypatch) -> None:
    batch = {
        "batch_job_id": "batch",
        "chatroom_id": "room",
        "status": "partial_failure",
        "batch_count": 2,
        "completed_count": 1,
        "failed_count": 1,
        "timed_out_count": 0,
        "unfinished_count": 0,
    }

    def get_conversation(conversation_id: str):
        if conversation_id == exporter.conversation_id("batch", 0):
            return {
                "status": "completed",
                "participants": [{"nickname": "AI", "internal_name": "condition", "avatar": {"emojiText": "legacy"}}],
            }
        return {"status": "failed"}

    monkeypatch.setattr(exporter.store, "get_conversation", get_conversation)
    monkeypatch.setattr(exporter.store, "query_history", lambda _id: [{
        "type": "message",
        "timestamp": Decimal("1789147117914"),
        "sender": "AI",
        "internal_name": "condition",
        "content": "hello",
        "avatar": {"emojiText": "legacy"},
    }])

    body, manifest = exporter.build_export_archive(batch)

    assert manifest["included_count"] == 1
    assert manifest["omitted_count"] == 1
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "conversations/0001.json" in names
        assert "conversations/0001.txt" in names
        assert "conversations/0002.json" not in names
        text = archive.read("conversations/0001.txt").decode()
        assert text == "AI (condition): hello\n"
        saved_conversation = json.loads(archive.read("conversations/0001.json"))
        assert 'avatar' not in saved_conversation['participants'][0]
        assert 'avatar' not in saved_conversation['events'][0]
        saved_timestamp = saved_conversation["events"][0]["timestamp"]
        assert saved_timestamp == 1789147117914
        assert isinstance(saved_timestamp, int)
        assert "authored_at" not in saved_conversation["events"][0]
        saved_manifest = json.loads(archive.read("manifest.json"))
        assert saved_manifest["outcome_counts"]["failed_count"] == 1


def test_text_export_omits_timestamps_for_all_event_types() -> None:
    assert exporter._render_text([
        {"type": "system", "timestamp": 123, "content": "Conversation started"},
        {"type": "message", "timestamp": 456, "sender": "Alex", "content": "Hi"},
        {"type": "message", "timestamp": 789, "content": "Hello\nthere"},
    ]) == "System: Conversation started\nAlex: Hi\nParticipant: Hello\nthere\n"
    assert exporter._render_text([]) == ""
