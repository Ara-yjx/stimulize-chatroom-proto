# Prompt Attachments: Isolated Integration

2026-09-13. Implementation remains uncommitted for review. Initial integration
used SQLite/mock RDS. The explicitly authorized follow-up added `chatroom_asset`
and dedicated test data to shared RDS. No existing customer deployment, shared
IAM role, or Pages branch was changed.

## Implemented

- Room-owned immutable file library: upload/list/download, same-room ownership,
  validation, quotas and idempotent retries. Detach only removes a prompt reference.
- Shared and per-persona selection from one library. Reusing an ID does not upload
  another object; identical uploads with different IDs remain separate.
- Trusted snapshots in human-AI creation and AI batch creation. Resume preserves
  the old snapshot. Native attachment blocks are assembled before cache points;
  widget auth/settings never receive attachment manifests or file references.
- Prompt reference includes ordered file metadata and bounded TXT content.
- Observed-run cost API and editor estimate: matches saved generation settings,
  aggregates recorded write-time costs, and multiplies by the new batch size.
  Missing/unknown-price usage is not zero. No automatic paid test run.

## Verification

- Runtime: 305 tests passed. Management: 43 tests passed. Editor: 42 tests passed,
  TypeScript and optimized build passed. CDK: 19 tests and isolated synth/diff
  passed. Widget build passed. Existing CRA/JWT-fixture warnings remain.
- Real browser: upload TXT, select/reuse the library file in a persona, save,
  download JPEG, and inspect desktop/mobile modal layout. Download SHA-256 matches
  the original JPEG. Direct HTTP tests also verify retry, detach/re-add and
  rejection of cross-room attachment IDs.
- Human-AI: local maintained editor -> new API Gateway/Lambda -> new DDB/S3 ->
  Bedrock. Sent a human question and received a correct description of the cups.
  Runtime fixture duration was 45 seconds. Both test conversations were ended
  through the isolated tick handler after expiry; no ongoing heartbeat was created.
- AI-only: local management -> isolated SQS/provisioner/worker/exporter. Two
  separate single-conversation/two-turn tests completed and exported in 29.97 and
  32.44 seconds. The latter included shared JPEG and persona PDF/TXT. Downloaded
  ZIP contains matching TXT/JSON histories with integer timestamps, plus file
  metadata and inline TXT in `info/prompt.txt`.
- New queues and DLQs were empty; no worker/provisioner errors observed in the
  checked integration log window. The temporary download-signing diagnosis stack
  was deleted once the real issue was fixed: presigned downloads must use the
  regional S3 endpoint, avoiding a redirect that invalidates the signed hostname.
- Protected Pages asset remains `main.95eb5b04.js` / `main.f1c0d2a5.css`, unchanged
  from the read-only baseline. No hosted editor/widget was published.

## Capability Probes

One native PDF + JPEG + PNG + TXT-context request with a speak tool was accepted
by Sonnet 4.6, Sonnet 4.5, Haiku 4.5, Opus 4.6 and Opus 4.7. Each used maxTokens=100.
Opus 4.7 requires omitting temperature; the adapter does so and the editor disables
its temperature override. Sonnet 4 was denied by AWS as an unused legacy model,
so it is excluded from this deployment's allowlist. These probes establish request
acceptance, not exhaustive vision accuracy, long-context or cache-boundary coverage.

The five successful multi-format probes used about $0.153941 at the repository's
configured rates. This excludes earlier probes, the preview/batch, storage and
transport, and is not an AWS invoice total.

## Retained Test Resources

All resources are in `us-east-2`, with prefix `stimulize-attachment-dev-yjx13`:

- CloudFormation stacks `StimulizeAttachmentDevYjx13{Files,Metadata,Events,Lobby,Secrets,Tick,Api,Batch}`.
- Private attachment bucket `stimulize-attachment-dev-yjx13-files`; local SQLite
  library records refer only to objects in this bucket.
- DDB `-conversations`, `-events`, `-lobbies`, `-batches`; dedicated API/tick/
  cleanup/provisioner/worker/exporter Lambdas, queues/DLQs, secrets and log groups.
- Export bucket `stimulizeattachmentdevyjx13ba-exportbucket4e99310e-ef5syyg32mdw`.
- API: `https://h5gps2ljg5.execute-api.us-east-2.amazonaws.com/prod/`.

No automatic cleanup is configured for ready attachments. Retained test resources
may incur small storage/PITR charges. Future cleanup must name these exact stacks
and buckets, inspect contents, and account for retained buckets/log groups. Never
use an unqualified default CDK destroy/deploy against the main application.

## Reproduce Locally

Use `cdk/bin/attachments-dev.ts` with a unique `devPrefix=stimulize-attachment-dev-*`,
`rdsHost=` and `useProdRds=false`. Inspect the synthesized resource names and diff
before deploying. Shared RDS requires explicit `confirmSharedRds=stimulusdb/postgres`
and the reviewed writer/database/secret. No heartbeat is added unless requested.

