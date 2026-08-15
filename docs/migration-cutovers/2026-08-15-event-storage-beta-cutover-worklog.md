# Event Storage Beta Cutover Worklog - 2026-08-15

## Result

**PASS.** Shared beta now uses `chatroom-conversation-events` for all new
participant-visible history. No resume runtime or `Stimulize-backend` change
was released. Source was fast-forwarded to `main` without a PR or force push.

## Migration

- Runtime source SHA: `7b4bace390f1e21def35c0867fc325d7bc80d768`
- Region: `us-east-2`
- Final backup: `stimulize-chatroom-event-cutover-20260815t084805z-final`
- Backup size: 35,642,446 bytes
- Fixed cutover timestamp: `1786784268735`
- Plan hash: `d03f5e82c432d8ef306f397f1d1443d90e0d9aac778caa698063182f76475fae`
- Metadata rows migrated: 591
- Participant-visible events migrated: 20,034
- Malformed rows: 0
- Diagnostics discarded from history: 568 `lobby_created`, 59,117 `tick`
- Standalone verify: 591 conversations, 20,034 events, no missing/extra
  partitions, and legacy lists unchanged

The API entered drain, then maintenance; the heartbeat remained disabled for
the migration and runtime switch. The write freeze lasted about 41 minutes.

## Runtime Acceptance

- Legacy and event-runtime CDK diffs contained no table replacement/delete or
  heartbeat update.
- One initial event-runtime deployment failed before traffic reopened because
  the predeployed table stack did not expose a new cross-stack export. Both
  consumer stacks rolled back cleanly in maintenance. The fix imports the
  fixed table name independently, avoiding a table-stack update.
- Local editor preview: one human message and one AI reply in 14.937 seconds.
- Hosted GitHub Pages preview: one human message and one AI reply in 15.241
  seconds.
- Both test rooms used a 30-second duration cap, used opaque polling cursors,
  wrote only event-table history, and left their legacy lists empty.
- Pages deployment run `31877769418` succeeded from `main`.

## Final State

- Event items: 20,046 (20,034 migrated plus 12 acceptance-test events)
- Metadata rows with `event_storage_version=1`: 593
- Active conversations: 0
- Open lobbies: 0
- API and tick mode: `normal`, event storage enabled
- Heartbeat rule: enabled
- Cleanup alarm: OK; cleanup DLQ: empty
- API, tick, heartbeat, and cleanup Lambda errors during the window: 0
- Final targeted API/tick CDK diff: none

## Resource Cleanup Inventory

- **Keep - shared beta:** production Pages, shared RDS, live API/tick/heartbeat,
  live metadata/event/lobby tables, and event-cleanup resources.
- **Deleted 2026-08-15 - rehearsal:** the three
  `stimulize-chatroom-event-rehearsal-20260815t070936z-yjx-*` tables and their
  rehearsal backup were removed after manifest-hash-guarded cleanup and AWS
  verification. They had no live dependency.
- **Keep until resume E2E - resume dev:** `stimulize-chatroom-proto-resume`
  Pages plus the seven `StimulizeChatroomEventDevYjx20260725*` stacks. This
  environment has independent conversation/event/lobby DDB tables and runtime
  Lambdas; only chatroom settings and usage share RDS. Disable its heartbeat
  while idle. After deletion, also remove any implicit Lambda log groups.
- **Keep through soak:** the final cutover backup, migration markers, untouched
  legacy lists, numeric `after` compatibility, and local raw cutover evidence.
- **Never include in this cleanup:** shared RDS, live event-storage stacks, or
  the shared CDK bootstrap stack. Keep the resume source branch even after its
  build-artifact repo is removed.

The rehearsal cleanup is complete. The final backup and rollback evidence
remain through at least the initial 24-hour soak.
