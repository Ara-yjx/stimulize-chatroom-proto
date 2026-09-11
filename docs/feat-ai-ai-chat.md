# Feat: AI-AI Chat

## Goal

Let researchers observe a conversation between two or more AIs, without a
human participant, given a topic or source material. Support both one-off and
batch generation with predictable operational bounds.

## Decisions

### Scheduling and token use

AI-only conversations do not use human waiting, simulated typing delays, or
the heartbeat scheduler.

For exactly two AIs:

- Pick the first speaker randomly.
- Alternate speakers strictly after that.
- Every inference must produce a non-empty message.

For three or more AIs:

1. After a message, randomly order all AIs except its author.
2. Ask candidates in that order until one produces a message. A candidate may
   choose silence when the current exchange does not call for its input.
3. If every candidate stays silent, randomly select one of those candidates
   and invoke it again with a prompt-only instruction that requires a message.

This is not a shuffled round-robin in which every AI must speak once per round.
The shuffled candidates are tried only until the next speaker is found.

The forced instruction is not written as a participant-visible history event.
It uses the required-message output contract already supported by inference.
For `N` AIs, normal speaker selection requires at most `N` inferences: at
most `N - 1` optional attempts plus one forced attempt. An over-length output
may add one corrective inference before that turn is accepted or failed.

This feature does not store a random seed or promise exact replay. We will
observe silence rate, forced-turn rate, speaker distribution, and conversation
quality, then tune prompts if the AIs are too active or too passive.

### Conversation semantics

- Persisted settings use `human_count=0`; "AI-only mode" is an editor control,
  not a second stored source of truth.
- `mimic_human=true` means each AI acts as a human participant with its assigned
  persona. The prompt must not falsely claim that the other participants are
  human.
- Each AI has a unique `ai_participant_id`. Persona `internal_name`, display
  name, model, temperature, and prompt remain labels/configuration on that AI
  instance. Reused personas still produce distinct AI participants.
- Server timestamps remain in stored events for ordering and cursors, but
  current time and timing metadata are omitted from AI-only inference prompts.
- Accepted messages store `timestamp` as a server-clock Unix epoch millisecond
  JSON number. AI-only conversations have no simulated typing delay, so they
  omit `authored_at`; its absence means the authored time equals `timestamp`.
- A batch clones its chatroom settings, personas, and model configuration into
  the batch metadata row when it is created. Every conversation in that batch
  uses this immutable snapshot. A future chatroom setting revision ID may
  replace the copied data. Exact output replay and a stored random seed are not
  required.
- If a snapshotted model is unavailable, that conversation fails instead of
  silently falling back to another model.

### Limits and billing

The editor exposes:

- `max_message_chars`, default `400`
- `max_total_chars`, default `20000`
- `max_turns`, default `100`, maximum `200`

`max_turns` is the main operational and quality bound; we should not optimize
conversation quality around an overly restrictive local cost ceiling. Account
spending is controlled by the shared billing hard cap. Public rollout must not
allow unrestricted batch generation before that hard cap is active.

`max_total_chars` is a target length, not a hard truncation boundary. A final
accepted message may take the conversation beyond the target, after which no
new turn is started.

## Editor Experience

The chatroom editor keeps its existing Basics, Model and Prompt, and AI
Personas sections.

- Add an "AI-only mode" toggle. It maps to `human_count=0`.
- Hide human-only participant controls and keep AI Count.
- Keep Mimic human with the semantics above.
- Remove the chatroom-level AI nickname field; per-persona display names remain.
- Replace the interactive Misc fields with Max message length, Conversation
  length, and Max turns.
- Warn when persona count is lower than AI count because the same persona will
  be assigned to more than one AI instance.
- Hide Generate Embed Script and Widget Preview.

Add a Start conversation section:

- **Start once** creates a batch containing one conversation and immediately
  opens its asynchronous history/status view. The page polls for progress just
  like a larger batch; the request does not wait for generation to finish.
- **Start batch** accepts a batch count, shows a cost/time estimate, and starts
  the job.
