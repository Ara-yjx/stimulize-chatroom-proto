"""Multi-participant conversation → Bedrock message mapping.

Used by the tick handler (task 2.3) to build the per-AI per-tick Bedrock
``messages`` array. Pure — no I/O, no clock reads. ``now`` is injected so
behavior is deterministic given inputs (which is also what the property
tests in 3.6 rely on).
"""

from __future__ import annotations


def build_bedrock_messages(
    conv: dict, ai_session_id: str, now: int
) -> list[dict]:
    """Build the Bedrock Converse ``messages`` array for one AI per tick.

    Algorithm (per ``docs/design.md`` "Algorithmic Pseudocode → Bedrock
    history mapping"):

    1. Filter events to ``type == "message"`` AND ``visible_at <= now``.
       ``system`` and ``tick`` events are dropped — system context is in
       the system prompt, and tick events are server-internal audit only.
    2. For each event, choose role:
       - ``"assistant"`` if ``event.session_id == ai_session_id``.
       - ``"user"`` otherwise. The text is prefixed with
         ``"[" + sender + "] "``. From the AI's perspective, every
         non-self participant looks like a fellow human (no role marker
         leaks).
    3. Merge consecutive same-role messages with a newline separator.
       Bedrock requires strict alternation; merging keeps the role
       sequence valid no matter how the underlying events arrived.
    4. Convert to Bedrock shape:
       ``[{"role": role, "content": [{"text": text}]}]``.

    Args:
        conv: Conversation row. Reads ``events`` (list) and
            ``participants`` (list, optional, used only for the nickname
            fallback when an event has no ``sender``).
        ai_session_id: The AI's ``session_id``. Events authored by this
            session map to ``assistant``; everything else maps to
            ``user``.
        now: Epoch ms. Events with ``visible_at > now`` are dropped (a
            non-yet-visible AI message must not appear in the next AI's
            view of history either).

    Returns:
        A list of Bedrock message dicts whose roles strictly alternate
        ``user``/``assistant`` after the consecutive-same-role merge.
        Empty if no events qualify.
    """
    events = conv.get("events", []) or []
    # Step 1: filter to visible message events. ``visible_at`` falls back to
    # ``timestamp`` for events written by older callers that don't set it.
    visible = [
        e
        for e in events
        if e.get("type") == "message"
        and e.get("visible_at", e.get("timestamp", 0)) <= now
    ]

    # Nickname fallback for events that didn't stamp ``sender``.
    participants_by_session = {
        p["session_id"]: p
        for p in (conv.get("participants") or [])
        if "session_id" in p
    }

    out: list[dict] = []
    for e in visible:
        # Step 2: pick role + text.
        if e.get("session_id") == ai_session_id:
            role = "assistant"
            text = e.get("content", "")
        else:
            role = "user"
            sender = e.get("sender")
            if not sender:
                participant = participants_by_session.get(
                    e.get("session_id"), {}
                )
                sender = participant.get("nickname", "Participant")
            text = f"[{sender}] {e.get('content', '')}"

        # Step 3: merge consecutive same-role.
        if out and out[-1]["role"] == role:
            out[-1]["content"][0]["text"] += "\n" + text
        else:
            # Step 4: convert to Bedrock shape.
            out.append({"role": role, "content": [{"text": text}]})

    return out
