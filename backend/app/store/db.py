"""The one SQLite connection AgentRoom's state lives in (D2). No jsonl,
no second in-memory store that could disagree with this file.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DATA_DIR / "agentroom.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


class Database:
    """Thin wrapper around one sqlite3 connection.

    SQLite only ever allows one writer at a time regardless of threading,
    so a single lock around every statement is not a scalability
    compromise here -- it is what SQLite already does internally, made
    explicit so concurrent asyncio tasks don't interleave multi-statement
    transactions (e.g. event seq allocation) into each other.
    """

    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self.conn:
            self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock, self.conn:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def transaction(self):
        """Context manager for a block that must commit-or-rollback as one
        unit (used by event seq allocation, which reads MAX(seq) then
        inserts -- two agents' runs finishing at once must not both read
        the same MAX and collide)."""
        return _Transaction(self)

    def table_names(self) -> list[str]:
        rows = self.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [r["name"] for r in rows]

    def close(self) -> None:
        with self._lock:
            self.conn.close()


class _Transaction:
    def __init__(self, db: Database) -> None:
        self.db = db

    def __enter__(self) -> sqlite3.Connection:
        self.db._lock.acquire()
        return self.db.conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None:
                self.db.conn.commit()
            else:
                self.db.conn.rollback()
        finally:
            self.db._lock.release()
        return False


_default_db: Database | None = None


def get_db() -> Database:
    global _default_db
    if _default_db is None:
        _default_db = Database()
    return _default_db


def reset_db_for_tests(path: Path | None = None) -> Database:
    """Swap in a fresh Database (in-memory or a tmp file). Tests use this
    instead of touching the module-level singleton's internals."""
    global _default_db
    if _default_db is not None:
        _default_db.close()
    _default_db = Database(path) if path else Database(Path(":memory:"))
    return _default_db
