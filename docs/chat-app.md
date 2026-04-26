# Chat App

## Purpose

The chat app is the stable core of the repository. It owns members, conversations, memberships, messages, and realtime updates. Everything else in the repo should use this layer instead of mutating storage directly.

## Main entry points

- `main.py`: creates the FastAPI app, includes REST and websocket routers, and initializes the database on startup.
- `api/routes.py`: HTTP request and response surface.
- `api/websockets.py`: websocket endpoints for conversation lists and conversation message streams.
- `services/message_service.py`: application rules and persistence behavior.
- `db/session.py`: sqlite connection management, schema creation, and lightweight migration logic.
- `models/chat.py`: dataclass domain entities.

## Core domain objects

- `Member`: an identity that can read conversations, post messages, create chats, manage groups, or be blocked from those actions by capability policy.
- `Conversation`: either `direct` or `group`.
- `Membership`: the relationship between a member and a conversation, with `status` and `role`.
- `Message`: immutable message data with optional soft-delete marker.

## Request flow

For most actions, the request flow is:

1. FastAPI route validates request shape in `api/routes.py`.
2. The route calls a function in `services/message_service.py`.
3. The service checks conversation state, membership state, and member capability policy.
4. The service writes to sqlite through `db/session.py` connections.
5. The route broadcasts websocket events when the change should be visible live.

The service layer is the source of truth for behavior. Routes should stay thin.

## Capability model

Capability policy lives in `services/message_service.py`.

Base capabilities currently include:

- `read_conversations`
- `send_messages`
- `create_direct_conversations`
- `create_group_conversations`
- `leave_conversations`
- `manage_memberships`
- `pause_group_messages`

Capabilities are resolved from:

1. member type defaults such as `user_regular`, `user_premium`, and `admin`
2. optional per-member overrides stored in `members.capabilities`

This lets the simulation layer create members with different permissions without changing the chat core.

## Important route groups

### Member management

- `POST /api/members`
- `GET /api/members`
- `GET /api/members/{member_id}/access`

Use these when you need to create members and inspect what a given member can currently see and do.

### Member-scoped actions

- `GET /api/members/{member_id}/conversations`
- `GET /api/members/{member_id}/conversations/{conversation_id}/messages`
- `POST /api/members/{member_id}/messages`
- `POST /api/members/{member_id}/conversations/group`
- `POST /api/members/{member_id}/conversations/{conversation_id}/leave`

These routes are important for simulations because they model actions from the member's point of view instead of from an omniscient admin shell.

### Conversation and membership management

- `POST /api/conversations`
- `POST /api/conversations/group`
- `GET /api/conversations`
- `GET /api/conversations/{conversation_id}/members`
- `POST /api/conversations/{conversation_id}/members`
- `DELETE /api/conversations/{conversation_id}/members/{member_id}`
- `POST /api/conversations/{conversation_id}/pause-messages`
- `POST /api/conversations/{conversation_id}/resume-messages`

These routes are used when the admin or a privileged member orchestrates groups directly.

## Realtime model

Websockets are used for observability and client updates.

- `ws://localhost:8000/ws/conversations`: conversation list channel
- `ws://localhost:8000/ws/conversations/{conversation_id}`: conversation-specific channel

Current event families include:

- `conversation.created`
- `conversation.updated`
- `conversation.deleted`
- `membership.added`
- `membership.removed`
- `membership.left`
- `message.created`
- `message.deleted`

## Environment and storage

- `.env` is loaded through `app_env.py`.
- sqlite is the active persistence layer.
- runtime defaults use shared in-memory sqlite unless `DATABASE_URL` overrides it.

## How to use the chat app directly

Start the server:

```bash
.venv/bin/uvicorn main:app --reload
```

Create a member:

```bash
curl -X POST http://127.0.0.1:8000/api/members \
  -H "Content-Type: application/json" \
  -d '{"display_name":"alice","type":"human"}'
```

Create a group conversation as a member:

```bash
curl -X POST http://127.0.0.1:8000/api/members/<member_id>/conversations/group \
  -H "Content-Type: application/json" \
  -d '{"title":"group-1","member_ids":["<other_member_id>"]}'
```