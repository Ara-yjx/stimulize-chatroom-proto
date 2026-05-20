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
4. Write the conversation row via ``db.append_events`` (whose underlying
   ``put_item`` is conditional on ``attribute_not_exists(conversation_id)``,
   making this step idempotent under racing closers).
5. Flip lobby ``status`` from ``closing`` → ``closed`` and stamp ``closed_at``.

This module lives outside ``lobby.py`` so it can talk to *both* the lobby
store and the conversation store (via env-toggled real or mock backends),
keeping ``lobby.py`` focused on its DDB primitives.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from uuid import uuid4
from typing import Optional

from chatroom_api import config
from chatroom_api.constants import EMOJI_POOL
from chatroom_api.lobby import compute_ai_count

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


def _get_db():
    """Return the conversation-store module (real or mock) per ``USE_MOCK_DYNAMO``."""
    if config.USE_MOCK_DYNAMO:
        from chatroom_api import mock_dynamo
        return mock_dynamo
    from chatroom_api import dynamo
    return dynamo


def _get_rds():
    """Return the RDS module (real, mock, or management-API HTTP) per config."""
    from chatroom_api._providers import get_rds_provider
    return get_rds_provider()


# ---------------------------------------------------------------------------
# Local helpers.
#
# TODO: ``_generate_nickname`` and ``_pick_avatar`` are duplicated from
# ``auth.py``. Once a third caller appears, factor them into a
# ``participants.py`` helper module and import from both. Keeping the
# duplicate here for now keeps the diff for task 1.3 focused.
# ---------------------------------------------------------------------------


def _generate_nickname(exclude=None) -> str:
    """Generate ``Participant`` + 4 digits, avoiding nicknames in *exclude*."""
    exclude = exclude or set()
    while True:
        name = f"Participant{random.randint(1000, 9999)}"
        if name not in exclude:
            return name


def _pick_avatar(exclude=None) -> dict:
    """Pick a random emoji avatar, avoiding emojis in *exclude* when possible."""
    exclude = exclude or set()
    available = [e for e in EMOJI_POOL if e not in exclude]
    if not available:
        available = list(EMOJI_POOL)
    emoji = random.choice(available)
    return {"emojiText": emoji}


def _pick_personas(persona_pool: list, ai_count: int) -> list[str]:
    """Pick *ai_count* personas from *persona_pool* for one cohort.

    Behavior:
    - Empty pool → returns ``[""] * ai_count`` (no persona section in the
      prompt; scaffold's "build an identity as the conversation goes"
      rule takes over).
    - Pool with at least *ai_count* entries → sample without replacement
      so each AI in the same room gets a distinct persona.
    - Pool smaller than *ai_count* → sample without replacement first,
      then top up with random picks (with replacement) from the pool.
      Better than collisions on the first AIs while padding stays uniform.

    Non-string entries and empty/whitespace strings are dropped before
    sampling so a typo in the editor (e.g. ``[null, ""]``) doesn't
    silently inject blank personas.
    """
    cleaned = [
        str(p).strip()
        for p in (persona_pool or [])
        if isinstance(p, str) and str(p).strip()
    ]
    if not cleaned:
        return [""] * ai_count
    if ai_count <= 0:
        return []
    if len(cleaned) >= ai_count:
        return random.sample(cleaned, ai_count)
    distinct = random.sample(cleaned, len(cleaned))
    fill = [random.choice(cleaned) for _ in range(ai_count - len(cleaned))]
    return distinct + fill


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
    db = _get_db()
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
        chatroom_setting = chatroom["setting"]
    else:
        chatroom_setting = {
            "target_human_count": lobby.get("target_human_count"),
            "ai_join_strategy": lobby.get("ai_join_strategy"),
            "ai_strategy_value": lobby.get("ai_strategy_value"),
            "max_wait_seconds": lobby.get("max_wait_seconds"),
        }

    persona_pool = chatroom_setting.get("ai_personas") or []
    personas_for_ais = _pick_personas(persona_pool, ai_count)

    used_nicknames = {p.get("nickname") for p in participants_after_prune}
    used_emojis = {(p.get("avatar") or {}).get("emojiText") for p in participants_after_prune}

    ai_participants: list[dict] = []
    for i in range(ai_count):
        nickname = _generate_nickname(exclude=used_nicknames)
        avatar = _pick_avatar(exclude=used_emojis)
        used_nicknames.add(nickname)
        used_emojis.add(avatar["emojiText"])
        ai_participants.append({
            "session_id": "ai_" + uuid4().hex[:8],
            "nickname": nickname,
            "avatar": avatar,
            "role": "ai",
            "persona": personas_for_ais[i],
        })

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
    # ``lobby_created`` audit event captures the lobby start timestamp on
    # the conversation row for researcher debugging (how long did this
    # cohort wait?). It's filtered out of /chat/messages just like ticks
    # — admin can opt in via include_ticks=true if a future flag is added.
    lobby_created_iso = lobby.get("created_at") or now_iso
    lobby_created_ms = _iso_to_ms_safe(lobby_created_iso)
    events: list[dict] = [
        {
            "type": "lobby_created",
            "session_id": None,
            "sender": "System",
            "role": "system",
            "ai_participant_id": None,
            "content": "Lobby created",
            "timestamp": lobby_created_ms,
            "visible_at": lobby_created_ms,
            "created_at": lobby_created_iso,
            "lobby_id": lobby_id,
            "target_human_count": lobby.get("target_human_count"),
            "ai_join_strategy": lobby.get("ai_join_strategy"),
            "ai_strategy_value": lobby.get("ai_strategy_value"),
            "max_wait_seconds": lobby.get("max_wait_seconds"),
        },
        {
            "type": "system",
            "session_id": None,
            "sender": "System",
            "role": "system",
            "ai_participant_id": None,
            "content": "Conversation started",
            "timestamp": now_ms,
            "visible_at": now_ms,
            "created_at": now_iso,
        }
    ]
    for i, p in enumerate(participants):
        ts = now_ms + 1 + i  # space the join events 1ms apart
        events.append({
            "type": "system",
            "session_id": p["session_id"],
            "sender": "System",
            "role": "system",
            "ai_participant_id": p["session_id"] if p.get("role") == "ai" else None,
            "content": f"{p['nickname']} joined",
            "timestamp": ts,
            "visible_at": ts,
            "created_at": now_iso,
        })

    # Tick-model fields land on the row at creation time. ``last_tick_at=0``
    # makes the very first heartbeat-driven tick eligible (any positive
    # ``now_ms - dedupe_window_ms`` threshold beats 0). ``started_at`` is the
    # ISO timestamp the tick handler compares against ``max_duration_seconds``.
    db.append_events(
        conversation_id,
        chatroom_id,
        events,
        chatroom_setting=chatroom_setting,
        participants=participants,
        status="active",
        started_at=now_iso,
        last_tick_at=0,
        last_speak_at_by_session={},
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
