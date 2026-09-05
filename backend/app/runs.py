"""RunManager: the only scheduler. Creates Runs, binds each to a workspace,
task, session and profile snapshot, runs them through `cc_runner`, persists
every message and status change as an event, enforces concurrency and task
dependencies, cancels, delivers messages into live sessions, and reconciles
after a restart (a run whose process is gone is `interrupted`, never
"running").

The tool servers the model sees are built here too, closed over the Run --
that is how a worker's Room identity and the main agent's worker-control
tools are bound to a real run rather than to whatever the model types.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from claude_agent_sdk import HookMatcher, create_sdk_mcp_server, tool

from .artifacts import ArtifactStore
from .cc_runner import CCRun, RunOutcome, RunSpec, kill_pid, pid_alive
from .db import Database, new_id, now
from .devservers import DevServerManager
from .events import EventBus
from .providers import ProviderProfiles, scrub
from .room import Room
from .workspaces import WorkspaceManager

MAX_CONCURRENT = int(os.environ.get("WORKBENCH_MAX_CONCURRENT_RUNS", "3"))
TERMINAL = {"succeeded", "failed", "cancelled", "exhausted", "interrupted"}

MAIN_SYSTEM = """You are the main agent of a local multi-agent workbench for this project.
You talk to the human in this session and you can delegate background work to workers, each of which is a
separate Claude Code process in its own git worktree. Use the workbench tools (mcp__workbench__*) to spawn,
list, message and cancel workers, and submit_artifact to show the human results. Do not use the built-in
Agent tool; workers are the only way to parallelise. Keep the human informed in plain language: task names,
not ids. When a worker finishes, summarise what it produced and where (workspace branch)."""

WORKER_SYSTEM = """You are a background worker in a local multi-agent workbench. You are running as a separate
process inside your own workspace ({ws_kind}) at {cwd}; only write files there. Your task: "{title}".
Coordinate with other workers through the Room tools (mcp__room__*): call room_claim on each file or directory
before you edit it, room_release when done, and read room_inbox if you are told to. If room_claim reports a
conflict pending a human decision, do not touch that path; the decision will arrive as a message.
When you finish, call submit_artifact (mcp__room__submit_artifact) at least once -- a 'diff' artifact of your
changes plus a short 'markdown' summary -- then stop. The process ending is not acceptance: a human reviews
your artifacts and may send feedback into this session."""


class RunManager:
    def __init__(self, db: Database, bus: EventBus, workspaces: WorkspaceManager, room: Room,
                 artifacts: ArtifactStore, devservers: DevServerManager, profiles: ProviderProfiles):
        self.db, self.bus, self.workspaces, self.room = db, bus, workspaces, room
        self.artifacts, self.devservers, self.profiles = artifacts, devservers, profiles
        self.live: dict[str, CCRun] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(MAX_CONCURRENT)
        self.loop = asyncio.get_running_loop()
        room.deliver = self.deliver

    # ---- creation ----------------------------------------------------------
    def create_run(self, *, project: dict, session: dict, workspace: dict, kind: str, prompt: str,
                   task_id: str | None, profile_id: str | None) -> dict:
        profile = self.profiles.get(profile_id) if profile_id else None
        _, snapshot = self.profiles.env_for(profile)
        attempt = 1 + (self.db.one("SELECT COUNT(*) AS n FROM runs WHERE session_id = ?", [session["id"]]) or {"n": 0})["n"]
        run = self.db.insert("runs", {"id": new_id("run"), "project_id": project["id"], "task_id": task_id, "session_id": session["id"],
                                      "workspace_id": workspace["id"], "profile_id": profile["id"] if profile else None,
                                      "profile_snapshot": snapshot, "kind": kind, "attempt_no": attempt, "prompt": prompt,
                                      "status": "queued", "outcome": None, "cc_session_id": session.get("cc_session_id"),
                                      "pid": None, "started_at": None, "ended_at": None, "result_summary": None, "error": None,
                                      "cost_usd": None, "num_turns": None, "created_at": now()})
        self._emit_status(run, "queued")
        self._schedule(run["id"])
        return run

    def _schedule(self, run_id: str) -> None:
        # Sync FastAPI handlers run on a worker thread; the task must still
        # be created on the server's loop.
        def make() -> None:
            self.tasks[run_id] = self.loop.create_task(self._execute(run_id))
        try:
            if asyncio.get_running_loop() is self.loop:
                make()
                return
        except RuntimeError:
            pass
        self.loop.call_soon_threadsafe(make)

    def _emit_status(self, run: dict, status: str, **extra) -> None:
        self.bus.emit("run_status", {"run_id": run["id"], "status": status, "kind": run["kind"], "task_id": run["task_id"],
                                     "session_id": run["session_id"], "workspace_id": run["workspace_id"], "attempt_no": run["attempt_no"], **extra},
                      project_id=run["project_id"], task_id=run["task_id"], run_id=run["id"], session_id=run["session_id"])

    def _set_status(self, run_id: str, status: str, **fields) -> dict:
        self.db.update("runs", run_id, status=status, **fields)
        run = self.db.one("SELECT * FROM runs WHERE id = ?", [run_id])
        self._emit_status(run, status, **{k: v for k, v in fields.items() if k in ("error", "outcome", "cost_usd", "num_turns", "result_summary")})
        if run["task_id"]:
            self.bus.emit("task", self.task_view(self.db.one("SELECT * FROM tasks WHERE id = ?", [run["task_id"]])),
                          project_id=run["project_id"], task_id=run["task_id"])
        return run

    # ---- execution ---------------------------------------------------------
    async def _execute(self, run_id: str) -> None:
        run = self.db.one("SELECT * FROM runs WHERE id = ?", [run_id])
        if run["task_id"] and not await self._wait_dependencies(run):
            return
        async with self._sem:
            run = self.db.one("SELECT * FROM runs WHERE id = ?", [run_id])
            if run["status"] != "queued":
                return
            spec = self._spec(run)
            cc = CCRun(spec)
            self.live[run_id] = cc
            run = self._set_status(run_id, "running", started_at=now())
            try:
                outcome = await cc.start(lambda t, p: self._on_event(run_id, t, p))
            finally:
                self.live.pop(run_id, None)
            self._finish(run_id, cc, outcome)

    async def _wait_dependencies(self, run: dict) -> bool:
        task = self.db.one("SELECT * FROM tasks WHERE id = ?", [run["task_id"]])
        deps = task["depends_on"] if task else []
        announced = False
        while deps:
            unmet = [d for d in deps if self.task_status(d) not in ("succeeded", "in_review", "ready_to_merge", "done", "approved")]
            if not unmet:
                return True
            if not announced:
                self._set_status(run["id"], "queued", error=None)
                self.bus.emit("run_waiting", {"run_id": run["id"], "waiting_on": unmet}, project_id=run["project_id"], task_id=run["task_id"], run_id=run["id"])
                announced = True
            await asyncio.sleep(3)
            cur = self.db.one("SELECT status FROM runs WHERE id = ?", [run["id"]])
            if cur["status"] != "queued":
                return False
        return True

    def _spec(self, run: dict) -> RunSpec:
        ws = self.workspaces.get(run["workspace_id"])
        proj = self.db.one("SELECT * FROM projects WHERE id = ?", [run["project_id"]])
        profile = self.profiles.get(run["profile_id"]) if run["profile_id"] else None
        env, _ = self.profiles.env_for(profile)
        model = (profile or {}).get("model") or os.environ.get("WORKBENCH_MODEL") or None
        hooks = {"PostToolUse": [HookMatcher(matcher=None, hooks=[self._heartbeat_hook(run)])]}
        if run["kind"] == "main":
            server = self._main_tools(run, proj)
            return RunSpec(run_id=run["id"], cwd=ws["path"], prompt=run["prompt"], resume=run["cc_session_id"], model=model, env=env,
                           mcp_servers={"workbench": server}, system_prompt_append=MAIN_SYSTEM, hooks=hooks, max_turns=80)
        task = self.db.one("SELECT * FROM tasks WHERE id = ?", [run["task_id"]]) or {"title": "?"}
        server = self._room_tools(run)
        shared = ws["edit_mode"] == "shared"
        if shared:
            from .shared_edit import shared_edit_hooks
            hooks["PreToolUse"] = shared_edit_hooks(self, run, ws, proj)
        sys_append = WORKER_SYSTEM.format(ws_kind=ws["kind"] + (" / shared-edit EXPERIMENTAL" if shared else ""), cwd=ws["path"], title=task["title"])
        return RunSpec(run_id=run["id"], cwd=ws["path"], prompt=run["prompt"], resume=run["cc_session_id"], model=model, env=env,
                       mcp_servers={"room": server}, system_prompt_append=sys_append, hooks=hooks, max_turns=100)

    def _heartbeat_hook(self, run: dict):
        async def hook(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
            self.room.heartbeat(run["id"])
            return {}
        return hook

    async def _on_event(self, run_id: str, type_: str, payload: dict) -> None:
        run = self.db.one("SELECT * FROM runs WHERE id = ?", [run_id])
        ids = dict(project_id=run["project_id"], task_id=run["task_id"], run_id=run_id, session_id=run["session_id"])
        if type_ in ("stream_delta", "stream_thinking"):
            self.bus.emit(type_, payload, persist=False, **ids)
            return
        if type_ == "run_init":
            self.db.update("runs", run_id, cc_session_id=payload["cc_session_id"], pid=self.live[run_id].pid if run_id in self.live else None)
            self.db.update("sessions", run["session_id"], cc_session_id=payload["cc_session_id"])
            self.bus.emit("run_init", payload, **ids)
            return
        if type_ in ("assistant_message", "user_message"):
            role = "assistant" if type_ == "assistant_message" else "user"
            author = "tool" if payload.get("origin") == "tool_result" else ("claude" if role == "assistant" else None)
            msg = self.db.insert("messages", {"id": new_id("msg"), "session_id": run["session_id"], "run_id": run_id, "role": role,
                                              "author": author, "blocks": payload["blocks"], "created_at": now()})
            if payload.get("error"):
                msg["error"] = payload["error"]
            self.bus.emit("message", msg, **ids)
            return
        self.bus.emit(type_, payload, **ids)

    def _finish(self, run_id: str, cc: CCRun, outcome: RunOutcome) -> None:
        env, _ = self.profiles.env_for(self.profiles.get(self.db.one("SELECT profile_id FROM runs WHERE id = ?", [run_id])["profile_id"] or ""))
        summary = (outcome.result_text or "")[:4000]
        run = self._set_status(run_id, outcome.status, outcome=outcome.subtype, ended_at=now(), result_summary=summary,
                               error=scrub(outcome.error, env), cost_usd=outcome.cost_usd, num_turns=outcome.num_turns,
                               cc_session_id=cc.cc_session_id or self.db.one("SELECT cc_session_id FROM runs WHERE id = ?", [run_id])["cc_session_id"])
        if outcome.status != "succeeded":
            self.room.release_all_for_run(run_id, reason=outcome.status)
        if cc.unconsumed and outcome.status in ("succeeded", "exhausted"):
            # the turn ended before the model answered these; resume the session with them
            session = self.db.one("SELECT * FROM sessions WHERE id = ?", [run["session_id"]])
            project = self.db.one("SELECT * FROM projects WHERE id = ?", [run["project_id"]])
            ws = self.workspaces.get(run["workspace_id"])
            self.create_run(project=project, session=session, workspace=ws, kind=run["kind"], prompt="\n\n".join(cc.unconsumed),
                            task_id=run["task_id"], profile_id=run["profile_id"])

    # ---- control -----------------------------------------------------------
    async def cancel(self, run_id: str, actor: str = "human") -> dict:
        run = self.db.one("SELECT * FROM runs WHERE id = ?", [run_id])
        if not run:
            return {"ok": False, "error": "no such run"}
        if run["status"] == "queued":
            t = self.tasks.pop(run_id, None)
            if t:
                t.cancel()
            self._set_status(run_id, "cancelled", ended_at=now(), error=f"cancelled by {actor} before start")
            return {"ok": True, "status": "cancelled"}
        cc = self.live.get(run_id)
        if cc is None or cc.finished:
            return {"ok": False, "error": f"run already finished its turn (status {run['status']}); nothing to cancel"}
        self.bus.emit("run_cancelling", {"by": actor}, project_id=run["project_id"], task_id=run["task_id"], run_id=run_id, session_id=run["session_id"])
        await cc.interrupt()
        return {"ok": True, "status": "cancelling"}

    async def deliver(self, run_id: str, text: str, author: str = "room") -> dict:
        """Get `text` in front of the agent behind `run_id`: live -> queued
        turn on its session; finished -> a new run resuming that session."""
        run = self.db.one("SELECT * FROM runs WHERE id = ?", [run_id])
        if not run:
            return {"ok": False, "error": "no such run"}
        self.db.insert("messages", {"id": new_id("msg"), "session_id": run["session_id"], "run_id": run_id, "role": "user",
                                    "author": author, "blocks": [{"type": "text", "text": text}], "created_at": now()})
        cc = self.live.get(run_id)
        if cc is not None and cc.client is not None:
            await cc.send(text)
            self.bus.emit("message", self.db.one("SELECT * FROM messages WHERE session_id = ? ORDER BY created_at DESC LIMIT 1", [run["session_id"]]),
                          project_id=run["project_id"], task_id=run["task_id"], run_id=run_id, session_id=run["session_id"])
            return {"ok": True, "how": "live", "run_id": run_id}
        session = self.db.one("SELECT * FROM sessions WHERE id = ?", [run["session_id"]])
        project = self.db.one("SELECT * FROM projects WHERE id = ?", [run["project_id"]])
        ws = self.workspaces.get(run["workspace_id"])
        new = self.create_run(project=project, session=session, workspace=ws, kind=run["kind"], prompt=text, task_id=run["task_id"],
                              profile_id=run["profile_id"])
        return {"ok": True, "how": "new_run", "run_id": new["id"]}

    # ---- restart reconciliation --------------------------------------------
    def reconcile(self) -> list[dict]:
        fixed = []
        for run in self.db.all("SELECT * FROM runs WHERE status IN ('queued','running')"):
            if pid_alive(run["pid"]):
                kill_pid(run["pid"])
                why = "server restarted; orphaned claude process was terminated"
            else:
                why = "server restarted; claude process was gone"
            self.db.update("runs", run["id"], status="interrupted", ended_at=now(), error=why)
            self.room.release_all_for_run(run["id"], reason="interrupted")
            fixed.append(self._set_status(run["id"], "interrupted", error=why))
        return fixed

    async def shutdown(self) -> None:
        for run_id, cc in list(self.live.items()):
            await cc.interrupt()
        for t in list(self.tasks.values()):
            t.cancel()

    # ---- board -------------------------------------------------------------
    def latest_run(self, task_id: str) -> dict | None:
        return self.db.one("SELECT * FROM runs WHERE task_id = ? ORDER BY created_at DESC LIMIT 1", [task_id])

    def task_status(self, task_id: str) -> str:
        task = self.db.one("SELECT * FROM tasks WHERE id = ?", [task_id])
        run = self.latest_run(task_id)
        if not task:
            return "unknown"
        if task["merge_status"] == "merged":
            return "done"
        if run is None:
            return "todo"
        if run["status"] in ("queued", "running"):
            blocked = self.db.one("SELECT id FROM decisions WHERE status = 'pending' AND blocked_run_id = ?", [run["id"]])
            return "needs_input" if blocked else run["status"]
        if run["status"] != "succeeded":
            return run["status"]
        if task["review_status"] == "approved":
            return "ready_to_merge"
        if task["review_status"] == "changes_requested":
            return "changes_requested"
        return "in_review"

    def task_view(self, task: dict) -> dict:
        run = self.latest_run(task["id"])
        return {**task, "status": self.task_status(task["id"]), "latest_run": run,
                "session_id": run["session_id"] if run else self._session_for_task(task["id"]),
                "runs_count": self.db.one("SELECT COUNT(*) AS n FROM runs WHERE task_id = ?", [task["id"]])["n"]}

    def _session_for_task(self, task_id: str) -> str | None:
        s = self.db.one("SELECT id FROM sessions WHERE task_id = ? ORDER BY created_at DESC LIMIT 1", [task_id])
        return s["id"] if s else None

    # ---- worker lifecycle (used by API and by the main agent's tools) ------
    def spawn_worker(self, project: dict, title: str, instructions: str, *, isolation: str = "worktree",
                     depends_on: list[str] | None = None, profile_id: str | None = None, edit_mode: str = "exclusive",
                     workspace_id: str | None = None) -> dict:
        t = now()
        task = self.db.insert("tasks", {"id": new_id("task"), "project_id": project["id"], "title": title, "description": instructions,
                                        "kind": "worker", "parent_task_id": None, "depends_on": depends_on or [], "review_status": "unreviewed",
                                        "merge_status": "unmerged", "workspace_id": None, "created_at": t, "updated_at": t})
        if workspace_id:
            ws = self.workspaces.get(workspace_id)
        elif isolation == "main":
            ws = self.workspaces.main_workspace(project)
        else:
            try:
                ws = self.workspaces.create_isolated(project, title, edit_mode=edit_mode)
            except Exception:
                self.db.execute("DELETE FROM tasks WHERE id = ?", [task["id"]])
                raise
        self.db.update("tasks", task["id"], workspace_id=ws["id"])
        task["workspace_id"] = ws["id"]
        try:
            return self._start_worker(project, task, ws, title, instructions, profile_id)
        except Exception:
            self.db.execute("DELETE FROM tasks WHERE id = ?", [task["id"]])
            self.workspaces.remove(ws, project)
            raise

    def _start_worker(self, project: dict, task: dict, ws: dict, title: str, instructions: str, profile_id: str | None) -> dict:
        t = now()
        session = self.db.insert("sessions", {"id": new_id("ses"), "project_id": project["id"], "task_id": task["id"], "kind": "worker",
                                              "title": title, "cc_session_id": None, "created_at": t})
        self.bus.emit("task", self.task_view(task), project_id=project["id"], task_id=task["id"])
        self.bus.emit("session", session, project_id=project["id"], task_id=task["id"], session_id=session["id"])
        run = self.create_run(project=project, session=session, workspace=ws, kind="worker", prompt=instructions,
                              task_id=task["id"], profile_id=profile_id)
        return {"task": self.task_view(task), "session": session, "run": run, "workspace": ws}

    def retry_task(self, task: dict, prompt: str | None, profile_id: str | None) -> dict:
        """A new attempt in the same session and workspace. Old runs stay."""
        project = self.db.one("SELECT * FROM projects WHERE id = ?", [task["project_id"]])
        session = self.db.one("SELECT * FROM sessions WHERE task_id = ? ORDER BY created_at DESC LIMIT 1", [task["id"]])
        ws = self.workspaces.get(task["workspace_id"])
        last = self.latest_run(task["id"])
        if last and last["status"] in ("queued", "running"):
            return {"ok": False, "error": "task already has an active run"}
        d = self.workspaces.diff(ws, project) if ws else {"available": False}
        context = ""
        if d.get("available") and d.get("status"):
            context = ("\n\n[Workbench] Previous attempt left these uncommitted changes in your workspace (git status):\n"
                       + d["status"] + "\nReview them before continuing; do not redo work that is already done.")
        text = (prompt or f"Continue the task \"{task['title']}\". The previous attempt ended with status '{last['status'] if last else 'none'}'"
                + (f": {last['error']}" if last and last.get("error") else "") + ".") + context
        run = self.create_run(project=project, session=session, workspace=ws, kind="worker", prompt=text, task_id=task["id"],
                              profile_id=profile_id or (last["profile_id"] if last else None))
        return {"ok": True, "run": run}

    # ---- tool servers ------------------------------------------------------
    def _main_tools(self, run: dict, project: dict):
        mgr = self

        @tool("spawn_worker", "Start a background worker (a separate Claude Code process in its own git worktree) on a task. "
              "Returns the task id. Use depends_on to chain tasks.",
              _schema({"title": "string", "instructions": "string", "depends_on": {"type": "array", "items": {"type": "string"}}}, ["title", "instructions"]))
        async def spawn_worker(args):
            r = mgr.spawn_worker(project, args["title"], args["instructions"], depends_on=args.get("depends_on") or [],
                                 profile_id=run["profile_id"])
            return _ok({"task_id": r["task"]["id"], "run_id": r["run"]["id"], "workspace": r["workspace"]["path"], "branch": r["workspace"]["branch"]})

        @tool("list_workers", "List worker tasks in this project with their real status", {})
        async def list_workers(args):
            rows = [mgr.task_view(t) for t in mgr.db.all("SELECT * FROM tasks WHERE project_id = ? AND kind = 'worker' ORDER BY created_at", [project["id"]])]
            return _ok([{"task_id": t["id"], "title": t["title"], "status": t["status"], "review": t["review_status"],
                         "merge": t["merge_status"], "error": (t["latest_run"] or {}).get("error"),
                         "summary": ((t["latest_run"] or {}).get("result_summary") or "")[:500]} for t in rows])

        @tool("message_worker", "Send a message to a worker (delivered into its session; starts a new run if it finished)", {"task_id": str, "text": str})
        async def message_worker(args):
            last = mgr.latest_run(args["task_id"])
            if not last:
                return _ok({"ok": False, "error": "no run for that task"})
            return _ok(await mgr.deliver(last["id"], args["text"], author="main-agent"))

        @tool("cancel_worker", "Cancel a worker's current run", {"task_id": str})
        async def cancel_worker(args):
            last = mgr.latest_run(args["task_id"])
            if not last:
                return _ok({"ok": False, "error": "no run for that task"})
            return _ok(await mgr.cancel(last["id"], actor="main-agent"))

        @tool("worker_transcript", "Read the last messages of a worker's session", _schema({"task_id": "string", "limit": "integer"}, ["task_id"]))
        async def worker_transcript(args):
            sid = mgr._session_for_task(args["task_id"])
            rows = mgr.db.all("SELECT role, author, blocks, created_at FROM messages WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                              [sid, int(args.get("limit") or 20)])
            return _ok(list(reversed(rows)))

        @tool("submit_artifact", "Show the human a result. kind: markdown|image|html|file|diff. content for markdown/html; path (workspace-relative) for image/file/html; diff needs neither.",
              _schema({"kind": "string", "title": "string", "content": "string", "path": "string"}, ["kind", "title"]))
        async def submit_artifact(args):
            return _ok(mgr.artifacts.submit(run, args["kind"], args["title"], content=args.get("content"), path=args.get("path")))

        return create_sdk_mcp_server("workbench", "1.0.0", [spawn_worker, list_workers, message_worker, cancel_worker, worker_transcript, submit_artifact])

    def _room_tools(self, run: dict):
        mgr = self

        @tool("room_claim", "Claim a file or directory (workspace-relative) before editing it. lease_seconds default 600; every tool call you make renews it.",
              _schema({"path": "string", "note": "string", "lease_seconds": "integer"}, ["path"]))
        async def room_claim(args):
            return _ok(await mgr.room.claim(run, args["path"], args.get("note") or "", args.get("lease_seconds") or 600))

        @tool("room_release", "Release your claim on a path (or all your claims if path is omitted)", _schema({"path": "string"}, []))
        async def room_release(args):
            return _ok(mgr.room.release(run, args.get("path")))

        @tool("room_state", "Who is working on what right now: active claims, overlaps, pending human decisions", {})
        async def room_state(args):
            s = mgr.room.summary(run["project_id"])
            return _ok({"you": mgr.room.identity(run), "claims": [{k: c[k] for k in ("run_id", "task_id", "workspace_id", "path", "note", "expires_at")} for c in s["claims"]],
                        "overlaps": [{"a": o["a"]["path"], "b": o["b"]["path"], "same_workspace": o["same_workspace"]} for o in s["overlaps"]],
                        "pending_decisions": [{"id": d["id"], "path": d["subject"].get("path")} for d in s["pending_decisions"]]})

        @tool("room_broadcast", "Tell every other worker something (they read it with room_inbox)", {"text": str})
        async def room_broadcast(args):
            return _ok(await mgr.room.broadcast(run, args["text"]))

        @tool("room_inbox", "Read messages addressed to you or broadcast to everyone", {})
        async def room_inbox(args):
            return _ok([{"kind": m["kind"], "from_run_id": m["from_run_id"], "payload": m["payload"], "at": m["created_at"]} for m in mgr.room.inbox(run)])

        @tool("room_handoff", "Hand a path you own to another task (by task id) with a note", _schema({"path": "string", "to_task_id": "string", "note": "string"}, ["path", "to_task_id"]))
        async def room_handoff(args):
            return _ok(await mgr.room.handoff(run, args["path"], args["to_task_id"], args.get("note") or ""))

        @tool("submit_artifact", "Show the human a result. kind: markdown|image|html|file|diff. content for markdown/html; path (workspace-relative) for image/file/html; diff needs neither.",
              _schema({"kind": "string", "title": "string", "content": "string", "path": "string"}, ["kind", "title"]))
        async def submit_artifact(args):
            return _ok(mgr.artifacts.submit(run, args["kind"], args["title"], content=args.get("content"), path=args.get("path")))

        @tool("start_devserver", "Start a dev server the human can open (managed by the workbench: port, health, logs). Returns its URL.",
              _schema({"title": "string", "command": "string", "port": "integer"}, ["title", "command"]))
        async def start_devserver(args):
            art = mgr.artifacts.submit(run, "devserver", args["title"], url="pending", meta={"command": args["command"]})
            if not art.get("ok"):
                return _ok(art)
            row = mgr.artifacts.get(art["artifact_id"])
            res = await mgr.devservers.start(row, run, args["command"], args.get("port"))
            if res.get("ok"):
                mgr.db.update("artifacts", row["id"], url=res["url"])
                mgr.bus.emit("artifact", mgr.artifacts.public(mgr.artifacts.get(row["id"])), project_id=run["project_id"], task_id=run["task_id"], run_id=run["id"])
            return _ok({**res, "artifact_id": row["id"]})

        return create_sdk_mcp_server("room", "1.0.0", [room_claim, room_release, room_state, room_broadcast, room_inbox, room_handoff,
                                                       submit_artifact, start_devserver])


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": {k: (v if isinstance(v, dict) else {"type": v}) for k, v in props.items()}, "required": required}


def _ok(data: Any) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}]}
