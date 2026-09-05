"""The one event bus every run, task and room change goes through (实况文档
§3.2/§3.3). Every event lands in SQLite before any subscriber sees it, so a
client that reconnects with `since_seq` gets exactly what it missed --
never more, never less, no reliance on refreshing the page.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from .db import Database, get_db

EVENT_TYPES = {
    "run.started", "run.output.text", "run.output.thinking", "run.tool.started",
    "run.tool.result", "run.permission.denied", "run.finished", "run.interrupted",
    "task.created", "task.status.changed", "session.created",
    "artifact.created", "artifact.superseded",
    "preview.server.status",
    "room.claim.granted", "room.claim.released", "room.overlap.detected",
    "room.conflict.escalated", "room.handoff", "decision.recorded",
    "provider.verify.result",
}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z"


class EventBus:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._async_lock = asyncio.Lock()

    async def publish(self, project_id: str, type_: str, payload: dict,
                       task_id: str | None = None, run_id: str | None = None) -> dict:
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {type_}")
        event = {
            "id": f"evt_{uuid.uuid4().hex}",
            "project_id": project_id,
            "task_id": task_id,
            "run_id": run_id,
            "type": type_,
            "ts": _now_iso(),
            "payload": payload,
        }
        # seq must be allocated and inserted atomically per project, or two
        # runs finishing at the same instant could both read the same
        # MAX(seq) and collide on the (project_id, seq) unique index.
        async with self._async_lock:
            with self.db.transaction() as conn:
                row = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM events WHERE project_id = ?",
                    (project_id,),
                ).fetchone()
                seq = row["next_seq"]
                conn.execute(
                    "INSERT INTO events (id, seq, project_id, task_id, run_id, type, ts, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (event["id"], seq, project_id, task_id, run_id, type_, event["ts"],
                     json.dumps(payload, ensure_ascii=False)),
                )
        event["seq"] = seq
        await self._broadcast(project_id, event)
        return event

    async def _broadcast(self, project_id: str, event: dict) -> None:
        for q in list(self._subscribers.get(project_id, ())):
            await q.put(event)

    def subscribe(self, project_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(project_id, set()).add(q)
        return q

    def unsubscribe(self, project_id: str, q: asyncio.Queue) -> None:
        self._subscribers.get(project_id, set()).discard(q)

    def since(self, project_id: str, since_seq: int = 0, limit: int = 500) -> list[dict]:
        rows = self.db.query(
            "SELECT id, seq, project_id, task_id, run_id, type, ts, payload FROM events "
            "WHERE project_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
            (project_id, since_seq, limit),
        )
        return [_row_to_event(r) for r in rows]


def _row_to_event(row) -> dict:
    return {
        "id": row["id"],
        "seq": row["seq"],
        "project_id": row["project_id"],
        "task_id": row["task_id"],
        "run_id": row["run_id"],
        "type": row["type"],
        "ts": row["ts"],
        "payload": json.loads(row["payload"]),
    }


_default_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _default_bus
    if _default_bus is None:
        _default_bus = EventBus()
    return _default_bus


def reset_event_bus_for_tests(db: Database) -> EventBus:
    global _default_bus
    _default_bus = EventBus(db)
    return _default_bus
