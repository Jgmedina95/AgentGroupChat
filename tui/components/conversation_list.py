from __future__ import annotations

from textual.widgets import DataTable

from tui.state.store import ConversationRecord


class ConversationTable(DataTable):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, zebra_stripes=True, **kwargs)
        self.cursor_type = "row"

    def configure_columns(self) -> None:
        if not self.columns:
            self.add_columns("Conversation", "Members", "Type")

    def set_conversations(self, conversations: list[ConversationRecord]) -> None:
        self.clear(columns=False)
        for conversation in conversations:
            self.add_row(
                conversation.label,
                str(len(conversation.participant_ids)),
                conversation.type,
                key=conversation.id,
            )