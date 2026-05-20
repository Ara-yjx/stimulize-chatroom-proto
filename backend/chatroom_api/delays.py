"""Simulated typing-delay helpers.

`compute_visible_at` is the formal helper that turns an authoring
timestamp `t0` and a list of per-message random delays into a list of
`visible_at` timestamps. The result is strictly monotonically increasing
so multi-message AI turns reveal one bubble at a time, not all at once.

`pick_delays_ms` generates a list of N random delays uniformly in
[MIN_DELAY_MS, MAX_DELAY_MS]. Pure on the random seed; the caller passes
`random` itself (or a seeded one) so tests can be deterministic.
"""

from __future__ import annotations

import random as _random_mod
from typing import Iterable, List

from chatroom_api.constants import MIN_DELAY_MS, MAX_DELAY_MS


def compute_visible_at(t0: int, delays: Iterable[int]) -> List[int]:
    """Stack delays onto t0 to produce strictly increasing visible_at values.

    Pre:
      - every d in delays satisfies MIN_DELAY_MS <= d <= MAX_DELAY_MS.
      (Validation enforced; raises ValueError if violated. The PBT in
      task 2.11 generates inputs respecting the precondition.)
    Post:
      - len(out) == len(delays).
      - out[0] >= t0 + MIN_DELAY_MS.
      - out[i] - out[i-1] >= MIN_DELAY_MS.
      - strictly monotonically increasing.
    """
    out: List[int] = []
    cumulative = 0
    for d in delays:
        if d < MIN_DELAY_MS or d > MAX_DELAY_MS:
            raise ValueError(
                f"delay {d} out of range [{MIN_DELAY_MS}, {MAX_DELAY_MS}]"
            )
        cumulative += int(d)
        out.append(int(t0) + cumulative)
    return out


def pick_delays_ms(n: int, rng: "_random_mod.Random | None" = None) -> List[int]:
    """Sample N integer delays in [MIN_DELAY_MS, MAX_DELAY_MS]."""
    if n <= 0:
        return []
    r = rng if rng is not None else _random_mod
    return [r.randint(MIN_DELAY_MS, MAX_DELAY_MS) for _ in range(n)]
