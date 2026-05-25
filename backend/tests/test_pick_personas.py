"""Tests for chatroom_api.close_lobby._pick_personas."""

from __future__ import annotations

import random

import pytest

from chatroom_api.close_lobby import _pick_personas


@pytest.fixture(autouse=True)
def deterministic_random():
    """Seed each test so without-replacement sampling is stable."""
    random.seed(0)
    yield


class TestPickPersonas:

    def test_empty_pool_returns_empty_strings(self):
        result = _pick_personas([], 3)
        assert result == ["", "", ""]

    def test_zero_ai_count_returns_empty_list(self):
        result = _pick_personas(["a", "b", "c"], 0)
        assert result == []

    def test_pool_larger_than_count_samples_without_replacement(self):
        pool = ["a", "b", "c", "d", "e"]
        result = _pick_personas(pool, 3)
        assert len(result) == 3
        assert len(set(result)) == 3, "should be all distinct"
        assert all(p in pool for p in result)

    def test_pool_equal_to_count_samples_without_replacement(self):
        pool = ["a", "b", "c"]
        result = _pick_personas(pool, 3)
        assert sorted(result) == sorted(pool)

    def test_pool_smaller_than_count_uses_full_rounds_then_partial_round(self):
        pool = ["a", "b"]
        result = _pick_personas(pool, 5)
        assert len(result) == 5
        # First two assignments are one full round of the pool.
        assert set(result[:2]) == {"a", "b"}
        # Next two assignments are another full round of the pool.
        assert set(result[2:4]) == {"a", "b"}
        # Final partial round draws from the same pool.
        assert result[4] in pool

    def test_multiple_full_rounds_before_random_remainder(self):
        pool = ["a", "b", "c"]
        result = _pick_personas(pool, 7)
        assert len(result) == 7
        assert set(result[:3]) == {"a", "b", "c"}
        assert set(result[3:6]) == {"a", "b", "c"}
        assert result[6] in pool

    def test_drops_non_string_and_blank_entries(self):
        # None, empty, whitespace-only should be filtered out.
        pool = [None, "", "   ", "real-persona", 42]
        result = _pick_personas(pool, 1)  # type: ignore[arg-type]
        assert result == ["real-persona"]

    def test_all_blank_pool_treated_as_empty(self):
        pool = ["", "  ", None]  # type: ignore[list-item]
        result = _pick_personas(pool, 2)  # type: ignore[arg-type]
        assert result == ["", ""]

    def test_object_entries_round_robin_without_losing_model_binding(self):
        pool = [
            {"persona": "persona-a", "model_id": "model-a"},
            {"persona": "persona-b", "model_id": "model-b"},
        ]
        result = _pick_personas(pool, 5)
        assert len(result) == 5
        assert {item["persona"] for item in result[:2]} == {"persona-a", "persona-b"}
        assert {item["model_id"] for item in result[:2]} == {"model-a", "model-b"}
        assert {item["persona"] for item in result[2:4]} == {"persona-a", "persona-b"}
        assert result[4]["persona"] in {"persona-a", "persona-b"}
