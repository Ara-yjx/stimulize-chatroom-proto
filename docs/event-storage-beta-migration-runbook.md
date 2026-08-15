# Event Storage Beta Migration Runbook

This runbook is the only approved path for migrating the shared beta tables.
Run it from a clean, tested `main` checkout in AWS account `487957693199` and
region `us-east-2`. Keep one operator for the entire window.

## Fixed Resources

```text
metadata table: chatroom-conversations
event table: chatroom-conversation-events
lobby table: chatroom-lobbies
API Lambda: ChatroomApiStack-ChatroomApiFunctionACFDFB70-ZRJSt5R9wq9c
tick Lambda: chatroom-tick-handler
heartbeat Lambda: chatroom-tick-heartbeat
heartbeat rule: TickHeartbeatStack-HeartbeatSchedule1795EA80-DELzfJfL38I9
API URL: https://pmvb4orly5.execute-api.us-east-2.amazonaws.com/prod
```

Store all raw output under `.local/migration-cutovers/<UTC run ID>/`. Never
commit tokens, messages, raw reports, checkpoints, or conversation IDs.

## 1. Readiness And Legacy Predeploy

1. Confirm a clean checkout, exact `main` SHA, AWS identity, and an exclusive
   deployment window.
2. Run all backend, editor, frontend, and CDK tests/builds.
3. Diff both runtime modes. Stop on any table replacement/delete or heartbeat
   update:

```bash
cd cdk
npx cdk diff TickHandlerStack ChatroomApiStack \
  -c chatroomServiceMode=normal \
  -c enableEventStorageRuntime=false
npx cdk diff TickHandlerStack ChatroomApiStack \
  -c chatroomServiceMode=maintenance \
  -c enableEventStorageRuntime=true
```

4. Predeploy the new code while it still uses legacy storage, then smoke-test
   the API:

```bash
npx cdk deploy TickHandlerStack ChatroomApiStack \
  -c chatroomServiceMode=normal \
  -c enableEventStorageRuntime=false \
  --exclusively --require-approval never
```

## 2. Drain And Freeze

1. Deploy `drain` to the API only. Use `--exclusively` so the tick stack stays
   unchanged:

```bash
npx cdk deploy ChatroomApiStack \
  -c chatroomServiceMode=drain \
  -c enableEventStorageRuntime=false \
  --exclusively --require-approval never
```

2. Confirm `/auth/token` returns `503 service_draining`. Wait until both the
   active-conversation query and open-lobby scan return zero.
3. Disable the heartbeat rule, then deploy maintenance to both runtime Lambdas:

```bash
aws events disable-rule --region us-east-2 \
  --name TickHeartbeatStack-HeartbeatSchedule1795EA80-DELzfJfL38I9
npx cdk deploy TickHandlerStack ChatroomApiStack \
  -c chatroomServiceMode=maintenance \
  -c enableEventStorageRuntime=false \
  --exclusively --require-approval never
```

4. Wait at least 120 seconds for an old tick invocation to finish. Confirm
   auth/send/live polling return maintenance, while OPTIONS and history remain
   readable. Recheck active conversations and open lobbies are zero.

## 3. Backup And Migration

Create a uniquely named final backup and wait for `AVAILABLE`. Set one fixed
`CUTOVER_AT_MS` after maintenance is confirmed. Before dry-run, require an
empty event table and zero existing `event_storage_version` markers.

Run from `backend/`:

```bash
.venv/bin/python scripts/migrate_conversation_events.py \
  --source-table chatroom-conversations \
  --target-metadata-table chatroom-conversations \
  --target-event-table chatroom-conversation-events \
  --region us-east-2 \
  --cutover-at-ms "$CUTOVER_AT_MS" \
  --dry-run --allow-protected-table \
  --checkpoint "$RUN_DIR/checkpoint.json" \
  --report-json "$RUN_DIR/dry-run.json"
```

Malformed rows, an unexpected count, or a non-empty target stops the operation.
Review the aggregate report and copy the full `plan_hash`. Apply and perform a
separate read-only verify with that exact hash and timestamp:

```bash
.venv/bin/python scripts/migrate_conversation_events.py \
  --source-table chatroom-conversations \
  --target-metadata-table chatroom-conversations \
  --target-event-table chatroom-conversation-events \
  --region us-east-2 \
  --cutover-at-ms "$CUTOVER_AT_MS" \
  --apply --allow-protected-table --confirm-plan "$PLAN_HASH" \
  --checkpoint "$RUN_DIR/checkpoint.json" \
  --report-json "$RUN_DIR/apply.json"

.venv/bin/python scripts/migrate_conversation_events.py \
  --source-table chatroom-conversations \
  --target-metadata-table chatroom-conversations \
  --target-event-table chatroom-conversation-events \
  --region us-east-2 \
  --cutover-at-ms "$CUTOVER_AT_MS" \
  --verify --allow-protected-table --confirm-plan "$PLAN_HASH" \
  --checkpoint "$RUN_DIR/checkpoint.json" \
  --report-json "$RUN_DIR/verify.json"
```

Verify counts and canonical hashes, no missing/extra partitions, all metadata
markers/projections, and byte-equivalent legacy `events` lists.

## 4. Runtime Cutover And Acceptance

Keep maintenance enabled and heartbeat disabled while deploying event storage:

```bash
cd ../cdk
npx cdk deploy TickHandlerStack ChatroomApiStack \
  -c chatroomServiceMode=maintenance \
  -c enableEventStorageRuntime=true \
  --exclusively --require-approval never
```

Run read-only history checks using numeric `after`, forward cursor, and
backward cursor. Then deploy `normal` with event storage enabled and immediately
run one controlled browser preview under one minute:

```bash
npx cdk deploy TickHandlerStack ChatroomApiStack \
  -c chatroomServiceMode=normal \
  -c enableEventStorageRuntime=true \
  --exclusively --require-approval never
```

The preview must write only event-table items, leave the legacy list unchanged,
and receive an AI reply. On success, enable the heartbeat rule and confirm it
is `ENABLED`:

```bash
aws events enable-rule --region us-east-2 \
  --name TickHeartbeatStack-HeartbeatSchedule1795EA80-DELzfJfL38I9
```

Finally dispatch `Deploy Pages Site` from `main`, wait for success, and repeat
the hosted preview smoke test.

## Failure Boundary

- Before the first event-runtime write, restore `eventStorage=false`,
  `serviceMode=normal`, and heartbeat. Untouched legacy lists remain usable.
- After the first event-runtime write, switch both Lambdas to `maintenance` and
  fix forward. Never restore the legacy writer.
- Do not clean up the final backup, rehearsal resources, event table, migration
  markers, or legacy lists during the migration window.
