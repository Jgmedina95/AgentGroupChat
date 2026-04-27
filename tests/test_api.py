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


def create_member(
    client: TestClient,
    display_name: str,
    runtime_type: str = "human",
    member_type: str = "user_regular",
    capabilities: dict | None = None,
) -> dict:
    response = client.post(
        "/api/members",
        json={
            "display_name": display_name,
            "type": runtime_type,
            "member_type": member_type,
            "capabilities": capabilities,
        },
    )
    assert response.status_code == 201
    return response.json()


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


def test_owner_managed_group_membership_flow(client: TestClient) -> None:
    owner = create_member(client, "owner", runtime_type="human", member_type="user_premium")
    member_to_add = create_member(client, "member-to-add", runtime_type="llm", member_type="user_regular")
    outsider = create_member(client, "outsider", runtime_type="bot", member_type="user_regular")

    group_response = client.post(
        "/api/conversations/group",
        json={
            "created_by_member_id": owner["id"],
            "title": "owner-group",
            "member_ids": [member_to_add["id"]],
        },
    )

    assert group_response.status_code == 201
    group = group_response.json()
    assert group["type"] == "group"
    assert group["participant_ids"] == [owner["id"], member_to_add["id"]]

    members_response = client.get(f"/api/conversations/{group['id']}/members")
    assert members_response.status_code == 200
    memberships = members_response.json()
    owner_membership = next(item for item in memberships if item["member_id"] == owner["id"])
    added_membership = next(item for item in memberships if item["member_id"] == member_to_add["id"])
    assert owner_membership["role"] == "owner"
    assert owner_membership["status"] == "active"
    assert added_membership["role"] == "member"
    assert added_membership["status"] == "active"

    with client.websocket_connect(f"/ws/conversations/{group['id']}") as conversation_ws:
        ready_event = conversation_ws.receive_json()
        assert ready_event["event"] == "connection.ready"

        added_member = create_member(client, "late-joiner", runtime_type="human", member_type="user_regular")
        add_response = client.post(
            f"/api/conversations/{group['id']}/members",
            json={"acting_member_id": owner["id"], "member_id": added_member["id"]},
        )
        assert add_response.status_code == 201
        add_event = conversation_ws.receive_json()
        assert add_event["event"] == "membership.added"
        assert add_event["data"]["member_id"] == added_member["id"]

        forbidden_add = client.post(
            f"/api/conversations/{group['id']}/members",
            json={"acting_member_id": outsider["id"], "member_id": outsider["id"]},
        )
        assert forbidden_add.status_code == 403

        remove_response = client.delete(
            f"/api/conversations/{group['id']}/members/{member_to_add['id']}",
            params={"acting_member_id": owner["id"]},
        )
        assert remove_response.status_code == 200
        remove_event = conversation_ws.receive_json()
        assert remove_event["event"] == "membership.removed"
        assert remove_event["data"]["member_id"] == member_to_add["id"]

        post_removed = client.post(
            "/api/messages",
            json={
                "conversation_id": group["id"],
                "sender_id": member_to_add["id"],
                "content": "should fail",
            },
        )
        assert post_removed.status_code == 403

        leave_response = client.post(
            f"/api/conversations/{group['id']}/leave",
            json={"member_id": added_member["id"]},
        )
        assert leave_response.status_code == 200
        leave_event = conversation_ws.receive_json()
        assert leave_event["event"] == "membership.left"
        assert leave_event["data"]["member_id"] == added_member["id"]

        post_left = client.post(
            "/api/messages",
            json={
                "conversation_id": group["id"],
                "sender_id": added_member["id"],
                "content": "should also fail",
            },
        )
        assert post_left.status_code == 403

    final_members_response = client.get(f"/api/conversations/{group['id']}/members")
    assert final_members_response.status_code == 200
    final_memberships = {item["member_id"]: item for item in final_members_response.json()}
    assert final_memberships[owner["id"]]["status"] == "active"
    assert final_memberships[member_to_add["id"]]["status"] == "removed"
    assert final_memberships[added_member["id"]]["status"] == "left"


