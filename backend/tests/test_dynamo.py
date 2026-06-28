from decimal import Decimal

from chatroom_api.dynamo import _to_dynamodb_safe


def test_to_dynamodb_safe_converts_nested_floats_to_decimal():
    value = {
        "temperature": 0.7,
        "participants": [
            {
                "session_id": "s1",
                "temperature": 0.4,
                "nested": {"values": [1, 0.25, "ok"]},
            }
        ],
    }

    converted = _to_dynamodb_safe(value)

    assert converted["temperature"] == Decimal("0.7")
    assert converted["participants"][0]["temperature"] == Decimal("0.4")
    assert converted["participants"][0]["nested"]["values"] == [
        1,
        Decimal("0.25"),
        "ok",
    ]
