from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.websockets as websocket_module
from db.session import create_connection, get_db, init_db
from main import app


@pytest.fixture
def client(tmp_path: Path):
    database_path = tmp_path / "test.db"

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

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    websocket_module.SessionLocal = original_session_local


def test_agent_conversation_message_flow(client: TestClient) -> None:
    first_agent = client.post(
        "/api/agents",
        json={"display_name": "agent1", "type": "tipster"},
    )
    second_agent = client.post(
        "/api/agents",
        json={"display_name": "agent2", "type": "user"},
    )

    assert first_agent.status_code == 201
    assert second_agent.status_code == 201

    with client.websocket_connect("/ws/conversations") as conversation_ws:
        ready_event = conversation_ws.receive_json()
        assert ready_event["event"] == "conversations.ready"

        conversation_response = client.post(
            "/api/conversations",
            json={
                "type": "direct",
                "title": "test-chat",
                "participant_ids": [first_agent.json()["id"], second_agent.json()["id"]],
            },
        )

        assert conversation_response.status_code == 201
        conversation = conversation_response.json()
        created_event = conversation_ws.receive_json()
        assert created_event["event"] == "conversation.created"
        assert created_event["data"]["id"] == conversation["id"]

    with client.websocket_connect(f"/ws/conversations/{conversation['id']}") as message_ws:
        message_ready = message_ws.receive_json()
        assert message_ready["event"] == "connection.ready"

        message_response = client.post(
            "/api/messages",
            json={
                "conversation_id": conversation["id"],
                "sender_id": first_agent.json()["id"],
                "content": "hello from the test suite",
            },
        )

        assert message_response.status_code == 201
        message_event = message_ws.receive_json()
        assert message_event["event"] == "message.created"
        assert message_event["data"]["content"] == "hello from the test suite"

    list_response = client.get(f"/api/conversations/{conversation['id']}/messages")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1


def test_delete_conversation_emits_event(client: TestClient) -> None:
    first_agent = client.post(
        "/api/agents",
        json={"display_name": "agent1", "type": "tipster"},
    ).json()
    second_agent = client.post(
        "/api/agents",
        json={"display_name": "agent2", "type": "tipster"},
    ).json()
    conversation = client.post(
        "/api/conversations",
        json={
            "type": "direct",
            "title": "delete-me",
            "participant_ids": [first_agent["id"], second_agent["id"]],
        },
    ).json()

    with client.websocket_connect("/ws/conversations") as conversation_ws:
        conversation_ws.receive_json()
        delete_response = client.delete(f"/api/conversations/{conversation['id']}")
        assert delete_response.status_code == 204

        deleted_event = conversation_ws.receive_json()
        assert deleted_event["event"] == "conversation.deleted"
        assert deleted_event["data"]["id"] == conversation["id"]

    conversations_response = client.get("/api/conversations")
    assert conversations_response.status_code == 200
    assert conversations_response.json() == []