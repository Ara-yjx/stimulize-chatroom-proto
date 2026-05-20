"""Shared constants for the chatroom API."""

EMOJI_POOL = [
    "🐱", "🐶", "🐻", "🐼", "🐨", "🦊", "🐯", "🦁", "🐸", "🐵",
    "🐔", "🐧", "🐦", "🦋", "🐢", "🐙", "🦀", "🐬", "🦄", "🐝",
]


# Tick handler / gate thresholds (epoch milliseconds).
MIN_SILENCE_MS = 5000  # gate skip if last visible event within this window
SAME_AI_COOLDOWN_MS = 5000  # gate skip if same AI just spoke within this
TICK_DEDUPE_WINDOW_MS = 4000  # tick handler idempotency guard

# Simulated typing-delay range per AI message (epoch milliseconds).
# AI message visible_at = author timestamp + cumulative delay drawn from this range.
MIN_DELAY_MS = 2000
MAX_DELAY_MS = 8000
