# Resumable Conversation LLD

## TL;DR

Phase 1 supports only `resumable=true`, 1 human, 1 AI, and
`mimic_human=false`. `POST /auth/token` accepts `participant_id`, resolves one
stable conversation, and either refreshes its active connection or starts the
next episode. History remains one ordered event stream; episode is metadata and
a write fence, never a message key/query dimension.

No new AWS table or index is required. Development and E2E use only the
isolated event-storage dev stacks. Existing beta/prod runtime resources remain
untouched until the event-storage cutover and resume release are approved.

## Identity and Schema

Normalize `participant_id` by trimming, then require 1-63 case-sensitive
characters matching `[A-Za-z0-9_.-]+`.

Phase 1 derives:

```text
conversation_id = UUIDv5("stimulize:resume:" + chatroom_id + ":" + participant_id)
```

This avoids an eventually-consistent lookup and makes concurrent first launch
converge on one DynamoDB PK. The ID is not a credential; access still requires
a valid JWT, and `participant_id` is explicitly a weak researcher-managed ID.

Add to the conversation metadata row:

```text
participant_id
resumable = true
episode_count
episodes[]                       # fewer than 10 in Phase 1
active_episode_number            # present only while active
active_episode_started_at
active_history_start_cursor
last_episode_ended_at
last_history_end_cursor
active_connection_id
last_connected_at
```

Keep `started_at` immutable as conversation creation time. Each `episodes[]`
entry stores `episode_number`, `started_at`, `ended_at?`, `status`,
`history_start_cursor`, and `history_end_cursor?`.

Human history events carry both the connection `session_id` and stable
`participant_id`. AI events retain stable `ai_participant_id`. All events in a
resumable episode may carry `episode_number` as opaque payload.

## Atomic Lifecycle

### First launch

1. Validate the active RDS chatroom and supported resumable shape.
2. Build stable human/AI participant snapshots and locked setting.
3. Conditionally create the deterministic conversation row with episode 1.
4. In the same event-store transaction, append
   `This is the beginning of the conversation` with `episode_number=1`.
5. If another request won creation, re-read and follow active refresh.

When `participant_id` is supplied, auth checks the deterministic conversation
first. An existing resumable conversation keeps its locked setting even if the
editor later changes the chatroom setting; the current RDS row still controls
whether the chatroom itself is active. The latest setting controls only creation
when no matching conversation exists.

### Active refresh / second device

Condition on `status=active`, the same `participant_id`, and the same active
episode. Atomically replace `active_connection_id` and refresh TTL. Last login
wins; send/poll/history from the old JWT returns
`409 connection_superseded`. Refresh does not increment the episode.

### Timeout and resume

The tick handler measures resumable duration from
`active_episode_started_at`. Timeout atomically:

- appends an episode-end system event;
- changes `status` to `inactive`;
- closes the current `episodes[]` entry and records its end cursor;
- removes active episode timing/cursor fields.

Resume first captures the latest history cursor, then conditionally appends a
`Conversation resumed` boundary while changing `inactive -> active`,
incrementing `episode_count`, starting the next episode, and rotating the
connection. Concurrent resume requests cannot increment twice; the loser
re-reads the active episode and performs connection refresh.

All tick-owned appends require `status=active`, the same `active_tick_id`, and
the captured `active_episode_number`. Human sends additionally require the JWT's
`active_connection_id`. Opaque metadata preconditions are passed into the event
store; event keys and queries remain episode-agnostic. Stale delayed AI output
is dropped, while its already-incurred usage remains recorded.

## API Contract

`POST /auth/token` request:

```json
{ "chatroom_id": "scid_...", "participant_id": "researcher-id" }
```

For resumable rooms, the response adds:

```json
{
  "participant_id": "researcher-id",
  "episode_number": 2,
  "episode_started_at": "2026-07-26T...Z",
  "connection_id": "uuid",
  "resumed": true,
  "history_start_cursor": "opaque-or-null"
}
```

`resumed=true` only when this request starts episode 2 or later; first launch
and active connection refresh return `false`.

The JWT carries those identity fields. Non-resumable requests/responses remain
compatible. Resumable status is `active|inactive`; legacy conversations may
still return `ended`.

`GET /chat/history?before=<cursor>&limit=50` continues to page the complete
conversation. `GET /chat/messages?after=<cursor>` remains the live endpoint.
Neither accepts episode as a filter.

## Widget and Editor

- Add `init({ participantId })`; send it only during token exchange.
- A resumable room without it surfaces the backend's participant-ID error.
- Initial render fetches the newest history page; upward scroll loads older
  pages. Stable `participant_id`, not old `session_id`, marks prior human
  messages as self.
- Qualtrics ED contains only events whose `episode_number` matches the current
  episode, even when prior history is displayed.
- `inactive` disables the current input; a later init with the same participant
  starts the next episode.
- The editor exposes `resumable` only for the supported room shape. Preview
  accepts a participant ID; generated Qualtrics code includes a clearly marked
  participant-ID placeholder only when resumable is enabled.

## Prompt Context

Correctness uses the locked setting and full conversation history. For
cache-supported Bedrock models, split at `active_history_start_cursor`:

```text
stable setup + completed history
cachePoint
current episode history + current timing
```

The structured Bedrock message list contains only current-episode events in
this path, avoiding duplication of completed history. Non-cache models retain
the existing full-history prompt.

## Implementation Map

- `backend/chatroom_api/resume.py`: validation, deterministic identity,
  first-create, connection refresh, episode start/end.
- `auth.py`, `jwt_utils.py`: resumable bootstrap and claims.
- `chat.py`, `tick_handler.py`: connection/episode fencing and inactive status.
- Event-store adapters: generic metadata conditions/removals.
- `frontend`: `participantId`, self identity, current-episode ED, inactive UI.
- `editor`: setting validation/control, preview ID, generated snippet.
- API/design docs: additive contract and implemented/pending status.

## Verification and Release

1. Unit-test validation, create races, active refresh, supersession, timeout,
   resume, setting lock, event authors, cursor ranges, and stale tick writes.
2. Build backend, widget, editor, and CDK; run all existing suites.
3. Deploy only `stimulize-chatroom-event-dev-yjx-20260725-*`.
4. Run a synthetic conversation with total active duration below one minute:
   create episode 1, exchange/send/receive, expire, resume episode 2, verify
   same conversation and prior context, then clean up.
5. Run the local editor in a browser against the dev API and launch preview.

Beta/prod migration, hosted widget deployment, strong participant auth,
intentional restart, setting revisions, history export, and summary/window
compression are out of scope.
