from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from chatapp.gateway import DEFAULT_API_BASE_URL, HttpChatGateway, RestChatGateway
from chatapp.options import CapabilityOption, capabilities_to_payload


@dataclass(slots=True)
class ChatConversation:
	server: ChatServer
	payload: dict[str, Any]

	@property
	def id(self) -> str:
		return self.payload["id"]

	@property
	def title(self) -> str | None:
		return self.payload.get("title")

	@property
	def participant_ids(self) -> list[str]:
		return list(self.payload.get("participant_ids", []))

	@property
	def messages_paused(self) -> bool:
		return bool(self.payload.get("messages_paused", False))

	def add_member(self, *, acting_member: ChatMember, member: ChatMember) -> ChatConversation:
		self.server.gateway.add_conversation_member(
			conversation_id=self.id,
			acting_member_id=acting_member.id,
			member_id=member.id,
		)
		participant_ids = self.payload.setdefault("participant_ids", [])
		if member.id not in participant_ids:
			participant_ids.append(member.id)
		return self

	def remove_member(self, *, acting_member: ChatMember, member: ChatMember) -> ChatConversation:
		self.server.gateway.remove_conversation_member(
			conversation_id=self.id,
			acting_member_id=acting_member.id,
			member_id=member.id,
		)
		self.payload["participant_ids"] = [participant_id for participant_id in self.participant_ids if participant_id != member.id]
		return self

	def pause(self, *, acting_member: ChatMember, notice: str) -> ChatConversation:
		self.payload = self.server.gateway.pause_group_messages(
			admin_member_id=acting_member.id,
			conversation_id=self.id,
			notice=notice,
		)
		return self

	def resume(self, *, acting_member: ChatMember) -> ChatConversation:
		self.payload = self.server.gateway.resume_group_messages(
			admin_member_id=acting_member.id,
			conversation_id=self.id,
		)
		return self

	def list_messages(self, *, viewer: ChatMember | None = None) -> list[dict[str, Any]]:
		if viewer is None:
			return self.server.gateway.list_conversation_messages(self.id)
		return viewer.read_messages(self)


@dataclass(slots=True)
class ChatMember:
	server: ChatServer
	payload: dict[str, Any]
	runtime: Any | None = None
	metadata: dict[str, Any] = field(default_factory=dict)

	@property
	def id(self) -> str:
		return self.payload["id"]

	@property
	def display_name(self) -> str:
		return self.payload["display_name"]

	@property
	def member_type(self) -> str:
		return self.payload["member_type"]

	@property
	def runtime_type(self) -> str:
		return self.payload["type"]

	@property
	def capabilities(self) -> dict[str, bool]:
		return dict(self.payload.get("capabilities", {}))

	def attach_runtime(self, runtime: Any) -> Any:
		self.runtime = runtime
		return runtime

	def send_message(self, conversation: ChatConversation, content: str) -> dict[str, Any]:
		return self.server.gateway.post_member_message(
			member_id=self.id,
			conversation_id=conversation.id,
			content=content,
		)

	def read_messages(self, conversation: ChatConversation) -> list[dict[str, Any]]:
		return self.server.gateway.list_member_visible_messages(self.id, conversation.id)

	def create_group_chat(self, *, title: str, members: Iterable[ChatMember] = ()) -> ChatConversation:
		return self.server.create_group_chat(owner=self, title=title, members=members)

	def start_direct_chat(self, *, title: str, members: Iterable[ChatMember]) -> ChatConversation:
		participants = [self, *list(members)]
		return self.server.create_direct_chat(title=title, participants=participants)

	def pause_group_chat(self, conversation: ChatConversation, notice: str) -> ChatConversation:
		return conversation.pause(acting_member=self, notice=notice)

	def resume_group_chat(self, conversation: ChatConversation) -> ChatConversation:
		return conversation.resume(acting_member=self)

	def leave(self, conversation: ChatConversation) -> ChatConversation:
		self.server.gateway.leave_member_conversation(member_id=self.id, conversation_id=conversation.id)
		conversation.payload["participant_ids"] = [participant_id for participant_id in conversation.participant_ids if participant_id != self.id]
		return conversation


class ChatServer:
	def __init__(self, gateway: RestChatGateway) -> None:
		self.gateway = gateway
		self._members_by_id: dict[str, ChatMember] = {}

	def add_member(
		self,
		*,
		name: str,
		runtime_type: str = "human",
		member_type: str = "user_regular",
		functionalities: Iterable[CapabilityOption | str] | dict[str, bool] | None = None,
		functionalites: Iterable[CapabilityOption | str] | dict[str, bool] | None = None,
		config: dict[str, Any] | None = None,
		runtime: Any | None = None,
	) -> ChatMember:
		capabilities = capabilities_to_payload(functionalites if functionalites is not None else functionalities)
		payload = self.gateway.create_member(
			display_name=name,
			runtime_type=runtime_type,
			member_type=member_type,
			capabilities=capabilities,
			config=config,
		)
		member = ChatMember(server=self, payload=payload, runtime=runtime)
		self._members_by_id[member.id] = member
		return member

	def create_group_chat(self, *, owner: ChatMember, title: str, members: Iterable[ChatMember] = ()) -> ChatConversation:
		payload = self.gateway.create_group_conversation(
			admin_member_id=owner.id,
			title=title,
			member_ids=[member.id for member in members],
		)
		return ChatConversation(server=self, payload=payload)

	def create_direct_chat(self, *, title: str, participants: Iterable[ChatMember]) -> ChatConversation:
		participant_list = list(participants)
		payload = self.gateway.create_direct_conversation(
			title=title,
			participant_ids=[participant.id for participant in participant_list],
		)
		return ChatConversation(server=self, payload=payload)

	def open_session(self, *, title: str, owner: ChatMember, members: Iterable[ChatMember] = ()) -> ChatConversation:
		return self.create_group_chat(owner=owner, title=title, members=members)

	def close(self) -> None:
		self.gateway.close()


def init_server(
	*,
	base_url: str = DEFAULT_API_BASE_URL,
	timeout: float = 10.0,
	gateway: RestChatGateway | None = None,
) -> ChatServer:
	resolved_gateway = gateway if gateway is not None else HttpChatGateway(base_url=base_url, timeout=timeout)
	return ChatServer(resolved_gateway)


def connect(
	*,
	base_url: str = DEFAULT_API_BASE_URL,
	timeout: float = 10.0,
	gateway: RestChatGateway | None = None,
) -> ChatServer:
	return init_server(base_url=base_url, timeout=timeout, gateway=gateway)


__all__ = [
	"ChatConversation",
	"ChatMember",
	"ChatServer",
	"connect",
	"init_server",
]