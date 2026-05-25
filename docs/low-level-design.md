# Stimulize Chatroom — Low-Level Design

See [design.md](./design.md) for requirements, architecture decisions, and design rationale.
See [api-reference.md](./api-reference.md) for full API contracts.

## Project Structure

```
backend/                     # Chatroom Lambda API (Python)
  chatroom_api/
    handler.py               # Lambda entry point, API Gateway router
    auth.py                  # POST /auth/token (one_on_one + group lobby flow)
    chat.py                  # POST /chat/send, GET /chat/messages
    lobby.py                 # Lobby DDB read/write + close_lobby subroutine
    mock_lobby.py            # Lobby in-memory mock for local dev
    tick_handler.py          # Async-invoked by heartbeat; runs gate + Bedrock + DDB writes
    gate.py                  # Pure gate logic (min_silence, fairness, candidate pick)
    prompts/
      speech_scaffold.py     # Platform-managed speech rules + tool spec + examples
    jwt_utils.py             # JWT sign/verify (HS256)
    bedrock_client.py        # Bedrock Converse API wrapper + retry + speak tool config
    conversation.py          # History rendering, visible_at filter, multi-participant -> Bedrock mapping
    dynamo.py                # Conversation DDB read/write (real)
    mock_dynamo.py           # In-memory mock
    rds.py                   # RDS chatroom setting + usage read/write
    mock_rds.py              # In-memory mock
    config.py                # Env vars
    constants.py             # EMOJI_POOL, default prompts, gate thresholds
    errors.py                # ChatroomNotFoundException, InactiveChatroomException, etc.
  tick_loop/                 # Heartbeat container (separate Python entry; no Lambda runtime)
    tick_loop.py             # Loops every N seconds; queries status-index; async-invokes tick handler
    Dockerfile
    requirements.txt
  tests/
  requirements.txt
  dev_server.py              # Local Flask wrapper (also spawns local heartbeat thread)

mock_management/             # Mock chatroom management API (Phase 1 beta)
  app.py                     # Flask app (chatroom CRUD, usage)
  store.py                   # SQLite-backed storage (WAL mode)
  auth.py                    # Bearer token middleware
  Dockerfile
  requirements.txt
  tests/

frontend/                    # Chat widget (TypeScript + jQuery)
  src/
    data/                    # Data layer (platform-agnostic)
      api.ts                 # HTTP calls to backend (/auth/token, /chat/send, /chat/messages)
      state.ts               # Widget state (token, session, history, lobby state, callbacks)
      types.ts               # Shared interfaces (ChatMessage, InitOptions, etc.)
    ui/                      # UI layer (DOM rendering, subscribes to data layer)
      renderer.ts            # Bubble rendering, styles injection, scroll, visible_at scheduling
      timer.ts               # Timer bar logic
      pairing.ts             # Lobby/pairing screen with countdown
      reconnect.ts           # "Reconnecting..." banner after 30s of failures
      ended.ts               # Conversation-ended state UI
    qualtrics/               # Qualtrics-specific integration
      embedded-data.ts       # Auto-write to Qualtrics ED on every message
      loader.ts              # jQuery.getScript wrapper
    index.ts                 # Entry point, wires data ↔ UI via callbacks, exports StimulizeChatroom
  dist/chatroom.min.js       # esbuild bundle
  test/index.html
  package.json, tsconfig.json

editor/                      # Editor UI (React + TypeScript + Vite + Arco Design)
  src/
    App.tsx                  # Router: /chatroom, /chatroom/:id
    pages/
      ChatroomList.tsx
      ChatroomEditor.tsx
    components/
      ScriptGenerator.tsx
      WidgetPreview.tsx      # Iframe + blob URL; uses dev override for apiBaseUrl in dev mode
    api.ts                   # Management API client with bearer token
  package.json, tsconfig.json

cdk/                         # CDK (TypeScript)
  bin/app.ts
  lib/
    conversation-table-stack.ts
    lobby-table-stack.ts
    secrets-stack.ts
    chatroom-api-stack.ts
    tick-handler-stack.ts
    tick-heartbeat-stack.ts
    mock-management-stack.ts
  package.json, tsconfig.json, cdk.json
```


## Data Schemas

### DynamoDB: `chatroom-conversations`

Partition key: `conversation_id` (S)

```
{
  conversation_id: "conv-uuid4",
  chatroom_id: "scid_uuid4",
  chatroom_setting: { ... },      // snapshot of setting at session creation
  status: "active" | "ended",     // ticking stops when "ended"
  started_at: "...",              // ISO 8601, set when lobby closes
  active_tick_id: null | "aws-request-id",     // set while a tick handler owns this conversation
  active_tick_until: null | 1711300060000,     // epoch ms; expired active ticks may be taken over
  last_tick_started_at: 1711300000000,         // epoch ms, observability/cadence
  last_tick_completed_at: 1711300003000,       // epoch ms, observability/cadence
  last_speak_at_by_session: {     // for fairness selection in gate
    "ai_001": 1711299990000
  },
  participants: [
    {
      session_id: "sess-uuid4",
      nickname: "Participant1234",
      avatar: { emojiText: "🐱" },   // future: emojiImg: "https://...jpg"
      role: "human"
    },
    {
      session_id: "ai_001",
      nickname: "Participant5678",
      avatar: { emojiText: "🐶" },
      role: "ai",
      persona: "upenn sophomore, asian studies minor, casual tone",  // injected each tick to prevent drift
      model_id: "global.anthropic.claude-sonnet-4-6"                // per-AI override resolved at conversation start
    }
  ],
  events: [
    {
      type: "system" | "message" | "tick" | "lobby_created",
      session_id: "sess-uuid4",
      sender: "Participant1234",
      role: "human" | "ai" | "system",
      ai_participant_id: null | "ai_001",
      content: "hello",
      timestamp: 1711300000000,     // epoch ms — when the event was generated (authoring time)
      visible_at: 1711300003000,    // epoch ms — when the event becomes visible to participants and AIs.
                                    //            For human messages and system events: equals timestamp.
                                    //            For AI messages: timestamp + simulated typing delay (2-8s,
                                    //            stacked across multi-message turns) to mimic human typing.
                                    //            Events with visible_at > now are filtered out everywhere
                                    //            (UI render, gate input, history rendered to next AI tick).
      created_at: "2026-04-18T...", // ISO 8601
      // tick-only fields (omit for message/system):
      chosen_session_id: "ai_001" | null,
      gate_decision: "skip" | "consider",
      skip_reason: "min_silence_not_elapsed" | "ai_just_spoke" | null,
      ai_decision: "speak" | "silent" | null,
      bedrock_invoked: true,
      input_tokens: 320,
      output_tokens: 18,
      // optional link from message events:
      triggered_by_tick_id: "tick-uuid"
    }
  ],
  created_at: "...",
  updated_at: "...",
  ttl: 1790000000                   // epoch seconds = created_at + 2.5 years
}
```

