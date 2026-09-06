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

from .agents import AgentDefinitions
from .artifacts import ArtifactStore
from .cc_runner import CCRun, RunOutcome, RunSpec, kill_pid, pid_alive
from .db import Database, new_id, now
from .devservers import DevServerManager
from .events import EventBus
from . import preview
from .prompts import build_system_prompt
from .providers import ProviderProfiles, scrub
from .room import Room
from .teams import Teams, mentioned_names, should_run
from .workspaces import WorkspaceManager

MAX_CONCURRENT = int(os.environ.get("WORKBENCH_MAX_CONCURRENT_RUNS", "3"))
DEFAULT_PROJECTS_DIR = os.environ.get("WORKBENCH_PROJECTS_DIR")
TERMINAL = {"succeeded", "failed", "cancelled", "exhausted", "interrupted"}

# What each in-process MCP server exposes. prompts.build_system_prompt is given
# the union for the servers a run actually has, so a rule never names a missing tool.
_TOOLS_ROOM = ("list_agents", "get_session", "message_agent", "ask_human", "room_claim", "room_release", "room_state",
               "room_broadcast", "room_inbox", "room_handoff", "submit_artifact", "preview", "start_devserver")
_TOOLS_WORKBENCH = ("spawn_worker", "list_agent_definitions", "list_workers", "message_worker", "cancel_worker",
                    "kill_session", "rename_session", "worker_transcript")

BACKLOG_HEADER = "[Earlier in this conversation, not yet shown to you]"
BACKLOG_MAX = 30


