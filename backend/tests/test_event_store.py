from chatroom_api import event_store


class FakeEventTable:
    def __init__(self):
        self.condition = None

    def query(self, **kwargs):
        self.condition = kwargs["KeyConditionExpression"]
        return {"Items": []}


def test_ascending_query_uses_one_between_sort_key_condition(monkeypatch):
    table = FakeEventTable()
    monkeypatch.setattr(event_store, "_event_table", table)

    events, has_more = event_store._query_ascending(
        "conv_one", "H#T0000000000000100#Bbatch#I000", "H#T0000000000000200#~", 10
    )

    expression = table.condition.get_expression()
    assert expression["operator"] == "AND"
    assert expression["values"][1].get_expression()["operator"] == "BETWEEN"
    assert events == []
    assert has_more is False