def test_member_types_control_group_permissions(client: TestClient) -> None:
    regular_user = create_member(client, "regular-user", runtime_type="human", member_type="user_regular")
    premium_user = create_member(client, "premium-user", runtime_type="llm", member_type="user_premium")
    admin_user = create_member(client, "admin-user", runtime_type="bot", member_type="admin")
    candidate = create_member(client, "candidate", runtime_type="human", member_type="user_regular")

    regular_group = client.post(
        "/api/conversations/group",
        json={
            "created_by_member_id": regular_user["id"],
            "title": "regular-group",
            "member_ids": [],
        },
    )
    assert regular_group.status_code == 403

    premium_group = client.post(
        "/api/conversations/group",
        json={
            "created_by_member_id": premium_user["id"],
            "title": "premium-group",
            "member_ids": [candidate["id"]],
        },
    )
    assert premium_group.status_code == 201
    assert premium_group.json()["participant_ids"] == [premium_user["id"], candidate["id"]]

    second_candidate = create_member(client, "second-candidate", runtime_type="human", member_type="user_regular")
    admin_add = client.post(
        f"/api/conversations/{premium_group.json()['id']}/members",
        json={"acting_member_id": admin_user["id"], "member_id": second_candidate["id"]},
    )
    assert admin_add.status_code == 201

    memberships = client.get(f"/api/conversations/{premium_group.json()['id']}/members")
    assert memberships.status_code == 200
    added_membership = next(item for item in memberships.json() if item["member_id"] == second_candidate["id"])
    assert added_membership["status"] == "active"


def test_admin_can_pause_and_resume_group_messages(client: TestClient) -> None:
    admin = create_member(client, "game-master", runtime_type="human", member_type="admin")
    player_one = create_member(client, "player-one", runtime_type="llm", member_type="user_regular")
    player_two = create_member(client, "player-two", runtime_type="bot", member_type="user_regular")

    group_response = client.post(
        "/api/conversations/group",
        json={
            "created_by_member_id": admin["id"],
            "title": "round-one",
            "member_ids": [player_one["id"], player_two["id"]],
        },
    )
    assert group_response.status_code == 201
    group = group_response.json()

    pause_response = client.post(
        f"/api/conversations/{group['id']}/pause-messages",
        json={
            "acting_member_id": admin["id"],
            "notice": "Conversation is closed momentarily while the game master reviews the round.",
        },
    )
    assert pause_response.status_code == 200
    assert pause_response.json()["messages_paused"] is True

    blocked_message = client.post(
        "/api/messages",
        json={
            "conversation_id": group["id"],
            "sender_id": player_one["id"],
            "content": "am I allowed to talk?",
        },
    )
    assert blocked_message.status_code == 403
    assert blocked_message.json()["detail"] == "Conversation is closed momentarily while the game master reviews the round."

    admin_message = client.post(
        "/api/messages",
        json={
            "conversation_id": group["id"],
            "sender_id": admin["id"],
            "content": "The round is paused. Think quietly.",
        },
    )
    assert admin_message.status_code == 201

    resume_response = client.post(
        f"/api/conversations/{group['id']}/resume-messages",
        json={"acting_member_id": admin["id"]},
    )
    assert resume_response.status_code == 200
    assert resume_response.json()["messages_paused"] is False

    resumed_message = client.post(
        "/api/messages",
        json={
            "conversation_id": group["id"],
            "sender_id": player_two["id"],
            "content": "discussion resumes",
        },
    )
    assert resumed_message.status_code == 201


def test_non_admin_cannot_pause_group_messages(client: TestClient) -> None:
    premium_owner = create_member(client, "premium-owner", runtime_type="human", member_type="user_premium")
    player = create_member(client, "player", runtime_type="llm", member_type="user_regular")

    group_response = client.post(
        "/api/conversations/group",
        json={
            "created_by_member_id": premium_owner["id"],
            "title": "premium-room",
            "member_ids": [player["id"]],
        },
    )
    assert group_response.status_code == 201

    pause_response = client.post(
        f"/api/conversations/{group_response.json()['id']}/pause-messages",
        json={
            "acting_member_id": premium_owner["id"],
            "notice": "stop",
        },
    )
    assert pause_response.status_code == 403


