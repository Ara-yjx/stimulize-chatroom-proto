# Stimulize Chatroom — API Reference

OpenAPI specs:
- Chatroom API (widget → Lambda): [api-chatroom.yml](./api-chatroom.yml)
- Management API (editor UI → backend): [api-management.yml](./api-management.yml)

## Overview

Two API surfaces:

1. **Chatroom API** (`chatroom.stimulize.org`) — called by the chat widget at runtime
2. **Management API** (`stimulize.org` or mock Flask) — called by the editor UI and internally by the chatroom Lambda

---

## Chatroom API (Widget → Lambda)

### POST /auth/token
Start a new chat session. Lambda validates the chatroom ID against RDS, creates a conversation in DynamoDB, assigns nickname + avatar, returns a JWT.

Request: `{ chatroom_id }`
Response: `{ token, session_id, conversation_id, nickname, avatar, chatroom_setting }`

### POST /chat/send
Send a message. Lambda reads conversation from DynamoDB, calls Bedrock, appends events, reports usage to RDS.

Headers: `Authorization: Bearer <jwt>`
Request: `{ message }`
Response: `{ replies: [{ nickname, avatar, content }], error: bool }`

### GET /chat/messages?after={timestamp}
Poll for new events since a timestamp (epoch ms).

Headers: `Authorization: Bearer <jwt>`
Response: `{ events: [{ type, session_id, sender, role, content, timestamp, avatar }] }`

---

## Management API — Chatroom CRUD (Editor UI → Backend)

### POST /chatrooms
Create a new chatroom. Generates `scid_` + UUIDv4 as the chatroom ID.

Request: `{ name, setting: { mode, mimic_human, system_prompt, model_id, simulate_pairing_seconds, timer_min_minutes, timer_max_minutes } }`
Response: `{ id, name, status, setting, created_at, updated_at }`

### GET /chatrooms
List chatrooms for the current user.

Response: `[{ id, name, status, created_at, updated_at }]` (setting omitted in list view)

### GET /chatrooms/:id
Get a single chatroom with full setting.

Response: `{ id, name, status, setting, created_at, updated_at }`

### PUT /chatrooms/:id
Update chatroom name, status, or setting.

Request: `{ name?, status?, setting? }` (partial update)
Response: updated chatroom object

### DELETE /chatrooms/:id
Deactivate a chatroom (sets status to `inactive`).

Response: `{ status: "deactivated" }`

---

## Management API — Usage (Editor UI → Backend)

### GET /chatrooms/:id/usage
Get total token usage for a chatroom.

Query params: `from` (ISO date, optional), `to` (ISO date, optional)

Response: `{ chatroom_id, input_tokens, output_tokens, total_tokens }`

---

## Note on Internal Endpoints

Lambda reads chatroom settings and writes usage directly to RDS — no internal API endpoints needed for Phase 1. If Lambda moves to SQS-based usage reporting or stops reading RDS directly in the future, internal endpoints will be added to the management API.
