from chatroom_api import config, tick_handler


def test_maintenance_tick_stops_before_dynamodb(monkeypatch):
    monkeypatch.setattr(config, "CHATROOM_SERVICE_MODE", "maintenance")

    def fail_get_db():
        raise AssertionError("maintenance tick must not read DynamoDB")

    monkeypatch.setattr(tick_handler, "_get_db", fail_get_db)

    assert tick_handler.handle_tick({"conversation_id": "conv_1"}) == {
        "status": "maintenance"
    }
