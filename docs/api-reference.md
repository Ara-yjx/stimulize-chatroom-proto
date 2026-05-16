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
Start a new chat session. Lambda validates the fixed beta client access key, validates the chatroom ID against RDS, creates or joins the lobby/conversation, assigns nickname + avatar, and returns a JWT.

Request: `{ chatroom_id, access_key }`
Response: `{ token, session_id, conversation_id, nickname, avatar, chatroom_setting, lobby? }`

### POST /chat/send
Send a human message. Lambda appends the human message event and returns currently visible events. It does **not** call Bedrock; AI replies are produced later by the tick handler and fetched through `/chat/messages`.

Headers: `Authorization: Bearer <jwt>`
Request: `{ message, after? }`
Response: `{ events: [{ type, session_id, sender, role, content, timestamp, visible_at, avatar }] }`

### GET /chat/messages?after={timestamp}
Poll for visible events since a timestamp (epoch ms). Pending AI messages with `visible_at > now` and internal `tick` events are filtered out. Admin/debug mode can include tick events when called with the separate admin token.

Headers: `Authorization: Bearer <jwt>`
Response: `{ events: [{ type, session_id, sender, role, content, timestamp, visible_at, avatar }], conversation_status, lobby? }`

---

## Management API Implementation Mapping

The design docs and OpenAPI spec describe a REST-style management API. The current `Stimulize-backend` implementation keeps the project's existing POST/action route style instead.

Mapping:

- `POST /chatrooms` -> `POST /api/createChatroom`
- `GET /chatrooms` -> `POST /api/getChatrooms`
- `GET /chatrooms/:id` -> `POST /api/getChatroom/<id>`
- `PUT /chatrooms/:id` -> `POST /api/updateChatroom/<id>`
- `DELETE /chatrooms/:id` -> `POST /api/deleteChatroom/<id>`

The actual backend contract is documented in:

- [CHATROOM_MANAGEMENT_API.md](../../Stimulize-backend/CHATROOM_MANAGEMENT_API.md)

Usage endpoints are deferred in the current backend implementation.

---

## Management API — Chatroom CRUD (Editor UI → Backend)

### POST /chatrooms
Create a new chatroom. Generates `scid_` + UUIDv4 as the chatroom ID.

Request: `{ name, setting: { mode, mimic_human, system_prompt, model_id, simulate_pairing_seconds, timer_min_minutes, timer_max_minutes, max_duration_seconds, target_human_count?, ai_join_strategy?, ai_strategy_value?, max_wait_seconds? } }`
Response: `{ id, name, status, setting, created_at, updated_at }`

### GET /chatrooms
List chatrooms for the current user.

Response: `[{ id, name, status, created_at, updated_at }]` (setting omitted in list view)

### GET /chatrooms/:id
Get a single chatroom with full setting.

Response: `{ id, name, status, setting, created_at, updated_at }`

### PUT /chatrooms/:id
Update chatroom name, status, or setting.

Request: `{ name?, status?, setting? }` (`setting`, when provided, replaces the full setting object)
Response: updated chatroom object

### DELETE /chatrooms/:id
Deactivate a chatroom (sets status to `inactive`).

Response: standard backend success envelope; chatroom is soft-deleted by setting `status=inactive`

---

## Management API — Usage

Usage endpoints are deferred in the current `Stimulize-backend` implementation.