def test_simulation_trace_runs_can_be_created_and_listed(client: TestClient) -> None:
    admin = create_member(client, "trace-admin", runtime_type="human", member_type="admin")
    friend = create_member(client, "trace-friend", runtime_type="llm", member_type="user_regular")
    conversation_response = client.post(
        f"/api/members/{admin['id']}/conversations/group",
        json={"title": "trace-group", "member_ids": [friend["id"]]},
    )
    assert conversation_response.status_code == 201
    conversation_id = conversation_response.json()["id"]

    create_response = client.post(
        "/api/simulation-traces",
        json={
            "scenario_type": "trip_planner",
            "root_conversation_id": conversation_id,
            "final_choice": "Lisbon",
            "consensus_reached": True,
            "stopped_early": False,
            "stop_requested_by_member_id": None,
            "events": [
                {
                    "event_type": "group_chat_created",
                    "member_id": admin["id"],
                    "member_name": admin["display_name"],
                    "conversation_id": conversation_id,
                    "details": {"title": "trace-group"},
                },
                {
                    "event_type": "message_posted",
                    "member_id": friend["id"],
                    "member_name": friend["display_name"],
                    "conversation_id": conversation_id,
                    "details": {"content": "hello", "message_scope": "group"},
                },
            ],
        },
    )
    assert create_response.status_code == 201
    trace_run = create_response.json()
    assert trace_run["scenario_type"] == "trip_planner"
    assert len(trace_run["events"]) == 2
    assert trace_run["events"][1]["member_id"] == friend["id"]

    list_response = client.get(f"/api/conversations/{conversation_id}/simulation-traces")
    assert list_response.status_code == 200
    trace_runs = list_response.json()
    assert len(trace_runs) == 1
    assert trace_runs[0]["id"] == trace_run["id"]

    get_response = client.get(f"/api/simulation-traces/{trace_run['id']}")
    assert get_response.status_code == 200
    loaded_trace_run = get_response.json()
    assert loaded_trace_run["root_conversation_id"] == conversation_id
    assert [event["event_type"] for event in loaded_trace_run["events"]] == [
        "group_chat_created",
        "message_posted",
    ]


def test_member_capabilities_gate_actions_but_allow_read_access(client: TestClient) -> None:
    owner = create_member(client, "owner", runtime_type="human", member_type="user_premium")
    restricted_member = create_member(
        client,
        "restricted-member",
        runtime_type="llm",
        member_type="user_regular",
        capabilities={
            "send_messages": False,
            "create_group_conversations": False,
            "leave_conversations": False,
        },
    )

    group_response = client.post(
        "/api/conversations/group",
        json={
            "created_by_member_id": owner["id"],
            "title": "capability-room",
            "member_ids": [restricted_member["id"]],
        },
    )
    assert group_response.status_code == 201
    group = group_response.json()

    seeded_message = client.post(
        "/api/messages",
        json={
            "conversation_id": group["id"],
            "sender_id": owner["id"],
            "content": "Read this before you decide.",
        },
    )
    assert seeded_message.status_code == 201

    access_response = client.get(f"/api/members/{restricted_member['id']}/access")
    assert access_response.status_code == 200
    access = access_response.json()
    assert access["member"]["id"] == restricted_member["id"]
    assert access["capabilities"]["send_messages"] is False
    assert access["capabilities"]["create_group_conversations"] is False
    assert access["capabilities"]["leave_conversations"] is False
    assert access["visible_conversation_ids"] == [group["id"]]

    visible_conversations = client.get(f"/api/members/{restricted_member['id']}/conversations")
    assert visible_conversations.status_code == 200
    assert [conversation["id"] for conversation in visible_conversations.json()] == [group["id"]]

    visible_messages = client.get(f"/api/members/{restricted_member['id']}/conversations/{group['id']}/messages")
    assert visible_messages.status_code == 200
    assert [message["content"] for message in visible_messages.json()] == ["Read this before you decide."]

    blocked_send = client.post(
        f"/api/members/{restricted_member['id']}/messages",
        json={"conversation_id": group["id"], "content": "I should not be allowed to send this."},
    )
    assert blocked_send.status_code == 403
    assert blocked_send.json()["detail"] == "Member cannot send messages"

    blocked_group_create = client.post(
        f"/api/members/{restricted_member['id']}/conversations/group",
        json={"title": "blocked-group", "member_ids": []},
    )
    assert blocked_group_create.status_code == 403
    assert blocked_group_create.json()["detail"] == "Member cannot create group conversations"

    blocked_leave = client.post(f"/api/members/{restricted_member['id']}/conversations/{group['id']}/leave")
    assert blocked_leave.status_code == 403
    assert blocked_leave.json()["detail"] == "Member cannot leave conversations"