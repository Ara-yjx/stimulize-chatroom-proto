# Chatroom Backend API

Python runtime for chatroom auth, messaging, lobby flow, heartbeat-driven AI ticks, Bedrock inference, and usage writes.

Current beta behavior:

- `/auth/token` validates the chatroom ID against RDS and requires the fixed beta client access key.
- Chatroom settings are read directly from Stimulize Postgres/RDS.
- Usage is written directly to RDS, one row per billable model invocation.
- AI replies are produced by the tick handler, not by `/chat/send`.

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install flask  # for local dev server
```

## Run locally
For local mock mode, start the mock management API first (port 5000), then:
```bash
python dev_server.py
```
Runs on `http://localhost:5001`. Local development can use mock DynamoDB/RDS/lobby env vars or shared RDS credentials, depending on the test.

### Ledger recording and balance enforcement

- Empty `STIMULIZE_API_URL`: no credit HTTP calls; RDS usage recording continues.
- Configured `STIMULIZE_API_URL` and service credentials: record inference debits,
  including at zero or negative balance. Deploy the management backend's full-cost
  debit implementation first; its older implementation clamps debits to balance.
- `CHATROOM_BALANCE_ENFORCEMENT_ENABLED=false` (default): neither human-AI ticks
  nor AI-AI workers check balance before inference. Ledger failures are logged
  without stopping the conversation.
- Set the flag to `true` to enable the existing pre-inference credit check when
  the API URL is configured. No URL still means local/free mode.

This does not backfill historical clamped debits. Before deployment, inspect the
actual RDS wallet/ledger constraints for any non-negative balance restriction;
local SQLite tests cannot establish the live database schema.

CDK uses `stimulizeApiUrl` and `chatroomBalanceEnforcement` context for tick/batch
worker only. Supply each stack's `StimulizeApiToken` NoEcho parameter privately;
never put the token in context files or Git. The production app records against
production management without enforcement. Override `-c stimulizeApiUrl=` for
disposable dev stacks that must not write the shared ledger. Do not revert the
management debit implementation to balance-clamping once negative balances exist.

## Test
```bash
pytest tests/
```

### Local AI-only conversation

Use the production prompt builder, model adapters and scheduler without DDB,
RDS, ledger writes or a deployment. From `backend/`:

```bash
PYTHONPATH=. python scripts/early_completion_conversation.py --state ../.local/demo.json show
PYTHONPATH=. python scripts/early_completion_conversation.py --state ../.local/demo.json step --invoke
PYTHONPATH=. python scripts/early_completion_conversation.py --state ../.local/demo.json run --invoke
```

`--invoke` explicitly permits Bedrock charges. A conversation has a 55-second
deadline from its first inference and a run is bounded to 20 calls. Use a new
state path for a new test. `--settings file.json` supplies initial settings;
otherwise a short two-AI example is used. `append --ai-id ai-0 --text "..."`
injects a message into a running local conversation and resets confirmations.
The private JSON file includes actual request/tools, raw output, parsed action
and usage, including rejected late results. Do not commit traces.

This is prompt-quality testing, not evidence of atomic AWS writes or UI wiring;
use workflow integration tests and an isolated cloud stack for those checks.
