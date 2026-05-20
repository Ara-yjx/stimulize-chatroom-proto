"""Property tests for ``compute_ai_count``.

Validates the formal "compute_ai_count shape" property from
``.kiro/specs/stimulize-chatroom-beta/design.md`` (Correctness Properties §1):

  For all H >= 0, value >= 0:
    - compute_ai_count("fixed_ai_count", value, H)         == value
    - compute_ai_count("total_participant_count", value, H) == max(0, value - H)
    - For "total_participant_count": result + H == max(value, H)

Plus a single example test for the unknown-strategy error path. That last
test isn't a property — it lives next to the property tests for coherence.
"""

from hypothesis import given, strategies as st
import pytest

from chatroom_api.lobby import compute_ai_count


# Bound the ranges to keep test runs fast while still covering the
# arithmetic interactions: H both below and above value, plus zeros.
_NON_NEG = st.integers(min_value=0, max_value=1000)


@given(value=_NON_NEG, h=_NON_NEG)
def test_fixed_ai_count_independent_of_h(value: int, h: int) -> None:
    """Validates: Property 1a — fixed_ai_count returns value regardless of H."""
    assert compute_ai_count("fixed_ai_count", value, h) == value


@given(value=_NON_NEG, h=_NON_NEG)
def test_total_participant_count_formula(value: int, h: int) -> None:
    """Validates: Property 1b — total_participant_count returns max(0, value - H)."""
    assert compute_ai_count("total_participant_count", value, h) == max(0, value - h)


@given(value=_NON_NEG, h=_NON_NEG)
def test_total_participant_count_room_total(value: int, h: int) -> None:
    """Validates: Property 1c — for total_participant_count, ai + H == max(value, H)."""
    result = compute_ai_count("total_participant_count", value, h)
    assert result + h == max(value, h)


def test_unknown_strategy_raises_value_error() -> None:
    """Example: an unrecognized strategy must raise ValueError (not silently default)."""
    with pytest.raises(ValueError):
        compute_ai_count("unknown_strategy", 1, 1)
