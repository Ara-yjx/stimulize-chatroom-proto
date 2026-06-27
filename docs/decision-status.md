# Decision and Implementation Status

This file records decisions that came from implementation/debug discussions after the original design docs. It is the short status index; detailed API and schema contracts remain in the dedicated docs.

## Current Source of Truth

- Runtime API contract: [api-chatroom.yml](./api-chatroom.yml)
- Management API contract: [api-management.yml](./api-management.yml)
- Runtime/backend LLD: [low-level-design.md](./low-level-design.md)
- Billing design: [token-usage-and-billing-design.md](./token-usage-and-billing-design.md)
- Prompt/token-saver design: [token-saver-design.md](./token-saver-design.md)

## Implemented

### Management Backend

- Real `Stimulize-backend` owns chatroom CRUD in beta.
- API route style is POST/action only:
  - `POST /api/createChatroom`
  - `POST /api/getChatrooms`
  - `POST /api/getChatroom/<id>`
  - `POST /api/updateChatroom/<id>`
  - `POST /api/deleteChatroom/<id>`
  - `POST /api/getChatroomUsage/<id>`
  - `POST /api/getUserUsage`
- Chatrooms are user-owned standalone rows in shared Stimulize Postgres.
- Runtime reads chatroom settings directly from RDS.
- Runtime writes usage rows directly to RDS; management API only aggregates reads.
- Admin-only `POST /api/getAdminBedrockUsage` aggregates recorded Bedrock cost by day/week/month across all users/chatrooms for AWS bill reconciliation.

### Runtime and Tick Loop

- Bedrock calls only happen in `chatroom-tick-handler`.
- `/chat/send` only writes human messages and returns visible events.
- Current heartbeat implementation is an EventBridge-scheduled Lambda loop:
  - EventBridge starts `chatroom-tick-heartbeat` every 15 minutes.
  - The heartbeat Lambda loops for a bounded window and async-invokes `chatroom-tick-handler`.
  - Reserved concurrency is 1 to avoid overlapping heartbeat loops.
- Active tick race control uses `active_tick_id` / `active_tick_until` on the conversation row.
- `max_duration_seconds` ends the conversation when exceeded.
- Runtime uses shared RDS public endpoint/credentials for beta.

### Prompt and Model Use

- Prompt is split into explicit blocks.
- Static scaffold/examples were shortened.
- Bedrock prompt cache is implemented for Claude Sonnet 4.6 model IDs.
- For Sonnet 4.6 tool-use calls, the cache checkpoint is in `messages[*].content`; live probes did not produce hits when the checkpoint was only in `system` or `tools`.
- Each AI participant gets a resolved `model_id`.
- A per-persona model override can replace the chatroom-level default model.
- If a persona model is null, runtime uses the chatroom default model.
- Persona assignment uses shuffled round-robin-style batches:
  - if personas >= AI count: sample without replacement
  - if personas < AI count: assign full shuffled rounds, then a final random partial round

### Widget

- Widget bundle is a single `chatroom.min.js` with inlined CSS.
- Beta hosted bundle URL is `https://ara-yjx.github.io/stimulize-chatroom-proto/chatroom.min.js`.
- Widget default runtime API is the beta API Gateway URL until DNS is ready:
  - `https://pmvb4orly5.execute-api.us-east-2.amazonaws.com/prod`
- `chatroom.stimulize.org` remains the future custom domain.
- Widget auto-writes Qualtrics Embedded Data when `Qualtrics?.SurveyEngine.setEmbeddedData` exists:
  - `QUALTRICS_CHATROOM_HISTORY`
  - `QUALTRICS_CHATROOM_HISTORY_JSON`
- Widget skips ED writes in local/GitHub Pages preview environments.
- Widget dedupes rare duplicate remote messages by `sender + content + timestamp + role`.

### Editor

- Editor uses `HashRouter` so GitHub Pages routes work under:
  - `https://ara-yjx.github.io/stimulize-chatroom-proto/#/chatroom`
