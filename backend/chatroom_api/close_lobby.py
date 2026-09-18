"""``close_lobby(lobby_id, now_ms)`` — idempotent lobby-close subroutine.

Implements the 5-step procedure in
``docs/low-level-design.md#close_lobbylobby--idempotent-subroutine``:

1. Flip lobby ``status`` from ``open`` → ``closing`` via a conditional
   ``UpdateItem``. Losing the race means another closer is already running;
   exit silently and return ``"already_closed"``.
2. Re-read the (now-fresh) lobby. Beta: prune is a no-op — the participants
   list is copied as-is. If empty, mark the lobby ``aborted`` and return.
3. Compute ``ai_count`` per the configured strategy and generate that many
   AI participants with unique nicknames + avatars.
4. Transactionally create conversation metadata and participant-visible
   history through the event store.
5. Flip lobby ``status`` from ``closing`` → ``closed`` and stamp ``closed_at``.

This module lives outside ``lobby.py`` so it can talk to *both* the lobby
store and the conversation store (via env-toggled real or mock backends),
keeping ``lobby.py`` focused on its DDB primitives.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5
from typing import Optional

from chatroom_api import config
from chatroom_api._providers import get_event_store_provider
from chatroom_api.ai_participants import (
    build_ai_participants,
    generate_nickname as _generate_nickname,
    pick_avatar as _pick_avatar,
    pick_personas as _pick_personas,
)
from chatroom_api.lobby import compute_ai_count
from chatroom_api.settings import (
    resolve_runtime_setting,
)
from chatroom_api.prompt_attachments import prepare_setting, AttachmentError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend selection (mirrors the auth.py pattern).
# ---------------------------------------------------------------------------


def _get_lobby():
    """Return the lobby module (real or mock) per ``USE_MOCK_LOBBY``."""
    if config.USE_MOCK_LOBBY:
        from chatroom_api import mock_lobby
        return mock_lobby
    from chatroom_api import lobby
    return lobby


def _get_rds():
    """Return the RDS module (real, mock, or management-API HTTP) per config."""
    from chatroom_api._providers import get_rds_provider
    return get_rds_provider()


def _get_event_store():
    return get_event_store_provider()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_to_ms_safe(iso: str) -> int:
    """Best-effort ISO 8601 → epoch ms.

    Falls back to the current time if parsing fails — the lobby_created
    audit event must always carry a valid timestamp so consumers can sort.
    """
    if not isinstance(iso, str) or not iso:
        return int(datetime.now(timezone.utc).timestamp() * 1000)
    try:
        return int(
            datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000
        )
    except (ValueError, TypeError):
        return int(datetime.now(timezone.utc).timestamp() * 1000)


# ---------------------------------------------------------------------------
# The procedure.
# ---------------------------------------------------------------------------


def close_lobby(lobby_id: str, now_ms: int) -> str:
    """Close *lobby_id* idempotently.

    Returns one of:
    - ``"closed"`` — this caller drove the lobby through the full close.
    - ``"aborted"`` — post-prune participants list was empty (forward-compat;
      not reachable in beta since the lobby is created on the first join).
    - ``"already_closed"`` — another closer won the open→closing race; this
      call was a no-op.
    """
    lobby_mod = _get_lobby()
    history_store = _get_event_store()
    rds_mod = _get_rds()

    # --- Step 1: flip status open -> closing.
    won = lobby_mod.update_lobby_status(
        lobby_id,
        from_status="open",
        to_status="closing",
        now_ms=now_ms,
    )
    if not won:
        # Another closer is already (or has finished) handling this lobby.
        return "already_closed"

    # --- Step 2: re-read; beta prune is a no-op.
    lobby = lobby_mod.get_lobby(lobby_id)
    if lobby is None:
        # Should not happen: we just flipped its status. Treat as a no-op.
        return "already_closed"

    participants_after_prune = list(lobby.get("participants") or [])

    if len(participants_after_prune) == 0:
        # Forward-compat branch: only reachable once pruning is enabled.
        lobby_mod.set_lobby_aborted(lobby_id, now_ms)
        return "aborted"

    # --- Step 3: compute and generate AI participants.
    h = len(participants_after_prune)
    ai_count = compute_ai_count(
        lobby["ai_join_strategy"],
        int(lobby["ai_strategy_value"]),
        h,
    )

    chatroom_id = lobby["chatroom_id"]
    # Fetch the full chatroom setting now (rather than at step 4) so the
    # AI generation loop can pick from the researcher-supplied
    # ``ai_personas`` pool. Falls back to the lobby's own subset if the
    # chatroom disappeared (e.g. researcher deleted it mid-cohort).
    chatroom = rds_mod.get_chatroom(chatroom_id)
    if chatroom is not None and chatroom.get("setting") is not None:
        chatroom_setting = resolve_runtime_setting(prepare_setting(chatroom, rds_mod))
    else:
        if lobby.get('has_prompt_attachments'):
            raise AttachmentError('Chatroom attachment settings are unavailable')
        chatroom_setting = resolve_runtime_setting({
            "target_human_count": lobby.get("target_human_count"),
            "ai_join_strategy": lobby.get("ai_join_strategy"),
            "ai_strategy_value": lobby.get("ai_strategy_value"),
            "max_wait_seconds": lobby.get("max_wait_seconds"),
        })

    ai_participants = build_ai_participants(
        chatroom_setting,
        ai_count,
        existing_participants=participants_after_prune,
    )

    # --- Step 4: build conversation row + events; idempotent put.
    humans = [
        {
            **{k: v for k, v in p.items() if k not in ("joined_at", "last_seen_at")},
            "role": "human",
        }
        for p in participants_after_prune
    ]
    participants = humans + ai_participants
    conversation_id = lobby["conversation_id"]

    now_iso = _now_iso()
    events: list[dict] = [
        {
            "type": "system",
            "subtype": "conversation_started",
            "sender": "System",
            "role": "system",
            "content": "Conversation started",
            "timestamp": now_ms,
            "created_at": now_iso,
        }
    ]
    for i, p in enumerate(participants):
        ts = now_ms + 1 + i  # space the join events 1ms apart
        events.append({
            "type": "system",
            "subtype": "participant_joined",
            "sender": "System",
            "role": "system",
            "content": f"{p['nickname']} joined",
            "timestamp": ts,
            "created_at": now_iso,
        })

    # Tick-model fields land on the row at creation time. ``last_tick_at=0``
    # makes the very first heartbeat-driven tick eligible (any positive
    # ``now_ms - dedupe_window_ms`` threshold beats 0). ``started_at`` is the
    # ISO timestamp the tick handler compares against ``max_duration_seconds``.
    creation_batch_id = uuid5(
        NAMESPACE_URL, f"stimulize:conversation-create:{conversation_id}"
    ).hex
    history_store.create_conversation(
        {
            "conversation_id": conversation_id,
            "chatroom_id": chatroom_id,
            "chatroom_setting": chatroom_setting,
            "participants": participants,
            "status": "active",
            "started_at": now_iso,
            "last_tick_at": 0,
            "ai_tick_state_by_participant_id": {},
            "next_actionable_tick_at": 0,
        },
        events,
        creation_batch_id,
    )
    logger.info(
        "lobby_closed conversation_id=%s lobby_id=%s humans=%s ais=%s wait_ms=%s",
        conversation_id,
        lobby_id,
        len(humans),
        len(ai_participants),
        max(0, now_ms - _iso_to_ms_safe(lobby.get("created_at") or now_iso)),
    )

    # --- Step 5: closing -> closed; stamp closed_at.
    lobby_mod.update_lobby_status(
        lobby_id,
        from_status="closing",
        to_status="closed",
        now_ms=now_ms,
        extra_set={"closed_at": now_iso},
    )

    return "closed"
