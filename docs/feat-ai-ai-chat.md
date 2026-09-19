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
- AI-only ignores `mimic_human` and hides its control. Use semi-formal peer
  discussion, not an assistant persona or instant-message mimicry.
- Each AI has a unique `ai_participant_id`. Persona `internal_name`, display
  name, model, temperature, and prompt remain labels/configuration on that AI
  instance. AI-only requires zero usable personas or at least as many as AIs;
  choose without replacement when personas are configured. Empty cards are ignored.
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

Batch detail includes input/output tokens, estimated USD cost, and recorded
inference count, refreshed with batch status. Management verifies ownership,
then aggregates RDS usage by owner, chatroom, and `raw_usage_json.batch_job_id`.
This includes recorded silent/discarded responses and all conversations, not
just the displayed page or completed exports. Empty recorded usage returns zero;
missing API support displays "Unavailable". Costs are estimates, not AWS bills,
and recent usage writes may lag. No new table or schema migration is required.

The editor exposes:

- `max_message_chars`, optional prompt guidance, default `null`
- `max_total_chars` (Max characters), default `20000`, maximum `500000`
- `max_turns` (Max messages), default `100`, maximum `1000`
- `batch_count`, maximum `50` conversations per batch.

These are positive integer configuration limits, enforced by the editor and
management API before batch creation. The provisioner revalidates the snapshot
before creating any conversations; invalid batches become `validation_failed`.
The character limit remains a completion target: the last message may exceed it.

`max_turns` is a successful-completion and quality target; the deadline is the
main safety bound. We should not optimize
conversation quality around an overly restrictive local cost ceiling. Account
spending is controlled by the shared billing hard cap. Public rollout must not
allow unrestricted batch generation before that hard cap is active.

`max_total_chars` is a target length, not a hard truncation boundary. A final
accepted message may take the conversation beyond the target, after which no
new turn is started.

### Prompt caching, randomness, and estimated cost

