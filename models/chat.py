from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class Member:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ""
    member_type: str = "user_regular"
    display_name: str = ""
    capabilities: dict | None = None
    config: dict | None = None


@dataclass(slots=True)
class Membership:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str = ""
    member_id: str = ""
    status: str = "active"
    role: str = "member"
    invited_by_member_id: str | None = None
    joined_at: datetime | None = None
    left_at: datetime | None = None

    @property
    def agent_id(self) -> str:
        return self.member_id


@dataclass(slots=True)
class Message:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str = ""
    sender_id: str = ""
    content: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    deleted_at: datetime | None = None


@dataclass(slots=True)
class Conversation:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ""
    title: str | None = None
    created_by_member_id: str | None = None
    join_policy: str = "invite_only"
    status: str = "active"
    messages_paused: bool = False
    message_pause_notice: str | None = None
    memberships: list[Membership] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)

    @property
    def participants(self) -> list[Membership]:
        return self.memberships


Agent = Member
ConversationParticipant = Membership