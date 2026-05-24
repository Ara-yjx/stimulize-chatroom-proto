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
Poll for visible events since a timestamp (epoch ms). Pending AI messages with `visible_at > now` and internal `tick` / `lobby_created` events are filtered out. Admin/debug mode can include audit events when called with the separate admin token.

Headers: `Authorization: Bearer <jwt>`
Response:
```json
{
  "events": [{ "type", "session_id", "sender", "role", "content", "timestamp", "visible_at", "avatar" }],
  "lobby": { "status", "actual_human_count", "target_human_count", "deadline_at" },  // only during lobby phase
  "conversation_status": "active" | "ended"
}
```

Status codes:
- `200` — success
- `401` — JWT expired or invalid
- `410 Gone` — lobby was aborted (no humans remained at deadline)

---

## Management API Implementation

The current `Stimulize-backend` implementation only exposes the project's POST/action route style.

Routes:

- `POST /api/createChatroom`
- `POST /api/getChatrooms`
- `POST /api/getChatroom/<id>`
- `POST /api/updateChatroom/<id>`
- `POST /api/deleteChatroom/<id>`

The actual backend contract is documented in:

- [CHATROOM_MANAGEMENT_API.md](../../Stimulize-backend/CHATROOM_MANAGEMENT_API.md)

Usage endpoints are deferred in the current backend implementation.

---

## Management API — Chatroom CRUD (Editor UI → Backend)

### POST /api/createChatroom
Create a new chatroom. Generates `scid_` + UUIDv4 as the chatroom ID.
The create request is the first persisted save, so any editor-provided default `setting` values are stored immediately at creation time.

Request: `{ name, setting: { mode, topic_instruction, additional_prompt?, ai_personas?, model_id, simulate_pairing_seconds, timer_min_minutes, timer_max_minutes, max_duration_seconds, target_human_count?, ai_join_strategy?, ai_strategy_value?, max_wait_seconds? } }`
Response: `{ id, name, status, setting, created_at, updated_at }`

### POST /api/getChatrooms
List chatrooms for the current user.

Response: `[{ id, name, status, created_at, updated_at }]` (setting omitted in list view)

### POST /api/getChatroom/:id
Get a single chatroom with full setting.

Response: `{ id, name, status, setting, created_at, updated_at }`

### POST /api/updateChatroom/:id
Update chatroom name, status, or setting.

Request: `{ name?, status?, setting? }` (`setting`, when provided, replaces the full setting object)
Response: updated chatroom object

### POST /api/deleteChatroom/:id
Deactivate a chatroom (sets status to `inactive`).

Response: standard backend success envelope; chatroom is soft-deleted by setting `status=inactive`

---

## Management API — Usage

### POST /api/getChatroomUsage/:id
Get total token usage for a chatroom.

Query params: `from` (ISO date, optional), `to` (ISO date, optional)

Response: `{ chatroom_id, input_tokens, output_tokens, total_tokens }`

Note: usage endpoints are deferred in the current `Stimulize-backend` implementation; this is the spec target.

---

## Widget JavaScript API

The chat widget is distributed as a single bundled script (`chatroom.min.js`). It exposes a global `StimulizeChatroom` namespace.

### Loading

```html
<script src="https://cdn.stimulize.org/chatroom.min.js"></script>
```

In Qualtrics (jQuery available):
```javascript
jQuery.getScript("https://cdn.stimulize.org/chatroom.min.js", function() {
  // widget ready
});
```

### `StimulizeChatroom.init(options)`

Mount the widget and start a chat session. Must only be called once per page (single-instance).

```typescript
interface InitOptions {
  element: string | HTMLElement;  // CSS selector or DOM element to mount into
  chatroomId: string;             // chatroom ID (e.g. "scid_550e8400-...")
  apiBaseUrl?: string;            // override backend URL
  beta?: boolean;                 // beta mode flag
}
```

**Returns:** `Promise<void>` — resolves when the chatroom UI is rendered and polling has started.

**Example (production):**
```javascript
StimulizeChatroom.init({
  element: "#chatroom-container",
  chatroomId: "scid_550e8400-e29b-41d4-a716-446655440000"
});
```

**Example (beta):**
```javascript
StimulizeChatroom.init({
  element: "#chatroom-container",
  chatroomId: "scid_550e8400-e29b-41d4-a716-446655440000",
  beta: true,
  apiBaseUrl: "https://pmvb4orly5.execute-api.us-east-2.amazonaws.com/prod"
});
```

### `StimulizeChatroom.getHistory()`

Returns the full conversation history as a structured array.

```typescript
interface ChatMessage {
  sender: string;
  content: string;
  role: "user" | "ai" | "system";
  timestamp: number;       // epoch ms
  session_id?: string;
  avatar?: { emojiText: string };
}
```

**Returns:** `ChatMessage[]`

### `StimulizeChatroom.getHistoryText()`

Returns the conversation history as plain text, one line per message. Format:
```
[SYS] Participant1234 (you) joined the chatroom
[SYS] Participant5678 joined the chatroom
[Participant1234] hello
[Participant5678] [AI] hey there!
```

**Returns:** `string`

### Qualtrics Embedded Data

When running inside Qualtrics, the widget automatically writes the full history text to `Qualtrics.SurveyEngine.setEmbeddedData("chatroom_history", ...)` on every message event. No additional configuration needed — the widget detects the Qualtrics environment automatically.

### Lifecycle

1. **Token exchange** — calls `/auth/token` with `chatroomId`
3. **Pairing screen** — animated "Finding a chat partner..." for `simulate_pairing_seconds` (configurable per chatroom)
4. **Active chat** — message input, polling every 3s, AI messages appear with simulated typing delay
5. **Conversation ended** — input disabled, "This conversation has ended." system message
5. **Lobby aborted** (group mode, 410) — "No one else joined this chatroom." + "Reconnect" button

### Error states

- **Failed to connect**: chatroom ID invalid or backend unreachable. Shows inline error.
- **Session expired**: JWT expired (3h TTL). Shows error bubble.
- **Reconnecting…**: polling fails continuously for 30s. Non-blocking banner; auto-recovers.

---

## Note on Internal Endpoints

Lambda reads chatroom settings directly via the management API (`MGMT_API_URL` + bearer) in beta. Usage writes are no-ops in beta — per-tick token counts are persisted on conversation tick events. If Lambda needs central usage aggregation in the future, an internal `POST /internal/usage` endpoint will be added to the management API.
