from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass, field
from typing import Any

import chatapp
from chatapp.gateway import DEFAULT_API_BASE_URL, HttpChatGateway, RestChatGateway
from chatapp.options import read_messages, send_messages

from simulation.runtimes.trip_planner import NO_TRIP_CHOICE, TripFriendPersona, TripPlannerRuntimeFactory


DEFAULT_TRIP_GROUP_TITLE = "Friends Trip"
DEFAULT_DESTINATION_OPTIONS = ["Lisbon", "Mexico City", "Vancouver"]
DEFAULT_STOP_COMMAND = "stop"


def default_friend_personas() -> list[TripFriendPersona]:
	return [
		TripFriendPersona(
			name="Nina",
			traits=["empathetic", "keeps the group together", "likes cozy plans"],
			budget_notes="Can do one nice trip this season, but not something extravagant.",
			travel_hopes="Wants quality time and a place where everyone can relax.",
			worries="Does not want anyone to feel pressured or left out.",
			hard_constraints=["Needs everyone to feel comfortable with the cost"],
		),
		TripFriendPersona(
			name="Marco",
			traits=["budget-conscious", "practical", "dry sense of humor"],
			budget_notes="Needs flights and lodging to stay reasonable and worries about overspending.",
			travel_hopes="Still wants something memorable if the group can do it affordably.",
			worries="Gets nervous about vague plans and hidden costs.",
			hard_constraints=["Would rather skip the trip than agree to a plan that blows the budget"],
		),
		TripFriendPersona(
			name="Leah",
			traits=["enthusiastic", "spontaneous", "deeply caring"],
			budget_notes="Can stretch a bit for the right destination, but not if others feel strained.",
			travel_hopes="Wants a beautiful destination with food, walking, and good stories.",
			worries="Does not want the trip to become a stressful argument.",
			hard_constraints=["Prefers somewhere lively and easy to explore without a car"],
		),
		TripFriendPersona(
			name="Owen",
			traits=["anxious planner", "detail-oriented", "loyal friend"],
			budget_notes="Needs enough notice to budget and likes plans with predictable costs.",
			travel_hopes="Would enjoy traveling if the destination feels simple and realistic.",
			worries="Gets stuck on logistics and worries the group will commit too fast.",
			hard_constraints=["Needs a destination with straightforward flights and accommodation options"],
		),
	]


@dataclass(slots=True)
class FriendsTripConfig:
	admin_name: str = "Trip Host"
	group_title: str = DEFAULT_TRIP_GROUP_TITLE
	destination_options: list[str] = field(default_factory=lambda: list(DEFAULT_DESTINATION_OPTIONS))
	friends: list[TripFriendPersona] = field(default_factory=default_friend_personas)
	initiator_name: str = "Nina"
	kickoff_message: str = "Hey everyone, can we finally plan a friends trip and see if there is somewhere we can all actually agree on?"
	max_discussion_rounds: int = 3
	host_decision_timeout_minutes: float = 5.0
	discussion_seed: int | None = None
	stop_command: str | None = DEFAULT_STOP_COMMAND
	continue_until_stopped: bool = False
	llm_provider: str | None = None
	action_delay_seconds: float = 0.0


@dataclass(slots=True)
class FriendsTripSimulationResult:
	admin_member: dict[str, Any]
	friends: list[dict[str, Any]]
	group_conversation: dict[str, Any]
	private_conversations: dict[str, dict[str, Any]]
	preferences_by_round: list[dict[str, str]]
	final_choice: str
	consensus_reached: bool
	stopped_early: bool = False
	stop_requested_by_member_id: str | None = None


