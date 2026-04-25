from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from fastapi import HTTPException, status

from models import Conversation, Member, Membership, Message


DEFAULT_MEMBER_TYPE = "user_regular"
GROUP_CREATOR_MEMBER_TYPES = {"user_premium", "admin"}
GROUP_MANAGER_MEMBER_TYPES = {"user_premium", "admin"}
ADMIN_MEMBER_TYPE = "admin"
VALID_MEMBER_TYPES = {DEFAULT_MEMBER_TYPE, "user_premium", ADMIN_MEMBER_TYPE}


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
    keys = set(row.keys())
    return Member(
        id=row["id"],
        type=row["type"],
        member_type=row["member_type"] if "member_type" in keys else DEFAULT_MEMBER_TYPE,
        display_name=row["display_name"],
        config=config,
    )


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


def _get_member(db: sqlite3.Connection, member_id: str) -> Member | None:
    row = db.execute(
        "SELECT id, type, member_type, display_name, config FROM members WHERE id = ?",
        (member_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_member(row)


def _require_group_creation_permission(db: sqlite3.Connection, created_by_member_id: str) -> Member:
    member = _get_member(db, created_by_member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    if member.member_type not in GROUP_CREATOR_MEMBER_TYPES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Member cannot create group conversations")
    return member


def _require_admin_member(db: sqlite3.Connection, acting_member_id: str) -> Member:
    member = _get_member(db, acting_member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Acting member not found")
    if member.member_type != ADMIN_MEMBER_TYPE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admins can control message windows")
    return member


def _get_membership(db: sqlite3.Connection, conversation_id: str, member_id: str) -> Membership | None:
    row = db.execute(
        """
        SELECT id, conversation_id, member_id, status, role, invited_by_member_id, joined_at, left_at
        FROM memberships
        WHERE conversation_id = ? AND member_id = ?
        """,
        (conversation_id, member_id),
    ).fetchone()
    if row is None:
        return None
    return _row_to_membership(row)


def _require_group_conversation(db: sqlite3.Connection, conversation_id: str) -> Conversation:
    conversation = _load_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if conversation.type != "group":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Membership management is only supported for group conversations",
        )
    return conversation


def _require_management_membership(db: sqlite3.Connection, conversation_id: str, acting_member_id: str) -> Membership:
    acting_member = _get_member(db, acting_member_id)
    if acting_member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Acting member not found")
    if acting_member.member_type == ADMIN_MEMBER_TYPE:
        membership = _get_membership(db, conversation_id, acting_member_id)
        return membership or Membership(conversation_id=conversation_id, member_id=acting_member_id, status="active", role="owner")
    if acting_member.member_type not in GROUP_MANAGER_MEMBER_TYPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acting member cannot manage memberships in this conversation",
        )

    membership = _get_membership(db, conversation_id, acting_member_id)
    if membership is None or membership.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acting member is not active in this conversation")
    if membership.role not in {"owner", "moderator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acting member cannot manage memberships in this conversation",
        )
    return membership


def _load_conversation(db: sqlite3.Connection, conversation_id: str) -> Conversation | None:
    row = db.execute(
        """
        SELECT id, type, title, created_by_member_id, join_policy, status, messages_paused, message_pause_notice
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
        messages_paused=bool(row["messages_paused"]),
        message_pause_notice=row["message_pause_notice"],
        memberships=_load_memberships(db, row["id"]),
    )
def create_member(
    db: sqlite3.Connection,
    runtime_type: str,
    display_name: str,
    config: dict | None = None,
    member_type: str = DEFAULT_MEMBER_TYPE,
) -> Member:
    if member_type not in VALID_MEMBER_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid member_type")

    member = Member(type=runtime_type, member_type=member_type, display_name=display_name, config=config)
    db.execute(
        "INSERT INTO members (id, type, member_type, display_name, config) VALUES (?, ?, ?, ?, ?)",
        (
            member.id,
            member.type,
            member.member_type,
            member.display_name,
            json.dumps(member.config) if member.config is not None else None,
        ),
    )
    db.commit()
    return member


def create_agent(
    db: sqlite3.Connection,
    agent_type: str,
    display_name: str,
    config: dict | None = None,
    member_type: str = DEFAULT_MEMBER_TYPE,
) -> Member:
    return create_member(
        db,
        runtime_type=agent_type,
        display_name=display_name,
        config=config,
        member_type=member_type,
    )


def list_members(db: sqlite3.Connection) -> list[Member]:
    rows = db.execute(
        "SELECT id, type, member_type, display_name, config FROM members ORDER BY display_name ASC, id ASC"
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

    if conversation_type == "group":
        _require_group_creation_permission(db, requested_ids[0])

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


def create_group_conversation(
    db: sqlite3.Connection,
    created_by_member_id: str,
    title: str | None = None,
    member_ids: list[str] | None = None,
) -> Conversation:
    participant_ids = [created_by_member_id, *(member_ids or [])]
    return create_conversation(
        db,
        conversation_type="group",
        title=title,
        participant_ids=participant_ids,
    )


def list_conversations(db: sqlite3.Connection) -> list[Conversation]:
    rows = db.execute(
        """
        SELECT id, type, title, created_by_member_id, join_policy, status, messages_paused, message_pause_notice
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
            messages_paused=bool(row["messages_paused"]),
            message_pause_notice=row["message_pause_notice"],
            memberships=_load_memberships(db, row["id"]),
        )
        for row in rows
    ]


def list_conversation_members(db: sqlite3.Connection, conversation_id: str) -> list[Membership]:
    conversation = _load_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation.memberships


def delete_conversation(db: sqlite3.Connection, conversation_id: str) -> Conversation:
    conversation = _load_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    db.commit()
    return conversation


def add_member_to_conversation(
    db: sqlite3.Connection,
    conversation_id: str,
    acting_member_id: str,
    member_id: str,
) -> Membership:
    _require_group_conversation(db, conversation_id)
    _require_management_membership(db, conversation_id, acting_member_id)

    member = _get_member(db, member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    existing_membership = _get_membership(db, conversation_id, member_id)
    if existing_membership is not None and existing_membership.status == "active":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Member is already active in this conversation")

    joined_at = _utc_now()
    if existing_membership is None:
        membership = Membership(
            conversation_id=conversation_id,
            member_id=member_id,
            status="active",
            role="member",
            invited_by_member_id=acting_member_id,
            joined_at=joined_at,
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
        db.commit()
        return membership

    db.execute(
        """
        UPDATE memberships
        SET status = ?, role = ?, invited_by_member_id = ?, joined_at = ?, left_at = ?
        WHERE id = ?
        """,
        (
            "active",
            "member",
            acting_member_id,
            _serialize_datetime(joined_at),
            None,
            existing_membership.id,
        ),
    )
    db.commit()
    return _get_membership(db, conversation_id, member_id) or existing_membership


def remove_member_from_conversation(
    db: sqlite3.Connection,
    conversation_id: str,
    acting_member_id: str,
    member_id: str,
) -> Membership:
    _require_group_conversation(db, conversation_id)
    _require_management_membership(db, conversation_id, acting_member_id)

    membership = _get_membership(db, conversation_id, member_id)
    if membership is None or membership.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member is not active in this conversation")
    if membership.role == "owner":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Owners cannot be removed through this action")
    if acting_member_id == member_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Use the leave action to remove yourself from a conversation")

    left_at = _utc_now()
    db.execute(
        """
        UPDATE memberships
        SET status = ?, left_at = ?
        WHERE id = ?
        """,
        ("removed", _serialize_datetime(left_at), membership.id),
    )
    db.commit()
    return _get_membership(db, conversation_id, member_id) or membership


def leave_conversation(db: sqlite3.Connection, conversation_id: str, member_id: str) -> Membership:
    conversation = _require_group_conversation(db, conversation_id)
    membership = _get_membership(db, conversation_id, member_id)
    if membership is None or membership.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member is not active in this conversation")

    active_others = [item for item in conversation.memberships if item.status == "active" and item.member_id != member_id]
    if membership.role == "owner":
        next_owner = active_others[0] if active_others else None
        if next_owner is not None:
            db.execute("UPDATE memberships SET role = 'owner' WHERE id = ?", (next_owner.id,))
        else:
            db.execute("UPDATE conversations SET status = 'archived' WHERE id = ?", (conversation_id,))

    left_at = _utc_now()
    db.execute(
        """
        UPDATE memberships
        SET status = ?, left_at = ?
        WHERE id = ?
        """,
        ("left", _serialize_datetime(left_at), membership.id),
    )
    db.commit()
    return _get_membership(db, conversation_id, member_id) or membership


def pause_conversation_messages(
    db: sqlite3.Connection,
    conversation_id: str,
    acting_member_id: str,
    notice: str | None = None,
) -> Conversation:
    conversation = _require_group_conversation(db, conversation_id)
    _require_admin_member(db, acting_member_id)
    resolved_notice = (notice or "Conversation is closed momentarily.").strip() or "Conversation is closed momentarily."
    db.execute(
        "UPDATE conversations SET messages_paused = 1, message_pause_notice = ? WHERE id = ?",
        (resolved_notice, conversation_id),
    )
    db.commit()
    return _load_conversation(db, conversation_id) or conversation


def resume_conversation_messages(
    db: sqlite3.Connection,
    conversation_id: str,
    acting_member_id: str,
) -> Conversation:
    conversation = _require_group_conversation(db, conversation_id)
    _require_admin_member(db, acting_member_id)
    db.execute(
        "UPDATE conversations SET messages_paused = 0, message_pause_notice = NULL WHERE id = ?",
        (conversation_id,),
    )
    db.commit()
    return _load_conversation(db, conversation_id) or conversation


def create_message(db: sqlite3.Connection, conversation_id: str, sender_id: str, content: str) -> Message:
    conversation = _load_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if conversation.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Conversation is not active")

    sender_exists = db.execute(
        "SELECT 1 FROM members WHERE id = ?",
        (sender_id,),
    ).fetchone()
    if sender_exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sender not found")

    sender = _get_member(db, sender_id)
    if conversation.messages_paused and (sender is None or sender.member_type != ADMIN_MEMBER_TYPE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=conversation.message_pause_notice or "Conversation is closed momentarily.",
        )

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