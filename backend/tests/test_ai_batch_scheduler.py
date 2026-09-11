import random

import pytest

from chatroom_api.ai_batch.scheduler import candidates_for_turn, forced_candidate


def _participants(count: int) -> list[dict]:
    return [
        {"role": "ai", "ai_participant_id": f"ai-{index}"}
        for index in range(count)
    ]


def test_two_ai_scheduler_strictly_alternates() -> None:
    participants = _participants(2)
    assert candidates_for_turn(participants, "ai-0") == [participants[1]]
    assert candidates_for_turn(participants, "ai-1") == [participants[0]]


def test_multi_ai_scheduler_excludes_previous_and_shuffles_all_others() -> None:
    participants = _participants(4)
    candidates = candidates_for_turn(participants, "ai-2", random.Random(42))
    assert len(candidates) == 3
    assert {item["ai_participant_id"] for item in candidates} == {"ai-0", "ai-1", "ai-3"}
    assert forced_candidate(candidates, random.Random(7)) in candidates


def test_scheduler_rejects_degenerate_conversation() -> None:
    with pytest.raises(ValueError, match="at least two"):
        candidates_for_turn(_participants(1), None)
