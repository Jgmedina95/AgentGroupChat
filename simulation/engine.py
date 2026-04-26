from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from simulation.runtimes.llm import LLMPlayerRuntimeFactory
from simulation.runtimes.rule_based import build_vote_map, choose_clue


DEFAULT_API_BASE_URL = "http://localhost:8000"
DEFAULT_GROUP_TITLE = "Impostor"
DEFAULT_READY_TEXT = "Ready"
DEFAULT_ASSIGNMENT_NOTICE = "Group chat is temporarily paused while private words are assigned."
DEFAULT_PLAYER_RUNTIME_TYPE = "rule_based"
DEFAULT_ADMIN_RUNTIME_TYPE = "human"

RULES_TEXT = """Rules of the Game:\nAll the players will receive one of two words.\nNone of the players know what the other word is.\n3 players will have the same word, the other one doesnt.\nThen each player will say one word related to the word they received.\nAfter each round, players vote for which Player they think has a different word.\nIf correct, other players win. If after two rounds the impostor is not voted out, the impostor wins.\nAnswer with Ready if you are."""


@dataclass(slots=True)
class ImpostorGameConfig:
	admin_name: str = "Admin"
	player_names: list[str] = field(default_factory=lambda: ["Player 1", "Player 2", "Player 3", "Player 4"])
	group_title: str = DEFAULT_GROUP_TITLE
	shared_word: str = "apple"
	impostor_word: str = "pear"
	impostor_player_name: str | None = None
	clue_order: list[str] | None = None
	ready_text: str = DEFAULT_READY_TEXT
	random_seed: int | None = None
	player_runtime_type: str = DEFAULT_PLAYER_RUNTIME_TYPE
	llm_provider: str | None = None
	admin_runtime_type: str = DEFAULT_ADMIN_RUNTIME_TYPE
	action_delay_seconds: float = 0.0


@dataclass(slots=True)
class ImpostorSimulationResult:
	admin_member: dict[str, Any]
	players: list[dict[str, Any]]
	group_conversation: dict[str, Any]
	private_conversations: dict[str, dict[str, Any]]
	player_ids_by_name: dict[str, str]
	assignments_by_player_name: dict[str, str]
	votes_by_player_name: dict[str, str]
	vote_totals: dict[str, int]
	impostor_player_name: str
	eliminated_player_name: str
	impostor_eliminated: bool


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

	def list_conversation_messages(self, conversation_id: str) -> list[dict[str, Any]]:
		response = self._client.get(f"/api/conversations/{conversation_id}/messages")
		response.raise_for_status()
		return response.json()

	def list_member_visible_messages(self, member_id: str, conversation_id: str) -> list[dict[str, Any]]:
		response = self._client.get(f"/api/members/{member_id}/conversations/{conversation_id}/messages")
		response.raise_for_status()
		return response.json()


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


