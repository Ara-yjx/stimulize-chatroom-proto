# Beta Feature Release

Released on 2026-09-20. See [release worklog](beta-feature-release-worklog-20260920.md)
for deployed revisions, operational fixes, and verified regression coverage.

## Environment Boundaries

- All development resources are disposable; beta management EC2 is the exception.
- No customer path may depend on a dev Lambda, table, bucket, queue, or Pages repo.
- `stimulize-beta` uses beta EC2 for login, general API, and chatroom management:
  `https://9wr63is7x6.execute-api.us-east-2.amazonaws.com/live`.
- Human-AI runtime is the existing production API/tick. Batch and attachment
  storage are new production resources with retention/deletion safeguards.
- Do not deploy or modify production EC2, its role, secrets, or Stripe settings.
  Beta EC2 does not connect to Stripe. Shared RDS still contains real data.
- Dev attachments/batches are test data, not automatically migrated. Validate
  release with new rooms and uploads; do not silently route missing files to dev.
- Do not delete dev resources during release; inventory references first.

## Order and Gates

1. Commit feature-scoped changes; integrate current main/master in clean worktrees.
2. Deploy production batch/attachment infrastructure first; inspect CDK diffs.
3. Deploy production API/tick with existing billing configuration preserved.
   Check active conversations before rollout; verify the 20-second opening gate.
4. Upgrade beta EC2 and its beta-only IAM permissions. Preserve local
   `gunicorn_config.py`, back up configuration, and point services at production
   batch/attachment resources. No RDS schema recreation or destructive migration.
5. Deploy editor using explicit beta endpoints and the stable widget URL.
6. Hosted E2E: login, existing human-AI, attachment upload, test once/small batch,
   validation failure, usage, and actual TXT/JSON/Markdown ZIP download.
7. Check workflow failures, queues/DLQs, API errors, and clean up test rooms.

Rollback disables new entry points or restores reviewed artifacts; never delete
customer assets/history. Do not switch worker prompt versions during active batches.

## Preflight: 2026-09-20

Read-only inspection of beta EC2 (`i-0b7f231da9ebc13e2`): running, nginx/gunicorn
active; branch `dev/yejiaxi/ai-ai-chat`, commit `b422f9a`. No tracked drift;
untracked `.env` backup and `gunicorn_config.py` must be preserved.
Dedicated `stimulize-beta-management-profile` is attached. Batch is enabled but
still references `stimulize-chatroom-ai-batch-dev-yjx12`; attachments are not
configured. No nonempty Stripe environment keys were found in `.env`.
This is an audit snapshot, not confirmation of release completion.

## Final Source Cleanup

At the pre-deployment review checkpoint, release paused before runtime, EC2, or Pages deployment.
Runtime changes through `b15fc12` already reached `origin/main`; management and
editor changes remain on their release branches (management targets `main`,
editor targets `master`). Follow-up cleanup does not rewrite published history.

Those release branches have since been merged. The production `cdk.json` now
keeps `enableAiBatch` and `enablePromptAttachments` enabled, matching the release;
ordinary synthesis/deployment must not remove these resources or disable attachments.

- Remove the unused matching-reference cost-estimate API; retain actual batch usage.
- Remove the S3 mock-room fixture loader/publisher and its Lambda permission.
  Cloud human-AI integration uses the real RDS adapter; local/unit mocks remain.
- Remove the completed FIFO-to-Step-Functions migration's legacy-queue toggle.
  This source cleanup does not delete retained AWS queues or other dev resources.
- Update integration checks for the single download API, Markdown prompt export,
  timestamp-free TXT, and create-without-navigation editor behavior.
- Use the stable widget URL by default, not the temporary resume Pages repo.
- Keep numeric history compatibility, immutable historical export support,
  reusable isolated test stacks, and test mocks. These still have consumers.
- Disable billing navigation/routes only in the beta build. Other builds retain
  existing billing behavior; this is not a server-side authorization mechanism.