GSI: `status-index` (PK: `status`). Sparse — only `status="active"` rows. Heartbeat queries this. `ended` rows fall out, keeping the GSI hot regardless of historical volume.

Tick events (`type: "tick"`) are recorded for full audit (every gate skip, every "asked but silent" decision, token costs). `/chat/messages` filters them out before returning to clients; they are never exposed to AI history either.

`lobby_created` events are written as the very first event when a lobby closes (group mode). They carry the lobby's `created_at` timestamp + pairing config (target_human_count, ai_join_strategy, ai_strategy_value, max_wait_seconds) so researchers can audit "how long did this cohort wait?". Filtered out of `/chat/messages` and AI history by the same audit-event rule as ticks; admin callers (`?include_ticks=true` with the admin bearer) see them.

**Beta storage guardrail**: events remain embedded in the conversation item for beta to keep implementation small. To reduce the chance of hitting DynamoDB's 400KB item limit during beta, the editor and management API cap `max_duration_seconds` at 900 seconds (15 minutes). Before production, move events to an append-only `chatroom-events` table and keep only conversation metadata/state in `chatroom-conversations` (see Beta -> Prod TODO).

TTL is set to `created_at + 2.5 years` to allow fetching conversation history from the editor website later. The TTL field is derived from `created_at`, so if the retention policy changes, existing items can be batch-updated by recalculating TTL from `created_at`.
```

### DynamoDB: `chatroom-lobbies` (group mode only)

Partition key: `lobby_id` (S, UUID)

A chatroom is long-lived; cohorts go through it many times. Each cohort has its own lobby. At most one lobby per chatroom is `status="open"` at a time, but many historical lobby rows exist per chatroom over the chatroom's lifetime. `lobby_id` keeps them distinguishable for audit. The `chatroom_id-status-index` GSI (sparse on `status="open"`) returns the at-most-one current open lobby for a chatroom_id.

```
{
  lobby_id: "lob-uuid4",
  chatroom_id: "scid_uuid4",
  conversation_id: "conv-uuid4",        // pre-allocated, becomes the conversation PK on close
  status: "open" | "closing" | "closed" | "aborted",
  target_human_count: 4,
  ai_join_strategy: "fixed_ai_count" | "total_participant_count",
  ai_strategy_value: 2,                 // F or P depending on strategy
  max_wait_seconds: 60,
  actual_human_count: 2,
  participants: [
    { session_id, nickname, avatar, joined_at, last_seen_at }
  ],
  deadline_at: 1711300600000,           // epoch ms = first_join + max_wait_seconds
  created_at, updated_at,
  closed_at: null,
  ttl: 1718900000                       // epoch s = closed_at + 180 days (or created_at + 180d if never closed)
}
```

GSIs:
- `chatroom_id-status-index` — sparse on `status="open"`. Used by `/auth/token` to find an open lobby for a chatroom_id. Closed/aborted rows drop out of this GSI, keeping query cost flat as history grows.
- `conversation_id-index` — used by `/chat/messages` to find the lobby when the conversation row doesn't exist yet.

Lobby rows are kept for ~180 days post-close for audit. Pruning of stale participants only happens while `status="open"`; once closed, the participants list is frozen even if humans disconnect.

**Beta**: `last_seen_at` is recorded on every `/chat/messages` poll while in lobby (client heartbeat = 10s; stale threshold = 30s), but **the actual prune step in `close_lobby` is no-op**. All joiners count toward `actual_human_count` regardless of staleness. Schema is forward-compatible; pruning is enabled in prod (see prod TODO).

### RDS: `chatroom` table

```sql
CREATE TABLE chatroom (
  id            VARCHAR(64) PRIMARY KEY,  -- scid_uuid4
  owner_id      VARCHAR(64) NOT NULL,
  name          VARCHAR(255) NOT NULL,
  status        VARCHAR(16) NOT NULL DEFAULT 'active',  -- active | inactive
  setting       JSON NOT NULL,  -- all chatroom settings as JSON
  created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
```

The `setting` JSON column contains:
```json
{
  "mode": "one_on_one",
  "topic_instruction": "Anything about your college life.",
  "ai_personas": [
    {
      "persona": "upenn sophomore, asian studies minor, casual tone",
      "model_id": null
    },
    {
      "persona": "ucsd sophomore, economics major, very concise",
      "model_id": "global.anthropic.claude-sonnet-4-6"
    }
  ],
  "model_id": "global.anthropic.claude-sonnet-4-6",
  "simulate_pairing_seconds": 5,
  "timer_min_minutes": 5,
  "timer_max_minutes": 10
}
```

For `mode: "group"`, additional fields:
```json
{
  "mode": "group",
  "target_human_count": 4,
  "ai_join_strategy": "total_participant_count",
  "ai_strategy_value": 6,
  "max_wait_seconds": 60,
  "max_duration_seconds": 600
}
```
- `ai_strategy_value` semantics depend on `ai_join_strategy`: total participant count (compensates) vs fixed AI count.
- `max_duration_seconds` applies to both modes. Tick handler stops ticking and ends the conversation past this deadline. Beta hard cap: 900 seconds (15 minutes).
- For `mode: "one_on_one"`, the group fields are **stored denormalized** with fixed values (`target_human_count=1, ai_join_strategy="fixed_ai_count", ai_strategy_value=1, max_wait_seconds=0`). The chat Lambda branches on the group fields uniformly and never re-derives from `mode`.

### RDS: `chatroom_usage` table

**Write-path decision**

- Option A: tick/runtime Lambda writes usage through the management API.
- Option B: tick/runtime Lambda writes usage directly to Stimulize Postgres.
- Decision: **Option B**.
- Reason:
  - the runtime already knows the exact provider/model/token counts at the moment cost is incurred,
  - direct Postgres avoids an extra network hop and auth layer,
  - idempotent `usage_event_id` inserts are simpler when the runtime owns the write.
- Consequence: management API exposes only aggregated read endpoints (`getChatroomUsage`, `getUserUsage`). It is not part of the write path.

```sql
CREATE TABLE chatroom_usage (
  id                 SERIAL PRIMARY KEY,
  usage_event_id     VARCHAR(255) NOT NULL UNIQUE,
  owner_id           INT NOT NULL REFERENCES users(id),
  chatroom_id        VARCHAR(64) NOT NULL REFERENCES chatroom(id),
  conversation_id    VARCHAR(255),
  session_id         VARCHAR(255),
  provider           VARCHAR(64) NOT NULL,
  model_id           VARCHAR(255) NOT NULL,
  pricing_key        VARCHAR(255) NOT NULL,
  input_tokens       INT NOT NULL,
  output_tokens      INT NOT NULL,
  estimated_cost_usd NUMERIC(18, 8) NOT NULL,
  currency           VARCHAR(8) NOT NULL DEFAULT 'USD',
  invoked_at         TIMESTAMPTZ NOT NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  raw_usage_json     JSONB
);
CREATE INDEX idx_usage_chatroom ON chatroom_usage(chatroom_id);
CREATE INDEX idx_usage_chatroom_invoked_at ON chatroom_usage(chatroom_id, invoked_at);
CREATE INDEX idx_usage_owner_invoked_at ON chatroom_usage(owner_id, invoked_at);
```

Note: Bedrock API response token counts are raw counts — they do NOT include model pricing multipliers. The runtime applies the backend's hardcoded provider/model pricing table at write time and stores `estimated_cost_usd` in each usage row so historical rows do not silently reprice when vendor rates change later.


## JWT

- Algorithm: HS256
- Secret: AWS Secrets Manager (all environments including local dev)
  - For local testing: create a Secrets Manager secret or set `JWT_SECRET` env var as fallback. Code tries Secrets Manager first, falls back to env var.
- TTL: 3 hours (fixed). Independent of `max_duration_seconds`. If a researcher sets `max_duration_seconds > 3h`, the JWT expires mid-conversation — flagged as a prod TODO to revisit if researchers want longer sessions.
- Claims: `session_id`, `conversation_id`, `chatroom_id`, `iat`, `exp`
- `conversation_id` is **pre-allocated at lobby creation** (group mode) or at conversation creation (one_on_one). The JWT carries it from the start so clients can poll `/chat/messages` even before the conversation row exists. The lobby's `conversation_id-index` GSI is used to find the lobby state during the lobby phase.


## Bedrock Integration

Bedrock Converse API is invoked exclusively from the **tick handler**. `/chat/send` no longer triggers Bedrock — it only appends the human message and returns. AI replies arrive on subsequent `/chat/messages` polls, driven by ticks.

The tick handler call site (illustrative):

```python
response = bedrock.converse(
    modelId=chosen_ai.model_id or setting["model_id"],
    messages=bedrock_messages,           # built from filtered events (visible_at <= now)
    system=[{"text": full_system_prompt}],   # SCAFFOLD + TOPIC + PERSONA + CONTEXT
    toolConfig=SPEAK_TOOL_CONFIG,        # forced tool use
    inferenceConfig={"maxTokens": 512, "temperature": 0.7},
)
```

Token usage: `response["usage"]["inputTokens"]`, `outputTokens` — recorded into the corresponding `tick` event and written as one `chatroom_usage` row per billable model invocation.

### Conversation History Mapping

Each AI's `messages` array is built per tick from events filtered to `visible_at <= now`:
- The current AI's own messages → `assistant` role.
- Everything else (humans + other AIs) → `user` role, with content prefixed by the participant's nickname only (e.g. `[Mars] hi there`). The prefix is just a display name — no `human`/`ai` marker leaks. From this AI's perspective, every other participant is indistinguishable from a human.
- `system` events are skipped.
- `tick` events are skipped.
- Consecutive same-role messages are merged with newline separators (Bedrock requires strict alternation).

### Error Classification

Retryable (exponential backoff, max 3 attempts, base 1s):
- `ThrottlingException` — Bedrock rate limit exceeded
- `ModelTimeoutException` — Bedrock inference timeout
- `ServiceUnavailableException` — Bedrock service down

Fatal — Bedrock errors (no retry):
- `ExpiredTokenException` — AWS credentials expired
- `ValidationException` — invalid model ID or malformed request

Fatal — Application errors (no retry):
- `InactiveChatroomException` — chatroom status is inactive
- `ChatroomNotFoundException` — chatroom ID not found in RDS

On failure during a tick: the tick handler appends a `tick` event with `bedrock_invoked=true, ai_decision=null, error=<class>` and a `system` event describing the error to the conversation. The conversation continues — the next tick still fires. The frontend renders the `system` event inline ("Chatroom server error: ..."). No `error: true` field is returned in the API response since `/chat/send` doesn't trigger inference.


## Group Mode Pairing

Implements the design in [design.md](./design.md#group-chatroom). Lobby state lives in `chatroom-lobbies`; deadline is enforced by client polling, not SQS.

### `POST /auth/token` (group mode branch)

```python
validate access_key against CHATROOM_CLIENT_ACCESS_KEY secret
load chatroom from RDS
if mode == "one_on_one": existing flow, return immediately

# group mode
loop:
  lobby = query chatroom_id-status-index for status="open"
  if lobby found and now >= lobby.deadline_at:
    close_lobby(lobby)              # freshness check
    continue                        # retry: now no open lobby
  if no usable lobby:
    lobby = create new (status=open, deadline_at = now + max_wait_seconds,
                       conversation_id = new uuid, participants = [])
  # atomically join
  try UpdateItem:
    Condition: status="open" AND actual_human_count < target_human_count
    Update:    actual_human_count += 1; append participant to list
  if conditional fail: retry loop
  break

if post-update actual_human_count == target_human_count:
  close_lobby(lobby)                # synchronous close, capacity reached

return JWT + session_info + lobby_state
```

### `GET /chat/messages`

```python
decode JWT -> conversation_id
conv = GetItem(chatroom-conversations, conversation_id)
if conv exists:
  # also opportunistically update last_seen_at if caller is in lobby (no-op if conv exists)
  return events after ?after, no lobby block

# conversation not yet created; check lobby
lobby = Query(conversation_id-index)
if lobby.status == "open":
  if now >= lobby.deadline_at:
    close_lobby(lobby)
    conv = GetItem(...); return events
  update last_seen_at for this session
  return empty events + lobby = { status, actual_human_count, target_human_count, deadline_at }
if lobby.status in ("closing", "closed"):
  return empty events           # next poll will see the conversation row
if lobby.status == "aborted":
  return 410 Gone               # frontend shows "no one else joined"
```

### `close_lobby(lobby)` — idempotent subroutine

Called from three sites: `/auth/token` capacity-reached, `/auth/token` freshness check, `/chat/messages` freshness check.

```python
1. UpdateItem lobby: status open -> closing
   ConditionExpression: status = "open"
   on conditional fail: another closer won; exit silently (idempotent)

2. read full lobby (fresh)
   prune participants whose last_seen_at < now - STALE_THRESHOLD_SEC
   if pruned count == 0: mark status="aborted"; exit

3. ai_count = compute_ai_count(strategy, value, actual_human_count after prune)
   ai_participants = generate(ai_count)   # nicknames + avatars + prompts

4. PutItem chatroom-conversations
   ConditionExpression: attribute_not_exists(conversation_id)
   participants = humans + ai_participants
   events = [session_started, "X joined" * N]
   chatroom_setting snapshot
   on conditional fail: another closer already wrote it; continue

5. UpdateItem lobby: status closing -> closed; closed_at = now
```

The `attribute_not_exists` guard on step 4 makes the close idempotent under racing closers. Only one writer succeeds; others no-op.

### Notes

- `STALE_THRESHOLD_SEC = 30s`. Client heartbeats `last_seen_at` via `/chat/messages` every 10s while in lobby. Pruning logic is implemented in the schema but **not enforced in beta** — only enabled before opening the system to wider users (see prod TODO).
- `max_wait_seconds` upper bound: no SQS limit applies (client-driven). Reasonable cap in editor: 600s.
- Late joiner during `closing`/`closed`: conditional update on join fails, joiner forms a new lobby. Automatic.
- Refresh during wait: client loses session_id, rejoins as new participant. Acceptable for v1.

### Lobby UX (widget)

While in lobby, the widget shows the same animated dots screen used for `simulate_pairing_seconds` ("Finding a chat partner..."). No counts, no countdown — researchers don't want to telegraph that other humans may not arrive.

When `/chat/messages` returns 410 Gone (lobby `aborted` because no humans remained at deadline, or pruning left 0 humans), the widget replaces the pairing screen with an **aborted state**:
- Message: "No one else joined this chatroom."
- Button: "Reconnect" — clicking it tears down the current widget instance and re-runs `init()` with the same options. The user joins as a new participant in a fresh lobby (server creates one on `/auth/token` if none open).

The reconnect button is the only recovery affordance. Once aborted, polling stops; it does not auto-retry.


## Async AI Conversation Flow

Implements the design in [design.md](./design.md#async-ai-conversation-flow-tick-model). Two new components:

- **`chatroom-tick-heartbeat`** — ECS Fargate task, single-instance, fires ticks.
- **`chatroom-tick-handler`** — Lambda, runs the gate + Bedrock call for one conversation.

### Heartbeat container

```python
# tick_loop.py
INTERVAL = int(os.environ["HEARTBEAT_INTERVAL_SEC"])  # default 5
LAMBDA = os.environ["TICK_HANDLER_LAMBDA"]
MAX_FAILURES = 3

ddb = boto3.client("dynamodb")
lam = boto3.client("lambda")
fail_count = 0

while True:
    try:
        now = int(time.time() * 1000)
        convs = ddb.query(
            TableName="chatroom-conversations",
            IndexName="status-index",
            KeyConditionExpression="status = :s",
            ExpressionAttributeValues={":s": {"S": "active"}},
        )["Items"]
        fail_count = 0
        for c in convs:
            active_until = int(c.get("active_tick_until", {"N": "0"})["N"])
            if active_until > now:
                continue  # best-effort prefilter; Lambda still does authoritative acquire
            lam.invoke(
                FunctionName=LAMBDA,
                InvocationType="Event",
                Payload=json.dumps({"conversation_id": c["conversation_id"]["S"]}),
            )
    except Exception as e:
        fail_count += 1
        if fail_count >= MAX_FAILURES:
            sys.exit(1)  # ECS restarts the task
    time.sleep(INTERVAL)
```

Env: `HEARTBEAT_INTERVAL_SEC` (default 5), `TICK_HANDLER_LAMBDA`, `AWS_REGION`.
IAM: `dynamodb:Query` on `status-index`, `lambda:InvokeFunction` on the tick handler.
Resources: 256 CPU / 512 MB. `desiredCount=1`.

### Tick handler

```python
def handler(event, context):
    cid = event["conversation_id"]
    tick_id = context.aws_request_id
    now = int(time.time() * 1000)
    active_until = now + 60000

    # 1. Acquire the active-tick slot before any Bedrock reasoning.
    #    Heartbeat prefilter is advisory only; this conditional write is authoritative.
    try:
        ddb.update_item(
            Key={"conversation_id": cid},
            UpdateExpression=(
                "SET active_tick_id = :tick_id, "
                "active_tick_until = :active_until, "
                "last_tick_started_at = :now"
            ),
            ConditionExpression=(
                "#status = :active AND "
                "(attribute_not_exists(active_tick_until) OR active_tick_until < :now)"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":active": "active",
                ":tick_id": tick_id,
                ":active_until": active_until,
                ":now": now,
            },
        )
    except ConditionalCheckFailedException:
        return

    try:
        conv = get_conversation(cid)

        # 2. max-duration check
        if conv.started_at + conv.setting.max_duration_seconds * 1000 < now:
            update_status(cid, "ended", condition_active_tick_id=tick_id)
            return

        # 3. gate (prose: min silence elapsed? same AI didn't just speak?)
        decision = run_gate(conv, now)
        if decision.skip:
            append_tick_event(cid, gate_decision="skip", skip_reason=decision.reason)
            return

        # 4. Bedrock Converse with `speak` tool
        candidate = decision.candidate
        response = bedrock.converse(
            modelId=conv.setting.model_id,
            messages=build_history_for(candidate, conv),
            system=[{"text": prompt_for(candidate)}],
            toolConfig=SPEAK_TOOL_CONFIG,
        )
        messages = parse_speak_tool_call(response)  # [] if silent

        # 5. record tick + messages, conditionally on still owning active_tick_id
        append_tick_event(cid,
            chosen_session_id=candidate.session_id,
            gate_decision="consider",
            ai_decision="speak" if messages else "silent",
            bedrock_invoked=True,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            condition_active_tick_id=tick_id,
        )
        for m in messages:
            append_message_event(cid, candidate, m, triggered_by_tick_id=...,
                                 condition_active_tick_id=tick_id)
        if messages:
            update_last_speak_at(cid, candidate.session_id, now,
                                 condition_active_tick_id=tick_id)
    finally:
        # 6. Release only if this handler still owns the active tick.
        release_active_tick(cid, tick_id)
```

Bedrock retry/error classification reuses existing `bedrock_client.py` logic. On fatal errors the tick handler appends a `system` event and lets the conversation continue (next tick still fires).

`release_active_tick(cid, tick_id)` removes `active_tick_id` and `active_tick_until`, and sets `last_tick_completed_at`, with `ConditionExpression="active_tick_id = :tick_id"`. If the condition fails, another handler has already taken over and this handler must not clear its ownership marker.

The active tick is a timeout-based ownership marker, not a permanent lock. If a Lambda crashes or times out after setting `active_tick_id`, the conversation recovers when `active_tick_until` expires and the next heartbeat invokes a new handler. All final writes that mutate tick-owned state must condition on `active_tick_id = tick_id`; if that check fails, this handler lost ownership and must not append AI messages or update `last_speak_at_by_session`.

### Tick admin endpoint (debugging)

Tick events are stored in the conversation row but filtered out of `/chat/messages` for clients. To debug "why didn't AI speak at t=120?" without raw DDB access, the chatroom Lambda exposes a debug query parameter:

```
GET /chat/messages?include_ticks=true
Authorization: Bearer <admin-token>
```

When `include_ticks=true` is present AND the bearer is the admin token (separate secret from JWT), the response includes `tick` events alongside `message` and `system`. Without the admin token, the parameter is ignored. Saves a lot of debugging time once researchers start asking "why is Mars quiet".

### CORS

API Gateway is configured with `Access-Control-Allow-Origin: *` (no credentials). Beta only — Qualtrics survey origins vary widely (`*.qualtrics.com`, custom domains, preview iframes), and we don't transmit cookies. Tighten to an allowlist before prod (see prod TODO).

### `speak` tool definition (Bedrock Converse `toolConfig`)

```json
{
  "tools": [
    {
      "toolSpec": {
        "name": "speak",
        "description": "Decide what messages (if any) to send right now. Pass an empty array to stay silent.",
        "inputSchema": {
          "json": {
            "type": "object",
            "properties": {
              "messages": {
                "type": "array",
                "items": { "type": "string" },
                "description": "Zero or more message texts. Multiple items become multiple bubbles."
              }
            },
            "required": ["messages"]
          }
        }
      }
    }
  ],
  "toolChoice": { "tool": { "name": "speak" } }
}
```

Forcing `toolChoice` ensures the model always returns structured output.

### Prompt structure (per-AI per-tick)

Each Bedrock Converse call is stateless — Bedrock holds no state across ticks. The full prompt is reassembled every tick:

```
SPEECH_SCAFFOLD            # platform-managed: tool use, silence rules, examples
+ CHATROOM_TOPIC_INSTRUCTION  # researcher-supplied per chatroom (e.g. "chat about college life")
+ PER_AI_PERSONA           # auto-generated identity facts (school, major, gender), persisted on conversation row
+ PARTICIPANTS             # <participants> block listing every nickname (caller annotated "(you)")
+ CONVERSATION_CONTEXT     # <your-name>, <conversation-history> with now-relative timestamps
+ ADDITIONAL_PROMPT        # researcher-supplied free-form, appended after history (last-mile reminders)
```

- `SPEECH_SCAFFOLD` and the `speak` tool config are not exposed to researchers — they live in our codebase and are versioned with the platform.
- `CHATROOM_TOPIC_INSTRUCTION` is the researcher's chatroom setting (`topic_instruction` in RDS). The backend wraps it in a `# Chatroom topic` block before stitching it into the system prompt.
- `PER_AI_PERSONA` is generated at conversation start and stored on the conversation row so each AI keeps a consistent identity across ticks. Without this, AIs drift across ticks (different school, different major) since Bedrock conversations are stateless. The persona is drawn from the chatroom setting's `ai_personas` pool using a round-robin-style assignment: without replacement when the pool has at least `ai_count` entries; otherwise assign whole shuffled rounds of the pool first, then fill the final partial round without replacement; empty pool → no persona block.
- Each selected AI also gets a resolved `model_id` on its participant row. If the chosen persona entry omits `model_id`, the chatroom-level default `model_id` is copied onto that participant at conversation creation time. Ticks then read the participant's `model_id` first and fall back to the chatroom default.
- `PARTICIPANTS` lists every nickname in the room (no role markers — see "AIs don't know who else is AI"). Without this, an AI can't notice a participant who never speaks, defeating the inclusivity rules in the scaffold.
- `CONVERSATION_CONTEXT` is built fresh every tick. Timestamps in `<conversation-history>` are computed as `now - event.timestamp`, so the model sees correct relative deltas at every call.
- `ADDITIONAL_PROMPT` is researcher-supplied free-form text (`additional_prompt` in RDS). Lands AFTER the history so last-mile reminders ("stay one-thought-per-turn") are the most recent thing the model sees before deciding what to say. Optional; omitted from the prompt when empty.

**Drift prevention**: do NOT truncate `<conversation-history>` during a conversation. Each tick must include the full message history. Truncation causes identity drift and topic-loop. Token cost per tick grows linearly, which is the explicit tradeoff. Conversation length is bounded by `max_duration_seconds`.

**Inclusivity rule**: the scaffold instructs AIs to notice silent participants and invite them in with group-addressed phrasing (no name targeting), with a ~15s cooldown after another participant has just invited them. Pairs with the `<participants>` block — without that block, an AI can't reliably see a participant who hasn't spoken.

### Simulated typing delay

To mimic human typing tempo, AI messages get a `visible_at` timestamp later than their `timestamp` (authoring time). Random delay 2-8s per message. For a multi-message turn, delays **stack** so the messages reveal one after another, not simultaneously:

```
turn returned at t=10s with 3 messages and delays [3, 5, 2]:
  msg1.timestamp = 10000   visible_at = 13000
  msg2.timestamp = 10000   visible_at = 18000
  msg3.timestamp = 10000   visible_at = 20000
```

Filtering rule: any consumer of `events` (UI render, gate input, history rendered into the next AI's prompt) excludes events where `visible_at > now`. This keeps the AI's perception of the conversation aligned with what users see.

Human messages and system events have `visible_at == timestamp`. They become visible the moment they're recorded.

Side effect: an AI is in "still typing" state until the last message of its turn becomes visible. The gate adds a rule — skip an AI candidate if its previous turn isn't fully visible yet (`max(visible_at) > now` for that AI's last batch). Otherwise the same AI could be force-picked twice while the user still sees it "typing".

Future polish: scale delay by message length (~30-50 wpm). Out of scope for v1.

### Local dev

`dev_server.py` runs the heartbeat as a daemon thread instead of a container, calling the handler in-process with the same code path:

```python
def _local_heartbeat():
    while True:
        for cid in list_active_conversations():
            tick_handler({"conversation_id": cid}, None)
        time.sleep(int(os.environ.get("HEARTBEAT_INTERVAL_SEC", 5)))

threading.Thread(target=_local_heartbeat, daemon=True).start()
```

No ECS, no Lambda async invoke locally.

### CDK additions (recap)

These are landed in the per-stack sections above; restated here for quick reference.

- New Lambda: `chatroom-tick-handler` (Python 3.12, in same VPC as chat Lambda, Bedrock + DynamoDB IAM).
- New ECS Fargate cluster (or reuse existing) + task definition + service (`desiredCount=1`).
- New GSI on `chatroom-conversations`: `status-index`.
- New DynamoDB table: `chatroom-lobbies` with two GSIs (`chatroom_id-status-index`, `conversation_id-index`).
- Container image built from `backend/tick_loop/` (small Python image).


## Frontend Widget Internals

### Architecture: Data / UI Decoupling

The data layer and UI layer are fully decoupled. They communicate through a callback interface:

```
data/state.ts  ──callbacks──▶  ui/renderer.ts
  onMessage(msg)                 appendBubble()
  onSystemEvent(evt)             appendSystemBubble()
  onError(err)                   appendErrorBubble()
  onParticipantJoin(p)           showJoinNotice()
  onTimerTick(elapsed)           updateTimerBar()
  onSessionReady(info)           hidePairingScreen()
```

`index.ts` wires them together:
- Creates the data layer (state + api)
- Creates the UI layer (renderer + timer + pairing)
- Registers UI callbacks on data layer events
- Registers Qualtrics ED writer as another callback (if in Qualtrics environment)

This allows:
- Testing data layer without DOM
- Swapping UI implementation (jQuery → React) without touching data logic
- Adding new consumers (e.g. analytics) by registering more callbacks

### Init Flow
1. `_injectStyles()` — inject CSS into `<head>`
2. `exchangeToken()` — POST `/auth/token` with `chatroom_id` and beta `access_key`
3. Read `session_id`, `conversation_id` from JWT payload (base64 decode)
4. Read `nickname`, `avatar`, chatroom setting from `/auth/token` response
5. Show pairing screen (if configured), wait N seconds
6. `renderChatroom()` — inject HTML structure
7. `startPolling()` — poll `/chat/messages` every 3s
8. `_startTimer()` — show timer bar if configured
9. Auto-write conversation to Qualtrics Embedded Data on every message

### jQuery Compatibility
Widget uses `jQuery` global (not `$`) because Qualtrics has Prototype.js claiming `$`.
```typescript
const _$ = (typeof jQuery !== "undefined" ? jQuery : $) as JQueryStatic;
```

### Script Loading in Qualtrics
Use `jQuery.getScript()` for simpler loading:
```javascript
jQuery.getScript("https://cdn.stimulize.org/chatroom.min.js", function() {
  StimulizeChatroom.init({ element: chatDiv, chatroomId: "scid_..." });
});
```

### Public API
- `StimulizeChatroom.init(options)` — mount widget
- `StimulizeChatroom.getHistory()` — structured array
- `StimulizeChatroom.getHistoryText()` — plain text with `[AI]`/`[SYS]` tags

`init()` must only be called once per page. Multi-mount (more than one chatroom on a single Qualtrics page) is not supported in v1; the global `StimulizeChatroom` namespace assumes a single instance.

### API hostname configuration

The widget talks only to the chatroom backend API (`/auth/token`, `/chat/send`, `/chat/messages`). It never talks to the management API. The default hostname is `https://chatroom.stimulize.org`.

`init()` accepts an optional `beta` flag. When `beta: true`, the widget renders an API hostname input box at widget start (before token exchange) and only proceeds once the user clicks "Start". This lets researchers point the widget at a non-prod backend during beta testing without a rebuild:

```javascript
StimulizeChatroom.init({
  element: chatDiv,
  chatroomId: "scid_...",
  beta: true        // shows hostname input box; default value pre-filled
});
```

Without `beta: true`, the widget always uses `https://chatroom.stimulize.org` and never shows the input. The flag is the only way to override the hostname in production builds.

### Reconnection UX

Polling failures (network, transient 5xx) are silently retried. If `/chat/messages` continuously fails for **30 seconds**, the widget overlays a non-blocking "Reconnecting…" banner. When a successful response arrives, the banner disappears. No "give up" state — the widget keeps trying as long as the user keeps the tab open.

### Conversation end UX

When `/chat/messages` returns `conversation_status: "ended"`, the widget:
- Disables the message input.
- Disables the send button.
- Appends a final system bubble: "This conversation has ended."
- Continues polling once to fetch any tail-end events, then stops.

### Avatar
Beta: emoji text characters assigned randomly from a pool (~20 emojis).
Phase 1.1 follow-up: replace with image avatars.

### CSS Strategy

Options considered:
- A) Separate CSS file loaded via `<link>` tag: clean separation, browser caches independently. But extra HTTP request, need to know CSS URL, risk of FOUC.
- B) CSS inlined in JS bundle: single file, no extra request, styles guaranteed before rendering. Slightly larger bundle, can't cache CSS separately.
- C) CSS extracted by esbuild, JS auto-loads from same path: clean separation, auto-discovered. But `document.currentScript.src` doesn't work reliably in Qualtrics.

