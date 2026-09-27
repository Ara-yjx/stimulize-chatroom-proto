# Incident Fix Release Worklog

## Scope and Status

Improve attachment upload rejection diagnostics; fix human-AI output parsing
and candidate starvation; fix AI-only response truncation handling. Shared
inference supports explicit JSON/native-tool protocols. Customer settings and
data are not rewritten. Production management EC2 and Stripe are out of scope.

## Verified Locally

- Backend: 437 passed, 1 skipped. CDK: 20 passed.
- Protocol trials and private original-prompt replays are summarized in
  [Bedrock Speech Protocols](./bedrock-speech-protocol-design.md).
- Original human-AI prompt replay: 9/9 valid speech responses.
- Original batch settings/persona replay: 2/2 valid Opus responses.
- These direct calls do not substitute for hosted browser/clone acceptance.

## Runtime Deployment

Deployed `TickHandlerStack`, `ChatroomApiStack`, and `AiConversationBatchStack`
from the reviewed local assembly, using `--exclusively`. All three reached
`UPDATE_COMPLETE`. Diff contained only code/asset metadata changes; dependency
stacks, tables, IAM, environments and heartbeat were not deployed.

Source asset: `defba2e87e0cf42e2557ed8c35285d972281cb8dec710a39998fb6dba3781e1d`.
Lambda ZIP SHA-256 (base64): `M9FXvCFsd+1dKtlBMkgxgqIIaox1Md3xfj0x/ev15wU=`.
Before deployment: zero active human conversations and zero nonterminal batches.
Runtime auth OPTIONS returns 204 after deployment. Tick/batch retain free mode
(`STIMULIZE_API_URL` absent/empty).

## Acceptance Summary

Scoped implementation, local tests, deployment and integration acceptance are
complete. Raw research content, credentials, tokens, conversation IDs and private
probe files stay out of this report. PDF historical root cause and model quality
limitations below remain explicitly unresolved, rather than being called fixed.

## Management and Frontend Release

- Beta EC2 is on `45f3e88` (branch `release/yejiaxi/incident-fix-20260928`),
  transferred through a verified Git bundle. Both relevant unittest modules
  pass on the host (26 tests); systemd is active and public upload OPTIONS is
  200. Preserved `.env`, its backups, venv and `gunicorn_config.py`. No DB
  migration, production EC2 or Stripe change. Source fix branch is pushed.
- Widget-only gh-pages publication succeeded; hosted bundle SHA-256 matches
  the local 113,623-byte artifact. No deprecated editor files were published.
- Editor `edd5fae` built and published to beta gh-pages; source feature branch
  is pushed. Build has existing lint warnings. Pages run `36336758776` succeeded
  and hosted `git-commit.txt` now returns `edd5faeabc9ea08caab7f56dfb35069d8b78fd4b`.
  Propagation and browser acceptance are verified below.

## Deployed Human-AI Clone

Cloned the original incident's live settings through the test user's management
API, preserving models/personas/pairing and limiting each conversation to 45
seconds. Two independent runs ended normally, with 5 and 4 AI messages and 3
recorded usage rows each. Gemma and Llama both spoke, and candidate evaluation
rotated across AI identities. The clone was soft-deleted afterward. This is
real deployed API/runtime/storage verification, not yet the browser preview
acceptance. Hosted browser login, list and UI creation also succeeded; temporary
UI-created rooms were soft-deleted.

Hosted browser acceptance now also covers login, UI creation, opening the
editor, switching mimic-human off, Save/Activate/Launch Preview, endpoint
confirmation, human send, two visible Sonnet AI bubbles, and natural end with
disabled input and one end notice. The test intercepted only its own save
request to set a 45-second duration and null timer constraints; all API responses
and inference were real. The test room was soft-deleted. Local screenshot:
`.local/incident-release-20260928/hosted-created-editor.png`. One earlier harness
attempt used unsupported fractional timer minutes and was correctly rejected;
another omitted the preview Start Chat step. Neither was a product failure.

## Deployed Batch Clone and PDF Regression

- Original Opus batch settings/personas cloned, limiting to two messages and
  one conversation. Only the newly created test batch's deadline was shortened
  to 55 seconds; service defaults were unchanged. Result: completed, 1/1
  conversation, 2 AI messages, 2 usage records, 4,496 input / 1,427 output tokens,
  estimated USD 0.058155. Snapshot numeric strings were restored to API numeric
  types before creation; prompts/models/personas and null message guidance stayed.
- Download API progressed to ready. Downloaded ZIP contains `info/prompt.md`,
  matching `conversations/0001.json` and `.txt`, and `manifest.json`. Verified
  both messages appear in TXT, JSON timestamps are integers, participants omit
  avatars, and Markdown prompt uses code fences. Local private artifacts:
  `/tmp/incident-fixed-batch-20260928/`. API confirms clone is inactive. The
  cleanup harness initially expected a data field in the successful delete
  response; subsequent explicit get confirmed deletion had already succeeded.
- Exact supplied PDF uploads with HTTP 200 and produces content-specific Sonnet
  replies (first observed about 18 seconds). Test duration 55 seconds; metadata
  subsequently confirmed ended and room was soft-deleted. Three synthetic files
  return distinct HTTP 400 errors: 11 pages, encryption, and unreadable structure.
  This verifies improved diagnostics, not a claimed historical root cause for
  the customer's original upload rejection, which remains unreproduced.

## Additional Coverage and Final Audit

- Hosted UI Llama 4 Scout, mimic-human enabled: selected through the model
  dropdown, saved, preview launched, human sent, AI replied, conversation ended
  normally at the 45-second limit; room soft-deleted. Screenshot:
  `.local/incident-release-20260928/hosted-scout-editor.png`. It repeated the same
  sentence while the human stayed silent. This proves protocol operation, not
  good conversational quality; repetition remains an observed model limitation
  requiring separate investigation before attributing it to a particular cause.
- Additional Sonnet 4.6 batch with null message-length guidance: completed,
  two messages, 549 and 690 output tokens, 2,493 and 3,145 characters. Estimated
  cost USD 0.03460425. This tiny sample is not a universal default length.
  Downloaded artifacts: `/tmp/incident-sonnet-null-20260928/`; room soft-deleted.
- Browser diagnostic isolation rechecked using the deployed widget bundle and
  deterministic intercepted API responses: ordinary participant sees no error
  bubble or debug request; preview coalesces errors; neither history nor actual
  Qualtrics ED fixture writes contain technical errors. This is a browser
  contract test, not an induced live-provider failure. API filtering is covered
  by backend tests. Widget's four diagnostic tests and TypeScript check pass.
- Final scan: no active human conversations or nonterminal batches. All three
  deployment stacks UPDATE_COMPLETE, main heartbeat ENABLED. Provision/export
  queues and all three batch DLQs show zero visible/in-flight messages.
- Available CloudWatch datapoints after deployment show zero Lambda Errors for
  API, tick, provisioner, worker and exporter. Fully paginated tick/worker logs
  contain no `Bedrock output:` error records at the audit point. Metrics are
  eventually consistent; this is a bounded release check, not a permanent claim.
- No customer room was edited, no production EC2/Stripe changes or schema
  migration. Test assets/history remain under normal retention; no hard delete.

Model instability remains visible in the editor. Llama 3.1 did not meet the
repeated 90% probe gate, despite successful original-room regressions. Later
ticks may recover, but correlated failures and paid failed inference remain
possible. Only Nova Premier was removed from the selectable model list.