class FriendsTripSimulationEngine:
	def __init__(
		self,
		gateway: RestChatGateway,
		*,
		runtime_factory: TripPlannerRuntimeFactory | None = None,
	) -> None:
		self._gateway = gateway
		self._runtime_factory = runtime_factory
		self._owned_runtime_factory: TripPlannerRuntimeFactory | None = None

	def run(self, config: FriendsTripConfig) -> FriendsTripSimulationResult:
		if not config.friends:
			raise ValueError("Trip simulation requires at least one friend")
		friend_names = [friend.name for friend in config.friends]
		if config.initiator_name not in friend_names:
			raise ValueError("Trip initiator must be one of the friend personas")

		server = chatapp.init_server(gateway=self._gateway)
		admin = server.add_member(name=config.admin_name, runtime_type="human", member_type="admin")
		friends = [
			server.add_member(
				name=persona.name,
				runtime_type="llm",
				member_type="user_regular",
				functionalities=[send_messages, read_messages],
				config={"simulation_runtime": "trip_planner"},
			)
			for persona in config.friends
		]
		friends_by_name = {friend.display_name: friend for friend in friends}
		group_conversation = server.open_session(title=config.group_title, owner=admin, members=friends)
		self._sleep(config.action_delay_seconds)

		private_conversations = {
			friend.display_name: admin.start_direct_chat(
				title=f"{config.admin_name} and {friend.display_name}",
				members=[friend],
			)
			for friend in friends
		}
		self._sleep(config.action_delay_seconds)

		runtimes = self._build_runtimes(config, friends)
		for persona in config.friends:
			admin.send_message(private_conversations[persona.name], persona.as_private_brief())
			self._sleep(config.action_delay_seconds)

		friends_by_name[config.initiator_name].send_message(group_conversation, config.kickoff_message)
		self._sleep(config.action_delay_seconds)

		preferences_by_round: list[dict[str, str]] = []
		final_choice = NO_TRIP_CHOICE
		consensus_reached = False
		rng = random.Random(config.discussion_seed)
		stop_requesting_member_id = self._find_stop_requesting_member_id(
			conversation_id=group_conversation.id,
			stop_command=config.stop_command,
		)
		if stop_requesting_member_id is not None:
			return self._build_result(
				admin=admin,
				friends=friends,
				group_conversation=group_conversation,
				private_conversations=private_conversations,
				preferences_by_round=preferences_by_round,
				final_choice=final_choice,
				consensus_reached=consensus_reached,
				stopped_early=True,
				stop_requested_by_member_id=stop_requesting_member_id,
			)

		round_index = 0
		while config.continue_until_stopped or round_index < config.max_discussion_rounds:
			available_speakers = [persona.name for persona in config.friends]
			messages_sent_this_round = 0

			while available_speakers:
				stop_requesting_member_id = self._find_stop_requesting_member_id(
					conversation_id=group_conversation.id,
					stop_command=config.stop_command,
				)
				if stop_requesting_member_id is not None:
					return self._build_result(
						admin=admin,
						friends=friends,
						group_conversation=group_conversation,
						private_conversations=private_conversations,
						preferences_by_round=preferences_by_round,
						final_choice=final_choice,
						consensus_reached=consensus_reached,
						stopped_early=True,
						stop_requested_by_member_id=stop_requesting_member_id,
					)

				candidate_names = list(available_speakers)
				rng.shuffle(candidate_names)
				next_speaker: tuple[str, str] | None = None

				for persona_name in candidate_names:
					message = runtimes[persona_name].decide_message(
						group_conversation_id=group_conversation.id,
						private_conversation_id=private_conversations[persona_name].id,
						destination_options=config.destination_options,
						round_index=round_index,
						max_rounds=max(config.max_discussion_rounds, round_index + 1),
						messages_sent_this_round=messages_sent_this_round,
					)
					if message is None:
						continue
					next_speaker = (persona_name, message)
					break

				if next_speaker is None:
					break

				speaker_name, message = next_speaker
				friends_by_name[speaker_name].send_message(group_conversation, message)
				available_speakers.remove(speaker_name)
				messages_sent_this_round += 1
				self._sleep(config.action_delay_seconds)
				stop_requesting_member_id = self._find_stop_requesting_member_id(
					conversation_id=group_conversation.id,
					stop_command=config.stop_command,
				)
				if stop_requesting_member_id is not None:
					return self._build_result(
						admin=admin,
						friends=friends,
						group_conversation=group_conversation,
						private_conversations=private_conversations,
						preferences_by_round=preferences_by_round,
						final_choice=final_choice,
						consensus_reached=consensus_reached,
						stopped_early=True,
						stop_requested_by_member_id=stop_requesting_member_id,
					)

			stop_requesting_member_id = self._find_stop_requesting_member_id(
				conversation_id=group_conversation.id,
				stop_command=config.stop_command,
			)
			if stop_requesting_member_id is not None:
				return self._build_result(
					admin=admin,
					friends=friends,
					group_conversation=group_conversation,
					private_conversations=private_conversations,
					preferences_by_round=preferences_by_round,
					final_choice=final_choice,
					consensus_reached=consensus_reached,
					stopped_early=True,
					stop_requested_by_member_id=stop_requesting_member_id,
				)

			preferences = {
				persona.name: runtimes[persona.name].decide_choice(
					group_conversation_id=group_conversation.id,
					private_conversation_id=private_conversations[persona.name].id,
					destination_options=config.destination_options,
				)
				for persona in config.friends
			}
			preferences_by_round.append(preferences)
			if len(set(preferences.values())) == 1:
				final_choice = next(iter(preferences.values()))
				consensus_reached = True
			elif not config.continue_until_stopped:
				final_choice = NO_TRIP_CHOICE

			round_index += 1
			if not config.continue_until_stopped and consensus_reached:
				break

		if config.continue_until_stopped:
			return self._build_result(
				admin=admin,
				friends=friends,
				group_conversation=group_conversation,
				private_conversations=private_conversations,
				preferences_by_round=preferences_by_round,
				final_choice=final_choice,
				consensus_reached=consensus_reached,
			)

		if not consensus_reached:
			final_choice = NO_TRIP_CHOICE
			admin.send_message(
				group_conversation,
				(
					f"It has been about {config.host_decision_timeout_minutes:g} minutes and the group still has not fully aligned, "
					"so the default outcome is no trip."
				),
			)
			self._sleep(config.action_delay_seconds)

		admin.send_message(
			group_conversation,
			self._format_final_message(final_choice, timeout_minutes=config.host_decision_timeout_minutes),
		)

		return self._build_result(
			admin=admin,
			friends=friends,
			group_conversation=group_conversation,
			private_conversations=private_conversations,
			preferences_by_round=preferences_by_round,
			final_choice=final_choice,
			consensus_reached=consensus_reached,
		)

	def close(self) -> None:
		if self._owned_runtime_factory is None:
			return
		self._owned_runtime_factory.close()
		self._owned_runtime_factory = None

	def _build_runtimes(
		self,
		config: FriendsTripConfig,
		friends: list[chatapp.ChatMember],
	) -> dict[str, Any]:
		if self._runtime_factory is not None:
			factory = self._runtime_factory
		else:
			if self._owned_runtime_factory is None:
				self._owned_runtime_factory = TripPlannerRuntimeFactory.from_environment(config.llm_provider)
			factory = self._owned_runtime_factory
		personas_by_name = {persona.name: persona for persona in config.friends}
		return {
			friend.display_name: factory.create(
				persona=personas_by_name[friend.display_name],
				member_id=friend.id,
				gateway=self._gateway,
			)
			for friend in friends
		}

	def _build_result(
		self,
		*,
		admin: chatapp.ChatMember,
		friends: list[chatapp.ChatMember],
		group_conversation: chatapp.ChatConversation,
		private_conversations: dict[str, chatapp.ChatConversation],
		preferences_by_round: list[dict[str, str]],
		final_choice: str,
		consensus_reached: bool,
		stopped_early: bool = False,
		stop_requested_by_member_id: str | None = None,
	) -> FriendsTripSimulationResult:
		return FriendsTripSimulationResult(
			admin_member=admin.payload,
			friends=[friend.payload for friend in friends],
			group_conversation=group_conversation.payload,
			private_conversations={name: conversation.payload for name, conversation in private_conversations.items()},
			preferences_by_round=preferences_by_round,
			final_choice=final_choice,
			consensus_reached=consensus_reached,
			stopped_early=stopped_early,
			stop_requested_by_member_id=stop_requested_by_member_id,
		)

	def _find_stop_requesting_member_id(
		self,
		*,
		conversation_id: str,
		stop_command: str | None,
	) -> str | None:
		if stop_command is None:
			return None
		normalized_stop_command = stop_command.strip().casefold()
		if not normalized_stop_command:
			return None
		for message in reversed(self._gateway.list_conversation_messages(conversation_id)):
			if message["content"].strip().casefold() == normalized_stop_command:
				return str(message["sender_id"])
		return None

	@staticmethod
	def _format_final_message(final_choice: str, *, timeout_minutes: float) -> str:
		if final_choice == NO_TRIP_CHOICE:
			return (
				"Final decision: no trip for now. "
				"The group cares more about staying comfortable and within budget than forcing a plan."
			)
		return (
			f"Final decision after about {timeout_minutes:g} minutes: the group is going to {final_choice}. "
			"Everyone can now move on to timing and logistics."
		)

	@staticmethod
	def _sleep(seconds: float) -> None:
		if seconds > 0:
			time.sleep(seconds)