In the management worktree, `scripts/run_attachment_sandbox.py` starts loopback
Flask with a local SQLite database, never inherited RDS bindings. The optional
cloud config accepts only isolated resources. `check_attachment_library.py`,
`publish_attachment_fixtures.py` and `check_attachment_batch.py` exercise the flow.
The fixture publisher caps interactive runtime duration at 45 seconds independently
of editor defaults. These helpers currently guard the reviewed test deployment.

Maintained editor environment: attachments enabled; management
`http://127.0.0.1:5123`; runtime set to the isolated API; widget served locally from
`http://127.0.0.1:5174/chatroom.min.js`. Local editor is port 3023 with hash routes.
Playwright storage state and test credentials are generated under management
`.local/attachment-sandbox/`; never put these in a hosted build or tracked files.

## Release Boundaries

### Authorized Shared-RDS Follow-up

- Added only `public.chatroom_asset` and its index to the existing `postgres`
  database on the `stimulusdb` Aurora PostgreSQL writer. Seven-day backup/PITR
  was checked. TLS certificate verification used the regional RDS CA bundle.
  DDL ran transactionally with 3-second lock and 15-second statement timeouts.
  Columns and unique indexes were checked before commit. `chatroom_usage` and
  existing customer rows were not migrated or rewritten.
- Reviewed DDL SHA-256:
  `c7b301bdf9359e11777bacc730658b3d001a1b7d5043acaa14d26a32b4215cca`.
  The first attempt failed on a script SQL-splitting error and rolled back;
  executing the complete reviewed SQL fixed it. No partial DDL was committed.
- Only dev Api/Tick/Batch stacks were deployed, explicitly and exclusively.
  Their dedicated roles can read the existing RDS secret. No shared IAM role,
  heartbeat, customer stack or Pages deployment was changed. `STIMULIZE_API_URL`
  remains unset, so credit check/debit stays disabled.
- Real-RDS library checks passed: upload/retry, detach/re-add, exact download
  bytes and cross-room reference rejection. Only two dedicated test rooms were
  created, under an existing account from the private account file; no user or
  credential records were created/modified.
- Two two-turn batches completed. The first wrote three usage rows (one length
  correction), totaling $0.02409240; the second wrote two rows, $0.02166150.
  SQL totals equal the cost-estimate API. Stale configuration and missing usage
  return no reference. Browser displayed $0.0217 and ~$0.2166 for ten new runs.
  Both ZIPs were downloaded and TXT/JSON/prompt-reference checks passed.
- A 45-second human-AI conversation read its asset snapshot from real RDS,
  persisted an AI message and one usage row ($0.01088250), and ended normally.
  The extra explicit tick was deduped because the send path already invoked it.
  These are stored estimates, not an AWS invoice reconciliation.
- Reproduction scripts live in the management worktree:
  `check_chatroom_asset_rds.py` defaults to read-only and requires the full DDL
  hash for apply. `run_attachment_rds_integration.py` requires
  `--confirm-shared-rds stimulusdb/postgres`, binds only port 5124 on loopback,
  and never calls `create_all`. Install `pg8000` in the local test virtualenv and
  set `RDS_CA_BUNDLE` to the AWS regional CA bundle.
  `check_attachment_rds_integration.py` provides library/batch/verify/cleanup;
  `check_attachment_rds_human.py` tests real tick writes and expiry.
- Real-RDS test artifacts are gitignored under `.local/attachment-rds-integration/`.
  Cleanup soft-deletes the two recorded test rooms; assets, usage and isolated
  DDB history are retained as evidence. Do not hard-delete the new shared table
  as part of isolated-infrastructure cleanup.
- Follow-up checks: 8 attachment-management tests, 6 cost-estimate tests,
  32 runtime attachment tests and 19 CDK tests passed. All isolated queues/DLQs
  were empty and no recent API/tick/provisioner/worker/exporter errors were found.
  Protected Pages still references `main.95eb5b04.js` / `main.f1c0d2a5.css`;
  live tick's last modification remains 2026-09-09. Temporary loopback integration
  servers/browser were stopped after verification.

### Remaining Release Work

- Shared PostgreSQL asset DDL is now applied; existing management/runtime/Pages
  publication still requires a separate coordinated release.
- Real PostgreSQL positive/stale/missing-ledger checks pass through the isolated
  dev runtime. The estimate is not exact billing and cannot account for provider failures
  without reported usage; current deployed prompt-source changes are not matched.
- Chatroom permanent deletion/history retention remains STML-28. No new hard-delete
  endpoint or background ready-file cleanup was introduced. Human-AI study-cost
  projection remains STML-27.

Raw reports, history, ZIP, IDs, credentials and browser captures are gitignored:
runtime `.local/prompt-attachments/` and management `.local/attachment-sandbox/`.