- Show batch status and provide downloadable text and JSON histories. Large
  batch exports are generated asynchronously in S3 and exposed through a
  pre-signed URL valid for 15 minutes. Export objects expire after 7 days and
  can be regenerated.

## Architecture

AI-only generation is a separate workload that reuses the existing chatroom
settings, participant resolution, prompt construction, inference adapter,
usage recording, conversation metadata table, and event table. It does not use
the lobby, widget JWT, Qualtrics widget, or interactive heartbeat.

Add one DynamoDB table for batch job metadata,
`ai-conversation-batches`. "Start once" is a batch with one conversation.
Conversation history continues to live in `chatroom-conversation-events`.

Each generated conversation metadata row may store `batch_job_id` for reverse
lookup. Do not call this field `batch_id`: event storage already uses
`batch_id` for the idempotent group of events written in one transaction.

The existing Flask management backend owns the user-facing create, polling,
and export endpoints. It derives `owner_id` from the authenticated user,
validates chatroom ownership, writes the batch snapshot, and sends one
provisioning message using its EC2 instance role. A Provisioner Lambda then
creates conversation metadata and fans out the work; the HTTP request does not
try to provision up to 1000 conversations synchronously. This avoids another
internal HTTP hop while keeping partial provisioning retryable. The current
service token remains useful for worker calls to internal credits APIs, but it
is not user identity.

Request flow:

```text
Editor
  -> owner-authenticated Flask EC2 API
  -> create batch snapshot and enqueue one provisioning message
  -> Provisioner Lambda creates conversation metadata
  -> one SQS FIFO message per conversation
  -> Lambda worker processes one accepted turn
  -> checkpoint scheduler/turn state after each accepted message
  -> requeue the conversation when more turns remain
  -> mark the conversation and batch terminal when complete
```

Use `conversation_id` as the FIFO `MessageGroupId`: one conversation is
processed serially while different conversations run concurrently. The worker
does not attempt to finish an unbounded conversation in one Lambda invocation,
and Step Functions or AWS Batch are not used.

The conversation metadata row carries `worker_lease_id`,
`worker_lease_until`, `state_version`, and `next_turn`. The lease is a lock with
an expiry: acquire and release it with atomic DynamoDB conditional updates, not
a read-then-write sequence. A deterministic turn ID plus a DynamoDB transaction
atomically writes the visible event and advances conversation state. This
prevents duplicate visible messages after SQS retries. A crash after an
inference succeeds but before persistence can still cause a second paid
inference; an external model invocation cannot be made exactly-once.

A failed conversation does not stop the rest of its batch. Conversation states
are `queued`, `running`, `completed`, `failed`, and `timed_out`. Batch states are
`queued`, `provisioning`, `running`, `completed`, `partial_failure`, `failed`,
and `timed_out`. A batch is `partial_failure` when some conversations complete
and others fail; it is `timed_out` when its deadline stops unfinished work.
Counters expose the exact outcome mix. Batch cancellation is not included in
v1.

The deadline starts at batch creation. After it expires, workers must not start
another inference. An inference already in progress may finish and persist its
result, but the worker starts no subsequent turn. Exports include only
`completed` conversations and include a manifest with aggregate terminal-state
counts; incomplete histories are omitted. The v1 batch timeout is fixed at 24
hours and is not user-configurable.

### Initial operational limits

- `batch_count`: `1..1000`; a typical batch is about `100` conversations.
- One worker invocation processes one accepted turn. Silence and retries do not
  count as turns.
- Batch timeout: 24 hours from batch creation.
- Lambda timeout: 10 minutes.
- Conversation lease: 11 minutes.
- SQS visibility timeout: 12 minutes.
- SQS event batch size: 1.
- Initial worker maximum concurrency: 10; increase it only after observing
  Bedrock throttling and completion latency.

A typical default batch has 100 conversations, 100 turns each, and two AIs, so
it performs about 10,000 model invocations. At concurrency 10 and roughly 4-8
seconds per invocation, model time is about 1.1-2.2 hours; queueing, retries,
and throttling make 1.5-3 hours a reasonable expectation. A 24-hour deadline
provides wide headroom for slower models and three-plus-AI silence checks while
still terminating abandoned work.

