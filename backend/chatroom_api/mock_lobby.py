"""In-memory mock of the DynamoDB lobby store.

Provides the same interface as ``lobby.py`` so the rest of the codebase can
swap implementations via the ``USE_MOCK_LOBBY`` env var. Conditional-update
semantics are preserved under a ``threading.Lock`` so the Hypothesis state
machine in the PBT suite (and the local heartbeat thread that races the
request thread) see atomic check-then-update on every public function.
"""

from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

# ``compute_ai_count`` is pure (no DDB dependency) so we re-export it from the
# real lobby module to keep one source of truth. Callers can swap modules via
# ``USE_MOCK_LOBBY`` without losing access to the helper.
from chatroom_api.lobby import compute_ai_count  # noqa: F401

# Keep these mirrored from ``lobby.py`` so callers see the same constants.
LOBBY_TTL_SECONDS = 60 * 60 * 24 * 180
STALE_THRESHOLD_SEC = 30

_lobbies: dict[str, dict] = {}
_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def reset() -> None:
    """Clear all in-memory lobbies. Tests call this in ``setup_method``."""
    with _lock:
        _lobbies.clear()


def get_lobby(lobby_id: str) -> Optional[dict]:
    """Return a deep copy of the full lobby item for *lobby_id*, or None."""
    with _lock:
        lobby = _lobbies.get(lobby_id)
        if lobby is None:
            return None
        return copy.deepcopy(lobby)


def query_open_lobby(chatroom_id: str) -> Optional[dict]:
    """Return the (at most one) open lobby for *chatroom_id*, or None."""
    with _lock:
        for lobby in _lobbies.values():
            if (
                lobby.get("chatroom_id") == chatroom_id
                and lobby.get("status") == "open"
            ):
                return copy.deepcopy(lobby)
        return None


def query_by_conversation_id(conversation_id: str) -> Optional[dict]:
    """Return the lobby with the given pre-allocated *conversation_id*, or None.

    Matches regardless of status so callers in the lobby/closing/closed/aborted
    phases can all locate the row.
    """
    with _lock:
        for lobby in _lobbies.values():
            if lobby.get("conversation_id") == conversation_id:
                return copy.deepcopy(lobby)
        return None


def create_open_lobby(
    chatroom_id: str,
    setting: dict,
    conversation_id: str,
    now_ms: int,
) -> dict:
    """Create a new ``status="open"`` lobby and return a copy of the stored item."""
    now_iso = _now_iso()
    lobby_id = "lob-" + str(uuid.uuid4())
    max_wait_seconds = int(setting["max_wait_seconds"])

    item = {
        "lobby_id": lobby_id,
        "chatroom_id": chatroom_id,
        "conversation_id": conversation_id,
        "status": "open",
        "target_human_count": int(setting["target_human_count"]),
        "ai_join_strategy": setting["ai_join_strategy"],
        "ai_strategy_value": int(setting["ai_strategy_value"]),
        "max_wait_seconds": max_wait_seconds,
        "actual_human_count": 0,
        "participants": [],
        "deadline_at": now_ms + max_wait_seconds * 1000,
        "created_at": now_iso,
        "updated_at": now_iso,
        "closed_at": None,
        "ttl": (now_ms // 1000) + LOBBY_TTL_SECONDS,
    }

    with _lock:
        # Mirror the real DDB ``attribute_not_exists(lobby_id)`` guard. Collisions
        # on a fresh UUID are not realistic but the check matches the contract.
        if lobby_id in _lobbies:
            raise RuntimeError(f"lobby_id collision: {lobby_id}")
        _lobbies[lobby_id] = copy.deepcopy(item)
    return copy.deepcopy(item)


def join_lobby(
    lobby_id: str,
    participant: dict,
    now_ms: int,
) -> tuple[bool, Optional[dict]]:
    """Atomically join *participant* to the open lobby.

    Conditional check: ``status == "open" AND actual_human_count < target_human_count``.
    On success: increment ``actual_human_count``, append participant, return
    ``(True, deepcopy_of_updated_lobby)``. On condition fail: ``(False, None)``.
    """
    now_iso = _now_iso()
    with _lock:
        lobby = _lobbies.get(lobby_id)
        if lobby is None:
            return False, None
        if lobby.get("status") != "open":
            return False, None
        if lobby.get("actual_human_count", 0) >= lobby.get("target_human_count", 0):
            return False, None

        lobby["actual_human_count"] = int(lobby.get("actual_human_count", 0)) + 1
        participants = lobby.setdefault("participants", [])
        participants.append(copy.deepcopy(participant))
        lobby["updated_at"] = now_iso
        return True, copy.deepcopy(lobby)


def update_last_seen_at(lobby_id: str, session_id: str, now_ms: int) -> None:
    """Best-effort, idempotent update of *session_id*'s ``last_seen_at``.

    No-op if the lobby is gone or *session_id* is not in the participants list.
    """
    now_iso = _now_iso()
    with _lock:
        lobby = _lobbies.get(lobby_id)
        if lobby is None:
            return
        for p in lobby.get("participants", []):
            if p.get("session_id") == session_id:
                p["last_seen_at"] = now_ms
                lobby["updated_at"] = now_iso
                return


def update_lobby_status(
    lobby_id: str,
    from_status: str,
    to_status: str,
    now_ms: int,
    extra_set: Optional[dict] = None,
) -> bool:
    """Atomically transition a lobby's ``status`` from *from_status* to *to_status*.

    Returns True on success, False if the conditional fails (lobby missing or
    status doesn't match *from_status*).
    """
    now_iso = _now_iso()
    with _lock:
        lobby = _lobbies.get(lobby_id)
        if lobby is None:
            return False
        if lobby.get("status") != from_status:
            return False
        lobby["status"] = to_status
        lobby["updated_at"] = now_iso
        if extra_set:
            for k, v in extra_set.items():
                lobby[k] = copy.deepcopy(v)
        return True


def set_lobby_aborted(lobby_id: str, now_ms: int) -> bool:
    """Flip a lobby from ``closing`` to ``aborted`` and stamp ``closed_at``."""
    return update_lobby_status(
        lobby_id,
        from_status="closing",
        to_status="aborted",
        now_ms=now_ms,
        extra_set={"closed_at": _now_iso()},
    )
