from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import api.websockets as websocket_module
from db.session import create_connection, get_db, init_db
from main import app
from simulation.runtimes.trip_planner import ScriptedTripDecisionClient, TripFriendPersona, TripPlannerRuntimeFactory
from simulation.trip_planner import FriendsTripConfig, FriendsTripSimulationEngine, FriendsTripSimulationState
from chatapp.gateway import TestClientChatGateway


class StopInjectingChatGateway(TestClientChatGateway):
	def __init__(
		self,
		client: TestClient,
		*,
		stop_command: str = "stop",
		stop_after_group_messages: int = 2,
		inject_stop_as_admin: bool = False,
	) -> None:
		super().__init__(client)
		self._stop_command = stop_command
		self._stop_after_group_messages = stop_after_group_messages
		self._group_conversation_id: str | None = None
		self._admin_member_id: str | None = None
		self._stop_injected = False
		self._group_message_count = 0
		self._inject_stop_as_admin = inject_stop_as_admin

	def create_group_conversation(
		self,
		*,
		admin_member_id: str,
		title: str,
		member_ids: list[str],
	) -> dict[str, str]:
		conversation = super().create_group_conversation(
			admin_member_id=admin_member_id,
			title=title,
			member_ids=member_ids,
		)
		self._group_conversation_id = conversation["id"]
		self._admin_member_id = admin_member_id
		return conversation

	def post_member_message(self, *, member_id: str, conversation_id: str, content: str) -> dict[str, str]:
		message = super().post_member_message(member_id=member_id, conversation_id=conversation_id, content=content)
		if conversation_id != self._group_conversation_id:
			return message
		if self._stop_injected or content.strip().casefold() == self._stop_command.casefold():
			return message
		self._group_message_count += 1
		if self._group_message_count >= self._stop_after_group_messages:
			stop_member_id = self._admin_member_id if self._inject_stop_as_admin and self._admin_member_id is not None else member_id
			super().post_member_message(member_id=stop_member_id, conversation_id=conversation_id, content=self._stop_command)
			self._stop_injected = True
		return message


def test_trip_planner_state_tracks_round_progress_and_stop() -> None:
	state = FriendsTripSimulationState()
	round_state = state.start_round(["Nina", "Marco"])

	assert round_state.round_index == 0
	assert round_state.available_speakers == ["Nina", "Marco"]
	assert round_state.messages_sent_this_round == 0

	round_state.mark_message_sent("Nina")
	state.record_preferences({"Nina": "Lisbon", "Marco": "Lisbon"})
	state.apply_consensus("Lisbon")
	state.trace_recorder.record(event_type="turn_offered", round_index=0, member_name="Nina")
	state.advance_round()
	state.mark_stop_requested("member-1")
	snapshot = state.to_dict()

	assert round_state.available_speakers == ["Marco"]
	assert round_state.messages_sent_this_round == 1
	assert state.preferences_by_round == [{"Nina": "Lisbon", "Marco": "Lisbon"}]
	assert state.final_choice == "Lisbon"
	assert state.consensus_reached is True
	assert state.round_index == 1
	assert state.active_round is None
	assert state.stopped_early is True
	assert state.stop_requested_by_member_id == "member-1"
	assert snapshot["round_index"] == 1
	assert snapshot["preferences_by_round"] == [{"Nina": "Lisbon", "Marco": "Lisbon"}]
	assert snapshot["active_round"] is None
	assert snapshot["trace_events"][0]["event_type"] == "turn_offered"


