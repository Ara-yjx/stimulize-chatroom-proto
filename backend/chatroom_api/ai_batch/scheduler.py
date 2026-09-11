"""Pure speaker scheduling for AI-only conversations."""

from __future__ import annotations

import random

from chatroom_api.participants import participant_id


def first_speaker(participants: list[dict], rng: random.Random | None = None) -> dict:
    if len(participants) < 2:
        raise ValueError("AI-only conversations require at least two participants")
    return (rng or random).choice(participants)


def candidates_for_turn(
    participants: list[dict],
    last_speaker_id: str | None,
    rng: random.Random | None = None,
) -> list[dict]:
    """Return ordered candidates for the next accepted message.

    Two AIs alternate strictly. Three or more AIs are shuffled after excluding
    the previous speaker and may each choose silence in this order.
    """
    if len(participants) < 2:
        raise ValueError("AI-only conversations require at least two participants")
    if last_speaker_id is None:
        return [first_speaker(participants, rng)]

    eligible = [
        participant
        for participant in participants
        if participant_id(participant) != last_speaker_id
    ]
    if len(participants) == 2:
        if len(eligible) != 1:
            raise ValueError("last speaker is not an AI participant")
        return eligible
    (rng or random).shuffle(eligible)
    return eligible


def forced_candidate(
    candidates: list[dict],
    rng: random.Random | None = None,
) -> dict:
    if not candidates:
        raise ValueError("cannot force a speaker without candidates")
    return (rng or random).choice(candidates)
