"""Resumable one-human conversation lifecycle and connection fencing."""

from __future__ import annotations

import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from chatroom_api import jwt_utils
from chatroom_api._providers import get_event_store_provider
from chatroom_api.close_lobby import _pick_avatar, _pick_personas
from chatroom_api.cursors import decode_cursor, encode_cursor, event_key_timestamp, make_event_key
from chatroom_api.event_store import ConditionalWriteFailed
from chatroom_api.settings import (
    normalize_ai_nickname,
    normalize_persona_entries,
    resolve_runtime_setting,
)


PARTICIPANT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,63}$")
_LATEST_EVENT_TIMESTAMP = 9_999_999_999_999_999
ACTIVE_METADATA_FIELDS = [
    "active_connection_id",
    "active_episode_number",
    "active_episode_started_at",
    "active_history_start_cursor",
    "active_tick_id",
    "active_tick_until",
]


def _get_db():
    from chatroom_api import config
    if config.USE_MOCK_DYNAMO:
        from chatroom_api import mock_dynamo
        return mock_dynamo
    from chatroom_api import dynamo
    return dynamo


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_to_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def normalize_participant_id(value) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if PARTICIPANT_ID_RE.fullmatch(normalized) else None


def conversation_id_for(chatroom_id: str, participant_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"stimulize:resume:{chatroom_id}:{participant_id}"))


def is_supported_setting(setting: dict) -> bool:
    normalized = resolve_runtime_setting(setting)
    return (
        bool(normalized.get("resumable", False))
        and int(normalized.get("human_count", 1)) == 1
        and int(normalized.get("ai_count", 1)) == 1
        and not bool(normalized.get("mimic_human", True))
    )


def _build_participants(setting: dict, participant_id: str) -> tuple[list[dict], dict]:
    human_avatar = _pick_avatar()
    human = {
        "participant_id": participant_id,
        "nickname": "PARTICIPANT",
        "avatar": human_avatar,
        "role": "human",
    }
    default_model_id = str(setting.get("model_id") or "").strip()
    default_temperature = setting.get("temperature")
    persona_entries = normalize_persona_entries(
        setting.get("ai_personas") or [],
        default_model_id=default_model_id,
        default_temperature=default_temperature,
    )
    selected = _pick_personas(persona_entries, 1)[0] if persona_entries else {
        "persona": "",
        "model_id": default_model_id,
        "temperature": default_temperature,
        "internal_name": None,
        "nickname": None,
    }
    nickname = normalize_ai_nickname(selected.get("nickname"))
    if not nickname:
        nickname = normalize_ai_nickname(setting.get("ai_nickname")) or "AI"
    ai = {
        "ai_participant_id": "ai_" + uuid4().hex[:8],
        "nickname": nickname,
        "avatar": _pick_avatar(exclude={human_avatar.get("emojiText")}),
        "role": "ai",
        "persona": selected.get("persona", ""),
        "model_id": selected.get("model_id", default_model_id),
        "temperature": selected.get("temperature", default_temperature),
        "internal_name": selected.get("internal_name") or "ai_1",
    }
    return [human, ai], human


def _boundary_event(subtype: str, content: str, timestamp: int, episode_number: int) -> dict:
    return {
        "type": "system",
        "subtype": subtype,
        "sender": "System",
        "role": "system",
        "content": content,
        "timestamp": timestamp,
        "created_at": _now_iso(),
        "episode_number": episode_number,
    }


def _event_cursor(conversation_id: str, timestamp: int, batch_id: str) -> str:
    return encode_cursor(conversation_id, make_event_key(timestamp, batch_id, 0))


def _episode_expired(conversation: dict, now_ms: int) -> bool:
    started_ms = _iso_to_ms(conversation.get("active_episode_started_at"))
    duration = int((conversation.get("chatroom_setting") or {}).get("max_duration_seconds", 0) or 0)
    return bool(started_ms is not None and duration > 0 and now_ms >= started_ms + duration * 1000)


