# Mock Management API

Flask mock for chatroom key validation and usage tracking. No chatroom config storage — config comes from the client.

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

Internal (called by Lambda):
- `GET /internal/keys/{chatroom_key}` — validate key
- `POST /internal/usage` — report token usage

Admin (called by Editor UI):
- `POST /keys` — create key
- `GET /keys` — list keys
- `PUT /keys/{key_id}` — update restrictions
- `DELETE /keys/{key_id}` — revoke key

Seed key for testing: `stimulize_ckv1_0000000000000001`
