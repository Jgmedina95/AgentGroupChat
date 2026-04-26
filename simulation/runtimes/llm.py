from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app_env import load_environment
from simulation.runtimes.rule_based import choose_clue


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"

DEFAULT_PRIME_BASE_URL = "https://api.pinference.ai/api/v1"
DEFAULT_PRIME_MODEL = "meta-llama/llama-3.3-70b-instruct"


def _get_openai_base_url() -> str:
	return os.getenv("AGENT_CHAT_OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)


def _get_openai_model() -> str:
	return os.getenv("AGENT_CHAT_OPENAI_MODEL") or os.getenv("AGENT_CHAT_LLM_MODEL", DEFAULT_OPENAI_MODEL)


def _get_openai_api_key() -> str | None:
	return os.getenv("AGENT_CHAT_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")


def _get_prime_base_url() -> str:
	return os.getenv("AGENT_CHAT_PRIME_BASE_URL", DEFAULT_PRIME_BASE_URL)


def _get_prime_model() -> str:
	return os.getenv("AGENT_CHAT_PRIME_MODEL") or os.getenv("AGENT_CHAT_LLM_MODEL") or DEFAULT_PRIME_MODEL


def _get_prime_api_key() -> str | None:
	return os.getenv("PRIME_API_KEY")


def _get_prime_team_id() -> str | None:
	return os.getenv("AGENT_CHAT_PRIME_TEAM_ID") or os.getenv("PRIME_TEAM_ID")


def _messages_to_transcript(messages: list[dict[str, Any]]) -> str:
	if not messages:
		return "<no messages>"
	return "\n".join(f"{message['sender_id']}: {message['content']}" for message in messages)


def _extract_secret_word(private_messages: list[dict[str, Any]]) -> str | None:
	for message in reversed(private_messages):
		content = message["content"].strip()
		if content.lower().startswith("your secret word is:"):
			return content.split(":", 1)[1].strip()
	return None


def _normalize_ready_response(response: str, ready_text: str) -> str:
	if ready_text.lower() in response.lower():
		return ready_text
	return ready_text


def _normalize_clue_response(response: str, secret_word: str | None) -> str:
	match = re.search(r"[A-Za-z][A-Za-z-]*", response)
	chooser = random.Random(0)
	if match is None:
		return choose_clue(secret_word or "hint", chooser)
	clue = match.group(0)
	if secret_word and clue.lower() == secret_word.lower():
		return choose_clue(secret_word, chooser)
	return clue.lower()


def _normalize_vote_response(response: str, allowed_players: list[str], current_player_name: str) -> str:
	normalized_lookup = {player_name.lower(): player_name for player_name in allowed_players if player_name != current_player_name}
	lower_response = response.lower()
	for player_name_lower, player_name in normalized_lookup.items():
		if player_name_lower in lower_response:
			return player_name
	return next(iter(normalized_lookup.values()), current_player_name)


@dataclass(slots=True)
class LLMProviderConfig:
	provider: str
	api_key: str
	base_url: str
	model: str
	headers: dict[str, str] = field(default_factory=dict)


def resolve_llm_provider_config(provider: str | None = None) -> LLMProviderConfig:
	load_environment()
	resolved_provider = provider or os.getenv("AGENT_CHAT_LLM_PROVIDER")
	prime_api_key = _get_prime_api_key()
	openai_api_key = _get_openai_api_key()
	if resolved_provider is None:
		if prime_api_key:
			resolved_provider = "primeintellect"
		elif openai_api_key:
			resolved_provider = "openai"
		else:
			raise RuntimeError(
				"LLM player runtime requires PRIME_API_KEY for Prime Intellect or AGENT_CHAT_LLM_API_KEY/OPENAI_API_KEY for OpenAI"
			)

	if resolved_provider == "primeintellect":
		if not prime_api_key:
			raise RuntimeError("Prime Intellect runtime requires PRIME_API_KEY to be set")
		headers: dict[str, str] = {}
		prime_team_id = _get_prime_team_id()
		if prime_team_id:
			headers["X-Prime-Team-ID"] = prime_team_id
		return LLMProviderConfig(
			provider="primeintellect",
			api_key=prime_api_key,
			base_url=_get_prime_base_url(),
			model=_get_prime_model(),
			headers=headers,
		)

	if resolved_provider == "openai":
		if not openai_api_key:
			raise RuntimeError("OpenAI runtime requires AGENT_CHAT_LLM_API_KEY or OPENAI_API_KEY to be set")
		return LLMProviderConfig(
			provider="openai",
			api_key=openai_api_key,
			base_url=_get_openai_base_url(),
			model=_get_openai_model(),
		)

	raise RuntimeError(f"Unsupported LLM provider: {resolved_provider}")


class OpenAICompatibleLLMDecisionClient:
	def __init__(
		self,
		*,
		api_key: str,
		model: str,
		base_url: str,
		timeout: float = 30.0,
		temperature: float = 0.2,
		default_headers: dict[str, str] | None = None,
	) -> None:
		self._model = model
		self._temperature = temperature
		headers = {
			"Authorization": f"Bearer {api_key}",
			"Content-Type": "application/json",
		}
		if default_headers:
			headers.update(default_headers)
		self._client = httpx.Client(
			base_url=base_url.rstrip("/"),
			timeout=timeout,
			headers=headers,
		)

	def decide(self, *, player_name: str, phase: str, system_prompt: str, user_prompt: str) -> str:
		response = self._client.post(
			"/chat/completions",
			json={
				"model": self._model,
				"temperature": self._temperature,
				"messages": [
					{"role": "system", "content": system_prompt},
					{"role": "user", "content": user_prompt},
				],
			},
		)
		if response.status_code >= 400:
			self._raise_llm_error(response, player_name, phase)
		payload = response.json()
		try:
			return payload["choices"][0]["message"]["content"].strip()
		except (KeyError, IndexError, TypeError) as exc:
			raise RuntimeError(f"Invalid LLM response payload for {player_name} during {phase}") from exc

	def close(self) -> None:
		self._client.close()

	def _raise_llm_error(self, response: httpx.Response, player_name: str, phase: str) -> None:
		try:
			payload = response.json()
		except ValueError:
			response.raise_for_status()
		message = payload.get("error", {}).get("message") or payload.get("detail") or response.text
		if response.status_code == 404 and "No allowed providers are available for the selected model" in str(message):
			raise RuntimeError(
				f"The configured model '{self._model}' is not currently available for this Prime Intellect account/provider combination. "
				f"Set AGENT_CHAT_PRIME_MODEL in .env to one of the models returned by GET {self._client.base_url}models."
			)
		raise RuntimeError(f"LLM request failed for {player_name} during {phase}: {message}")


@dataclass(slots=True)
class ScriptedLLMDecisionClient:
	ready_responses: dict[str, str] = field(default_factory=dict)
	clue_responses: dict[str, str] = field(default_factory=dict)
	vote_responses: dict[str, str] = field(default_factory=dict)
	calls: list[tuple[str, str]] = field(default_factory=list)

	def decide(self, *, player_name: str, phase: str, system_prompt: str, user_prompt: str) -> str:
		self.calls.append((player_name, phase))
		response_map = {
			"ready": self.ready_responses,
			"clue": self.clue_responses,
			"vote": self.vote_responses,
		}
		return response_map[phase][player_name]

	def close(self) -> None:
		return None


class LLMPlayerRuntime:
	def __init__(self, *, player_name: str, member_id: str, gateway: Any, decision_client: Any) -> None:
		self._player_name = player_name
		self._member_id = member_id
		self._gateway = gateway
		self._decision_client = decision_client

	def decide_ready(self, *, group_conversation_id: str, ready_text: str) -> str:
		group_messages = self._gateway.list_member_visible_messages(self._member_id, group_conversation_id)
		response = self._decision_client.decide(
			player_name=self._player_name,
			phase="ready",
			system_prompt=(
				f"You are {self._player_name} in an Impostor game. "
				f"Reply with exactly '{ready_text}' when the group rules are clear."
			),
			user_prompt=(
				"Visible group chat transcript:\n"
				f"{_messages_to_transcript(group_messages)}\n\n"
				f"Respond with exactly: {ready_text}"
			),
		)
		return _normalize_ready_response(response, ready_text)

	def decide_clue(self, *, group_conversation_id: str, private_conversation_id: str) -> str:
		group_messages = self._gateway.list_member_visible_messages(self._member_id, group_conversation_id)
		private_messages = self._gateway.list_member_visible_messages(self._member_id, private_conversation_id)
		secret_word = _extract_secret_word(private_messages)
		response = self._decision_client.decide(
			player_name=self._player_name,
			phase="clue",
			system_prompt=(
				f"You are {self._player_name} in an Impostor game. "
				"Use only the messages visible in your own chat interface. "
				"Reply with exactly one clue word and do not reveal your secret word."
			),
			user_prompt=(
				"Visible group chat transcript:\n"
				f"{_messages_to_transcript(group_messages)}\n\n"
				"Visible private chat transcript:\n"
				f"{_messages_to_transcript(private_messages)}\n\n"
				"Reply with one short clue word only."
			),
		)
		return _normalize_clue_response(response, secret_word)

	def decide_vote(
		self,
		*,
		group_conversation_id: str,
		private_conversation_id: str,
		player_names: list[str],
	) -> str:
		group_messages = self._gateway.list_member_visible_messages(self._member_id, group_conversation_id)
		private_messages = self._gateway.list_member_visible_messages(self._member_id, private_conversation_id)
		response = self._decision_client.decide(
			player_name=self._player_name,
			phase="vote",
			system_prompt=(
				f"You are {self._player_name} in an Impostor game. "
				"Use only your visible chat history. "
				"Vote for the player most likely to have a different word. "
				"Reply with exactly one allowed player name."
			),
			user_prompt=(
				"Visible group chat transcript:\n"
				f"{_messages_to_transcript(group_messages)}\n\n"
				"Visible private chat transcript:\n"
				f"{_messages_to_transcript(private_messages)}\n\n"
				f"Allowed player names: {[name for name in player_names if name != self._player_name]}"
			),
		)
		return _normalize_vote_response(response, player_names, self._player_name)


class LLMPlayerRuntimeFactory:
	def __init__(self, decision_client: Any) -> None:
		self._decision_client = decision_client

	@classmethod
	def from_environment(cls, provider: str | None = None) -> LLMPlayerRuntimeFactory:
		provider_config = resolve_llm_provider_config(provider)
		return cls(
			OpenAICompatibleLLMDecisionClient(
				api_key=provider_config.api_key,
				base_url=provider_config.base_url,
				model=provider_config.model,
				default_headers=provider_config.headers,
			)
		)

	def create(self, *, player_name: str, member_id: str, gateway: Any) -> LLMPlayerRuntime:
		return LLMPlayerRuntime(
			player_name=player_name,
			member_id=member_id,
			gateway=gateway,
			decision_client=self._decision_client,
		)

	def close(self) -> None:
		close = getattr(self._decision_client, "close", None)
		if callable(close):
			close()
