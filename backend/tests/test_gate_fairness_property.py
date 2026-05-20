"""Property tests for ``run_gate`` fairness candidate selection.

Validates: Correctness Properties §2 (Gate fairness) from
``.kiro/specs/stimulize-chatroom-beta/design.md``:

  Given a conversation where the silence threshold is met and at least one
  AI candidate is **not** "still typing", ``run_gate`` selects the AI with
  the smallest ``last_speak_at_by_session`` value (deterministic tie-break
  by ``session_id``). Excluded "still-typing" AIs are never chosen.

The strategy generates scenarios with:
- 1-4 AI participants (deterministic session_ids ``ai_001``…``ai_00n``).
- Per-AI ``last_speak_at_by_session`` values drawn from a small overlapping
  range so tie-break by ``session_id`` is exercised in many examples.
- A subset of AIs marked "still typing" (their last batch's
  ``max(visible_at) > now``) — at least one AI is left non-typing so a
  candidate exists.
- A single human-authored visible message at ``t0`` so the last visible
  message is from a human (avoids the same-AI cooldown skip).
- ``now = t0 + MIN_SILENCE_MS + offset`` with ``offset >= 0`` so the
  min-silence check passes deterministically.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from chatroom_api.constants import MIN_SILENCE_MS
from chatroom_api.gate import run_gate


@st.composite
def _gate_fairness_scenario(draw):
    n_ais = draw(st.integers(min_value=1, max_value=4))
    ai_session_ids = [f"ai_{i:03d}" for i in range(1, n_ais + 1)]

    # At least one AI must be a usable candidate, so still-typing is at most
    # n_ais - 1. Drawing a unique subset of ``ai_session_ids``.
    still_typing = draw(
        st.lists(
            st.sampled_from(ai_session_ids),
            unique=True,
            min_size=0,
            max_size=n_ais - 1,
        )
    )
    still_typing_set = set(still_typing)

    # Small overlapping value range exercises the session_id tiebreak in
    # many examples (multiple AIs sharing the same last_speak_at).
    last_speak_map = {
        sid: draw(st.integers(min_value=0, max_value=5))
        for sid in ai_session_ids
    }

    t0 = draw(st.integers(min_value=1_000_000, max_value=10_000_000))
    silence_offset = draw(st.integers(min_value=0, max_value=100_000))
    now = t0 + MIN_SILENCE_MS + silence_offset

    # How far in the future each still-typing AI's last batch is visible.
    typing_offsets = {
        sid: draw(st.integers(min_value=1, max_value=10_000))
        for sid in still_typing
    }

    return {
        "ai_session_ids": ai_session_ids,
        "still_typing": still_typing_set,
        "last_speak_map": last_speak_map,
        "t0": t0,
        "now": now,
        "typing_offsets": typing_offsets,
    }


def _build_conv(scenario: dict) -> dict:
    """Translate a scenario into a conversation row for ``run_gate``."""
    participants = [
        {"session_id": "human_001", "nickname": "Earth", "role": "human"},
    ]
    for sid in scenario["ai_session_ids"]:
        participants.append({
            "session_id": sid,
            "nickname": f"AI-{sid}",
            "role": "ai",
        })

    events: list[dict] = [
        # Human-authored visible message at t0. Keeps last visible message
        # from being an AI (which would trip the same-AI cooldown skip).
        {
            "type": "message",
            "session_id": "human_001",
            "sender": "Earth",
            "role": "human",
            "content": "hello",
            "timestamp": scenario["t0"],
            "visible_at": scenario["t0"],
        },
    ]
    # For each still-typing AI, append a message whose visible_at is in the
    # future (relative to ``now``) so the gate's exclusion rule kicks in.
    for sid, offset in scenario["typing_offsets"].items():
        events.append({
            "type": "message",
            "session_id": sid,
            "sender": f"AI-{sid}",
            "role": "ai",
            "content": "typing",
            # ``timestamp`` is the authoring time; ``visible_at`` is in the
            # future so the AI is still "typing" from the gate's POV.
            "timestamp": scenario["t0"] + 1,
            "visible_at": scenario["now"] + offset,
        })

    return {
        "participants": participants,
        "events": events,
        "last_speak_at_by_session": dict(scenario["last_speak_map"]),
    }


@settings(max_examples=200, deadline=None)
@given(scenario=_gate_fairness_scenario())
def test_gate_picks_smallest_last_speak_with_session_tiebreak(scenario: dict) -> None:
    """Validates: Correctness Properties §2 — gate fairness.

    For any scenario with >= 1 non-still-typing AI, ``run_gate``:
      - returns ``skip=False``;
      - picks the candidate with smallest ``(last_speak_at, session_id)``;
      - never picks an AI that is "still typing".
    """
    conv = _build_conv(scenario)
    decision = run_gate(conv, scenario["now"])

    eligible = [
        sid for sid in scenario["ai_session_ids"]
        if sid not in scenario["still_typing"]
    ]
    # Sanity: scenario generator guarantees at least one eligible AI.
    assert eligible, "scenario must produce at least one non-still-typing AI"

    assert decision.skip is False, (
        f"expected skip=False, got skip=True (reason={decision.reason!r})"
    )
    assert decision.candidate_session_id is not None
    assert decision.candidate_session_id not in scenario["still_typing"], (
        f"chosen candidate {decision.candidate_session_id!r} is still typing "
        f"(still_typing={sorted(scenario['still_typing'])})"
    )

    # Expected pick: smallest (last_speak_at, session_id) over eligible AIs.
    last_speak_map = scenario["last_speak_map"]
    expected = min(
        eligible,
        key=lambda sid: (int(last_speak_map.get(sid, 0)), sid),
    )
    assert decision.candidate_session_id == expected, (
        f"expected candidate {expected!r}, got {decision.candidate_session_id!r}; "
        f"eligible={eligible}, last_speak_map={last_speak_map}"
    )
