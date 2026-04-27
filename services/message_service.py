from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from fastapi import HTTPException, status

from models import Conversation, Member, Membership, Message, SimulationTraceEventRecord, SimulationTraceRun


DEFAULT_MEMBER_TYPE = "user_regular"
GROUP_CREATOR_MEMBER_TYPES = {"user_premium", "admin"}
GROUP_MANAGER_MEMBER_TYPES = {"user_premium", "admin"}
ADMIN_MEMBER_TYPE = "admin"
VALID_MEMBER_TYPES = {DEFAULT_MEMBER_TYPE, "user_premium", ADMIN_MEMBER_TYPE}
BASE_MEMBER_CAPABILITIES = {
    "read_conversations": True,
    "send_messages": True,
    "create_direct_conversations": True,
    "create_group_conversations": False,
    "leave_conversations": True,
    "manage_memberships": False,
    "pause_group_messages": False,
}
MEMBER_TYPE_CAPABILITY_OVERRIDES = {
    DEFAULT_MEMBER_TYPE: {},
    "user_premium": {
        "create_group_conversations": True,
        "manage_memberships": True,
    },
    ADMIN_MEMBER_TYPE: {
        "create_group_conversations": True,
        "manage_memberships": True,
        "pause_group_messages": True,
    },
}
VALID_CAPABILITY_KEYS = set(BASE_MEMBER_CAPABILITIES)


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
    capabilities = json.loads(row["capabilities"]) if "capabilities" in row.keys() and row["capabilities"] else None
    keys = set(row.keys())
    return Member(
        id=row["id"],
        type=row["type"],
        member_type=row["member_type"] if "member_type" in keys else DEFAULT_MEMBER_TYPE,
        display_name=row["display_name"],
        capabilities=capabilities,
        config=config,
    )


def _normalize_capability_overrides(capabilities: dict | None) -> dict[str, bool] | None:
    if capabilities is None:
        return None

    normalized: dict[str, bool] = {}
    invalid_keys = [key for key in capabilities if key not in VALID_CAPABILITY_KEYS]
    if invalid_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid capability overrides", "invalid_capabilities": sorted(invalid_keys)},
        )

    for key, value in capabilities.items():
        if not isinstance(value, bool):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Capability '{key}' must be a boolean",
            )
        normalized[key] = value
    return normalized


def get_effective_member_capabilities(member: Member) -> dict[str, bool]:
    capabilities = dict(BASE_MEMBER_CAPABILITIES)
    capabilities.update(MEMBER_TYPE_CAPABILITY_OVERRIDES.get(member.member_type, {}))
    capabilities.update(_normalize_capability_overrides(member.capabilities) or {})
    return capabilities


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


def _row_to_simulation_trace_event(row: sqlite3.Row) -> SimulationTraceEventRecord:
    return SimulationTraceEventRecord(
        id=row["id"],
        trace_run_id=row["trace_run_id"],
        sequence_index=row["sequence_index"],
        event_type=row["event_type"],
        recorded_at=_parse_datetime(row["recorded_at"]) or _utc_now(),
        round_index=row["round_index"],
        member_id=row["member_id"],
        member_name=row["member_name"],
        conversation_id=row["conversation_id"],
        details=json.loads(row["details"]) if row["details"] else {},
    )


def _load_simulation_trace_events(db: sqlite3.Connection, trace_run_id: str) -> list[SimulationTraceEventRecord]:
    rows = db.execute(
        """
        SELECT id, trace_run_id, sequence_index, event_type, recorded_at, round_index,
               member_id, member_name, conversation_id, details
        FROM simulation_trace_events
        WHERE trace_run_id = ?
        ORDER BY sequence_index ASC
        """,
        (trace_run_id,),
    ).fetchall()
    return [_row_to_simulation_trace_event(row) for row in rows]


