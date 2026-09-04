"""Shared in-process state for AgentRoom: claims, CRDT docs, broadcast log,
escalation queue and decisions. One process, so every MCP tool call and every
dashboard request sees the same state directly -- no IPC needed.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pycrdt import Doc, Text

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = DATA_DIR / "room_log.jsonl"
DECISIONS_PATH = DATA_DIR / "decisions.jsonl"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append_jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


@dataclass
class Claim:
    path: str
    actor_id: str
    owner_id: str
    worktree_id: str
    scope: str
    claimed_at: str = field(default_factory=_now)


class RoomState:
    """CRDT-backed shared workspace + claim table + escalation queue.

    scope=worktree  -> real-time CRDT merge, claim collisions auto-resolved
    scope=person    -> lightweight same-owner overlap tracking, no auto-escalate
    scope=team      -> cross-owner claims on the same path escalate to a human
    """

    def __init__(self) -> None:
        self._docs: dict[str, Doc] = {}
        self._claims: dict[str, Claim] = {}
        self._agents: dict[str, dict] = {}
        self._escalations: dict[str, dict] = {}
        self._previews: list[dict] = []
        self._log: list[dict] = []
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._load_log()

    def _load_log(self) -> None:
        # Ids must survive a process restart, or /api/log?since_id and the
        # gate's own reading of room_log.jsonl would disagree with the log
        # actually on disk.
        if not LOG_PATH.exists():
            return
        with LOG_PATH.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self._log.append(json.loads(line))

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
        """Public entry point for callers outside claim/release/broadcast --
        the chat and subagent loops report status this way instead of
        reaching into the claim-flow-only `_touch_agent`."""
        agent = self._touch_agent(actor_id, owner_id, worktree_id, status, current_task)
        await self._emit("agent_status", agent)
        return agent

    def _touch_agent(self, actor_id: str, owner_id: str, worktree_id: str,
                      status: str, current_task: str = "") -> dict:
        agent = self._agents.get(actor_id, {})
        agent.update(
            agent_id=actor_id,
            owner_id=owner_id,
            worktree_id=worktree_id,
            status=status,
            current_task=current_task or agent.get("current_task", ""),
            updated_at=_now(),
        )
        self._agents[actor_id] = agent
        return agent

    # ---- the 5 AgentRoom MCP verbs ----

    async def claim(self, path: str, actor_id: str, owner_id: str,
                     worktree_id: str, scope: str) -> dict:
        async with self._lock:
            existing = self._claims.get(path)

            if scope == "worktree":
                # Same shared buffer: CRDT owns conflict resolution, so a
                # claim here just tracks "who is actively typing", never rejects.
                self._get_doc(path)
                self._claims[path] = Claim(path, actor_id, owner_id, worktree_id, scope)
                agent = self._touch_agent(actor_id, owner_id, worktree_id, "working", f"editing {path}")
                await self._emit("agent_status", agent)
                await self._record("claim", scope, path, actor_id, owner_id, worktree_id,
                                    f"{actor_id} claimed {path} (worktree scope, CRDT-merged)")
                return {"ok": True, "conflict": False, "path": path}

            if scope == "person":
                # Same human, different worktree: warn on overlap, never block.
                overlap = existing and existing.owner_id == owner_id and existing.actor_id != actor_id
                self._claims[path] = Claim(path, actor_id, owner_id, worktree_id, scope)
                agent = self._touch_agent(actor_id, owner_id, worktree_id, "working", f"editing {path}")
                await self._emit("agent_status", agent)
                note = (f"{actor_id} and {existing.actor_id} (both owned by {owner_id}) "
                        f"are both touching {path}" if overlap else
                        f"{actor_id} claimed {path} (person scope)")
                await self._record("claim", scope, path, actor_id, owner_id, worktree_id, note)
                return {"ok": True, "conflict": bool(overlap), "path": path}

            # scope == "team": cross-owner collisions are exactly what the
            # track requires a human to decide, not the machine.
            conflict = existing is not None and existing.owner_id != owner_id
            self._claims[path] = Claim(path, actor_id, owner_id, worktree_id, scope)
            agent = self._touch_agent(actor_id, owner_id, worktree_id, "working", f"editing {path}")
            await self._emit("agent_status", agent)
            if conflict:
                self._touch_agent(actor_id, owner_id, worktree_id, "needs_input", f"editing {path}")
                # One unresolved conflict per path is enough to block the gate;
                # a second agent piling onto the same claim (a retry, or the
                # demo script re-run without a human ever deciding the first
                # one) should not spawn a look-alike card next to it.
                existing_esc = next(
                    (e for e in self._escalations.values()
                     if e["status"] == "pending" and e["path"] == path), None)
                esc = existing_esc or await self._raise_escalation(
                    path=path, worktree_id=worktree_id, owner_id=owner_id, actor_id=actor_id,
                    description=f"{owner_id}'s agent {actor_id} wants {path}, "
                                f"already claimed by {existing.owner_id}'s agent {existing.actor_id}.",
                    risk_summary="跨人（不同队友）改同一个文件，机器不裁决，需要人来决定谁先做/怎么合。",
                )
                await self._record("claim", scope, path, actor_id, owner_id, worktree_id,
                                    f"CONFLICT escalated: {esc['id']}")
                return {"ok": True, "conflict": True, "path": path, "escalation_id": esc["id"]}
            await self._record("claim", scope, path, actor_id, owner_id, worktree_id,
                                f"{actor_id} claimed {path} (team scope, no prior owner)")
            return {"ok": True, "conflict": False, "path": path}

    async def release(self, path: str, actor_id: str) -> dict:
        async with self._lock:
            claim = self._claims.get(path)
            if claim and claim.actor_id == actor_id:
                del self._claims[path]
                agent = self._touch_agent(actor_id, claim.owner_id, claim.worktree_id, "ready_to_merge")
                await self._emit("agent_status", agent)
                await self._record("release", claim.scope, path, actor_id, claim.owner_id,
                                    claim.worktree_id, f"{actor_id} released {path}")
                return {"ok": True}
            return {"ok": False, "reason": "not the current claim holder"}

    async def broadcast(self, actor_id: str, owner_id: str, worktree_id: str,
                         scope: str, message: str, path: str = "") -> dict:
        async with self._lock:
            await self._record("note", scope, path, actor_id, owner_id, worktree_id, message)
            return {"ok": True}

    def read(self, since_id: int = 0) -> list[dict]:
        return self._log[since_id:]

    def state(self) -> dict:
        return {
            "agents": list(self._agents.values()),
            "claims": [c.__dict__ for c in self._claims.values()],
            "escalations": [e for e in self._escalations.values() if e["status"] == "pending"],
        }

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
        preview = {
            "id": uuid.uuid4().hex[:8],
            "actor_id": actor_id,
            "owner_id": owner_id,
            "title": title,
            "summary": summary,
            "html": html,
            "created_at": _now(),
        }
        self._previews.append(preview)
        await self._emit("preview", preview)
        return {"ok": True, "id": preview["id"]}

    def list_previews(self) -> list[dict]:
        return list(self._previews)

    # ---- escalation + decisions ----

    async def _raise_escalation(self, path: str, worktree_id: str, owner_id: str,
                                 actor_id: str, description: str, risk_summary: str) -> dict:
        esc = {
            "id": uuid.uuid4().hex[:8],
            "scope": "team",
            "path": path,
            "worktree_id": worktree_id,
            "owner_id": owner_id,
            "actor_id": actor_id,
            "description": description,
            "risk_summary": risk_summary,
            "created_at": _now(),
            "status": "pending",
        }
        self._escalations[esc["id"]] = esc
        await self._emit("escalation", esc)
        return esc

    def list_escalations(self, pending_only: bool = True) -> list[dict]:
        vals = list(self._escalations.values())
        return [e for e in vals if e["status"] == "pending"] if pending_only else vals

    async def decide(self, escalation_id: str, decision: str, reason: str, actor: str) -> dict:
        esc = self._escalations.get(escalation_id)
        if not esc:
            return {"ok": False, "reason": "unknown escalation id"}
        esc["status"] = "approved" if decision == "approve" else "rejected"
        record = {
            "escalation_id": escalation_id,
            "path": esc["path"],
            "decision": decision,
            "reason": reason,
            "actor": actor,
            "decided_at": _now(),
        }
        _append_jsonl(DECISIONS_PATH, record)
        await self._emit("decision", record)
        return {"ok": True, "record": record}

    # ---- log persistence ----

    async def _record(self, type_: str, scope: str, path: str, actor_id: str,
                       owner_id: str, worktree_id: str, note: str) -> None:
        entry = {
            "id": len(self._log),
            "type": type_,
            "scope": scope,
            "path": path,
            "actor_id": actor_id,
            "owner_id": owner_id,
            "worktree_id": worktree_id,
            "note": note,
            "ts": _now(),
        }
        self._log.append(entry)
        _append_jsonl(LOG_PATH, entry)


room = RoomState()
