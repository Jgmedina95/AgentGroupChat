from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chatapp.facade import ChatConversation, ChatMember, ChatServer
from chatapp.options import read_messages, send_messages
from simulation.runtimes.llm import OpenAICompatibleLLMDecisionClient, resolve_llm_provider_config


DEFAULT_DIRECT_CHAT_TITLE_TEMPLATE = "{host_name} and {assistant_name}"
DEFAULT_ASSISTANT_SYSTEM_PROMPT = (
	"You are a helpful assistant chatting with a human inside a direct chat. "
	"Use only the messages visible in this conversation. "
	"Reply naturally in one or two short paragraphs unless the user asks for more detail."
)


def _messages_to_transcript(messages: list[dict[str, Any]]) -> str:
	if not messages:
		return "<no messages>"
	return "\n".join(f"{message['sender_id']}: {message['content']}" for message in messages)


def _normalize_reply(response: str | None) -> str:
	if response is None:
		return "I do not have a useful reply yet."
	message = " ".join(str(response).strip().split())
	return message or "I do not have a useful reply yet."


class GenericLLMChatRuntime:
	def __init__(self, *, member: ChatMember, decision_client: Any, system_prompt: str = DEFAULT_ASSISTANT_SYSTEM_PROMPT) -> None:
		self._member = member
		self._decision_client = decision_client
		self._system_prompt = system_prompt

	def generate_reply(self, *, conversation: ChatConversation) -> str:
		visible_messages = self._member.read_messages(conversation)
		response = self._decision_client.decide(
			player_name=self._member.display_name,
			phase="reply",
			system_prompt=self._system_prompt,
			user_prompt=(
				"Visible direct chat transcript:\n"
				f"{_messages_to_transcript(visible_messages)}\n\n"
				"Reply with the next message you would send in this chat."
			),
		)
		return _normalize_reply(response)


class GenericLLMChatRuntimeFactory:
	def __init__(self, decision_client: Any, *, system_prompt: str = DEFAULT_ASSISTANT_SYSTEM_PROMPT) -> None:
		self._decision_client = decision_client
		self._system_prompt = system_prompt

	@classmethod
	def from_environment(
		cls,
		provider: str | None = None,
		*,
		system_prompt: str = DEFAULT_ASSISTANT_SYSTEM_PROMPT,
	) -> GenericLLMChatRuntimeFactory:
		provider_config = resolve_llm_provider_config(provider)
		return cls(
			OpenAICompatibleLLMDecisionClient(
				api_key=provider_config.api_key,
				base_url=provider_config.base_url,
				model=provider_config.model,
				default_headers=provider_config.headers,
			),
			system_prompt=system_prompt,
		)

	def create(self, *, member: ChatMember) -> GenericLLMChatRuntime:
		return GenericLLMChatRuntime(
			member=member,
			decision_client=self._decision_client,
			system_prompt=self._system_prompt,
		)

	def close(self) -> None:
		close = getattr(self._decision_client, "close", None)
		if callable(close):
			close()


@dataclass(slots=True)
class DirectHumanLLMChatSession:
	server: ChatServer
	host: ChatMember
	assistant: ChatMember
	conversation: ChatConversation
	assistant_runtime: GenericLLMChatRuntime
	last_replied_host_message_id: str | None = None

	def send_host_message(self, content: str) -> dict[str, Any]:
		return self.host.send_message(self.conversation, content)

	def generate_assistant_reply(self) -> dict[str, Any]:
		reply_text = self.assistant_runtime.generate_reply(conversation=self.conversation)
		return self.assistant.send_message(self.conversation, reply_text)

	def exchange(self, content: str) -> tuple[dict[str, Any], dict[str, Any]]:
		host_message = self.send_host_message(content)
		self.last_replied_host_message_id = host_message["id"]
		assistant_message = self.generate_assistant_reply()
		return host_message, assistant_message

	def maybe_reply_to_new_host_message(self) -> dict[str, Any] | None:
		messages = self.conversation.list_messages(viewer=self.host)
		if not messages:
			return None
		latest_message = messages[-1]
		if latest_message.get("deleted_at") is not None:
			return None
		if latest_message["sender_id"] != self.host.id:
			return None
		if latest_message["id"] == self.last_replied_host_message_id:
			return None
		self.last_replied_host_message_id = latest_message["id"]
		return self.generate_assistant_reply()


def create_direct_human_llm_chat(
	*,
	server: ChatServer,
	runtime_factory: GenericLLMChatRuntimeFactory,
	host_name: str = "Host",
	assistant_name: str = "Assistant",
	title: str | None = None,
) -> DirectHumanLLMChatSession:
	host = server.add_member(
		name=host_name,
		runtime_type="human",
		member_type="user_regular",
		functionalities=[send_messages, read_messages],
	)
	assistant = server.add_member(
		name=assistant_name,
		runtime_type="llm",
		member_type="user_regular",
		functionalities=[send_messages, read_messages],
		config={"chat_runtime": "generic_llm_chat"},
	)
	assistant_runtime = runtime_factory.create(member=assistant)
	assistant.attach_runtime(assistant_runtime)
	conversation = host.start_direct_chat(
		title=title or DEFAULT_DIRECT_CHAT_TITLE_TEMPLATE.format(host_name=host_name, assistant_name=assistant_name),
		members=[assistant],
	)
	return DirectHumanLLMChatSession(
		server=server,
		host=host,
		assistant=assistant,
		conversation=conversation,
		assistant_runtime=assistant_runtime,
	)


__all__ = [
	"DEFAULT_ASSISTANT_SYSTEM_PROMPT",
	"DEFAULT_DIRECT_CHAT_TITLE_TEMPLATE",
	"DirectHumanLLMChatSession",
	"GenericLLMChatRuntime",
	"GenericLLMChatRuntimeFactory",
	"create_direct_human_llm_chat",
]