Decision: Option B (inline in JS bundle). For a widget running in Qualtrics' unpredictable environment, single-file with zero external dependencies is safest. CSS is ~2KB so bundle impact is negligible. Future style customization via CSS custom properties (variables) that the host page can override.

Source structure: CSS lives in a separate file (`src/ui/styles.css`) for clean project organization. The build step (esbuild) imports it as a string and inlines it into `chatroom.min.js`. The JS injects a `<style>` tag at runtime. This gives clean source separation while producing a single distributable file.


## Editor UI

Uses `@arco-design/web-react` (v2.66.7) to match the main Stimulize editor project.

### Pages
- `/chatroom` — list all chatrooms with status (active/inactive)
- `/chatroom/:id` — edit chatroom setting, generate script, preview widget

### Chatroom Editor Form
- Name, status toggle, mode toggle (`one_on_one` / `group`)
- Mimic human switch (default on)
- System prompt textarea
- Model ID dropdown (Anthropic, Amazon Nova, Llama, DeepSeek, Qwen, Google, Mistral)
- Simulate pairing seconds, timer min/max, `max_duration_seconds`
- Group-only: `target_human_count`, `ai_join_strategy` radio, `ai_strategy_value`, `max_wait_seconds`
- Generate Script button → Qualtrics-compatible JS snippet
- Widget Preview (iframe with blob URL; "Launch another preview" button mounts side-by-side iframes for multi-participant testing)

