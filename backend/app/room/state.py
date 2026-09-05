"""Same AgentRoom coordination behaviour as before (claims, CRDT docs,
activity log, escalation queue and decisions) -- carried over onto SQLite
(D2) instead of room_log.jsonl/decisions.jsonl + process-local dicts, so a
restart doesn't lose claims, escalations, or the activity log.

Deliberately not redesigned: no project scoping, no run-bound identity yet
(D4 identity binding is a later round). CRDT docs stay in-memory only, same
as before -- they were never persisted across restarts either.
"""
from __future__ import annotations

import asyncio
import time
import uuid

from pycrdt import Doc, Text

from ..store.db import Database, get_db


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class RoomState:
    """scope=worktree  -> real-time CRDT merge, claim collisions auto-resolved
    scope=person    -> lightweight same-owner overlap tracking, no auto-escalate
    scope=team      -> cross-owner claims on the same path escalate to a human
    """

    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()
        self._docs: dict[str, Doc] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    # ---- pub/sub for the dashboard websocket ----

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def _emit(self, event_type: str, data: dict) -> None:
        msg = {"type": event_type, "data": data}
        for q in list(self._subscribers):
            await q.put(msg)

    # ---- agent status bookkeeping ----

    async def set_agent_status(self, actor_id: str, owner_id: str, worktree_id: str,
                                status: str, current_task: str = "") -> dict:
        agent = self._touch_agent(actor_id, owner_id, worktree_id, status, current_task)
        await self._emit("agent_status", agent)
        return agent

    def _touch_agent(self, actor_id: str, owner_id: str, worktree_id: str,
                      status: str, current_task: str = "") -> dict:
        existing = self.db.query_one(
            "SELECT current_task FROM room_agents WHERE project_id = 'default' AND actor_id = ?", (actor_id,))
        current_task = current_task or (existing["current_task"] if existing else "") or ""
        now = _now()
        self.db.execute(
            "INSERT INTO room_agents (project_id, actor_id, owner_id, worktree_id, status, current_task, updated_at) "
            "VALUES ('default', ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (project_id, actor_id) DO UPDATE SET "
            "owner_id = excluded.owner_id, worktree_id = excluded.worktree_id, status = excluded.status, "
            "current_task = excluded.current_task, updated_at = excluded.updated_at",
            (actor_id, owner_id, worktree_id, status, current_task, now),
        )
        return self._get_agent(actor_id)

    def _get_agent(self, actor_id: str) -> dict:
        row = self.db.query_one(
            "SELECT actor_id AS agent_id, owner_id, worktree_id, status, current_task, updated_at "
            "FROM room_agents WHERE project_id = 'default' AND actor_id = ?", (actor_id,))
        return dict(row)

    # ---- the 5 AgentRoom MCP verbs ----

    async def claim(self, path: str, actor_id: str, owner_id: str,
                     worktree_id: str, scope: str) -> dict:
        async with self._lock:
            existing_row = self.db.query_one(
                "SELECT * FROM claims WHERE project_id = 'default' AND path = ?", (path,))
            existing = dict(existing_row) if existing_row else None

            if scope == "worktree":
                self._get_doc(path)
                self._set_claim(path, actor_id, owner_id, worktree_id, scope)
                agent = self._touch_agent(actor_id, owner_id, worktree_id, "working", f"editing {path}")
                await self._emit("agent_status", agent)
                await self._record("claim", scope, path, actor_id, owner_id, worktree_id,
                                    f"{actor_id} claimed {path} (worktree scope, CRDT-merged)")
                return {"ok": True, "conflict": False, "path": path}

            if scope == "person":
                overlap = existing and existing["owner_id"] == owner_id and existing["actor_id"] != actor_id
                self._set_claim(path, actor_id, owner_id, worktree_id, scope)
                agent = self._touch_agent(actor_id, owner_id, worktree_id, "working", f"editing {path}")
                await self._emit("agent_status", agent)
                note = (f"{actor_id} and {existing['actor_id']} (both owned by {owner_id}) "
                        f"are both touching {path}" if overlap else
                        f"{actor_id} claimed {path} (person scope)")
                await self._record("claim", scope, path, actor_id, owner_id, worktree_id, note)
                return {"ok": True, "conflict": bool(overlap), "path": path}

            # scope == "team": cross-owner collisions escalate to a human,
            # never auto-resolved -> CLAUDE.md hard rule #1 / ARCHITECTURE.md.
            conflict = existing is not None and existing["owner_id"] != owner_id
            self._set_claim(path, actor_id, owner_id, worktree_id, scope)
            agent = self._touch_agent(actor_id, owner_id, worktree_id, "working", f"editing {path}")
            await self._emit("agent_status", agent)
            if conflict:
                self._touch_agent(actor_id, owner_id, worktree_id, "needs_input", f"editing {path}")
                existing_esc_row = self.db.query_one(
                    "SELECT * FROM escalations WHERE status = 'pending' AND path = ? ORDER BY created_at LIMIT 1",
                    (path,))
                esc = dict(existing_esc_row) if existing_esc_row else await self._raise_escalation(
                    path=path, worktree_id=worktree_id, owner_id=owner_id, actor_id=actor_id,
                    description=f"{owner_id}'s agent {actor_id} wants {path}, "
                                f"already claimed by {existing['owner_id']}'s agent {existing['actor_id']}.",
                    risk_summary="跨人（不同队友）改同一个文件，机器不裁决，需要人来决定谁先做/怎么合。",
                )
                await self._record("claim", scope, path, actor_id, owner_id, worktree_id,
                                    f"CONFLICT escalated: {esc['id']}")
                return {"ok": True, "conflict": True, "path": path, "escalation_id": esc["id"]}
            await self._record("claim", scope, path, actor_id, owner_id, worktree_id,
                                f"{actor_id} claimed {path} (team scope, no prior owner)")
            return {"ok": True, "conflict": False, "path": path}

    def _set_claim(self, path: str, actor_id: str, owner_id: str, worktree_id: str, scope: str) -> None:
        self.db.execute(
            "INSERT INTO claims (project_id, path, actor_id, owner_id, worktree_id, scope, claimed_at) "
            "VALUES ('default', ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (project_id, path) DO UPDATE SET "
            "actor_id = excluded.actor_id, owner_id = excluded.owner_id, worktree_id = excluded.worktree_id, "
            "scope = excluded.scope, claimed_at = excluded.claimed_at",
            (path, actor_id, owner_id, worktree_id, scope, _now()),
        )

    def has_claim(self, path: str, actor_id: str) -> bool:
        row = self.db.query_one(
            "SELECT 1 FROM claims WHERE project_id = 'default' AND path = ? AND actor_id = ?", (path, actor_id))
        return row is not None

    async def release(self, path: str, actor_id: str) -> dict:
        async with self._lock:
            row = self.db.query_one(
                "SELECT * FROM claims WHERE project_id = 'default' AND path = ? AND actor_id = ?",
                (path, actor_id))
            if row:
                claim = dict(row)
                self.db.execute(
                    "DELETE FROM claims WHERE project_id = 'default' AND path = ?", (path,))
                agent = self._touch_agent(actor_id, claim["owner_id"], claim["worktree_id"], "ready_to_merge")
                await self._emit("agent_status", agent)
                await self._record("release", claim["scope"], path, actor_id, claim["owner_id"],
                                    claim["worktree_id"], f"{actor_id} released {path}")
                return {"ok": True}
            return {"ok": False, "reason": "not the current claim holder"}

    async def broadcast(self, actor_id: str, owner_id: str, worktree_id: str,
                         scope: str, message: str, path: str = "") -> dict:
        async with self._lock:
            await self._record("note", scope, path, actor_id, owner_id, worktree_id, message)
            return {"ok": True}

    def read(self, since_id: int = 0) -> list[dict]:
        rows = self.db.query(
            "SELECT seq AS id, type, scope, path, actor_id, owner_id, worktree_id, note, ts "
            "FROM room_log WHERE seq > ? ORDER BY seq", (since_id,))
        return [dict(r) for r in rows]

    def state(self) -> dict:
        agents = [dict(r) for r in self.db.query(
            "SELECT actor_id AS agent_id, owner_id, worktree_id, status, current_task, updated_at "
            "FROM room_agents WHERE project_id = 'default'")]
        claims = [dict(r) for r in self.db.query(
            "SELECT path, actor_id, owner_id, worktree_id, scope, claimed_at "
            "FROM claims WHERE project_id = 'default'")]
        escalations = [dict(r) for r in self.db.query(
            "SELECT * FROM escalations WHERE status = 'pending'")]
        return {"agents": agents, "claims": claims, "escalations": escalations}

    # ---- CRDT text (scope=worktree real merge target) ----

    def _get_doc(self, path: str) -> Doc:
        if path not in self._docs:
            doc = Doc()
            doc["content"] = Text()
            self._docs[path] = doc
        return self._docs[path]

    def edit_text(self, path: str, index: int, insert: str = "", delete: int = 0) -> str:
        doc = self._get_doc(path)
        text = doc["content"]
        if delete:
            text.delete(index, index + delete)
        if insert:
            text.insert(index, insert)
        return str(text)

    def read_text(self, path: str) -> str:
        return str(self._get_doc(path)["content"])

    # ---- previews: an agent showing a human what it made, AO-style ----

    async def submit_preview(self, actor_id: str, owner_id: str, title: str,
                              summary: str, html: str = "") -> dict:
        preview_id = uuid.uuid4().hex[:8]
        now = _now()
        self.db.execute(
            "INSERT INTO previews (id, project_id, actor_id, owner_id, title, summary, html, created_at) "
            "VALUES (?, 'default', ?, ?, ?, ?, ?, ?)",
            (preview_id, actor_id, owner_id, title, summary, html, now),
        )
        preview = {"id": preview_id, "actor_id": actor_id, "owner_id": owner_id, "title": title,
                   "summary": summary, "html": html, "created_at": now}
        await self._emit("preview", preview)
        return {"ok": True, "id": preview_id}

    def list_previews(self) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT id, actor_id, owner_id, title, summary, html, created_at "
            "FROM previews WHERE project_id = 'default' ORDER BY created_at")]

    # ---- escalation + decisions ----

    async def _raise_escalation(self, path: str, worktree_id: str, owner_id: str,
                                 actor_id: str, description: str, risk_summary: str) -> dict:
        esc_id = uuid.uuid4().hex[:8]
        now = _now()
        self.db.execute(
            "INSERT INTO escalations (id, project_id, scope, path, worktree_id, owner_id, actor_id, "
            "description, risk_summary, created_at, status) VALUES (?, 'default', 'team', ?, ?, ?, ?, ?, ?, ?, 'pending')",
            (esc_id, path, worktree_id, owner_id, actor_id, description, risk_summary, now),
        )
        esc = {"id": esc_id, "scope": "team", "path": path, "worktree_id": worktree_id, "owner_id": owner_id,
               "actor_id": actor_id, "description": description, "risk_summary": risk_summary,
               "created_at": now, "status": "pending"}
        await self._emit("escalation", esc)
        return esc

    def list_escalations(self, pending_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM escalations" + (" WHERE status = 'pending'" if pending_only else "")
        return [dict(r) for r in self.db.query(sql)]

    async def decide(self, escalation_id: str, decision: str, reason: str, actor: str) -> dict:
        row = self.db.query_one("SELECT * FROM escalations WHERE id = ?", (escalation_id,))
        if not row:
            return {"ok": False, "reason": "unknown escalation id"}
        esc = dict(row)
        new_status = "approved" if decision == "approve" else "rejected"
        now = _now()
        decision_id = uuid.uuid4().hex[:8]
        with self.db.transaction() as conn:
            conn.execute("UPDATE escalations SET status = ? WHERE id = ?", (new_status, escalation_id))
            conn.execute(
                "INSERT INTO decisions (id, escalation_id, path, decision, reason, actor, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (decision_id, escalation_id, esc["path"], decision, reason, actor, now),
            )
        record = {"escalation_id": escalation_id, "path": esc["path"], "decision": decision,
                  "reason": reason, "actor": actor, "decided_at": now}
        await self._emit("decision", record)
        return {"ok": True, "record": record}

    # ---- log persistence ----

    async def _record(self, type_: str, scope: str, path: str, actor_id: str,
                       owner_id: str, worktree_id: str, note: str) -> None:
        now = _now()
        self.db.execute(
            "INSERT INTO room_log (type, scope, path, actor_id, owner_id, worktree_id, note, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (type_, scope, path, actor_id, owner_id, worktree_id, note, now),
        )


room = RoomState()
