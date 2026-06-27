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

## Test
```bash
pytest tests/
```
