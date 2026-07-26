"""Participant identity helpers for legacy and event-storage schemas."""

from __future__ import annotations


def participant_id(participant: dict) -> str | None:
    """Return a participant's conversation-scoped runtime identity."""
    if participant.get("role") == "ai":
        return participant.get("ai_participant_id") or participant.get("session_id")
    return participant.get("participant_id") or participant.get("session_id")


def event_author_id(event: dict) -> str | None:
    """Return the immutable author identity carried by a history event."""
    if event.get("role") == "ai":
        return event.get("ai_participant_id") or event.get("session_id")
    if event.get("role") in {"human", "user"}:
        return event.get("participant_id") or event.get("session_id")
    if event.get("role") == "system":
        return None
    return event.get("session_id")
