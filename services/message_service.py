from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from fastapi import HTTPException, status

from models import Conversation, Member, Membership, Message


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _row_to_member(row: sqlite3.Row) -> Member:
    config = json.loads(row["config"]) if row["config"] else None
    return Member(id=row["id"], type=row["type"], display_name=row["display_name"], config=config)


def _row_to_membership(row: sqlite3.Row) -> Membership:
    return Membership(
        id=row["id"],
        conversation_id=row["conversation_id"],
        member_id=row["member_id"],
        status=row["status"],
        role=row["role"],
        invited_by_member_id=row["invited_by_member_id"],
        joined_at=_parse_datetime(row["joined_at"]),
        left_at=_parse_datetime(row["left_at"]),
    )


def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        sender_id=row["sender_id"],
        content=row["content"],
        created_at=_parse_datetime(row["created_at"]) or _utc_now(),
        deleted_at=_parse_datetime(row["deleted_at"]),
    )


def _load_memberships(db: sqlite3.Connection, conversation_id: str) -> list[Membership]:
    rows = db.execute(
        """
        SELECT id, conversation_id, member_id, status, role, invited_by_member_id, joined_at, left_at
        FROM memberships
        WHERE conversation_id = ?
        ORDER BY joined_at ASC, id ASC
        """,
        (conversation_id,),
    ).fetchall()
    return [_row_to_membership(row) for row in rows]


def _load_conversation(db: sqlite3.Connection, conversation_id: str) -> Conversation | None:
    row = db.execute(
        """
        SELECT id, type, title, created_by_member_id, join_policy, status
        FROM conversations
        WHERE id = ?
        """,
        (conversation_id,),
    ).fetchone()
    if row is None:
        return None
    return Conversation(
        id=row["id"],
        type=row["type"],
        title=row["title"],
        created_by_member_id=row["created_by_member_id"],
        join_policy=row["join_policy"],
        status=row["status"],
        memberships=_load_memberships(db, row["id"]),
    )


def create_member(db: sqlite3.Connection, member_type: str, display_name: str, config: dict | None = None) -> Member:
    member = Member(type=member_type, display_name=display_name, config=config)
    db.execute(
        "INSERT INTO members (id, type, display_name, config) VALUES (?, ?, ?, ?)",
        (member.id, member.type, member.display_name, json.dumps(member.config) if member.config is not None else None),
    )
    db.commit()
    return member


def create_agent(db: sqlite3.Connection, agent_type: str, display_name: str, config: dict | None = None) -> Member:
    return create_member(db, member_type=agent_type, display_name=display_name, config=config)


def list_members(db: sqlite3.Connection) -> list[Member]:
    rows = db.execute(
        "SELECT id, type, display_name, config FROM members ORDER BY display_name ASC, id ASC"
    ).fetchall()
    return [_row_to_member(row) for row in rows]


def list_agents(db: sqlite3.Connection) -> list[Member]:
    return list_members(db)


def create_conversation(
    db: sqlite3.Connection,
    conversation_type: str,
    title: str | None = None,
    participant_ids: list[str] | None = None,
) -> Conversation:
    participant_ids = participant_ids or []
    if not participant_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one participant is required")

    requested_ids = list(dict.fromkeys(participant_ids))
    placeholders = ", ".join("?" for _ in requested_ids)
    rows = db.execute(
        f"SELECT id FROM members WHERE id IN ({placeholders})",
        requested_ids,
    ).fetchall()
    found_ids = {row["id"] for row in rows}
    missing_ids = [member_id for member_id in requested_ids if member_id not in found_ids]
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Some participants were not found", "missing_agent_ids": missing_ids},
        )

    created_at = _utc_now()
    conversation = Conversation(
        type=conversation_type,
        title=title,
        created_by_member_id=requested_ids[0],
        memberships=[],
    )
    db.execute(
        """
        INSERT INTO conversations (id, type, title, created_by_member_id, join_policy, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            conversation.id,
            conversation.type,
            conversation.title,
            conversation.created_by_member_id,
            conversation.join_policy,
            conversation.status,
        ),
    )

    memberships: list[Membership] = []
    for index, participant_id in enumerate(requested_ids):
        membership = Membership(
            conversation_id=conversation.id,
            member_id=participant_id,
            status="active",
            role="owner" if index == 0 else "member",
            invited_by_member_id=conversation.created_by_member_id,
            joined_at=created_at,
        )
        db.execute(
            """
            INSERT INTO memberships (
                id, conversation_id, member_id, status, role, invited_by_member_id, joined_at, left_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                membership.id,
                membership.conversation_id,
                membership.member_id,
                membership.status,
                membership.role,
                membership.invited_by_member_id,
                _serialize_datetime(membership.joined_at),
                _serialize_datetime(membership.left_at),
            ),
        )
        memberships.append(membership)

    db.commit()
    conversation.memberships = memberships
    return conversation


def list_conversations(db: sqlite3.Connection) -> list[Conversation]:
    rows = db.execute(
        """
        SELECT id, type, title, created_by_member_id, join_policy, status
        FROM conversations
        ORDER BY title ASC, id ASC
        """
    ).fetchall()
    return [
        Conversation(
            id=row["id"],
            type=row["type"],
            title=row["title"],
            created_by_member_id=row["created_by_member_id"],
            join_policy=row["join_policy"],
            status=row["status"],
            memberships=_load_memberships(db, row["id"]),
        )
        for row in rows
    ]


def delete_conversation(db: sqlite3.Connection, conversation_id: str) -> Conversation:
    conversation = _load_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    db.commit()
    return conversation


def create_message(db: sqlite3.Connection, conversation_id: str, sender_id: str, content: str) -> Message:
    conversation_exists = db.execute(
        "SELECT 1 FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if conversation_exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    sender_exists = db.execute(
        "SELECT 1 FROM members WHERE id = ?",
        (sender_id,),
    ).fetchone()
    if sender_exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sender not found")

    membership = db.execute(
        """
        SELECT 1
        FROM memberships
        WHERE conversation_id = ? AND member_id = ? AND status = 'active'
        """,
        (conversation_id, sender_id),
    ).fetchone()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sender is not a participant in this conversation")

    message = Message(conversation_id=conversation_id, sender_id=sender_id, content=content, created_at=_utc_now())
    db.execute(
        """
        INSERT INTO messages (id, conversation_id, sender_id, content, created_at, deleted_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            message.id,
            message.conversation_id,
            message.sender_id,
            message.content,
            _serialize_datetime(message.created_at),
            _serialize_datetime(message.deleted_at),
        ),
    )
    db.commit()
    return message


def list_messages(db: sqlite3.Connection, conversation_id: str, include_deleted: bool = False) -> list[Message]:
    if include_deleted:
        rows = db.execute(
            """
            SELECT id, conversation_id, sender_id, content, created_at, deleted_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (conversation_id,),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT id, conversation_id, sender_id, content, created_at, deleted_at
            FROM messages
            WHERE conversation_id = ? AND deleted_at IS NULL
            ORDER BY created_at ASC, id ASC
            """,
            (conversation_id,),
        ).fetchall()
    return [_row_to_message(row) for row in rows]


def delete_message(db: sqlite3.Connection, message_id: str) -> Message:
    row = db.execute(
        "SELECT id, conversation_id, sender_id, content, created_at, deleted_at FROM messages WHERE id = ?",
        (message_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    message = _row_to_message(row)
    if message.deleted_at is None:
        message.deleted_at = _utc_now()
        db.execute(
            "UPDATE messages SET deleted_at = ? WHERE id = ?",
            (_serialize_datetime(message.deleted_at), message.id),
        )
        db.commit()

    return message