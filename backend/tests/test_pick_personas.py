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

    def test_pool_smaller_than_count_first_distinct_then_padded(self):
        pool = ["a", "b"]
        result = _pick_personas(pool, 5)
        assert len(result) == 5
        # First two must be the entire pool (in some order — distinct).
        assert set(result[:2]) == {"a", "b"}
        # The remaining three come from the pool with replacement.
        assert all(p in pool for p in result[2:])

    def test_drops_non_string_and_blank_entries(self):
        # None, empty, whitespace-only should be filtered out.
        pool = [None, "", "   ", "real-persona", 42]
        result = _pick_personas(pool, 1)  # type: ignore[arg-type]
        assert result == ["real-persona"]

    def test_all_blank_pool_treated_as_empty(self):
        pool = ["", "  ", None]  # type: ignore[list-item]
        result = _pick_personas(pool, 2)  # type: ignore[arg-type]
        assert result == ["", ""]
