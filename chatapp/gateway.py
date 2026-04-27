from __future__ import annotations

from typing import Any

import httpx


DEFAULT_API_BASE_URL = "http://localhost:8000"


class RestChatGateway:
	def __init__(self, client: Any) -> None:
		self._client = client

	def create_member(
		self,
		*,
		display_name: str,
		runtime_type: str,
		member_type: str,
		capabilities: dict[str, bool] | None = None,
		config: dict[str, Any] | None = None,
	) -> dict[str, Any]:
		response = self._client.post(
			"/api/members",
			json={
				"display_name": display_name,
				"type": runtime_type,
				"member_type": member_type,
				"capabilities": capabilities,
				"config": config,
			},
		)
		response.raise_for_status()
		return response.json()

	def create_group_conversation(
		self,
		*,
		admin_member_id: str,
		title: str,
		member_ids: list[str],
	) -> dict[str, Any]:
		response = self._client.post(
			f"/api/members/{admin_member_id}/conversations/group",
			json={"title": title, "member_ids": member_ids},
		)
		response.raise_for_status()
		return response.json()

	def create_direct_conversation(
		self,
		*,
		title: str,
		participant_ids: list[str],
	) -> dict[str, Any]:
		response = self._client.post(
			"/api/conversations",
			json={"type": "direct", "title": title, "participant_ids": participant_ids},
		)
		response.raise_for_status()
		return response.json()

	def add_conversation_member(
		self,
		*,
		conversation_id: str,
		acting_member_id: str,
		member_id: str,
	) -> dict[str, Any]:
		response = self._client.post(
			f"/api/conversations/{conversation_id}/members",
			json={"acting_member_id": acting_member_id, "member_id": member_id},
		)
		response.raise_for_status()
		return response.json()

	def remove_conversation_member(
		self,
		*,
		conversation_id: str,
		acting_member_id: str,
		member_id: str,
	) -> dict[str, Any]:
		response = self._client.delete(
			f"/api/conversations/{conversation_id}/members/{member_id}",
			params={"acting_member_id": acting_member_id},
		)
		response.raise_for_status()
		return response.json()

	def post_member_message(self, *, member_id: str, conversation_id: str, content: str) -> dict[str, Any]:
		response = self._client.post(
			f"/api/members/{member_id}/messages",
			json={"conversation_id": conversation_id, "content": content},
		)
		response.raise_for_status()
		return response.json()

	def pause_group_messages(self, *, admin_member_id: str, conversation_id: str, notice: str) -> dict[str, Any]:
		response = self._client.post(
			f"/api/conversations/{conversation_id}/pause-messages",
			json={"acting_member_id": admin_member_id, "notice": notice},
		)
		response.raise_for_status()
		return response.json()

	def resume_group_messages(self, *, admin_member_id: str, conversation_id: str) -> dict[str, Any]:
		response = self._client.post(
			f"/api/conversations/{conversation_id}/resume-messages",
			json={"acting_member_id": admin_member_id},
		)
		response.raise_for_status()
		return response.json()

	def leave_member_conversation(self, *, member_id: str, conversation_id: str) -> dict[str, Any]:
		response = self._client.post(f"/api/members/{member_id}/conversations/{conversation_id}/leave")
		response.raise_for_status()
		return response.json()

	def list_conversation_messages(self, conversation_id: str) -> list[dict[str, Any]]:
		response = self._client.get(f"/api/conversations/{conversation_id}/messages")
		response.raise_for_status()
		return response.json()

	def list_member_visible_messages(self, member_id: str, conversation_id: str) -> list[dict[str, Any]]:
		response = self._client.get(f"/api/members/{member_id}/conversations/{conversation_id}/messages")
		response.raise_for_status()
		return response.json()

	def close(self) -> None:
		return None


class HttpChatGateway(RestChatGateway):
	def __init__(self, base_url: str = DEFAULT_API_BASE_URL, timeout: float = 10.0) -> None:
		client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
		super().__init__(client)
		self._http_client = client

	def close(self) -> None:
		self._http_client.close()


class TestClientChatGateway(RestChatGateway):
	__test__ = False

	pass


__all__ = [
	"DEFAULT_API_BASE_URL",
	"HttpChatGateway",
	"RestChatGateway",
	"TestClientChatGateway",
]