# Beta Chatroom Release: 2026-09-20

## Released

- Management: `mhan00/Stimulize-backend` PR #8 merged into `main`; beta EC2 runs
  `ca06de6`. PR #9 adds deployment prerequisites (documentation only).
- Editor: `Ara-yjx/amp-generator-beta` PR #4 and #5 merged into `master`;
  `stimulize-beta` Pages serves `c237f10`. PR #6 only corrects a browser-test selector.
- Runtime: source `b6d4d6e`; production API/tick deployed with real RDS and no
  `STIMULIZE_API_URL` credit gate/debit. Heartbeat code and environment unchanged.
- Widget: rebuilt artifact is byte-identical to current proto Pages; no republish.
- Production management EC2, its IAM role, and Stripe configuration were untouched.

## Production Resources

- `ChatroomAssetsStack`: retained private attachment bucket.
- `BatchConversationTableStack`: `chatroom-batch-conversations`.
- `BatchConversationEventStack`: `chatroom-batch-events`, cleanup Lambda/DLQ.
- `AiConversationBatchStack`: `stimulize-chatroom-batch-batches`, provisioner,
  worker, exporter, Step Functions workflow/recovery, queues and alarms.
- Beta management points to these resources, not disposable dev stacks. Its
  dedicated role has a scoped production-resource policy; obsolete dev-batch
  inline permissions were removed. No dev resources were deleted.
- Existing shared RDS tables were reused; no schema migration during release.

## Operational Fixes

- Beta's old Python 3.9 could not install Pillow 12. The first update briefly
  failed gunicorn reload; old code/configuration was restored before proceeding.
  A separate Python 3.11 venv passed all 56 management tests and was selected via
  a systemd drop-in. Old venv, `.env` backup, and `gunicorn_config.py` were retained.
- Binary uploads exposed missing API Gateway `multipart/form-data` binary
  handling. Enabled it only on beta API `9wr63is7x6/live`; JSON routes unchanged.
- Added nginx's 5 MiB allowance only to `/api/uploadPromptAsset`; Flask retains
  its tighter 5,000,000-byte request and per-format limits.
- Hiding billing alone still imported Stripe. PR #5 lazy-loads billing; hosted
  verification confirms no Stripe requests on the beta participant path.
- Cleared a stale local `gh-pages` cache after a non-fast-forward rejection;
  publication used a normal push, never a force push.

## Regression Evidence

- Unit checks: runtime 351 passed / 1 skipped; management 56 passed (also on beta);
  editor 85 passed; CDK tests and TypeScript checks passed. Production build passed
  with existing CRA/lint warnings.
- Real human-AI API scenarios: ordinary 1+1, mimic empty-room 20-second gate,
  simulated pairing, 2-human/2-AI group, missing-human replacement, TXT attachment,
  and resumable episode expiry/resume/history. Tests use 15-45-second durations.
  Resume's first 25-second observation window was too short; bounded retry passed.
- Real AI-AI scenarios: 2 AI, 3 AI, two-conversation batch, attachments, and
  two named personas with Sonnet/Haiku overrides. RDS records both models.
  Conversations capped at two messages; malformed persona batch becomes
  `validation_failed` before any conversation is created.
- Hosted browser: real login/create/save; human preview send/AI reply/history;
  attachment upload and automatic selection; test-once without navigation;
  batch history/detail/download. Separate two-preview group test verifies both
  humans share the same conversation and receive AI replies.
- Downloaded ZIPs were opened locally: timestamp-free TXT, numeric-ms JSON without
  AI avatars, and fenced Markdown `info/prompt.md` were checked.
- PNG/JPEG/PDF uploads, upload idempotency, invalid-file rejection, 50/1000/500000
  upper limits, unauthenticated rejection, and three-origin CORS passed.
- Usage aggregation succeeds for hour/day/week/month through the public API.
- Final health: no active test conversations or nonterminal batches; queues/DLQs
  empty, all eight inspected alarms OK, no runtime/batch Lambda error events in
  the inspected 15-minute window.

Raw IDs, credentials, histories, screenshots and downloads remain under ignored
`.local/production-feature-release/`. Test chatrooms are soft-deleted; test history,
usage and assets remain under the normal retention policy, not hard-deleted.
This covers the release matrix above, not every possible model/file combination.

## Rollback Boundary

Use recorded source revisions and configuration backups; preserve customer data.
Beta Python/environment/code must be restored together. API Gateway's prior
deployment and Lambda configuration snapshots are retained privately. Do not
roll back by pointing customer traffic at disposable dev tables or buckets.