Batch APIs and exports are owner-only. Batch count, worker concurrency, and
retry limits protect service capacity; billing owns the account-level hard
spending cap. Every inference continues to write the existing usage record.
The export endpoint only enqueues work; an exporter Lambda builds the archive
in S3, and the polling response provides a fresh pre-signed URL when ready.

### Placement and packaging

Runtime placement is not an authentication decision. A future OIDC/Cognito
migration changes how an API resolves a trusted `owner_id`; it does not require
provisioning or export to run on EC2 or Lambda. Choose placement from task
duration, resource isolation, retry behavior, and deployment ownership.

The short, user-authenticated create/poll/export-request APIs remain in the
existing Flask EC2 backend. Provisioning and export are asynchronous jobs.
Export already needs an isolated Lambda because reading many histories and
building an archive is too long and resource-heavy for a web request. Once that
infrastructure pattern exists, using a separate Provisioner Lambda adds little
operational complexity and keeps fan-out retries out of the EC2 request path.
The inference Worker remains a third Lambda because it needs different IAM,
concurrency, timeout, and Bedrock access.

The three handlers live in a new `chatroom_api.ai_batch` subpackage but reuse
the existing backend Lambda artifact. Do not create another top-level Python
project: provision, inference, and export already depend on the current event
store, participant, prompt, pricing, and usage modules. A subpackage gives the
feature a clear boundary without duplicating dependencies or build tooling.

## Out of Scope for v1

- Future source-material attachments such as PDFs and images.

## LLD Sketch

### Components

- The Flask management backend authenticates the user, derives `owner_id`,
  checks chatroom ownership, snapshots settings, and exposes create, polling,
  and export-request APIs.
- The Provisioner Lambda consumes one batch request, creates deterministic
  conversation metadata, and sends one FIFO work message per conversation.
- The Worker Lambda consumes one conversation message, acquires its lease,
  produces at most one accepted turn, commits the event and state together,
  then requeues unfinished work.
- The Exporter Lambda reads only completed conversations, writes text/JSON ZIP
  files to S3, and records the object key and export status.

### Source layout

```text
backend/chatroom_api/
  ai_batch/
    __init__.py
    contracts.py
    store.py
    provisioner.py
    scheduler.py
    worker.py
    exporter.py
  ai_participants.py
  prompts/
    construction.py
    speech_scaffold.py
```

`contracts.py` owns versioned queue envelopes and lifecycle values;
`scheduler.py` contains pure two-AI and three-plus-AI speaker selection.
`store.py` owns batch DDB conditional updates and counters. The handlers remain
thin and do not import private helpers from `tick_handler.py`.

Move only immediately shared logic: persona assignment into
`ai_participants.py`, and pure prompt assembly into `prompts/construction.py`.
Reuse the existing Bedrock client, event store, pricing, RDS usage, and credits
client directly. Do not introduce a generic `core` package in this change.

The management repository adds a dedicated API package and a service whose
business methods receive a resolved `owner_id`, not a Flask auth object:

```text
Stimulize-backend/app/api/ai_conversation_batch/routes.py
Stimulize-backend/app/services/ai_conversation_batch_service.py
```

### Infrastructure

One `AiConversationBatchStack` owns the batch table, provision queue, FIFO work
queue, export queue, export bucket, three Lambdas, DLQs, and alarms. All Lambdas
use the same backend code asset but receive separate IAM roles and runtime
limits. Existing conversation metadata and event tables remain the source of
conversation history.

Queue messages are small, versioned references: provision messages carry a
`batch_job_id`; work messages add deterministic `conversation_id` and expected
turn; export messages add an export job ID. Full settings stay in the immutable
batch snapshot rather than being copied into every message.

### Data model

`ai-conversation-batches` uses `batch_job_id` as its primary key and an
`owner_id`/`created_at` GSI for the editor list. A batch row stores:

```text
batch_job_id, owner_id, chatroom_id, client_request_id, request_hash
status, batch_count, created_at, updated_at, deadline_at
settings_snapshot
provision_lease_id, provision_lease_until, last_error
queued_count, running_count, unfinished_count
completed_count, failed_count, timed_out_count
export_status, export_generation, export_job_id
export_lease_id, export_lease_until
export_s3_key, export_error
```