### Form validation

Editor enforces these client-side; the management API re-validates server-side as the source of truth:
- `target_human_count >= 1`
- `total_participant_count >= target_human_count` (if `ai_strategy = total_participant_count`)
- `max_wait_seconds <= 600`
- `max_duration_seconds <= 900` for beta (15-minute hard cap; revisit in prod after event-history storage is split out)
- `ai_strategy_value >= 0` and `<= 7` (cap on AI count for cost safety)
- For `mode: "one_on_one"`, settings are auto-derived: `target_human_count=1`, `ai_strategy=fixed_ai_count`, `value=1`, `max_wait_seconds=0`. The 1-on-1 form is a UI preset over the same group settings.

### Management API hostname configuration

The editor reads `VITE_MOCK_MGMT_URL` from build env (default: `http://localhost:5000` in dev). For beta deployment, the URL is baked into the build.

The editor authenticates to the management API with a bearer token from `VITE_MOCK_MGMT_TOKEN`. Token rotation requires a rebuild — acceptable for internal beta.

For testing arbitrary backend hostnames during development, the editor exposes a dev-mode-only input field (visible when `import.meta.env.DEV`) that overrides the API hostname for the session. Hidden in production builds.

### Mode UX

The editor surfaces mode as a toggle (`one_on_one` / `group`):
- `one_on_one` form shows: name, model, topic_instruction, simulate_pairing_seconds, timer min/max, max_duration_seconds. Group fields hidden.
- `group` form additionally shows: target_human_count, ai_join_strategy radio (`fixed_ai_count` / `total_participant_count`), ai_strategy_value, max_wait_seconds.
- `simulate_pairing_seconds` is hidden in group mode when `target_human_count > 1` — the lobby provides a real wait (`max_wait_seconds`), so a cosmetic wait on top would be confusing. With `target_human_count = 1` (1-on-1 preset under the hood, or a degenerate group room) the field stays visible because there is no real wait and the simulated pairing screen is the only pacing.
- On save, the management API stores both sets of fields. For `one_on_one`, group fields are denormalized to fixed values (`target_human_count=1`, `ai_join_strategy=fixed_ai_count`, `ai_strategy_value=1`, `max_wait_seconds=0`) so the chat Lambda can branch uniformly on group fields without re-deriving from `mode`.

