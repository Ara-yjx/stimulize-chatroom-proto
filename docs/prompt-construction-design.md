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
