from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict

from db.session import get_db
from api.websockets import conversation_list_manager, manager
from models import Conversation, Member, Message
from services.message_service import (
	add_member_to_conversation,
	create_agent,
	create_conversation,
	create_group_conversation,
	create_message,
	delete_conversation,
	delete_message,
	leave_conversation,
	list_agents,
	list_conversation_members,
	list_conversations,
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


class AgentRead(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: str
	type: str
	member_type: str
	display_name: str
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


def serialize_member(member: Member) -> AgentRead:
	return AgentRead(
		id=member.id,
		type=member.type,
		member_type=member.member_type,
		display_name=member.display_name,
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


@router.get("/agents", response_model=list[AgentRead])
def list_agents_route(db: sqlite3.Connection = Depends(get_db)) -> list[AgentRead]:
	return [serialize_member(member) for member in list_agents(db)]


@router.get("/members", response_model=list[AgentRead])
def list_members_route(db: sqlite3.Connection = Depends(get_db)) -> list[AgentRead]:
	return [serialize_member(member) for member in list_agents(db)]


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
			config=payload.config,
			member_type=payload.member_type,
		)
	)


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


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageRead])
def list_messages_route(
	conversation_id: str,
	include_deleted: bool = False,
	db: sqlite3.Connection = Depends(get_db),
) -> list[MessageRead]:
	return [serialize_message(message) for message in list_messages(db, conversation_id=conversation_id, include_deleted=include_deleted)]


@router.delete("/messages/{message_id}", response_model=MessageRead)
async def delete_message_route(message_id: str, db: sqlite3.Connection = Depends(get_db)) -> MessageRead:
	message = delete_message(db, message_id=message_id)
	message_read = serialize_message(message)
	await manager.broadcast(
		message.conversation_id,
		{"event": "message.deleted", "data": message_read.model_dump(mode="json")},
	)
	return message_read