def build_http_trip_engine(
	*,
	base_url: str = DEFAULT_API_BASE_URL,
	timeout: float = 10.0,
	llm_provider: str | None = None,
) -> FriendsTripSimulationEngine:
	return FriendsTripSimulationEngine(
		HttpChatGateway(base_url=base_url, timeout=timeout),
		runtime_factory=TripPlannerRuntimeFactory.from_environment(llm_provider),
	)


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Run a live friends trip-planning simulation through the chat API.")
	parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL, help="Base URL of the running FastAPI app.")
	parser.add_argument("--admin-name", default="Trip Host")
	parser.add_argument("--group-title", default=DEFAULT_TRIP_GROUP_TITLE)
	parser.add_argument("--initiator", default="Nina", help="Friend who starts the trip-planning chat.")
	parser.add_argument(
		"--destinations",
		nargs="+",
		default=list(DEFAULT_DESTINATION_OPTIONS),
		help="Destination options the group can consider.",
	)
	parser.add_argument("--max-rounds", type=int, default=2, help="Maximum number of discussion rounds before auto-finishing. Ignored unless --auto-finish is set.")
	parser.add_argument(
		"--llm-provider",
		choices=["openai", "primeintellect"],
		help="LLM provider for friend decisions. Defaults to Prime Intellect when PRIME_API_KEY is present, otherwise OpenAI.",
	)
	parser.add_argument("--discussion-seed", type=int, default=None, help="Optional random seed for turn-taking during each discussion round.")
	parser.add_argument("--stop-command", default=DEFAULT_STOP_COMMAND, help="Exact group-chat message that stops the simulation when posted by any participant, including the admin.")
	parser.add_argument("--auto-finish", action="store_true", help="Let the host stop the simulation automatically using the round limit and final decision logic.")
	parser.add_argument("--delay", type=float, default=1.5, help="Seconds to wait between actions so the TUI can follow the conversation.")
	parser.add_argument("--no-delay", action="store_true", help="Run without pauses between actions.")
	return parser.parse_args()