def test_trip_planner_reaches_destination_consensus(tmp_path: Path) -> None:
	database_path = tmp_path / "trip-planner.db"
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
			decision_client = ScriptedTripDecisionClient(
				message_responses={
					"Nina": ["I want a trip that feels warm and easy for everyone."],
					"Marco": ["If we keep it affordable, Lisbon sounds realistic to me."],
					"Leah": ["I could get excited about Lisbon if we keep the days relaxed."],
				},
				choice_responses={
					"Nina": ["Lisbon"],
					"Marco": ["Lisbon"],
					"Leah": ["Lisbon"],
				},
			)
			engine = FriendsTripSimulationEngine(
				TestClientChatGateway(client),
				runtime_factory=TripPlannerRuntimeFactory(decision_client),
			)
			config = FriendsTripConfig(
				friends=[
					TripFriendPersona(
						name="Nina",
						traits=["empathetic"],
						budget_notes="Needs a reasonable plan.",
						travel_hopes="Wants time together.",
						worries="Does not want anyone left out.",
					),
					TripFriendPersona(
						name="Marco",
						traits=["budget-conscious"],
						budget_notes="Needs to keep costs down.",
						travel_hopes="Would enjoy a walkable city.",
						worries="Does not want surprise costs.",
					),
					TripFriendPersona(
						name="Leah",
						traits=["enthusiastic"],
						budget_notes="Can flex a little.",
						travel_hopes="Wants a charming destination.",
						worries="Does not want the planning to feel tense.",
					),
				],
				destination_options=["Lisbon", "Montreal"],
				max_discussion_rounds=1,
				discussion_seed=7,
			)

			result = engine.run(config)

			assert result.consensus_reached is True
			assert result.final_choice == "Lisbon"
			assert result.stopped_early is False
			assert result.preferences_by_round == [{"Leah": "Lisbon", "Marco": "Lisbon", "Nina": "Lisbon"}]

			group_messages = engine._gateway.list_conversation_messages(result.group_conversation["id"])
			group_contents = [message["content"] for message in group_messages]
			assert group_contents[0] == config.kickoff_message
			assert any("Lisbon sounds realistic" in content for content in group_contents)
			assert not any("Current preferences are split" in content for content in group_contents)
			assert group_contents[-1] == "Final decision after about 5 minutes: the group is going to Lisbon. Everyone can now move on to timing and logistics."
			message_calls = [call for call in decision_client.calls if call[1] == "message"]
			choice_calls = [call for call in decision_client.calls if call[1] == "choice"]
			assert {player_name for player_name, _phase in message_calls} == {"Nina", "Marco", "Leah"}
			assert choice_calls == [("Nina", "choice"), ("Marco", "choice"), ("Leah", "choice")]
	finally:
		app.dependency_overrides.clear()
		websocket_module.SessionLocal = original_session_local


def test_trip_planner_defaults_to_no_trip_without_unanimity(tmp_path: Path) -> None:
	database_path = tmp_path / "trip-planner-no-trip.db"
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
			decision_client = ScriptedTripDecisionClient(
				message_responses={
					"Nina": ["Lisbon still feels balanced to me.", "NO_MESSAGE"],
					"Marco": ["I can afford Lisbon if we plan ahead.", "NO_MESSAGE"],
					"Leah": ["Lisbon sounds fun and easy.", "NO_MESSAGE"],
					"Owen": ["I still think Vancouver is simpler for me to manage.", "NO_MESSAGE"],
				},
				choice_responses={
					"Nina": ["Lisbon"],
					"Marco": ["Lisbon"],
					"Leah": ["Lisbon"],
					"Owen": ["Vancouver"],
				},
			)
			engine = FriendsTripSimulationEngine(
				TestClientChatGateway(client),
				runtime_factory=TripPlannerRuntimeFactory(decision_client),
			)
			config = FriendsTripConfig(
				friends=[
					TripFriendPersona(
						name="Nina",
						traits=["empathetic"],
						budget_notes="Needs a reasonable plan.",
						travel_hopes="Wants time together.",
						worries="Does not want anyone left out.",
					),
					TripFriendPersona(
						name="Marco",
						traits=["budget-conscious"],
						budget_notes="Needs to keep costs down.",
						travel_hopes="Would enjoy a walkable city.",
						worries="Does not want surprise costs.",
					),
					TripFriendPersona(
						name="Leah",
						traits=["enthusiastic"],
						budget_notes="Can flex a little.",
						travel_hopes="Wants a charming destination.",
						worries="Does not want the planning to feel tense.",
					),
					TripFriendPersona(
						name="Owen",
						traits=["anxious planner"],
						budget_notes="Needs predictable costs.",
						travel_hopes="Would travel if logistics feel simple.",
						worries="Gets stuck on route complexity.",
					),
				],
				destination_options=["Lisbon", "Vancouver"],
				max_discussion_rounds=1,
				discussion_seed=11,
			)

			result = engine.run(config)

			assert result.consensus_reached is False
			assert result.final_choice == "NO_TRIP"
			assert result.stopped_early is False
			assert result.preferences_by_round == [{"Leah": "Lisbon", "Marco": "Lisbon", "Nina": "Lisbon", "Owen": "Vancouver"}]

			group_messages = engine._gateway.list_conversation_messages(result.group_conversation["id"])
			group_contents = [message["content"] for message in group_messages]
			assert not any("Current preferences are split" in content for content in group_contents)
			assert group_contents[-2] == "It has been about 5 minutes and the group still has not fully aligned, so the default outcome is no trip."
			assert group_contents[-1] == "Final decision: no trip for now. The group cares more about staying comfortable and within budget than forcing a plan."
	finally:
		app.dependency_overrides.clear()
		websocket_module.SessionLocal = original_session_local


