from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AgentRecord:
    id: str
    type: str
    display_name: str
    config: dict | None

    @classmethod
    def from_dict(cls, payload: dict) -> "AgentRecord":
        return cls(
            id=payload["id"],
            type=payload["type"],
            display_name=payload["display_name"],
            config=payload.get("config"),
        )


@dataclass(slots=True)
class ConversationRecord:
    id: str
    type: str
    title: str | None
    participant_ids: list[str]

    @classmethod
    def from_dict(cls, payload: dict) -> "ConversationRecord":
        return cls(
            id=payload["id"],
            type=payload["type"],
            title=payload.get("title"),
            participant_ids=list(payload.get("participant_ids", [])),
        )

    @property
    def label(self) -> str:
        return self.title or self.id


@dataclass(slots=True)
class MessageRecord:
    id: str
    conversation_id: str
    sender_id: str
    content: str
    created_at: str
    deleted_at: str | None

    @classmethod
    def from_dict(cls, payload: dict) -> "MessageRecord":
        return cls(
            id=payload["id"],
            conversation_id=payload["conversation_id"],
            sender_id=payload["sender_id"],
            content=payload["content"],
            created_at=payload["created_at"],
            deleted_at=payload.get("deleted_at"),
        )


@dataclass(slots=True)
class AppStore:
    agents: dict[str, AgentRecord] = field(default_factory=dict)
    conversations: list[ConversationRecord] = field(default_factory=list)
    messages_by_conversation: dict[str, list[MessageRecord]] = field(default_factory=dict)
    selected_conversation_id: str | None = None

    def set_agents(self, agents: list[AgentRecord]) -> None:
        self.agents = {agent.id: agent for agent in agents}

    def set_conversations(self, conversations: list[ConversationRecord]) -> None:
        self.conversations = conversations

    def upsert_conversation(self, conversation: ConversationRecord) -> None:
        for index, current in enumerate(self.conversations):
            if current.id == conversation.id:
                self.conversations[index] = conversation
                break
        else:
            self.conversations.append(conversation)

        self.conversations.sort(key=lambda item: ((item.title or item.id).lower(), item.id))

    def remove_conversation(self, conversation_id: str) -> bool:
        original_count = len(self.conversations)
        self.conversations = [conversation for conversation in self.conversations if conversation.id != conversation_id]
        self.messages_by_conversation.pop(conversation_id, None)
        return len(self.conversations) != original_count

    def set_messages(self, conversation_id: str, messages: list[MessageRecord]) -> None:
        self.messages_by_conversation[conversation_id] = sorted(messages, key=lambda message: message.created_at)

    def upsert_message(self, message: MessageRecord) -> None:
        messages = self.messages_by_conversation.setdefault(message.conversation_id, [])
        for index, current in enumerate(messages):
            if current.id == message.id:
                messages[index] = message
                break
        else:
            messages.append(message)

        messages.sort(key=lambda item: item.created_at)

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        for conversation in self.conversations:
            if conversation.id == conversation_id:
                return conversation
        return None

    def get_messages(self, conversation_id: str) -> list[MessageRecord]:
        return list(self.messages_by_conversation.get(conversation_id, []))

    def get_agent_name(self, agent_id: str) -> str:
        agent = self.agents.get(agent_id)
        return agent.display_name if agent else agent_id