### Editor → Management API

Editor talks to the management API (mock Flask container in beta, real Stimulize backend in prod). All requests carry `Authorization: Bearer <token>`.

- `POST /api/getChatrooms` — list
- `POST /api/createChatroom` — create
- `POST /api/getChatroom/:id` — get
- `POST /api/updateChatroom/:id` — update
- `POST /api/deleteChatroom/:id` — deactivate


## CDK (TypeScript)

Stacks (one file per stack, see Project Structure):

### ConversationTableStack
- DynamoDB table `chatroom-conversations`. PK: `conversation_id` (S). PAY_PER_REQUEST. TTL on `ttl`.
- GSI `status-index` (sparse on `status="active"`). Used by the heartbeat to find tickable conversations.
- Fields `active_tick_id` and `active_tick_until` are used as the per-conversation active tick marker. The heartbeat may prefilter on `active_tick_until`, but the tick handler must conditionally acquire it before invoking Bedrock.

### LobbyTableStack
- DynamoDB table `chatroom-lobbies`. PK: `lobby_id` (S). PAY_PER_REQUEST. TTL on `ttl` (180 days).
- GSI `chatroom_id-status-index` (sparse on `status="open"`).
- GSI `conversation_id-index`.

### SecretsStack
- JWT secret (HS256) — random value generated at first deploy.
- Beta chatroom client access key — fixed beta value required by `/auth/token` before issuing a JWT.
- Mock-management bearer token — random value, surfaced as env var to chatroom Lambda + heartbeat container, and as build-time secret to editor.
- Admin bearer token (for `?include_ticks=true` debugging).

