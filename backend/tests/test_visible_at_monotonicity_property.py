"""Property tests for ``compute_visible_at`` strict monotonicity.

Validates: Correctness Properties §3 (`visible_at` strict monotonicity)
from ``.kiro/specs/stimulize-chatroom-beta/design.md``:

  For any non-empty ``delays`` array with each ``d ∈ [MIN_DELAY_MS,
  MAX_DELAY_MS]``, ``compute_visible_at(t0, delays)`` returns a list whose:
    - length matches ``len(delays)``;
    - first element is ``>= t0 + MIN_DELAY_MS``;
    - every element is ``>= t0 + MIN_DELAY_MS``;
    - is strictly increasing with each consecutive gap ``>= MIN_DELAY_MS``.

The strict monotonicity follows from each delay being ``>= MIN_DELAY_MS >
0``: cumulative sums of strictly positive integers are strictly increasing.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from chatroom_api.constants import MAX_DELAY_MS, MIN_DELAY_MS
from chatroom_api.delays import compute_visible_at


_DELAYS = st.lists(
    st.integers(min_value=MIN_DELAY_MS, max_value=MAX_DELAY_MS),
    min_size=1,
    max_size=8,
)
# t0 is unconstrained — the function adds delays as integer ms with no
# overflow concern in Python, so any reasonable epoch ms range is fine.
_T0 = st.integers(min_value=0, max_value=10**13)


@settings(max_examples=200, deadline=None)
@given(t0=_T0, delays=_DELAYS)
def test_compute_visible_at_strict_monotonicity(t0: int, delays: list[int]) -> None:
    """Validates: Correctness Properties §3 — visible_at monotonicity."""
    result = compute_visible_at(t0, delays)

    # Length matches.
    assert len(result) == len(delays), (
        f"length mismatch: len(result)={len(result)} vs len(delays)={len(delays)}"
    )

    # First element bound: cumulative sum after one delay >= MIN_DELAY_MS.
    assert result[0] >= t0 + MIN_DELAY_MS, (
        f"result[0]={result[0]} < t0+MIN_DELAY_MS={t0 + MIN_DELAY_MS}"
    )

    # Every element bound (subsumed by the strictly-increasing check, but
    # checked explicitly so a regression on the first element is obvious).
    for i, v in enumerate(result):
        assert v >= t0 + MIN_DELAY_MS, (
            f"result[{i}]={v} < t0+MIN_DELAY_MS={t0 + MIN_DELAY_MS}"
        )

    # Strict monotonicity with min gap >= MIN_DELAY_MS.
    for i in range(1, len(result)):
        gap = result[i] - result[i - 1]
        assert gap >= MIN_DELAY_MS, (
            f"gap between result[{i-1}]={result[i-1]} and result[{i}]={result[i]} "
            f"is {gap}, expected >= MIN_DELAY_MS={MIN_DELAY_MS}"
        )
