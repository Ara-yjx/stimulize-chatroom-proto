import base64
import json

import pytest

from chatroom_api.cursors import (
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
    event_key_timestamp,
    make_event_key,
)


def _encoded(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_event_key_preserves_timestamp_batch_and_index_order():
    keys = [
        make_event_key(9, "batch_b", 0),
        make_event_key(10, "batch_a", 1),
        make_event_key(10, "batch_a", 0),
    ]

    assert sorted(keys) == [keys[0], keys[2], keys[1]]
    assert event_key_timestamp(keys[1]) == 10


def test_cursor_round_trip_is_scoped_to_conversation():
    event_key = make_event_key(123, "batch", 4)
    cursor = encode_cursor("conv_one", event_key)

    assert decode_cursor(cursor, "conv_one")["event_key"] == event_key
    with pytest.raises(InvalidCursorError, match="mismatch"):
        decode_cursor(cursor, "conv_two")


@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64",
        _encoded([]),
        _encoded({"v": 2, "stream": "history", "conversation_id": "conv", "event_key": "x"}),
        _encoded({"v": 1, "stream": "audit", "conversation_id": "conv", "event_key": "x"}),
        _encoded({"v": 1, "stream": "history", "conversation_id": "conv", "event_key": "x"}),
    ],
)
def test_invalid_cursor_payloads_are_rejected(cursor):
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor, "conv")


def test_event_key_rejects_unsafe_batch_id():
    with pytest.raises(ValueError, match="URL-safe"):
        make_event_key(1, "not safe", 0)