### ChatroomApiStack
- Lambda `chatroom-api` (Python 3.12, 256MB, 30s timeout, deployed into existing Stimulize VPC).
- API Gateway HTTP API: `POST /auth/token`, `POST /chat/send`, `GET /chat/messages`.
- CORS: `Access-Control-Allow-Origin: *`, no credentials.
- Custom domain: `chatroom.stimulize.org` with ACM certificate.
- IAM: DynamoDB R/W on conversation + lobby tables; Bedrock InvokeModel; RDS read; Secrets Manager read.
- Env vars: `CONVERSATION_TABLE`, `LOBBY_TABLE`, `JWT_SECRET_ARN`, `ADMIN_TOKEN_SECRET_ARN`, `RDS_*`, `BEDROCK_REGION`, `MGMT_API_URL`, `MGMT_API_TOKEN_SECRET_ARN`.
- Also reads `CHATROOM_CLIENT_ACCESS_KEY_SECRET_ARN` for the fixed beta widget access key used by `/auth/token`.
- RDS Proxy for Lambda connection pooling.
- VPC endpoints (or NAT Gateway) for Bedrock + DynamoDB + Secrets Manager from VPC.

### TickHandlerStack
- Lambda `chatroom-tick-handler` (Python 3.12, same VPC, same IAM as chatroom-api except no API Gateway).
- Async invoke target only — no API Gateway integration.
- Reserved concurrency: default for beta (revisit in prod).

