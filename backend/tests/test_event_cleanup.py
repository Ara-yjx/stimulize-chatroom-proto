from chatroom_api import event_cleanup


class FakeBatch:
    def __init__(self):
        self.deleted = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def delete_item(self, Key):
        self.deleted.append(Key)


class FakeTable:
    def __init__(self):
        self.batch = FakeBatch()
        self.calls = 0

    def batch_writer(self):
        return self.batch

    def query(self, **kwargs):
        self.calls += 1
        assert kwargs["ConsistentRead"] is True
        if self.calls == 1:
            return {
                "Items": [{"conversation_id": "conv", "event_key": "one"}],
                "LastEvaluatedKey": {"conversation_id": "conv", "event_key": "one"},
            }
        assert "ExclusiveStartKey" in kwargs
        return {"Items": [{"conversation_id": "conv", "event_key": "two"}]}


def test_cleanup_handles_remove_records_and_query_pagination(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(event_cleanup, "_table", table)

    result = event_cleanup.handler({"Records": [
        {"eventName": "MODIFY", "dynamodb": {"Keys": {"conversation_id": {"S": "skip"}}}},
        {"eventName": "REMOVE", "dynamodb": {"Keys": {"conversation_id": {"S": "conv"}}}},
    ]}, None)

    assert result == {"cleaned": [{"conversation_id": "conv", "deleted_events": 2}]}
    assert table.batch.deleted == [
        {"conversation_id": "conv", "event_key": "one"},
        {"conversation_id": "conv", "event_key": "two"},
    ]
