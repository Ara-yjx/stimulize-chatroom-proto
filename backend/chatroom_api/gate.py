"""Pure gate logic for the tick handler.

Decides whether some AI should speak at ``now`` given a conversation snapshot.
No I/O. No clock reads. ``now`` and any threshold values are injected.

The function returns a :class:`Decision` describing whether to skip and, when
not skipping, which AI participant should be asked to speak. The returned
shape is intentionally lightweight so it can be embedded in tick event records.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from chatroom_api.constants import MIN_SILENCE_MS, SAME_AI_COOLDOWN_MS


@dataclass(frozen=True)
class Decision:
    skip: bool
    reason: Optional[str] = None
    candidate_session_id: Optional[str] = None
    candidate_nickname: Optional[str] = None


def _event_visible_at(event: Dict[str, Any]) -> int:
    """Return the visible_at for an event, falling back to timestamp.

    v2 events may not carry a ``visible_at`` field; in that case the event
    becomes visible at its authoring time. Missing both returns 0 so the
    event sorts before any real timestamp.
    """
    return int(event.get("visible_at", event.get("timestamp", 0)) or 0)


def _filter_visible(events: Iterable[Dict[str, Any]], now: int) -> List[Dict[str, Any]]:
    return [e for e in events if _event_visible_at(e) <= now]


def _last_message_event(visible: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for event in reversed(visible):
        if event.get("type") == "message":
            return event
    return None


def _ai_last_batch_max_visible_at(
    events: List[Dict[str, Any]], session_id: str
) -> Optional[int]:
    """Max ``visible_at`` over the AI's most recent authored batch.

    A "batch" is the set of consecutive messages from this session sharing the
    same authoring ``timestamp`` (a multi-message turn). Returns ``None`` if
    the AI has never spoken.
    """
    last_ts: Optional[int] = None
    max_visible: Optional[int] = None
    # Walk events in reverse to find the last batch authored by this session.
    for event in reversed(events):
        if event.get("type") != "message":
            continue
        if event.get("session_id") != session_id:
            continue
        ts = int(event.get("timestamp", 0) or 0)
        if last_ts is None:
            last_ts = ts
        if ts != last_ts:
            break
        v = _event_visible_at(event)
        if max_visible is None or v > max_visible:
            max_visible = v
    return max_visible


def run_gate(
    conv: Dict[str, Any],
    now: int,
    min_silence_ms: int = MIN_SILENCE_MS,
    same_ai_cooldown_ms: int = SAME_AI_COOLDOWN_MS,
) -> Decision:
    """Decide whether some AI should speak at ``now``.

    ``conv`` shape (relevant fields)::

        {
          "participants": [{"session_id", "nickname", "role"}, ...],
          "events": [{"type", "session_id", "visible_at", "timestamp"}, ...],
          "last_speak_at_by_session": {session_id: epoch_ms},
        }

    Algorithm:

    1. Filter events to the visible-only set (``visible_at <= now``).
    2. If the most recent visible event is within ``min_silence_ms`` of ``now``,
       skip with ``reason="min_silence_not_elapsed"``.
    3. If the most recent visible message is from an AI whose most recent
       message is within ``same_ai_cooldown_ms`` of ``now``, skip with
       ``reason="ai_just_spoke"``.
    4. Compute candidate set: AI participants whose previous turn is fully
       visible (``max(visible_at)`` over their last batch is ``<= now``).
    5. If empty: skip with ``reason="all_candidates_typing"``. If there are no
       AI participants at all: skip with ``reason="no_ai_participants"``.
    6. Pick the AI with the smallest ``last_speak_at_by_session`` value
       (defaulting to 0 if absent), breaking ties by ``session_id`` ascending.
    """
    participants = conv.get("participants", []) or []
    ai_participants = [p for p in participants if p.get("role") == "ai"]
    if not ai_participants:
        return Decision(skip=True, reason="no_ai_participants")

    events = conv.get("events", []) or []
    visible = _filter_visible(events, now)

    # 2. min silence
    if visible:
        max_visible_at = max(_event_visible_at(e) for e in visible)
        if now - max_visible_at < min_silence_ms:
            return Decision(skip=True, reason="min_silence_not_elapsed")

    # 3. same AI just spoke
    last_msg = _last_message_event(visible)
    if last_msg is not None:
        sender_session_id = last_msg.get("session_id")
        sender = next(
            (p for p in participants if p.get("session_id") == sender_session_id),
            None,
        )
        if sender is not None and sender.get("role") == "ai":
            last_speak_map = conv.get("last_speak_at_by_session", {}) or {}
            last_speak_ms = last_speak_map.get(sender_session_id)
            if last_speak_ms is None:
                # Fall back to scanning the visible events for this AI's most
                # recent visible message.
                last_speak_ms = _event_visible_at(last_msg)
            if now - int(last_speak_ms) < same_ai_cooldown_ms:
                return Decision(skip=True, reason="ai_just_spoke")

    # 4. exclude AIs still typing their previous turn.
    last_speak_map = conv.get("last_speak_at_by_session", {}) or {}
    candidates = []
    for ai in ai_participants:
        session_id = ai.get("session_id")
        last_batch_max = _ai_last_batch_max_visible_at(events, session_id)
        if last_batch_max is not None and last_batch_max > now:
            continue
        candidates.append(ai)

    if not candidates:
        return Decision(skip=True, reason="all_candidates_typing")

    # 6. fairness: smallest last_speak_at, tie-break by session_id ascending.
    def _sort_key(ai: Dict[str, Any]):
        session_id = ai.get("session_id") or ""
        return (int(last_speak_map.get(session_id, 0) or 0), session_id)

    candidates.sort(key=_sort_key)
    chosen = candidates[0]
    return Decision(
        skip=False,
        candidate_session_id=chosen.get("session_id"),
        candidate_nickname=chosen.get("nickname"),
    )
