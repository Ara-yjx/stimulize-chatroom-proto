"""Example tests for group-mode lobby corner cases.

Validates: Requirements 4.x — group-mode pairing edge cases from
``.kiro/specs/stimulize-chatroom-beta/requirements.md``:

- Freshness-check path closes a stale lobby (past ``deadline_at``) and the
  retry iteration creates a fresh lobby for the new joiner.
- Late joiner arriving while the prior lobby is still ``closing`` (capacity
  reached but the close subroutine has not finished) re-runs the loop and
  creates a brand-new open lobby.
- Aborted lobby surfaces as ``410 Gone`` via ``/chat/messages``. Skipped here
  because ``/chat/messages`` (task 3.4) and the 410 routing in ``handler.py``
  (task 3.5) are not yet implemented; this stub will be fleshed out then.

These complement the property-based suites in
``test_lobby_capacity_property.py`` and
``test_close_lobby_idempotency_property.py`` by pinning specific scenarios
called out in the design doc.
"""

from __future__ import annotations

from unittest.mock import patch

from chatroom_api import auth as auth_mod
from chatroom_api import config, mock_dynamo, mock_lobby, mock_rds


GROUP_CHATROOM_ID = "scid_pbt-group-examples"


def _group_setting(target_human_count: int, max_wait_seconds: int) -> dict:
    """Build a minimal but complete group-mode chatroom setting."""
    return {
        "mode": "group",
        "topic_instruction": "test topic",
        "model_id": "test-model",
        "simulate_pairing_seconds": 0,
        "timer_min_minutes": None,
        "timer_max_minutes": None,
        "target_human_count": target_human_count,
        "ai_join_strategy": "fixed_ai_count",
        "ai_strategy_value": 1,
        "max_wait_seconds": max_wait_seconds,
    }


def _seed_chatroom(target_human_count: int, max_wait_seconds: int) -> None:
    """Insert a group chatroom into ``mock_rds`` for the test."""
    mock_rds._chatrooms[GROUP_CHATROOM_ID] = {
        "id": GROUP_CHATROOM_ID,
        "owner_id": "user_pbt",
        "name": "PBT Group Examples",
        "status": "active",
        "setting": _group_setting(target_human_count, max_wait_seconds),
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
    }


