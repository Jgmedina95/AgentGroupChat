from __future__ import annotations

import asyncio
import os
from contextlib import suppress

import httpx
import websockets
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Static

from tui.components.conversation_list import ConversationTable
from tui.components.message_panel import MessagePanel
from tui.services.api_client import ApiClient
from tui.services.websocket_client import ChannelWebSocketClient, ConversationWebSocketClient
from tui.state.store import AppStore, ConversationRecord, MessageRecord


class ChatAdminApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }

    #body {
        height: 1fr;
    }

    #sidebar {
        width: 34;
        min-width: 28;
        border: round $panel;
        padding: 1;
    }

    #content {
        border: round $panel;
        padding: 1;
    }

    #conversation-table {
        height: 1fr;
    }

    #message-panel {
        height: 1fr;
        border: round $surface;
    }

    #conversation-meta {
        height: auto;
        padding-bottom: 1;
        color: $text-muted;
    }

    #composer {
        height: auto;
        padding-top: 1;
        layout: horizontal;
    }

    #sender-input {
        width: 30;
        margin-right: 1;
    }

    #message-input {
        width: 1fr;
        margin-right: 1;
    }

    #status {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    """

    BINDINGS = [("r", "refresh_data", "Refresh"), ("q", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self.store = AppStore()
        self.api_client = ApiClient()
        self.websocket_client = ConversationWebSocketClient()
        self.conversation_list_client = ChannelWebSocketClient("ws://localhost:8000/ws/conversations")
        self.websocket_stop = asyncio.Event()
        self.websocket_task: asyncio.Task[None] | None = None
        self.conversation_list_stop = asyncio.Event()
        self.conversation_list_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Loading conversations...", id="status")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static("Conversations", classes="panel-title")
                yield ConversationTable(id="conversation-table")
            with Vertical(id="content"):
                yield Static("Select a conversation with Enter.", id="conversation-meta")
                yield MessagePanel(id="message-panel")
                with Horizontal(id="composer"):
                    yield Input(placeholder="Sender ID", id="sender-input")
                    yield Input(placeholder="Type a message", id="message-input")
                    yield Button("Send", id="send-button", variant="primary")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one(ConversationTable).configure_columns()
        await self.load_initial_data()
        self.conversation_list_task = asyncio.create_task(
            self.conversation_list_client.listen(
                self.handle_conversation_list_event,
                self.conversation_list_stop,
            )
        )

    async def on_unmount(self) -> None:
        await self.shutdown_conversation_list_websocket()
        await self.shutdown_websocket()
        await self.api_client.aclose()

    async def load_initial_data(self) -> None:
        self.set_status("Loading agents and conversations...")
        previous_selection = self.store.selected_conversation_id
        try:
            agents, conversations = await asyncio.gather(
                self.api_client.list_agents(),
                self.api_client.list_conversations(),
            )
        except httpx.HTTPError as error:
            self.set_status(f"Failed to load data: {error}")
            return

        self.store.set_agents(agents)
        self.store.set_conversations(conversations)
        self.refresh_conversation_table()

        if not conversations:
            await self.clear_conversation_view()
            self.set_status("No conversations found. Create one through the API first.")
            return

        if previous_selection is None:
            await self.select_conversation(conversations[0].id)
            return

        if any(conversation.id == previous_selection for conversation in conversations):
            await self.select_conversation(previous_selection)
            return

        await self.clear_conversation_view()
        self.set_status("Selected conversation no longer exists. Choose another one from the list.")

    def refresh_conversation_table(self) -> None:
        table = self.query_one(ConversationTable)
        table.set_conversations(self.store.conversations)

    async def clear_conversation_view(self) -> None:
        await self.shutdown_websocket()
        self.store.selected_conversation_id = None
        self.query_one(MessagePanel).show_messages([], self.name_lookup)
        self.query_one("#conversation-meta", Static).update("No conversation selected.")
        self.query_one("#sender-input", Input).value = ""
        self.query_one("#message-input", Input).value = ""

    async def select_conversation(self, conversation_id: str) -> None:
        conversation = self.store.get_conversation(conversation_id)
        if conversation is None:
            self.set_status("Selected conversation was not found.")
            return

        self.store.selected_conversation_id = conversation_id
        await self.load_messages(conversation_id)
        self.show_conversation_meta(conversation)
        await self.subscribe_to_conversation(conversation_id)
        self.set_status(f"Watching {conversation.label}")

    async def load_messages(self, conversation_id: str) -> None:
        try:
            messages = await self.api_client.list_messages(conversation_id)
        except httpx.HTTPError as error:
            self.set_status(f"Failed to load messages: {error}")
            return

        self.store.set_messages(conversation_id, messages)
        self.render_messages(conversation_id)

    def render_messages(self, conversation_id: str) -> None:
        panel = self.query_one(MessagePanel)
        messages = self.store.get_messages(conversation_id)
        panel.show_messages(messages, self.name_lookup)

    def show_conversation_meta(self, conversation: ConversationRecord) -> None:
        participant_names = [self.store.get_agent_name(agent_id) for agent_id in conversation.participant_ids]
        participants = ", ".join(participant_names) if participant_names else "No participants"
        details = f"{conversation.label} [{conversation.type}]\nParticipants: {participants}"
        self.query_one("#conversation-meta", Static).update(details)
        sender_input = self.query_one("#sender-input", Input)
        if conversation.participant_ids and not sender_input.value:
            sender_input.value = conversation.participant_ids[0]

    @property
    def name_lookup(self) -> dict[str, str]:
        return {agent_id: agent.display_name for agent_id, agent in self.store.agents.items()}

    async def subscribe_to_conversation(self, conversation_id: str) -> None:
        await self.shutdown_websocket()
        self.websocket_stop = asyncio.Event()
        self.websocket_task = asyncio.create_task(
            self.websocket_client.listen(
                conversation_id,
                self.handle_websocket_event,
                self.websocket_stop,
                self.handle_websocket_status,
            )
        )

    async def shutdown_websocket(self) -> None:
        self.websocket_stop.set()
        if self.websocket_task is None:
            return

        self.websocket_task.cancel()
        with suppress(asyncio.CancelledError, websockets.WebSocketException, OSError):
            await self.websocket_task
        self.websocket_task = None

    async def shutdown_conversation_list_websocket(self) -> None:
        self.conversation_list_stop.set()
        if self.conversation_list_task is None:
            return

        self.conversation_list_task.cancel()
        with suppress(asyncio.CancelledError, websockets.WebSocketException, OSError):
            await self.conversation_list_task
        self.conversation_list_task = None

    async def handle_websocket_event(self, event: dict) -> None:
        event_name = event.get("event", "unknown")
        if event_name == "connection.ready":
            self.set_status(f"Live websocket ready for {event.get('conversation_id')}")
            return

        payload = event.get("data")
        if not isinstance(payload, dict):
            self.set_status(f"Unexpected websocket event: {event_name}")
            return

        message = MessageRecord.from_dict(payload)
        self.store.upsert_message(message)
        if message.conversation_id == self.store.selected_conversation_id:
            self.render_messages(message.conversation_id)
        self.set_status(f"Live update: {event_name}")

    async def handle_websocket_status(self, status: str) -> None:
        self.set_status(status)

    async def handle_conversation_list_event(self, event: dict) -> None:
        event_name = event.get("event", "unknown")
        if event_name == "conversations.ready":
            return

        if event_name == "conversation.created":
            payload = event.get("data")
            if not isinstance(payload, dict):
                self.set_status("Conversation created event was malformed.")
                return

            conversation = ConversationRecord.from_dict(payload)
            self.store.upsert_conversation(conversation)
            self.refresh_conversation_table()
            self.set_status("Conversation list updated.")
            return

        if event_name == "conversation.deleted":
            payload = event.get("data")
            conversation_id = payload.get("id") if isinstance(payload, dict) else None
            if conversation_id is None:
                self.set_status("Conversation deleted event was malformed.")
                return

            removed = self.store.remove_conversation(conversation_id)
            if not removed:
                return

            self.refresh_conversation_table()
            if conversation_id == self.store.selected_conversation_id:
                await self.clear_conversation_view()
                self.set_status("Selected conversation was deleted.")
                return

            self.set_status("Conversation removed.")

    async def on_data_table_row_selected(self, event: ConversationTable.RowSelected) -> None:
        row_key = str(event.row_key.value)
        await self.select_conversation(row_key)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-button":
            await self.send_current_message()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "message-input":
            await self.send_current_message()

    async def send_current_message(self) -> None:
        conversation_id = self.store.selected_conversation_id
        sender_input = self.query_one("#sender-input", Input)
        message_input = self.query_one("#message-input", Input)
        sender_id = sender_input.value.strip()
        content = message_input.value.strip()

        if not conversation_id:
            self.set_status("Select a conversation before sending a message.")
            return
        if not sender_id:
            self.set_status("Provide a sender ID.")
            return
        if not content:
            self.set_status("Message content cannot be empty.")
            return

        try:
            await self.api_client.create_message(conversation_id, sender_id, content)
        except httpx.HTTPStatusError as error:
            detail = error.response.text
            self.set_status(f"Message send failed: {detail}")
            return
        except httpx.HTTPError as error:
            self.set_status(f"Message send failed: {error}")
            return

        message_input.value = ""
        self.set_status("Message sent.")

    async def action_refresh_data(self) -> None:
        await self.load_initial_data()

    def set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)


def main() -> None:
    ChatAdminApp().run()


if __name__ == "__main__":
    main()