### TickHeartbeatStack
- ECS Fargate cluster (or reuse existing).
- Task definition: 256 CPU / 512 MB. Image built from `backend/tick_loop/Dockerfile`.
- Service: `desiredCount=1`, no load balancer.
- Env: `HEARTBEAT_INTERVAL_SEC` (default 5), `TICK_HANDLER_LAMBDA`, `CONVERSATION_TABLE`, `AWS_REGION`.
- IAM: `dynamodb:Query` on `status-index`, `lambda:InvokeFunction` on tick handler.

### MockManagementStack (beta only)
- ECS Fargate cluster (reuse heartbeat cluster) + task definition for `mock_management/Dockerfile`.
- Service: `desiredCount=1` (SQLite is single-writer; multi-task corrupts).
- EFS mount for SQLite file persistence across task replacements.
- ALB + ACM cert for `mock-mgmt.stimulize.org`.
- Env: `BEARER_TOKEN_SECRET_ARN`, `SQLITE_PATH=/data/chatrooms.db`.
- Marked clearly as beta-only; replaced by real Stimulize backend in prod.


## Domain Setup

Two subdomains, both managed via CNAME on domain.com (no Route 53). No changes to the main `stimulize.org` GitHub Pages setup.

### `chatroom.stimulize.org` — chatroom API