def _row_to_simulation_trace_run(db: sqlite3.Connection, row: sqlite3.Row) -> SimulationTraceRun:
    return SimulationTraceRun(
        id=row["id"],
        scenario_type=row["scenario_type"],
        root_conversation_id=row["root_conversation_id"],
        created_at=_parse_datetime(row["created_at"]) or _utc_now(),
        final_choice=row["final_choice"],
        consensus_reached=bool(row["consensus_reached"]),
        stopped_early=bool(row["stopped_early"]),
        stop_requested_by_member_id=row["stop_requested_by_member_id"],
        events=_load_simulation_trace_events(db, row["id"]),
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
        "SELECT id, type, member_type, display_name, capabilities, config FROM members WHERE id = ?",
        (member_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_member(row)


def _require_member_capability(
    db: sqlite3.Connection,
    member_id: str,
    capability_key: str,
    detail: str,
    *,
    missing_detail: str = "Member not found",
) -> Member:
    member = _get_member(db, member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=missing_detail)
    if not get_effective_member_capabilities(member).get(capability_key, False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return member


def _require_group_creation_permission(db: sqlite3.Connection, created_by_member_id: str) -> Member:
    member = _require_member_capability(
        db,
        created_by_member_id,
        "create_group_conversations",
        "Member cannot create group conversations",
    )
    if member.member_type not in GROUP_CREATOR_MEMBER_TYPES and not (member.capabilities or {}).get("create_group_conversations"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Member cannot create group conversations")
    return member


def _require_direct_conversation_permission(db: sqlite3.Connection, created_by_member_id: str) -> Member:
    return _require_member_capability(
        db,
        created_by_member_id,
        "create_direct_conversations",
        "Member cannot create direct conversations",
    )


def _require_admin_member(db: sqlite3.Connection, acting_member_id: str) -> Member:
    return _require_member_capability(
        db,
        acting_member_id,
        "pause_group_messages",
        "Member cannot control message windows",
        missing_detail="Acting member not found",
    )


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
    acting_member = _require_member_capability(
        db,
        acting_member_id,
        "manage_memberships",
        "Acting member cannot manage memberships in this conversation",
        missing_detail="Acting member not found",
    )
    if acting_member.member_type == ADMIN_MEMBER_TYPE:
        membership = _get_membership(db, conversation_id, acting_member_id)
        return membership or Membership(conversation_id=conversation_id, member_id=acting_member_id, status="active", role="owner")
    if acting_member.member_type not in GROUP_MANAGER_MEMBER_TYPES and not get_effective_member_capabilities(acting_member)["manage_memberships"]:
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


def _row_to_conversation(db: sqlite3.Connection, row: sqlite3.Row) -> Conversation:
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
    return _row_to_conversation(db, row)


def _list_member_visible_conversations(db: sqlite3.Connection, member_id: str) -> list[Conversation]:
    rows = db.execute(
        """
        SELECT DISTINCT conversations.id, conversations.type, conversations.title, conversations.created_by_member_id,
               conversations.join_policy, conversations.status, conversations.messages_paused, conversations.message_pause_notice
        FROM conversations
        JOIN memberships ON memberships.conversation_id = conversations.id
        WHERE memberships.member_id = ? AND memberships.status = 'active'
        ORDER BY conversations.title ASC, conversations.id ASC
        """,
        (member_id,),
    ).fetchall()
    return [_row_to_conversation(db, row) for row in rows]


def create_member(
    db: sqlite3.Connection,
    runtime_type: str,
    display_name: str,
    config: dict | None = None,
    member_type: str = DEFAULT_MEMBER_TYPE,
    capabilities: dict | None = None,
) -> Member:
    if member_type not in VALID_MEMBER_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid member_type")

    normalized_capabilities = _normalize_capability_overrides(capabilities)

    member = Member(
        type=runtime_type,
        member_type=member_type,
        display_name=display_name,
        capabilities=normalized_capabilities,
        config=config,
    )
    db.execute(
        "INSERT INTO members (id, type, member_type, display_name, capabilities, config) VALUES (?, ?, ?, ?, ?, ?)",
        (
            member.id,
            member.type,
            member.member_type,
            member.display_name,
            json.dumps(member.capabilities) if member.capabilities is not None else None,
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
    capabilities: dict | None = None,
) -> Member:
    return create_member(
        db,
        runtime_type=agent_type,
        display_name=display_name,
        config=config,
        member_type=member_type,
        capabilities=capabilities,
    )


def list_members(db: sqlite3.Connection) -> list[Member]:
    rows = db.execute(
        "SELECT id, type, member_type, display_name, capabilities, config FROM members ORDER BY display_name ASC, id ASC"
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
    if conversation_type == "group":
        _require_group_creation_permission(db, requested_ids[0])
    elif conversation_type == "direct":
        _require_direct_conversation_permission(db, requested_ids[0])

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
    return [_row_to_conversation(db, row) for row in rows]


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
    _require_member_capability(db, member_id, "leave_conversations", "Member cannot leave conversations")
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

    sender = _require_member_capability(
        db,
        sender_id,
        "send_messages",
        "Member cannot send messages",
        missing_detail="Sender not found",
    )
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


def create_simulation_trace_run(
    db: sqlite3.Connection,
    *,
    scenario_type: str,
    root_conversation_id: str,
    final_choice: str | None,
    consensus_reached: bool,
    stopped_early: bool,
    stop_requested_by_member_id: str | None,
    events: list[dict],
) -> SimulationTraceRun:
    conversation = _load_conversation(db, root_conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if stop_requested_by_member_id is not None and _get_member(db, stop_requested_by_member_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stop-requesting member not found")

    trace_run = SimulationTraceRun(
        scenario_type=scenario_type,
        root_conversation_id=root_conversation_id,
        final_choice=final_choice,
        consensus_reached=consensus_reached,
        stopped_early=stopped_early,
        stop_requested_by_member_id=stop_requested_by_member_id,
    )
    db.execute(
        """
        INSERT INTO simulation_trace_runs (
            id, scenario_type, root_conversation_id, created_at, final_choice,
            consensus_reached, stopped_early, stop_requested_by_member_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trace_run.id,
            trace_run.scenario_type,
            trace_run.root_conversation_id,
            _serialize_datetime(trace_run.created_at),
            trace_run.final_choice,
            1 if trace_run.consensus_reached else 0,
            1 if trace_run.stopped_early else 0,
            trace_run.stop_requested_by_member_id,
        ),
    )

    stored_events: list[SimulationTraceEventRecord] = []
    for sequence_index, event_payload in enumerate(events):
        recorded_at = _parse_datetime(event_payload.get("recorded_at")) or _utc_now()
        member_id = event_payload.get("member_id")
        if member_id is not None and _get_member(db, member_id) is None:
            member_id = None
        conversation_id = event_payload.get("conversation_id")
        if conversation_id is not None and _load_conversation(db, conversation_id) is None:
            conversation_id = None
        event = SimulationTraceEventRecord(
            trace_run_id=trace_run.id,
            sequence_index=sequence_index,
            event_type=event_payload["event_type"],
            recorded_at=recorded_at,
            round_index=event_payload.get("round_index"),
            member_id=member_id,
            member_name=event_payload.get("member_name"),
            conversation_id=conversation_id,
            details=event_payload.get("details") or {},
        )
        db.execute(
            """
            INSERT INTO simulation_trace_events (
                id, trace_run_id, sequence_index, event_type, recorded_at, round_index,
                member_id, member_name, conversation_id, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.trace_run_id,
                event.sequence_index,
                event.event_type,
                _serialize_datetime(event.recorded_at),
                event.round_index,
                event.member_id,
                event.member_name,
                event.conversation_id,
                json.dumps(event.details),
            ),
        )
        stored_events.append(event)

    db.commit()
    trace_run.events = stored_events
    return trace_run


def list_conversation_simulation_trace_runs(db: sqlite3.Connection, conversation_id: str) -> list[SimulationTraceRun]:
    conversation = _load_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    rows = db.execute(
        """
        SELECT id, scenario_type, root_conversation_id, created_at, final_choice,
               consensus_reached, stopped_early, stop_requested_by_member_id
        FROM simulation_trace_runs
        WHERE root_conversation_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (conversation_id,),
    ).fetchall()
    return [_row_to_simulation_trace_run(db, row) for row in rows]


def get_simulation_trace_run(db: sqlite3.Connection, trace_run_id: str) -> SimulationTraceRun:
    row = db.execute(
        """
        SELECT id, scenario_type, root_conversation_id, created_at, final_choice,
               consensus_reached, stopped_early, stop_requested_by_member_id
        FROM simulation_trace_runs
        WHERE id = ?
        """,
        (trace_run_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation trace run not found")
    return _row_to_simulation_trace_run(db, row)


def get_member_access_context(db: sqlite3.Connection, member_id: str) -> tuple[Member, dict[str, bool], list[Conversation]]:
    member = _get_member(db, member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    capabilities = get_effective_member_capabilities(member)
    visible_conversations = _list_member_visible_conversations(db, member_id) if capabilities["read_conversations"] else []
    return member, capabilities, visible_conversations


def list_member_visible_conversations(db: sqlite3.Connection, member_id: str) -> list[Conversation]:
    _require_member_capability(db, member_id, "read_conversations", "Member cannot read conversations")
    return _list_member_visible_conversations(db, member_id)


def list_member_visible_messages(
    db: sqlite3.Connection,
    member_id: str,
    conversation_id: str,
    include_deleted: bool = False,
) -> list[Message]:
    _require_member_capability(db, member_id, "read_conversations", "Member cannot read conversations")
    membership = _get_membership(db, conversation_id, member_id)
    if membership is None or membership.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Member cannot read this conversation")
    return list_messages(db, conversation_id=conversation_id, include_deleted=include_deleted)


def create_member_message(db: sqlite3.Connection, member_id: str, conversation_id: str, content: str) -> Message:
    _require_member_capability(db, member_id, "send_messages", "Member cannot send messages")
    return create_message(db, conversation_id=conversation_id, sender_id=member_id, content=content)


def create_member_group_conversation(
    db: sqlite3.Connection,
    member_id: str,
    title: str | None = None,
    member_ids: list[str] | None = None,
) -> Conversation:
    _require_member_capability(db, member_id, "create_group_conversations", "Member cannot create group conversations")
    return create_group_conversation(db, created_by_member_id=member_id, title=title, member_ids=member_ids)


def leave_member_conversation(db: sqlite3.Connection, member_id: str, conversation_id: str) -> Membership:
    _require_member_capability(db, member_id, "leave_conversations", "Member cannot leave conversations")
    return leave_conversation(db, conversation_id=conversation_id, member_id=member_id)


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