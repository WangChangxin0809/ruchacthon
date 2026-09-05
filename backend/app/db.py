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
"""


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


JSON_COLUMNS = {"blocks", "payload", "meta", "profile_snapshot", "extra_env", "compat", "depends_on", "subject"}


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