def _auth_payload(conversation: dict, session_id: str, connection_id: str, *, resumed: bool) -> dict:
    human = next(p for p in conversation.get("participants", []) if p.get("role") == "human")
    episode_number = int(conversation["active_episode_number"])
    token = jwt_utils.create_token(
        session_id,
        conversation["conversation_id"],
        conversation["chatroom_id"],
        participant_id=conversation["participant_id"],
        connection_id=connection_id,
        episode_number=episode_number,
    )
    return {
        "token": token,
        "session_id": session_id,
        "participant_id": conversation["participant_id"],
        "connection_id": connection_id,
        "conversation_id": conversation["conversation_id"],
        "nickname": human.get("nickname", "PARTICIPANT"),
        "avatar": human.get("avatar"),
        "chatroom_setting": conversation["chatroom_setting"],
        "episode_number": episode_number,
        "episode_started_at": conversation["active_episode_started_at"],
        "history_start_cursor": conversation.get("active_history_start_cursor"),
        "resumed": resumed,
        "lobby": None,
    }


def create_or_resume(chatroom: dict, participant_id: str) -> tuple[int, dict]:
    """Create, refresh, or resume the participant's deterministic conversation."""
    chatroom_id = chatroom["id"]
    conversation_id = conversation_id_for(chatroom_id, participant_id)
    setting = resolve_runtime_setting(chatroom["setting"])
    history_store = get_event_store_provider()
    db = _get_db()

    for _attempt in range(5):
        now_ms = int(time.time() * 1000)
        now_iso = _now_iso()
        session_id = str(uuid4())
        connection_id = str(uuid4())
        conversation = db.get_conversation(conversation_id)

        if conversation is None:
            participants, _human = _build_participants(setting, participant_id)
            episode = {
                "episode_number": 1,
                "started_at": now_iso,
                "ended_at": None,
                "status": "active",
                "history_start_cursor": None,
                "history_end_cursor": None,
            }
            batch_id = uuid5(
                NAMESPACE_URL, f"stimulize:resumable-create:{conversation_id}"
            ).hex
            try:
                history_store.create_conversation(
                    {
                        "conversation_id": conversation_id,
                        "chatroom_id": chatroom_id,
                        "chatroom_setting": setting,
                        "participants": participants,
                        "status": "active",
                        "started_at": now_iso,
                        "resumable": True,
                        "participant_id": participant_id,
                        "episode_count": 1,
                        "episodes": [episode],
                        "active_episode_number": 1,
                        "active_episode_started_at": now_iso,
                        "active_history_start_cursor": None,
                        "active_connection_id": connection_id,
                        "last_connected_at": now_iso,
                        "last_tick_at": 0,
                        "ai_tick_state_by_participant_id": {},
                        "next_actionable_tick_at": 0,
                    },
                    [_boundary_event(
                        "conversation_started",
                        "This is the beginning of the conversation.",
                        now_ms,
                        1,
                    )],
                    batch_id,
                )
            except ConditionalWriteFailed:
                continue
            conversation = db.get_conversation(conversation_id)
            return (200, _auth_payload(conversation, session_id, connection_id, resumed=False))

        if (
            not conversation.get("resumable")
            or conversation.get("chatroom_id") != chatroom_id
            or conversation.get("participant_id") != participant_id
        ):
            return (409, {"error": "participant conversation identity conflict"})

        if conversation.get("status") == "active" and _episode_expired(conversation, now_ms):
            end_episode(conversation, now_ms=now_ms)
            continue

        if conversation.get("status") == "active":
            expected = {
                "active_episode_number": conversation.get("active_episode_number"),
                "active_connection_id": conversation.get("active_connection_id"),
            }
            if not history_store.update_metadata(
                conversation_id,
                {"active_connection_id": connection_id, "last_connected_at": now_iso},
                expected_status="active",
                expected_metadata=expected,
            ):
                continue
            conversation["active_connection_id"] = connection_id
            conversation["last_connected_at"] = now_iso
            return (200, _auth_payload(conversation, session_id, connection_id, resumed=False))

        if conversation.get("status") != "inactive":
            return (409, {"error": "conversation cannot be resumed"})

        previous_cursor = history_store.query_history_before(
            conversation_id, None, _LATEST_EVENT_TIMESTAMP, 1
        ).get("latest_cursor")
        if previous_cursor:
            previous_timestamp = event_key_timestamp(
                decode_cursor(previous_cursor, conversation_id)["event_key"]
            )
            now_ms = max(now_ms, previous_timestamp + 1)
        episode_number = int(conversation.get("episode_count", 0) or 0) + 1
        episodes = deepcopy(conversation.get("episodes") or [])
        episodes.append({
            "episode_number": episode_number,
            "started_at": now_iso,
            "ended_at": None,
            "status": "active",
            "history_start_cursor": previous_cursor,
            "history_end_cursor": None,
        })
        batch_id = uuid4().hex
        try:
            history_store.append_history_batch(
                conversation_id,
                [_boundary_event(
                    "conversation_resumed", "Conversation resumed.", now_ms, episode_number
                )],
                batch_id,
                metadata_updates={
                    "status": "active",
                    "episode_count": episode_number,
                    "episodes": episodes,
                    "active_episode_number": episode_number,
                    "active_episode_started_at": now_iso,
                    "active_history_start_cursor": previous_cursor,
                    "active_connection_id": connection_id,
                    "last_connected_at": now_iso,
                    "next_actionable_tick_at": 0,
                    "ai_tick_state_by_participant_id": {},
                },
                expected_status="inactive",
                expected_metadata={"episode_count": episode_number - 1},
            )
        except ConditionalWriteFailed:
            continue
        conversation = db.get_conversation(conversation_id)
        return (200, _auth_payload(conversation, session_id, connection_id, resumed=True))

    return (503, {"error": "conversation update conflict; try again"})


