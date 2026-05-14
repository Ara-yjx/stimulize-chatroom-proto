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

- Phase 1
  - 1-on-1 participant mode
  - Standalone mode (portable script, independent of Stimulize trial)
  - Chatroom ID only - no "Channel" or "Key"
  - Chatroom settings are online (always sync)
  - Auto-generated participant name, random avatar (like GitHub)
  - Conversation recorded in Qualtrics ED

- Phase 2 (will do later)
  - Group participant mode
  - Participant name
  - Usage chart
  - Usage-writer Lambda + SQS
  - Fetch conversation history from editor

- Phase 99 (discussed but likely won't do)
  - Trial-integrated mode
  - Channel and key
  - Revision management

## Requirements

**Chatroom**
- Two participant modes: 1-on-1 mode (Phase 1) / Group mode (Phase 2)
- AI Should simulate human texting habit 
- Simulated "waiting for pairing" screen before entering chat
- Optional timer: show status like "Please stay 5 to 10 minutes in the chatroom. Now: 4 minutes."

**Chatroom settings**
- Simplify prompt writing - just add a "Mimic human" switch
- [Phase 2] Participants can set their name , and editor can decide whether to enable this feature
- Can deactivate at any time
- Cost estimation

**Usage and Billing**
- Chatroom total usage

## Core Design

### Chatroom
- Two participant modes:
  - 1-on-1 mode (Phase 1): one human, one AI per conversation.
  - Group mode (Phase 2): multiple humans and AIs per conversation. Server dynamically manages bot participants.
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

Phase 2 settings
- Participant name and avatar
  - Allow set name or not (default false) (if false, use "Participant" + 4-digit random number)
  - Enable avatar or not (default true)
- [Phase 2] Group settings: `max_human_count`, and either (`min_bot_count` + `max_bot_count`) or (`min_participant` + `max_participant`)~~


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
- Token-based usage tracking, mirroring Bedrock pricing (input + output tokens).


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

**Session token**
- Short-lived JWT (3h)
- Claims: `session_id`, `conversation_id`, `chatroom_id`, `iat`, `exp`
- `session_id` is kept separate from `conversation_id` to prepare for group mode (Phase 2), where multiple participants share one conversation but each needs a distinct session.
- Nickname, avatar, and role are NOT in the JWT — they're stored in DynamoDB alongside the conversation and looked up when needed. Keeps the token small.


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

These non-critical features will be done lastly as follow-up (although in scope of phase 1)
- Mimic human by not always 1-ask-1-reply
- Token usage estimation
- Allow customizing the ED name to output
- Hide next button in Qualtrics
- Usage by day/month
- Use actual emoji image as avatar (start with emoji text elementLKet')



---



## Alternative/Future Designs

### Multi-Participant Conversation Mapping (Phase 2)
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
- Just use Chatroom ID instead of Channel key in the generated QSF/script. 
- Chatroom can be "Open" or "Closed".
- If a Chatroom gets abused, user should close it and create a new one (copy settings).

In standalone mode, use full config or only ChatroomID in script?
- Since we'll always check the chatroom status, let's use ChatroomID.
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
