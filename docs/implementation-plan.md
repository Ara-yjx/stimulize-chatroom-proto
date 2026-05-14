# Stimulize Chatroom — Implementation Plan (Phase 1)

## 1. Mock Management API (`mock_management/`)

### 1.1 Project setup
- Flask app with `requirements.txt` (flask)
- `app.py` entry point, `chatroom_config.py`, `routes.py`

### 1.2 Static chatroom config (`chatroom_config.py`)
- Hardcoded chatroom definitions: id, name, ai_participants (each with nickname, system_prompt, model_id), created_at, updated_at
- Hardcoded keys: chatroom_key → chatroom_id mapping, is_active flag
- In-memory usage accumulator: chatroom_id → { input_tokens, output_tokens }

### 1.3 Internal endpoints (called by Lambda)
- `GET /internal/keys/{chatroom_key}` — validate key, return chatroom_id
- `GET /internal/chatrooms/{chatroom_id}` — return chatroom config + AI participants
- `POST /internal/usage` — accumulate token usage

---

## 2. Chatroom Backend (`backend/`)

### 2.1 Project setup
- Python package with `requirements.txt` (boto3, pyjwt, requests)
- `chatroom_api/` module with `config.py` for env vars

### 2.2 DynamoDB layer (`dynamo.py`)
- `get_messages(conversation_id, after=0)` — read messages, optionally filtered by timestamp. Serves both full conversation fetch and polling.
- `append_messages(conversation_id, chatroom_id, messages)` — append to messages list, update `updated_at`

### 2.3 Mock DynamoDB (`mock_dynamo.py`)
- In-memory implementation of the same interface as `dynamo.py`.
- Toggled via env var `USE_MOCK_DYNAMO=true`.
- Allows running/testing locally without a real DynamoDB table.

### 2.4 JWT utilities (`jwt_utils.py`)
- `create_token(session_id, conversation_id, chatroom_id, nickname)` — sign JWT (HS256, 1h TTL)
- `verify_token(token)` — decode and validate, return claims

### 2.5 Auth handler (`auth.py`)
- `POST /auth/token` handler
- Validate chatroom_key against management API
- Fetch chatroom config
- Generate session_id, assign conversation_id
- Return signed JWT

### 2.6 Bedrock client (`bedrock_client.py`)
- `invoke(model_id, system_prompt, messages)` — call Bedrock Converse API
- Return response text + token usage counts

### 2.7 Conversation mapping (`conversation.py`)
- `build_bedrock_messages(room_messages, ai_participant_id)` — convert multi-participant history to Bedrock user/assistant format
- Merge consecutive same-role messages
- Prefix non-self messages with `[nickname]`

### 2.8 Chat handler (`chat.py`)
- `POST /chat/send` — verify JWT, read conversation, call Bedrock per AI participant, append messages, report usage
- `GET /chat/messages` — verify JWT, return messages after given timestamp

### 2.9 Lambda entry point (`handler.py`)
- Route API Gateway events to auth/chat handlers
- Error handling wrapper (401, 500, etc.)

---

## 3. Frontend (`frontend/`)

### 3.1 Project setup
- `package.json` with TypeScript + esbuild
- `src/chatroom.ts` entry point
- `tsconfig.json`

### 3.2 Auth module
- `exchangeToken(apiBaseUrl, chatroomKey, nickname)` — call `/auth/token`, store JWT in memory

### 3.3 Chat UI rendering
- `renderChatroom(element)` — inject HTML structure (message list + input + send button) via jQuery
- CSS styles inline or bundled

### 3.4 Message sending
- On send button click or Enter: POST `/chat/send`, append user bubble immediately, append AI reply bubble(s) on response

### 3.5 Message polling
- `pollMessages(afterTimestamp)` — GET `/chat/messages?after={ts}` every 2-3 seconds
- Append new messages from other participants to the UI
- Deduplicate messages already shown

### 3.6 Widget init API
- `StimulizeChatroom.init({ element, chatroomKey, nickname, apiBaseUrl })` — public entry point
- Handles auth → render → start polling

### 3.7 Build & bundle
- esbuild to `dist/chatroom.min.js` for CDN distribution

---

## 4. CDK Infrastructure (`cdk/`)

### 4.1 Project setup
- Python CDK app with `requirements.txt` (aws-cdk-lib, constructs)
- `app.py`, `cdk.json`

### 4.2 ConversationTableStack
- DynamoDB table: `chatroom-conversations`, PK `conversation_id`, PAY_PER_REQUEST, TTL on `ttl`

### 4.3 ChatroomApiStack
- Lambda function for chatroom API (Python 3.12, 256MB, 30s timeout)
- API Gateway with routes: `POST /auth/token`, `POST /chat/send`, `GET /chat/messages`
- IAM: DynamoDB CRUD + Bedrock InvokeModel
- Env vars: `DYNAMODB_TABLE`, `JWT_SECRET`, `MANAGEMENT_API_URL`

### 4.4 MockManagementStack
- Lambda function for Flask mock management API
- Function URL (no API Gateway needed for internal use)

---

## Implementation Order

1. `mock_management/` — needed by backend for key validation and chatroom config
2. `backend/` — depends on mock management being available
3. `frontend/` — depends on backend APIs being available
4. `cdk/` — can be built in parallel with backend, deploy after all code is ready

Within backend, build bottom-up: mock_dynamo → dynamo → jwt_utils → bedrock_client → conversation → auth → chat → handler.