class RunManager:
    def __init__(self, db: Database, bus: EventBus, workspaces: WorkspaceManager, room: Room,
                 artifacts: ArtifactStore, devservers: DevServerManager, profiles: ProviderProfiles,
                 teams: Teams | None = None, notify=None, agents: AgentDefinitions | None = None, gateway=None):
        self.db, self.bus, self.workspaces, self.room = db, bus, workspaces, room
        self.artifacts, self.devservers, self.profiles = artifacts, devservers, profiles
        self.teams = teams or Teams(db, bus)
        self.agents = agents or AgentDefinitions(db, bus, self.teams)
        self.gateway = gateway
        self.notify = notify
        self.live: dict[str, CCRun] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(MAX_CONCURRENT)
        self.loop = asyncio.get_running_loop()
        room.deliver = self.deliver

    # ---- creation ----------------------------------------------------------
    def create_run(self, *, project: dict, session: dict, workspace: dict, kind: str, prompt: str,
                   task_id: str | None, profile_id: str | None, model: str | None = None, created_by: str | None = None) -> dict:
        # whose key: the run's creator, resolved profile -> user default -> team default -> global -> server login
        profile, model = self.profiles.resolve(profile_id, model, created_by, project)
        _, snapshot = self.profiles.env_for(profile, model)
        attempt = 1 + (self.db.one("SELECT COUNT(*) AS n FROM runs WHERE session_id = ?", [session["id"]]) or {"n": 0})["n"]
        run = self.db.insert("runs", {"id": new_id("run"), "project_id": project["id"], "task_id": task_id, "session_id": session["id"],
                                      "workspace_id": workspace["id"], "profile_id": profile["id"] if profile else None,
                                      "profile_snapshot": snapshot, "kind": kind, "attempt_no": attempt, "prompt": prompt,
                                      "status": "queued", "outcome": None, "cc_session_id": session.get("cc_session_id"),
                                      "pid": None, "started_at": None, "ended_at": None, "result_summary": None, "error": None,
                                      "cost_usd": None, "num_turns": None, "created_at": now(), "created_by": created_by})
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
                if self.gateway is not None:
                    self.gateway.revoke(run_id)
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
        """Everything the model sees comes from the session's agent definition:
        which prompt, which tools, which permission mode, model and caps. The
        definition is read per run, so rebinding a session changes the next
        run without touching the live one."""
        ws = self.workspaces.get(run["workspace_id"])
        proj = self.db.one("SELECT * FROM projects WHERE id = ?", [run["project_id"]])
        session = self.db.one("SELECT * FROM sessions WHERE id = ?", [run["session_id"]]) or {"kind": run["kind"]}
        d, _missing = self.agents.resolve(session.get("agent_definition_id"), run["kind"])
        profile = self.profiles.get(run["profile_id"]) if run["profile_id"] else None
        model = (run.get("profile_snapshot") or {}).get("model") or d.get("model") or os.environ.get("WORKBENCH_MODEL") or None
        env, _ = self.profiles.env_for(profile, model)
        setting_sources = ["user", "project", "local"]
        if profile is not None and self.gateway is not None and self.profiles.needs_routing(profile):
            env, setting_sources = self._proxy_env(run, profile, model, env)
        hooks = {"PostToolUse": [HookMatcher(matcher=None, hooks=[self._heartbeat_hook(run)])]}

        shared = run["kind"] == "worker" and ws["edit_mode"] == "shared"
        if shared:
            from .shared_edit import shared_edit_hooks
            hooks["PreToolUse"] = shared_edit_hooks(self, run, ws, proj)

        servers: dict[str, Any] = {"room": self._room_tools(run)}
        if d["can_spawn"]:
            servers["workbench"] = self._main_tools(run, proj)
        tools = [*_TOOLS_ROOM, *(_TOOLS_WORKBENCH if "workbench" in servers else ())]
        extra = self._run_context(run, session, ws, shared, d)
        prompt = build_system_prompt(d["prompt_role"], project_name=proj["name"] if proj else "", project_root=(proj or {}).get("root_path") or "",
                                     main_session_id=self._main_session_id(run) if run["kind"] == "worker" else None,
                                     tools=tools, extra="\n\n".join(x for x in (extra, d.get("system_prompt") or "") if x))
        self.db.update("runs", run["id"], agent_definition_id=d["id"])
        return RunSpec(run_id=run["id"], cwd=ws["path"], prompt=run["prompt"], resume=run["cc_session_id"], model=model, env=env,
                       mcp_servers=servers, system_prompt_append=prompt, hooks=hooks,
                       allowed_tools=list(d.get("allowed_tools") or []), disallowed_tools=list(d.get("disallowed_tools") or []),
                       permission_mode=d.get("permission_mode") or "acceptEdits", max_turns=d.get("max_turns"),
                       max_budget_usd=d.get("max_budget_usd"), effort=d.get("effort"), setting_sources=setting_sources)

    def _proxy_env(self, run: dict, profile: dict, model: str | None, env: dict) -> tuple[dict, list[str]]:
        """Point this run at a loopback route that speaks Anthropic Messages to
        Claude Code and the vendor's own protocol upstream. The route token is
        what the child process gets; the vendor key stays in this process.

        `user` settings are dropped from the sources: ~/.claude/settings.json
        has an `env` block that Claude Code assigns over the launch
        environment, so the machine's own login would win over the key the
        person picked for this run."""
        upstream = self.profiles.upstream_for(profile, model)
        if not upstream or not upstream.get("credential_present"):
            return env, ["user", "project", "local"]
        url, token = self.gateway.issue(run["id"], upstream)
        routed = {k: v for k, v in env.items() if not k.startswith("ANTHROPIC_") or k in ("ANTHROPIC_CUSTOM_HEADERS",)}
        routed.update({"ANTHROPIC_BASE_URL": url, "ANTHROPIC_AUTH_TOKEN": token, "ANTHROPIC_API_KEY": "",
                       "CLAUDE_CODE_OAUTH_TOKEN": "", **self.profiles.alias_env(profile, model)})
        return routed, ["project", "local"]

    def _run_context(self, run: dict, session: dict, ws: dict, shared: bool, d: dict) -> str:
        """The few facts that are true of this run only, appended to the role
        prompt: where it may write, what it was asked to do, who is in the
        room with it."""
        lines = [f"Your workspace is {ws['kind']}{' / shared-edit EXPERIMENTAL' if shared else ''} at {ws['path']}; only write files there."]
        if run["task_id"]:
            task = self.db.one("SELECT title FROM tasks WHERE id = ?", [run["task_id"]])
            if task:
                lines.append(f'Your task: "{task["title"]}".')
        lines.append(f'You are "{session.get("agent_name") or session.get("title") or d["name"]}" in this workbench; '
                     f"people address you by that name after an @.")
        return "\n".join(lines)

    def _main_session_id(self, run: dict) -> str | None:
        s = self.db.one("SELECT id FROM sessions WHERE project_id = ? AND kind = 'main' ORDER BY created_at LIMIT 1", [run["project_id"]])
        return s["id"] if s else None

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
        env, _ = self.profiles.env_for(self.profiles.get(self.db.one("SELECT profile_id FROM runs WHERE id = ?", [run_id])["profile_id"] or ""), None)
        summary = (outcome.result_text or "")[:4000]
        run = self._set_status(run_id, outcome.status, outcome=outcome.subtype, ended_at=now(), result_summary=summary,
                               error=scrub(outcome.error, env), cost_usd=outcome.cost_usd, num_turns=outcome.num_turns,
                               cc_session_id=cc.cc_session_id or self.db.one("SELECT cc_session_id FROM runs WHERE id = ?", [run_id])["cc_session_id"])
        if outcome.status != "succeeded":
            self.room.release_all_for_run(run_id, reason=outcome.status)
        if run["kind"] == "worker" and run["task_id"] and self.notify:
            task = self.db.one("SELECT title, created_by FROM tasks WHERE id = ?", [run["task_id"]]) or {}
            self.notify.send(task.get("created_by"), "run_finished", f"「{task.get('title')}」运行{_STATUS_ZH.get(outcome.status, outcome.status)}",
                             summary[:200], project_id=run["project_id"], link=f"?p={run['project_id']}&s={run['session_id']}")
        if cc.unconsumed and outcome.status in ("succeeded", "exhausted"):
            # the turn ended before the model answered these; resume the session with them
            session = self.db.one("SELECT * FROM sessions WHERE id = ?", [run["session_id"]])
            project = self.db.one("SELECT * FROM projects WHERE id = ?", [run["project_id"]])
            ws = self.workspaces.get(run["workspace_id"])
            self.create_run(project=project, session=session, workspace=ws, kind=run["kind"], prompt="\n\n".join(cc.unconsumed),
                            task_id=run["task_id"], profile_id=run["profile_id"], created_by=run.get("created_by"))

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

    async def deliver(self, run_id: str, text: str, author: str = "room", *, user_id: str | None = None,
                      model_text: str | None = None, message_id: str | None = None) -> dict:
        """Get `text` in front of the agent behind `run_id`: live -> pushed into
        the turn; still queued -> appended to that run's prompt (never a second
        queued run for one session); finished -> a new run resuming the session.
        `model_text` is what the model sees when it differs from the stored row
        (sender prefix, backlog); `message_id` says the caller stored the row."""
        run = self.db.one("SELECT * FROM runs WHERE id = ?", [run_id])
        if not run:
            return {"ok": False, "error": "no such run"}
        if run["status"] not in ("queued", "running"):
            # the caller named the run that produced an artifact / holds a claim;
            # if its session already has an active attempt, that is the agent now
            alt = self.active_run(run["session_id"])
            if alt:
                run, run_id = alt, alt["id"]
        model_text = model_text or text
        if message_id is None:
            msg = self.db.insert("messages", {"id": new_id("msg"), "session_id": run["session_id"], "run_id": run_id, "role": "user",
                                              "author": author, "user_id": user_id, "blocks": [{"type": "text", "text": text}], "created_at": now()})
            self.bus.emit("message", msg, project_id=run["project_id"], task_id=run["task_id"], run_id=run_id, session_id=run["session_id"])
        cc = self.live.get(run_id)
        if cc is not None and cc.client is not None and not cc.finished:
            await cc.send(model_text)
            return {"ok": True, "how": "live", "run_id": run_id}
        if run["status"] == "queued":
            self.db.update("runs", run_id, prompt=run["prompt"] + "\n\n" + model_text)
            return {"ok": True, "how": "queued", "run_id": run_id}
        session = self.db.one("SELECT * FROM sessions WHERE id = ?", [run["session_id"]])
        project = self.db.one("SELECT * FROM projects WHERE id = ?", [run["project_id"]])
        ws = self.workspaces.get(run["workspace_id"])
        task = self.db.one("SELECT created_by FROM tasks WHERE id = ?", [run["task_id"]]) if run["task_id"] else None
        creator = task["created_by"] if task and task["created_by"] else (user_id or run.get("created_by"))
        new = self.create_run(project=project, session=session, workspace=ws, kind=run["kind"], prompt=model_text, task_id=run["task_id"],
                              profile_id=self.inheritable_profile(run["profile_id"], creator), created_by=creator)
        if message_id:
            self.db.update("messages", message_id, run_id=new["id"])
        return {"ok": True, "how": "new_run", "run_id": new["id"]}

    def active_run(self, session_id: str) -> dict | None:
        return self.db.one("SELECT * FROM runs WHERE session_id = ? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1", [session_id])

    def inheritable_profile(self, profile_id: str | None, user_id: str | None) -> str | None:
        """A follow-up run keeps the previous run's profile only if the person
        it now runs as may use it; otherwise their own default chain applies."""
        if not profile_id:
            return None
        p = self.profiles.get(profile_id)
        u = self.db.one("SELECT * FROM users WHERE id = ?", [user_id]) if user_id else None
        return profile_id if p and (u is None or self.profiles.visible_to(p, u)) else None

    # ---- the one path a human message takes into a session ------------------
    async def send_human(self, session: dict, user: dict, text: str, *, profile_id: str | None = None, model: str | None = None) -> dict:
        """Store + broadcast first (other members see it live), then apply the
        @ rule: one human -> the agent gets everything; several -> only what
        @-mentions it, with the skipped backlog replayed on the next mention.
        Every line the model sees is prefixed `[name (@handle)] `."""
        conv = self.teams.ensure_session_conversation(session, session.get("created_by"))
        run_it = should_run(conv["agent_reply"], self.teams.human_count(conv["id"]), text, session)
        active = self.active_run(session["id"])
        msg = self.db.insert("messages", {"id": new_id("msg"), "session_id": session["id"], "run_id": active["id"] if active else None, "role": "user",
                                          "author": user["display_name"], "user_id": user["id"], "blocks": [{"type": "text", "text": text}],
                                          "created_at": now(), "delivered_to_agent": 0})
        self.bus.emit("message", msg, project_id=session["project_id"], task_id=session.get("task_id"), session_id=session["id"],
                      run_id=active["id"] if active else None)
        self._mention_humans(user, conv, text)
        if not run_it:
            return {"ok": True, "how": "stored", "delivered": False, "message_id": msg["id"],
                    "note": "多人会话：只有 @主 agent（或 @agent、@<agent 名字>）时它才会看到"}
        model_text = self._model_text(session, user, text, msg["id"])
        # the flag says "the model got it": set only once routing succeeded, so a
        # failed delivery is replayed under the backlog header next time
        d = await self.route_to_agent(session, text, model_text, author=user["display_name"], user_id=user["id"], message_id=msg["id"],
                                      profile_id=profile_id, model=model)
        if d.get("ok"):
            self.db.execute("UPDATE messages SET delivered_to_agent = 1 WHERE id = ?", [msg["id"]])
        return d

    async def route_to_agent(self, session: dict, text: str, model_text: str, *, author: str, user_id: str | None,
                             message_id: str | None = None, profile_id: str | None = None, model: str | None = None) -> dict:
        """Live or queued run -> deliver into it; a finished worker -> resume
        it; otherwise a fresh run in the project's main workspace. Also the
        path a group message takes to an agent member (design §2.1)."""
        active = self.active_run(session["id"])
        last = active or (self.db.one("SELECT * FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1", [session["id"]])
                          if session["kind"] == "worker" else None)
        if last:
            return await self.deliver(last["id"], text, author=author, user_id=user_id, model_text=model_text, message_id=message_id)
        if message_id is None:
            msg = self.db.insert("messages", {"id": new_id("msg"), "session_id": session["id"], "run_id": None, "role": "user", "author": author,
                                              "user_id": user_id, "blocks": [{"type": "text", "text": text}], "created_at": now()})
            self.bus.emit("message", msg, project_id=session["project_id"], task_id=session.get("task_id"), session_id=session["id"])
            message_id = msg["id"]
        project = self.db.one("SELECT * FROM projects WHERE id = ?", [session["project_id"]])
        ws = self.workspaces.main_workspace(project)
        run = self.create_run(project=project, session=session, workspace=ws, kind=session["kind"], prompt=model_text, task_id=session.get("task_id"),
                              profile_id=profile_id, model=model, created_by=user_id)
        self.db.update("messages", message_id, run_id=run["id"])
        return {"ok": True, "how": "new_run", "run_id": run["id"], "message_id": message_id}

    def _model_text(self, session: dict, user: dict, text: str, current_id: str) -> str:
        """The current line, preceded by the newest BACKLOG_MAX lines the model
        was not shown (every human line prefixed `[name (@handle)]`); older ones
        are counted in one line so the prompt and the delivered flag agree."""
        where = "WHERE m.session_id = ? AND m.role = 'user' AND m.delivered_to_agent = 0 AND m.id != ?"
        total = self.db.one(f"SELECT COUNT(*) AS n FROM messages m {where}", [session["id"], current_id])["n"]
        skipped = self.db.all(f"SELECT m.author, m.blocks, u.display_name, u.handle FROM messages m LEFT JOIN users u ON u.id = m.user_id {where} "
                              "ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?", [session["id"], current_id, BACKLOG_MAX])
        out = ""
        if skipped:
            lines = [(human_prefix(m) if m.get("handle") else f"[{m['author'] or '?'}] ")
                     + " ".join(b.get("text", "") for b in (m["blocks"] or []) if b.get("type") == "text") for m in reversed(skipped)]
            if total > len(skipped):
                lines.insert(0, f"[... {total - len(skipped)} earlier messages omitted]")
            out = BACKLOG_HEADER + "\n" + "\n".join(lines) + "\n\n"
            self.db.execute("UPDATE messages SET delivered_to_agent = 1 WHERE session_id = ? AND role = 'user' AND delivered_to_agent = 0 AND id != ?",
                            [session["id"], current_id])
        return out + human_prefix(user) + text

    def _mention_humans(self, user: dict, conv: dict, text: str) -> None:
        if not self.notify:
            return
        names = mentioned_names(text)
        if not names:
            return
        link = f"?p={conv['project_id']}&s={conv['session_id']}" if conv["kind"] == "session" else f"?area=chat&c={conv['id']}"
        for m in self.teams.conv_members(conv["id"]):
            if m["member_kind"] == "user" and m["member_id"] != user["id"] and (m.get("handle") or "").lower() in names:
                self.notify.send(m["member_id"], "mention", f"{user['display_name']} 在「{conv['title']}」里 @ 了你", text[:200],
                                 actor_id=user["id"], conversation_id=conv["id"], project_id=conv.get("project_id"), link=link)

    def _notify_artifact(self, run: dict, art: dict) -> None:
        if not self.notify or not art.get("ok"):
            return
        conv = self.teams.conv_of_session(run["session_id"])
        if not conv:
            return
        ident = self.room.identity(run)
        self.notify.send_many([m["member_id"] for m in self.teams.conv_members(conv["id"]) if m["member_kind"] == "user"], "artifact",
                              f"{ident['agent_name'] or 'agent'} 提交了成果「{art.get('title') or art.get('kind')}」",
                              project_id=run["project_id"], conversation_id=conv["id"], link=f"?p={run['project_id']}&s={run['session_id']}")

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
        ws = self.workspaces.get(task["workspace_id"]) if task.get("workspace_id") else None
        return {**task, "status": self.task_status(task["id"]), "latest_run": run,
                "branch": ws.get("branch") if ws else None, "isolation": ws.get("kind") if ws else None,
                "session_id": run["session_id"] if run else self._session_for_task(task["id"]),
                "runs_count": self.db.one("SELECT COUNT(*) AS n FROM runs WHERE task_id = ?", [task["id"]])["n"]}

    def _session_for_task(self, task_id: str) -> str | None:
        s = self.db.one("SELECT id FROM sessions WHERE task_id = ? ORDER BY created_at DESC LIMIT 1", [task_id])
        return s["id"] if s else None

    # ---- worker lifecycle (used by API and by the main agent's tools) ------
    def spawn_worker(self, project: dict, title: str, instructions: str, *, isolation: str = "worktree",
                     depends_on: list[str] | None = None, profile_id: str | None = None, edit_mode: str = "exclusive",
                     workspace_id: str | None = None, model: str | None = None, created_by: str | None = None,
                     members: list[str] | None = None, agent_definition_id: str | None = None) -> dict:
        t = now()
        task = self.db.insert("tasks", {"id": new_id("task"), "project_id": project["id"], "title": title, "description": instructions,
                                        "kind": "worker", "parent_task_id": None, "depends_on": depends_on or [], "review_status": "unreviewed",
                                        "merge_status": "unmerged", "workspace_id": None, "created_at": t, "updated_at": t, "created_by": created_by})
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
            return self._start_worker(project, task, ws, title, instructions, profile_id, model=model, created_by=created_by, members=members,
                                      agent_definition_id=agent_definition_id)
        except Exception:
            self.db.execute("DELETE FROM tasks WHERE id = ?", [task["id"]])
            self.workspaces.remove(ws, project)
            raise

    def _start_worker(self, project: dict, task: dict, ws: dict, title: str, instructions: str, profile_id: str | None,
                      model: str | None = None, created_by: str | None = None, members: list[str] | None = None,
                      agent_definition_id: str | None = None) -> dict:
        t = now()
        session = self.db.insert("sessions", {"id": new_id("ses"), "project_id": project["id"], "task_id": task["id"], "kind": "worker",
                                              "title": title, "cc_session_id": None, "created_at": t, "created_by": created_by, "agent_name": title,
                                              "agent_definition_id": agent_definition_id or self.agents.default_for(project.get("team_id"), "worker")})
        # a worker session is strict: creator + agent (+ whoever the spawner was already sharing with)
        self.teams.ensure_session_conversation(session, created_by, members)
        self.bus.emit("task", self.task_view(task), project_id=project["id"], task_id=task["id"])
        self.bus.emit("session", session, project_id=project["id"], task_id=task["id"], session_id=session["id"])
        run = self.create_run(project=project, session=session, workspace=ws, kind="worker", prompt=instructions,
                              task_id=task["id"], profile_id=profile_id, model=model, created_by=created_by)
        return {"task": self.task_view(task), "session": session, "run": run, "workspace": ws}

    def retry_task(self, task: dict, prompt: str | None, profile_id: str | None, actor: dict | None = None) -> dict:
        """A new attempt in the same session and workspace, run as the person
        who asked (their key, their defaults). Old runs stay."""
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
        if prompt and actor:
            prompt = human_prefix(actor) + prompt        # a human typed it: same prefix as every other human line
        text = (prompt or f"Continue the task \"{task['title']}\". The previous attempt ended with status '{last['status'] if last else 'none'}'"
                + (f": {last['error']}" if last and last.get("error") else "") + ".") + context
        creator = actor["id"] if actor else task.get("created_by")
        run = self.create_run(project=project, session=session, workspace=ws, kind="worker", prompt=text, task_id=task["id"],
                              profile_id=profile_id or self.inheritable_profile(last["profile_id"] if last else None, creator), created_by=creator)
        return {"ok": True, "run": run}

    # ---- agent-to-agent addressing -----------------------------------------
    def _reachable(self, run: dict, session: dict) -> str | None:
        """None if this run may address that session, else the refusal text.
        Reach follows the human membership model of batch 1 rather than the
        project: an agent speaks for the person who started it, so it can
        reach exactly the sessions that person is a member of (AO's
        sessionguard refuses, it does not silently drop)."""
        if session["project_id"] != run["project_id"]:
            return "refused: that session belongs to a different project"
        if session["id"] == run["session_id"]:
            return "refused: that is your own session; just answer in it"
        conv = self.teams.conv_of_session(session["id"])
        if conv is None:
            return None
        owner = run.get("created_by")
        if owner is None:
            return None
        u = self.db.one("SELECT * FROM users WHERE id = ?", [owner])
        if u and self.teams.is_member(u, conv["id"]):
            return None
        return f"refused: {session.get('agent_name') or session['title']} is private to another person's conversation"

    def _find_session(self, run: dict, to: str) -> tuple[dict | None, str | None]:
        """`to` is a session id, a task id, 'main', or an agent's name."""
        db = self.db
        s = db.one("SELECT * FROM sessions WHERE id = ?", [to])
        if not s and to.startswith("task"):
            s = db.one("SELECT * FROM sessions WHERE task_id = ? ORDER BY created_at DESC LIMIT 1", [to])
        if not s and to.strip().lower() in ("main", "orchestrator", "主 agent", "主控"):
            s = db.one("SELECT * FROM sessions WHERE project_id = ? AND kind = 'main' ORDER BY created_at LIMIT 1", [run["project_id"]])
        if not s:
            s = db.one("SELECT * FROM sessions WHERE project_id = ? AND (agent_name = ? OR title = ?) ORDER BY created_at DESC LIMIT 1",
                       [run["project_id"], to, to])
        if not s:
            s = db.one("SELECT * FROM sessions WHERE task_id IN (SELECT id FROM tasks WHERE project_id = ? AND title = ?) "
                       "ORDER BY created_at DESC LIMIT 1", [run["project_id"], to])
        if not s:
            return None, f"refused: no agent called '{to}' in this project (call list_agents to see the names)"
        return s, self._reachable(run, s)

    def _agent_prefix(self, run: dict) -> str:
        me = self.room.identity(run)
        return f"[from agent {me['agent_name'] or me['task_title']} (session {run['session_id']})] "

    def _session_card(self, s: dict) -> dict:
        task = self.db.one("SELECT id, title, status FROM tasks WHERE id = ?", [s["task_id"]]) if s["task_id"] else None
        last = self.db.one("SELECT status, ended_at, result_summary FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1", [s["id"]])
        ws = self.db.one("SELECT path, branch, kind FROM workspaces WHERE id = ("
                         "SELECT workspace_id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1)", [s["id"]])
        d, _ = self.agents.resolve(s.get("agent_definition_id"), s["kind"])
        return {"session_id": s["id"], "task_id": s["task_id"], "title": task["title"] if task else s["title"],
                "agent_name": s.get("agent_name") or s["title"], "kind": s["kind"], "agent_definition_id": d["id"],
                "status": (last or {}).get("status") or "idle", "branch": (ws or {}).get("branch"),
                "workspace": (ws or {}).get("path"), "summary": ((last or {}).get("result_summary") or "")[:300]}

    async def message_agent(self, run: dict, to: str, text: str) -> dict | str:
        """One agent talks to another: same delivery path as a human message,
        with the sender named the way AO names it (`[from <id>]`)."""
        target, refusal = self._find_session(run, to)
        if refusal:
            return refusal
        me = self.room.identity(run)
        body = self._agent_prefix(run) + text
        msg = self.db.insert("messages", {"id": new_id("msg"), "session_id": target["id"], "run_id": None, "role": "user",
                                          "author": me["agent_name"] or "agent", "blocks": [{"type": "text", "text": body}],
                                          "meta": {"from_agent": me["agent_name"], "from_session": run["session_id"]},
                                          "created_at": now(), "delivered_to_agent": 1})
        self.bus.emit("message", msg, project_id=target["project_id"], task_id=target.get("task_id"), session_id=target["id"])
        d = await self.route_to_agent(target, body, body, author=me["agent_name"] or "agent", user_id=None, message_id=msg["id"],
                                      profile_id=self.inheritable_profile(run["profile_id"], target.get("created_by")))
        return {"delivered": bool(d.get("ok")), "how": d.get("how"), "session_id": target["id"], "agent": target.get("agent_name") or target["title"]}

    def ask_human(self, run: dict, question: str, options: list[str]) -> dict:
        """A question posted into the session as an agent message the UI can
        render with one-click answers, plus a notification for every human
        member. The agent is told (in its prompt) to end its turn after this."""
        session = self.db.one("SELECT * FROM sessions WHERE id = ?", [run["session_id"]])
        msg = self.db.insert("messages", {"id": new_id("msg"), "session_id": run["session_id"], "run_id": run["id"], "role": "assistant",
                                          "author": "claude", "blocks": [{"type": "text", "text": question}],
                                          "meta": {"needs_human": True, "options": options}, "created_at": now()})
        self.bus.emit("message", msg, project_id=run["project_id"], task_id=run["task_id"], run_id=run["id"], session_id=run["session_id"])
        conv = self.teams.conv_of_session(run["session_id"])
        if conv and self.notify:
            humans = [m["member_id"] for m in self.teams.conv_members(conv["id"]) if m["member_kind"] == "user"]
            name = session.get("agent_name") or session.get("title") or "agent"
            self.notify.send_many(humans, "question", f"{name} 在等你回答", question[:200],
                                  project_id=run["project_id"], session_id=run["session_id"], conversation_id=conv["id"])
        return {"posted": True, "message_id": msg["id"], "asked": len(options)}

    def set_preview(self, run: dict, target: str, title: str = "") -> dict:
        """Point this session's preview pane at a file or a URL (AO's `ao
        preview`). The revision is bumped even when the target is unchanged:
        calling preview again means "look at this now", and a URL comparison
        alone cannot express that."""
        ws = self.workspaces.get(run["workspace_id"])
        try:
            got = preview.resolve((ws or {}).get("path"), target)
        except preview.PreviewError as e:
            return {"ok": False, "error": str(e)}
        session = self.db.one("SELECT * FROM sessions WHERE id = ?", [run["session_id"]])
        rev = int(session.get("preview_revision") or 0) + 1
        row = {**got, "title": (title or "").strip()[:80] or None, "run_id": run["id"],
               "file_kind": preview.kind_of(got["path"]) if got["kind"] == "file" else "url", "at": now()}
        self.db.update("sessions", run["session_id"], preview=row, preview_revision=rev)
        self.bus.emit("preview", {"session_id": run["session_id"], "preview": row, "preview_revision": rev},
                      project_id=run["project_id"], task_id=run["task_id"], run_id=run["id"], session_id=run["session_id"])
        return {"ok": True, **row, "revision": rev}

    def rename_session(self, session: dict, title: str) -> dict:
        title = (title or "").strip()[:80]
        if not title:
            return {"ok": False, "error": "empty title"}
        self.db.update("sessions", session["id"], title=title, agent_name=title)
        if session["task_id"]:
            self.db.update("tasks", session["task_id"], title=title, updated_at=now())
        row = self.db.one("SELECT * FROM sessions WHERE id = ?", [session["id"]])
        self.bus.emit("session", row, project_id=row["project_id"], session_id=row["id"])
        return {"ok": True, "session_id": row["id"], "title": title}

    # ---- tool servers ------------------------------------------------------
    def _main_tools(self, run: dict, project: dict):
        mgr = self

        @tool("spawn_worker", "Start a background worker (a separate Claude Code process in its own git worktree) on a task. "
              "Returns the task id. title is the short sidebar label (<= 20 characters), instructions the full task. "
              "agent_definition_id picks a different kind of agent (list them with list_agent_definitions).",
              _schema({"title": "string", "instructions": "string", "depends_on": {"type": "array", "items": {"type": "string"}},
                       "agent_definition_id": "string", "model": "string", "isolation": "string", "edit_mode": "string"}, ["title", "instructions"]))
        async def spawn_worker(args):
            conv = mgr.teams.conv_of_session(run["session_id"])
            members = [m["member_id"] for m in mgr.teams.conv_members(conv["id"]) if m["member_kind"] == "user"] if conv else []
            definition = args.get("agent_definition_id")
            if definition:
                d = mgr.agents.get(definition)
                if not d or d["role"] not in ("worker", "any"):
                    return _ok(f"refused: no worker agent definition '{definition}' (call list_agent_definitions)")
            r = mgr.spawn_worker(project, args["title"][:20], args["instructions"], depends_on=args.get("depends_on") or [],
                                 isolation=args.get("isolation") or "worktree", edit_mode=args.get("edit_mode") or "exclusive",
                                 model=args.get("model"), agent_definition_id=definition,
                                 profile_id=run["profile_id"], created_by=run.get("created_by"), members=members)
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

        @tool("list_agent_definitions", "The kinds of agent you can spawn: id, name, what it is for", {})
        async def list_agent_definitions(args):
            rows = mgr.agents.visible_for_project(project, role_in=("worker", "any"))
            return _ok([{"id": d["id"], "name": d["name"], "description": d["description"], "permission": d["permission_mode"]} for d in rows])

        @tool("kill_session", "Terminate an agent's current run. to: a session id, a task id or an agent's name.", {"to": str})
        async def kill_session(args):
            s_, refusal = mgr._find_session(run, args["to"])
            if refusal:
                return _ok(refusal)
            active = mgr.active_run(s_["id"])
            if not active:
                return _ok({"ok": False, "error": "that agent is not running"})
            return _ok(await mgr.cancel(active["id"], actor="main-agent"))

        @tool("rename_session", "Rename an agent's display title (<= 20 characters is best)", {"to": str, "title": str})
        async def rename_session(args):
            s_, refusal = mgr._find_session(run, args["to"])
            if refusal:
                return _ok(refusal)
            return _ok(mgr.rename_session(s_, args["title"]))

        return create_sdk_mcp_server("workbench", "1.0.0", [spawn_worker, list_workers, list_agent_definitions, message_worker,
                                                            cancel_worker, kill_session, rename_session, worker_transcript])

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

        @tool("room_inbox", "Read messages addressed to you or broadcast to everyone. Pass the next_since you got last time to read only what is new.",
              _schema({"since": "string"}, []))
        async def room_inbox(args):
            rows = mgr.room.inbox(run, args.get("since") or None)
            items = [{"kind": m["kind"], "from_run_id": m["from_run_id"], "payload": m["payload"], "at": m["created_at"]} for m in rows]
            # rows come back newest first; the cursor is the newest one seen
            return _ok({"items": items, "next_since": (mgr.room.inbox_cursor(rows[0]) if rows else args.get("since") or "")})

        @tool("list_agents", "List every agent in this project you can reach: name, kind, task, status, branch", {})
        async def list_agents(args):
            rows = mgr.db.all("SELECT * FROM sessions WHERE project_id = ? ORDER BY created_at", [run["project_id"]])
            return _ok([mgr._session_card(x) for x in rows if x["id"] == run["session_id"] or mgr._reachable(run, x) is None])

        @tool("get_session", "Inspect one agent: its state and its last messages", _schema({"session_id": "string", "limit": "integer"}, ["session_id"]))
        async def get_session(args):
            s_, refusal = mgr._find_session(run, args["session_id"])
            if refusal:
                return _ok(refusal)
            rows = mgr.db.all("SELECT role, author, blocks, created_at FROM messages WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                              [s_["id"], min(int(args.get("limit") or 10), 50)])
            return _ok({**mgr._session_card(s_), "messages": list(reversed(rows))})

        @tool("message_agent", "Send a message to another agent. to: a session id, a task id, an agent's name, or 'main' for the orchestrator.",
              {"to": str, "text": str})
        async def message_agent(args):
            return _ok(await mgr.message_agent(run, args["to"], args["text"]))

        @tool("send_message", "Alias of message_agent: send a message to another agent by session id, task id or name.", {"to": str, "text": str})
        async def send_message(args):
            return _ok(await mgr.message_agent(run, args["to"], args["text"]))

        @tool("ask_human", "Ask the people in this session a question, then END YOUR TURN. The answer arrives as the next human message.",
              _schema({"question": "string", "options": {"type": "array", "items": {"type": "string"}}}, ["question"]))
        async def ask_human(args):
            return _ok(mgr.ask_human(run, args["question"], [str(o) for o in (args.get("options") or [])][:6]))

        @tool("room_handoff", "Hand a path you own to another task (by task id) with a note", _schema({"path": "string", "to_task_id": "string", "note": "string"}, ["path", "to_task_id"]))
        async def room_handoff(args):
            return _ok(await mgr.room.handoff(run, args["path"], args["to_task_id"], args.get("note") or ""))

        @tool("submit_artifact", "Show the human a result. kind: markdown|image|html|file|diff. content for markdown/html; path (workspace-relative) for image/file/html; diff needs neither.",
              _schema({"kind": "string", "title": "string", "content": "string", "path": "string"}, ["kind", "title"]))
        async def submit_artifact(args):
            art = mgr.artifacts.submit(run, args["kind"], args["title"], content=args.get("content"), path=args.get("path"))
            mgr._notify_artifact(run, {**art, "title": args["title"]})
            return _ok(art)

        @tool("preview", "Point the human's preview pane at something: a workspace-relative file (HTML, Markdown, image, PDF) "
                         "or an http(s) URL such as a dev server you started. Empty target picks the workspace's entry page. "
                         "Call it as soon as the thing worth looking at exists.",
              _schema({"target": "string", "title": "string"}, []))
        async def preview_tool(args):
            return _ok(mgr.set_preview(run, args.get("target") or "", args.get("title") or ""))

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
                                                       list_agents, get_session, message_agent, send_message, ask_human,
                                                       submit_artifact, preview_tool, start_devserver])


_STATUS_ZH = {"succeeded": "完成", "failed": "失败", "cancelled": "已取消", "exhausted": "用尽额度", "interrupted": "被中断"}


def human_prefix(user: dict) -> str:
    """AO's `[from <id>]` rule with the id replaced by the person."""
    return f"[{user['display_name']} (@{user['handle']})] "


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": {k: (v if isinstance(v, dict) else {"type": v}) for k, v in props.items()}, "required": required}


def _ok(data: Any) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}]}
