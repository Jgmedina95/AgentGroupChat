from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import api.websockets as websocket_module
from chatapp.gateway import TestClientChatGateway
from db.session import create_connection, get_db, init_db
from main import app
from simulation.core.trace import render_trace_log, write_trace_log
from simulation.runtimes.trip_planner import ScriptedTripDecisionClient, TripFriendPersona, TripPlannerRuntimeFactory
from simulation.trip_planner import FriendsTripConfig, FriendsTripSimulationEngine


class StopInjectingChatGateway(TestClientChatGateway):
	def __init__(
		self,
		client: TestClient,
		*,
		stop_command: str = "stop",
		stop_after_group_messages: int = 2,
	) -> None:
		super().__init__(client)
		self._stop_command = stop_command
		self._stop_after_group_messages = stop_after_group_messages
		self._group_conversation_id: str | None = None
		self._stop_injected = False
		self._group_message_count = 0

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
		return conversation

	def post_member_message(self, *, member_id: str, conversation_id: str, content: str) -> dict[str, str]:
		message = super().post_member_message(member_id=member_id, conversation_id=conversation_id, content=content)
		if conversation_id != self._group_conversation_id:
			return message
		if self._stop_injected or content.strip().casefold() == self._stop_command.casefold():
			return message
		self._group_message_count += 1
		if self._group_message_count >= self._stop_after_group_messages:
			super().post_member_message(member_id=member_id, conversation_id=conversation_id, content=self._stop_command)
			self._stop_injected = True
		return message


def test_trip_planner_records_trace_for_turns_and_consensus(tmp_path: Path) -> None:
	database_path = tmp_path / "trip-planner-trace.db"
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
					"Nina": ["NO_MESSAGE", "Lisbon is sounding better now that Marco set a budget."],
					"Marco": ["If we keep the total cost predictable, Lisbon works for me."],
				},
				choice_responses={
					"Nina": ["Lisbon"],
					"Marco": ["Lisbon"],
				},
			)
			engine = FriendsTripSimulationEngine(
				TestClientChatGateway(client),
				runtime_factory=TripPlannerRuntimeFactory(decision_client),
			)
			result = engine.run(
				FriendsTripConfig(
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
					],
					destination_options=["Lisbon", "Montreal"],
					max_discussion_rounds=1,
					discussion_seed=1,
				)
			)

			assert any(event.event_type == "turn_offered" for event in result.trace_events)
			assert any(event.event_type == "group_chat_created" for event in result.trace_events)
			assert any(
				event.event_type == "turn_skipped" and event.member_name == "Nina"
				for event in result.trace_events
			)
			assert any(
				event.event_type == "message_posted"
				and event.member_name == "Marco"
				and event.details["message_scope"] == "group"
				for event in result.trace_events
			)
			consensus_event = next(event for event in result.trace_events if event.event_type == "consensus_checked")
			assert consensus_event.details["consensus_reached"] is True
			assert consensus_event.details["consensus_choice"] == "Lisbon"
	finally:
		app.dependency_overrides.clear()
		websocket_module.SessionLocal = original_session_local


def test_trace_log_is_rendered_as_human_readable_events(tmp_path: Path) -> None:
	database_path = tmp_path / "trip-planner-trace-log.db"
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
					"Lara": ["I would love somewhere warm and easygoing.", "NO_MESSAGE"],
					"Owen": ["NO_MESSAGE", "NO_MESSAGE"],
				},
				choice_responses={
					"Lara": ["Lisbon"],
					"Owen": ["Montreal"],
				},
			)
			engine = FriendsTripSimulationEngine(
				TestClientChatGateway(client),
				runtime_factory=TripPlannerRuntimeFactory(decision_client),
			)
			result = engine.run(
				FriendsTripConfig(
					friends=[
						TripFriendPersona(
							name="Lara",
							traits=["enthusiastic"],
							budget_notes="Can stretch a bit.",
							travel_hopes="Wants a warm destination.",
							worries="Does not want tense planning.",
						),
						TripFriendPersona(
							name="Owen",
							traits=["anxious planner"],
							budget_notes="Needs predictable costs.",
							travel_hopes="Would travel if logistics feel simple.",
							worries="Gets stuck on route complexity.",
						),
					],
					initiator_name="Lara",
					destination_options=["Lisbon", "Montreal"],
					max_discussion_rounds=1,
					discussion_seed=1,
				)
			)

			trace_log = render_trace_log(result.trace_events)
			assert "[event] Trip Host created group chat: Friends Trip" in trace_log
			assert "[event] Lara sent a message:" in trace_log
			assert "[event] Round 1: Owen decided not to answer" in trace_log

			output_path = tmp_path / "trip-trace.log"
			written_path = write_trace_log(result.trace_events, output_path)
			assert written_path == output_path
			assert output_path.read_text(encoding="utf-8") == trace_log + "\n"
	finally:
		app.dependency_overrides.clear()
		websocket_module.SessionLocal = original_session_local


def test_trip_planner_records_trace_when_stop_is_requested(tmp_path: Path) -> None:
	database_path = tmp_path / "trip-planner-trace-stop.db"
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
					"Nina": ["I want a destination that still feels calm and affordable."],
					"Marco": ["Lisbon looks realistic if we keep flights and hotel simple."],
				},
				choice_responses={
					"Nina": ["Lisbon"],
					"Marco": ["Lisbon"],
				},
			)
			engine = FriendsTripSimulationEngine(
				StopInjectingChatGateway(client, stop_after_group_messages=2),
				runtime_factory=TripPlannerRuntimeFactory(decision_client),
			)
			result = engine.run(
				FriendsTripConfig(
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
					],
					destination_options=["Lisbon", "Montreal"],
					max_discussion_rounds=3,
					discussion_seed=7,
					stop_command="stop",
				)
			)

			stop_event = next(event for event in result.trace_events if event.event_type == "stop_requested")
			assert stop_event.details["stop_requested_by_member_id"] == result.stop_requested_by_member_id
			assert stop_event.conversation_id == result.group_conversation["id"]
			stored_runs = engine._gateway.list_conversation_simulation_trace_runs(result.group_conversation["id"])
			assert len(stored_runs) == 1
			assert stored_runs[0]["scenario_type"] == "trip_planner"
			stored_run = engine._gateway.get_simulation_trace_run(stored_runs[0]["id"])
			assert stored_run["root_conversation_id"] == result.group_conversation["id"]
			assert any(event["event_type"] == "stop_requested" for event in stored_run["events"])
	finally:
		app.dependency_overrides.clear()
		websocket_module.SessionLocal = original_session_local