Routes to API Gateway (chatroom Lambda).

1. Create a custom domain in API Gateway for `chatroom.stimulize.org`.
2. Request an ACM certificate for `chatroom.stimulize.org` (free, DNS validation).
3. On domain.com DNS: add the ACM validation CNAME record.
4. After certificate is issued, on domain.com DNS: add CNAME `chatroom` → API Gateway custom domain (e.g. `d-xxxx.execute-api.us-east-1.amazonaws.com`).

### `cdn.stimulize.org` — widget bundle

Hosts `chatroom.min.js`. Beta uses GitHub Pages from a separate repo (e.g. `stimulize-widget-cdn`).

1. Create a separate GitHub repo for the widget bundle.
2. Configure GitHub Pages on the repo, custom domain = `cdn.stimulize.org`, enforce HTTPS.
3. On domain.com DNS: add CNAME `cdn` → `<github-username>.github.io`.
4. Build pipeline pushes `dist/chatroom.min.js` to the repo on each release.

GitHub Pages handles cert provisioning. Cache invalidation on update is via filename versioning (`chatroom.min.js?v=1.2.3`) since GH Pages doesn't expose CDN headers. CloudFront migration is in the prod TODO if needed.

### `mock-mgmt.stimulize.org` — beta management API

Routes to ALB → Fargate container (mock management API). Same pattern: ACM cert validation CNAME + final CNAME to ALB DNS name. Beta only.

CDK can automate API Gateway custom domain + ACM. Domain.com DNS records are manual one-time setup.


## Local Dev Setup

```
Terminal 1 — Mock management API (port 5000):
  cd mock_management && source .venv/bin/activate && python app.py

Terminal 2 — Chatroom backend (port 5001) — also spawns local heartbeat thread:
  cd backend && source .venv/bin/activate && python dev_server.py --port 5001

Terminal 3 — Editor UI:
  cd editor && npm run dev
  → http://localhost:5173

Test widget:
  Open frontend/test/index.html in browser

Env vars for local dev (set in shell or `.env.local`):
  USE_MOCK_DYNAMO=true
  USE_MOCK_RDS=true
  USE_MOCK_LOBBY=true
  JWT_SECRET=dev-secret              # fallback when Secrets Manager unavailable
  BEDROCK_REGION=us-east-2
  HEARTBEAT_INTERVAL_SEC=5            # local heartbeat thread cadence
  TICK_HANDLER_LOCAL=true             # call tick handler in-process (no Lambda async invoke)
  MGMT_API_URL=http://localhost:5000
  MGMT_API_TOKEN=dev-mgmt-token       # bearer token shared with mock management
  ADMIN_TOKEN=dev-admin-token         # for /chat/messages?include_ticks=true

Editor (.env.local):
  VITE_MOCK_MGMT_URL=http://localhost:5000
  VITE_MOCK_MGMT_TOKEN=dev-mgmt-token
  VITE_DEV_API_OVERRIDE=true          # show dev hostname-override input
```
