"""Property tests for the lobby capacity invariant and close-write idempotency.

Validates: Correctness Properties §4 (Lobby capacity invariant) from
``.kiro/specs/stimulize-chatroom-beta/design.md``.

Two invariants are checked after every rule transition under the Hypothesis
``RuleBasedStateMachine``:

1. **Capacity** — for any lobby snapshot,
   ``actual_human_count <= target_human_count``.
2. **At-most-one conversation write** — for any sequence of join /
   freshness_tick / close rules, the row keyed by the lobby's pre-allocated
   ``conversation_id`` is written **at most once**. The test tracks a
   ``self.conversation_written`` monotonic flag (only ever flips False → True
   when a ``close_lobby`` call returns ``"closed"``) and asserts it stays in
   sync with ``mock_dynamo.get_participants(conversation_id) is not None``.

The runtime mocks (``mock_lobby``, ``mock_dynamo``, ``mock_rds``) implement
the same conditional-update semantics as the real DDB code paths, so the
state machine exercises the idempotent guards (``status open→closing`` flip
and ``attribute_not_exists`` on the conversation put) without needing AWS.
"""

from __future__ import annotations

import uuid

from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from chatroom_api import close_lobby as close_lobby_mod
from chatroom_api import config, mock_dynamo, mock_lobby, mock_rds


# Fixed instance config — Hypothesis state machines use one config per run;
# the strategy/value can be hardcoded since randomness comes from the rule
# sequence (and the random ``delta_ms`` applied per rule).
TARGET_HUMAN_COUNT = 3
# Small enough that ``freshness_tick`` rules (delta up to 20s) reliably push
# the virtual clock past ``deadline_at`` and exercise the freshness path.
MAX_WAIT_SECONDS = 5
TEST_CHATROOM_ID = "scid_pbt-lobby-capacity"

_GROUP_SETTING_MIN = {
    "target_human_count": TARGET_HUMAN_COUNT,
    "ai_join_strategy": "fixed_ai_count",
    "ai_strategy_value": 1,
    "max_wait_seconds": MAX_WAIT_SECONDS,
}

_FULL_CHATROOM_SETTING = {
    "mode": "group",
    "topic_instruction": "test topic",
    "model_id": "test-model",
    "simulate_pairing_seconds": 0,
    "timer_min_minutes": None,
    "timer_max_minutes": None,
    **_GROUP_SETTING_MIN,
}