class TestGroupLobbyExamples:
    """Class-based setup matches the convention in ``test_auth.py``."""

    def setup_method(self) -> None:
        # Force runtime mocks. Defaults are already True but set defensively
        # in case the test process inherits ``USE_MOCK_*=false`` from env.
        config.USE_MOCK_LOBBY = True
        config.USE_MOCK_DYNAMO = True
        config.USE_MOCK_RDS = True
        mock_lobby.reset()
        mock_dynamo._rooms.clear()

    def teardown_method(self) -> None:
        mock_rds._chatrooms.pop(GROUP_CHATROOM_ID, None)
        mock_lobby.reset()
        mock_dynamo._rooms.clear()

    # ------------------------------------------------------------------
    # 1. Freshness-check path.
    # ------------------------------------------------------------------

    def test_freshness_check_closes_stale_lobby_and_starts_conversation(self) -> None:
        """A second joiner arriving past ``deadline_at`` triggers freshness close.

        Flow exercised:
        - First joiner creates a lobby (status=open, deadline = first_join + 1s).
        - Time advances past the deadline.
        - Second joiner enters ``handle_auth_token``:
          - iteration 1: ``query_open_lobby`` finds the stale lobby; freshness
            check fires ``close_lobby`` (which writes the conversation row for
            the first lobby's ``conversation_id``) and ``continue``s.
          - iteration 2: no open lobby exists; auth creates a fresh lobby and
            the second joiner joins it.
        """
        _seed_chatroom(target_human_count=2, max_wait_seconds=1)

        t0 = 1_700_000_000.0  # arbitrary fixed unix-ish epoch seconds

        # --- First joiner at t0; lobby created with deadline = t0_ms + 1000ms.
        with patch.object(auth_mod.time, "time", return_value=t0):
            status, body = auth_mod.handle_auth_token(
                {"chatroom_id": GROUP_CHATROOM_ID}
            )
        assert status == 200, body
        assert body["lobby"]["status"] == "open"
        assert body["lobby"]["actual_human_count"] == 1
        first_conv_id = body["conversation_id"]

        # --- Second joiner at t0 + 2 (past deadline by 1s).
        with patch.object(auth_mod.time, "time", return_value=t0 + 2):
            status, body = auth_mod.handle_auth_token(
                {"chatroom_id": GROUP_CHATROOM_ID}
            )

        assert status == 200, body
        assert "lobby" in body
        assert body["lobby"]["status"] == "open"
        assert body["lobby"]["actual_human_count"] == 1
        # Freshness close created a new lobby with a fresh pre-allocated conv_id.
        second_conv_id = body["conversation_id"]
        assert second_conv_id != first_conv_id

        # --- Two lobbies in storage: the stale one (closed) + the new open one.
        all_lobbies = list(mock_lobby._lobbies.values())
        assert len(all_lobbies) == 2, all_lobbies

        statuses = {l["status"] for l in all_lobbies}
        assert statuses == {"closed", "open"}

        closed = next(l for l in all_lobbies if l["status"] == "closed")
        opened = next(l for l in all_lobbies if l["status"] == "open")

        # The closed one was the first joiner's lobby; the open one is fresh.
        assert closed["conversation_id"] == first_conv_id
        assert opened["conversation_id"] == second_conv_id
        assert opened["actual_human_count"] == 1
        assert closed["actual_human_count"] == 1

        # The conversation row for the first lobby was written by close_lobby
        # (1 human + ai_strategy_value=1 AI = 2 participants).
        first_participants = mock_dynamo.get_participants(first_conv_id)
        assert first_participants is not None
        assert len(first_participants) == 2
        assert {p["role"] for p in first_participants} == {"human", "ai"}

        # The new (still-open) lobby has not closed yet — no conversation row.
        assert mock_dynamo.get_participants(second_conv_id) is None

    # ------------------------------------------------------------------
    # 2. Late joiner during closing.
    # ------------------------------------------------------------------

    def test_late_joiner_during_closing_creates_new_lobby(self) -> None:
        """A joiner arriving while a prior lobby is ``closing`` forms a new lobby.

        ``query_open_lobby`` filters by ``status="open"`` so the closing lobby
        is invisible to a new caller, who therefore creates and joins a fresh
        lobby. Both lobbies coexist in storage afterward.
        """
        _seed_chatroom(target_human_count=2, max_wait_seconds=60)

        # First joiner → fresh open lobby with count 1/2.
        status, body = auth_mod.handle_auth_token(
            {"chatroom_id": GROUP_CHATROOM_ID}
        )
        assert status == 200, body
        assert body["lobby"]["status"] == "open"
        assert body["lobby"]["actual_human_count"] == 1
        first_conv_id = body["conversation_id"]

        first_lobby = next(iter(mock_lobby._lobbies.values()))
        first_lobby_id = first_lobby["lobby_id"]
        assert first_lobby["status"] == "open"

        # Simulate a closer that has flipped status open → closing but has not
        # yet finished writing the conversation row.
        flipped = mock_lobby.update_lobby_status(
            first_lobby_id,
            from_status="open",
            to_status="closing",
            now_ms=int(first_lobby["deadline_at"]) - 1,
        )
        assert flipped, "manual status flip to closing should succeed"

        # --- Second joiner: query_open_lobby returns None, so auth creates
        # a brand-new open lobby and joins.
        status, body = auth_mod.handle_auth_token(
            {"chatroom_id": GROUP_CHATROOM_ID}
        )
        assert status == 200, body
        assert body["lobby"]["status"] == "open"
        assert body["lobby"]["actual_human_count"] == 1
        second_conv_id = body["conversation_id"]
        assert second_conv_id != first_conv_id

        # Both lobbies coexist: the closing (still-not-closed) one and the new
        # open one for the late joiner.
        all_lobbies = list(mock_lobby._lobbies.values())
        assert len(all_lobbies) == 2, all_lobbies
        statuses = {l["status"] for l in all_lobbies}
        assert statuses == {"closing", "open"}

        closing = next(l for l in all_lobbies if l["status"] == "closing")
        opened = next(l for l in all_lobbies if l["status"] == "open")
        assert closing["lobby_id"] == first_lobby_id
        assert closing["conversation_id"] == first_conv_id
        assert opened["conversation_id"] == second_conv_id
        assert opened["actual_human_count"] == 1

        # Neither conversation row has been written yet — the closing lobby's
        # closer never finished, and the new open lobby is still at 1/2.
        assert mock_dynamo.get_participants(first_conv_id) is None
        assert mock_dynamo.get_participants(second_conv_id) is None

    # ------------------------------------------------------------------
    # 3. Aborted lobby → 410 (now wired up via tasks 3.4 / 3.5).
    # ------------------------------------------------------------------

    def test_aborted_lobby_returns_410_via_chat_messages(self) -> None:
        """Aborted lobby surfaces as 410 Gone on a ``/chat/messages`` poll.

        ``/chat/messages`` raises :class:`LobbyAbortedException` when the
        lobby for the JWT's ``conversation_id`` is in ``status="aborted"``;
        ``handler.py`` maps that exception to a 410 response.
        """
        from chatroom_api import handler as handler_mod
        from chatroom_api import jwt_utils

        _seed_chatroom(target_human_count=2, max_wait_seconds=60)

        # Create an open lobby for the chatroom and walk it to ``aborted``
        # the same way ``close_lobby`` would (open → closing → aborted).
        now_ms = 1_700_000_000_000
        conversation_id = "conv-aborted-example"
        session_id = "sess-aborted-example"
        lobby = mock_lobby.create_open_lobby(
            GROUP_CHATROOM_ID,
            {
                "target_human_count": 2,
                "ai_join_strategy": "fixed_ai_count",
                "ai_strategy_value": 1,
                "max_wait_seconds": 60,
            },
            conversation_id,
            now_ms,
        )
        assert mock_lobby.update_lobby_status(
            lobby["lobby_id"],
            from_status="open",
            to_status="closing",
            now_ms=now_ms,
        )
        assert mock_lobby.set_lobby_aborted(lobby["lobby_id"], now_ms)

        # Hit ``/chat/messages`` end-to-end via the Lambda router so the
        # exception → 410 mapping is exercised.
        token = jwt_utils.create_token(
            session_id, conversation_id, GROUP_CHATROOM_ID
        )
        event = {
            "httpMethod": "GET",
            "path": "/chat/messages",
            "headers": {"Authorization": f"Bearer {token}"},
            "body": None,
            "queryStringParameters": None,
        }
        resp = handler_mod.lambda_handler(event, None)
        assert resp["statusCode"] == 410
