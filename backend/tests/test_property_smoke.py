"""Smoke property test to verify Hypothesis is wired up.

This is a placeholder: it asserts a trivial mathematical identity over
arbitrary integers so that ``pytest -k property`` discovers at least one
property-based test. Real property tests will replace or live alongside
this file (see tasks.md sections 1, 2, 3).
"""

from hypothesis import given, strategies as st


@given(st.integers(), st.integers())
def test_addition_is_commutative_property(a: int, b: int) -> None:
    """Validates: smoke — addition is commutative for any two integers."""
    assert a + b == b + a
