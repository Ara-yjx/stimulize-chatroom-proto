"""Pure speaker scheduling for AI-only conversations."""

from __future__ import annotations

import random
from dataclasses import dataclass

from chatroom_api.participants import participant_id
from chatroom_api.ai_batch.completion import current_ticks


@dataclass(frozen=True)
class DecisionTurn:
    """One selected inference, or participant=None when every AI has agreed."""

    participant: dict | None
    require_message: bool = True
    allow_agreement: bool = True


def next_decision(conversation: dict, rng: random.Random | None = None) -> DecisionTurn:
    """Derive the next AI from facts, not a persisted random permutation.

    Until agreement occurs this is the old policy: alternate for two AIs;
    uniformly sample untried non-last speakers for three-plus, then force one.
    Confirm the last speaker only after all others agree to the same history.
    A new message resets ticks, so reconsideration needs no reverse action.
    """
    participants = conversation.get('participants') or []
    ids = {participant_id(p) for p in participants}
    if len(participants) < 2 or len(ids) != len(participants) or None in ids or '' in ids:
        raise ValueError('AI-only conversations require distinct participant identities')
    last = conversation.get('last_speaker_id')
    if last is None:
        return DecisionTurn(first_speaker(participants, rng), allow_agreement=False)
    if last not in ids:
        raise ValueError('last speaker is not an AI participant')
    ticks = current_ticks(conversation)
    attempted = {tick['ai_participant_id'] for tick in ticks}
    agreed = {tick['ai_participant_id'] for tick in ticks if tick['action'] == 'agree_to_end'}
    if ids <= agreed:
        return DecisionTurn(None)
    others = [p for p in participants if participant_id(p) != last]
    untried = [p for p in others if participant_id(p) not in attempted]
    if untried:
        return DecisionTurn((rng or random).choice(untried), require_message=len(participants) == 2)
    unconfirmed = [p for p in others if participant_id(p) not in agreed]
    if unconfirmed:
        return DecisionTurn((rng or random).choice(unconfirmed))
    return DecisionTurn(next(p for p in participants if participant_id(p) == last))


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