- GitHub Pages build uses base path `/stimulize-chatroom-proto/`.
- Header has username/password login and shows username + logout after login.
- Management auth token is persisted in localStorage under `stimulize.editor.managementAuth`.
- Chatroom list/editor/usage routes are implemented.
- "Token Usage" opens a usage page with totals, daily/weekly/monthly selector, table, and simple chart.
- New chatroom defaults:
  - Simulate Pairing = 15 seconds
  - Timer Min = 1 minute
  - Timer Max = 5 minutes
  - `max_duration_seconds = (timer_max_minutes + 1) * 60`
- `Max Duration (sec)` is hidden in the editor and derived from Timer Max.
- Timer Max greater than 15 minutes shows a cost warning.

### Deploy

- GitHub Pages deployment builds both editor and widget from source.
- `scripts/build_pages_site.sh`:
  - builds `frontend/dist/chatroom.min.js`
  - builds the editor
  - copies the widget bundle into the Pages artifact root
- `.github/workflows/deploy-pages-site.yml` deploys the artifact through GitHub Pages Actions.
- Source branch workflow currently triggers on `main` pushes affecting editor/frontend/pages build files and on manual `workflow_dispatch`.

## Pending

### Runtime and Infra

- Custom DNS is not complete:
  - future API: `chatroom.stimulize.org`
  - future CDN: `cdn.stimulize.org`
- Production heartbeat hardening:
  - current beta heartbeat has one active loop via reserved concurrency
  - evaluate multi-runner leader election, sharding, or another scheduler before public launch
- Split conversation events out of the embedded DynamoDB conversation item before long/high-volume production usage.
- Add stronger operational alarms and explicit Bedrock/conversation spend guardrails.
- Confirm whether heartbeat interval should be 5s or 8s for the next deployment; source currently sets `HEARTBEAT_INTERVAL_SEC=5`.

### Billing

- Add first-class cache token columns and cost component columns from [token-usage-and-billing-design.md](./token-usage-and-billing-design.md).
- Add additive RDS migration for existing `chatroom_usage`; `db.create_all()` will not add columns.
- Extend pricing and usage adapters for direct OpenAI and Anthropic API-key inference.
- UI currently shows aggregate `input_tokens`, `output_tokens`, and estimated USD; future UI should separate uncached input, cache read, cache write, and output buckets.
- Add app-side hard budget enforcement only after backend estimated cost is reconciled against AWS billing. AWS Budget/SNS should be treated as an emergency brake because AWS Budget alerts are delayed.

### Prompt/Inference

- Add token-size instrumentation by prompt block.
- Keep full history for now; rolling summary + recent window remains future work.
- Add canary/eval checks before further large prompt-example trimming.
- Generalize inference adapters for direct OpenAI/Anthropic APIs.

### Editor and API Alignment

- Backend beta cap is `max_duration_seconds <= 900`.
- Editor hides `max_duration_seconds` and derives it from Timer Max, but the editor-side validation constant currently allows up to 3600. Align this with backend validation or intentionally document a wider editor draft range.
- Usage dashboard should later show cache token buckets once schema supports them.
- Customizable Qualtrics ED field names are still pending.

## Deprecated or Deferred Decisions

- Per-chatroom/channel key design is deferred. Beta uses chatroom ID plus a fixed beta client access key.
- Runtime no longer reads chatroom setting from widget config. It reads settings directly from RDS.
- Runtime no longer writes usage through the management API. It writes directly to RDS.
- `/api/chatroom/...` management routes were removed in favor of POST/action routes under `/api/actionName`.
- BrowserRouter/path-routing was replaced by HashRouter for GitHub Pages.
- CDN custom domain `cdn.stimulize.org` is deferred; beta uses `ara-yjx.github.io/stimulize-chatroom-proto`.
- Earlier ECS/Fargate heartbeat-container design is not the current deployed source shape; current source uses an EventBridge-scheduled heartbeat Lambda loop.
- Mock-management as deployed beta management service is deprecated. It remains useful for local/dev compatibility.
