"""Chatroom setting normalization helpers used by runtime code.

The editor-facing schema is allowed to evolve. The runtime still needs a
small, stable set of fields for lobby pairing and inference. Keep that mapping
here so lobby/auth/tick code does not need to know every UI-era field name.
"""

from __future__ import annotations

from copy import deepcopy

MAX_BEDROCK_TEMPERATURE = 1.0
MIN_BEDROCK_TEMPERATURE = 0.0


def _coerce_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def resolve_runtime_setting(setting: dict | None) -> dict:
    """Return a setting dict with legacy runtime lobby fields populated.

    New editor fields:
    - ``human_count``
    - ``ai_count``
    - ``replace_human_with_ai``

    Runtime fields kept for now:
    - ``target_human_count``
    - ``ai_join_strategy``
    - ``ai_strategy_value``
    """
    normalized = deepcopy(setting or {})
    mode = normalized.get("mode") or "one_on_one"
    normalized["mode"] = mode

    human_count = _coerce_int(
        normalized.get("human_count", normalized.get("target_human_count", 1)),
        1,
    )
    ai_count = _coerce_int(
        normalized.get("ai_count", normalized.get("ai_strategy_value", 1)),
        1,
    )
    human_count = max(1, human_count)
    ai_count = max(0, ai_count)

    replace_human_with_ai = bool(
        normalized.get("replace_human_with_ai", False)
    )

    if mode == "one_on_one":
        human_count = 1
        ai_count = 1
        replace_human_with_ai = False
        normalized["target_human_count"] = 1
        normalized["ai_join_strategy"] = "fixed_ai_count"
        normalized["ai_strategy_value"] = 1
        normalized["max_wait_seconds"] = 0
    else:
        normalized["target_human_count"] = human_count
        if replace_human_with_ai:
            normalized["ai_join_strategy"] = "total_participant_count"
            normalized["ai_strategy_value"] = human_count + ai_count
        else:
            normalized["ai_join_strategy"] = "fixed_ai_count"
            normalized["ai_strategy_value"] = ai_count

    normalized["human_count"] = human_count
    normalized["ai_count"] = ai_count
    normalized["replace_human_with_ai"] = replace_human_with_ai
    normalized["mimic_human"] = bool(normalized.get("mimic_human", True))

    temperature = normalized.get("temperature", 0.7)
    normalized["temperature"] = normalize_temperature(temperature, default=0.7)
    return normalized


def normalize_temperature(value, *, default: float | None = None) -> float | None:
    """Normalize the Bedrock temperature field for beta.

    Bedrock's accepted range is constrained to 0.0..1.0 here. Future OpenAI
    and Anthropic direct-provider paths may use different provider limits.
    """
    if value is None:
        return default
    try:
        temperature = float(value)
    except (TypeError, ValueError):
        return default
    if temperature < MIN_BEDROCK_TEMPERATURE:
        return MIN_BEDROCK_TEMPERATURE
    if temperature > MAX_BEDROCK_TEMPERATURE:
        return MAX_BEDROCK_TEMPERATURE
    return temperature


def normalize_persona_entry(
    entry,
    *,
    default_model_id: str,
    default_temperature: float | None,
) -> dict | None:
    """Normalize one AI persona entry.

    Supports legacy string entries and the object form used by the editor.
    """
    if isinstance(entry, str):
        persona = entry.strip()
        if not persona:
            return None
        return {
            "persona": persona,
            "model_id": default_model_id,
            "temperature": default_temperature,
            "internal_name": None,
            "nickname": None,
        }
    if not isinstance(entry, dict):
        return None

    persona = str(entry.get("persona") or "").strip()
    model_id = str(entry.get("model_id") or "").strip() or default_model_id
    temperature = normalize_temperature(
        entry.get("temperature"),
        default=default_temperature,
    )
    internal_name = str(entry.get("internal_name") or "").strip() or None
    nickname = str(entry.get("nickname") or "").strip() or None
    if not persona and not internal_name and not nickname:
        return None

    return {
        "persona": persona,
        "model_id": model_id,
        "temperature": temperature,
        "internal_name": internal_name,
        "nickname": nickname,
    }


def normalize_persona_entries(
    persona_pool: list,
    *,
    default_model_id: str,
    default_temperature: float | None,
) -> list[dict]:
    return [
        normalized
        for entry in (persona_pool or [])
        if (
            normalized := normalize_persona_entry(
                entry,
                default_model_id=default_model_id,
                default_temperature=default_temperature,
            )
        )
    ]