class LobbyStateMachine(RuleBasedStateMachine):
    """Drive ``mock_lobby`` + ``close_lobby`` through random rule sequences."""

    def __init__(self) -> None:
        super().__init__()
        # Force runtime mocks. Defaults in ``config.py`` are already True, but
        # set the booleans defensively in case the test process inherits
        # ``USE_MOCK_*=false`` from the environment.
        config.USE_MOCK_LOBBY = True
        config.USE_MOCK_DYNAMO = True
        config.USE_MOCK_RDS = True

        # Reset both stores. ``mock_dynamo`` has no public ``reset()`` so we
        # clear its module-level dict directly (per task instructions).
        mock_lobby.reset()
        mock_dynamo._rooms.clear()

        # Seed a fake chatroom in mock_rds whose ``setting`` matches the
        # group config we used to create the lobby, so ``close_lobby``'s
        # RDS lookup succeeds and snapshots the right setting.
        mock_rds._chatrooms[TEST_CHATROOM_ID] = {
            "id": TEST_CHATROOM_ID,
            "owner_id": "user_pbt",
            "name": "PBT Lobby",
            "status": "active",
            "setting": _FULL_CHATROOM_SETTING,
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
        }

        self.now_ms = 0
        self.conversation_id = "conv-pbt-" + uuid.uuid4().hex
        lobby = mock_lobby.create_open_lobby(
            chatroom_id=TEST_CHATROOM_ID,
            setting=_GROUP_SETTING_MIN,
            conversation_id=self.conversation_id,
            now_ms=self.now_ms,
        )
        self.lobby_id: str = lobby["lobby_id"]
        self.conversation_written = False
        self._next_session_seq = 0

    def teardown(self) -> None:
        """Clean up shared module state so other tests aren't polluted."""
        mock_rds._chatrooms.pop(TEST_CHATROOM_ID, None)
        mock_lobby.reset()
        mock_dynamo._rooms.clear()

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    def _advance_clock(self, delta_ms: int) -> None:
        # Always strictly advance so timestamps are monotonic.
        self.now_ms += max(1, int(delta_ms))

    def _new_session_id(self) -> str:
        self._next_session_seq += 1
        return f"sess-pbt-{self._next_session_seq}"

    def _record_close_outcome(self, outcome: str) -> None:
        # The flag flips only one direction (False → True) when we observe a
        # successful close that wrote the conversation row. The
        # ``at_most_one_conversation_write`` invariant verifies this stays in
        # lockstep with the actual DDB state.
        if outcome == "closed":
            self.conversation_written = True

    # ------------------------------------------------------------------
    # Rules.
    # ------------------------------------------------------------------

    @rule(delta_ms=st.integers(min_value=1, max_value=2000))
    def join(self, delta_ms: int) -> None:
        """Try to join a fresh participant; close synchronously on capacity."""
        self._advance_clock(delta_ms)
        session_id = self._new_session_id()
        participant = {
            "session_id": session_id,
            "nickname": f"Participant{self._next_session_seq:04d}",
            "avatar": {"emojiText": "🐱"},
            "joined_at": self.now_ms,
            "last_seen_at": self.now_ms,
        }
        success, lobby_after = mock_lobby.join_lobby(
            self.lobby_id, participant, self.now_ms
        )
        if success and lobby_after is not None:
            actual = int(lobby_after.get("actual_human_count", 0))
            target = int(lobby_after.get("target_human_count", 0))
            if actual >= target:
                # Capacity reached — mirror auth.py's synchronous close.
                outcome = close_lobby_mod.close_lobby(self.lobby_id, self.now_ms)
                self._record_close_outcome(outcome)

    @rule(delta_ms=st.integers(min_value=500, max_value=20_000))
    def freshness_tick(self, delta_ms: int) -> None:
        """Advance the clock; if past ``deadline_at``, close the lobby."""
        self._advance_clock(delta_ms)
        lobby = mock_lobby.get_lobby(self.lobby_id)
        if lobby is None:
            return
        if lobby.get("status") != "open":
            return
        if self.now_ms < int(lobby.get("deadline_at", 0)):
            return
        outcome = close_lobby_mod.close_lobby(self.lobby_id, self.now_ms)
        self._record_close_outcome(outcome)

    @rule(delta_ms=st.integers(min_value=1, max_value=2000))
    def close(self, delta_ms: int) -> None:
        """Force a close attempt regardless of lobby status (racing closer)."""
        self._advance_clock(delta_ms)
        outcome = close_lobby_mod.close_lobby(self.lobby_id, self.now_ms)
        self._record_close_outcome(outcome)

    # ------------------------------------------------------------------
    # Invariants — checked after every rule.
    # ------------------------------------------------------------------

    @invariant()
    def capacity_invariant(self) -> None:
        """``actual_human_count <= target_human_count`` for any lobby state."""
        lobby = mock_lobby.get_lobby(self.lobby_id)
        if lobby is None:
            return
        actual = int(lobby.get("actual_human_count", 0))
        target = int(lobby.get("target_human_count", 0))
        assert actual <= target, (
            f"capacity invariant violated: actual={actual} > target={target} "
            f"(status={lobby.get('status')!r})"
        )

    @invariant()
    def at_most_one_conversation_write(self) -> None:
        """Conversation row exists ⟺ a prior ``close_lobby`` returned ``closed``.

        Combined with the monotonic flag (only ever flips False → True), this
        catches both *missing* writes (e.g. close said it succeeded but no row
        appeared) and *unexpected* writes (e.g. a row appeared without the
        canonical ``"closed"`` return).
        """
        participants = mock_dynamo.get_participants(self.conversation_id)
        row_exists = participants is not None
        # If we ever observed a ``"closed"`` outcome, the row must persist
        # (i.e. ``conversation_written`` stays True for the rest of the run
        # because the flag is write-once-True).
        assert row_exists == self.conversation_written, (
            f"row_exists={row_exists} but conversation_written="
            f"{self.conversation_written} for conversation_id="
            f"{self.conversation_id!r}"
        )


# Cap runtime: 50 examples × 30 steps each ≈ 1500 rule invocations.
LobbyStateMachine.TestCase.settings = settings(
    max_examples=50,
    stateful_step_count=30,
    deadline=None,
)


# Pytest discovers ``TestCase`` subclasses, so this exposes the state machine
# as a regular test class.
TestLobbyCapacity = LobbyStateMachine.TestCase