def main() -> None:
	args = parse_args()
	gateway = HttpChatGateway(base_url=args.api_base_url)
	engine = FriendsTripSimulationEngine(gateway)
	try:
		try:
			result = engine.run(
				FriendsTripConfig(
					admin_name=args.admin_name,
					group_title=args.group_title,
					initiator_name=args.initiator,
					destination_options=list(args.destinations),
					max_discussion_rounds=args.max_rounds,
					discussion_seed=args.discussion_seed,
					stop_command=args.stop_command,
					continue_until_stopped=not args.auto_finish,
					llm_provider=args.llm_provider,
					action_delay_seconds=0.0 if args.no_delay else args.delay,
				)
			)
		except RuntimeError as exc:
			raise SystemExit(str(exc)) from exc
		print(f"Created admin: {result.admin_member['display_name']} ({result.admin_member['id']})")
		for friend in result.friends:
			print(f"Created friend: {friend['display_name']} ({friend['id']})")
		print(f"Group conversation: {result.group_conversation['title']} ({result.group_conversation['id']})")
		for friend_name, conversation in result.private_conversations.items():
			print(f"Private conversation for {friend_name}: {conversation['id']}")
		print(f"Preferences by round: {result.preferences_by_round}")
		print(f"Final decision: {result.final_choice}")
		print(f"Consensus reached: {result.consensus_reached}")
		print(f"Stopped early: {result.stopped_early}")
		if result.stop_requested_by_member_id is not None:
			print(f"Stop requested by member: {result.stop_requested_by_member_id}")
	finally:
		engine.close()
		gateway.close()


if __name__ == "__main__":
	main()