# Mock Management API

Local/dev mock for the chatroom management API.

Status: not the current deployed beta source of truth. Beta management now uses
the real `Stimulize-backend` API with shared Postgres. Keep this package for
isolated local tests and preview flows.

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run
```bash
flask --app app run --port 5000
```

## Test
```bash
pytest tests/
```

## Endpoints

Editor-compatible routes:

- `POST /api/getChatrooms`
- `POST /api/createChatroom`
- `POST /api/getChatroom/<id>`
- `POST /api/updateChatroom/<id>`
- `POST /api/deleteChatroom/<id>`
- `POST /api/getChatroomUsage/<id>`
- `POST /api/getUserUsage`

The runtime should not write usage through this mock in current design; usage
writes belong next to provider invocation and go directly to RDS in beta/prod.
