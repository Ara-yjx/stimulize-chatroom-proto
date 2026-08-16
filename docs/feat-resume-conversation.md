# Resumable Conversation Design

## Goal and Scope

A participant can return days later and continue the same conversation. The AI
retains earlier context, and the conversation may be resumed repeatedly.

Phase 1 is limited to chatrooms with:

- one human
- one AI
- `mimic_human=false`
- `resumable=true`

Resuming and referencing are separate features. Resuming continues one logical
conversation; future referencing may quote or branch from another conversation.

Event storage cut over on 2026-08-15 and is now the only supported runtime
writer. Resume was released to shared beta on 2026-08-17 after isolated-dev,
non-resumable, two-episode, and hosted-editor E2E gates passed. The release was
code-only: it added no table/index and migrated no existing conversation.
Non-resumable rooms preserve their lobby, token, lifecycle, history, and prompt
behavior.

## Terminology

- **Conversation**: the long-lived chat, identified by the existing
  `conversation_id`. It may span months or years.
- **Episode**: the provisional Phase 1 AI-runtime term for one actively
  participated period. It lasts minutes and is identified by an incrementing
  `episode_number`. It controls tick activation and duration but is not part of
  messaging-event identity or ordering.
- **Connection**: one browser/JWT lifecycle. Reconnecting or opening another
  device can replace a connection without starting another episode.

As an analogy only, a conversation behaves like a messaging thread that spans
multiple episodes. `thread` is not a domain object, identifier, table, or API
term; the system uses `conversation` and `conversation_id` consistently.

## Participant Identity and Access Boundary

The participant supplies the case-sensitive `participant_id` in the widget's
launch prompt. Phase 1 relies on the generated script's `resumable: true` hint;
a later two-stage bootstrap exchange will read this setting from the backend
before showing the prompt.

Phase 1 resolves a conversation by:

```text
chatroom_id + participant_id
```

Rules:

- IDs are case-sensitive.
- Trim leading/trailing whitespace, then accept 1-63 characters from
  `a-zA-Z0-9-_.`.
- Automatically create a conversation when no match exists.
- Allow exactly one conversation per `chatroom_id + participant_id`.
- A participant cannot intentionally start over with the same pair.
- An inactive/deleted chatroom cannot create or resume a conversation.

Bootstrap remains one step. `POST /auth/token` accepts optional
`participant_id`; it is ignored for non-resumable chatrooms and required for a
resumable chatroom. A missing value for the latter returns
`400 participant_id_required`. No discovery request is required first.

`participant_id` is intentionally a weak identifier, not strong authentication.
Mechanically it still grants access to matching history, so Phase 1 explicitly
accepts that risk. Researchers own participant authentication and should not use
PII as IDs. Future options include uploaded hashed ID lists or researcher-signed
participant assertions. Hashing at rest alone does not stop online guessing.

## Logical Model and Storage Decision

Two approaches were considered:

1. Reuse one long-lived conversation. History is naturally continuous, but
   changing AI identity and branching are harder.
2. Create a new conversation per active period and link prior conversations.
   This simplifies physical retention and branching, but a linked list would
   make history reads unnecessarily expensive.

Decision: expose Option 1 as the logical model. Physically, do not require all
history to remain in one DynamoDB item. A conversation metadata row plus events
partitioned by `conversation_id` preserves continuous history while supporting
pagination and future branching. Option 2's multi-read concern is also avoidable
with `conversation_id` as a partition key and ordered event sort keys.

This physical split matters because the current conversation item embeds every
message and tick diagnostic and is limited to 400 KB. Fewer than ten episodes
can still exceed that limit.

Decision: implement the focused event-table refactor as a separate prerequisite
feature. Its table schema, cursor contract, migration, and cleanup path are
defined in [`feat-conversation-event-storage.md`](./feat-conversation-event-storage.md).
The broader chatroom/inference refactor remains optional and must not block
resume.

The messaging layer owns the conversation's ordered history, audience, cursors,
and retention. The chatroom AI application owns episodes, tick state,
connection supersession, locked settings, and the start/end cursors of each
active period. The current metadata table may temporarily contain both groups
of fields, but the event-store interface must not inspect episode state. This
keeps a future standalone messaging service possible without migrating history
keys.

## Conversation and Episode State

The conversation metadata should contain at least:

