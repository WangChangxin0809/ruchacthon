"""One SQLite file for everything the workbench must remember across a
restart: projects, workspaces, tasks, sessions, runs, messages, artifacts,
room state, decisions, provider profiles, and the ordered event log.

Plain `sqlite3` behind one lock rather than an async driver: every query here
is a point lookup or a small scan, and one process owns the file. WAL mode so
the WebSocket replay (a reader) never waits on a run writing events.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DATA_DIR = Path(os.environ.get("WORKBENCH_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, root_path TEXT NOT NULL UNIQUE,
  is_git INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
  branch TEXT, base_ref TEXT, status TEXT NOT NULL DEFAULT 'active', edit_mode TEXT NOT NULL DEFAULT 'exclusive',
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'worker', parent_task_id TEXT, depends_on TEXT NOT NULL DEFAULT '[]',
  review_status TEXT NOT NULL DEFAULT 'unreviewed', merge_status TEXT NOT NULL DEFAULT 'unmerged',
  workspace_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT, kind TEXT NOT NULL, title TEXT NOT NULL,
  cc_session_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT, session_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
  profile_id TEXT, profile_snapshot TEXT NOT NULL DEFAULT '{}', kind TEXT NOT NULL, attempt_no INTEGER NOT NULL DEFAULT 1,
  prompt TEXT NOT NULL, status TEXT NOT NULL, outcome TEXT, cc_session_id TEXT, pid INTEGER,
  started_at TEXT, ended_at TEXT, result_summary TEXT, error TEXT, cost_usd REAL, num_turns INTEGER,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, run_id TEXT, role TEXT NOT NULL, author TEXT,
  blocks TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, created_at);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, project_id TEXT, task_id TEXT, run_id TEXT,
  session_id TEXT, type TEXT NOT NULL, payload TEXT NOT NULL, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT, run_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
  kind TEXT NOT NULL, title TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'current',
  content TEXT, file_path TEXT, url TEXT, meta TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifact_feedback (
  id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, version INTEGER NOT NULL, author TEXT NOT NULL,
  verdict TEXT NOT NULL, text TEXT NOT NULL, delivered_run_id TEXT, delivery TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS devservers (
  id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, run_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
  command TEXT NOT NULL, port INTEGER NOT NULL, pid INTEGER, status TEXT NOT NULL, health TEXT,
  log_path TEXT NOT NULL, started_at TEXT, ended_at TEXT);
CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, run_id TEXT NOT NULL, task_id TEXT, workspace_id TEXT NOT NULL,
  path TEXT NOT NULL, scope TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
  lease_seconds INTEGER NOT NULL, heartbeat_at TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS room_messages (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, from_run_id TEXT, to_run_id TEXT, kind TEXT NOT NULL,
  payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, subject_kind TEXT NOT NULL, subject TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', decision TEXT, reason TEXT, actor TEXT, decided_at TEXT,
  blocked_run_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS provider_profiles (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, base_url TEXT, model TEXT,
  credential_env TEXT, extra_env TEXT NOT NULL DEFAULT '{}', compat TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chat_channels (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_by TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chat_messages (
  id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, author TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS chat_messages_channel ON chat_messages(channel_id, created_at);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, handle TEXT NOT NULL UNIQUE COLLATE NOCASE, display_name TEXT NOT NULL, email TEXT,
  password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, prefs TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, last_seen_at TEXT);
CREATE TABLE IF NOT EXISTS auth_sessions (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, user_agent TEXT,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, last_used_at TEXT);
CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions(user_id);
CREATE TABLE IF NOT EXISTS teams (
  id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL, owner_id TEXT NOT NULL,
  default_profile_id TEXT, default_model TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS team_members (
  team_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member', joined_at TEXT NOT NULL,
  PRIMARY KEY (team_id, user_id));
CREATE TABLE IF NOT EXISTS invites (
  id TEXT PRIMARY KEY, team_id TEXT NOT NULL, token TEXT NOT NULL UNIQUE, created_by TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'member', max_uses INTEGER, uses INTEGER NOT NULL DEFAULT 0,
  expires_at TEXT NOT NULL, revoked_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, team_id TEXT, project_id TEXT, session_id TEXT UNIQUE,
  title TEXT NOT NULL, owner_id TEXT, dm_key TEXT UNIQUE, is_default INTEGER NOT NULL DEFAULT 0,
  agent_reply TEXT NOT NULL DEFAULT 'auto', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS conversations_team ON conversations(team_id, kind);
CREATE TABLE IF NOT EXISTS conversation_members (
  conversation_id TEXT NOT NULL, member_kind TEXT NOT NULL, member_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'member', added_by TEXT, joined_at TEXT NOT NULL, last_read_at TEXT,
  muted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (conversation_id, member_kind, member_id));
CREATE INDEX IF NOT EXISTS conversation_members_user ON conversation_members(member_kind, member_id);
CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
  link TEXT, conversation_id TEXT, project_id TEXT, actor_id TEXT, read_at TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS notifications_inbox ON notifications(user_id, read_at, created_at);
CREATE TABLE IF NOT EXISTS agent_definitions (
  id TEXT PRIMARY KEY, team_id TEXT, owner_id TEXT, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  trust TEXT NOT NULL DEFAULT 'user', is_default INTEGER NOT NULL DEFAULT 0, role TEXT NOT NULL DEFAULT 'worker',
  prompt_role TEXT NOT NULL DEFAULT 'worker', system_prompt TEXT NOT NULL DEFAULT '',
  allowed_tools TEXT NOT NULL DEFAULT '[]', disallowed_tools TEXT NOT NULL DEFAULT '[]', mcp_servers TEXT NOT NULL DEFAULT '[]',
  model TEXT, permission_mode TEXT NOT NULL DEFAULT 'acceptEdits', max_turns INTEGER, max_budget_usd REAL,
  can_spawn INTEGER NOT NULL DEFAULT 0, effort TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS agent_definitions_team ON agent_definitions(team_id, role);
"""