Keep caching enabled. Identical inputs can produce different sampled outputs:
for a fixed model, prefix computation is normally deterministic, while decoding
samples the next token. Caching reuses prefix computation, not a previous answer
or its random choices. It should not couple separate conversations' trajectories.
This is not a promise of bitwise-identical results with caching on/off; numerical
nondeterminism and provider implementation details still exist. Similar openings
can also result from identical personas, examples, and an empty history, without
caching. See [Anthropic's caching explanation](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

Reuse requires a matching prefix, not a shared conversation ID. Our prefix
includes speaker-specific setup, so each AI generally warms its own prefix;
different participant names or settings can prevent reuse across conversations.
No other conversation's history is loaded into a request.

**Planning estimate (2026-09-12), not a measured bill:** use the editor defaults:
two AIs, global Claude Sonnet 4.6, temperature 0.7, mimic-human enabled,
20,000-character target, 400-character message limit, and 100-turn ceiling.
Assume 100 successful calls averaging 200 message characters, no retries, and
no custom persona/additional prompt. The turn ceiling may stop real runs before
the character target; neither output length nor cost is guaranteed.

- Approximate English tokenization as 4 characters/token. The current scaffold
  measures 9,139 characters (1,492 whitespace-delimited words); round the reusable
  prefix, including setup and tool schema, to **2,500 tokens per call**.
- History currently appears twice: a formatted history block and role-mapped
  messages. Its cumulative content is approximately
  `2 * (200 / 4) * (0 + ... + 99) = 495,000` uncached input tokens.
- Allow another 50,000 input tokens for sender/role formatting and triggers,
  and 7,000 output tokens for message content plus tool-call structure.
- Assume two cold prefix writes and 98 hits, with each speaker reused within
  the default five-minute TTL. Region routing or eviction can add cold writes;
  cross-conversation hits are not assumed.
- Rates in USD per million tokens: ordinary input **3.00**, five-minute cache
  write **3.75**, cache read **0.30**, output **15.00**. These match our current
  pricing config and [Sonnet pricing](https://platform.claude.com/docs/en/models/sonnet-4-6/overview).
  This is our on-demand worker, not the discounted AWS Batch Inference API.

| Estimated inference cost | No cache hits/writes | Warm explicit cache |
| --- | ---: | ---: |
| Repeated prefix | $0.750 | $0.092 |
| Dynamic input/history | $1.635 | $1.635 |
| Output | $0.105 | $0.105 |
| **One conversation** | **$2.49** | **$1.83** |
| **Ten conversations** | **$24.90** | **$18.32** |

Under these assumptions, caching saves about **$0.66 / conversation (26% total;
28% input cost)**. Prefix-only savings are about 88%, not 88% of the whole bill.
Longer custom static prompts increase the benefit; long dynamic histories reduce
its percentage. Formula, with all rates per million tokens:
`off = (100*P + D)*3/1e6 + O*15/1e6`;
`on = (2*P*3.75 + 98*P*0.30 + D*3 + O*15)/1e6`.
Infrastructure, retries, discounts, and taxes are excluded. Token counts above
are sizing assumptions, not tokenizer measurements or observed usage.

**Later experiment:** compare repeated runs with identical model, temperature,
personas, and prompt ordering; measure conversation diversity/quality as well as
input/output/cache-read/cache-write tokens and cost. Do not change examples or
inject random prompt noise to force misses. Removing a checkpoint alone does
not establish an uncached control: Bedrock may also use implicit caching. Verify
usage counters and provider-supported controls first; otherwise label the test
explicit-cache versus default caching, not cache-on versus cache-off. Our current
unsupported-model code path also changes prompt placement, so it is not a clean
control. See [Bedrock cache behavior and TTL](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html).
No paid comparison experiment has been run for this estimate.

### Current prompt structure and reference export

Each batch inference reconstructs a complete Bedrock Converse request:

1. System: dedicated AI-only rules. Two AIs alternate without silence examples;
   3+ include speak/silence examples and a forced-response variant when needed.
2. Initial user prefix: topic, this AI's persona, participant names, own name,
   and optional chatroom Additional Prompt; then a cache checkpoint.
3. Dynamic context: the full current conversation as formatted history, followed
   by its role-mapped messages (`assistant` for this AI, `user` for others).
4. Current message/character progress and a continuation instruction, plus the `speak` tool
   schema and model/temperature settings. Two-AI turns require one message.

This is not a stateful Bedrock conversation or a delta-only request: the complete
payload is sent again, and the provider may reuse its matching prefix internally.
Historical messages after our explicit checkpoint are not deliberately cached by
that checkpoint. The duplicate history representation is current behavior, not
a requirement; evaluating its removal is separate from the cache comparison.

**Implemented locally:** new batch ZIP exports include one `info/prompt.md` per batch,
not a prompt JSON or per-inference payloads. The goal is to help researchers
understand behavior by reading the full fixed instructions and examples.
The file explains the request's construction order: shared system text,
selected persona setup (topic/names/persona/Additional Prompt), cache boundary,
history placeholders, turn instructions, and a plain-language tool contract.
Model, temperature, and message/length/turn limits appear separately as settings,
not as literal prompt text. Persona variants show resolved model/temperature;
actual assignments and generated names remain in conversation history exports.

Provisioner saves this readable reference in S3 **before dispatching work**,
using the batch settings snapshot and then-current templates. A conditional
create preserves the first file across retries, including retries after deployment.
The batch records its object key and SHA-256; exporter reads and verifies the
saved bytes rather than regenerating from current code. No worker changes:
this is an initialization-time reference, not a guarantee about every inference
if code changes mid-batch. We deliberately do not serialize the builder rules,
freeze worker versions, or capture histories/corrective attempts per inference.

A version label alone cannot restore old content. Save the content first, with
reference format version and a hash of the deployed prompt-related source files
(also identifies uncommitted builds); manually named template versions can wait.
Templates currently combine Python string constants with Python assembly logic.
Freezing the text for worker consumption is possible but not needed for this
explanatory export; freezing all assembly behavior would be a larger feature.

References use `prompt-references/` in the private export bucket and do not expire
with regenerated ZIPs (`owners/`, seven days). Future batch-data deletion must
also remove its reference. Old batches without a reference receive an explicit
unavailable notice, never today's template presented as historical fact. Missing
or corrupt referenced objects fail export rather than silently substituting text.
Previously generated ZIPs remain unchanged until a new export is generated.

## Editor Experience

The chatroom editor keeps its existing Basics, Model and Prompt, and AI
Personas sections.

- Basics Mode radio: AI+Human / AI+AI; the latter maps to `human_count=0`.
- Hide human-only participant controls and keep AI Count.
- Hide Mimic human; its stored legacy value has no AI-only prompt effect.
- Remove the chatroom-level AI nickname field; per-persona display names remain.
- Replace the interactive Misc fields with Max messages and Max characters,
  followed by optional Max message length (prompt guidance only).
- Insufficient non-empty personas show a red error and disable Save & start
  once/batch, not Save. Provisioner validates the immutable snapshot before
  prompt storage, conversation creation, or workflow dispatch. Invalid batches
  settle atomically as `validation_failed` with `last_error`, all queued/running/
  unfinished counts zero, and failed_count equal to requested batch size.
  No conversation rows are fabricated. Duplicate delivery is a no-op; fix settings
  and create a new batch. Detail/history display the error; download is disabled.
  A prior partially provisioned batch must not be overwritten by this fast path.
- Hide Generate Embed Script and Widget Preview.

Add a Start conversation section:

- **Start once** creates a batch containing one conversation and immediately
  opens its asynchronous history/status view. The page polls for progress just
  like a larger batch; the request does not wait for generation to finish.
- **Start batch** accepts a batch count and estimates cost from the latest normally
  completed, same-configuration **Run once**, multiplied by the number of new
  conversations. Without a valid reference, prompt the user to Run once; do not
  estimate from file size or silently reuse stale/incomplete usage. This is an
  informational estimate, not a billing cap or an automatic test run. See the
  [run-based cost decision](feat-prompt-attachments.md#run-once-then-estimate-batch-decision-2026-09-13).
- Show batch status and provide downloadable text and JSON histories. Large
  batch exports are generated asynchronously in S3 and exposed through a
  pre-signed URL valid for 15 minutes. Export objects expire after 7 days and
  can be regenerated.

## Orchestration redesign (2026-09-19)

### TLDR

Use one **Step Functions Standard execution per conversation**. A worker keeps
advancing the persisted conversation until terminal or near its Lambda time
budget; the workflow immediately invokes it again when needed. Do not schedule
one task per expected message and do not self-enqueue. No conversation lease.
Keep conditional, atomic message/progress commits. Occasional duplicate paid
inference is acceptable; duplicate persisted progress and indefinitely stale
`running` are not. The workflow catches worker failures and writes terminal
state directly to DynamoDB, without another finalizer Lambda.

### Failure sources and what we promise

Inference can throttle, time out, return invalid output, or succeed just before
the worker loses its response. Lambda can crash, hit its hard timeout, or fail
to start. A worker's `finally` cannot handle failures before/after its execution.
Our observed self-enqueue chain stopped around turn 16 at Lambda recursive-loop
protection: the next handler never ran, so business state remained `running`.
SQS retries/DLQ and CloudWatch errors do not update business rows automatically.
The previous poll-time 24-hour deadline repair was too late and too opaque.

Guarantees: committed history/progress is consistent; unfinished work is either
continued or explicitly failed; terminal results do not change. We do **not**
promise exactly-once Bedrock invocation/billing. A model call that succeeded
before persistence may be retried. Record usage for responses we receive even
when their messages are discarded after the deadline.

### Options considered

1. **SQS self-callback:** one accepted turn per invocation, then enqueue the
   next turn. Explicit `RecursiveLoop=Allow` is supported, not a hack; deadline,
   turn limits, retry caps, and concurrency bounds must replace its safety net.
   It still needs recovery for commit-success/enqueue-failure and platform-level
   failures. Rejected here because scheduling and observability remain custom.
2. **SQS "bang" / durable task list:** one message represents the conversation.
   Run from its checkpoint, ACK only at terminal, otherwise let it be delivered
   again. FIFO with conversation ID as group, batch size one, one execution
   entry point, and visibility longer than execution can replace our lease.
   Conditional transactional commits are still required. No quantitative
   99.99% exactly-once promise: delivery retries and ambiguous inference remain.
   Healthy continuation consumes receive attempts and waits for visibility;
   DLQ/retention are finite. AWS recommends visibility at least 6x Lambda
   timeout, which makes this a poor fit for immediate continuation. Cancellation
   still requires cooperative state/deadline checks.
3. **Step Functions, fixed per-message loop:** simpler execution visibility,
   but `for expected_message_count: invoke()` confuses attempts with accepted
   messages. Silence and retries make them different. A step per
   model call also moves speaker policy into infrastructure. Rejected.
4. **Step Functions, loop until terminal (chosen):** workflow schedules worker
   slices, worker owns AI policy and persists progress. Re-read actual progress
   on every retry. No fixed inference/message count in the workflow. This moves
   orchestration to AWS without turning the AI policy into an ASL program.

### State, deadlines, and commits

Conversation fields: `execution_state=pending|running|terminal`, and nullable
`outcome=succeeded|timed_out|failed` (`token_limit_reached` is future work).
`status=queued|running|completed|timed_out|failed` remains a derived API/export
projection, updated together with these fields, not a second decision source.
An immutable `deadline_at` starts at batch creation. Any of these stops work:

- Deadline reached: `timed_out`; primary safety bound.
- 1,000 worker dispatches: `failed`, reason `worker_iteration_limit`.
- Accepted AI message count reaches `max_turns`, or content chars reach
  `max_total_chars`: `succeeded`. Silence/system events do not count; the final
  accepted message may exceed the character target.
- Non-recoverable inference failure or exhausted execution retries: `failed`.
- Future: token budget, without changing the orchestration model.

Before **every** provider call (including silent candidates and retries),
check deadline. After inference, check again before accepting output. Expired
results are dropped; do not start another call. The final application-clock
check is immediately before transaction construction, not a promise that DDB
has a server-side wall-clock condition. Terminal is one-way. Export/status
projections may change later, but accepted history and outcome may not.

Atomically write event, progress, and any terminal/counter transition. A stable
turn ID and expected progress revision prevent duplicate commits on retry.
This handles crash-between-writes even with a single writer; it is not a lease.
External inference and its usage write cannot share that DDB transaction.

### Workflow failure handling

Read persisted state; check deadline/dispatch budget; invoke worker; repeat on
nonterminal output. Worker uses a soft slice budget and returns before the hard
Lambda timeout. Task timeout exceeds Lambda timeout with margin. Transient
worker failures have a finite workflow-owned retry budget; every retry passes
through deadline checks. Permanent provider errors terminate that conversation,
not the whole batch. Unfinished retries are not charged as accepted turns.

On exhausted worker failure or dispatch/deadline limit, Step Functions uses
native DynamoDB integration to atomically terminate the conversation and adjust
batch counters. Preserve an already-terminal outcome. Finalize the batch from
its counters, so no browser polling is required to complete a normal run.
Do not add a finalizer Lambda sharing the worker's failure surface.

The workflow hard timeout includes cleanup headroom beyond the business
deadline. Top-level abort/timeout or broken IAM/DDB can still prevent cleanup;
these must surface as failed/timed-out/aborted executions and alarms, not be
claimed as successfully finalized. Management reconciliation can check the
execution status to repair stale projections. StopExecution does not revoke an
in-flight Bedrock call. No cancellation UI is included in this change.

References: [Lambda recursion](https://docs.aws.amazon.com/lambda/latest/dg/invocation-recursion.html),
[SQS visibility](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html),
[Step Functions error handling](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html),
[DynamoDB integration](https://docs.aws.amazon.com/step-functions/latest/dg/connect-ddb.html).

## Original architecture (superseded orchestration)

The following original SQS worker/lease/deadline design is retained for decision
history. The redesign above and implementation architecture below replace its
worker scheduling, late-result policy, and failure handling. API ownership,
snapshots, prompt construction, usage recording, and export behavior still apply.

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
try to provision up to 50 conversations synchronously. This avoids another
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

The original deadline starts at batch creation. **Superseded late-result policy:** after it expires, workers must not start
another inference. An inference already in progress may finish and persist its
result, but the worker starts no subsequent turn. Exports include only
`completed` conversations and include a manifest with aggregate terminal-state
counts; incomplete histories are omitted. The v1 batch timeout is fixed at 24
hours and is not user-configurable.

### Initial operational limits

- `batch_count`: `1..50`.
- One worker invocation processes one accepted turn. Silence and retries do not
  count as turns.
- Batch timeout: 24 hours from batch creation.
- Lambda timeout: 10 minutes.
- Conversation lease: 11 minutes.
- SQS visibility timeout: 12 minutes.
- SQS event batch size: 1.
- Initial worker maximum concurrency: 10; increase it only after observing
  Bedrock throttling and completion latency.

A maximum-size batch with default conversation limits has 50 conversations,
100 messages each, and two AIs, or up to about 5,000 model invocations.
At concurrency 10 and roughly 4-8 seconds per invocation, model time is about
34-67 minutes, before queueing, retries, and throttling. A 24-hour deadline
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

## Original v1 exclusions (historical)

- Source-material attachments were initially excluded; see
  [the subsequent attachment feature](feat-prompt-attachments.md).

## Original LLD sketch (superseded orchestration)

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
POST /api/downloadAiConversationBatch/<batch_job_id>
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
produce exactly one message. `max_message_chars` is an optional prompt-only
suggestion, not a tool-schema constraint or reason for correction/failure.
Every provider invocation, including silence and retries, records usage and
participates in the billing gate.

### Conversation Limits (2026-09-19)

- Max message length is optional: null/omitted means no message-length instruction.
  Do not truncate or retry long messages. Provider output-token limits remain a
  technical safeguard, not the conversation's business limit.
- Editor order: Max messages, Max characters. Keep API keys `max_turns` and
  `max_total_chars`; either terminates the conversation. Count only accepted AI
  messages/content (not system events, silence, or failed calls). The last message
  may exceed the character target; preserve it intact. Timeout/dispatch caps remain.
- Each inference gets dynamic progress after the cache checkpoint, e.g.
  `Conversation progress: 12/100 messages; 3000/10000 characters used.`
- Static AI-only rules instruct the model to finish its points and naturally
  conclude as either limit approaches, avoiding new topics near the limit.
  This is guidance, not guaranteed closure; no extra forced final round.

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

## Implementation architecture: terminal-driven workflows

This section supersedes the original LLD's work queue, conversation lease,
per-turn invocation, late-result acceptance, and polling-only timeout repair.

```text
amp-webapp editor (Ara-yjx/amp-generator-beta)
  -> Flask owner-authenticated create/poll/export APIs
  -> provision SQS -> Provisioner Lambda
       -> immutable settings/prompt reference + deterministic conversation rows
       -> StartExecution(name=conversation_id, stable input)
            Standard conversation workflow
              -> read state / deadline / dispatch budget
              -> Worker Lambda: infer + commit repeatedly within a time slice
              -> nonterminal: immediately repeat
              -> failure: bounded retry, then native DynamoDB terminal transaction
              -> finalize batch from persisted counters
  -> export SQS -> Exporter Lambda -> private ZIP in S3

Exceptional FAILED/TIMED_OUT/ABORTED execution
  -> EventBridge -> native-DDB recovery workflow (no Lambda/inference)
  -> polling fallback: DescribeExecution + request the same recovery workflow
```

### Code ownership

- `ai_batch/worker.py`: provider policy, actual-history progress checks, bounded
  execution slices, pre-call/post-call deadline enforcement. No queue handling,
  expected-turn dispatch contract, or conversation lease.
- `ai_batch/store.py`: deterministic execution start, conditional start/terminal
  transitions, and atomic event/progress/counter transactions. A successful
  turn has one stable write ID. Terminal replay does not change counters twice.
- `bedrock_client.py`: optional pre-attempt guard for deadline-aware workers;
  their client disables hidden SDK retries and bounds network waits. Existing
  human-AI callers keep their existing client path.
- `cdk/lib/ai-conversation-workflow.ts`: actual ASL definition, shared native
  finalization building blocks, normal and exceptional recovery workflows.
  ASL owns orchestration only, never prompt construction or speaker selection.
- `AiConversationBatchStack`: replace inference FIFO queue/DLQ/mapping with
  Standard workflow, execution alarms, exceptional-recovery EventBridge rule,
  delivery DLQ, and a DDB-only recovery role. Keep provision/export independent.
- Management `AiConversationBatchService`: owner-check first; for new-format
  nonterminal conversations, inspect the configured workflow's execution and
  request recovery when execution has stopped. EventBridge is best-effort;
  failed recovery returns an explicit error instead of silently claiming running.
- Editor changes belong to `amp-webapp` (the current integration worktree is
  `amp-webapp-batch-editor`), not this repository's deprecated `editor/`.
  Show `Finalizing` while a failed execution's state is being reconciled, and
  continue polling until reconciliation completes.

### Bounds and state details

- 24-hour business deadline from batch creation; 25-hour workflow hard timeout.
  Expired work is rejected by both workflow and worker, including retries and
  retry calls. The extra hour is cleanup headroom, not inference budget.
- Worker soft slice: 240 seconds; Lambda hard timeout: 600 seconds; ASL Task
  timeout: 660 seconds. Do not start a call with less than 120 seconds Lambda
  time remaining. Deadline-aware Bedrock connection/read limits: 5/90 seconds.
- At most 1,000 worker dispatches, including failed dispatches; at most three
  consecutive failed dispatches before terminal failure, two seconds between
  attempts. Successful slices reset consecutive failures, not dispatch count.
- Native DDB tasks retry transient failures up to three times. Infrastructure
  failures remain visible in execution history and CloudWatch alarms; alarms
  require the environment's notification routing before release.
- No full history/settings in workflow payloads. Carry only IDs, immutable
  deadline, small counters, and minimal DDB state projections.
- `execution_state` and `outcome` are the decision fields; old `status` is only
  their API/export projection. `state_version`, `next_turn`, `message_count`,
  and `total_chars` describe committed progress. `execution_arn` links to the
  operational execution. Terminal transitions update the proper batch counter
  (`queued` or `running`) in the same transaction.
- A workflow may successfully handle a failed conversation: execution
  `SUCCEEDED` is not the business outcome. The editor uses persisted outcome.
- EventBridge recovery and poll-triggered recovery preserve existing terminal
  state. Conditional transactions also reject late writes from a stopped
  workflow's still-running Lambda. No distributed lease is reintroduced.

### Local validation and release boundary

Run backend tests with `requirements-test.txt`. Workflow integration tests load
the **actual TypeScript ASL** and execute it with Moto's Step Functions engine
and DynamoDB transactions, replacing only Lambda transport and model/usage
boundaries. The normal scenario accepts 20 messages over four worker slices;
fault scenarios cover expired results, unavailable workers, lost responses
after commit, dispatch limits, terminal replay, and exceptional reconciliation.
Moto's RFC3339 timestamp parser requires a test-only adapter for fractional UTC
timestamps; this is not production runtime code.

Before deployment, validate both definitions, synth/diff the target stack, and
check the retained tables/bucket. Configure management with
`AI_BATCH_STATE_MACHINE_ARN` and `AI_BATCH_RECOVERY_STATE_MACHINE_ARN`; its role
needs scoped `states:DescribeExecution` for conversation/recovery executions
and `states:StartExecution` only for the recovery machine. Provisioner can start
only the conversation machine; worker cannot enqueue or start executions.
Do not swap the runtime under an active old FIFO batch. Use isolated infra for
the first cloud run, or wait for existing batches to finish before cutover.
This local implementation does not authorize changes to shared user paths.

### Verification record (2026-09-19)

- Backend full suite: 320 passed; the opt-in paid test is skipped by default.
  Workflow integration scenarios include 20-message continuation, deadline
  before dispatch, late output, deadline between retries/correction attempts,
  worker startup failure, crash after commit, dispatch cap, aborted workflow
  recovery, terminal replay, transaction rollback, mixed batch outcomes, stable
  execution dispatch, and preservation of successes after partial provisioning.
- Management service: 11 tests passed. Editor chatroom suite in
  `amp-webapp-batch-editor`: 49 tests passed; TypeScript and production build
  passed (existing CRA/lint warnings remain). CDK: 19 tests, TypeScript build,
  isolated development synth, and AWS's read-only validation of both ASL
  definitions passed with no ASL diagnostics.
- Paid local smoke: **20 real Sonnet 4.6 messages, 20 inferences, four worker
  slices, succeeded in approximately 39 seconds**. Step Functions/DDB ran in
  Moto, Lambda transport ran in-process, and usage was captured locally; only
  Bedrock was real. This is not a deployed AWS orchestration E2E.
- Successful smoke usage: 29,730 ordinary input tokens, 1,186 output tokens,
  zero reported cache read/write tokens; configured-rate estimate **$0.10698**.
  This is the successful run only, not a total AWS bill. An earlier paid smoke
  stopped at its local test-call cap because the harness counted pre-call
  slice exits as calls; the harness was corrected before the successful rerun.
- Local artifacts: `.local/ai-batch-workflow-smoke/{conversation.txt,
  conversation.json,summary.json}`; ignored by Git. No shared RDS/DDB rows,
  running customer batches, EC2 services, Lambda code, or Pages were changed.

Reproduce ordinary tests from `backend/` with
`python -m pip install -r requirements-test.txt` and `python -m pytest -q`.
The ASL tests also require the CDK Node dependencies. To explicitly opt into
the small paid smoke (default AWS credential chain; no credentials in files):

```bash
AI_BATCH_LIVE_SMOKE=20 \
AI_BATCH_SMOKE_OUTPUT=../.local/ai-batch-workflow-smoke \
python -m pytest tests/test_ai_batch_workflow_integration.py::test_live_twenty_message_conversation -q
```

### Deployed verification (2026-09-19)

- Updated only `StimulizeAttachmentDevYjx13Batch`, the backend used by local
  attachment/batch acceptance. No EC2, Pages, human-AI API/tick, shared table
  schema, or `StimulizeChatroomAiBatchDevYjx12Batch` deployment. Management HTTP
  checks used the current local Flask code; its startup helper discovers the
  workflow/recovery ARNs from this stack.
- No historical-data migration is required: new conversations receive
  `execution_state`/`outcome`, while historical results retain their readable
  status. Existing unfinished FIFO jobs are not automatically resumed by SFN.
  Preflight found two stale running test batches and two legacy DLQ messages.
  They remain as diagnostic evidence, not active SFN work.
- Historical cutover used `keepLegacyWorkQueues=true` (removed after migration): preserve the
  existing FIFO/DLQ with RETAIN, but remove their worker event-source mapping.
  Review pending work before any later cleanup; do not redrive it into the new
  direct-invocation handler. CDK diff contained no DDB/S3 replacement.
- Real AWS success path: management service -> SQS -> provisioner -> Standard
  SFN -> Lambda -> Bedrock -> DDB/RDS -> export Lambda/S3. One 20-message run
  finished in **39.25 seconds**, with a test-only 55-second deadline. Downloaded
  matching TXT/JSON plus `info/prompt.txt`; timestamps are numeric and all
  accepted messages precede the deadline. RDS has 20 usage rows: 31,104 input,
  1,250 output tokens, **$0.112062** estimated cost. Authenticated local HTTP
  polling/history also passed. No browser test was run in this deployment.
- Real failure paths passed: expired work invokes no worker; three worker
  exceptions converge to failed via native DDB transactions; stopping an
  execution triggers EventBridge recovery and correct terminal counters.
  No inference was made by those fault fixtures. No executions remain running.
- Provision/export/recovery queues and DLQs were empty. The intentional abort
  triggered the workflow-aborted alarm; other checked alarms were OK. Alarm
  notification delivery and an actual 600-second Lambda hard timeout were not
  exercised. API/tick code/config snapshots were unchanged.
- Private evidence and downloads: `.local/sfn-cloud-verification/`. Test rows,
  usage and exports are retained; no customer histories were rewritten.

### Batch History in the Editor (2026-09-19)

- Save & start navigates in the current tab. History column: Conversations.
  Detail Refresh is icon-only; Download conversation data keeps a fixed label.
  The single download API prepares/reuses the archive and returns in_progress
  or ready + download_url. Poll every 3s while loading; stop after 3 consecutive
  failures or 3 minutes (15s per request), show Arco Message error, and unlock
  retry. A new click sends retry_failed=true; later polls do not restart failed
  exports. Abort client polling on navigation. Download automatically when ready.

- At the bottom of AI-only editing, list this chatroom's runs newest first:
  local time, size, status, and View details in a new tab. Include single runs,
  Refresh, Load more, loading/empty/error states. Refresh after creating a run;
  fetching history never starts inference or saves settings.
- Extend existing `POST /api/getAiConversationBatches` with optional
  `{chatroom_id, limit, cursor}`. Response: `{batches, next_cursor}` in the normal
  envelope. Existing owner-wide requests remain supported. Authorize room
  ownership; bind cursors to the owner and room. Default editor page size: 20.
- Reuse `owner-created-index` with a room filter, following sparse DynamoDB pages
  for at most 10 queries per request. A still-sparse page can be empty with a
  continuation cursor; preserve Load more. No new table, index or migration.
- Before the run buttons: "We recommend running one conversation to assess
  quality and cost before starting a batch." This is advice, not a prerequisite.
- Verified locally against dev DDB/RDS: authenticated room-filtered pagination,
  invalid-cursor rejection, browser Refresh and detail-tab navigation, and
  desktop/mobile layout. Focused management tests: 23; editor chatroom tests: 55;
  TypeScript and production build passed. No customer deployment or new inference
  batch was started for these UI checks (separate small model-capability probes ran).

### Release Follow-up: Consistent Beta Management Backend

Editor refinement: Basics contains a Mode radio (AI+Human / AI+AI); the persisted
setting still derives mode from participant counts. New batch conversations use
slot-based default display names (`Participant_001`, `Participant_002`, ...),
preserving custom persona names and immutable identity IDs. Human-AI naming and
existing conversation snapshots are unchanged. Model menus retain provider
groups, with newer/flagship choices first; this is a curated order, not a benchmark.

AI-only batches do not generate avatars or write avatar fields to messages.
Newly generated JSON exports strip legacy avatars from participants and events;
stored histories and already-built ZIPs are not rewritten. Human-AI behavior is
unchanged. AI-only persona reuse is now prohibited by launch validation. Without
configured names, every instance uses its slot default. Explicitly duplicated
display names still use the existing collision fallback; internal names receive
unique suffixes independently.

- TODO before closing this feature: route all EC2-backed APIs in hosted
  `stimulize-beta` to beta EC2, including login and general APIs, not only
  chatroom management. Its current bundle mixes prod general APIs with beta
  chatroom management. Set explicit build-time endpoints for both clients.
- Beta EC2 is for participant-user feature testing and must not connect to
  Stripe. Billing/subscription parity is not a release gate for this environment.
  Gate the switch on auth/refresh and participant-feature API/configuration
  readiness; verify the complete participant browser flow. Keep Stripe-backed
  controls unavailable in the beta UI rather than falling back to production.
  Do not switch `stimulize.org` or implicitly change the Lambda runtime endpoint.
- Beta EC2 still shares data resources with production; endpoint consistency
  does not provide database isolation. This is pending, not deployed.
