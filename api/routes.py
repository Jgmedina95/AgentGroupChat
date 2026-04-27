from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict

from db.session import get_db
from api.websockets import conversation_list_manager, manager
from models import Conversation, Member, Message, SimulationTraceEventRecord, SimulationTraceRun
from services.message_service import (
	add_member_to_conversation,
	create_agent,
	create_conversation,
	create_member_group_conversation,
	create_member_message,
	create_simulation_trace_run,
	create_group_conversation,
	create_message,
	delete_conversation,
	delete_message,
	get_effective_member_capabilities,
	get_member_access_context,
	get_simulation_trace_run,
	leave_conversation,
	leave_member_conversation,
	list_agents,
	list_conversation_members,
	list_conversation_simulation_trace_runs,
	list_conversations,
	list_member_visible_conversations,
	list_member_visible_messages,
	list_messages,
	pause_conversation_messages,
	resume_conversation_messages,
	remove_member_from_conversation,
)


router = APIRouter(prefix="/api")


class AgentCreate(BaseModel):
	type: str
	member_type: str = "user_regular"
	display_name: str
	capabilities: dict[str, bool] | None = None
	config: dict | None = None


class ConversationCreate(BaseModel):
	type: str
	title: str | None = None
	participant_ids: list[str]


class MessageCreate(BaseModel):
	conversation_id: str
	sender_id: str
	content: str


class GroupConversationCreate(BaseModel):
	created_by_member_id: str
	title: str | None = None
	member_ids: list[str] = []


class ConversationMemberAdd(BaseModel):
	acting_member_id: str
	member_id: str


class ConversationLeave(BaseModel):
	member_id: str


class ConversationPauseControl(BaseModel):
	acting_member_id: str
	notice: str | None = None


class ConversationResumeControl(BaseModel):
	acting_member_id: str


class MemberMessageCreate(BaseModel):
	conversation_id: str
	content: str


class MemberGroupConversationCreate(BaseModel):
	title: str | None = None
	member_ids: list[str] = []


