# Stimulize Chatroom — Low-Level Design

See [design.md](./design.md) for requirements, architecture decisions, and design rationale.
See [api-reference.md](./api-reference.md) for full API contracts.

## Project Structure

```
backend/                     # Chatroom Lambda API (Python)
  chatroom_api/
    handler.py               # Lambda entry point, API Gateway router
    auth.py                  # POST /auth/token
    chat.py                  # POST /chat/send, GET /chat/messages
    jwt_utils.py             # JWT sign/verify (HS256)
    bedrock_client.py        # Bedrock Converse API wrapper + retry
    conversation.py          # Multi-participant → Bedrock message mapping
    dynamo.py                # DynamoDB read/write (real)
    mock_dynamo.py           # DynamoDB in-memory mock
    rds.py                   # RDS chatroom setting + usage read/write
    mock_rds.py              # RDS static mock (in-memory)
    config.py                # Env vars
  tests/
  requirements.txt
  dev_server.py              # Local Flask wrapper

frontend/                    # Chat widget (TypeScript + jQuery)
  src/
    data/                    # Data layer (platform-agnostic)
      api.ts                 # HTTP calls to backend (/auth/token, /chat/send, /chat/messages)
      state.ts               # Widget state (token, session, history, participants)
      types.ts               # Shared interfaces (ChatMessage, InitOptions, etc.)
    ui/                      # UI layer (DOM rendering, subscribes to data layer)
      renderer.ts            # Bubble rendering, styles injection, scroll
      timer.ts               # Timer bar logic
      pairing.ts             # Pairing screen
    qualtrics/               # Qualtrics-specific integration
      embedded-data.ts       # Auto-write to Qualtrics ED on every message
      loader.ts              # jQuery.getScript wrapper
    index.ts                 # Entry point, wires data ↔ UI via callbacks, exports StimulizeChatroom
  dist/chatroom.min.js       # esbuild bundle
  test/index.html
  package.json, tsconfig.json

editor/                      # Editor UI (React + TypeScript + Vite + Arco Design)
  src/
    App.tsx                  # Router: /chatrooms, /chatrooms/:id
    pages/
      ChatroomList.tsx
      ChatroomEditor.tsx
    components/
      ScriptGenerator.tsx
      WidgetPreview.tsx
  package.json, tsconfig.json

cdk/                         # CDK (TypeScript)
  bin/app.ts
  lib/
    chatroom-api-stack.ts
    conversation-table-stack.ts
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
      role: "ai"
    }
  ],
  events: [
    {
      type: "system" | "message",
      session_id: "sess-uuid4",
      sender: "Participant1234",
      role: "human" | "ai" | "system",
      ai_participant_id: null | "ai_001",
      content: "hello",
      timestamp: 1711300000000,     // epoch ms
      created_at: "2026-04-18T..."  // ISO 8601
    }
  ],
  created_at: "...",
  updated_at: "...",
  ttl: 1790000000                   // epoch seconds = created_at + 2.5 years
}
```

TTL is set to `created_at + 2.5 years` to allow fetching conversation history from the editor website later. The TTL field is derived from `created_at`, so if the retention policy changes, existing items can be batch-updated by recalculating TTL from `created_at`.
```

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
  "mimic_human": true,
  "system_prompt": "...",
  "model_id": "global.anthropic.claude-sonnet-4-6",
  "simulate_pairing_seconds": 5,
  "timer_min_minutes": 5,
  "timer_max_minutes": 10
}
```

### RDS: `chatroom_usage` table

```sql
CREATE TABLE chatroom_usage (
  id              SERIAL PRIMARY KEY,
  chatroom_id     VARCHAR(64) NOT NULL REFERENCES chatroom(id),
  conversation_id VARCHAR(64) NOT NULL,
  session_id      VARCHAR(64) NOT NULL,
  input_tokens    INT NOT NULL,
  output_tokens   INT NOT NULL,
  total_tokens    INT NOT NULL,  -- input_tokens + output_tokens (raw count, no pricing multiplier)
  created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_usage_chatroom ON chatroom_usage(chatroom_id);
```

Note: Bedrock API response token counts are raw counts — they do NOT include model pricing multipliers. Actual cost calculation (multiplier per model) happens at billing query time, not at write time.


## JWT

- Algorithm: HS256
- Secret: AWS Secrets Manager (all environments including local dev)
  - For local testing: create a Secrets Manager secret or set `JWT_SECRET` env var as fallback. Code tries Secrets Manager first, falls back to env var.
- TTL: 3 hours
- Claims: `session_id`, `conversation_id`, `chatroom_id`, `iat`, `exp`


## Bedrock Integration

Using the Converse API for model-agnostic calls.

```python
response = bedrock.converse(
    modelId=setting["model_id"],
    messages=bedrock_messages,
    system=[{"text": system_prompt}],
    inferenceConfig={"maxTokens": 512, "temperature": 0.7},
)
```

