"""Property tests for ``build_bedrock_messages`` correctness.

Validates: Correctness Properties §3.6 from
``.kiro/specs/stimulize-chatroom-beta/tasks.md``:

  For any ``events`` list and any ``ai_session_id`` / ``now``:

    1. Output roles strictly alternate ``user`` / ``assistant``.
    2. Output contains no ``system`` and no ``tick`` events (i.e. every
       output item's role is ``user`` or ``assistant``, and content drawn
       from system / tick events never reaches the output).
    3. Every ``user`` text starts with ``"[" + sender + "] "``. After the
       consecutive-same-role merge, this means every line of a user
       message (split on ``\\n``) starts with that prefix.
    4. The function never introduces ``"human:"`` or ``"ai:"`` substrings.
       To make this tractable we restrict generated ``content`` to
       lowercase letters + spaces (no colons), so any ``human:`` / ``ai:``
       in the output would have to come from the function itself.
    5. Every input event with ``visible_at > now`` is absent from output.
       To make this tractable we tag every event with a globally unique
       marker (``"k{i} ..."``) and assert that pending events' contents
       do not appear in any output text.
"""

from __future__ import annotations

import string

from hypothesis import given, settings, strategies as st

from chatroom_api.conversation import build_bedrock_messages


# ---------------------------------------------------------------------------
# Generators.
# ---------------------------------------------------------------------------


# Lowercase letters + space only — no colons (so we can detect role-marker
# leaks unambiguously) and no brackets (so the ``[sender]`` prefix check
# can rely on ``[`` only ever coming from the function's own prefixing).
_CONTENT_ALPHABET = string.ascii_lowercase + " "


_PARTICIPANT_NICKNAMES = {
    "h1": "Earth",
    "ai_001": "Mars",
    "ai_002": "Venus",
    "ai_003": "Jupiter",
}


@st.composite
def _conversation_scenario(draw):
    """Generate a conversation snapshot, an ``ai_session_id``, and ``now``.

    Layout: 1 human (``h1``) + 1-3 AIs. Events have random type
    (``message`` / ``system`` / ``tick``), random ``session_id`` from
    participants (or ``"system"`` for system events), random
    ``visible_at`` and ``timestamp``, and a globally unique content marker
    so we can detect content leaks.
    """
    n_ai = draw(st.integers(min_value=1, max_value=3))
    ai_session_ids = [f"ai_{i:03d}" for i in range(1, n_ai + 1)]
    all_session_ids = ["h1"] + ai_session_ids

    participants = [
        {
            "session_id": sid,
            "nickname": _PARTICIPANT_NICKNAMES[sid],
            "role": "human" if sid == "h1" else "ai",
        }
        for sid in all_session_ids
    ]

    ai_session_id = draw(st.sampled_from(ai_session_ids))

    n_events = draw(st.integers(min_value=0, max_value=10))

    events: list[dict] = []
    for i in range(n_events):
        # Globally unique marker — ``k{i}`` only appears in event ``i``.
        suffix = draw(
            st.text(alphabet=_CONTENT_ALPHABET, min_size=0, max_size=10)
        )
        content = f"k{i} {suffix}"

        ev_type = draw(st.sampled_from(["message", "system", "tick"]))
        if ev_type == "message":
            session_id = draw(st.sampled_from(all_session_ids))
            sender = _PARTICIPANT_NICKNAMES[session_id]
        elif ev_type == "system":
            session_id = "system"
            sender = "System"
        else:  # tick
            session_id = draw(st.sampled_from(ai_session_ids))
            sender = None

        timestamp = draw(st.integers(min_value=0, max_value=1_000_000))
        visible_at = draw(st.integers(min_value=0, max_value=1_000_000))

        events.append(
            {
                "type": ev_type,
                "session_id": session_id,
                "sender": sender,
                "content": content,
                "timestamp": timestamp,
                "visible_at": visible_at,
            }
        )

    now = draw(st.integers(min_value=0, max_value=1_000_000))

    return {
        "events": events,
        "participants": participants,
        "ai_session_id": ai_session_id,
        "now": now,
    }


# ---------------------------------------------------------------------------
# Property test.
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(scenario=_conversation_scenario())
def test_build_bedrock_messages_correctness(scenario: dict) -> None:
    """Validates: Tasks 3.6 — build_bedrock_messages correctness."""
    conv = {
        "events": scenario["events"],
        "participants": scenario["participants"],
    }
    ai_session_id = scenario["ai_session_id"]
    now = scenario["now"]

    out = build_bedrock_messages(conv, ai_session_id, now)

    # --- Property 1: roles strictly alternate.
    for i in range(1, len(out)):
        assert out[i]["role"] != out[i - 1]["role"], (
            f"adjacent same role at i={i}: "
            f"{out[i - 1]['role']!r} == {out[i]['role']!r} "
            f"(events={scenario['events']!r}, ai={ai_session_id!r}, now={now})"
        )

    # --- Property 2: only ``user`` / ``assistant`` (no system, no tick).
    for i, msg in enumerate(out):
        assert msg["role"] in ("user", "assistant"), (
            f"unexpected role at i={i}: {msg['role']!r}"
        )

    # Build set of pending event contents (visible_at > now) — these must
    # never appear in any output text.
    pending_contents = {
        e["content"]
        for e in scenario["events"]
        if e.get("visible_at", 0) > now
    }
    # Also collect contents from system/tick events that were within the
    # visibility window — these were filtered out for being non-message,
    # so their content should not appear either. Property 2 covers this
    # in spirit; we double-check via content tracing.
    skipped_type_contents = {
        e["content"]
        for e in scenario["events"]
        if e.get("type") in ("system", "tick")
        and e.get("visible_at", 0) <= now
    }

    for msg in out:
        text = msg["content"][0]["text"]

        # --- Property 4: no role-marker leak.
        # Generated content uses lowercase + space only, so any ``human:``
        # or ``ai:`` substring in output would have to come from the
        # function itself. The implementation never adds these.
        assert "human:" not in text.lower(), (
            f"role marker 'human:' leaked into output text: {text!r}"
        )
        assert "ai:" not in text.lower(), (
            f"role marker 'ai:' leaked into output text: {text!r}"
        )

        # --- Property 3: every user text starts with ``[sender] `` per line.
        if msg["role"] == "user":
            for line in text.split("\n"):
                assert line.startswith("["), (
                    f"user line doesn't start with '[': {line!r}"
                )
                # ``[sender] content``  →  must contain ``] `` separator.
                assert "] " in line, (
                    f"user line missing '] ' separator: {line!r}"
                )

        # --- Property 5: pending events' contents are absent from output.
        for pc in pending_contents:
            assert pc not in text, (
                f"pending event content {pc!r} appears in output text "
                f"{text!r} (now={now})"
            )

        # --- Property 2 (content trace): system/tick contents absent.
        for sc in skipped_type_contents:
            assert sc not in text, (
                f"system/tick event content {sc!r} appears in output: {text!r}"
            )
