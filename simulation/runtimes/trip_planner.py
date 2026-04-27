from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simulation.runtimes.llm import OpenAICompatibleLLMDecisionClient, resolve_llm_provider_config


NO_TRIP_CHOICE = "NO_TRIP"
NO_MESSAGE_CHOICE = "NO_MESSAGE"


def _messages_to_transcript(messages: list[dict[str, Any]]) -> str:
	if not messages:
		return "<no messages>"
	return "\n".join(f"{message['sender_id']}: {message['content']}" for message in messages)


def _normalize_trip_message(response: str | None, *, fallback: str) -> str | None:
	if response is None:
		return None
	message = " ".join(str(response).strip().split())
	if not message:
		return None
	normalized_choice = "".join(character for character in message.lower() if character.isalnum())
	if normalized_choice in {"nomessage", "pass", "wait", "skip", "stayquiet", "staysilent"}:
		return None
	return message or fallback


def _normalize_trip_choice(response: str, destination_options: list[str]) -> str:
	lower_response = response.strip().lower()
	if not lower_response:
		return NO_TRIP_CHOICE
	if any(marker in lower_response for marker in ["no trip", "dont travel", "don't travel", "skip the trip", "stay home"]):
		return NO_TRIP_CHOICE
	for destination in destination_options:
		if destination.lower() in lower_response:
			return destination
	return NO_TRIP_CHOICE


@dataclass(slots=True)
class TripFriendPersona:
	name: str
	traits: list[str]
	budget_notes: str
	travel_hopes: str
	worries: str
	hard_constraints: list[str] = field(default_factory=list)

	def as_private_brief(self) -> str:
		constraints = "; ".join(self.hard_constraints) if self.hard_constraints else "None"
		traits = ", ".join(self.traits)
		return (
			f"Your planning profile for the friends trip simulation:\n"
			f"- Name: {self.name}\n"
			f"- Traits: {traits}\n"
			f"- Budget notes: {self.budget_notes}\n"
			f"- Travel hopes: {self.travel_hopes}\n"
			f"- Worries: {self.worries}\n"
			f"- Hard constraints: {constraints}\n"
			"Use this brief to guide your messages, but talk naturally like a friend in a group chat."
		)


@dataclass(slots=True)
class ScriptedTripDecisionClient:
	message_responses: dict[str, list[str | None]] = field(default_factory=dict)
	choice_responses: dict[str, list[str]] = field(default_factory=dict)
	calls: list[tuple[str, str]] = field(default_factory=list)

	def decide(self, *, player_name: str, phase: str, system_prompt: str, user_prompt: str) -> str | None:
		self.calls.append((player_name, phase))
		if phase == "message":
			return self.message_responses[player_name].pop(0)
		if phase == "choice":
			return self.choice_responses[player_name].pop(0)
		raise KeyError(f"Unsupported scripted phase: {phase}")

	def close(self) -> None:
		return None


class TripPlannerRuntime:
	def __init__(self, *, persona: TripFriendPersona, member_id: str, gateway: Any, decision_client: Any) -> None:
		self._persona = persona
		self._member_id = member_id
		self._gateway = gateway
		self._decision_client = decision_client

	def decide_message(
		self,
		*,
		group_conversation_id: str,
		private_conversation_id: str,
		destination_options: list[str],
		round_index: int,
		max_rounds: int,
		messages_sent_this_round: int,
	) -> str | None:
		group_messages = self._gateway.list_member_visible_messages(self._member_id, group_conversation_id)
		private_messages = self._gateway.list_member_visible_messages(self._member_id, private_conversation_id)
		response = self._decision_client.decide(
			player_name=self._persona.name,
			phase="message",
			system_prompt=(
				f"You are {self._persona.name}, one friend in a group chat planning a trip together. "
				"You should sound natural, specific, and collaborative. "
				"Keep each reply to one or two short sentences. "
				"Mention budget or emotional concerns when relevant, but stay in character as a real friend. "
				f"If you would rather wait and read more before replying, answer with exactly {NO_MESSAGE_CHOICE}."
			),
			user_prompt=(
				f"Round {round_index + 1} of {max_rounds}.\n"
				f"Messages already sent in this round: {messages_sent_this_round}.\n"
				f"Possible outcomes: {destination_options + [NO_TRIP_CHOICE]}\n\n"
				"Visible group chat transcript:\n"
				f"{_messages_to_transcript(group_messages)}\n\n"
				"Visible private planning brief:\n"
				f"{_messages_to_transcript(private_messages)}\n\n"
				f"Reply with either {NO_MESSAGE_CHOICE} if you would stay quiet for now, or the next message you would send in the group chat."
			),
		)
		return _normalize_trip_message(
			response,
			fallback="I want this to work, but I need an option that feels realistic for everyone.",
		)

	def decide_choice(
		self,
		*,
		group_conversation_id: str,
		private_conversation_id: str,
		destination_options: list[str],
	) -> str:
		group_messages = self._gateway.list_member_visible_messages(self._member_id, group_conversation_id)
		private_messages = self._gateway.list_member_visible_messages(self._member_id, private_conversation_id)
		response = self._decision_client.decide(
			player_name=self._persona.name,
			phase="choice",
			system_prompt=(
				f"You are {self._persona.name} deciding whether the friends should take a trip. "
				"Choose the single destination you support most, or choose NO_TRIP if travel should not happen. "
				"Reply with exactly one destination name or NO_TRIP."
			),
			user_prompt=(
				f"Allowed destination choices: {destination_options}\n"
				f"Allowed no-travel choice: {NO_TRIP_CHOICE}\n\n"
				"Visible group chat transcript:\n"
				f"{_messages_to_transcript(group_messages)}\n\n"
				"Visible private planning brief:\n"
				f"{_messages_to_transcript(private_messages)}"
			),
		)
		return _normalize_trip_choice(response, destination_options)


class TripPlannerRuntimeFactory:
	def __init__(self, decision_client: Any) -> None:
		self._decision_client = decision_client

	@classmethod
	def from_environment(cls, provider: str | None = None) -> TripPlannerRuntimeFactory:
		provider_config = resolve_llm_provider_config(provider)
		return cls(
			OpenAICompatibleLLMDecisionClient(
				api_key=provider_config.api_key,
				base_url=provider_config.base_url,
				model=provider_config.model,
				default_headers=provider_config.headers,
			)
		)

	def create(self, *, persona: TripFriendPersona, member_id: str, gateway: Any) -> TripPlannerRuntime:
		return TripPlannerRuntime(
			persona=persona,
			member_id=member_id,
			gateway=gateway,
			decision_client=self._decision_client,
		)

	def close(self) -> None:
		close = getattr(self._decision_client, "close", None)
		if callable(close):
			close()