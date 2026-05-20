"""Property tests for ``close_lobby`` idempotency under racing closers.

Validates: Correctness Properties §6.a (close_lobby idempotency) from
``.kiro/specs/stimulize-chatroom-beta/design.md``:

  - ``close_lobby`` called twice (or N times concurrently) on the same lobby
    produces the same final DDB state.
  - At most one ``chatroom-conversations`` ``PutItem`` succeeds — i.e. the
    conversation row's ``events`` list contains exactly one
    ``"Conversation started"`` system event and exactly one ``"<nick> joined"``
    system event per participant (humans + AIs).

This complements ``test_lobby_capacity_property.py`` (the
``RuleBasedStateMachine`` exercising arbitrary join/freshness/close
sequences). Here we focus narrowly on the *racing-closer* scenario: the
lobby is set up at full capacity, then ``num_callers`` threads call
``close_lobby`` simultaneously (synchronized via ``threading.Barrier``).
Exactly one caller should observe ``"closed"``; all others should observe
``"already_closed"`` and leave DDB untouched.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from hypothesis import given, settings, strategies as st

from chatroom_api import close_lobby as close_lobby_mod
from chatroom_api import config, mock_dynamo, mock_lobby, mock_rds


CHATROOM_ID = "scid_pbt-close-idempotency"


def _full_chatroom_setting(
    target_human_count: int,
    ai_strategy_value: int = 1,
    max_wait_seconds: int = 60,
) -> dict:
    return {
        "mode": "group",
        "topic_instruction": "test topic",
        "model_id": "test-model",
        "simulate_pairing_seconds": 0,
        "timer_min_minutes": None,
        "timer_max_minutes": None,
        "target_human_count": target_human_count,
        "ai_join_strategy": "fixed_ai_count",
        "ai_strategy_value": ai_strategy_value,
        "max_wait_seconds": max_wait_seconds,
    }


def _setup(target_human_count: int, num_joiners: int) -> dict:
    """Reset shared mocks and create an open lobby with *num_joiners* humans.

    Returns the freshly-created lobby item (status still ``open``).
    """
    # Force runtime mocks; defaults are already True but set defensively in
    # case the test process inherits ``USE_MOCK_*=false`` from the shell env.
    config.USE_MOCK_LOBBY = True
    config.USE_MOCK_DYNAMO = True
    config.USE_MOCK_RDS = True

    mock_lobby.reset()
    mock_dynamo._rooms.clear()

    setting = _full_chatroom_setting(target_human_count=target_human_count)
    mock_rds._chatrooms[CHATROOM_ID] = {
        "id": CHATROOM_ID,
        "owner_id": "user_pbt",
        "name": "PBT close-idempotency",
        "status": "active",
        "setting": setting,
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
    }

    lobby = mock_lobby.create_open_lobby(
        chatroom_id=CHATROOM_ID,
        setting=setting,
        conversation_id="conv-pbt-" + uuid.uuid4().hex,
        now_ms=0,
    )
    for i in range(num_joiners):
        participant = {
            "session_id": f"sess-pbt-{i}",
            "nickname": f"Participant{1000 + i:04d}",
            "avatar": {"emojiText": "🐱"},
            "joined_at": 0,
            "last_seen_at": 0,
        }
        success, _ = mock_lobby.join_lobby(lobby["lobby_id"], participant, now_ms=0)
        assert success, f"setup join {i} failed unexpectedly"
    return lobby


@settings(max_examples=30, deadline=None)
@given(
    target_human_count=st.integers(min_value=1, max_value=5),
    # Up to 8 racing closers — enough to exercise contention without
    # blowing test runtime past a few seconds.
    num_callers=st.integers(min_value=1, max_value=8),
    num_joiners_offset=st.integers(min_value=0, max_value=4),
)
def test_close_lobby_idempotency_under_racing_closers(
    target_human_count: int,
    num_callers: int,
    num_joiners_offset: int,
) -> None:
    """Validates: Correctness Properties §6.a.

    For any (target_human_count, num_callers, num_joiners): N concurrent
    ``close_lobby`` calls produce identical post-state and exactly one
    effective conversation write.
    """
    # Clamp joiners so we always have at least one human (avoids the
    # "aborted" branch which is forward-compat in beta) and never exceed
    # the lobby's target.
    num_joiners = 1 + (num_joiners_offset % target_human_count)
    lobby = _setup(target_human_count, num_joiners)
    lobby_id = lobby["lobby_id"]
    conversation_id = lobby["conversation_id"]

    barrier = threading.Barrier(num_callers)

    def _race_close() -> str:
        # All threads block here until everyone is ready, so the
        # close attempts fire as close to simultaneously as the
        # scheduler allows. Mock_lobby's internal Lock then serializes
        # the actual conditional flips — exactly the property we want
        # to characterize.
        barrier.wait()
        return close_lobby_mod.close_lobby(lobby_id, now_ms=1000)

    with ThreadPoolExecutor(max_workers=num_callers) as pool:
        outcomes = list(pool.map(lambda _: _race_close(), range(num_callers)))

    # --- Outcome shape: exactly one "closed", rest "already_closed".
    closed_count = outcomes.count("closed")
    already_count = outcomes.count("already_closed")
    aborted_count = outcomes.count("aborted")
    assert closed_count == 1, (
        f"expected exactly 1 'closed' outcome, got {closed_count} "
        f"(outcomes={outcomes!r})"
    )
    assert aborted_count == 0, (
        f"unexpected 'aborted' outcome with {num_joiners} joiners "
        f"(outcomes={outcomes!r})"
    )
    assert already_count == num_callers - 1, (
        f"expected {num_callers - 1} 'already_closed' outcomes, got "
        f"{already_count} (outcomes={outcomes!r})"
    )

    # --- Final lobby state.
    final_lobby = mock_lobby.get_lobby(lobby_id)
    assert final_lobby is not None
    assert final_lobby["status"] == "closed", (
        f"expected lobby status='closed', got {final_lobby['status']!r}"
    )

    # --- Conversation row exists exactly once.
    participants = mock_dynamo.get_participants(conversation_id)
    assert participants is not None, "conversation row was never written"

    # Humans + AIs (ai_count = 1 from the fixed-AI strategy with value=1).
    expected_total = num_joiners + 1
    assert len(participants) == expected_total, (
        f"expected {expected_total} participants, got {len(participants)}"
    )
    # Sanity check: human session_ids are exactly the ones we joined.
    human_sessions = {p["session_id"] for p in participants if p.get("role") == "human"}
    assert human_sessions == {f"sess-pbt-{i}" for i in range(num_joiners)}
    ai_sessions = [p for p in participants if p.get("role") == "ai"]
    assert len(ai_sessions) == 1, (
        f"expected exactly 1 AI participant, got {len(ai_sessions)}"
    )

    # --- Events list shape: 1 conversation-started + N "joined" events.
    events = mock_dynamo.get_events(conversation_id, after=0)
    started = [e for e in events if e.get("content") == "Conversation started"]
    joined = [e for e in events if e.get("content", "").endswith(" joined")]
    assert len(started) == 1, (
        f"expected exactly 1 'Conversation started' event, got {len(started)}"
    )
    assert len(joined) == expected_total, (
        f"expected {expected_total} 'joined' events (one per participant), "
        f"got {len(joined)}"
    )
    # All "joined" events should be for participants we know about.
    joined_nicks = {e["content"].rsplit(" joined", 1)[0] for e in joined}
    expected_nicks = {p["nickname"] for p in participants}
    assert joined_nicks == expected_nicks


@settings(max_examples=30, deadline=None)
@given(
    target_human_count=st.integers(min_value=1, max_value=5),
    num_extra_calls=st.integers(min_value=0, max_value=5),
)
def test_close_lobby_sequential_post_state_is_stable(
    target_human_count: int,
    num_extra_calls: int,
) -> None:
    """Validates: Correctness Properties §6.a (sequential idempotency).

    Calling ``close_lobby`` repeatedly after the first successful close is a
    no-op: no new events are appended, the participants list is unchanged,
    and lobby ``status`` stays ``closed``. This is the sequential complement
    to the concurrent test above.
    """
    lobby = _setup(target_human_count, num_joiners=target_human_count)
    lobby_id = lobby["lobby_id"]
    conversation_id = lobby["conversation_id"]

    # First close — should win.
    first = close_lobby_mod.close_lobby(lobby_id, now_ms=1000)
    assert first == "closed"

    snapshot_lobby = mock_lobby.get_lobby(lobby_id)
    snapshot_events = mock_dynamo.get_events(conversation_id, after=0)
    snapshot_participants = mock_dynamo.get_participants(conversation_id)
    assert snapshot_lobby is not None
    assert snapshot_participants is not None

    for i in range(num_extra_calls):
        outcome = close_lobby_mod.close_lobby(lobby_id, now_ms=1000 + i + 1)
        assert outcome == "already_closed", (
            f"extra call {i} returned {outcome!r}, expected 'already_closed'"
        )
        # State must not drift.
        assert mock_lobby.get_lobby(lobby_id) == snapshot_lobby
        assert mock_dynamo.get_events(conversation_id, after=0) == snapshot_events
        assert mock_dynamo.get_participants(conversation_id) == snapshot_participants
