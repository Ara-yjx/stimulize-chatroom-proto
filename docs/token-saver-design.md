# Token Saver Design

Status: partially implemented.

Implemented:

- prompt blocks are explicit
- static scaffold/examples were shortened
- Bedrock prompt cache is enabled for Claude Sonnet 4.6 model IDs
- cache checkpoint is placed in `messages[*].content` for the current Bedrock tool-use path

Pending:

- token-size instrumentation by prompt block
- rolling summary + recent window
- direct OpenAI/Anthropic adapters

## Goal

Reduce input tokens per AI tick without losing:

- replayability
- auditability
- provider portability
- per-AI persona consistency

This doc is about prompt and inference design. Billing details live in
[token-usage-and-billing-design.md](./token-usage-and-billing-design.md).


## Current Problem

Today each tick sends:

1. scaffold
2. topic instruction
3. additional prompt
4. per-AI persona
5. participant list
6. full visible history

This means:

- the static prompt is paid again every tick
- the history grows over time

For current Stimulize usage, the bigger problem is likely the static prompt:

- most chats are short, around 5 minutes
- many messages are short
- the examples/scaffold block is already large
- more examples may be added later


## Decision

Use an application-managed prompt, not provider-managed hidden conversation
state.

Optimize in this order:

1. reduce the static prompt
2. cache the static prompt where supported
3. add rolling summary + recent window only if history becomes the next cost
   driver

Reason:

- replay and debugging are easier
- provider lock-in stays lower
- prompt structure stays portable across Bedrock, OpenAI, and Anthropic


## Prompt Shape

Each tick should build prompt input from these blocks:

1. **Static prefix**
   - scaffold
   - examples
   - stable behavior rules
   - tool/output contract

2. **Semi-static setup**
   - topic instruction
   - additional prompt
   - participant list
   - AI name
   - persona

3. **Dynamic context**
   - rolling summary, if enabled
   - recent verbatim messages

4. **Tick trigger**
   - the instruction to decide whether to speak and call the `speak` tool


## Rough Token Math

These are planning estimates, not exact billing numbers.

Assume:

- static prompt: `4,000-6,000` tokens
- recent history: `800-2,000` tokens
- `10` AI ticks per session

Very rough conversion:

- `1 token ~= 0.75 word`
- `1,000 tokens ~= 750 words`

### Current Shape

Per tick:

```text
4,000-6,000 static
+   800-2,000 history
= 4,800-8,000 input tokens
```

Across 10 ticks:

```text
48,000-80,000 input tokens
```

Repeated static block alone:

```text
40,000-60,000 input tokens
```

### Phase 1: Reduce Static Prompt

Example:

- reduce static prompt from `5,000` to `2,500` tokens

Savings:

```text
about 2,500 tokens saved per tick
about 25,000 tokens saved per 10-tick conversation
about 40% lower input tokens in the middle-case example
```

Word equivalent:

```text
2,500 tokens ~= 1,875 words
25,000 tokens ~= 18,750 words
```

### Phase 2: Cache Static Prompt

After Phase 1, assume static prompt is `2,500` tokens.

Without caching:

```text
10 * 2,500 = 25,000 repeated static tokens
```

If repeated static prefix work after tick 1 is mostly cached, the savings
opportunity is:

```text
about 22,500 static tokens per 10-tick conversation
about 90% savings on repeated static block after tick 1
about 36% savings versus the uncached 62,000-token middle-case conversation
```

This is still only a prompt-size estimate. Exact dollars depend on provider
cache billing rules.

### Phase 3: Rolling Summary + Recent Window

This matters more once history gets larger.

Example:

- old history without summary: `3,000` tokens
- summary: `400` tokens
- recent window: `1,000` tokens

Then dynamic context becomes:

```text
400 summary + 1,000 recent = 1,400
```

instead of:

```text
3,000 old-history tokens
```

Savings:

```text
about 1,600 tokens per tick
about 53% reduction of the old-history portion
about 20-25% reduction of total per-tick input in the middle-case example
```

For current short chats, this is likely Phase 3, not Phase 1.


## How To Trim Examples In Phase 1

### Challenge

We do not currently have a strong enough eval system to measure the exact value
of each example one by one.

That means:

- we cannot cheaply score every example
- removing one example at a time would be slow and noisy
- Phase 1 needs a lower-cost method

### Solution

So Phase 1 should use a simpler process:

