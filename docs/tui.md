# TUI

## Purpose

The Textual TUI is the admin and observer client for the chat app. It is not the source of truth for chat rules. It consumes the API and websocket streams the same way any other client should.

## Main files

- `tui/app.py`: top-level Textual app.
- `tui/services/api_client.py`: REST client used by the TUI.
- `tui/services/websocket_client.py`: websocket clients for one conversation channel and the global conversation-list channel.
- `tui/state/store.py`: local state container for agents, conversations, and messages.
- `tui/components/conversation_list.py`: conversation table widget.
- `tui/components/message_panel.py`: message rendering widget.

## Runtime flow

When the TUI starts:

1. it loads agents and conversations through the API client
2. it renders the conversation list
3. it subscribes to the conversation-list websocket
4. when a conversation is selected, it loads messages and opens that conversation websocket

## State ownership

The TUI keeps a local `AppStore` with:

- known agents
- current conversations
- messages per conversation
- selected conversation id

The store is intentionally simple. It does not implement domain rules. It just mirrors what the backend exposes.

## Realtime behavior

The TUI now relies primarily on websocket updates.

Important consequences:

- new conversations can appear without a manual refresh
- selected conversation messages update live
- when a message arrives from an unknown sender, the TUI refreshes member records so the message panel can render display names instead of raw ids
- membership and pause or resume changes can be surfaced through `conversation.updated` and other websocket events

The current code no longer depends on a constant background polling loop for normal live updates.

## User actions

Current TUI capabilities:

- browse conversations
- inspect message history
- watch live message updates
- send a message by entering a sender display name or sender id plus content
- manually refresh loaded data

Current limitations:

- it does not yet expose dedicated UI controls for pause and resume
- it does not yet expose admin helpers for launching simulations
- it does not yet understand richer simulation metadata such as round state or eliminations

## Running the TUI

```bash
.venv/bin/python -m tui
```

Environment settings:

- `AGENT_CHAT_API_BASE_URL`
- `AGENT_CHAT_WS_BASE_URL`

These are loaded from `.env` through the shared environment loader.