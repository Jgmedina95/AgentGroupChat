from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from db.session import get_db
from api.websockets import conversation_list_manager, manager
from models import Agent, Conversation, Message
from services.message_service import (
	create_agent,
	create_conversation,
	create_message,
	delete_conversation,
	delete_message,
	list_agents,
	list_conversations,
	list_messages,
)


router = APIRouter(prefix="/api")


class AgentCreate(BaseModel):
	type: str
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


class AgentRead(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: str
	type: str
	display_name: str
	config: dict | None


class ConversationRead(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: str
	type: str
	title: str | None
	participant_ids: list[str]


class MessageRead(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: str
	conversation_id: str
	sender_id: str
	content: str
	created_at: datetime
	deleted_at: datetime | None


def serialize_message(message: Message) -> MessageRead:
	return MessageRead.model_validate(message)


def serialize_conversation(conversation: Conversation) -> ConversationRead:
	participant_ids = [participant.agent_id for participant in conversation.participants]
	return ConversationRead(
		id=conversation.id,
		type=conversation.type,
		title=conversation.title,
		participant_ids=participant_ids,
	)


@router.get("/agents", response_model=list[AgentRead])
def list_agents_route(db: Session = Depends(get_db)) -> list[Agent]:
	return list_agents(db)


@router.get("/conversations", response_model=list[ConversationRead])
def list_conversations_route(db: Session = Depends(get_db)) -> list[ConversationRead]:
	return [serialize_conversation(conversation) for conversation in list_conversations(db)]


@router.post("/agents", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
def create_agent_route(payload: AgentCreate, db: Session = Depends(get_db)) -> Agent:
	return create_agent(db, agent_type=payload.type, display_name=payload.display_name, config=payload.config)


@router.post("/conversations", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation_route(payload: ConversationCreate, db: Session = Depends(get_db)) -> ConversationRead:
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


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation_route(conversation_id: str, db: Session = Depends(get_db)) -> None:
	delete_conversation(db, conversation_id)
	await conversation_list_manager.broadcast(
		"__all_conversations__",
		{"event": "conversation.deleted", "data": {"id": conversation_id}},
	)


@router.post("/messages", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
async def create_message_route(payload: MessageCreate, db: Session = Depends(get_db)) -> MessageRead:
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
	db: Session = Depends(get_db),
) -> list[Message]:
	return list_messages(db, conversation_id=conversation_id, include_deleted=include_deleted)


@router.delete("/messages/{message_id}", response_model=MessageRead)
async def delete_message_route(message_id: str, db: Session = Depends(get_db)) -> MessageRead:
	message = delete_message(db, message_id=message_id)
	message_read = serialize_message(message)
	await manager.broadcast(
		message.conversation_id,
		{"event": "message.deleted", "data": message_read.model_dump(mode="json")},
	)
	return message_read
