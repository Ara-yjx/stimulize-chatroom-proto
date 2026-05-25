# Stimulize Chatroom Requirement

## Background

**Qualtrics** is an online survey platform. Surveys can be exported/imported as qsf files and allow embedding JavaScript.

**Stimulize** is a web editor that lets psychologists create interactive experiments hosted on Qualtrics.
- Users drag-and-drop to define experiment displays - no code - then export as qsf and distribute via Qualtrics.
- Current backend: Flask on EC2 with RDS.

## Problem Summary

- Psychologists wants to add AI Chatroom in their Qualtrics experiments (for priming, etc.). 
- The AI participants should pretend human to the participant.


## Scope

- **Beta (v1)** — current target
  - Group participant mode (1-on-1 is a UI preset over the group settings)
  - Standalone mode (portable script, independent of Stimulize trial)
  - Chatroom ID + fixed beta client access key — no per-chatroom "Channel" or "Key"
  - Chatroom settings cloud-managed via mock management API (Fargate + SQLite + bearer token)
  - Auto-generated participant nicknames + emoji avatars (random)
  - Async AI flow via the tick model (heartbeat container + tick handler Lambda)
  - Conversation recorded in Qualtrics ED
  - Lobby-based pairing (`target_human_count`, `ai_join_strategy`, `max_wait_seconds`)
  - `max_duration_seconds` cap per chatroom; beta hard cap is 15 minutes
  - Researcher-facing audit via debug endpoint (`include_ticks=true`)

