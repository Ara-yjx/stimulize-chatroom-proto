"""Property tests for ``run_gate`` skip rules (min-silence and same-AI cooldown).

Validates: Correctness Properties §2 (Gate skip rules) from
``.kiro/specs/stimulize-chatroom-beta/design.md``:

  - For any history where the last visible event is within ``MIN_SILENCE_MS``
    of ``now``, ``run_gate`` returns ``skip=True, reason="min_silence_not_elapsed"``.
  - For any history where the last visible message is from an AI within
    ``SAME_AI_COOLDOWN_MS`` of ``now`` (and min-silence has elapsed for the
    overall last visible event), ``run_gate`` returns
    ``skip=True, reason="ai_just_spoke"``.

The two scenarios are kept in separate strategies so the two skip reasons
are exercised independently — sharing a strategy would require building the
"min-silence elapsed but AI cooldown not" condition, which is naturally
expressed as a second strategy.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from chatroom_api.constants import MIN_SILENCE_MS, SAME_AI_COOLDOWN_MS
from chatroom_api.gate import run_gate


# ---------------------------------------------------------------------------
# Property A: min-silence skip.
# ---------------------------------------------------------------------------


@st.composite
def _within_min_silence_scenario(draw):
    """Generate a conversation whose last visible event is within MIN_SILENCE_MS.

    We always populate at least one AI participant so ``run_gate`` doesn't
    short-circuit on ``no_ai_participants``, and we vary the sender of the
    last visible event between human and AI to exercise both branches —
    the min-silence skip happens *before* same-AI checks so this should
    fire regardless.
    """
    now = draw(st.integers(min_value=1_000_000, max_value=10_000_000))
    # gap < MIN_SILENCE_MS so the skip definitely fires. We use [0, MIN-1]
    # inclusive to cover the boundary cases.
    gap = draw(st.integers(min_value=0, max_value=MIN_SILENCE_MS - 1))
    last_visible_at = now - gap

    # Include some older events sometimes, to make the gate's
    # ``max(visible_at)`` over visible events do real work.
    extra_events_count = draw(st.integers(min_value=0, max_value=3))
    extra_events = [
        {
            "type": "message",
            "session_id": "human_001",
            "sender": "Earth",
            "role": "human",
            "content": f"older {i}",
            "timestamp": last_visible_at - (i + 1) * 10_000,
            "visible_at": last_visible_at - (i + 1) * 10_000,
        }
        for i in range(extra_events_count)
    ]

    sender_role = draw(st.sampled_from(["human", "ai"]))
    if sender_role == "human":
        last_event = {
            "type": "message",
            "session_id": "human_001",
            "sender": "Earth",
            "role": "human",
            "content": "recent",
            "timestamp": last_visible_at,
            "visible_at": last_visible_at,
        }
    else:
        last_event = {
            "type": "message",
            "session_id": "ai_001",
            "sender": "Mars",
            "role": "ai",
            "content": "recent",
            "timestamp": last_visible_at,
            "visible_at": last_visible_at,
        }

    return {
        "now": now,
        "events": extra_events + [last_event],
    }


@settings(max_examples=150, deadline=None)
@given(scenario=_within_min_silence_scenario())
def test_min_silence_skip(scenario: dict) -> None:
    """Validates: Correctness Properties §2 — min-silence skip.

    When the most recent visible event is within ``MIN_SILENCE_MS`` of
    ``now``, the gate must skip with reason ``"min_silence_not_elapsed"``.
    """
    conv = {
        "participants": [
            {"session_id": "human_001", "nickname": "Earth", "role": "human"},
            {"session_id": "ai_001", "nickname": "Mars", "role": "ai"},
        ],
        "events": scenario["events"],
        "last_speak_at_by_session": {},
    }
    decision = run_gate(conv, scenario["now"])

    assert decision.skip is True, (
        f"expected skip=True, got skip=False (events={scenario['events']!r})"
    )
    assert decision.reason == "min_silence_not_elapsed", (
        f"expected reason='min_silence_not_elapsed', got {decision.reason!r}"
    )


# ---------------------------------------------------------------------------
# Property B: same-AI cooldown skip.
# ---------------------------------------------------------------------------


@st.composite
def _ai_just_spoke_scenario(draw):
    """Generate a history where:

    - The last visible message is from an AI ``ai_001``.
    - Min-silence HAS elapsed (so the gate moves past the min-silence check).
    - The AI's ``last_speak_at`` is within ``SAME_AI_COOLDOWN_MS`` of ``now``.

    To satisfy both "min silence elapsed" (gap from last visible event >=
    MIN_SILENCE_MS) and "AI just spoke" (gap from AI's last_speak_at <
    SAME_AI_COOLDOWN_MS), we set:
      - last visible event's ``visible_at = now - MIN_SILENCE_MS - tail``
        with ``tail >= 0``;
      - AI's ``last_speak_at = now - cooldown_gap`` with
        ``0 <= cooldown_gap < SAME_AI_COOLDOWN_MS``.

    This works because the gate reads ``last_speak_at`` from
    ``last_speak_at_by_session`` independently from ``visible_at``.
    """
    now = draw(st.integers(min_value=10_000_000, max_value=100_000_000))
    silence_tail = draw(st.integers(min_value=0, max_value=10_000))
    last_visible_at = now - MIN_SILENCE_MS - silence_tail

    cooldown_gap = draw(st.integers(min_value=0, max_value=SAME_AI_COOLDOWN_MS - 1))
    last_speak_at = now - cooldown_gap

    # The last visible message must be from this AI to trigger the
    # same-AI-cooldown branch.
    events = [
        {
            "type": "message",
            "session_id": "ai_001",
            "sender": "Mars",
            "role": "ai",
            "content": "earlier",
            "timestamp": last_visible_at,
            "visible_at": last_visible_at,
        },
    ]

    return {
        "now": now,
        "events": events,
        "last_speak_map": {"ai_001": last_speak_at},
    }


@settings(max_examples=150, deadline=None)
@given(scenario=_ai_just_spoke_scenario())
def test_ai_just_spoke_skip(scenario: dict) -> None:
    """Validates: Correctness Properties §2 — same-AI cooldown skip.

    When the last visible message is from an AI whose ``last_speak_at`` is
    within ``SAME_AI_COOLDOWN_MS`` of ``now`` (and min-silence has elapsed),
    the gate must skip with reason ``"ai_just_spoke"``.
    """
    conv = {
        "participants": [
            {"session_id": "human_001", "nickname": "Earth", "role": "human"},
            {"session_id": "ai_001", "nickname": "Mars", "role": "ai"},
        ],
        "events": scenario["events"],
        "last_speak_at_by_session": scenario["last_speak_map"],
    }
    decision = run_gate(conv, scenario["now"])

    assert decision.skip is True, (
        f"expected skip=True, got skip=False "
        f"(last_speak_map={scenario['last_speak_map']!r}, now={scenario['now']!r})"
    )
    assert decision.reason == "ai_just_spoke", (
        f"expected reason='ai_just_spoke', got {decision.reason!r}"
    )