def validate_connection(conversation: dict, claims: dict) -> str | None:
    """Return a 409 reason when a resumable token is no longer current."""
    if not conversation.get("resumable"):
        return None
    if claims.get("participant_id") != conversation.get("participant_id"):
        return "participant does not match conversation"
    if (
        conversation.get("status") == "active"
        and claims.get("connection_id") != conversation.get("active_connection_id")
    ):
        return "conversation opened in another browser"
    if int(claims.get("episode_number", 0) or 0) != int(
        conversation.get("episode_count", 0) or 0
    ):
        return "conversation episode has changed"
    return None


def episode_fence(conversation: dict) -> dict:
    if not conversation.get("resumable"):
        return {}
    return {"active_episode_number": conversation.get("active_episode_number")}


def end_episode(
    conversation: dict,
    *,
    now_ms: int | None = None,
    expected_active_tick_id: str | None = None,
) -> bool:
    """Close a resumable episode while leaving its conversation resumable."""
    if not conversation.get("resumable") or conversation.get("status") != "active":
        return False
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    now_iso = _now_iso()
    conversation_id = conversation["conversation_id"]
    episode_number = int(conversation["active_episode_number"])
    latest_cursor = get_event_store_provider().query_history_before(
        conversation_id, None, _LATEST_EVENT_TIMESTAMP, 1
    ).get("latest_cursor")
    if latest_cursor:
        latest_timestamp = event_key_timestamp(
            decode_cursor(latest_cursor, conversation_id)["event_key"]
        )
        now_ms = max(now_ms, latest_timestamp + 1)
    batch_id = uuid4().hex
    end_cursor = _event_cursor(conversation_id, now_ms, batch_id)
    episodes = deepcopy(conversation.get("episodes") or [])
    for episode in episodes:
        if int(episode.get("episode_number", 0)) == episode_number:
            episode.update({
                "status": "inactive",
                "ended_at": now_iso,
                "history_end_cursor": end_cursor,
            })
            break
    try:
        get_event_store_provider().append_history_batch(
            conversation_id,
            [_boundary_event(
                "episode_ended",
                "This conversation session has ended.",
                now_ms,
                episode_number,
            )],
            batch_id,
            metadata_updates={
                "status": "inactive",
                "episodes": episodes,
                "last_episode_ended_at": now_iso,
                "last_history_end_cursor": end_cursor,
            },
            metadata_remove=ACTIVE_METADATA_FIELDS,
            expected_status="active",
            expected_active_tick_id=expected_active_tick_id,
            expected_metadata={"active_episode_number": episode_number},
        )
    except ConditionalWriteFailed:
        return False
    return True
