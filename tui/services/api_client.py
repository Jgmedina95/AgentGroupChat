from __future__ import annotations

import os

import httpx

from tui.state.store import AgentRecord, ConversationRecord, MessageRecord


DEFAULT_API_BASE_URL = os.getenv("AGENT_CHAT_API_BASE_URL", "http://localhost:8000/api")


class ApiClient:
    def __init__(self, base_url: str = DEFAULT_API_BASE_URL) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_agents(self) -> list[AgentRecord]:
        response = await self._client.get("/agents")
        response.raise_for_status()
        return [AgentRecord.from_dict(payload) for payload in response.json()]

    async def list_conversations(self) -> list[ConversationRecord]:
        response = await self._client.get("/conversations")
        response.raise_for_status()
        return [ConversationRecord.from_dict(payload) for payload in response.json()]

    async def list_messages(self, conversation_id: str, include_deleted: bool = True) -> list[MessageRecord]:
        response = await self._client.get(
            f"/conversations/{conversation_id}/messages",
            params={"include_deleted": str(include_deleted).lower()},
        )
        response.raise_for_status()
        return [MessageRecord.from_dict(payload) for payload in response.json()]

    async def create_message(self, conversation_id: str, sender_id: str, content: str) -> MessageRecord:
        response = await self._client.post(
            "/messages",
            json={
                "conversation_id": conversation_id,
                "sender_id": sender_id,
                "content": content,
            },
        )
        response.raise_for_status()
        return MessageRecord.from_dict(response.json())

    async def delete_conversation(self, conversation_id: str) -> None:
        response = await self._client.delete(f"/conversations/{conversation_id}")
        response.raise_for_status()