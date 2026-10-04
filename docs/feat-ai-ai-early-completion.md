# Feat: AI-AI Early Completion

Status: live batch runtime and beta management/editor released and verified
(2026-10-04). Production management/editor follow the tested source merges.
Tracking: [STML-32](https://linear.app/petryyy/issue/STML-32/).
Baseline: [AI-AI Chat](./feat-ai-ai-chat.md). This extends AI-only batch workers,
not the human-AI heartbeat/tick handler.

## TL;DR

Add `agreeToEnd()`: an AI has reviewed the latest history and has no further
substantive contribution. End successfully only when every AI confirms against
that same history, with the last speaker checked last. Readiness is not departure
or proof of consensus; participants may agree to stop while retaining differences.
Any new message invalidates all previous confirmations.

Derive each next candidate from completed tick results, never a persisted random
order or pending queue. With no ready results, preserve existing scheduling.
Keep max message count (`max_turns`); do not add expected/minimum message counts.
Existing character threshold, deadline and workflow limits remain in force.

## Room Setting and Rollout

Decided: add optional boolean `setting.allow_early_completion`. Editor label:
"Allow early completion", shown only for AI-only rooms.

- **New rooms:** editor defaults the control on and explicitly saves `true`.
- **Existing rooms and batch snapshots:** missing means `false`, preserving the
  old prompt, speech-only protocol and scheduling path. Loading/saving an old
  room must not silently opt it in; its editor control starts off.
- **Explicit values:** `true` enables the feature; `false` disables it. Runtime
  reads only the batch snapshot, so changing a room does not change an old batch.

Human-AI ignores this setting. It lives in existing JSON, not a new SQL column.

Management already preserves unknown top-level setting fields, copies the setting
into `settings_snapshot`, and returns conversation metadata without a field
allowlist. Add a shared optional-boolean type check on room create/update and
batch creation; reject strings, numbers and null with a field-specific error.
Do not add a default in management or refactor other setting validation.

Editor sends an explicit boolean. Runtime owns strict boolean validation and
the missing-value default (`false`). Validate before inference; invalid API-supplied
values also fail provisioner validation before any conversation is created.
Do not rewrite raw snapshots/request hashes to insert defaults.

**Rollout:** deploy runtime before exposing the editor control. Missing-field
batches stay on the legacy path without a migration or execution marker; do not
rewrite them to add the field. Inspect active work before updating the shared
runtime, particularly any snapshots already containing explicit `true`. A blanket
drain is not required solely for missing-field compatibility. No env switch or
SQL migration is needed.

## Options and Decision

1. **Ready means departure; never tick that AI again.** Simplest, but prevents
   reconsideration after another participant contributes something new.
2. **Keep ticking; add a reverse tool to withdraw readiness.** Allows
   reconsideration, but adds a control action and needs another rule to prevent
   speaking while still marked ready.
3. **Keep eligible; speaking withdraws readiness (chosen).** A substantive
   response already expresses renewed participation, so no reverse tool is
   needed. New speech also expires everyone else's old confirmation: they have
   not yet evaluated that contribution.

Final naming: tool `agreeToEnd()`, JSON/stored action `agree_to_end`, completion
reason `all_ai_agreed_to_end`. Do not abbreviate the action as `ready`, which can
be mistaken for connection/model readiness. Earlier names `readyToExit` and
`readyToFinish` described the same intent but are not the chosen contract.
This is consent to end the shared discussion, not leaving the room or majority
voting. One AI cannot end the conversation alone.

## Decisions and Prompt Contract

- `speak([message])`: one actual contribution; starts a new confirmation round.
- `agreeToEnd()`: explicit confirmation for the current history.
- `speak([])`: temporary silence, not consent to finish; allowed only on optional
  three-plus-AI turns.
- Invalid/missing output: an error, never silence or readiness. Use existing
  bounded recovery/failure handling; do not keep rescheduling errors forever.

Expose separate `speak` and `agreeToEnd` tools on native tool-use paths. The
current forced-`speak` selection must permit a choice; probe supported models
rather than assuming forced multi-tool choice works everywhere. JSON-only paths
need the equivalent explicit action `{"action":"agree_to_end"}`;
speech/silence can retain the existing `{"messages":[...]}` envelope. Accept
exactly one action; reject mixed actions, extra keys and malformed/truncated
control output. Do not infer readiness from prose such as "I am done".

Scope this action contract to AI-only calls. Keep human-AI parsing, prompts and
silence nudges unchanged. Preserve usable truncated speech handling, but never
recover/guess a readiness signal from incomplete output.

Prompt rules: contribute when there is something meaningful to add; choose ready
when adequately expressed, without padding to the message cap. Do not require
agreement, an extra summary or a final goodbye. Do not tell an AI that everyone
else wants to stop as a reason to conform. Static rules/examples join the cached
prefix; current progress and decision instructions remain after the checkpoint.
Update the batch's saved `info/prompt.md` reference with the new contract.

## Scheduler Derived from Tick History

A tick here is one accepted AI-only decision, not a heartbeat interval. For the
latest committed message revision, derive `attempted` and `agreed` participant
sets from valid tick results since that message. Identify authors by immutable
`ai_participant_id`, not persona/display names. A decision must refer to the
history revision actually supplied to its inference.

With last speaker `A`, let `others = participants - {A}`:

1. Preserve the existing opening: randomly select a first speaker and require
   one message. No empty conversation is completed by readiness alone.
2. If all participants are ready for the current revision, finish.
3. If `others - attempted` is nonempty, randomly choose one of them. For two AIs,
   require speech or ready; for three-plus, also allow temporary silence.
4. Otherwise, if `others - agreed` is nonempty, randomly choose one of them and
   require a nonempty message or ready, not another silence.
5. Otherwise all others are ready: tick `A` last, requiring speech or ready.
   Its ready result finishes; its new message starts another round.

```text
A speaks -> B ready -> C ready -> A ready -> completed
A speaks -> B ready -> C speaks -> recheck A/B, then C last
```

New speech resets the current-round tick record atomically. Readiness itself
does not reset anything. Once ready for an unchanged history, an AI needs no
repeat inference. The last-speaker confirmation may produce consecutive messages
from that AI; this exception occurs only after everyone else is ready.

No-ready compatibility is mandatory: two AIs alternate without optional silence;
three-plus try non-last-speakers without replacement, then force one random
candidate if all are silent. Drawing uniformly from remaining candidates is
equivalent to the existing shuffle-and-iterate policy. Do not switch to random
sampling with replacement or a fixed round-robin. Compatibility concerns policy
and selection distribution, not replaying identical random seeds or old lost
in-memory progress after a crash.

After a message, all-ready confirmation takes `N` calls if everyone immediately
confirms. For three-plus AIs, at most `2N-1` calls are needed if the other AIs all
first stay silent; two AIs need two confirmations. With no ready at all, the
existing at-most-`N` path to the next message remains. Bounds exclude provider/output
retries. A model that
never confirms may continue speaking until an existing terminal limit; never
substitute majority voting or silently treat silence as consent.

## Persistence, Termination and Visibility

The current worker checkpoints messages, not completed silent candidate checks.
Add one compact current-round record to conversation metadata: the message
revision (reuse `next_turn`) and accepted tick results `{ai_participant_id,
action}`, under `ai_tick_history`. Actions stored here are `silence` and `agree_to_end`;
speech is already recorded in message history and resets this record.
Record facts, not future plans. Derive sets on read; do not also store
a readiness map, shuffled order or queue cursor. This bounded record is scheduler
state, not a full audit log or a new participant-visible event stream.

Persist every valid non-message decision with the existing `state_version`
condition and running-status/history-revision guards; increment `state_version`
on every accepted action, not just messages. New speech atomically
appends its event, updates progress and clears the record. Last-ready acceptance,
terminal status and batch counter changes are one transaction, without a fake
message. Retries reload state; they must not double-count a decision or completion.
An absent record means no accepted decisions in the current round.

Check existing terminal conditions before inference and again after it, before
accepting any result. Do not accept a late ready/speech result, or run a final
confirmation after deadline, max messages or the character threshold is reached.
Keep Step Functions slices, retries and failure finalizers; no new infrastructure
or historical-data backfill is required. Do not change active batches' behavior
silently during rollout.

Completion stays `status=completed`, `execution_state=terminal`,
`outcome=succeeded`; add `completion_reason` with `all_ai_agreed_to_end`, `max_messages`
or `max_characters`. If one message reaches both limits, use `max_messages` for
deterministic reporting; retain both actual counters. Ready/silent calls do not increment message/character
counts. Record usage/ledger for every billable invocation, including confirmations,
errors and results rejected after a deadline. Do not promise a fixed saving:
ending sooner saves turns, but confirmation calls also cost money.

Show the completion reason in batch detail and exported metadata. Keep ready
actions out of conversation TXT/JSON message arrays, model dialogue history,
widget bubbles and Qualtrics ED. TXT export appends one final `System:` line
derived from the reason. This is an export-only footer, not a stored chat event
or an input to later inference; do not append it to the event table or duplicate
it in the JSON `events` array.

### Format Preview (Proposed, Not Live Data)

Relevant fields of a completed `chatroom-conversations` item, shown as ordinary
JSON rather than DynamoDB AttributeValue wrappers. A was the last speaker; B/C
confirmed before A. The final round is retained; a new message during the run
would instead reset `ticks` and advance `message_revision`.

```json
{
  "conversation_id": "example_conversation",
  "conversation_type": "ai_batch",
  "status": "completed",
  "execution_state": "terminal",
  "outcome": "succeeded",
  "completion_reason": "all_ai_agreed_to_end",
  "next_turn": 3,
  "message_count": 3,
  "state_version": 6,
  "last_speaker_id": "ai_a",
  "ai_tick_history": {
    "message_revision": 3,
    "ticks": [
      {"ai_participant_id": "ai_b", "action": "agree_to_end"},
      {"ai_participant_id": "ai_c", "action": "agree_to_end"},
      {"ai_participant_id": "ai_a", "action": "agree_to_end"}
    ]
  }
}
```

`state_version=6` illustrates three messages and three accepted ready actions,
not a formula for all conversations. No separate per-AI ready boolean is stored.
Only the current/final round is retained, not a complete readiness audit trail.

`conversations/0001.txt` retains the existing timestamp-free `Name (internal_name):
message` format. The content below is synthetic:

```text
Alex (planner): I suggest a small pilot before a full rollout.
Blair (critic): The pilot should have a clear success criterion.
Alex (planner): Agreed. We can evaluate retention before expanding it.
System: This conversation has ended because all AI participants were ready to finish.
```

There are no individual `agreeToEnd` lines. Add
`completion_reason: "all_ai_agreed_to_end"` as a top-level field in the corresponding
`0001.json`, alongside `events`, never as a chat event. Batch detail displays the
same reason. TXT gets exactly one export-only footer as its final line:

| `completion_reason` | TXT final line |
| --- | --- |
| `all_ai_agreed_to_end` | `System: This conversation has ended because all AI participants were ready to finish.` |
| `max_messages` | `System: This conversation has ended because the maximum number of messages was reached.` |
| `max_characters` | `System: This conversation has ended because the maximum character count was reached.` |
| Missing/unknown reason | `System: This conversation has ended.` |

Do not infer readiness or a specific limit for old exports lacking a reason.
Existing archive files remain immutable. `timed_out` and `failed` remain separate
outcomes; current exports omit those conversations and describe their omission
in the manifest, so they do not receive a successful-completion TXT footer.

## Implementation and Validation

1. **Pure scheduler + compatibility tests:** `ai_batch/scheduler.py`; table-driven
   and generated decision histories cover no-ready behavior, all-ready rotation,
   silence, reconsideration and call bounds. No AWS needed.
2. **AI-only action protocol + prompt:** `bedrock_client.py`, `prompts/ai_only.py`
   and native/JSON adapters. Test valid speech/silence/ready, ambiguous actions,
   truncation and human-AI regressions. Small real-model probes verify tool choice
   and JSON parsing before end-to-end integration. Add a local step/run conversation
   runner in `backend/scripts/`, reusing actual request construction, parsers and
   scheduling with local JSON state. Paid calls require explicit opt-in; do not
   write shared DDB/RDS/ledger. Inspect free-running traces, not only instructed
   tool calls, to assess whether agreement arrives at an appropriate time.
3. **Worker/store integration:** `ai_batch/worker.py` and `store.py`; replace the
   message-only candidate loop with history-derived decisions. Test atomicity,
   crash/retry/slice recovery, stale results, deadlines, counters and accounting
   with fake inference and isolated storage/workflow integration tests.
4. **Editor/export and API contract checks:** add the explicit boolean setting
   and completion reason in `Ara-yjx/amp-generator-beta`, not deprecated `editor/`.
   Runtime validates/defaults the field. Management validates the optional boolean
   on save/create and batch creation, preserves missing values in snapshots, and
   passes through completion reasons. Update prompt reference generation and exports
   without rewriting previous batches' saved references or raw snapshots.
5. **Controlled acceptance, then release:** real two- and three-AI runs, native
   and JSON paths, readiness withdrawn by speech, no-ready limit completion and
   a human-AI regression. Test batches stay tiny and under one minute. Inspect
   actual downloaded TXT/JSON/prompt.md, completion reasons, usage/ledger and
   workflow status; clean up test rooms. Preserve active customer batches using
   the compatibility rules above, then deploy runtime and editor.

### Live Release Checks (2026-10-04)

- Released runtime `d67094a`, management `21cddc7`, editor `e474086`.
  Only batch Lambda code changed; human-AI API/tick, heartbeat, environment
  variables, billing settings and table schemas remained unchanged.
- Missing field and explicit `false` completed at `max_messages`; native-tool
  2/3-AI runs and a Gemma JSON run completed at `all_ai_agreed_to_end`.
- Hosted beta browser verified legacy rooms remain off, new rooms explicitly
  opt in, save/test-once, completion details and actual ZIP download. TXT/JSON
  completion reasons and usage passed; human-AI send/reply also passed.
- Test runs were single-conversation and under one minute. No global batch
  pause, payment test or customer-room changes. Raw evidence stays local.

### Isolated Acceptance Evidence (2026-10-04)

The cloud evidence below predates the missing-field correction to `false`.
Do not treat the earlier missing-field cloud run as proof of legacy compatibility;
verify that separately with the corrected candidate before release.

- Candidate commits: runtime `890a66a`, management `21cddc7`, editor `217c449`
  plus the browser-verified wrapping fix `afbcda0`. All remain on local
  `feat/yejiaxi/ai-ai-early-completion` branches; no main push or live release.
- Local suites: runtime 500 passed / 1 existing opt-in paid test skipped;
  management 72 passed; editor chatroom 73 passed; CDK 28 passed. Editor typecheck
  and production build passed, with existing CRA/lint warnings. Tests cover
  human-AI compatibility, no-ready selection, reconsideration, stale commits,
  slice/retry recovery, deadline rejection and accounting, not only happy paths.
- Real Bedrock local traces and cloud tests exercised Sonnet 4.6 native tools,
  Gemma 3 27B JSON, Scout auto-tool choice, two/three AIs and mixed-model personas.
  Gemma exposed an assistant-first history rejection; the AI-only request builder
  now prepends a neutral user frame when needed, without changing stored history
  or the shared human-AI mapper.
- Browser path: local editor -> SSH tunnel -> separate loopback process on beta
  EC2 -> temporary batch stack. Created a room, verified default-on/explicit-off
  save and reload, ran one conversation and a two-conversation disabled batch,
  viewed reasons/usage, and downloaded actual TXT/JSON/prompt.md archives. Browser
  and direct-API ZIP downloads matched byte-for-byte. Consent never became an event;
  TXT had one final reason footer. The local harness imposed a 55-second deadline;
  public management's 24-hour setting was not changed.
- Cloud aggregate: 12 batches, 12 conversations: 10 completed, one explicit model
  output failure, one deliberately expired conversation; another invalid-snapshot
  batch created no conversation. Six completions were unanimous, three hit message
  limits, one hit the character limit. Invalid snapshot and expired work made no
  inference. 47 paid invocations, including confirmations and the failed output,
  recorded about $0.1266 estimated usage. Temporary runtime had no ledger endpoint;
  wallet writes were not enabled for these tests.
- Do not hide the failure: one three-AI Sonnet opening returned
  `invalid_tool_arguments` and failed at zero messages. Four local opening probes
  and a new cloud run then succeeded; this does not establish its cause or a
  statistical reliability rate. It was not interpreted as silence/consent.
- All temporary workflows reached terminal state; queues/DLQs were empty and
  alarms OK. A duplicate terminal worker call made no new inference/event/counter
  change. All 55 deployed Python modules matched the runtime commit. Live Lambda
  code/env/heartbeat and the public beta PID/release remained unchanged.
- Cleanup: nine test rooms are inactive; all three temporary stacks, their tables,
  workers, workflows, queues, export bucket and retained test log group were removed.
  The beta-only IAM grant, loopback process, copied private env and local tunnel/editor
  were removed/stopped. Shared RDS usage records remain. AWS refuses manual deletion
  of the three automatic deleted-table system backups; they expire on 2026-11-08.
  No running temporary infrastructure remains.

Remaining release work: verify missing-field compatibility and inspect active
batches, then release runtime before editor. Raw responses, identifiers and downloaded test data stay
in gitignored local artifacts, not this document.

## Main Risks and Mitigations

- **Premature or unending discussion:** unanimous readiness is not evidence of
  research quality or consensus. Evaluate real conversations for premature
  closure/repetition, avoid pressure to agree, retain existing terminal limits
  and let researchers disable the feature. A new prompt can change model output
  even when no ready action occurs; compatibility guarantees scheduling policy,
  not identical experimental results.
- **Model/protocol regression:** multiple tools, JSON envelopes, mixed actions
  or malformed/truncated output can be misclassified. Keep readiness explicit,
  scoped to AI-only and tested on representative native/auto/JSON models; fail
  visibly rather than inventing a ready vote. Regress human-AI separately.
- **Stale or duplicate confirmation:** new messages, retries and Lambda slice
  boundaries can otherwise complete too early, lose decisions or double-count.
  Guard every action with history revision, running status and incremented state
  version; transact last-ready acceptance with terminal state and batch counters.
- **Default/rollout drift:** new editor rooms explicitly opt in, but missing means
  false for stored rooms and snapshots. Test existing-room load/save, in-flight
  legacy batches, explicit true/false and invalid values. Management must preserve
  absence; runtime must not rewrite snapshots or request hashes.
- **Cost and liveness:** confirmation adds paid calls; duplicate inferences may
  still occur on retry even when commits are idempotent. Preserve usage/ledger,
  bounded recovery and before/after-inference terminal checks. Monitor readiness
  calls and completion reasons; do not promise a fixed savings percentage.