def test_trip_planner_allows_friends_to_wait_before_replying(tmp_path: Path) -> None:
	database_path = tmp_path / "trip-planner-waiting.db"
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
			decision_client = ScriptedTripDecisionClient(
				message_responses={
					"Nina": ["NO_MESSAGE", "Lisbon is sounding better now that we have budget guardrails."],
					"Marco": [
						"Can we agree to keep the total cost under control before we lock anything in?",
						"Lisbon still works for me if we keep costs predictable.",
					],
					"Leah": ["NO_MESSAGE", "I could get excited about Lisbon if we keep it relaxed and walkable."],
				},
				choice_responses={
					"Nina": ["Montreal", "Lisbon"],
					"Marco": ["Lisbon", "Lisbon"],
					"Leah": ["Montreal", "Lisbon"],
				},
			)
			engine = FriendsTripSimulationEngine(
				TestClientChatGateway(client),
				runtime_factory=TripPlannerRuntimeFactory(decision_client),
			)
			config = FriendsTripConfig(
				friends=[
					TripFriendPersona(
						name="Nina",
						traits=["empathetic"],
						budget_notes="Needs a reasonable plan.",
						travel_hopes="Wants time together.",
						worries="Does not want anyone left out.",
					),
					TripFriendPersona(
						name="Marco",
						traits=["budget-conscious"],
						budget_notes="Needs to keep costs down.",
						travel_hopes="Would enjoy a walkable city.",
						worries="Does not want surprise costs.",
					),
					TripFriendPersona(
						name="Leah",
						traits=["enthusiastic"],
						budget_notes="Can flex a little.",
						travel_hopes="Wants a charming destination.",
						worries="Does not want the planning to feel tense.",
					),
				],
				destination_options=["Lisbon", "Montreal"],
				max_discussion_rounds=2,
				discussion_seed=3,
			)

			result = engine.run(config)

			assert result.consensus_reached is True
			assert result.final_choice == "Lisbon"
			assert result.stopped_early is False
			assert result.preferences_by_round == [
				{"Leah": "Montreal", "Marco": "Lisbon", "Nina": "Montreal"},
				{"Leah": "Lisbon", "Marco": "Lisbon", "Nina": "Lisbon"},
			]

			group_messages = engine._gateway.list_conversation_messages(result.group_conversation["id"])
			group_contents = [message["content"] for message in group_messages]
			assert group_contents[0] == config.kickoff_message
			assert any("keep the total cost under control" in content for content in group_contents)
			assert any("Lisbon is sounding better" in content for content in group_contents)
			assert any("I could get excited about Lisbon" in content for content in group_contents)
			assert decision_client.calls.count(("Nina", "message")) >= 2
			assert decision_client.calls.count(("Leah", "message")) >= 2
	finally:
		app.dependency_overrides.clear()
		websocket_module.SessionLocal = original_session_local