1. group examples by purpose:
   - tool-call format
   - turn-taking
   - persona behavior
   - safety or edge cases
2. keep one strong, short example per purpose
3. shorten examples before deleting them
4. remove examples in batches, not one by one
5. compare changes on a small fixed canary set

The canary set does not need to be large. Even `15-30` representative cases is
enough for Phase 1 if it covers:

- should speak vs should stay silent
- tool-call correctness
- persona consistency
- group turn-taking

This is cheaper and more reliable than trying to attribute exact value to each
individual example before we have a proper eval harness.


## Bedrock Cache Design

For Bedrock, the cache target should be a **cache-stable static prefix**.

### Cacheable Prefix

Put these into the cacheable prefix:

1. scaffold
2. examples
3. stable behavior rules
4. tool/output contract

Do not put these into the cacheable prefix:

- recent history
- rolling summary
- per-tick trigger
- anything volatile across ticks

### Bedrock Prompt Layout

```text
[cacheable static prefix]
[semi-static setup]
[dynamic context]
[tick trigger]
```

For the current Sonnet 4.6 Converse tool-use path, the cache checkpoint should
live in `messages`, not in `system` or `tools`.

Working layout:

- `system`: static scaffold/examples only
- leading `user` message:
  - semi-static setup
  - `cachePoint`
  - dynamic history block
- later messages: normal conversation history and trigger

Why keep the prefix narrow:

- scaffold/examples are the most reusable part
- persona and participant setup change more often
- a narrower prefix should give a better cache hit rate

### Bedrock Implementation Notes

At a high level:

1. build prompt blocks explicitly
2. keep the cacheable prefix byte-stable
3. measure token size by block:
   - static prefix
   - semi-static setup
   - summary
   - recent history
4. record enough usage metadata to compare cached vs uncached runs later


## OpenAI And Anthropic Support

This design should already support future direct inference through:

- Bedrock
- OpenAI API
- Anthropic API

Rule:

- prompt structure stays shared
- provider request syntax stays provider-specific

### Shared Internal Shape

Each tick should produce an internal request like:

```text
InferenceRequest
- provider
- model_id
- static_prefix_block
- semi_static_block
- summary_block
- recent_window_block
- trigger_block
- tool_schema
```

### Provider Adapter Responsibilities

Each provider adapter should:

1. turn prompt blocks into provider request format
2. use provider cache features if available
3. normalize usage back into common fields:
   - input tokens
   - output tokens
   - cache-related usage if available
4. normalize tool-call output back into the common runtime format

### Provider Notes

- **OpenAI**
  - use the same logical prompt blocks
  - map them into OpenAI's input/tool format
- **Anthropic direct API**
  - likely close to the current Bedrock/Anthropic shape
  - still use the shared prompt-builder model, not a Bedrock-only design


## Later Summary Design

Rolling summary is still useful later. It should be shared conversation state,
not per-AI private memory.

Suggested conversation-row fields:

```json
{
  "summary_text": "short summary of older turns",
  "summary_updated_at": "2026-05-26T12:34:56Z",
  "summary_source_visible_at": 1711300003000,
  "summary_turn_count": 18
}
```

Meaning:

- `summary_text`: current shared summary
- `summary_source_visible_at`: latest event already absorbed into the summary
- recent verbatim window starts after that point

Suggested beta rule:

- summarize only when needed
- for example:
  - more than `12` visible messages since the last summary, or
  - estimated prompt size above `6000` input tokens


## Migration Plan

### Phase 1

- separate static prefix from the rest of the prompt
- shorten scaffold/examples
- measure token composition:
  - static prefix
  - semi-static setup
  - dynamic history
  - total input

Status: prompt splitting and shortening are implemented; token composition
measurement is pending.

### Phase 2

- enable provider-side caching for the static prefix
- keep the cached block deterministic

Status: Bedrock cache is implemented for Claude Sonnet 4.6 model IDs. In live
probes, the cache checkpoint had to live in `messages[*].content` for the
current Converse tool-use path.

### Phase 3

- add rolling summary fields
- summarize only when prompt size needs it
- keep full-history fallback if summarization fails

Status: pending.


## Recommendation

Implement in this order:

1. split prompt into explicit blocks
2. reduce static prefix size
3. add Bedrock cache support for the static prefix
4. instrument token size by block
5. add rolling summary only if history becomes the next real cost driver
