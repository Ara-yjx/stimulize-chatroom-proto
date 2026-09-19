# Prompt Construction Design

This doc defines how the runtime builds one AI inference request. Token and
billing details live in [token-usage-and-billing-design.md](./token-usage-and-billing-design.md).

## Current Decision

Each tick is stateless. The backend rebuilds the prompt from the conversation
row, the chatroom setting snapshot, and the selected AI participant.

Prompt blocks:

1. Static scaffold
2. Chatroom topic
3. Per-AI persona
4. Participant list
5. Conversation history
6. Additional prompt

The dynamic conversation-history block includes the current UTC time. Every
visible event includes both its UTC display time and its relative age (for
example, `2026-07-15T10:00:00Z; 42 sec ago`). This gives the model an explicit
clock reference on every tick.

The static scaffold is selected by `mimic_human`:

- `mimic_human=true`: use the human-mimic scaffold and examples.
- `mimic_human=false`: remove human-mimic instructions/examples and use a
  short generic AI-assistant scaffold.
- When `mimic_human=false`, `ai_count=1`, and the latest visible message is
  human-authored, use the required-response assistant scaffold and a `speak`
  schema that requires at least one message. This variant cannot choose silence.

For Bedrock prompt caching, these are separate static prefixes. Cache hits for
one `mimic_human` mode do not apply to the other mode.
The required-response assistant scaffold also has distinct static content, so
its prompt-cache entry is separate from the silence-capable assistant scaffold.

AI-only batch inference derives its prompt mode from `human_count=0`. It omits
wall-clock and relative-age text because speaker order is worker-managed rather
than heartbeat-managed. AI-only mode ignores `mimic_human` and uses its own
semi-formal peer-discussion scaffold (see below).

Human-AI mimic prompts and multi-participant assistant prompts allow a gentle
topic nudge after the **whole room** has been silent for roughly 30 seconds.
They separately invite a quiet participant to share, without repeated pressure
or duplicating a recent invitation. These are model guidelines, not a forced
30-second timer; inference cadence and typing delay affect visible timing.

The human-AI tick additionally skips inference during the first 20 seconds
when there are no chat messages (system events do not count). Any human/AI
message lifts this opening-only gate; at 20 seconds normal eligibility resumes,
not forced speech. Duration expiry still takes precedence. AI-only batch workers
are unaffected.

Isolated-dev opening-gate check (2026-09-20): empty-room ticks at 0/8/16s
returned `initial_silence_window`; the 24s tick spoke, with its message visible
at 33.77s. With early human input, the 6.51s tick spoke and the reply appeared
at 16.25s. Both short test conversations were ended and rooms soft-deleted.
Only the isolated dev tick was deployed; the customer runtime was unchanged.

Isolated-dev probe (2026-09-20): two Sonnet 4.6 mimic-human 1:1 rooms,
55-second inference windows, 8-second tick cadence, no human messages. First
greetings appeared at 14.02s and 14.71s. Neither produced a follow-up; the last
model evaluations remained silent at 39.52s and 37.56s after the greeting.
Thus the soft wording alone did not demonstrate a reliable 30-second nudge.
Group invitation behavior has not yet been behaviorally validated.

For one-human/one-AI rooms with `mimic_human=false`, a cached setup rule tells
the AI to wait roughly 60 seconds after its unanswered message, then send at
most one brief check-in. The backend still runs inference on every eligible
heartbeat. When the approximate wait is reached, it requires a non-empty
check-in and marks the resulting event as `message_kind=idle_follow_up`; another
follow-up is not required until the human speaks again.

## Bedrock Prompt Caching

Supported Anthropic Claude and Amazon Nova models use Bedrock Converse API
`cachePoint` content blocks. The backend allowlist follows AWS's prompt-caching
guide and model cards, and accepts both base model IDs and cross-region
inference-profile IDs. Models have different minimum cacheable prefix sizes, so
support does not guarantee a cache hit for a short prompt.

Current implementation:

- Static scaffold stays in the Bedrock `system` block.
- A leading `user` message carries setup blocks and the cache point.
- That leading message currently contains: chatroom topic, persona,
  participant list, AI name, optional single-AI idle policy, additional prompt,
  `cachePoint`, then conversation history and its dynamic timing data.
- The normal conversation messages are prepended after that leading message.

Effect:

- The provider-managed cache is content-based; we do not create or name cache
  records ourselves.
- Cache reuse varies by prompt prefix content, so changes to topic, persona,
  participant list, AI name, or additional prompt can create a different cache
  prefix.
- This means the cache is effectively per resolved AI setup/version, not one
  global cache for the chatroom.

Known gap: the logical prompt order above says additional prompt follows
conversation history, but the cache path currently places it before the cache
point and before history. Product copy should describe additional prompt as a
general fine-tuning instruction until we either move it after history in the
cache path or intentionally keep it cached.

## AI Identity

Each resolved AI participant stores:

- `session_id`: runtime participant id
- `internal_name`: analysis label for export
- `nickname`: participant-visible display name
- `persona`: prompt instruction for this AI
- `model_id`: per-AI model override, or chatroom default
- `temperature`: per-AI temperature override, or chatroom default

`internal_name` appears in formatted exported history as:

```text
[John (condition1)] message text
```

The visible chat UI still shows the nickname only.

## Temperature

For the Bedrock beta path, temperature is clamped/validated to `0.0..1.0`.
Direct OpenAI and Anthropic API paths may use different provider limits later.

## Pending

- No automatic evaluation mechanism exists yet for deciding which examples to
  keep or remove from the human-mimic scaffold.
- Prompt variants for direct OpenAI/Anthropic providers are design-only for now.
# AI-only Discussion Rules (2026-09-19)

AI-only uses `prompts/ai_only.py`, independent of the stored `mimic_human` flag.
Write concise, complete sentences as peers exchanging ideas, not as an assistant.
Return one complete contribution, never multiple instant-message fragments.
There is no study/experiment narrative, texting shorthand, or emoji mimicry.
Two AIs alternate without silence examples. Three or more include substantive
speak/silence decisions and rewritten semi-formal examples; forced candidates
receive a one-message override. Human-AI scaffolds are unchanged. The initializer's
`info/prompt.md` includes the actual variants in fenced code blocks, and the
source hash includes `prompts/ai_only.py`. Legacy immutable TXT references remain
unchanged; a new batch is needed to capture a new prompt version.