class ImpostorSimulationEngine:
	def __init__(
		self,
		gateway: RestChatGateway,
		*,
		llm_runtime_factory: LLMPlayerRuntimeFactory | None = None,
	) -> None:
		self._gateway = gateway
		self._llm_runtime_factory = llm_runtime_factory
		self._owned_llm_runtime_factory: LLMPlayerRuntimeFactory | None = None

	def run(self, config: ImpostorGameConfig) -> ImpostorSimulationResult:
		if len(config.player_names) != 4:
			raise ValueError("The initial impostor simulation expects exactly four players")

		chooser = random.Random(config.random_seed)
		admin = self._gateway.create_member(
			display_name=config.admin_name,
			runtime_type=config.admin_runtime_type,
			member_type="admin",
		)
		players = [
			self._gateway.create_member(
				display_name=player_name,
				runtime_type=config.player_runtime_type,
				member_type="user_regular",
				config={"simulation_runtime": config.player_runtime_type},
			)
			for player_name in config.player_names
		]
		player_ids_by_name = {player["display_name"]: player["id"] for player in players}

		group_conversation = self._gateway.create_group_conversation(
			admin_member_id=admin["id"],
			title=config.group_title,
			member_ids=[player["id"] for player in players],
		)
		self._sleep(config.action_delay_seconds)

		private_conversations = {
			player_name: self._gateway.create_direct_conversation(
				title=f"{config.admin_name} and {player_name}",
				participant_ids=[admin["id"], player_ids_by_name[player_name]],
			)
			for player_name in config.player_names
		}
		self._sleep(config.action_delay_seconds)
		llm_player_runtimes = self._build_llm_player_runtimes(config, player_ids_by_name)

		self._gateway.post_member_message(
			member_id=admin["id"],
			conversation_id=group_conversation["id"],
			content=RULES_TEXT,
		)

		for player in players:
			player_name = player["display_name"]
			ready_response = config.ready_text
			if llm_player_runtimes is not None:
				ready_response = llm_player_runtimes[player_name].decide_ready(
					group_conversation_id=group_conversation["id"],
					ready_text=config.ready_text,
				)
			self._gateway.post_member_message(
				member_id=player["id"],
				conversation_id=group_conversation["id"],
				content=ready_response,
			)
			self._sleep(config.action_delay_seconds)

		self._gateway.post_member_message(
			member_id=admin["id"],
			conversation_id=group_conversation["id"],
			content=DEFAULT_ASSIGNMENT_NOTICE,
		)
		self._gateway.pause_group_messages(
			admin_member_id=admin["id"],
			conversation_id=group_conversation["id"],
			notice=DEFAULT_ASSIGNMENT_NOTICE,
		)

		impostor_player_name = config.impostor_player_name or chooser.choice(config.player_names)
		assignments_by_player_name = {
			player_name: config.impostor_word if player_name == impostor_player_name else config.shared_word
			for player_name in config.player_names
		}

		for player_name, word in assignments_by_player_name.items():
			self._gateway.post_member_message(
				member_id=admin["id"],
				conversation_id=private_conversations[player_name]["id"],
				content=f"Your secret word is: {word}",
			)
			self._sleep(config.action_delay_seconds)

		group_conversation = self._gateway.resume_group_messages(
			admin_member_id=admin["id"],
			conversation_id=group_conversation["id"],
		)
		self._gateway.post_member_message(
			member_id=admin["id"],
			conversation_id=group_conversation["id"],
			content="Round 1 begins now.",
		)

		clue_order = config.clue_order or chooser.sample(config.player_names, len(config.player_names))
		for player_name in clue_order:
			if llm_player_runtimes is not None:
				clue = llm_player_runtimes[player_name].decide_clue(
					group_conversation_id=group_conversation["id"],
					private_conversation_id=private_conversations[player_name]["id"],
				)
			else:
				clue = choose_clue(assignments_by_player_name[player_name], chooser)
			self._gateway.post_member_message(
				member_id=player_ids_by_name[player_name],
				conversation_id=group_conversation["id"],
				content=f"{player_name} clue: {clue}",
			)
			self._sleep(config.action_delay_seconds)

		votes_by_player_name = build_vote_map(config.player_names, impostor_player_name) if llm_player_runtimes is None else {}
		for player_name in config.player_names:
			self._gateway.post_member_message(
				member_id=admin["id"],
				conversation_id=private_conversations[player_name]["id"],
				content="Cast your vote for the player you think has a different word.",
			)
			if llm_player_runtimes is not None:
				votes_by_player_name[player_name] = llm_player_runtimes[player_name].decide_vote(
					group_conversation_id=group_conversation["id"],
					private_conversation_id=private_conversations[player_name]["id"],
					player_names=config.player_names,
				)
			self._gateway.post_member_message(
				member_id=player_ids_by_name[player_name],
				conversation_id=private_conversations[player_name]["id"],
				content=votes_by_player_name[player_name],
			)
			self._sleep(config.action_delay_seconds)

		vote_totals: dict[str, int] = {player_name: 0 for player_name in config.player_names}
		for vote_target in votes_by_player_name.values():
			vote_totals[vote_target] += 1

		eliminated_player_name = max(
			vote_totals,
			key=lambda player_name: (vote_totals[player_name], player_name == impostor_player_name, player_name),
		)
		vote_summary = ", ".join(
			f"{player_name}={vote_totals[player_name]}" for player_name in sorted(vote_totals)
		)
		self._gateway.post_member_message(
			member_id=admin["id"],
			conversation_id=group_conversation["id"],
			content=f"Vote results: {vote_summary}",
		)

		impostor_eliminated = eliminated_player_name == impostor_player_name
		end_message = (
			f"Impostor eliminated: {eliminated_player_name}."
			if impostor_eliminated
			else f"Impostor survives this round: {impostor_player_name}."
		)
		self._gateway.post_member_message(
			member_id=admin["id"],
			conversation_id=group_conversation["id"],
			content=end_message,
		)

		return ImpostorSimulationResult(
			admin_member=admin,
			players=players,
			group_conversation=group_conversation,
			private_conversations=private_conversations,
			player_ids_by_name=player_ids_by_name,
			assignments_by_player_name=assignments_by_player_name,
			votes_by_player_name=votes_by_player_name,
			vote_totals=vote_totals,
			impostor_player_name=impostor_player_name,
			eliminated_player_name=eliminated_player_name,
			impostor_eliminated=impostor_eliminated,
		)

	@staticmethod
	def _sleep(seconds: float) -> None:
		if seconds > 0:
			time.sleep(seconds)

	def _build_llm_player_runtimes(
		self,
		config: ImpostorGameConfig,
		player_ids_by_name: dict[str, str],
	) -> dict[str, Any] | None:
		if config.player_runtime_type != "llm":
			return None
		if self._llm_runtime_factory is not None:
			factory = self._llm_runtime_factory
		else:
			if self._owned_llm_runtime_factory is None:
				self._owned_llm_runtime_factory = LLMPlayerRuntimeFactory.from_environment(config.llm_provider)
			factory = self._owned_llm_runtime_factory
		return {
			player_name: factory.create(
				player_name=player_name,
				member_id=player_ids_by_name[player_name],
				gateway=self._gateway,
			)
			for player_name in config.player_names
		}

	def close(self) -> None:
		if self._owned_llm_runtime_factory is None:
			return
		self._owned_llm_runtime_factory.close()
		self._owned_llm_runtime_factory = None


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Run one scripted Impostor round through the chat API.")
	parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL, help="Base URL of the running FastAPI app.")
	parser.add_argument("--admin-name", default="Admin")
	parser.add_argument("--player-names", nargs=4, default=["Player 1", "Player 2", "Player 3", "Player 4"])
	parser.add_argument("--group-title", default=DEFAULT_GROUP_TITLE)
	parser.add_argument("--shared-word", default="apple")
	parser.add_argument("--impostor-word", default="pear")
	parser.add_argument("--impostor-player")
	parser.add_argument("--player-runtime", choices=["rule_based", "llm"], default="llm")
	parser.add_argument("--llm-provider", choices=["openai", "primeintellect"], help="LLM provider for player decisions. Defaults to Prime Intellect when PRIME_API_KEY is present, otherwise OpenAI.")
	parser.add_argument("--seed", type=int, default=7)
	parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between actions.")
	parser.add_argument("--no-delay", action="store_true", help="Run without pauses between actions.")
	return parser.parse_args()