Token usage: `response["usage"]["inputTokens"]`, `outputTokens`.

### Conversation History Mapping

Each AI sees the full history from its perspective:
- Own messages → `assistant` role
- Everything else (humans + other AIs) → `user` role, prefixed with `[nickname]`
- System events are skipped
- Consecutive same-role messages merged (Bedrock requires strict alternation)

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

On failure: record system event in DynamoDB (type: "system", content describes the error), return `error: true` in response. Frontend shows "Chatroom server error" inline.


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
2. `exchangeToken()` — POST `/auth/token` with `chatroom_id`
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
jQuery.getScript("https://chatroom.stimulize.org/chatroom.min.js", function() {
  StimulizeChatroom.init({ element: chatDiv, chatroomId: "scid_..." });
});
```

### Public API
- `StimulizeChatroom.init(options)` — mount widget
- `StimulizeChatroom.getHistory()` — structured array
- `StimulizeChatroom.getHistoryText()` — plain text with `[AI]`/`[SYS]` tags

### Avatar
Phase 1: emoji text characters assigned randomly from a pool (~20 emojis).
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
- `/chatrooms` — list all chatrooms with status (active/inactive)
- `/chatrooms/:id` — edit chatroom setting, generate script, preview widget

### Chatroom Editor Form
- Name, status toggle, mode dropdown
- Mimic human switch (default on)
- System prompt textarea
- Model ID (dropdown in follow-up)
- Simulate pairing seconds, timer min/max
- Generate Script button → Qualtrics-compatible JS snippet
- Widget Preview (iframe with blob URL)

### API
Editor talks to the management API (mock Flask in Phase 1, real Stimulize backend in Phase 2).
- `GET /chatrooms` — list
- `POST /chatrooms` — create
- `GET /chatrooms/:id` — get
- `PUT /chatrooms/:id` — update
- `DELETE /chatrooms/:id` — delete (or deactivate)


## CDK (TypeScript)

### ConversationTableStack
- DynamoDB table `chatroom-conversations`
- PK: `conversation_id` (S), PAY_PER_REQUEST, TTL on `ttl`

### ChatroomApiStack
- Lambda (Python 3.12, 256MB, 30s timeout, deployed into existing Stimulize VPC)
- Uses existing VPC to reach the same RDS as Stimulize backend (avoids VPC peering complexity). VPC ID, subnet IDs, and SG IDs passed as CDK context params.
- API Gateway: `POST /auth/token`, `POST /chat/send`, `GET /chat/messages`
- Custom domain: `chatroom.stimulize.org` with ACM certificate
- IAM: DynamoDB CRUD, Bedrock InvokeModel, RDS access, Secrets Manager read
- Env vars: `DYNAMODB_TABLE`, `JWT_SECRET_ARN`, `RDS_*` connection params, `BEDROCK_REGION`
- RDS Proxy for Lambda connection pooling
- NAT Gateway or VPC endpoints for Bedrock + DynamoDB access from VPC
- JWT secret created by CDK in Secrets Manager (auto-generated random value)


## Domain Setup: chatroom.stimulize.org

`stimulize.org` is registered on domain.com and the main site is hosted on GitHub Pages. We add `chatroom.stimulize.org` as a subdomain pointing to the chatroom API without migrating DNS.

Setup steps:
1. Create a custom domain in API Gateway for `chatroom.stimulize.org`
2. Request an ACM certificate for `chatroom.stimulize.org` (free, DNS validation)
3. On domain.com DNS: add the ACM validation CNAME record
4. After certificate is issued, on domain.com DNS: add CNAME `chatroom` → API Gateway custom domain name (e.g. `d-xxxx.execute-api.us-east-1.amazonaws.com`)
5. `chatroom.stimulize.org` now routes to the Lambda API

No Route 53 needed. No changes to the main `stimulize.org` GitHub Pages setup. Just two CNAME records on domain.com (one for ACM validation, one for the subdomain).

CDK can automate steps 1-2 (API Gateway custom domain + ACM certificate). Steps 3-4 are manual one-time DNS records on domain.com.


## Local Dev Setup

```
Terminal 1 — Mock management API (port 5000):
  cd backend && source .venv/bin/activate && python dev_server.py --mock-rds --port 5000

Terminal 2 — Chatroom backend (port 5001):
  cd backend && source .venv/bin/activate && python dev_server.py --port 5001

Terminal 3 — Editor UI:
  cd editor && npm run dev
  → http://localhost:5173

Test widget:
  Open frontend/test/index.html in browser

Env vars for local dev:
  USE_MOCK_DYNAMO=true
  USE_MOCK_RDS=true
  JWT_SECRET=dev-secret          # fallback when Secrets Manager unavailable
  BEDROCK_REGION=us-east-2
```
