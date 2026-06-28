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

The static scaffold is selected by `mimic_human`:

- `mimic_human=true`: use the human-mimic scaffold and examples.
- `mimic_human=false`: remove human-mimic instructions/examples and use a
  short generic AI-assistant scaffold.

For Bedrock prompt caching, these are separate static prefixes. Cache hits for
one `mimic_human` mode do not apply to the other mode.

## Bedrock Prompt Caching

Some Bedrock models support prompt caching through the Converse API
`cachePoint` content block. We use this only for models explicitly listed in
the backend.

Current implementation:

- Static scaffold stays in the Bedrock `system` block.
- A leading `user` message carries setup blocks and the cache point.
- That leading message currently contains: chatroom topic, persona,
  participant list, AI name, additional prompt, `cachePoint`, then
  conversation history.
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