- **Prod (v2)**
  - Migrate mock management API → real Stimulize backend (Postgres + proper auth)
  - Lobby pruning enabled (currently no-op in beta)
  - CORS allowlist
  - Cost guardrails (per-conversation token caps)
  - Multi-task heartbeat or alternate tick mechanism (no 30s gap)
  - Tighter operational alarms
  - See [Beta → Prod TODO](#beta--prod-todo) for the full list.

- **Phase 1.1 (follow-up, in scope of beta but lower priority)**
  - Token usage estimation in editor
  - Customizable Qualtrics ED name
  - Hide-next-button-until-min-time
  - Image avatars
  - Usage by day / month dashboards

- **Phase 99 (discussed but likely won't do)**
  - Trial-integrated mode
  - Channel and key
  - Revision management
  - Rolling join (humans joining a started conversation)

## Requirements

**Chatroom**
- Two participant modes: 1-on-1 mode and Group mode (1-on-1 is a UI preset over group settings)
- AI Should simulate human texting habit 
- Simulated "waiting for pairing" screen before entering chat
- Optional timer: show status like "Please stay 5 to 10 minutes in the chatroom. Now: 4 minutes."

**Chatroom settings**
- Simplify prompt writing - just add a "Mimic human" switch
- [Future] Participants can set their name , and editor can decide whether to enable this feature
- Can deactivate at any time
- Cost estimation

**Usage and Billing**
- Chatroom total usage

## Core Design

### Chatroom
- Two participant modes:
  - 1-on-1 mode: one human, one AI per conversation. Implemented as a UI preset over the group settings (`target_human_count=1, ai_strategy=fixed_ai_count, value=1, max_wait_seconds=0`).
  - Group mode: multiple humans and AIs per conversation. Server dynamically manages bot participants.
- Should simulate human texting habit 
  - Might have delay due to texting
  - Might reply in 2 messages or no reply, not always 1-ask-1-reply
- Simulated "waiting for pairing" screen before entering chat (configurable duration).
- Optional timer: show status like "Please stay 5 to 10 minutes in the chatroom. Now: 4 minutes."
- No revision management. If user want to keep the old revision running, they should create a new chatroom.


### Chatroom Settings
- Status: Active/Inactive (should be at a higher level in data schema)
- Chatroom Name
- Participant mode: 1-on-1 / group
- Prompt
  - Mimic human (on/off, default on)
  - AI instruction(s) (allow per-AI instructions in group mode)- Simulate pairing time (seconds)
- Timer: min / max (minutes)

Future settings (out of scope for beta)
- Participant name and avatar
  - Allow set name or not (default false) (if false, use "Participant" + 4-digit random number)
  - Enable avatar or not (default true)


### Frontend Widget
- UI
  - Simple bubble-style message UI. Nickname + avatar.
  - Display system events (e.g. "xxx joined") and error events inline.
  - Avatar pool: start with some emoji pictures. 
- API
  - Host page can access conversation history (structured + plain text) for data collection.
    - Exposes `getHistory()` and `getHistoryText()` for host page integration.
    - Allow directly write to Qualtrics Embedded Data onChange (auto write). 
- Distributed as a CDN package. Host page loads and mounts it inside a target HTML element.
- Script loading in Qualtrics: since Qualtrics has jQuery, consider using `jQuery.getScript()`


### Chatroom Backend
- Architecture
  - Python AWS Lambda + API Gateway.
  - Bedrock Converse API for LLM inference.
  - DynamoDB for conversation history, PK: `conversation_id`.
  - TTL: `created_at + 2.5 years`. Long retention to allow fetching conversation history from the editor website. TTL is derived from `created_at` so it can be batch-recalculated if retention policy changes.
  - Directly reads chatroom settings from Stimulize RDS chatroom table.
  - Directly writes usage info to Stimulize RDS billing table.
- Conversation data
  - Events model: each entry has a `type` ("message", "system", "error").
  - 1-on-1 mode: new `conversation_id` per session.  
    Group mode: might reuse conversation with matching logic.
- Use URL `chatroom.stimulize.org` (better data segregation than `stimulize.org/chatroom`).

### Billing
- Token-based usage tracking, mirroring Bedrock pricing (input + output tokens), with one persisted usage row per billable model invocation and write-time `estimated_cost_usd` so historical records stay stable if provider pricing changes later.
- Decision on write path:
  - Option A: runtime writes usage through the management API.
  - Option B: runtime writes usage directly to Stimulize Postgres.
  - Decision: **Option B**.
  - Brief comparison:
    - Option A adds one more network hop, one more auth surface, and turns the management backend into a write-through proxy for data the runtime already owns.
    - Option B keeps the billable event adjacent to the Bedrock invocation, reduces moving parts, and makes idempotent `usage_event_id` writes straightforward.
  - Result: management API is read-only for billing aggregation; the chatroom runtime writes `chatroom_usage` rows directly to RDS.


## Design Details

### Chatroom Auth
**Auth flow**
1. Receives Chatroom ID
2. Check if chatroom exist and is active
3. Issue session token
4. Verify chat requests by session token

**Chatroom ID**
- Since Chatroom ID will be used as Channel Key, it should be able to defend against brute-force attack.  
- => Use UUIDv4 with prefix `scid_` (e.g. `scid_550e8400-e29b-41d4-a716-446655440000`). UUIDv4 chosen over UUIDv6 for simplicity — Python has native support. May migrate to UUIDv6 later if time-ordering is needed.
- If a Chatroom gets abused, user should close it and create a new one (copy settings).

**Beta client access key**
- Beta widget calls `/auth/token` with `{ chatroom_id, access_key }`.
- `access_key` is a single fixed beta value baked into generated beta scripts and checked against a server-side secret before a JWT is issued.
- This is not a per-chatroom billing/security boundary; it is only a lightweight beta gate to avoid completely unauthenticated session creation. Production replaces it with the fuller channel/key model or real Stimulize auth.

**Session token**
- Short-lived JWT (3h)
- Claims: `session_id`, `conversation_id`, `chatroom_id`, `iat`, `exp`
- `session_id` is kept separate from `conversation_id` because in group mode multiple participants share one conversation but each needs a distinct session.
- Nickname, avatar, and role are NOT in the JWT — they're stored in DynamoDB alongside the conversation and looked up when needed. Keeps the token small.


### Group Chatroom
#### Pairing logic
**Requirement**
From researcher's perspective I want:
- 2 use cases:
	- 1 - I need to do a conversation as part of my exp (only 2-3 participants), but too few human participant - thus use AI to pretend a participant and ensure a conversation happens.
	- 2 - I have enough participants and in most cases I get collect enough human during wait time, but I want to add AI who will say something that I want them to say. 
		- AI prompts are defined elsewhere and randomly selected - just want to control the num of AIs
- And in either case, I want to set "max wait time": if participant waits too long, just start a room with as-many-human-as-possible and rest AI
- For simplicity
	- Rolling join (human join a started conversation) is not in scope
	- We want to start a conversation whenever we have enough human.

**Solution**
Configuration:
- `target_human_count`
- `ai_join_strategy` : either `total_participant_count` or `fixed_ai_count`
- `max_wait_seconds`

Humans will first wait in the lobby.
A conversation would start when either enough human or deadline reached.

AI count:
- If `fixed_ai_count` strategy => `fixed_ai_count`
- If `total_participant_count` strategy => `total_participant_count - actual_human_count`


**Impl note:** 
We might update the pairing logic - decouple this code and keep it flexible.

#### Paring Implementation

**Deadline trigger: client poll vs SQS delayed message
1. Client poll triggers a "freshness check" (chosen)
	1. backend opportunistically closes when `now > deadline_at`.
2. SQS delayed message + closer Lambda. 
	1. Server-driven. Reliable even if all clients leave.

Decision -> **(1) client-driven**. 
The waiting client is the one who needs the close to fire, and they're the one polling. If everyone leaves, no UX exists to break — DDB TTL cleans up the orphan lobby. Saves an SQS queue and a closer Lambda. Also dodges SQS's 900s DelaySeconds cap. The freshness check also runs in `/auth/token` so a fresh joiner re-uses or re-creates the lobby cleanly.

**Lobby storage: lobby-row vs client-row**
1. Lobby-row (chosen): one DDB item per lobby, participants embedded as a list. Capacity check is one conditional `UpdateItem`. Simple atomic semantics.
2. Client-row: one item per participant. Capacity check needs a separate counter row (which becomes a lobby row anyway), and racing inserts can overshoot.

Decision -> **(1) lobby-row**. 
Lobbies hold ~4 participants, so list rewrite is cheap. The atomicity primitives we need (capacity, status flip) are per-item in DDB, so the lobby should be the item. Pruning a stale participant is one extra `UpdateItem`, acceptable.

Lobby state lives in a **separate** `chatroom-lobbies` table, not the conversation table. The conversation row only exists once the lobby closes; until then, clients polling `/chat/messages` look up the lobby by pre-allocated `conversation_id` via GSI. See LLD for table shape.

**Stale-participant pruning (post-beta)**

While in lobby, every `/chat/messages` poll updates the caller's `last_seen_at`. If a client closes its tab, polls stop. After `STALE_THRESHOLD_SEC` (30s) without a heartbeat, that participant is considered stale.

When pruning is enabled (prod), it runs at decision points:
- Inside `/auth/token` join flow: prune stale participants before counting capacity.
- Inside `close_lobby`: prune again right before computing `ai_count` and writing the conversation row.

If pruning would leave 0 humans, the lobby is marked `aborted` instead of starting a conversation. Pruning never runs after close — the participant list is frozen at that moment.

For **beta**, the schema records `last_seen_at` faithfully (so we have data) but the prune step in `close_lobby` is a no-op. All joiners count regardless of staleness. Disabled to keep beta simple.

**Aborted-lobby UX**

If a lobby reaches `aborted` (no humans remained at deadline, or pruning left 0 humans), the widget shows "No one else joined this chatroom." plus a "Reconnect" button. Clicking it re-runs `init()` so the user joins as a new participant in a fresh lobby. This makes the failure mode explicit rather than leaving the user staring at an animation. See LLD for the widget contract.

#### Async AI conversation flow ("tick" model)

The naive "user sends message → all AIs reply" pattern doesn't fit group mode (multiple AIs talking over each other) or human-like timing (silent pauses, follow-up messages). Replace it with a **tick** model: a periodic event per active conversation that decides whether some AI should speak now. Same model serves group and 1-on-1.

**Hybrid gate + tool-use:**
- Gate (server logic): enforces minimum silence (default 5s), prevents same-AI-spamming, picks the AI that has been silent longest. Loose values; no max-silence rule for v1.
- AI decision (Bedrock tool use): when the gate passes, ask the chosen AI via Converse `toolConfig` with one tool `speak(messages: string[])`. Empty array = stay silent. Non-empty array = the AI sends those messages (multi-message replies are natural).
- Why hybrid over pure-prompt: cost-bounded; small models love to talk regardless of instructions.
- Why tool use over control tokens (`<EOM>`/`<br>`): structured output, model-agnostic, no parsing brittleness across Claude/Nova/Llama.

**Tick mechanism: container heartbeat + Lambda async invoke**

A small ECS Fargate task (`desiredCount=1`) loops every N seconds, queries `status="active"` conversations from a sparse GSI, and async-invokes a tick handler Lambda for each. The handler runs the gate + Bedrock + DDB writes.

Options considered:
1. EventBridge cron + fan-out Lambda. EventBridge minimum is 1 minute — too coarse for 5-15s ticks.
2. SQS self-trigger chain. Each handler invocation enqueues the next. Lower idle cost but needs explicit watchdog + healing logic when chains break.
3. Container heartbeat (chosen). Simple mental model, central cadence control, no per-conversation healing logic. Restart recovery via ECS.

Why async Lambda invoke over SQS: built-in retry (2x), built-in throttling buffer (6h queue), no queue infra to provision. Race control is required regardless — the handler conditionally acquires an `active_tick_id` / `active_tick_until` marker before any Bedrock call.

**Configurability and ops:**
- `HEARTBEAT_INTERVAL_SEC` env var on the container (tunable without redeploy; default 5s).
- Container healthcheck: exits after N consecutive DDB query failures so ECS restarts.
- `max_duration_seconds` chatroom setting: researcher caps total conversation duration. Tick handler stops ticking and flips `status="ended"` past this deadline. Beta hard cap: 900 seconds (15 minutes), primarily to bound Bedrock spend and keep embedded DynamoDB event history safely below item-size limits.

**Active tick race control**

The heartbeat may skip conversations whose `active_tick_until > now`, but that is only an optimization. The tick handler itself is authoritative: before reading history or calling Bedrock, it conditionally sets `active_tick_id=<lambda request id>` and `active_tick_until=now+timeout` on the conversation row. If another handler already owns an unexpired active tick, the new handler returns. All final state writes and AI message appends condition on `active_tick_id` still matching the handler's tick id. If a Lambda dies, the timeout expires and the next heartbeat can recover the conversation.

**Beta vs production note:** Single-task ECS means ~30s gap during container restart — no ticks fire in that window. Acceptable for beta research traffic. **Revisit at production phase**: options include 2 tasks with leader election or shard assignment, EventBridge-driven fan-out, or migrating to the SQS self-trigger model.

**Audit: all events recorded**

The conversation `events[]` records every tick — including skipped ticks, "asked but stayed silent" ticks, and token costs. The Bedrock-visible history filters to message + system events only. This gives full research auditability ("why didn't AI speak then?") without polluting the AI's context.

**Stateless ticks: full message history per tick**

Bedrock Converse calls are stateless — Bedrock retains nothing across ticks. Each tick rebuilds the full prompt. The conversation history sent to Bedrock includes **all** prior messages, not a truncated window. This is the only way to keep AI identity (school, major, etc.) and topic state consistent across ticks. Token cost per tick grows linearly with conversation length; bounded by `max_duration_seconds`. Per-AI persona facts are also persisted on the conversation row and re-injected each tick, and each AI may also carry its own resolved `model_id` that overrides the chatroom default for that participant. See LLD for prompt composition.

**AIs don't know who else is AI**

Each AI sees other participants only by nickname — there is no role marker (`human`/`ai`) in the history exposed to Bedrock. The AI knows its own nickname (via `<your-name>` in the prompt) but treats every other participant as a fellow human. This preserves the illusion and keeps researcher control over how the AI behaves toward "humans" without leaking metadata.

**Simulated typing delay**

AI messages don't appear instantly. Each message gets a `visible_at` timestamp = authoring time + 2-8s random delay (delays stack across a multi-message turn). UI rendering, the gate, and the history sent to the next AI all filter events by `visible_at <= now`. This mimics real typing tempo and keeps AIs reasoning about the conversation as users perceive it. Human and system events become visible immediately. See LLD for details.

**No typing indicator in v1**

While an AI message has `visible_at > now`, no "Mars is typing…" indicator is shown to the user. Backend `/chat/messages` filters events by `visible_at <= now`, so clients have no awareness of pending messages. This keeps the contract simple. A typing indicator is in the prod TODO list — it would require either a separate `typing` event type or exposing pending messages with content stripped.

**1-on-1 mode is a special case of group mode**

Conceptually, a 1-on-1 chatroom is just `target_human_count=1, ai_strategy=fixed_ai_count, value=1, max_wait_seconds=0`. Backend treats it the same way: same lobby flow, same tick handler, same conversation schema. The editor exposes 1-on-1 as a separate preset in the UI (because the researcher's mental model is different), but under the hood `mode` is just a discriminator that picks default values for the group settings. We keep `mode` as an explicit field for clarity and forward compatibility, not because the runtime needs it.

### Usage/Billing Architecture

Options for storage and transmission of usage data
- A) Direct HTTP to management API:  
  Simple fire-and-forget, but data lost if backend is down.
- B) Lambda → RDS directly:  
  Accurate, no middleman. But requires VPC (adds cold start latency) and RDS Proxy (connection pooling for concurrent Lambdas).
- C) Lambda → SQS → billing Lambda or Stimulize backend → RDS:  
  Decoupled yet still resilient. No VPC needed for chatroom Lambda.
