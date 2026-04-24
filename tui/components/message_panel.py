from __future__ import annotations

from textual.widgets import RichLog

from tui.state.store import MessageRecord


class MessagePanel(RichLog):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, wrap=True, highlight=True, markup=True, **kwargs)

    def show_messages(self, messages: list[MessageRecord], name_lookup: dict[str, str]) -> None:
        self.clear()
        if not messages:
            self.write("[italic]No messages yet for this conversation.[/italic]")
            return

        for message in messages:
            self.write(self._format_message(message, name_lookup))

    def append_message(self, message: MessageRecord, name_lookup: dict[str, str]) -> None:
        self.write(self._format_message(message, name_lookup))

    def _format_message(self, message: MessageRecord, name_lookup: dict[str, str]) -> str:
        sender = name_lookup.get(message.sender_id, message.sender_id)
        if message.deleted_at:
            return (
                f"[dim]{message.created_at}[/dim] [bold]{sender}[/bold]: "
                f"[strike dim]{message.content}[/strike dim] [red](deleted)[/red]"
            )
        return f"[dim]{message.created_at}[/dim] [bold]{sender}[/bold]: {message.content}"