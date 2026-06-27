# Billing and Usage Design

Status: design target. Current implementation is simpler; see "Current gap".

## Goals

- Record one row per billable model invocation.
- Support Bedrock now, and direct OpenAI / Anthropic API-key inference later.
- Keep cross-provider aggregation simple: per user, chatroom, model, hour/day/week/month.
- Preserve provider-native usage payloads for debugging and bill reconciliation.
- Store write-time estimated cost so historical rows do not silently reprice when vendor rates change.

## Current gap

Current production/beta code writes `chatroom_usage` directly from the chatroom runtime Lambda to Stimulize Postgres.

Current persisted columns:

- `provider`
- `model_id`
- `pricing_key`
- `input_tokens`
- `output_tokens`
- `estimated_cost_usd`
- `currency`
- `raw_usage_json`

The runtime already reads Bedrock cache fields and includes cache read/write cost in `estimated_cost_usd`. However, cache token counts are only preserved in `raw_usage_json`; they are not first-class query columns yet. This makes the existing usage API useful for current totals, but weak for explaining why recorded cost differs from visible input/output token totals.

Future implementation should add the normalized columns below. Until then, the current schema remains the deployed source of truth.

## Storage decision

The chatroom runtime writes usage directly to RDS.

Options considered:

- Runtime -> management API -> RDS: simple API boundary, but adds an extra hop, an auth surface, and makes the management backend a write-through proxy for data the runtime already owns.
- Runtime -> RDS directly: keeps the billing event next to the provider invocation and makes idempotent `usage_event_id` inserts straightforward.

Decision: runtime -> RDS directly. The management API only reads and aggregates usage.

## Universal row model

Use a normalized billing table, not provider-specific union columns.

Each row means:

> One billable model invocation, normalized into common billing buckets, plus raw provider usage for audit/debug.

Avoid columns like:

```sql
bedrock_input_tokens
bedrock_cache_read_tokens
openai_prompt_tokens
anthropic_cache_creation_input_tokens
```

That design is sparse, harder to aggregate, and requires schema changes for every provider-specific usage field.

Prefer stable normalized columns:

```text
input_uncached_tokens
input_cached_read_tokens
input_cache_write_tokens
output_tokens
output_reasoning_tokens
```

Provider-specific fields still belong in:

```text
raw_usage_json
raw_response_metadata_json
```

## Future table shape

```sql
CREATE TABLE chatroom_usage (
  id                                   SERIAL PRIMARY KEY,
  usage_event_id                       VARCHAR(255) NOT NULL UNIQUE,

  owner_id                             INT NOT NULL REFERENCES users(id),
  chatroom_id                          VARCHAR(64) NOT NULL REFERENCES chatroom(id),
  conversation_id                      VARCHAR(255),
  session_id                           VARCHAR(255),

  provider                             VARCHAR(64) NOT NULL,
  api_surface                          VARCHAR(64),
  model_id                             VARCHAR(255) NOT NULL,
  pricing_key                          VARCHAR(255) NOT NULL,
  provider_request_id                  VARCHAR(255),
  region                               VARCHAR(64),
  service_tier                         VARCHAR(64),
  routing_type                         VARCHAR(64),

  input_uncached_tokens                INT NOT NULL DEFAULT 0,
  input_cached_read_tokens             INT NOT NULL DEFAULT 0,
  input_cache_write_tokens             INT NOT NULL DEFAULT 0,
  output_tokens                        INT NOT NULL DEFAULT 0,
  output_reasoning_tokens              INT NOT NULL DEFAULT 0,

  estimated_input_uncached_cost_usd    NUMERIC(18, 8) NOT NULL DEFAULT 0,
  estimated_input_cached_read_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
  estimated_input_cache_write_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
  estimated_output_cost_usd            NUMERIC(18, 8) NOT NULL DEFAULT 0,
  estimated_total_cost_usd             NUMERIC(18, 8) NOT NULL DEFAULT 0,
  currency                             VARCHAR(8) NOT NULL DEFAULT 'USD',
  pricing_version                      VARCHAR(64),
  unit_prices_json                     JSONB,

  invoked_at                           TIMESTAMPTZ NOT NULL,
  created_at                           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  raw_usage_json                       JSONB,
  raw_response_metadata_json           JSONB
);

CREATE INDEX idx_usage_chatroom ON chatroom_usage(chatroom_id);
CREATE INDEX idx_usage_chatroom_invoked_at ON chatroom_usage(chatroom_id, invoked_at);
CREATE INDEX idx_usage_owner_invoked_at ON chatroom_usage(owner_id, invoked_at);
CREATE INDEX idx_usage_provider_model_invoked_at ON chatroom_usage(provider, model_id, invoked_at);
```