- D) Lambda → DynamoDB usage table:  
  No VPC needed, fast writes. But bad support for aggregation queries (no native SUM).
- E) CloudWatch Logs/EMF:  
  Zero coupling, but slow/expensive for aggregation queries. Better for monitoring than billing.

**Decision -> start with B, migrate to C.**


### Editor UI

**Chatroom List Page:**
- A list of all chatrooms with its status

**Chatroom Editor Page:**
- Edit chatroom settings
- Generate embeddable script: `fetch('http://stimulize-chatroom/script').start({chatroomId: '...'})`.
- Preview the chatroom widget inline.
- Chatroom setting is saved to cloud
- Token usage estimation (`timer * model * total participants`)


### Editor backend (Stimulize Backend)

- New tables in RDS for chatroom setting, keys, and billing.
- Need to create API doc for collaboration

### CDK
Use TypeScript for stronger typing.

### Error Handling & Resilience

- Catch common inference errors (expired credentials, invalid model, rate limit, timeout, service down).
- Retry recoverable errors (exponential backoff, 3 attempts).
- Frontend displays "Chatroom server error" for critical error events, and record in conversation history


### Lambda VPC Latency Analysis

The chatroom Lambda needs VPC access to read/write RDS directly.

Cold start overhead for VPC-attached Lambda:
- Without Hyperplane ENI caching: 5-10s extra (legacy behavior, no longer typical)
- With Hyperplane ENI caching (current AWS default): ~1-2s extra on first cold start only. Subsequent invocations reuse the ENI.
- With provisioned concurrency = 1: zero cold start for the first concurrent request. Cost: ~$3/month for a 256MB Lambda running 24/7. Additional concurrent requests still cold start normally.

