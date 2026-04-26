from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from pathlib import Path

from app_env import load_environment


load_environment()

DEFAULT_MEMORY_DATABASE_URL = "file:agent_group_chat?mode=memory&cache=shared"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_MEMORY_DATABASE_URL)
_memory_keeper_connection: sqlite3.Connection | None = None


def resolve_database_path(database_url: str | None = None) -> Path:
	database_url = database_url or DATABASE_URL
	if database_url.startswith("sqlite:///"):
		return Path(database_url.removeprefix("sqlite:///"))
	if database_url.startswith("file:"):
		raise ValueError("Shared in-memory SQLite URLs do not map to filesystem paths")
	return Path(database_url)


def _is_memory_database(database_url: str | None = None) -> bool:
	database_url = database_url or DATABASE_URL
	return database_url.startswith("file:") and "mode=memory" in database_url


def _connect(database_url: str, *, uri: bool) -> sqlite3.Connection:
	connection = sqlite3.connect(database_url, check_same_thread=False, uri=uri)
	connection.row_factory = sqlite3.Row
	connection.execute("PRAGMA foreign_keys = ON")
	return connection


def _ensure_memory_keeper(database_url: str) -> None:
	global _memory_keeper_connection
	if _memory_keeper_connection is None:
		_memory_keeper_connection = _connect(database_url, uri=True)


def create_connection(database_path: str | Path | None = None) -> sqlite3.Connection:
	if database_path is None:
		database_url = DATABASE_URL
		if _is_memory_database(database_url):
			_ensure_memory_keeper(database_url)
			return _connect(database_url, uri=True)
		return _connect(str(resolve_database_path(database_url)), uri=False)

	path = Path(database_path)
	return _connect(str(path), uri=False)


SessionLocal = create_connection


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
	row = connection.execute(
		"SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
		(table_name,),
	).fetchone()
	return row is not None


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
	rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
	return {row["name"] for row in rows}