Migration note: this must be an additive migration for shared RDS. `db.create_all()` will not add columns to an existing table.

## Provider mappings

### Bedrock

Bedrock Converse usage with prompt caching reports:

- `inputTokens`
- `outputTokens`
- `cacheReadInputTokens`
- `cacheWriteInputTokens`

Mapping:

```text
input_uncached_tokens    = usage.inputTokens
input_cached_read_tokens = usage.cacheReadInputTokens
input_cache_write_tokens = usage.cacheWriteInputTokens
output_tokens            = usage.outputTokens
```

Bedrock billing/CUR separates input, output, cache read, and cache write token types. Rate selection should include model, service tier, region/routing type, and whether cross-region inference was used.

### OpenAI API

OpenAI usage currently exposes cached prompt hits inside token detail fields.

Responses API style:

```text
input_tokens
output_tokens
input_tokens_details.cached_tokens
output_tokens_details.reasoning_tokens
```

Chat Completions style:

```text
prompt_tokens
completion_tokens
prompt_tokens_details.cached_tokens
completion_tokens_details.reasoning_tokens
```

Mapping:

```text
input_uncached_tokens    = input_tokens - cached_tokens
input_cached_read_tokens = cached_tokens
input_cache_write_tokens = 0
output_tokens            = output_tokens
output_reasoning_tokens  = reasoning_tokens
```

OpenAI does not expose a separate cache-write token bucket for normal prompt caching. Keep the provider-native object in `raw_usage_json` in case this changes.

### Anthropic API

Anthropic usage with prompt caching reports:

- `input_tokens`
- `output_tokens`
- `cache_creation_input_tokens`
- `cache_read_input_tokens`
- optional cache creation details such as TTL buckets
- optional metadata such as `service_tier` and `inference_geo`

Mapping:

```text
input_uncached_tokens    = usage.input_tokens
input_cached_read_tokens = usage.cache_read_input_tokens
input_cache_write_tokens = usage.cache_creation_input_tokens
output_tokens            = usage.output_tokens
```

If Anthropic returns TTL-specific cache creation buckets, store them in `raw_usage_json` and include TTL in `unit_prices_json` / `pricing_key` if rates differ.

## Cost computation

Cost must be computed from component buckets, not from one generic `total_tokens` number.

At write time:

1. Normalize provider usage into the common token buckets.
2. Resolve a pricing record by `provider`, `model_id`, `region`, `service_tier`, `routing_type`, and cache TTL where relevant.
3. Compute component costs:
   - uncached input cost
   - cached read input cost
   - cache write input cost
   - output cost
4. Store component costs, `estimated_total_cost_usd`, `pricing_version`, and `unit_prices_json`.
5. Store the original provider usage in `raw_usage_json`.

Query-time totals:

```text
total_input_tokens = input_uncached_tokens
                   + input_cached_read_tokens
                   + input_cache_write_tokens

total_output_tokens = output_tokens

total_model_tokens = total_input_tokens + total_output_tokens
```

UI should show at least:

- input tokens
- cache read tokens
- cache write tokens
- output tokens
- approximate total USD

Do not make "total tokens" the primary billing number. The buckets have different prices.

## Reconciliation notes

Recorded cost is an estimate, not guaranteed invoice truth.

Expected gaps:

- Provider invoices may aggregate by hour/day and do not always expose per-request billing rows.
- Provider usage fields may separate cache reads/writes differently from the visible "input/output" totals.
- Cross-region routing, service tier, discounts, credits, or pricing changes may not be fully captured by a hardcoded table.
- Cost Explorer and CUR can lag request-time logs.

The design keeps enough metadata to reconcile by provider/model/token-type/time bucket later.

## References

- Bedrock prompt caching: https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html
- Bedrock CUR token types: https://docs.aws.amazon.com/bedrock/latest/userguide/cost-mgmt-understanding-cur-data.html
- OpenAI prompt caching: https://platform.openai.com/docs/guides/prompt-caching
- Anthropic prompt caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
