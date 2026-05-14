# Chatroom Backend API

Python Lambda for chatroom auth, messaging, and Bedrock LLM integration. Chatroom config comes from the client at `/auth/token` time, not from the management API.

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install flask  # for local dev server
```

## Run locally
Start the mock management API first (port 5000), then:
```bash
python dev_server.py
```
Runs on `http://localhost:5001` with mock DynamoDB enabled.

## Test
```bash
pytest tests/
```