def _ensure_conversations_schema(connection: sqlite3.Connection) -> None:
	if not _table_exists(connection, "conversations"):
		return

	columns = _table_columns(connection, "conversations")
	if "created_by_member_id" not in columns:
		connection.execute("ALTER TABLE conversations ADD COLUMN created_by_member_id TEXT REFERENCES members(id)")
	if "join_policy" not in columns:
		connection.execute("ALTER TABLE conversations ADD COLUMN join_policy TEXT NOT NULL DEFAULT 'invite_only'")
	if "status" not in columns:
		connection.execute("ALTER TABLE conversations ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
	if "messages_paused" not in columns:
		connection.execute("ALTER TABLE conversations ADD COLUMN messages_paused INTEGER NOT NULL DEFAULT 0")
	if "message_pause_notice" not in columns:
		connection.execute("ALTER TABLE conversations ADD COLUMN message_pause_notice TEXT")


def _ensure_members_schema(connection: sqlite3.Connection) -> None:
	if not _table_exists(connection, "members"):
		return

	columns = _table_columns(connection, "members")
	if "member_type" not in columns:
		connection.execute("ALTER TABLE members ADD COLUMN member_type TEXT NOT NULL DEFAULT 'user_regular'")
	if "capabilities" not in columns:
		connection.execute("ALTER TABLE members ADD COLUMN capabilities TEXT")


def _ensure_memberships_schema(connection: sqlite3.Connection) -> None:
	if not _table_exists(connection, "memberships"):
		return

	columns = _table_columns(connection, "memberships")
	if "status" not in columns:
		connection.execute("ALTER TABLE memberships ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
	if "role" not in columns:
		connection.execute("ALTER TABLE memberships ADD COLUMN role TEXT NOT NULL DEFAULT 'member'")
	if "invited_by_member_id" not in columns:
		connection.execute("ALTER TABLE memberships ADD COLUMN invited_by_member_id TEXT REFERENCES members(id)")
	if "joined_at" not in columns:
		connection.execute("ALTER TABLE memberships ADD COLUMN joined_at TEXT")
	if "left_at" not in columns:
		connection.execute("ALTER TABLE memberships ADD COLUMN left_at TEXT")


def _ensure_members_table(connection: sqlite3.Connection) -> None:
	if not _table_exists(connection, "members") and _table_exists(connection, "agents"):
		connection.execute("ALTER TABLE agents RENAME TO members")


def _backfill_members_from_agents(connection: sqlite3.Connection) -> None:
	if not _table_exists(connection, "agents") or not _table_exists(connection, "members"):
		return

	connection.execute(
		"""
		INSERT INTO members (id, type, member_type, display_name, capabilities, config)
		SELECT agents.id, agents.type, 'user_regular', agents.display_name, NULL, agents.config
		FROM agents
		LEFT JOIN members ON members.id = agents.id
		WHERE members.id IS NULL
		"""
	)


def _backfill_memberships_from_legacy_participants(connection: sqlite3.Connection) -> None:
	if not _table_exists(connection, "conversation_participants") or not _table_exists(connection, "memberships"):
		return

	legacy_columns = _table_columns(connection, "conversation_participants")
	member_column = "member_id" if "member_id" in legacy_columns else "agent_id"

	connection.execute(
		f"""
		INSERT INTO memberships (
			id,
			conversation_id,
			member_id,
			status,
			role,
			invited_by_member_id,
			joined_at,
			left_at
		)
		SELECT
			legacy.id,
			legacy.conversation_id,
			legacy.{member_column},
			'active',
			'member',
			NULL,
			NULL,
			NULL
		FROM conversation_participants AS legacy
		LEFT JOIN memberships
			ON memberships.conversation_id = legacy.conversation_id
			AND memberships.member_id = legacy.{member_column}
		WHERE memberships.id IS NULL
		"""
	)


def _backfill_conversation_defaults(connection: sqlite3.Connection) -> None:
	if not _table_exists(connection, "conversations"):
		return

	connection.execute(
		"""
		UPDATE conversations
		SET created_by_member_id = (
			SELECT memberships.member_id
			FROM memberships
			WHERE memberships.conversation_id = conversations.id
			ORDER BY memberships.rowid ASC
			LIMIT 1
		)
		WHERE created_by_member_id IS NULL
		"""
	)
	connection.execute(
		"UPDATE conversations SET join_policy = 'invite_only' WHERE join_policy IS NULL OR join_policy = ''"
	)
	connection.execute(
		"UPDATE conversations SET status = 'active' WHERE status IS NULL OR status = ''"
	)
	connection.execute(
		"UPDATE conversations SET messages_paused = 0 WHERE messages_paused IS NULL"
	)


def _migrate_existing_schema(connection: sqlite3.Connection) -> None:
	_ensure_members_table(connection)
	_ensure_members_schema(connection)
	_ensure_conversations_schema(connection)
	_ensure_memberships_schema(connection)
	_backfill_members_from_agents(connection)
	_backfill_memberships_from_legacy_participants(connection)
	_backfill_conversation_defaults(connection)


def init_db(database_path: str | Path | None = None) -> None:
	if database_path is None and _is_memory_database(DATABASE_URL):
		_ensure_memory_keeper(DATABASE_URL)
	connection = create_connection(database_path)
	try:
		_migrate_existing_schema(connection)
		connection.executescript(
			"""
			CREATE TABLE IF NOT EXISTS members (
				id TEXT PRIMARY KEY,
				type TEXT NOT NULL,
				member_type TEXT NOT NULL DEFAULT 'user_regular',
				display_name TEXT NOT NULL,
				capabilities TEXT,
				config TEXT
			);

			CREATE TABLE IF NOT EXISTS conversations (
				id TEXT PRIMARY KEY,
				type TEXT NOT NULL,
				title TEXT,
				created_by_member_id TEXT REFERENCES members(id),
				join_policy TEXT NOT NULL DEFAULT 'invite_only',
				status TEXT NOT NULL DEFAULT 'active',
				messages_paused INTEGER NOT NULL DEFAULT 0,
				message_pause_notice TEXT
			);

			CREATE TABLE IF NOT EXISTS memberships (
				id TEXT PRIMARY KEY,
				conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
				member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
				status TEXT NOT NULL DEFAULT 'active',
				role TEXT NOT NULL DEFAULT 'member',
				invited_by_member_id TEXT REFERENCES members(id),
				joined_at TEXT,
				left_at TEXT,
				UNIQUE(conversation_id, member_id)
			);

			CREATE TABLE IF NOT EXISTS messages (
				id TEXT PRIMARY KEY,
				conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
				sender_id TEXT NOT NULL REFERENCES members(id),
				content TEXT NOT NULL,
				created_at TEXT NOT NULL,
				deleted_at TEXT
			);

			CREATE INDEX IF NOT EXISTS idx_memberships_conversation_id ON memberships(conversation_id);
			CREATE INDEX IF NOT EXISTS idx_memberships_member_id ON memberships(member_id);
			CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
			CREATE INDEX IF NOT EXISTS idx_messages_sender_id ON messages(sender_id);
			"""
		)
		connection.commit()
	finally:
		connection.close()


def get_db() -> Generator[sqlite3.Connection, None, None]:
	db = create_connection()
	try:
		yield db
	finally:
		db.close()
