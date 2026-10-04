"""AI-only completion policy, read from immutable settings and accepted decisions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class InferenceDecision:
    """One validated model action; control actions never carry chat text."""

    action: str
    message: str | None = None


def early_completion_enabled(setting: dict) -> bool:
    """Keep legacy rooms/batches disabled; new rooms explicitly opt in via editor."""
    value = setting.get('allow_early_completion', False)
    if not isinstance(value, bool):
        raise ValueError('allow_early_completion must be a boolean')
    return value


def current_ticks(conversation: dict) -> list[dict]:
    """Ignore confirmations for older message revisions; never infer from prose."""
    history = conversation.get('ai_tick_history') or {}
    if history.get('message_revision') != conversation.get('next_turn', 0):
        return []
    return list(history.get('ticks') or [])


def limit_reason(message_count: int, total_chars: int, setting: dict) -> str | None:
    """Prefer the message limit when the same committed message reaches both."""
    if message_count >= int(setting['max_turns']):
        return 'max_messages'
    if total_chars >= int(setting['max_total_chars']):
        return 'max_characters'
    return None


def completion_footer(reason: str | None) -> str:
    """Export-only explanation, never a synthetic dialogue/model-history event."""
    explanations = {
        'all_ai_agreed_to_end': 'because all AI participants were ready to finish',
        'max_messages': 'because the maximum number of messages was reached',
        'max_characters': 'because the maximum character count was reached',
    }
    explanation = explanations.get(reason)
    return 'System: This conversation has ended' + (f' {explanation}' if explanation else '') + '.'
