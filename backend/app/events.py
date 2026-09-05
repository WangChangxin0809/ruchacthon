"""The ordered event log and its live fan-out.

Every state change the UI cares about is appended here with a monotonic
`seq` before anyone is told about it, so a browser that reconnects with
`since=<last seq>` gets exactly the events it missed -- no refresh, no
duplicates. Streaming text deltas are the one exception: they go out live
with `persist=False` because the completed message is persisted anyway, and
a reconnecting client rebuilds from that.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from .db import Database, new_id, now

Listener = Callable[[dict], None]


class EventBus:
    def __init__(self, db: Database):
        self.db = db
        self._listeners: set[Listener] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, fn: Listener) -> Callable[[], None]:
        self._listeners.add(fn)
        return lambda: self._listeners.discard(fn)

    def emit(self, type_: str, payload: dict, *, project_id: str | None = None, task_id: str | None = None,
             run_id: str | None = None, session_id: str | None = None, persist: bool = True) -> dict:
        ev: dict[str, Any] = {"id": new_id("ev"), "project_id": project_id, "task_id": task_id, "run_id": run_id,
                              "session_id": session_id, "type": type_, "payload": payload, "ts": now()}
        if persist:
            cur = self.db.execute(
                "INSERT INTO events (id, project_id, task_id, run_id, session_id, type, payload, ts) VALUES (?,?,?,?,?,?,?,?)",
                [ev["id"], project_id, task_id, run_id, session_id, type_, _dumps(payload), ev["ts"]])
            ev["seq"] = cur.lastrowid
        else:
            ev["seq"] = None
        self._dispatch(ev)
        return ev

    def _dispatch(self, ev: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            for fn in list(self._listeners):
                fn(ev)
        else:
            loop.call_soon_threadsafe(lambda: [fn(ev) for fn in list(self._listeners)])

    def since(self, seq: int, project_id: str | None = None, limit: int = 2000) -> list[dict]:
        if project_id:
            rows = self.db.all("SELECT * FROM events WHERE seq > ? AND (project_id = ? OR project_id IS NULL) ORDER BY seq LIMIT ?",
                               [seq, project_id, limit])
        else:
            rows = self.db.all("SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?", [seq, limit])
        return rows

    def last_seq(self) -> int:
        row = self.db.one("SELECT MAX(seq) AS s FROM events")
        return int(row["s"] or 0) if row else 0


def _dumps(payload: dict) -> str:
    import json
    return json.dumps(payload, ensure_ascii=False, default=str)
