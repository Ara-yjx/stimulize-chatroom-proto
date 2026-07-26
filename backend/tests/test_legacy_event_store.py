from chatroom_api import _providers, config, event_store, legacy_event_store


def test_provider_requires_explicit_event_storage_enable(monkeypatch):
    monkeypatch.setattr(config, "USE_MOCK_DYNAMO", False)
    monkeypatch.setattr(config, "EVENT_STORAGE_ENABLED", False)
    assert _providers.get_event_store_provider() is legacy_event_store

    monkeypatch.setattr(config, "EVENT_STORAGE_ENABLED", True)
    assert _providers.get_event_store_provider() is event_store


def test_legacy_adapter_pages_embedded_history_with_new_cursors(monkeypatch):
    monkeypatch.setattr(
        legacy_event_store.dynamo,
        "get_conversation",
        lambda _conversation_id: {
            "events": [
                {
                    "type": "message",
                    "role": "human",
                    "content": "first",
                    "timestamp": 100,
                    "visible_at": 100,
                },
                {
                    "type": "message",
                    "role": "ai",
                    "content": "delayed",
                    "timestamp": 110,
                    "visible_at": 200,
                },
            ]
        },
    )

    first = legacy_event_store.query_live_after("conv_one", None, 150, 10)
    assert [event["content"] for event in first["events"]] == ["first"]
    assert first["next_after"]

    second = legacy_event_store.query_live_after(
        "conv_one", first["next_after"], 250, 10
    )
    assert [event["content"] for event in second["events"]] == ["delayed"]
    assert second["events"][0]["timestamp"] == 200
    assert legacy_event_store.query_next_pending("conv_one", None, 150) == 200

    history = legacy_event_store.query_history_before("conv_one", None, 250, 1)
    assert [event["content"] for event in history["events"]] == ["delayed"]
    assert history["has_more"] is True
