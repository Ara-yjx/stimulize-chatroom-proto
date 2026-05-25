"""POST /auth/token handler — validate chatroom via RDS, return JWT + session info.

Two modes are supported:

- ``one_on_one`` (default, backward compatible with v2 chatrooms): one human
  + one AI per session; conversation row is pre-created here.
- ``group``: humans wait in a lobby until either capacity is reached or the
  ``max_wait_seconds`` deadline elapses. Conversation row is created
  exclusively inside ``close_lobby`` (idempotent), not here.

Dispatch happens in :func:`handle_auth_token`.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from uuid import uuid4

from chatroom_api import config, jwt_utils
from chatroom_api.close_lobby import close_lobby
from chatroom_api.constants import EMOJI_POOL
from chatroom_api.errors import ChatroomNotFoundException, InactiveChatroomException

# Hard cap on the group-mode join retry loop. Each iteration either advances
# (joins or creates+joins) or moves a stale lobby toward closing/closed via
# the freshness check, so 10 attempts is well above any realistic interleaving.
_GROUP_JOIN_MAX_ATTEMPTS = 10

# Required group-mode setting fields, validated up-front so a 400 lands on the
# editor rather than a confusing KeyError deep in the lobby write path.
_REQUIRED_GROUP_SETTING_FIELDS = (
    "target_human_count",
    "ai_join_strategy",
    "ai_strategy_value",
    "max_wait_seconds",
)


def _get_rds():
    """Return the appropriate RDS module based on config."""
    from chatroom_api._providers import get_rds_provider
    return get_rds_provider()


def _get_db():
    """Return the appropriate DynamoDB module based on config."""
    if config.USE_MOCK_DYNAMO:
        from chatroom_api import mock_dynamo
        return mock_dynamo
    from chatroom_api import dynamo
    return dynamo


def _get_lobby():
    """Return the appropriate lobby module based on config."""
    if config.USE_MOCK_LOBBY:
        from chatroom_api import mock_lobby
        return mock_lobby
    from chatroom_api import lobby
    return lobby


def _generate_nickname(exclude=None) -> str:
    """Generate 'Participant' + 4-digit random, avoiding collisions."""
    exclude = exclude or set()
    while True:
        name = f"Participant{random.randint(1000, 9999)}"
        if name not in exclude:
            return name


def _pick_avatar(exclude=None) -> dict:
    """Pick a random emoji avatar, avoiding collisions when possible."""
    exclude = exclude or set()
    available = [e for e in EMOJI_POOL if e not in exclude]
    if not available:
        available = list(EMOJI_POOL)
    emoji = random.choice(available)
    return {"emojiText": emoji}


def handle_auth_token(body: dict) -> tuple[int, dict]:
    """Exchange a chatroom_id for a signed JWT + session info.

    Request body: { "chatroom_id": "scid_..." }

    Returns (status_code, response_body).
    """
    chatroom_id = body.get("chatroom_id", "")
    if not chatroom_id:
        return (400, {"error": "chatroom_id is required"})

    rds_mod = _get_rds()

    # 1. Read chatroom from RDS
    chatroom = rds_mod.get_chatroom(chatroom_id)
    if chatroom is None:
        return (404, {"error": "chatroom not found"})

    # 2. Check status
    if chatroom.get("status") != "active":
        return (401, {"error": "chatroom is inactive"})

    chatroom_setting = chatroom["setting"]

    # 3. Dispatch on mode. Default to one_on_one to stay back-compat with v2
    # chatrooms whose setting did not carry a ``mode`` field.
    mode = chatroom_setting.get("mode", "one_on_one")
    if mode == "group":
        return _handle_group_auth(chatroom, chatroom_id, chatroom_setting)

    # --- one_on_one path (unchanged from v2) ---

    # 3. Generate session_id and conversation_id
    session_id = str(uuid4())
    conversation_id = str(uuid4())

    # 4. Auto-generate human nickname + avatar
    human_nickname = _generate_nickname()
    human_avatar = _pick_avatar()

    # 5. Auto-generate AI nickname + avatar (no collision with human)
    ai_id = f"ai_{uuid4().hex[:8]}"
    ai_nickname = _generate_nickname(exclude={human_nickname})
    ai_avatar = _pick_avatar(exclude={human_avatar["emojiText"]})
    default_model_id = chatroom_setting.get("model_id") or ""

    # 6. Build participants list
    participants = [
        {
            "session_id": session_id,
            "nickname": human_nickname,
            "avatar": human_avatar,
            "role": "human",
        },
        {
            "session_id": ai_id,
            "nickname": ai_nickname,
            "avatar": ai_avatar,
            "role": "ai",
            "model_id": default_model_id,
        },
    ]

    # 7. Store conversation in DynamoDB with chatroom_setting snapshot + participants + join events
    _store_conversation(
        session_id, conversation_id, chatroom_id,
        chatroom_setting, participants, human_nickname, ai_id, ai_nickname,
    )

    # 8. Sign JWT
    token = jwt_utils.create_token(session_id, conversation_id, chatroom_id)

    # 9. Return session info
    return (200, {
        "token": token,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "nickname": human_nickname,
        "avatar": human_avatar,
        "chatroom_setting": chatroom_setting,
    })


def _store_conversation(
    session_id: str,
    conversation_id: str,
    chatroom_id: str,
    chatroom_setting: dict,
    participants: list[dict],
    human_nickname: str,
    ai_id: str,
    ai_nickname: str,
) -> None:
    """Create conversation in DynamoDB with join events."""
    db = _get_db()

    now_ms = int(time.time() * 1000)
    now_iso = datetime.now(timezone.utc).isoformat()

    events = [
        {
            "type": "system",
            "session_id": session_id,
            "sender": "System",
            "role": "system",
            "ai_participant_id": None,
            "content": f"{human_nickname} joined the chatroom",
            "timestamp": now_ms,
            "visible_at": now_ms,
            "created_at": now_iso,
        },
        {
            "type": "system",
            "session_id": session_id,
            "sender": "System",
            "role": "system",
            "ai_participant_id": ai_id,
            "content": f"{ai_nickname} joined the chatroom",
            "timestamp": now_ms + 1,
            "visible_at": now_ms + 1,
            "created_at": now_iso,
        },
    ]

    # Tick-model fields are populated up-front so the heartbeat-driven tick
    # handler sees a fully-shaped row from the moment it's created. See
    # ``docs/low-level-design.md`` for the field semantics.
    db.append_events(
        conversation_id, chatroom_id, events,
        chatroom_setting=chatroom_setting,
        participants=participants,
        status="active",
        started_at=now_iso,
        last_tick_at=0,
        last_speak_at_by_session={},
    )


# ---------------------------------------------------------------------------
# Group-mode branch.
# ---------------------------------------------------------------------------


def _validate_group_setting(chatroom_setting: dict) -> tuple[bool, str]:
    """Return (ok, error_message) for a group-mode chatroom setting.

    The required fields must all be present and coercible to ints (except for
    ``ai_join_strategy``, which is a string discriminator).
    """
    for field in _REQUIRED_GROUP_SETTING_FIELDS:
        if field not in chatroom_setting:
            return False, f"chatroom setting missing required group field: {field}"

    strategy = chatroom_setting.get("ai_join_strategy")
    if strategy not in ("fixed_ai_count", "total_participant_count"):
        return False, f"invalid ai_join_strategy: {strategy!r}"

    for field in ("target_human_count", "ai_strategy_value", "max_wait_seconds"):
        try:
            if int(chatroom_setting[field]) < 0:
                return False, f"{field} must be non-negative"
        except (TypeError, ValueError):
            return False, f"{field} must be an integer"

    if int(chatroom_setting["target_human_count"]) < 1:
        return False, "target_human_count must be >= 1"

    return True, ""


def _build_human_participant(session_id: str, now_ms: int) -> tuple[dict, str, dict]:
    """Build a human participant dict for the lobby.

    Returns ``(participant, nickname, avatar)`` so the caller can echo
    ``nickname``/``avatar`` in the auth response without re-deriving them.
    """
    nickname = _generate_nickname()
    avatar = _pick_avatar()
    participant = {
        "session_id": session_id,
        "nickname": nickname,
        "avatar": avatar,
        "joined_at": now_ms,
        "last_seen_at": now_ms,
    }
    return participant, nickname, avatar


def _handle_group_auth(
    chatroom: dict,
    chatroom_id: str,
    chatroom_setting: dict,
) -> tuple[int, dict]:
    """Handle ``POST /auth/token`` for a group-mode chatroom.

    Loops through the four-stage flow described in
    ``docs/low-level-design.md#post-authtoken-group-mode-branch``:

    1. Query the open lobby for ``chatroom_id`` (sparse GSI on ``status=open``).
    2. Freshness check: if past ``deadline_at``, ``close_lobby`` and retry.
    3. Create a fresh open lobby if none exists.
    4. Atomically join via conditional ``UpdateItem``; retry on conditional
       failure (lobby just closed/closing/full under a racing joiner).

    On capacity-reached we synchronously call ``close_lobby`` and report the
    resulting closed state in the response.
    """
    ok, err = _validate_group_setting(chatroom_setting)
    if not ok:
        return (400, {"error": err})

    lobby_mod = _get_lobby()

    # Group setting passed to ``create_open_lobby``. The lobby snapshots only
    # the pairing-relevant subset; ``close_lobby`` reads the rest from RDS.
    group_setting = {
        "target_human_count": int(chatroom_setting["target_human_count"]),
        "ai_join_strategy": chatroom_setting["ai_join_strategy"],
        "ai_strategy_value": int(chatroom_setting["ai_strategy_value"]),
        "max_wait_seconds": int(chatroom_setting["max_wait_seconds"]),
    }

    session_id = str(uuid4())
    participant, nickname, avatar = _build_human_participant(
        session_id, int(time.time() * 1000)
    )

    updated_lobby: dict | None = None
    joined_lobby: dict | None = None  # the lobby we successfully joined

    for _attempt in range(_GROUP_JOIN_MAX_ATTEMPTS):
        now_ms = int(time.time() * 1000)
        lobby = lobby_mod.query_open_lobby(chatroom_id)

        # Freshness check: stale lobby past its deadline gets closed before
        # we try to join. Then retry from the top so the next iteration sees
        # no open lobby and creates a new one.
        if lobby is not None and now_ms >= int(lobby.get("deadline_at", 0)):
            close_lobby(lobby["lobby_id"], now_ms)
            continue

        if lobby is None:
            conversation_id = str(uuid4())
            lobby = lobby_mod.create_open_lobby(
                chatroom_id, group_setting, conversation_id, now_ms
            )

        # Refresh the participant's joined_at/last_seen_at to *this* attempt.
        participant["joined_at"] = now_ms
        participant["last_seen_at"] = now_ms

        success, lobby_after_join = lobby_mod.join_lobby(
            lobby["lobby_id"], participant, now_ms
        )
        if not success:
            # Lobby closed/closing/full between query and update — try again.
            continue

        joined_lobby = lobby
        updated_lobby = lobby_after_join
        break

    if updated_lobby is None or joined_lobby is None:
        return (503, {"error": "lobby join failed; try again"})

    # Capacity-reached: synchronously close. The conversation row is created
    # inside ``close_lobby`` (idempotent under racing closers).
    capacity_close_ran = False
    if int(updated_lobby.get("actual_human_count", 0)) >= int(
        updated_lobby.get("target_human_count", 0)
    ):
        close_lobby(updated_lobby["lobby_id"], int(time.time() * 1000))
        capacity_close_ran = True

    conversation_id = joined_lobby["conversation_id"]
    token = jwt_utils.create_token(session_id, conversation_id, chatroom_id)

    lobby_state = {
        "status": "closed" if capacity_close_ran else updated_lobby.get("status"),
        "actual_human_count": int(updated_lobby.get("actual_human_count", 0)),
        "target_human_count": int(updated_lobby.get("target_human_count", 0)),
        "deadline_at": int(updated_lobby.get("deadline_at", 0)),
    }

    return (200, {
        "token": token,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "nickname": nickname,
        "avatar": avatar,
        "chatroom_setting": chatroom_setting,
        "lobby": lobby_state,
    })
