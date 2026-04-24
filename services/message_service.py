from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Agent, Conversation, ConversationParticipant, Message


def create_agent(db: Session, agent_type: str, display_name: str, config: dict | None = None) -> Agent:
    agent = Agent(type=agent_type, display_name=display_name, config=config)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


def list_agents(db: Session) -> list[Agent]:
    statement = select(Agent).order_by(Agent.display_name.asc(), Agent.id.asc())
    return list(db.scalars(statement).all())


def create_conversation(
    db: Session,
    conversation_type: str,
    title: str | None = None,
    participant_ids: list[str] | None = None,
) -> Conversation:
    participant_ids = participant_ids or []
    if not participant_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one participant is required")

    agents = list(db.scalars(select(Agent).where(Agent.id.in_(participant_ids))).all())
    found_ids = {agent.id for agent in agents}
    missing_ids = [agent_id for agent_id in participant_ids if agent_id not in found_ids]
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Some participants were not found", "missing_agent_ids": missing_ids},
        )

    conversation = Conversation(type=conversation_type, title=title)
    db.add(conversation)
    db.flush()

    for participant_id in dict.fromkeys(participant_ids):
        db.add(ConversationParticipant(conversation_id=conversation.id, agent_id=participant_id))

    db.commit()
    db.refresh(conversation)
    return conversation


def list_conversations(db: Session) -> list[Conversation]:
    statement = select(Conversation).order_by(Conversation.title.asc(), Conversation.id.asc())
    return list(db.scalars(statement).all())


def delete_conversation(db: Session, conversation_id: str) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    db.delete(conversation)
    db.commit()
    return conversation


def create_message(db: Session, conversation_id: str, sender_id: str, content: str) -> Message:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    agent = db.get(Agent, sender_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sender not found")

    membership = db.scalar(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.agent_id == sender_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sender is not a participant in this conversation")

    message = Message(conversation_id=conversation_id, sender_id=sender_id, content=content)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def list_messages(db: Session, conversation_id: str, include_deleted: bool = False) -> list[Message]:
    filters = [Message.conversation_id == conversation_id]
    if not include_deleted:
        filters.append(Message.deleted_at.is_(None))

    statement = select(Message).where(*filters).order_by(Message.created_at.asc())
    return list(db.scalars(statement).all())


def delete_message(db: Session, message_id: str) -> Message:
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    if message.deleted_at is None:
        message.deleted_at = datetime.now(timezone.utc)
        db.add(message)
        db.commit()
        db.refresh(message)

    return message