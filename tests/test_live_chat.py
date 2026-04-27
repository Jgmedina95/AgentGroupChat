from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import api.websockets as websocket_module
import chatapp
from db.session import create_connection, get_db, init_db
from main import app
from chatapp.gateway import TestClientChatGateway
from chatapp.live_chat import GenericLLMChatRuntimeFactory


class ScriptedReplyDecisionClient:
	def __init__(self, replies: list[str]) -> None:
		self._replies = list(replies)
		self.calls: list[tuple[str, str]] = []

	def decide(self, *, player_name: str, phase: str, system_prompt: str, user_prompt: str) -> str | None:
		self.calls.append((player_name, phase))
		if not self._replies:
			return None
		return self._replies.pop(0)

	def close(self) -> None:
		return None


def test_create_direct_human_llm_chat_supports_back_and_forth(tmp_path: Path) -> None:
	database_path = tmp_path / "live-chat.db"
	init_db(database_path)

	def testing_session_local() -> sqlite3.Connection:
		return create_connection(database_path)

	def override_get_db():
		db = testing_session_local()
		try:
			yield db
		finally:
			db.close()

	app.dependency_overrides[get_db] = override_get_db
	original_session_local = websocket_module.SessionLocal
	websocket_module.SessionLocal = testing_session_local

	try:
		with TestClient(app) as client:
			server = chatapp.init_server(gateway=TestClientChatGateway(client))
			runtime_factory = GenericLLMChatRuntimeFactory(ScriptedReplyDecisionClient(["Hello from the assistant."]))
			session = chatapp.create_direct_human_llm_chat(
				server=server,
				runtime_factory=runtime_factory,
				host_name="Jorge",
				assistant_name="Helper",
			)

			host_message, assistant_message = session.exchange("Hi there")

			assert session.host.display_name == "Jorge"
			assert session.assistant.display_name == "Helper"
			assert set(session.conversation.participant_ids) == {session.host.id, session.assistant.id}
			assert host_message["content"] == "Hi there"
			assert assistant_message["content"] == "Hello from the assistant."

			visible_messages = session.conversation.list_messages(viewer=session.host)
			assert [message["content"] for message in visible_messages] == ["Hi there", "Hello from the assistant."]
			assert session.assistant.runtime is not None
			assert session.assistant.runtime.generate_reply(conversation=session.conversation) == "I do not have a useful reply yet."
			runtime_factory.close()
			server.close()
	finally:
		app.dependency_overrides.clear()
		websocket_module.SessionLocal = original_session_local


def test_direct_human_llm_chat_can_reply_to_tui_driven_host_messages(tmp_path: Path) -> None:
	database_path = tmp_path / "live-chat-tui.db"
	init_db(database_path)

	def testing_session_local() -> sqlite3.Connection:
		return create_connection(database_path)

	def override_get_db():
		db = testing_session_local()
		try:
			yield db
		finally:
			db.close()

	app.dependency_overrides[get_db] = override_get_db
	original_session_local = websocket_module.SessionLocal
	websocket_module.SessionLocal = testing_session_local

	try:
		with TestClient(app) as client:
			server = chatapp.init_server(gateway=TestClientChatGateway(client))
			runtime_factory = GenericLLMChatRuntimeFactory(ScriptedReplyDecisionClient(["Reply from Copilot."]))
			session = chatapp.create_direct_human_llm_chat(
				server=server,
				runtime_factory=runtime_factory,
				host_name="Jorge",
				assistant_name="Copilot",
			)

			session.send_host_message("Message from the TUI")
			assistant_message = session.maybe_reply_to_new_host_message()

			assert assistant_message is not None
			assert assistant_message["content"] == "Reply from Copilot."
			assert session.maybe_reply_to_new_host_message() is None

			visible_messages = session.conversation.list_messages(viewer=session.host)
			assert [message["content"] for message in visible_messages] == ["Message from the TUI", "Reply from Copilot."]
			runtime_factory.close()
			server.close()
	finally:
		app.dependency_overrides.clear()
		websocket_module.SessionLocal = original_session_local