```text
conversation_id
chatroom_id
participant_id
ai_participants                   # stable IDs + resolved persona snapshots
chatroom_setting                 # immutable Phase 1 snapshot
status                           # active | inactive
created_at
started_at                       # immutable first-episode start
episode_count
active_episode_number
active_episode_started_at
last_episode_ended_at
active_history_start_cursor
last_history_end_cursor
active_connection_id
ttl
```

`active_episode_number` is present only while an episode is active and is the
runtime write fence. Do not introduce a duplicate `write_generation` in Phase
1. `episode_count` retains the latest allocated number while inactive.

An opaque episode ID is unnecessary while episodes cannot branch and only one
may be active. `conversation_id + episode_number` uniquely identifies an
episode. Add an ID later if episodes become independently referenceable.

Each episode records:

```text
episode_number
started_at
ended_at
status
history_start_cursor
history_end_cursor
```

Messages may carry `episode_number` as optional application metadata. Event
keys and core messaging write/query APIs never depend on it.
`history_start_cursor` is the last history cursor before the episode's first
event; `history_end_cursor` is the last cursor included when the episode ends.
The range `(history_start_cursor, history_end_cursor]` therefore identifies that
episode's history without coupling storage to the lifecycle name. Optional
application-supplied visible boundary events can render separators; UI text such
as "This is the beginning of the conversation" remains a presentation decision
for user testing.

The tick handler measures duration from `active_episode_started_at`, never from
the immutable conversation `started_at`.

## Lifecycle

Conversation status intentionally uses only:

- `active`: an episode is running; heartbeat and inference are enabled.
- `inactive`: no episode is running; history remains and the conversation may
  resume when its chatroom and locked setting allow it.

We avoid `ended` or `closed` because those names imply permanent termination.
A future terminal state can be added separately if needed.

Lifecycle:

1. **First launch**: conditionally create the conversation and locked setting
   snapshot, capture the current history cursor, start episode 1, set
   `status=active`, create a connection, and optionally append a visible
   application boundary event.
2. **Active use**: messages are stored only in the continuous
   `conversation_id` history; inference duration uses
   `active_episode_started_at`.
3. **Episode timeout**: atomically set `status=inactive`, record the end time,
   optionally append a visible boundary event, and save the last included
   history cursor. Clear `active_episode_number`; new ticks and inference stop,
   and no delayed AI message may be appended afterward.
4. **Resume**: resolve the same conversation, increment `episode_number`, set
   the new active timing/connection fields, capture the new start cursor,
   refresh TTL, and optionally append a visible boundary event.
5. **Refresh while active**: keep the current episode and rotate only the
   connection.
6. **Second device while active**: last successful login wins. Rotate
   `active_connection_id`; the old device receives an explicit error such as
   `409 connection_superseded` on its next poll/send.

Conditional writes must prevent duplicate conversation creation and duplicate
episode increments under retries or concurrent launches.

Every AI tick captures `active_episode_number` before inference. Its message
append transaction requires both `status=active` and the same
`active_episode_number`. If the episode ended or a later episode started while
inference or simulated delay was in progress, the append is rejected and the
output is dropped with a structured CloudWatch diagnostic. Usage is still
recorded because inference already incurred cost. Resumable paths do not
pre-persist future-timestamp messages.

This truncation keeps episode cursor ranges unambiguous. Otherwise, a delayed
message authored in episode N could be appended after episode N+1 starts and
would fall inside N+1's continuous-history cursor range; extending N's end
cursor instead would make the two ranges overlap. Reject the stale output
rather than making the core messaging store/query path episode-aware.

## Connection Supersession

The combined Phase 1 API JWT may carry `conversation_id`, `episode_number`, and
`connection_id`. The AI application can use `episode_number` to reject stale
runtime actions, but the messaging query and cursor do not use it. Both send and
poll paths compare the connection claim with `active_connection_id`. Because
those paths already read the conversation, this check needs no additional read.

This is a small Phase 1 server-side revocation mechanism. The future refactor
should also stop overloading the current `session_id` concept for participant,
connection, and runtime identities.

## Chatroom Settings

The editor adds `resumable`, enabled only for one-human, one-AI,
non-mimic-human chatrooms. It explains that retained memory expires about 2.5
years after the latest event.

Phase 1 locks the complete chatroom setting when the conversation is created.
Editor changes affect new conversations only; resume never refreshes settings.
This preserves AI identity and behavior across episodes.

