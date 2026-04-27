from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import chatapp
import api.websockets as websocket_module
from chatapp.options import pause_group_chat, read_messages, resume_group_chat, send_messages
from app_env import load_environment
from db.session import create_connection, get_db, init_db
from main import app
from simulation.engine import ImpostorGameConfig, ImpostorSimulationEngine, TestClientChatGateway
from simulation.runtimes.llm import (
    LLMPlayerRuntimeFactory,
    ScriptedLLMDecisionClient,
    resolve_llm_provider_config,
)


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


def test_impostor_engine_runs_one_scripted_round(client: TestClient) -> None:
    gateway = TestClientChatGateway(client)
    engine = ImpostorSimulationEngine(gateway)

    result = engine.run(
        ImpostorGameConfig(
            admin_name="Admin",
            player_names=["Player 1", "Player 2", "Player 3", "Player 4"],
            shared_word="apple",
            impostor_word="pear",
            impostor_player_name="Player 4",
            clue_order=["Player 2", "Player 4", "Player 1", "Player 3"],
            ready_text="Ready",
            random_seed=7,
        )
    )

    assert result.admin_member["member_type"] == "admin"
    assert len(result.players) == 4
    assert len(result.private_conversations) == 4
    assert result.group_conversation["messages_paused"] is False
    assert result.eliminated_player_name == "Player 4"
    assert result.impostor_player_name == "Player 4"
    assert result.impostor_eliminated is True

    group_messages = gateway.list_conversation_messages(result.group_conversation["id"])
    group_contents = [message["content"] for message in group_messages]
    assert any("Rules of the Game" in content for content in group_contents)
    assert group_contents.count("Ready") == 4
    assert any("Group chat is temporarily paused while private words are assigned." == content for content in group_contents)
    assert any("Round 1 begins now." == content for content in group_contents)
    assert any("Player 2 clue:" in content for content in group_contents)
    assert any("Player 4 clue:" in content for content in group_contents)
    assert any("Vote results:" in content for content in group_contents)
    assert any("Impostor eliminated: Player 4." == content for content in group_contents)

    for player_name, conversation in result.private_conversations.items():
        private_messages = gateway.list_member_visible_messages(result.player_ids_by_name[player_name], conversation["id"])
        private_contents = [message["content"] for message in private_messages]
        assert any(content.startswith("Your secret word is:") for content in private_contents)
        assert any(content.startswith("Cast your vote") for content in private_contents)


def test_impostor_engine_can_use_llm_player_runtime(client: TestClient) -> None:
    gateway = TestClientChatGateway(client)
    decision_client = ScriptedLLMDecisionClient(
        ready_responses={
            "Player 1": "Ready",
            "Player 2": "Ready",
            "Player 3": "Ready",
            "Player 4": "Ready",
        },
        clue_responses={
            "Player 1": "orchard",
            "Player 2": "cider",
            "Player 3": "crisp",
            "Player 4": "green",
        },
        vote_responses={
            "Player 1": "Player 4",
            "Player 2": "Player 4",
            "Player 3": "Player 4",
            "Player 4": "Player 1",
        },
    )
    engine = ImpostorSimulationEngine(
        gateway,
        llm_runtime_factory=LLMPlayerRuntimeFactory(decision_client),
    )

    result = engine.run(
        ImpostorGameConfig(
            admin_name="Admin",
            player_names=["Player 1", "Player 2", "Player 3", "Player 4"],
            shared_word="apple",
            impostor_word="pear",
            impostor_player_name="Player 4",
            clue_order=["Player 1", "Player 2", "Player 3", "Player 4"],
            player_runtime_type="llm",
        )
    )

    assert decision_client.calls == [
        ("Player 1", "ready"),
        ("Player 2", "ready"),
        ("Player 3", "ready"),
        ("Player 4", "ready"),
        ("Player 1", "clue"),
        ("Player 2", "clue"),
        ("Player 3", "clue"),
        ("Player 4", "clue"),
        ("Player 1", "vote"),
        ("Player 2", "vote"),
        ("Player 3", "vote"),
        ("Player 4", "vote"),
    ]

    group_messages = gateway.list_conversation_messages(result.group_conversation["id"])
    group_contents = [message["content"] for message in group_messages]
    assert "Player 1 clue: orchard" in group_contents
    assert "Player 2 clue: cider" in group_contents
    assert "Player 3 clue: crisp" in group_contents
    assert "Player 4 clue: green" in group_contents


def test_chatapp_facade_supports_member_actions(client: TestClient) -> None:
    gateway = TestClientChatGateway(client)
    server = chatapp.init_server(gateway=gateway)

    admin = server.add_member(
        name="Admin",
        runtime_type="human",
        member_type="admin",
        functionalities=[send_messages, read_messages, pause_group_chat, resume_group_chat],
    )
    player = server.add_member(
        name="Claudia",
        runtime_type="llm",
        member_type="user_regular",
        functionalities=[send_messages, read_messages],
    )

    group = server.open_session(title="Facade Session", owner=admin)
    group.add_member(acting_member=admin, member=player)
    admin.send_message(group, "hello from admin")

    visible_messages = player.read_messages(group)
    assert [message["content"] for message in visible_messages] == ["hello from admin"]

    admin.pause_group_chat(group, "Hold please")
    assert group.messages_paused is True

    with pytest.raises(Exception):
        player.send_message(group, "this should fail while paused")

    admin.resume_group_chat(group)
    assert group.messages_paused is False

    player.send_message(group, "back again")
    group_messages = gateway.list_conversation_messages(group.id)
    assert [message["content"] for message in group_messages] == ["hello from admin", "back again"]


def test_primeintellect_provider_config_uses_prime_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRIME_API_KEY", "prime-key")
    monkeypatch.setenv("AGENT_CHAT_PRIME_TEAM_ID", "team-123")
    monkeypatch.delenv("AGENT_CHAT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    provider_config = resolve_llm_provider_config("primeintellect")

    assert provider_config.provider == "primeintellect"
    assert provider_config.api_key == "prime-key"
    assert provider_config.base_url == "https://api.pinference.ai/api/v1"
    assert provider_config.model == "meta-llama/llama-3.3-70b-instruct"
    assert provider_config.headers == {"X-Prime-Team-ID": "team-123"}


def test_auto_provider_prefers_primeintellect_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRIME_API_KEY", "prime-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    provider_config = resolve_llm_provider_config()

    assert provider_config.provider == "primeintellect"
    assert provider_config.api_key == "prime-key"


def test_provider_config_loads_prime_key_from_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("PRIME_API_KEY=prime-from-dotenv\nAGENT_CHAT_PRIME_TEAM_ID=team-from-dotenv\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PRIME_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_CHAT_PRIME_TEAM_ID", raising=False)
    monkeypatch.delenv("PRIME_TEAM_ID", raising=False)
    monkeypatch.delenv("AGENT_CHAT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    load_environment.cache_clear()

    provider_config = resolve_llm_provider_config()

    assert provider_config.provider == "primeintellect"
    assert provider_config.api_key == "prime-from-dotenv"
    assert provider_config.headers == {"X-Prime-Team-ID": "team-from-dotenv"}