class AgentRead(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: str
	type: str
	member_type: str
	display_name: str
	capabilities: dict[str, bool]
	config: dict | None


class ConversationRead(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: str
	type: str
	title: str | None
	participant_ids: list[str]
	messages_paused: bool = False
	message_pause_notice: str | None = None


class MessageRead(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: str
	conversation_id: str
	sender_id: str
	content: str
	created_at: datetime
	deleted_at: datetime | None


class MembershipRead(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: str
	conversation_id: str
	member_id: str
	status: str
	role: str
	invited_by_member_id: str | None
	joined_at: datetime | None
	left_at: datetime | None


class MemberAccessRead(BaseModel):
	member: AgentRead
	capabilities: dict[str, bool]
	visible_conversation_ids: list[str]


class SimulationTraceEventCreate(BaseModel):
	event_type: str
	recorded_at: datetime | None = None
	round_index: int | None = None
	member_id: str | None = None
	member_name: str | None = None
	conversation_id: str | None = None
	details: dict | None = None


class SimulationTraceRunCreate(BaseModel):
	scenario_type: str
	root_conversation_id: str
	final_choice: str | None = None
	consensus_reached: bool = False
	stopped_early: bool = False
	stop_requested_by_member_id: str | None = None
	events: list[SimulationTraceEventCreate]


class SimulationTraceEventRead(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: str
	trace_run_id: str
	sequence_index: int
	event_type: str
	recorded_at: datetime
	round_index: int | None
	member_id: str | None
	member_name: str | None
	conversation_id: str | None
	details: dict


class SimulationTraceRunRead(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: str
	scenario_type: str
	root_conversation_id: str
	created_at: datetime
	final_choice: str | None
	consensus_reached: bool
	stopped_early: bool
	stop_requested_by_member_id: str | None
	events: list[SimulationTraceEventRead]


def serialize_member(member: Member) -> AgentRead:
	return AgentRead(
		id=member.id,
		type=member.type,
		member_type=member.member_type,
		display_name=member.display_name,
		capabilities=get_effective_member_capabilities(member),
		config=member.config,
	)


def serialize_message(message: Message) -> MessageRead:
	return MessageRead(
		id=message.id,
		conversation_id=message.conversation_id,
		sender_id=message.sender_id,
		content=message.content,
		created_at=message.created_at,
		deleted_at=message.deleted_at,
	)


def serialize_membership(membership) -> MembershipRead:
	return MembershipRead(
		id=membership.id,
		conversation_id=membership.conversation_id,
		member_id=membership.member_id,
		status=membership.status,
		role=membership.role,
		invited_by_member_id=membership.invited_by_member_id,
		joined_at=membership.joined_at,
		left_at=membership.left_at,
	)


def serialize_conversation(conversation: Conversation) -> ConversationRead:
	participant_ids = [participant.agent_id for participant in conversation.participants if participant.status == "active"]
	return ConversationRead(
		id=conversation.id,
		type=conversation.type,
		title=conversation.title,
		participant_ids=participant_ids,
		messages_paused=conversation.messages_paused,
		message_pause_notice=conversation.message_pause_notice,
	)


def serialize_simulation_trace_event(event: SimulationTraceEventRecord) -> SimulationTraceEventRead:
	return SimulationTraceEventRead(
		id=event.id,
		trace_run_id=event.trace_run_id,
		sequence_index=event.sequence_index,
		event_type=event.event_type,
		recorded_at=event.recorded_at,
		round_index=event.round_index,
		member_id=event.member_id,
		member_name=event.member_name,
		conversation_id=event.conversation_id,
		details=event.details,
	)


def serialize_simulation_trace_run(trace_run: SimulationTraceRun) -> SimulationTraceRunRead:
	return SimulationTraceRunRead(
		id=trace_run.id,
		scenario_type=trace_run.scenario_type,
		root_conversation_id=trace_run.root_conversation_id,
		created_at=trace_run.created_at,
		final_choice=trace_run.final_choice,
		consensus_reached=trace_run.consensus_reached,
		stopped_early=trace_run.stopped_early,
		stop_requested_by_member_id=trace_run.stop_requested_by_member_id,
		events=[serialize_simulation_trace_event(event) for event in trace_run.events],
	)


@router.get("/agents", response_model=list[AgentRead])
def list_agents_route(db: sqlite3.Connection = Depends(get_db)) -> list[AgentRead]:
	return [serialize_member(member) for member in list_agents(db)]


@router.get("/members", response_model=list[AgentRead])
def list_members_route(db: sqlite3.Connection = Depends(get_db)) -> list[AgentRead]:
	return [serialize_member(member) for member in list_agents(db)]


@router.get("/members/{member_id}/access", response_model=MemberAccessRead)
def get_member_access_route(member_id: str, db: sqlite3.Connection = Depends(get_db)) -> MemberAccessRead:
	member, capabilities, visible_conversations = get_member_access_context(db, member_id)
	return MemberAccessRead(
		member=serialize_member(member),
		capabilities=capabilities,
		visible_conversation_ids=[conversation.id for conversation in visible_conversations],
	)


@router.get("/members/{member_id}/conversations", response_model=list[ConversationRead])
def list_member_conversations_route(
	member_id: str,
	db: sqlite3.Connection = Depends(get_db),
) -> list[ConversationRead]:
	return [serialize_conversation(conversation) for conversation in list_member_visible_conversations(db, member_id)]


@router.get("/members/{member_id}/conversations/{conversation_id}/messages", response_model=list[MessageRead])
def list_member_messages_route(
	member_id: str,
	conversation_id: str,
	include_deleted: bool = False,
	db: sqlite3.Connection = Depends(get_db),
) -> list[MessageRead]:
	return [
		serialize_message(message)
		for message in list_member_visible_messages(
			db,
			member_id=member_id,
			conversation_id=conversation_id,
			include_deleted=include_deleted,
		)
	]


@router.get("/conversations", response_model=list[ConversationRead])
def list_conversations_route(db: sqlite3.Connection = Depends(get_db)) -> list[ConversationRead]:
	return [serialize_conversation(conversation) for conversation in list_conversations(db)]


@router.post("/agents", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
def create_agent_route(payload: AgentCreate, db: sqlite3.Connection = Depends(get_db)) -> AgentRead:
	return serialize_member(
		create_agent(
			db,
			agent_type=payload.type,
			display_name=payload.display_name,
			capabilities=payload.capabilities,
			config=payload.config,
			member_type=payload.member_type,
		)
	)


@router.post("/members", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
def create_member_route(payload: AgentCreate, db: sqlite3.Connection = Depends(get_db)) -> AgentRead:
	return serialize_member(
		create_agent(
			db,
			agent_type=payload.type,
			display_name=payload.display_name,
			capabilities=payload.capabilities,
			config=payload.config,
			member_type=payload.member_type,
		)
	)


@router.post("/members/{member_id}/conversations/group", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_member_group_conversation_route(
	member_id: str,
	payload: MemberGroupConversationCreate,
	db: sqlite3.Connection = Depends(get_db),
) -> ConversationRead:
	conversation = create_member_group_conversation(
		db,
		member_id=member_id,
		title=payload.title,
		member_ids=payload.member_ids,
	)
	conversation_read = serialize_conversation(conversation)
	await conversation_list_manager.broadcast(
		"__all_conversations__",
		{"event": "conversation.created", "data": conversation_read.model_dump(mode="json")},
	)
	return conversation_read


@router.post("/conversations/group", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_group_conversation_route(
	payload: GroupConversationCreate,
	db: sqlite3.Connection = Depends(get_db),
) -> ConversationRead:
	conversation = create_group_conversation(
		db,
		created_by_member_id=payload.created_by_member_id,
		title=payload.title,
		member_ids=payload.member_ids,
	)
	conversation_read = serialize_conversation(conversation)
	await conversation_list_manager.broadcast(
		"__all_conversations__",
		{"event": "conversation.created", "data": conversation_read.model_dump(mode="json")},
	)
	return conversation_read


@router.post("/conversations", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation_route(payload: ConversationCreate, db: sqlite3.Connection = Depends(get_db)) -> ConversationRead:
	conversation = create_conversation(
		db,
		conversation_type=payload.type,
		title=payload.title,
		participant_ids=payload.participant_ids,
	)
	conversation_read = serialize_conversation(conversation)
	await conversation_list_manager.broadcast(
		"__all_conversations__",
		{"event": "conversation.created", "data": conversation_read.model_dump(mode="json")},
	)
	return conversation_read


@router.get("/conversations/{conversation_id}/members", response_model=list[MembershipRead])
def list_conversation_members_route(
	conversation_id: str,
	db: sqlite3.Connection = Depends(get_db),
) -> list[MembershipRead]:
	return [serialize_membership(membership) for membership in list_conversation_members(db, conversation_id)]


@router.post("/conversations/{conversation_id}/members", response_model=MembershipRead, status_code=status.HTTP_201_CREATED)
async def add_conversation_member_route(
	conversation_id: str,
	payload: ConversationMemberAdd,
	db: sqlite3.Connection = Depends(get_db),
) -> MembershipRead:
	membership = add_member_to_conversation(
		db,
		conversation_id=conversation_id,
		acting_member_id=payload.acting_member_id,
		member_id=payload.member_id,
	)
	membership_read = serialize_membership(membership)
	await manager.broadcast(
		conversation_id,
		{"event": "membership.added", "data": membership_read.model_dump(mode="json")},
	)
	return membership_read


@router.delete("/conversations/{conversation_id}/members/{member_id}", response_model=MembershipRead)
async def remove_conversation_member_route(
	conversation_id: str,
	member_id: str,
	acting_member_id: str = Query(...),
	db: sqlite3.Connection = Depends(get_db),
) -> MembershipRead:
	membership = remove_member_from_conversation(
		db,
		conversation_id=conversation_id,
		acting_member_id=acting_member_id,
		member_id=member_id,
	)
	membership_read = serialize_membership(membership)
	await manager.broadcast(
		conversation_id,
		{"event": "membership.removed", "data": membership_read.model_dump(mode="json")},
	)
	return membership_read


@router.post("/conversations/{conversation_id}/leave", response_model=MembershipRead)
async def leave_conversation_route(
	conversation_id: str,
	payload: ConversationLeave,
	db: sqlite3.Connection = Depends(get_db),
) -> MembershipRead:
	membership = leave_conversation(db, conversation_id=conversation_id, member_id=payload.member_id)
	membership_read = serialize_membership(membership)
	await manager.broadcast(
		conversation_id,
		{"event": "membership.left", "data": membership_read.model_dump(mode="json")},
	)
	return membership_read


@router.post("/members/{member_id}/conversations/{conversation_id}/leave", response_model=MembershipRead)
async def leave_member_conversation_route(
	member_id: str,
	conversation_id: str,
	db: sqlite3.Connection = Depends(get_db),
) -> MembershipRead:
	membership = leave_member_conversation(db, member_id=member_id, conversation_id=conversation_id)
	membership_read = serialize_membership(membership)
	await manager.broadcast(
		conversation_id,
		{"event": "membership.left", "data": membership_read.model_dump(mode="json")},
	)
	return membership_read


@router.post("/conversations/{conversation_id}/pause-messages", response_model=ConversationRead)
async def pause_conversation_messages_route(
	conversation_id: str,
	payload: ConversationPauseControl,
	db: sqlite3.Connection = Depends(get_db),
) -> ConversationRead:
	conversation = pause_conversation_messages(
		db,
		conversation_id=conversation_id,
		acting_member_id=payload.acting_member_id,
		notice=payload.notice,
	)
	conversation_read = serialize_conversation(conversation)
	await conversation_list_manager.broadcast(
		"__all_conversations__",
		{"event": "conversation.updated", "data": conversation_read.model_dump(mode="json")},
	)
	await manager.broadcast(
		conversation_id,
		{"event": "conversation.updated", "data": conversation_read.model_dump(mode="json")},
	)
	return conversation_read


@router.post("/conversations/{conversation_id}/resume-messages", response_model=ConversationRead)
async def resume_conversation_messages_route(
	conversation_id: str,
	payload: ConversationResumeControl,
	db: sqlite3.Connection = Depends(get_db),
) -> ConversationRead:
	conversation = resume_conversation_messages(
		db,
		conversation_id=conversation_id,
		acting_member_id=payload.acting_member_id,
	)
	conversation_read = serialize_conversation(conversation)
	await conversation_list_manager.broadcast(
		"__all_conversations__",
		{"event": "conversation.updated", "data": conversation_read.model_dump(mode="json")},
	)
	await manager.broadcast(
		conversation_id,
		{"event": "conversation.updated", "data": conversation_read.model_dump(mode="json")},
	)
	return conversation_read


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation_route(conversation_id: str, db: sqlite3.Connection = Depends(get_db)) -> None:
	delete_conversation(db, conversation_id)
	await conversation_list_manager.broadcast(
		"__all_conversations__",
		{"event": "conversation.deleted", "data": {"id": conversation_id}},
	)


@router.post("/messages", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
async def create_message_route(payload: MessageCreate, db: sqlite3.Connection = Depends(get_db)) -> MessageRead:
	message = create_message(
		db,
		conversation_id=payload.conversation_id,
		sender_id=payload.sender_id,
		content=payload.content,
	)
	message_read = serialize_message(message)
	await manager.broadcast(
		payload.conversation_id,
		{"event": "message.created", "data": message_read.model_dump(mode="json")},
	)
	return message_read


@router.post("/members/{member_id}/messages", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
async def create_member_message_route(
	member_id: str,
	payload: MemberMessageCreate,
	db: sqlite3.Connection = Depends(get_db),
) -> MessageRead:
	message = create_member_message(
		db,
		member_id=member_id,
		conversation_id=payload.conversation_id,
		content=payload.content,
	)
	message_read = serialize_message(message)
	await manager.broadcast(
		payload.conversation_id,
		{"event": "message.created", "data": message_read.model_dump(mode="json")},
	)
	return message_read


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageRead])
def list_messages_route(
	conversation_id: str,
	include_deleted: bool = False,
	db: sqlite3.Connection = Depends(get_db),
) -> list[MessageRead]:
	return [serialize_message(message) for message in list_messages(db, conversation_id=conversation_id, include_deleted=include_deleted)]


@router.post("/simulation-traces", response_model=SimulationTraceRunRead, status_code=status.HTTP_201_CREATED)
def create_simulation_trace_run_route(
	payload: SimulationTraceRunCreate,
	db: sqlite3.Connection = Depends(get_db),
) -> SimulationTraceRunRead:
	trace_run = create_simulation_trace_run(
		db,
		scenario_type=payload.scenario_type,
		root_conversation_id=payload.root_conversation_id,
		final_choice=payload.final_choice,
		consensus_reached=payload.consensus_reached,
		stopped_early=payload.stopped_early,
		stop_requested_by_member_id=payload.stop_requested_by_member_id,
		events=[event.model_dump(mode="json") for event in payload.events],
	)
	return serialize_simulation_trace_run(trace_run)


@router.get("/conversations/{conversation_id}/simulation-traces", response_model=list[SimulationTraceRunRead])
def list_conversation_simulation_traces_route(
	conversation_id: str,
	db: sqlite3.Connection = Depends(get_db),
) -> list[SimulationTraceRunRead]:
	return [
		serialize_simulation_trace_run(trace_run)
		for trace_run in list_conversation_simulation_trace_runs(db, conversation_id)
	]


@router.get("/simulation-traces/{trace_run_id}", response_model=SimulationTraceRunRead)
def get_simulation_trace_run_route(
	trace_run_id: str,
	db: sqlite3.Connection = Depends(get_db),
) -> SimulationTraceRunRead:
	return serialize_simulation_trace_run(get_simulation_trace_run(db, trace_run_id))


@router.delete("/messages/{message_id}", response_model=MessageRead)
async def delete_message_route(message_id: str, db: sqlite3.Connection = Depends(get_db)) -> MessageRead:
	message = delete_message(db, message_id=message_id)
	message_read = serialize_message(message)
	await manager.broadcast(
		message.conversation_id,
		{"event": "message.deleted", "data": message_read.model_dump(mode="json")},
	)
	return message_read
