# Event Storage Live-Backup Rehearsal - 2026-08-15

## Result

PASS. The live backup was restored and migrated in isolated tables. Apply,
idempotent rerun, standalone verify, cursor checks, and a short browser preview
all passed. No live conversation row was migrated and no runtime stack was
updated.

## Run

- Region: `us-east-2`
- Run ID: `20260815t070936z-yjx`
- Fixed cutover timestamp: `1786777776579`
- Manifest hash:
  `3eb3f3ffba1c6d18ccc4a98a474ad0127372ab69d7f926914730aabd4ae8efbe`
- Migration plan hash:
  `04cb2eb050afb21f0c9808799d87e4b30886979e05ff163ccd69365fe8d87a33`
- Prepare/dry-run duration: 306.563 seconds
- Apply, verify, checkpoint reset, and idempotent rerun: 1,621.646 seconds
- Standalone verify duration: 240.299 seconds

Retained resources:

- Backup: `stimulize-chatroom-event-rehearsal-20260815t070936z-yjx-backup`
- Metadata: `stimulize-chatroom-event-rehearsal-20260815t070936z-yjx-metadata`
- Events: `stimulize-chatroom-event-rehearsal-20260815t070936z-yjx-events`
- Lobbies: `stimulize-chatroom-event-rehearsal-20260815t070936z-yjx-lobbies`

The backup is `AVAILABLE` and is 35,642,446 bytes. Cleanup was not run.

## Migration Checks

- Source rows: 591
- Migrated metadata rows: 591
- Visible history events: 20,034
- Malformed rows: 0
- Discarded diagnostics: 568 `lobby_created`, 59,117 `tick`
- Metadata/event canonical hashes: matched
- `event_storage_version=1` and tick-state projections: matched
- Legacy embedded event lists: preserved
- Extra or missing event partitions: none
- Checkpoint reset and second apply/verify: passed with no event rewrites
- Standalone read-only verify: 591 metadata rows and 20,034 events

The first row-by-row rehearsal write was safely interrupted after a checkpoint
because it was too slow. The migration now validates existing partition items,
batch-writes only missing events, and still verifies each full partition hash
before writing metadata/checkpoints. The same plan hash resumed successfully.

## Runtime Checks

- Constructed local JWT read a migrated conversation through the local API.
- Numeric `after=0`, opaque forward cursor, and backward `before` cursor each
  returned non-overlapping pages.
- Browser flow used the real management API/shared RDS and the local rehearsal
  runtime: login, create chatroom, edit, save/activate, launch preview, send one
  message, and receive one AI reply.
- Preview reached the AI reply in 24.69 seconds with
  `max_duration_seconds=45`.
- The test conversation produced 6 event-table items, including human and AI
  messages, and zero legacy embedded events.
- The test conversation was ended and the RDS chatroom was soft-deleted.
- The test ID was absent from both live DynamoDB tables.
- Post-E2E retained rehearsal counts: 592 metadata, 20,040 events, 1 lobby.

## Live Boundaries

- `chatroom-conversations`: PITR enabled, `KEYS_ONLY` stream, deletion
  protection enabled, and `RETAIN` in CloudFormation.
- `chatroom-conversation-events`: PITR enabled, deletion protection enabled,
  PAY_PER_REQUEST, and empty after all rehearsal/E2E work.
- Cleanup stream mapping is enabled; its DLQ exists and its alarm is `OK`.
- Live API Lambda, tick Lambda, and heartbeat rule snapshots were byte-identical
  before and after deployment.
- Final live scan: 591 rows, zero `event_storage_version` attributes.
- No resume runtime, live migration, heartbeat change, widget deployment, or
  `Stimulize-backend` change was made.

## Local Verification

- Backend: 219 passed
- Editor: 29 passed; production build passed
- CDK: 9 passed; TypeScript build and targeted synth passed
- Widget: production build passed
- Targeted CDK diff contained no replacement/delete and no API/tick/heartbeat
  update.
- Final targeted CDK diff after aligning the cleanup asset: zero differences.

Known non-blocking output: existing short test-JWT warnings, CDK deprecation
warnings, editor bundle-size warning, and existing npm dependency audit items.

## Future Cleanup

Review the retained resources first. To delete only this run, execute from
`backend/` with the exact manifest confirmation:

```bash
python scripts/rehearse_live_conversation_event_migration.py \
  --stage cleanup \
  --source-table chatroom-conversations \
  --region us-east-2 \
  --run-id 20260815t070936z-yjx \
  --cutover-at-ms 1786777776579 \
  --work-dir ../.local/migration-rehearsals/20260815t070936z-yjx \
  --confirm-source-table chatroom-conversations \
  --confirm-manifest 3eb3f3ffba1c6d18ccc4a98a474ad0127372ab69d7f926914730aabd4ae8efbe
```