def main() -> None:
	args = parse_args()
	gateway = HttpChatGateway(base_url=args.api_base_url)
	engine = ImpostorSimulationEngine(gateway)
	try:
		try:
			result = engine.run(
				ImpostorGameConfig(
					admin_name=args.admin_name,
					player_names=list(args.player_names),
					group_title=args.group_title,
					shared_word=args.shared_word,
					impostor_word=args.impostor_word,
					impostor_player_name=args.impostor_player,
					player_runtime_type=args.player_runtime,
					llm_provider=args.llm_provider,
					random_seed=args.seed,
					action_delay_seconds=0.0 if args.no_delay else args.delay,
				)
			)
		except RuntimeError as exc:
			raise SystemExit(str(exc)) from exc
		print(f"Created admin: {result.admin_member['display_name']} ({result.admin_member['id']})")
		for player in result.players:
			print(f"Created player: {player['display_name']} ({player['id']})")
		print(f"Group conversation: {result.group_conversation['title']} ({result.group_conversation['id']})")
		for player_name, conversation in result.private_conversations.items():
			print(f"Private conversation for {player_name}: {conversation['id']}")
		print(f"Impostor: {result.impostor_player_name}")
		print(f"Eliminated: {result.eliminated_player_name}")
		print(f"Impostor eliminated: {result.impostor_eliminated}")
	finally:
		engine.close()
		gateway.close()


if __name__ == "__main__":
	main()