The server derives a stable `batch_job_id` from `owner_id` and
`client_request_id`. Repeating the same request returns the existing batch;
reusing the ID with a different `request_hash` returns `409`.

The existing conversation metadata table receives AI-batch rows with
deterministic IDs derived from `batch_job_id` and `batch_index`. New fields are:

```text
conversation_type=ai_batch, batch_job_id, batch_index, owner_id
status, participants, deadline_at
worker_lease_id, worker_lease_until, state_version, next_turn
message_count, total_chars
```

Resolved persona/model assignments are stored in each conversation while the
shared chatroom snapshot stays on the batch. Deterministic IDs let provisioning
and export address all conversations without adding a GSI to the live
conversation table. Visible messages remain in the existing event table; no
batch or episode segment is added to the event key.

### Management APIs

All routes use the existing Flask user-token authentication and POST/action
convention:

```text
POST /api/createAiConversationBatch
POST /api/getAiConversationBatches
POST /api/getAiConversationBatch/<batch_job_id>
POST /api/getAiConversationHistory/<conversation_id>
POST /api/exportAiConversationBatch/<batch_job_id>
```

Create accepts `chatroom_id`, `batch_count`, and `client_request_id`; Start once
sets `batch_count=1`. The backend requires an active, owner-owned AI-only
chatroom and snapshots its saved settings. History is owner-only and paginated;
the page uses its forward cursor for polling. Export is accepted only after the
batch is terminal and returns asynchronous export status through the normal
batch polling response.

### Queue contracts

Provision and export queues are standard queues; conversation work uses FIFO.
Messages contain no settings or history:

```text
Provision: {version, batch_job_id}
Work:      {version, batch_job_id, conversation_id, expected_turn}
Export:    {version, batch_job_id, export_job_id}
```

Work messages use `conversation_id` as `MessageGroupId` and
`conversation_id:expected_turn` as the deduplication ID. DynamoDB conditions,
not the SQS deduplication window, remain the correctness boundary.

### Provisioning flow

The Provisioner first changes `queued` to `provisioning` and acquires a
batch-level expiring lease. It then loads the immutable snapshot and
conditionally creates each deterministic conversation row. Existing rows
belonging to the same batch are successful idempotent replays; any ID collision
with another batch fails the job. Persona selection uses the shared round-robin
assignment rule, and each AI receives a new conversation-scoped
`ai_participant_id`.

After a row exists, the Provisioner sends its initial FIFO work message. It
checks every partial SQS batch result and retries failed entries. Only after all
conversations are created and dispatched does it mark the batch `running`.
Lambda/SQS retries may repeat dispatch, so the Worker must always tolerate
duplicate work messages.

Workers may begin while the Provisioner is still dispatching, so both
`provisioning` and `running` are valid executable batch states. A duplicate
Provisioner delivery that cannot acquire the lease remains retryable until the
lease expires. Retryable failures update `last_error`, release an owned lease,
and raise for SQS retry; on the configured final receive attempt, the handler
marks undispatched conversations and the batch failed instead of leaving it
permanently in `provisioning`.

### Worker flow

One work invocation attempts one accepted turn:

1. Verify `expected_turn`, executable batch state (`provisioning` or `running`),
   conversation state, deadline, length, and turn limits; otherwise no-op or
   make the appropriate terminal transition.
2. Acquire the conversation lease with a conditional update.
3. For two AIs, invoke the required alternating speaker. For three or more,
   try shuffled non-last-speaker candidates until one speaks, then force one
   candidate if all stay silent.
4. Build the AI-only prompt without wall-clock timing metadata and invoke the
   snapshotted model without fallback.
5. Transactionally put the deterministic event and advance `state_version`,
   `next_turn`, `message_count`, and `total_chars`. Include terminal batch
   counters in the same transaction when the conversation ends.
6. Requeue the next expected turn or release the lease at a terminal state.

