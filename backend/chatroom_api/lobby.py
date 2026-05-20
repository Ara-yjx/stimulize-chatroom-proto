"""DynamoDB lobby store for group-mode pairing.

Sibling of ``dynamo.py``. Reads/writes the ``chatroom-lobbies`` table that
holds the at-most-one open lobby per chatroom (sparse GSI on ``status="open"``)
plus historical closed/aborted rows for audit.

See ``docs/low-level-design.md`` for the schema and the ``close_lobby``
subroutine; this module covers the read/write primitives only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key

from chatroom_api import config

_table = None

# Lobby rows linger ~180 days post-close for audit. TTL is set so the table
# stays small even with many cohorts.
LOBBY_TTL_SECONDS = 60 * 60 * 24 * 180

# Stale-participant threshold (beta records but does not enforce).
STALE_THRESHOLD_SEC = 30


def _get_table():
    """Lazy-init the DynamoDB Table resource for the lobby table."""
    global _table
    if _table is None:
        dynamodb = boto3.resource("dynamodb")
        _table = dynamodb.Table(config.LOBBY_TABLE)
    return _table


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_lobby(lobby_id: str) -> Optional[dict]:
    """Return the full lobby item for *lobby_id*, or None.

    Thin wrapper around ``GetItem`` for callers that hold a ``lobby_id`` but
    not the full row (e.g. ``close_lobby`` after the open→closing flip).
    """
    table = _get_table()
    resp = table.get_item(Key={"lobby_id": lobby_id})
    return resp.get("Item")


def query_open_lobby(chatroom_id: str) -> Optional[dict]:
    """Return the open lobby for *chatroom_id*, or None.

    The ``chatroom_id-status-index`` GSI is sparse on ``status="open"`` so at
    most one row matches. We still ``Limit=1`` defensively.
    """
    table = _get_table()
    resp = table.query(
        IndexName="chatroom_id-status-index",
        KeyConditionExpression=(
            Key("chatroom_id").eq(chatroom_id) & Key("status").eq("open")
        ),
        Limit=1,
    )
    items = resp.get("Items") or []
    return items[0] if items else None


def query_by_conversation_id(conversation_id: str) -> Optional[dict]:
    """Return the lobby with the given pre-allocated *conversation_id*, or None."""
    table = _get_table()
    resp = table.query(
        IndexName="conversation_id-index",
        KeyConditionExpression=Key("conversation_id").eq(conversation_id),
        Limit=1,
    )
    items = resp.get("Items") or []
    return items[0] if items else None


def create_open_lobby(
    chatroom_id: str,
    setting: dict,
    conversation_id: str,
    now_ms: int,
) -> dict:
    """Create a new ``status="open"`` lobby and return the stored item.

    *setting* must carry the group-mode fields ``target_human_count``,
    ``ai_join_strategy``, ``ai_strategy_value``, ``max_wait_seconds``.
    """
    table = _get_table()
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

    table.put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(lobby_id)",
    )
    return item


def join_lobby(
    lobby_id: str,
    participant: dict,
    now_ms: int,
) -> tuple[bool, Optional[dict]]:
    """Atomically join *participant* to the open lobby.

    Conditional UpdateItem ensures ``status="open"`` and capacity is available.
    Returns ``(True, updated_lobby)`` on success and ``(False, None)`` if the
    conditional fails (lobby closed/closing/full).

    *participant* should be a dict like
    ``{session_id, nickname, avatar, joined_at, last_seen_at}``; callers fill
    ``joined_at`` and ``last_seen_at`` (typically both = ``now_ms``).
    """
    table = _get_table()
    now_iso = _now_iso()
    try:
        resp = table.update_item(
            Key={"lobby_id": lobby_id},
            UpdateExpression=(
                "ADD actual_human_count :one "
                "SET participants = list_append("
                "if_not_exists(participants, :empty), :p"
                "), updated_at = :now"
            ),
            ConditionExpression=(
                "#status = :open AND actual_human_count < target_human_count"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":one": 1,
                ":p": [participant],
                ":empty": [],
                ":open": "open",
                ":now": now_iso,
            },
            ReturnValues="ALL_NEW",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False, None
    return True, resp.get("Attributes")


def update_last_seen_at(lobby_id: str, session_id: str, now_ms: int) -> None:
    """Best-effort, idempotent update of *session_id*'s ``last_seen_at``.

    No-op if the lobby is gone or *session_id* is not in the participants list.
    Beta uses read-then-update with a list-index path; staleness here is
    acceptable since pruning is not enforced in beta.
    """
    table = _get_table()
    resp = table.get_item(Key={"lobby_id": lobby_id})
    item = resp.get("Item")
    if item is None:
        return
    participants = item.get("participants") or []
    idx = next(
        (i for i, p in enumerate(participants) if p.get("session_id") == session_id),
        -1,
    )
    if idx < 0:
        return
    try:
        table.update_item(
            Key={"lobby_id": lobby_id},
            UpdateExpression=(
                f"SET participants[{idx}].last_seen_at = :ls, updated_at = :now"
            ),
            ExpressionAttributeValues={
                ":ls": now_ms,
                ":now": _now_iso(),
            },
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        # Some other writer reshaped the list; harmless for a heartbeat.
        return


def update_lobby_status(
    lobby_id: str,
    from_status: str,
    to_status: str,
    now_ms: int,
    extra_set: Optional[dict] = None,
) -> bool:
    """Atomically transition a lobby's ``status`` from *from_status* to *to_status*.

    *extra_set* attribute names must not collide with the reserved set used
    here (``status``, ``updated_at``). Returns True on success, False if the
    conditional fails.
    """
    table = _get_table()
    now_iso = _now_iso()
    set_clauses = ["#status = :to", "updated_at = :now"]
    values = {
        ":to": to_status,
        ":from": from_status,
        ":now": now_iso,
    }
    names = {"#status": "status"}

    if extra_set:
        for i, (k, v) in enumerate(extra_set.items()):
            placeholder = f":x{i}"
            name_alias = f"#x{i}"
            set_clauses.append(f"{name_alias} = {placeholder}")
            values[placeholder] = v
            names[name_alias] = k

    try:
        table.update_item(
            Key={"lobby_id": lobby_id},
            UpdateExpression="SET " + ", ".join(set_clauses),
            ConditionExpression="#status = :from",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
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


# ---------------------------------------------------------------------------
# Pure pairing helpers.
# ---------------------------------------------------------------------------


def compute_ai_count(strategy: str, value: int, h: int) -> int:
    """Pure compute_ai_count.

    - "fixed_ai_count": returns value (independent of h).
    - "total_participant_count": returns max(0, value - h) so the room totals
      to max(value, h) participants.

    Pre: value >= 0, h >= 0, strategy in {"fixed_ai_count", "total_participant_count"}.
    Post: result >= 0; for fixed_ai_count result is independent of h;
          for total_participant_count, result + h == max(value, h).
    """
    if strategy == "fixed_ai_count":
        return max(0, int(value))
    if strategy == "total_participant_count":
        return max(0, int(value) - int(h))
    raise ValueError(f"unknown ai_join_strategy: {strategy!r}")
