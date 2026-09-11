"""Shared construction helpers for AI conversation participants."""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from uuid import uuid4

from chatroom_api.constants import EMOJI_POOL
from chatroom_api.settings import (
    is_single_human_single_ai_assistant_room,
    normalize_ai_nickname,
    normalize_persona_entries,
)


def generate_nickname(exclude=None) -> str:
    """Generate ``Participant`` plus four digits, avoiding *exclude*."""
    excluded = exclude or set()
    while True:
        name = f"Participant{random.randint(1000, 9999)}"
        if name not in excluded:
            return name


def pick_avatar(exclude=None) -> dict:
    """Pick a random emoji avatar, avoiding *exclude* when possible."""
    excluded = exclude or set()
    available = [emoji for emoji in EMOJI_POOL if emoji not in excluded]
    if not available:
        available = list(EMOJI_POOL)
    return {"emojiText": random.choice(available)}


def pick_personas(persona_pool: list, ai_count: int) -> list:
    """Assign personas in shuffled rounds, then a partial shuffled round."""
    cleaned = []
    for persona in persona_pool or []:
        if isinstance(persona, str):
            stripped = persona.strip()
            if stripped:
                cleaned.append(stripped)
        elif isinstance(persona, dict):
            cleaned.append(persona)
    if not cleaned:
        return [""] * ai_count
    if ai_count <= 0:
        return []
    if len(cleaned) >= ai_count:
        return random.sample(cleaned, ai_count)

    full_rounds, remainder = divmod(ai_count, len(cleaned))
    result = []
    for _ in range(full_rounds):
        result.extend(random.sample(cleaned, len(cleaned)))
    if remainder:
        result.extend(random.sample(cleaned, remainder))
    return result


def build_ai_participants(
    chatroom_setting: dict,
    ai_count: int,
    *,
    existing_participants: Iterable[dict] = (),
    ai_id_factory: Callable[[int], str] | None = None,
    rng: random.Random | None = None,
) -> list[dict]:
    """Build normalized AI participants for lobby and batch conversations."""
    default_model_id = str(chatroom_setting.get("model_id") or "").strip()
    default_temperature = chatroom_setting.get("temperature")
    persona_entries = normalize_persona_entries(
        chatroom_setting.get("ai_personas") or [],
        default_model_id=default_model_id,
        default_temperature=default_temperature,
    )
    random_source = rng or random
    selected_entries = []
    if persona_entries:
        if len(persona_entries) >= ai_count:
            selected_entries = random_source.sample(persona_entries, ai_count)
        else:
            rounds, remainder = divmod(ai_count, len(persona_entries))
            for _ in range(rounds):
                selected_entries.extend(
                    random_source.sample(persona_entries, len(persona_entries))
                )
            if remainder:
                selected_entries.extend(
                    random_source.sample(persona_entries, remainder)
                )
    use_assistant_names = is_single_human_single_ai_assistant_room(
        chatroom_setting
    )
    room_ai_nickname = normalize_ai_nickname(
        chatroom_setting.get("ai_nickname")
    )

    existing = list(existing_participants)
    used_nicknames = {
        participant.get("nickname") for participant in existing
    }
    used_emojis = {
        (participant.get("avatar") or {}).get("emojiText")
        for participant in existing
    }
    used_internal_names: set[str] = set()

    def resolve_internal_name(raw_name: str | None, index: int) -> str:
        base = (raw_name or f"ai_{index + 1}").strip() or f"ai_{index + 1}"
        candidate = base
        suffix = 2
        while candidate in used_internal_names:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used_internal_names.add(candidate)
        return candidate

    def default_id_factory(_index: int) -> str:
        return "ai_" + uuid4().hex[:8]

    make_id = ai_id_factory or default_id_factory
    participants = []
    for index in range(ai_count):
        selected = (
            selected_entries[index]
            if index < len(selected_entries)
            else {"persona": "", "model_id": default_model_id}
        )
        preferred_nickname = normalize_ai_nickname(selected.get("nickname"))
        if preferred_nickname and preferred_nickname not in used_nicknames:
            nickname = preferred_nickname
        elif use_assistant_names:
            nickname = (
                room_ai_nickname
                if room_ai_nickname and room_ai_nickname not in used_nicknames
                else "AI"
            )
        else:
            while True:
                nickname = f"Participant{random_source.randint(1000, 9999)}"
                if nickname not in used_nicknames:
                    break
        available = [emoji for emoji in EMOJI_POOL if emoji not in used_emojis]
        if not available:
            available = list(EMOJI_POOL)
        avatar = {"emojiText": random_source.choice(available)}
        used_nicknames.add(nickname)
        used_emojis.add(avatar["emojiText"])
        participants.append({
            "ai_participant_id": make_id(index),
            "nickname": nickname,
            "avatar": avatar,
            "role": "ai",
            "persona": selected.get("persona", ""),
            "model_id": selected.get("model_id", default_model_id),
            "temperature": selected.get("temperature", default_temperature),
            "internal_name": resolve_internal_name(
                selected.get("internal_name"), index
            ),
        })
    return participants
