"""Room: who is working on what, so concurrent runs can see each other.

A claim is a *lease* on a project-relative path, held by a Run (identity is
bound by the server when the run's tools are built -- a model cannot claim
as somebody else). Claims expire unless heartbeated; every tool call the run
makes is a heartbeat, so a run that stops talking is a run that lost its
claims.

Two runs claiming the same path in *different* workspaces is an overlap
notice: they cannot corrupt each other's files, they will collide at merge
time, so both are told. Two runs in the *same* workspace on the same path is
a conflict, and per CLAUDE.md hard rule 1 it becomes a pending human
decision; the second claimant is blocked (its run is `needs_input`) until
the decision lands, and the decision is pushed into that run's session.
"""
from __future__ import annotations

import posixpath
from datetime import datetime, timedelta, timezone
from pathlib import PurePath, PurePosixPath
from typing import Awaitable, Callable

from .db import Database, new_id, now
from .events import EventBus

DEFAULT_LEASE = 600
Deliver = Callable[[str, str], Awaitable[dict]]   # (run_id, text) -> delivery info


def normalize_path(raw: str, workspace_path: str, project_root: str) -> str:
    """Project-relative POSIX path, so the same file in two worktrees is
    recognised as the same file."""
    p = raw.strip().replace("\\", "/")
    for base in (workspace_path, project_root):
        b = PurePath(base).as_posix().rstrip("/")
        if p.startswith(b + "/"):
            p = p[len(b) + 1:]
        elif p == b:
            p = "."
    p = posixpath.normpath(p)
    if p.startswith("../") or p == ".." or PurePosixPath(p).is_absolute():
        raise ValueError(f"path escapes the project: {raw}")
    return p


def overlaps(a: str, b: str) -> bool:
    if a == "." or b == ".":
        return True
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