The AI-only output contract is zero or one message: zero is allowed only during
an optional three-plus-AI candidate attempt. A speaking or forced attempt must
produce exactly one message. `max_message_chars` is enforced in the tool schema;
one corrective retry is allowed for an over-length result, after which the
conversation fails. Every provider invocation, including silence and corrective
retries, records usage and participates in the billing gate.

Lease ownership prevents overlapping workers, while the expected turn and
transaction prevent duplicate visible events. A provider call may still be
charged twice if the Lambda dies after inference but before commit; this is an
accepted external side-effect limitation.

The batch begins with `unfinished_count=batch_count`. Each conversation's
one-way transition to a terminal state atomically decrements it and increments
exactly one terminal counter in the same transaction as the conversation
update. Afterward, a worker may conditionally finalize the batch when
`unfinished_count=0`; the polling API performs the same idempotent repair so a
crash between those operations cannot leave a finished batch stuck forever.
Finalization chooses `completed` when every conversation completed, `failed`
when none completed, and `partial_failure` for a mixed terminal result.

The deadline needs no scheduled sweeper. Every worker checks the immutable
`deadline_at` before inference, and the polling API conditionally changes an
overdue non-terminal batch to `timed_out`. Its counters continue to describe
the work actually completed; remaining work is `unfinished_count`. A late
queue delivery observes the deadline and makes no provider call.

Validation errors, unavailable snapshotted models, billing denial, and invalid
output after the corrective retry are terminal conversation failures.
Throttling, provider 5xx/timeouts, and AWS persistence failures are retryable.
On the final SQS receive, the worker conditionally marks the conversation
failed and still returns a failure so the original message reaches the DLQ.

### Export flow

The Exporter verifies the export job and terminal batch, derives every
conversation ID, and includes only rows in `completed`. It queries their event
partitions and writes one text file and one JSON file per conversation plus a
manifest containing aggregate counts and omitted statuses. The ZIP is uploaded
to a private S3 key scoped by owner, batch, and export job. The API generates a
fresh 15-minute pre-signed URL; S3 lifecycle removes the object after 7 days.
Duplicate export deliveries use an export-level lease and the deterministic
export job ID; a completed object is reused rather than rebuilt. If lifecycle
expiration removed the object, a new request atomically increments
`export_generation` and derives a new export job ID, allowing regeneration
without making concurrent requests build duplicate archives.

### Runtime and IAM

- Provisioner: 512 MB, 10-minute timeout, maximum concurrency 2; batch and
  conversation DDB write plus work-queue send.
- Worker: 512 MB, 10-minute timeout, maximum concurrency 10; batch/conversation/
  event DDB, Bedrock, usage/credits, and work-queue send.
- Exporter: 1024 MB, 15-minute timeout, 1 GB ephemeral storage, maximum
  concurrency 2; batch/conversation/event DDB read plus export-bucket write.
- All queues have 14-day DLQs and alarms. The bucket blocks public access,
  encrypts objects, retains infrastructure, and expires export objects in 7
  days. The batch table uses on-demand billing, PITR, deletion protection, and
  retention matching conversation data.

The management EC2 role needs only batch-table read/write, provision/export
queue send, event/conversation read for owner history, and export-object read
for pre-signing. It must use the default AWS credential chain; do not copy the
backend's legacy hard-coded AWS credential pattern.

### Delivery and verification

Implement the pure contracts, scheduler, and stores first; then Provisioner,
Worker, Exporter, management APIs, and editor UI. Deploy the new stack with
isolated development resource names before wiring the management backend.

Tests cover scheduler behavior, state transitions, request idempotency,
provision retries, duplicate work delivery, lease expiry, transactional event
writes, batch deadline, partial failure, and export filtering. The first cloud
E2E uses `batch_count=2` and `max_turns=4`, confirms model replies and usage
rows, and deletes or expires its test artifacts.

There is no design blocker to implementation. Two release/deployment items must
be resolved before public enablement:

- Management beta and production currently share an EC2 instance profile. Add
  resource-scoped Batch DDB/SQS/S3 permissions deliberately rather than
  broadly expanding the shared role during an application deploy.
- Public batch generation waits for the billing hard cap. Development and
  tightly bounded E2E can proceed before it is enabled.