# Columns added after the first release: (table, column, DDL type/default).
# CREATE IF NOT EXISTS cannot add them to an existing file, so we ALTER once.
MIGRATIONS = [
    ("provider_profiles", "models", "TEXT NOT NULL DEFAULT '[]'"),
    ("provider_profiles", "credential_ref", "TEXT"),
    ("provider_profiles", "display_name", "TEXT"),
    # multi-user (docs/decisions/0005): ids next to the free-text author snapshots
    ("messages", "user_id", "TEXT"),
    ("messages", "delivered_to_agent", "INTEGER NOT NULL DEFAULT 1"),
    ("chat_messages", "user_id", "TEXT"),
    ("sessions", "created_by", "TEXT"),
    ("sessions", "agent_name", "TEXT"),
    ("tasks", "created_by", "TEXT"),
    ("runs", "created_by", "TEXT"),
    ("projects", "team_id", "TEXT"),
    ("projects", "created_by", "TEXT"),
    ("artifact_feedback", "user_id", "TEXT"),
    ("decisions", "actor_id", "TEXT"),
    ("events", "user_id", "TEXT"),
    ("provider_profiles", "owner_id", "TEXT"),
    ("provider_profiles", "team_id", "TEXT"),
    ("provider_profiles", "shared", "INTEGER NOT NULL DEFAULT 0"),
    # agent definitions (docs/decisions/0006): which definition a session is bound to, which one a run actually used
    ("sessions", "agent_definition_id", "TEXT"),
    ("runs", "agent_definition_id", "TEXT"),
    ("messages", "meta", "TEXT NOT NULL DEFAULT '{}'"),
]

SCHEMA_VERSION = 3


def migrate_data(db: "Database") -> None:
    """Idempotent data migration, keyed by settings.schema_version, one block
    per version so a database skips straight to the current one.
    v2: every legacy chat channel and session gets a conversation row
    (channels keep their ch_ id, so chat_messages.channel_id needs no
    rewrite), every session gets its agent as a member, and sessions learn
    the name people @ them by.
    v3: the four built-in agent definitions exist and every session is bound
    to one (main -> orchestrator, worker -> worker)."""
    have = int(db.setting("schema_version", 1) or 1)
    if have >= SCHEMA_VERSION:
        return
    if have < 2:
        db.execute("INSERT OR IGNORE INTO conversations (id, kind, title, owner_id, is_default, created_at, updated_at) "
                   "SELECT id, 'group', name, NULL, CASE WHEN name = '全员' THEN 1 ELSE 0 END, created_at, created_at FROM chat_channels")
        db.execute("INSERT OR IGNORE INTO conversations (id, kind, project_id, session_id, title, created_at, updated_at) "
                   "SELECT 'conv_' || substr(id, 5), 'session', project_id, id, title, created_at, created_at FROM sessions")
        db.execute("INSERT OR IGNORE INTO conversation_members (conversation_id, member_kind, member_id, role, joined_at) "
                   "SELECT c.id, 'agent', s.id, 'agent', s.created_at FROM sessions s JOIN conversations c ON c.session_id = s.id")
        db.execute("UPDATE sessions SET agent_name = CASE kind WHEN 'main' THEN '主 agent' ELSE title END WHERE agent_name IS NULL")
    if have < 3:
        from .agents import seed_builtins     # agents.py imports this module; the seed itself is plain SQL
        seed_builtins(db)
        db.execute("UPDATE sessions SET agent_definition_id = CASE kind WHEN 'main' THEN 'orchestrator' ELSE 'worker' END "
                   "WHERE agent_definition_id IS NULL")
    db.set_setting("schema_version", SCHEMA_VERSION)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Database:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else DATA_DIR / "workbench.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            for table, col, ddl in MIGRATIONS:
                have = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if col not in have:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            migrate_data(self)

    @contextmanager
    def transaction(self):
        """Hold the lock and one BEGIN IMMEDIATE .. COMMIT around a multi-statement
        change that must not interleave with another thread's (first-user
        registration decides "am I first" and inserts in one step)."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key = ?", [key])
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     [key, json.dumps(value, ensure_ascii=False)])

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        row = self.execute(sql, params).fetchone()
        return _row(row) if row else None

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return [_row(r) for r in self.execute(sql, params).fetchall()]

    def insert(self, table: str, row: dict) -> dict:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", [_enc(v) for v in row.values()])
        return row

    def update(self, table: str, id_: str, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*(_enc(v) for v in fields.values()), id_])

    def close(self) -> None:
        with self._lock:
            self._conn.close()


JSON_COLUMNS = {"blocks", "payload", "meta", "profile_snapshot", "extra_env", "compat", "depends_on", "subject", "models", "prefs",
                "allowed_tools", "disallowed_tools", "mcp_servers", "link"}


def _enc(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in JSON_COLUMNS & d.keys():
        if isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d