class Room:
    def __init__(self, db: Database, bus: EventBus):
        self.db = db
        self.bus = bus
        self.deliver: Deliver | None = None   # set by RunManager

    # ---- identity ----------------------------------------------------------
    def identity(self, run: dict) -> dict:
        task = self.db.one("SELECT id, title FROM tasks WHERE id = ?", [run["task_id"]]) if run["task_id"] else None
        return {"run_id": run["id"], "task_id": run["task_id"], "task_title": task["title"] if task else run["kind"],
                "workspace_id": run["workspace_id"], "kind": run["kind"]}

    # ---- claims ------------------------------------------------------------
    def active_claims(self, project_id: str) -> list[dict]:
        self.expire()
        return self.db.all("SELECT * FROM claims WHERE project_id = ? AND status = 'active' ORDER BY created_at", [project_id])

    async def claim(self, run: dict, raw_path: str, note: str = "", lease_seconds: int = DEFAULT_LEASE) -> dict:
        ws = self.db.one("SELECT * FROM workspaces WHERE id = ?", [run["workspace_id"]])
        proj = self.db.one("SELECT * FROM projects WHERE id = ?", [run["project_id"]])
        try:
            path = normalize_path(raw_path, ws["path"], proj["root_path"])
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        lease_seconds = max(30, min(int(lease_seconds or DEFAULT_LEASE), 3600))
        mine = self.db.one("SELECT * FROM claims WHERE run_id = ? AND path = ? AND status = 'active'", [run["id"], path])
        if mine:
            self._touch(mine["id"], lease_seconds)
            return {"ok": True, "claim_id": mine["id"], "path": path, "renewed": True, "overlaps": []}
        others = [c for c in self.active_claims(run["project_id"]) if c["run_id"] != run["id"] and overlaps(c["path"], path)]
        same_ws = [c for c in others if c["workspace_id"] == run["workspace_id"]]
        if same_ws:
            return await self._conflict(run, path, note, same_ws[0])
        claim = self._insert_claim(run, path, note, lease_seconds)
        overlap_info = []
        for c in others:
            overlap_info.append({"run_id": c["run_id"], "task_id": c["task_id"], "path": c["path"], "note": c["note"]})
            await self._notify(run["project_id"], from_run=run["id"], to_run=c["run_id"], kind="overlap",
                               payload={"path": path, "their_path": c["path"], "note": note, **self.identity(run)},
                               text=f"[Room] Overlap notice: '{self.identity(run)['task_title']}' (run {run['id']}) also claimed '{path}' "
                                    f"in its own workspace. You hold '{c['path']}'. Coordinate via room_broadcast or expect a merge conflict.")
        return {"ok": True, "claim_id": claim["id"], "path": path, "renewed": False, "overlaps": overlap_info,
                "merge_note": "overlaps are in other workspaces: not a lock, expect merge conflicts" if overlap_info else None}

    def _insert_claim(self, run: dict, path: str, note: str, lease_seconds: int) -> dict:
        t = now()
        exp = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds")
        claim = self.db.insert("claims", {"id": new_id("cl"), "project_id": run["project_id"], "run_id": run["id"],
                                          "task_id": run["task_id"], "workspace_id": run["workspace_id"], "path": path,
                                          "scope": "path", "note": note or "", "status": "active", "lease_seconds": lease_seconds,
                                          "heartbeat_at": t, "expires_at": exp, "created_at": t})
        self.bus.emit("claim", {**claim, "identity": self.identity(run)}, project_id=run["project_id"], task_id=run["task_id"], run_id=run["id"])
        return claim

    async def _conflict(self, run: dict, path: str, note: str, holder: dict) -> dict:
        pending = self.db.one("SELECT * FROM decisions WHERE status = 'pending' AND subject_kind = 'claim_conflict' AND blocked_run_id = ? AND subject LIKE ?",
                              [run["id"], f'%"path": "{path}"%'])
        if pending:
            return {"ok": False, "conflict": True, "decision_id": pending["id"], "status": "pending",
                    "message": "a human decision on this conflict is still pending; wait for it (it will arrive as a message)"}
        subject = {"path": path, "requester": self.identity(run), "holder": self.identity(self.db.one("SELECT * FROM runs WHERE id = ?", [holder["run_id"]])),
                   "holder_claim_id": holder["id"], "note": note}
        d = self.db.insert("decisions", {"id": new_id("dec"), "project_id": run["project_id"], "subject_kind": "claim_conflict",
                                         "subject": subject, "status": "pending", "blocked_run_id": run["id"], "created_at": now()})
        self.bus.emit("decision", d, project_id=run["project_id"], task_id=run["task_id"], run_id=run["id"])
        self.bus.emit("run_blocked", {"reason": "claim_conflict", "decision_id": d["id"], "path": path},
                      project_id=run["project_id"], task_id=run["task_id"], run_id=run["id"])
        return {"ok": False, "conflict": True, "decision_id": d["id"], "status": "pending",
                "holder": subject["holder"],
                "message": "another run in the SAME workspace holds this path. A human must decide. Do not edit this path; "
                           "work on something else or wait -- the decision will arrive as a message in this session."}

    def release(self, run: dict, raw_path: str | None = None) -> dict:
        q = "UPDATE claims SET status = 'released' WHERE run_id = ? AND status = 'active'"
        params: list = [run["id"]]
        if raw_path:
            ws = self.db.one("SELECT * FROM workspaces WHERE id = ?", [run["workspace_id"]])
            proj = self.db.one("SELECT * FROM projects WHERE id = ?", [run["project_id"]])
            path = normalize_path(raw_path, ws["path"], proj["root_path"])
            q += " AND path = ?"
            params.append(path)
        n = self.db.execute(q, params).rowcount
        self.bus.emit("claims_released", {"run_id": run["id"], "path": raw_path, "count": n}, project_id=run["project_id"],
                      task_id=run["task_id"], run_id=run["id"])
        return {"ok": True, "released": n}

    def heartbeat(self, run_id: str) -> int:
        rows = self.db.all("SELECT id, lease_seconds FROM claims WHERE run_id = ? AND status = 'active'", [run_id])
        for r in rows:
            self._touch(r["id"], r["lease_seconds"])
        return len(rows)

    def _touch(self, claim_id: str, lease_seconds: int) -> None:
        exp = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds")
        self.db.update("claims", claim_id, heartbeat_at=now(), expires_at=exp)

    def expire(self) -> list[dict]:
        rows = self.db.all("SELECT * FROM claims WHERE status = 'active' AND expires_at < ?", [now()])
        for r in rows:
            self.db.update("claims", r["id"], status="expired")
            self.bus.emit("claim_expired", r, project_id=r["project_id"], task_id=r["task_id"], run_id=r["run_id"])
        return rows

    def release_all_for_run(self, run_id: str, reason: str) -> None:
        rows = self.db.all("SELECT * FROM claims WHERE run_id = ? AND status = 'active'", [run_id])
        for r in rows:
            self.db.update("claims", r["id"], status="released")
            self.bus.emit("claims_released", {"run_id": run_id, "path": r["path"], "count": 1, "reason": reason},
                          project_id=r["project_id"], task_id=r["task_id"], run_id=run_id)

    # ---- messages ------------------------------------------------------------
    async def _notify(self, project_id: str, *, from_run: str | None, to_run: str | None, kind: str, payload: dict, text: str | None) -> dict:
        msg = self.db.insert("room_messages", {"id": new_id("rm"), "project_id": project_id, "from_run_id": from_run,
                                               "to_run_id": to_run, "kind": kind, "payload": payload, "created_at": now()})
        self.bus.emit("room_message", msg, project_id=project_id, run_id=from_run)
        if to_run and text and self.deliver:
            await self.deliver(to_run, text)
        return msg

    async def broadcast(self, run: dict, text: str) -> dict:
        m = await self._notify(run["project_id"], from_run=run["id"], to_run=None, kind="broadcast",
                               payload={"text": text, **self.identity(run)}, text=None)
        return {"ok": True, "message_id": m["id"]}

    def inbox(self, run: dict, since: str | None = None) -> list[dict]:
        rows = self.db.all("SELECT * FROM room_messages WHERE project_id = ? AND from_run_id IS NOT ? AND (to_run_id IS NULL OR to_run_id = ?) "
                           "AND created_at > ? ORDER BY created_at DESC LIMIT 50",
                           [run["project_id"], run["id"], run["id"], since or ""])
        return rows

    async def handoff(self, run: dict, raw_path: str, to_task_id: str, note: str) -> dict:
        target_task = self.db.one("SELECT * FROM tasks WHERE id = ? AND project_id = ?", [to_task_id, run["project_id"]])
        if not target_task:
            return {"ok": False, "error": f"no task {to_task_id} in this project"}
        ws = self.db.one("SELECT * FROM workspaces WHERE id = ?", [run["workspace_id"]])
        proj = self.db.one("SELECT * FROM projects WHERE id = ?", [run["project_id"]])
        path = normalize_path(raw_path, ws["path"], proj["root_path"])
        mine = self.db.one("SELECT * FROM claims WHERE run_id = ? AND path = ? AND status = 'active'", [run["id"], path])
        if mine:
            self.db.update("claims", mine["id"], status="handed_off")
        target_run = self.db.one("SELECT * FROM runs WHERE task_id = ? ORDER BY created_at DESC LIMIT 1", [to_task_id])
        payload = {"path": path, "note": note, "to_task_id": to_task_id, "from": self.identity(run)}
        text = (f"[Room] Handoff from '{self.identity(run)['task_title']}': you now own '{path}'. Note: {note}. "
                f"Call room_claim on it before editing.")
        m = await self._notify(run["project_id"], from_run=run["id"], to_run=target_run["id"] if target_run else None,
                               kind="handoff", payload=payload, text=text)
        self.bus.emit("handoff", {**payload, "message_id": m["id"], "released_claim": bool(mine)},
                      project_id=run["project_id"], task_id=run["task_id"], run_id=run["id"])
        return {"ok": True, "message_id": m["id"], "delivered_to_run": target_run["id"] if target_run else None,
                "note": None if target_run else "target task has no run yet; it will see the handoff in its inbox when it starts"}

    # ---- decisions ---------------------------------------------------------
    async def decide(self, decision_id: str, decision: str, reason: str, actor: str) -> dict:
        d = self.db.one("SELECT * FROM decisions WHERE id = ?", [decision_id])
        if not d:
            return {"ok": False, "error": "no such decision"}
        if d["status"] != "pending":
            return {"ok": False, "error": f"already decided: {d['decision']}"}
        self.db.update("decisions", decision_id, status="decided", decision=decision, reason=reason, actor=actor, decided_at=now())
        d = self.db.one("SELECT * FROM decisions WHERE id = ?", [decision_id])
        self.bus.emit("decision", d, project_id=d["project_id"], run_id=d["blocked_run_id"])
        effects = []
        if d["subject_kind"] == "claim_conflict":
            effects = await self._apply_claim_decision(d)
        return {"ok": True, "decision": d, "effects": effects}

    async def _apply_claim_decision(self, d: dict) -> list[str]:
        s = d["subject"]
        requester = self.db.one("SELECT * FROM runs WHERE id = ?", [s["requester"]["run_id"]])
        holder_run_id = s["holder"]["run_id"]
        effects = []
        if d["decision"] == "approve":
            self.db.execute("UPDATE claims SET status = 'released' WHERE id = ?", [s["holder_claim_id"]])
            effects.append(f"released holder claim {s['holder_claim_id']}")
            if requester:
                self._insert_claim(requester, s["path"], s.get("note") or "granted by human decision", DEFAULT_LEASE)
                effects.append(f"granted {s['path']} to run {requester['id']}")
            await self._notify(d["project_id"], from_run=None, to_run=holder_run_id, kind="decision", payload=d,
                               text=f"[Room] Human decision ({d['actor']}): '{s['path']}' has been reassigned to task "
                                    f"'{s['requester']['task_title']}'. Reason: {d['reason'] or '-'}. Stop editing that path now.")
            requester_text = f"[Room] Human decision ({d['actor']}): your claim on '{s['path']}' is APPROVED. Reason: {d['reason'] or '-'}. You may edit it."
        else:
            requester_text = (f"[Room] Human decision ({d['actor']}): your claim on '{s['path']}' is REJECTED. Reason: {d['reason'] or '-'}. "
                              f"Do not edit that path; finish what you can without it and report.")
        await self._notify(d["project_id"], from_run=None, to_run=s["requester"]["run_id"], kind="decision", payload=d, text=requester_text)
        self.bus.emit("run_unblocked", {"decision_id": d["id"]}, project_id=d["project_id"], run_id=s["requester"]["run_id"],
                      task_id=requester["task_id"] if requester else None)
        return effects

    def summary(self, project_id: str) -> dict:
        claims = self.active_claims(project_id)
        pending = self.db.all("SELECT * FROM decisions WHERE project_id = ? AND status = 'pending' ORDER BY created_at", [project_id])
        msgs = self.db.all("SELECT * FROM room_messages WHERE project_id = ? ORDER BY created_at DESC LIMIT 100", [project_id])
        overlaps_ = []
        for i, a in enumerate(claims):
            for b in claims[i + 1:]:
                if a["run_id"] != b["run_id"] and overlaps(a["path"], b["path"]):
                    overlaps_.append({"a": a, "b": b, "same_workspace": a["workspace_id"] == b["workspace_id"]})
        return {"claims": claims, "pending_decisions": pending, "messages": msgs, "overlaps": overlaps_}