def test_trip_planner_stops_when_group_receives_stop_message(tmp_path: Path) -> None:
	database_path = tmp_path / "trip-planner-stop.db"
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
			gateway = StopInjectingChatGateway(client, stop_after_group_messages=2)
			decision_client = ScriptedTripDecisionClient(
				message_responses={
					"Nina": ["I want a destination that still feels calm and affordable."],
					"Marco": ["Lisbon looks realistic if we keep flights and hotel simple."],
					"Leah": ["I could be into Lisbon if the whole plan stays low-stress."],
				},
				choice_responses={
					"Nina": ["Lisbon"],
					"Marco": ["Lisbon"],
					"Leah": ["Lisbon"],
				},
			)
			engine = FriendsTripSimulationEngine(
				gateway,
				runtime_factory=TripPlannerRuntimeFactory(decision_client),
			)
			config = FriendsTripConfig(
				friends=[
					TripFriendPersona(
						name="Nina",
						traits=["empathetic"],
						budget_notes="Needs a reasonable plan.",
						travel_hopes="Wants time together.",
						worries="Does not want anyone left out.",
					),
					TripFriendPersona(
						name="Marco",
						traits=["budget-conscious"],
						budget_notes="Needs to keep costs down.",
						travel_hopes="Would enjoy a walkable city.",
						worries="Does not want surprise costs.",
					),
					TripFriendPersona(
						name="Leah",
						traits=["enthusiastic"],
						budget_notes="Can flex a little.",
						travel_hopes="Wants a charming destination.",
						worries="Does not want the planning to feel tense.",
					),
				],
				destination_options=["Lisbon", "Montreal"],
				max_discussion_rounds=3,
				discussion_seed=7,
				stop_command="stop",
			)

			result = engine.run(config)

			assert result.stopped_early is True
			assert result.consensus_reached is False
			assert result.final_choice == "NO_TRIP"
			assert result.preferences_by_round == []
			assert result.stop_requested_by_member_id is not None

			group_messages = gateway.list_conversation_messages(result.group_conversation["id"])
			group_contents = [message["content"] for message in group_messages]
			assert group_contents[-1] == "stop"
			assert not any(content.startswith("Final decision:") for content in group_contents)
			assert not any(content.startswith("It has been about 5 minutes") for content in group_contents)
	finally:
		app.dependency_overrides.clear()
		websocket_module.SessionLocal = original_session_local


def test_trip_planner_can_continue_past_round_limit_until_admin_sends_stop(tmp_path: Path) -> None:
	database_path = tmp_path / "trip-planner-admin-stop.db"
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
			gateway = StopInjectingChatGateway(
				client,
				stop_after_group_messages=5,
				inject_stop_as_admin=True,
			)
			decision_client = ScriptedTripDecisionClient(
				message_responses={
					"Nina": [
						"I want us to keep talking until the plan feels good for everyone.",
						"Lisbon still feels promising if nobody is stretching their budget.",
					],
					"Marco": [
						"Let us keep an eye on the total cost before we lock anything in.",
						"I am still okay with Lisbon if we stay disciplined on flights and hotel.",
					],
					"Leah": [
						"I am into Lisbon if it stays relaxed and fun.",
						"I still like Lisbon, but I am happy to keep hearing everyone out.",
					],
				},
				choice_responses={
					"Nina": ["Lisbon"],
					"Marco": ["Lisbon"],
					"Leah": ["Lisbon"],
				},
			)
			engine = FriendsTripSimulationEngine(
				gateway,
				runtime_factory=TripPlannerRuntimeFactory(decision_client),
			)
			config = FriendsTripConfig(
				friends=[
					TripFriendPersona(
						name="Nina",
						traits=["empathetic"],
						budget_notes="Needs a reasonable plan.",
						travel_hopes="Wants time together.",
						worries="Does not want anyone left out.",
					),
					TripFriendPersona(
						name="Marco",
						traits=["budget-conscious"],
						budget_notes="Needs to keep costs down.",
						travel_hopes="Would enjoy a walkable city.",
						worries="Does not want surprise costs.",
					),
					TripFriendPersona(
						name="Leah",
						traits=["enthusiastic"],
						budget_notes="Can flex a little.",
						travel_hopes="Wants a charming destination.",
						worries="Does not want the planning to feel tense.",
					),
				],
				destination_options=["Lisbon", "Montreal"],
				max_discussion_rounds=1,
				discussion_seed=7,
				stop_command="stop",
				continue_until_stopped=True,
			)

			result = engine.run(config)

			assert result.stopped_early is True
			assert result.stop_requested_by_member_id == result.admin_member["id"]
			assert result.consensus_reached is True
			assert result.preferences_by_round == [{"Leah": "Lisbon", "Marco": "Lisbon", "Nina": "Lisbon"}]

			group_messages = gateway.list_conversation_messages(result.group_conversation["id"])
			group_contents = [message["content"] for message in group_messages]
			assert group_contents[-1] == "stop"
			assert len(group_contents) >= 6
			assert not any(content.startswith("Final decision:") for content in group_contents)
			assert not any(content.startswith("It has been about 5 minutes") for content in group_contents)
	finally:
		app.dependency_overrides.clear()
		websocket_module.SessionLocal = original_session_local