At conversation creation, assign every AI a stable `ai_participant_id` and
store its resolved persona, model, temperature, internal name, and display name
in the conversation's participant roster. Reuse the roster across episodes.
AI messages use `ai_participant_id` as immutable author identity;
`internal_name` and display name are snapshots used for labeling and exports.

Later, immutable chatroom-setting revisions will allow resume to deliberately
select a newer snapshot. The conversation can then store a revision number
instead of full JSON, provided referenced revisions are never overwritten or
removed.

## History, Pagination, and Qualtrics

The widget visually displays history from previous active periods. It initially
fetches the newest page and loads older pages as the user scrolls upward.

Use ordered event keys and opaque cursors rather than timestamps alone because
events may share timestamps:

```http
GET /chat/messages?after=<live_cursor>
GET /chat/history?before=<older_cursor>&limit=50
```

These core messaging APIs remain episode-agnostic. A later chatroom-level
episode endpoint may resolve its stored start/end cursors and delegate to a
generic cursor-range query; it does not require an episode key or index.

Messaging uses `H#T{timestamp}#B{batch_id}#I{index}`. `timestamp` is the
server-assigned canonical history and availability time. There is no
`visible_at`. A delayed AI message may additionally carry optional
`authored_at`, meaning when its content was produced before simulated typing;
`authored_at` never controls ordering or delivery. The full physical contract
and legacy mapping are in the event-storage document.

Responses include `next_before` and `has_more`. Pages are returned in display
order. Optional application boundary events, or boundary cursors from runtime
metadata, render separators without changing messaging keys.

Two history views remain intentionally different:

- The widget can page through the complete conversation.
- Qualtrics ED continues using `QUALTRICS_CHATROOM_HISTORY` and
  `QUALTRICS_CHATROOM_HISTORY_JSON`. It contains only events after the current
  `history_start_cursor`, bounded by `history_end_cursor` after the period
  ends. Loading or displaying older episodes must not add them to these ED
  values.

Researchers may later retrieve the complete conversation history through a
backend export API.

## Prompt Context and Caching

Phase 1 assumes fewer than ten resumed active periods and initially gives the
AI full message context. CloudWatch tick diagnostics are not model history.

At the first inference of each application-owned episode, structure the prompt
as:

```text
platform scaffold
cache point

locked setting, persona, and history through `history_start_cursor`
cache point

messages after `history_start_cursor`
```

Bedrock cache entries do not survive a gap of days. The first inference of a
returning active period rewrites the completed-history cache; subsequent ticks
reuse it while that period is active. This lowers repeated within-period cost
but does not solve storage or eventual context growth. Rolling summaries and a
recent-message window remain later optimizations.

## Retention

Each persisted `H#` history batch or explicit conversation/application
lifecycle action sets TTL to `now + 2.5 years`. Background tick attempts,
scheduling projections, and CloudWatch diagnostics do not extend history
retention. Since active periods are much shorter than the retention period, the
practical retention point is the latest participant-visible conversation
activity. Consent and research data-retention policy are outside this feature's
scope.

## Relationship to the Storage and Runtime Refactors

The selected event-storage feature first separates conversation metadata from
persisted history. Tick diagnostics move to CloudWatch Logs while compact
operational tick state remains in metadata. It is independently deployable and
preserves current conversation behavior.

The broader planned runtime refactor may later separate:

- application activity lifecycle
- browser/JWT connection state
- AI inference and tick ownership

For resumable conversations, AI behavior performs simulated delay before the
history append. The application passes the captured episode number as a write
precondition, and the append is dropped when it no longer matches
`active_episode_number`. Core history keys and queries remain episode-agnostic.
A later service split can preserve this contract while moving the delay outside
the current tick Lambda.

Resume adds only the provisional episode and connection boundaries it needs
after the event store lands. Replacing that lifecycle model later does not
change message keys. Resume does not wait for a full inference-service refactor.

## Remaining LLD Work

- Define the atomic participant-binding, conversation-create, episode-start,
  and connection-supersession writes.
- Define the exact token response fields and widget state transitions.
- Use concise system copy such as "This is the beginning of the conversation"
  for first launch and "Conversation resumed" for later episodes; wording is a
  reversible UI detail.

Event storage and pagination LLD remain owned by the prerequisite
event-storage document.
