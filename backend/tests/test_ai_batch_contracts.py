import json

import pytest

from chatroom_api.ai_batch.contracts import (
    batch_job_id,
    canonical_request_hash,
    conversation_id,
    parse_queue_body,
    terminal_batch_status,
    work_message,
)


def test_ids_and_request_hash_are_stable_and_scoped() -> None:
    assert batch_job_id("7", "request-1") == batch_job_id("7", "request-1")
    assert batch_job_id("7", "request-1") != batch_job_id("8", "request-1")
    batch_id = batch_job_id("7", "request-1")
    assert conversation_id(batch_id, 0) == conversation_id(batch_id, 0)
    assert conversation_id(batch_id, 0) != conversation_id(batch_id, 1)
    assert canonical_request_hash({"b": 2, "a": 1}) == canonical_request_hash({"a": 1, "b": 2})


def test_queue_contract_requires_version_and_fields() -> None:
    message = work_message("batch", "conversation", 4)
    assert parse_queue_body(
        json.dumps(message),
        ("batch_job_id", "conversation_id", "expected_turn"),
    ) == message
    with pytest.raises(ValueError, match="version"):
        parse_queue_body('{"version":2}', ())
    with pytest.raises(ValueError, match="missing"):
        parse_queue_body('{"version":1}', ("batch_job_id",))


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({"completed_count": 2}, "completed"),
        ({"failed_count": 2}, "failed"),
        ({"timed_out_count": 2}, "timed_out"),
        ({"completed_count": 1, "failed_count": 1}, "partial_failure"),
    ],
)
def test_terminal_batch_status(counts: dict, expected: str) -> None:
    assert terminal_batch_status(counts) == expected