Context: Bedrock inference latency is ~2-3s per call, so a 1-2s cold start is barely noticeable to the user. Provisioned concurrency is not worth it at this scale.

VPC infra cost: NAT Gateway (~$32/month) or VPC endpoints for Bedrock + DynamoDB (~$7-14/month). RDS Proxy (~$15/month) for connection pooling.

Decision: Lambda in VPC is OK.


### Widget code structure
- Option 1: the package is built for qualtrics by default; "qualtrics integration" is a component of it.  
  `fetch('.../script.js').start({chatroomId: '...', qualtricsED: true})`.
- Option 2: the core package is built for any web platform; and "qualtrics integration" packages wraps it to turn it into a qualtrics specific version.  
  `fetch('.../qualtrics-ver-script.js').start({chatroomId: '...'})`

**Decision ->  Option 1, because the current qualtrics integration only involves writing to ED**



## Follow-up features (Phase 1.1)

These non-critical features will be done lastly as follow-up (although in scope of beta)
- Mimic human by not always 1-ask-1-reply
- Token usage estimation
- Allow customizing the ED name to output
- Hide next button in Qualtrics
- Usage by day/month
- Use actual emoji image as avatar (start with emoji text elementLKet')



---



## Alternative/Future Designs

### Multi-Participant Conversation Mapping
- Each AI sees the full history from its own perspective:
  - Own messages → "assistant" role
  - Everything else → "user" role, prefixed with `[nickname]`
- Consecutive same-role messages merged (Bedrock requires strict alternation).


### Full auth design (Chatroom + Channel + ChannelKey)

### Auth
**Concepts**
- Chatroom: A set of settings = prompt + model + ... ; Is shareable without cost concern.
- Channel: A wallet; Is shareable to trusted users, with cost concern.
  - However, given the current user-based billing model, might need to reevaluate the necessity of channel.
  - Pending question: how to share a channel? Allow collaborator to create/revoke key?
- Channel key: To be carried in QSF publicly. Is revokable at any time (Backend always checks latest key status).
- Session token: short-lived JWT (3h), issued by `/auth/token` in exchange for a valid chatroom key.




**Channel and chatroom key design**

Channel is an internal grouping concept. A channel can issue multiple chatroom keys, which inherit channel settings. Users use chatroom keys to enter chatrooms.

- Channel is internal. Can be shareable (accessible by many users) after permission.
- Chatroom key is semi-public and long-living, thus has leak risk. Must be revocable at any time.
- Channel contains restriction info (e.g. allowed chatroom_ids), like IAM conditions.
- Chatroom key can override (more strictly) the channel restrictions.

Start with simple version:
- Each user has one fixed "DefaultChannel" with no restrictions. The channel concept is invisible to the user.
- User creates chatroom keys directly. Keys can optionally have restrictions (allowed chatroom_ids).
- Backend always checks latest key status on every `/auth/token` call. 

Decision -> the "simple version" is not as simple as "only the concept of Chatroom"

**Key validation approach:**

Option A: Validate key against backend on every `/auth/token` call.
- Pros: real-time revocation, can enforce restrictions per key
- Cons: one extra HTTP round-trip per init (negligible vs Bedrock latency)

Option B: Chatroom key is a signed JWT validated locally by Lambda.
- Pros: no backend call during init
- Cons: no real-time revocation (only expires when JWT expires)

Option C: Hybrid — signed JWT for fast validation + lightweight backend check for revocation only.

**Decision: Option A.** Real-time revocation is important. The latency cost is trivial. Lambda calls the management API to validate the chatroom key on every `/auth/token`.

Auth flow:
- Client sends chatroom key + chatroom setting + nickname to `/auth/token`
- Lambda validates chatroom key against management API (is it active? restrictions satisfied?)
- Lambda creates conversation, signs JWT with session_id, conversation_id, chatroom_id, nickname
- Returns JWT to client. All subsequent calls use JWT only (no further key checks).


**Decision: Start with: Chatroom == Channel == Channel Key**, all 1-1-1 bound.  
We completely hide the concept of Channel and Key from users.  
- For beta, use Chatroom ID plus the fixed beta client access key in the generated QSF/script instead of per-chatroom channel keys.
- Chatroom can be "Open" or "Closed".
- If a Chatroom gets abused, user should close it and create a new one (copy settings).

In standalone mode, use full config or only ChatroomID in script?
- Since we'll always check the chatroom status, let's use ChatroomID plus the fixed beta client access key.
  - This also restricts the capability of each Chatroom to prevent abuse.
- From high perspective, it's like we're creating another sub-survey that has same edit-publish lifecycle, and referencing it in the main survey.
  - Or, the Chatroom becomes a public mini-app. 



### Options of Chatroom Stimulize Integration

- Standalone mode
  - User can create a chatroom (and key) without stimulize trial. Then embed into arbitrary question, including existing SPT trial.
  - => Easier flow for updating 
  
- Trial integrated mode
  - Chatroom is directly integrated into stimulize as a trial stimuliz
  - => User can configure layout, add other elements, and repeat the test.


> Actually I think Standalone mode is the most common use case. Users usually don't need repeated and time-sensitive chatroom in the experiment*.


#### Chatroom & Channel management flow

Q: We want both standalone and integrated mode. In integrated mode, shall we create a standalone config and refer to it in exp, or directly create config within exp?
A: For easy sharing (both in stimulize and through qsf/), let's put it inside exp form.


**Approach 1 (Preferred):**
**Only channel is online** 
("Online" := managed in cloud at runtime, always sync)
- in stimulize-integrated mode, full chatroom setting is part of the experiment form, and is distributable with qsf ED
- in standalone mode, use copies full chatroom setting + script into their survey; chatroom can be saved to cloud, but is not used in experiment runtime
- auth uses chatroom key

Standalone mode user flow:
- User creates a chatroom (saved to cloud, but we can start with local-only). 
- User creates a chatroom key (cloud managed).
- We generate the user the script to put into Qualtrics question JS, which contains full chatroom setting.
- User copy-paste into their own Qualtrics survey.
  - (Setting chatroom key through copy-paste code is safer than through ED.)

Stimulize-integrated mode user flow:
- User configures chatroom inside an experiment. Chatroom is part of the experiment config. (saved to cloud)
- User creates a chatroom key (cloud managed).
- The chatroom setting is exported in qsf in ED.
- A separate "Key management" page in Stimulize. User create chatroom key here.
- User generates experiment qsf, import into qualtrics, and paste the chatroom key into ED.

Pros and cons:
- Security: Chatroom key can be used for any chatroom setting (if no restriction set)
  - flexible
  - higher risk if the key is leaked (would increase the thief's benefit)
- Implementation: Does not need chatroom management logic. 
  - When user shares exp or shares qsf, that chatroom goes with it automatically
  - During runtime, no need to read chatroom setting from backend


**Approach 2:**
**Both channel and chatroom settings are online** 

Standalone mode user flow:
- User creates a chatroom (cloud managed, visible to public (auto or controlled publish)). 
- User creates a chatroom key (cloud managed).
- We generate the user the script to put into Qualtrics question JS, which contains **chatroom id**.
- User copy-paste into their own Qualtrics survey.
  - (Setting channel key through copy-paste code is safer than through ED.)

This can co-exist with Approach 1's Standalone mode flow.

Stimulize-integrated mode user flow:
- User configures chatroom inside an experiment. Chatroom is part of the experiment config. (cloud managed, visible to public (auto or controlled publish)). 

Issue:
- Experiment runtime becomes dependent on Stimulize backend (not only Lambda)
- If chatroom settings changes, the generated and distributed survey will also change. This is usually not expected. Need chatroom setting to be static, or with revision management.


### Though-process of pairing logic
First, determine two researcher cases (secure a conversation / AI to guide topic)
To unify both cases, use configs
- `min_ais`, `max_ais`
- `min_humans`, `max_humans` 
- `max_wait_seconds`
And decide to start conversation when "enough human" or "wait timeout"
Then simplify by reducing two corner case.
- No rolling join -> no need `max_humans`
- How many AI -> "total participants" (more AI when deadline reached) or "AI count" (fixed AI in both cases)


---


## Beta → Prod TODO

Items deferred during the internal-beta phase. Revisit before public launch.

**Reliability and ops**
- ECS heartbeat is single-task. ~30s tick gap on container restart. Move to 2-task with leader election or shard assignment, or migrate to SQS-self-trigger model.
- Mock management API runs as a single Fargate container with SQLite + bearer token. Migrate to real Stimulize backend (Postgres + proper auth).
- Lambda concurrency limit set to default. Add explicit `reservedConcurrentExecutions` and a Bedrock-throttling alarm.
- Bedrock cross-region inference latency varies (`global.` / `us.` prefixed models). Measure and document; consider regional pinning if it matters.
- Lobby pruning: schema includes `last_seen_at` per participant but pruning logic is not implemented for beta. Implement before opening to wider users so abandoned tabs don't ghost-fill lobbies.
- Bundle CDN: beta uses GitHub Pages on `cdn.stimulize.org`. Move to CloudFront + S3 if we need cache invalidation, edge logs, or stricter WAF rules.
- CORS is `*` for beta. Tighten to a Qualtrics-domain allowlist (or Cognito-style auth) before prod.
- JWT TTL is fixed at 3h. Revisit if researchers want longer sessions (`max_duration_seconds > 3h` would expire mid-conversation).
- Beta keeps events embedded in the conversation DynamoDB item and caps conversations at 15 minutes. Before prod, split historical events into an append-only `chatroom-events` table to avoid the 400KB item limit, reduce hot-row contention, and support pagination/export.

**Cost guardrails**
- No per-chatroom Bedrock spend cap. Add daily / per-conversation token caps.
- Editor cost estimation only listed in Phase 1.1. Surface live "estimated max cost" before researcher saves a chatroom.

**Researcher experience**
- Token usage estimation in editor (live, based on duration × strategy × model rate).
- Usage-by-day / usage-by-month dashboards in editor.

**Data and privacy**
- Conversation TTL is 2.5 years. Confirm with research compliance before prod.
- Tick events stored verbatim include token counts and gate decisions — fine for audit, but expose via researcher-facing endpoints with care.

**Widget polish**
- Typing indicator (`Mars is typing…`) — currently no indicator while AI's `visible_at` is in the future.
- Reconnection UX is "reconnecting…" after 30s. Could be smarter (immediate retry feedback, jittered backoff).
- Multi-mount support (more than one chatroom per Qualtrics page) — out of scope for v1.


---


## Appendix

### 1st Team Review Raw Feedback

- Standalone mode is okay.
- Bind channel key with chatroom - user can activate/deactivate a chatroom. Or clone to new chatroom.
  - If a key is leaked, user just clone to a new chatroom.
- Just provide an on/off "Mimic human - yes/no"
- Allow random give avatar.  
  - -> Randomness can reduce the avatar 
- Also allow set specific avatar.  
  - -> Exp editor want to set up a role-playing settings
- Allow set name / pick avatar   
  - -> editor set AI name, user can set self name
- Add dynamic response - more like human, not 1 by 1
- Add "hide next button until minTime"
- Model dropdown 
- Token usage estimation



### 2nd Team Review Raw Feedback

- Want to allow beta user to try and evaluate the new features; auth and billing is not required.
  - Solution TODO:
    - If it turn out to be difficult to set up dev env for Stimulize backend , let's just use the mock management API and deploy it to EC2.
    - Not a worthy shortcut to do no-RDS solution (store chatroom configs in the widget arguments). Will still have issue in chatroom save-edit and access key.

- Prioritize multi-human-multi-ai mode
- Prioritize "AI might respond one sentence in 2 message, or no response"
  - Solution TODO:
    - Need to separate "converse" with "get chatroom history"
    - Instead of invoking converse every time when user send message, we should use an offline trigger (like every 5s) to trigger it.
    - AI can reply control tokens like `<EOM>` to represent they don't want to say anything, or `<br>` to represent they want to send their in two or more messages.
    - Need to update the prompt and verify the flow in experiment/ .



### 3rd Team Review Raw Feedback

- Need per-AI personality prompt

- Add "mimic human" back

- When goes public, do not use our own bedrock token - use their own Chat GPT / Claude API key

- Validate widget exports history to qualtrics ED.

- Input prompt length limit.

Technical:

- Need to lock down chatroom prompt - edits in chatroom will impact ongoing conversation; ai prompt will change
  - simple solution: show "ongoing conversation count" and let user save only when count is 0.
  - better solution: "publish" action with "revision"


### 4rd Team Review Raw Feedback

AI behaviour:
should prioritize "listen and rely to human" over "stick on the given topic"

add "xxx exit chat"

fix
- rejoin chatroom
- in qualtrics preview mode, should use same conversation instance


### Lobby table schema: `lobby_id` PK vs `chatroom_id` PK

Two ways to key the active-lobby store. We use option (1).

(1) **PK = `lobby_id` (UUID), sparse GSI on `chatroom_id+status="open"`** (chosen)
- Audit: each cohort gets its own row. Closed rows linger with TTL (180 days). "What happened in last week's 9am cohort?" is a `GetItem` away.
- Multi-active-lobby migration: zero. Already keyed by lobby; just relax the "one open per chatroom" invariant in app code.
- Cost: one extra UUID generation per lobby; one GSI.

(2) **PK = `chatroom_id`, no GSI on status**
- Audit: not in this table. Need CW Logs (or another store) at close time. Two write paths.
- Multi-active-lobby migration: requires creating a new table with a different PK and migrating data. Disruptive (hard cut-over) or complex (dual-write).
- Cost: simpler day-one schema; no UUID, no GSI.

We chose (1) because the cost difference is trivial (one GSI), audit is "free" via TTL, and we don't have to think about migration if multi-active ever comes up.
