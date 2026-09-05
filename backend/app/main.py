"""FastAPI entry point: REST for state, one WebSocket for the ordered event
stream. All state lives in `Database`; all execution goes through
`RunManager`. Restart recovery happens in `lifespan` before any request is
served, so a client never sees a run as `running` that no process backs.

Every request is resolved to a principal (`request.state.user`) before a
route runs; authorship and visibility come from it, never from the body
(ARCHITECTURE invariant 7). Auth, teams and conversations routes live in
`routes_auth.py` / `routes_conversations.py`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ccconfig, preview, routes_agents, routes_auth, routes_conversations, routes_providers
from .agents import AgentDefinitions
from .artifacts import ArtifactStore
from .auth import Auth, admin, bearer_of, me
from .cc_runner import CCRun, RunSpec
from .db import DATA_DIR as DATA_DIR_PATH, Database, new_id, now
from .devservers import DevServerManager
from .events import EventBus
from .notifications import Notifier
from .providers import DEFAULT_MODELS, KINDS as PROVIDER_KINDS, ProviderProfiles, scrub
from .proxy_gateway import ProxyGateway, mount as mount_proxy
from .room import Room
from .runs import RunManager
from .secrets_store import SecretStore
from .teams import Teams, event_visible
from .workspaces import WorkspaceManager, is_git_repo

db = Database()
bus = EventBus(db)
workspaces = WorkspaceManager(db)
room = Room(db, bus)
artifacts = ArtifactStore(db, bus, workspaces)
devservers = DevServerManager(db, bus)
secrets = SecretStore()
gateway = ProxyGateway()
profiles = ProviderProfiles(db, secrets)
notify = Notifier(db, bus)
teams = Teams(db, bus, notify)
agents = AgentDefinitions(db, bus, teams)
auth = Auth(db, teams)
PROJECTS_DIR = Path(os.environ.get("WORKBENCH_PROJECTS_DIR", str(DATA_DIR_PATH / "projects")))
runs: RunManager  # built in lifespan: it needs the running loop
svc = SimpleNamespace(db=db, bus=bus, auth=auth, teams=teams, agents=agents, notify=notify, profiles=profiles,
                      workspaces=workspaces, runs=lambda: runs)


def _decision_pending(d: dict) -> None:
    p = db.one("SELECT team_id, name FROM projects WHERE id = ?", [d["project_id"]]) or {}
    ids = [m["user_id"] for m in teams.members(p["team_id"])] if p.get("team_id") else [u["id"] for u in db.all("SELECT id FROM users")]
    notify.send_many(ids, "decision_pending", f"「{p.get('name')}」有一个 Room 冲突等你裁决", (d["subject"] or {}).get("path") or "",
                     project_id=d["project_id"], link=f"?p={d['project_id']}")


room.on_decision_pending = _decision_pending


@asynccontextmanager
async def lifespan(app: FastAPI):
    global runs
    bus.bind_loop(asyncio.get_running_loop())
    runs = RunManager(db, bus, workspaces, room, artifacts, devservers, profiles, teams=teams, notify=notify, agents=agents,
                      gateway=gateway)
    recovered = runs.reconcile()
    devservers.reconcile_after_restart()
    workspaces.sweep_orphans()
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    bus.emit("server_started", {"recovered_runs": [r["id"] for r in recovered]})
    sweeper = asyncio.get_running_loop().create_task(_sweep())
    try:
        yield
    finally:
        sweeper.cancel()
        await runs.shutdown()
        devservers.shutdown()


async def _sweep() -> None:
    while True:
        await asyncio.sleep(15)
        try:
            room.expire()
        except Exception:  # noqa: BLE001 -- a sweeper must not die
            pass


app = FastAPI(title="CC Workbench", lifespan=lifespan)

# Workers run Claude Code with bypassPermissions inside the server's
# filesystem, so every /api route needs a logged-in person. The deploy-time
# WORKBENCH_TOKEN only bootstraps (first registration, /api/admin/*).
PUBLIC = {"/api/auth", "/api/auth/login", "/api/auth/register"}
_PUBLIC_GET = re.compile(r"^/api/invites/[^/]+$")


class _RedactToken(logging.Filter):
    """uvicorn logs the request line with its query string; the WebSocket and
    artifact-file URLs carry the bearer there (a browser cannot send a header
    for those), and a proxy request carries its route token in the path.
    Hard rule 3 says a secret never reaches a log."""
    _res = (re.compile(r"(token=)[^&\s\"]+"), re.compile(r"(/proxy/)wbp_[A-Za-z0-9_-]+"))

    def _scrub(self, text: str) -> str:
        for r in self._res:
            text = r.sub(r"\1<redacted>", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self._scrub(a) if isinstance(a, str) else a for a in record.args)
        record.msg = self._scrub(record.msg) if isinstance(record.msg, str) else record.msg
        return True


for _name in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_name).addFilter(_RedactToken())


@app.middleware("http")
async def _principal(request: Request, call_next):
    path = request.url.path
    user = auth.principal(bearer_of(request.headers.get("authorization"), request.query_params.get("token"), path))
    request.state.user = user
    if path.startswith("/api/") and path not in PUBLIC and not (request.method == "GET" and _PUBLIC_GET.match(path)):
        if user is None:
            return JSONResponse({"detail": "login required"}, status_code=401)
        if user.get("bootstrap") and not auth.bootstrap_allowed(path):
            return JSONResponse({"detail": "引导令牌只能用于注册和管理"}, status_code=403)
    return await call_next(request)


@app.exception_handler(PermissionError)
async def _forbidden(request: Request, exc: PermissionError):
    return JSONResponse({"detail": str(exc)}, status_code=403)


app.include_router(routes_auth.make_router(svc))
app.include_router(routes_conversations.make_router(svc))
app.include_router(routes_agents.make_router(svc))
app.include_router(routes_providers.make_router(svc))
mount_proxy(app, gateway)


def _get(table: str, id_: str) -> dict:
    row = db.one(f"SELECT * FROM {table} WHERE id = ?", [id_])
    if not row:
        raise HTTPException(404, f"no such {table[:-1]}: {id_}")
    return row


def _project_for(user: dict, project_id: str, min_role: str | None = None) -> dict:
    p = _get("projects", project_id)
    if not teams.project_visible(user, p):
        raise HTTPException(403, "你不是这个项目所属团队的成员")
    if min_role:
        teams.require_team(user, p.get("team_id"), min_role)
    return p


def _session_for(user: dict, session_id: str) -> dict:
    s = _get("sessions", session_id)
    if s["kind"] == "main":
        teams.join_main_session(user, _get("projects", s["project_id"]), s)
    teams.require_session_member(user, s)
    return s


def _session_owner(user: dict, session: dict, what: str) -> None:
    """Who a run is charged to and what it may do is the owner's call: one
    person per session decides the model and the agent definition, everybody
    else in it talks to the agent (docs/decisions/0004 §2)."""
    conv = teams.conv_of_session(session["id"])
    if conv is None:                                  # legacy session, no conversation: nothing to own
        return
    if user.get("is_admin") or conv["owner_id"] == user["id"]:
        return
    raise HTTPException(403, f"只有会话 owner 能{what}")


def _task_member(user: dict, task: dict) -> dict:
    s = db.one("SELECT * FROM sessions WHERE task_id = ? ORDER BY created_at DESC LIMIT 1", [task["id"]])
    if s:
        teams.require_session_member(user, s)
    else:
        _project_for(user, task["project_id"])
    return task


# ---- health / discovery -----------------------------------------------------
@app.get("/api/health")
def health():
    d = ccconfig.discover()
    return {"ok": True, "claude_binary": d["claude_binary"], "claude_version": d["claude_version"], "live_runs": len(runs.live),
            "last_seq": bus.last_seq()}


@app.get("/api/cc/status")
def cc_status():
    """Login state as `claude auth status` reports it, for the server's own
    login and for the token saved on the settings page (if any)."""
    server = ccconfig.auth_status()
    saved = None
    if secrets.is_set("claude_code.oauth_token"):
        saved = ccconfig.auth_status({"CLAUDE_CODE_OAUTH_TOKEN": secrets.get("claude_code.oauth_token") or ""})
    effective = saved if saved and saved.get("logged_in") else server
    return {"server_login": server, "saved_token": saved, "saved_token_set": saved is not None, "effective": effective}


class CcLoginIn(BaseModel):
    oauth_token: str


@app.post("/api/cc/login-token")
def cc_login_token(body: CcLoginIn, user: dict = Depends(admin)):
    """Save a long-lived Claude Code token (from `claude setup-token`). Write-only."""
    tok = body.oauth_token.strip()
    if len(tok) < 20:
        raise HTTPException(400, "token looks too short")
    secrets.set("claude_code.oauth_token", tok)
    status = ccconfig.auth_status({"CLAUDE_CODE_OAUTH_TOKEN": tok})
    bus.emit("cc_status", status)
    return {"ok": True, "status": status}


@app.delete("/api/cc/login-token")
def cc_login_token_delete(user: dict = Depends(admin)):
    secrets.delete("claude_code.oauth_token")
    bus.emit("cc_status", ccconfig.auth_status())
    return {"ok": True}


@app.get("/api/cc/discovery")
def cc_discovery(project_id: str | None = None, user: dict = Depends(me)):
    root = _project_for(user, project_id)["root_path"] if project_id else None
    return ccconfig.discover(root)


# ---- projects -----------------------------------------------------------------
class ProjectIn(BaseModel):
    root_path: str | None = None      # an existing directory on this server
    git_url: str | None = None        # clone into the projects directory
    name: str | None = None           # with neither: create an empty git project of this name
    team_id: str | None = None        # default: the caller's current team


@app.get("/api/projects")
def list_projects(user: dict = Depends(me)):
    return [p for p in db.all("SELECT * FROM projects ORDER BY created_at") if teams.project_visible(user, p)]


@app.get("/api/projects/candidates")
def project_candidates(user: dict = Depends(me)):
    """Directories under the projects dir that are not registered yet."""
    known = {p["root_path"] for p in db.all("SELECT root_path FROM projects")}
    dirs = sorted(str(p) for p in PROJECTS_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")) if PROJECTS_DIR.is_dir() else []
    return {"projects_dir": str(PROJECTS_DIR), "candidates": [d for d in dirs if d not in known]}


def _register(root: Path, name: str | None, user: dict, team_id: str | None) -> dict:
    existing = db.one("SELECT * FROM projects WHERE root_path = ?", [str(root)])
    if existing:
        return existing
    p = db.insert("projects", {"id": new_id("prj"), "name": name or root.name, "root_path": str(root),
                               "is_git": 1 if is_git_repo(root) else 0, "created_at": now(), "team_id": team_id, "created_by": user["id"]})
    workspaces.main_workspace(p)
    bus.emit("project", p, project_id=p["id"])
    return p


@app.post("/api/projects")
def create_project(body: ProjectIn, user: dict = Depends(me)):
    import subprocess
    team_id = body.team_id or teams.default_team_for(user)
    if not team_id:        # a NULL-team project is the legacy "open to everyone" row; nobody creates those on purpose
        raise HTTPException(400, "先加入或创建一个团队")
    teams.require_team(user, team_id)
    if body.root_path:
        root = Path(body.root_path).expanduser().resolve()
        if not root.is_dir():
            raise HTTPException(400, f"这台服务器上没有这个目录：{root}。用「克隆 git 仓库」或「新建项目」。")
        return _register(root, body.name, user, team_id)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    if body.git_url:
        url = body.git_url.strip()
        if not re.match(r"^(https?://|git@|ssh://)[^\s]+$", url):
            raise HTTPException(400, "git 地址格式不对（https://… 或 git@…）")
        name = body.name or re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1].split(":")[-1])
        target = PROJECTS_DIR / _safe_dirname(name)
        if target.exists():
            return _register(target, body.name, user, team_id)
        r = subprocess.run(["git", "clone", "--", url, str(target)], capture_output=True, text=True, timeout=600,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if r.returncode != 0:
            raise HTTPException(400, f"git clone 失败：{(r.stderr or r.stdout).strip()[-800:]}")
        return _register(target, body.name, user, team_id)
    if body.name:
        target = PROJECTS_DIR / _safe_dirname(body.name)
        if not target.exists():
            target.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(target)], capture_output=True, timeout=60)
            (target / "README.md").write_text(f"# {body.name}\n")
            subprocess.run(["git", "-C", str(target), "add", "-A"], capture_output=True, timeout=60)
            subprocess.run(["git", "-C", str(target), "-c", "user.email=workbench@local", "-c", "user.name=workbench",
                            "commit", "-qm", "Initial commit"], capture_output=True, timeout=60)
        return _register(target, body.name, user, team_id)
    raise HTTPException(400, "需要 root_path、git_url 或 name 之一")


def _safe_dirname(name: str) -> str:
    out = re.sub(r"[^A-Za-z0-9._一-鿿-]+", "-", name.strip()).strip("-.")
    return out[:64] or "project"


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, user: dict = Depends(me)):
    p = _project_for(user, project_id)
    return {**p, "main_workspace": workspaces.main_workspace(p)}


@app.get("/api/overview")
def overview(user: dict = Depends(me)):
    """Everything across the caller's teams, for the landing board."""
    projs = [p for p in db.all("SELECT * FROM projects ORDER BY created_at") if teams.project_visible(user, p)]
    ids = {p["id"] for p in projs}
    visible = teams.visible_sessions(user)
    tasks = [_task_card(runs.task_view(t), visible) for t in db.all("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 300") if t["project_id"] in ids]
    live = [r for r in db.all("SELECT id, project_id, task_id, session_id, kind, status, started_at FROM runs WHERE status IN ('queued','running') ORDER BY created_at")
            if r["project_id"] in ids]
    pending = [d for d in db.all("SELECT * FROM decisions WHERE status = 'pending' ORDER BY created_at") if d["project_id"] in ids]
    arts = [a for a in db.all("SELECT a.id, a.project_id, a.task_id, a.run_id, a.kind, a.title, a.version, a.status, a.created_at, r.session_id "
                              "FROM artifacts a JOIN runs r ON r.id = a.run_id WHERE a.status = 'current' ORDER BY a.created_at DESC LIMIT 60")
            if a["project_id"] in ids and _session_open(a.pop("session_id"), visible)][:30]
    return {"projects": projs, "tasks": tasks, "live_runs": live, "pending_decisions": pending, "recent_artifacts": arts,
            "counts": {"projects": len(projs), "tasks": len(tasks), "live_runs": len(live), "pending_decisions": len(pending)}}


# ---- settings -----------------------------------------------------------------
class SettingsIn(BaseModel):
    default_profile_id: str | None = None
    default_model: str | None = None


@app.get("/api/settings")
def get_settings(user: dict = Depends(me)):
    return {"default_profile_id": db.setting("default_profile_id"), "default_model": db.setting("default_model"),
            "max_concurrent_runs": int(os.environ.get("WORKBENCH_MAX_CONCURRENT_RUNS", "3")), "projects_dir": str(PROJECTS_DIR),
            "data_dir": str(DATA_DIR_PATH), "token_required": not auth.single_user, "single_user": auth.single_user}


@app.put("/api/settings")
def put_settings(body: SettingsIn, user: dict = Depends(admin)):
    if body.default_profile_id is not None:
        db.set_setting("default_profile_id", body.default_profile_id or None)
    if body.default_model is not None:
        db.set_setting("default_model", body.default_model or None)
    return get_settings(user)


# ---- sessions / chat ------------------------------------------------------------
def _session_view(s: dict) -> dict:
    base = {**s, "agent_definition": agents.binding(s), "preview": s.get("preview") or None}
    conv = teams.conv_of_session(s["id"])
    if not conv:
        return {**base, "conversation_id": None, "member_count": 0, "human_count": 0}
    members = teams.conv_members(conv["id"])
    return {**base, "conversation_id": conv["id"], "member_count": len(members), "agent_reply": conv["agent_reply"],
            "human_count": sum(1 for m in members if m["member_kind"] == "user")}


@app.get("/api/projects/{project_id}/main-session")
def main_session(project_id: str, user: dict = Depends(me)):
    p = _project_for(user, project_id)
    s = db.one("SELECT * FROM sessions WHERE project_id = ? AND kind = 'main' ORDER BY created_at DESC LIMIT 1", [project_id])
    if not s:
        s = db.insert("sessions", {"id": new_id("ses"), "project_id": project_id, "task_id": None, "kind": "main", "title": f"{p['name']} · main",
                                   "cc_session_id": None, "created_at": now(), "created_by": user["id"], "agent_name": "主 agent",
                                   "agent_definition_id": agents.default_for(p.get("team_id"), "orchestrator")})
        teams.ensure_session_conversation(s, user["id"])
        bus.emit("session", s, project_id=project_id, session_id=s["id"])
    teams.join_main_session(user, p, s)
    return _session_view(s)


@app.get("/api/projects/{project_id}/sessions")
def list_sessions(project_id: str, user: dict = Depends(me)):
    p = _project_for(user, project_id)
    main = db.one("SELECT * FROM sessions WHERE project_id = ? AND kind = 'main' ORDER BY created_at DESC LIMIT 1", [project_id])
    if main:
        teams.join_main_session(user, p, main)
    visible = teams.visible_sessions(user)
    rows = db.all("SELECT * FROM sessions WHERE project_id = ? ORDER BY created_at", [project_id])
    return [_session_view(s) for s in rows if s["id"] in visible or teams.conv_of_session(s["id"]) is None]


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str, user: dict = Depends(me)):
    s = _session_for(user, session_id)
    runs_ = db.all("SELECT * FROM runs WHERE session_id = ? ORDER BY created_at", [session_id])
    # the UI shows paths relative to where the session actually edits
    ws = db.one("SELECT * FROM workspaces WHERE id = ?", [runs_[-1]["workspace_id"]]) if runs_ else None
    conv = teams.conv_of_session(session_id)
    return {**_session_view(s), "runs": runs_, "workspace": ws, "members": teams.conv_members(conv["id"]) if conv else [],
            "owner_id": conv["owner_id"] if conv else None}


class PreviewIn(BaseModel):
    target: str = ""
    title: str = ""


@app.get("/api/sessions/{session_id}/preview")
def get_preview(session_id: str, user: dict = Depends(me)):
    s = _session_for(user, session_id)
    return {"preview": s.get("preview") or None, "revision": s.get("preview_revision") or 0}


@app.delete("/api/sessions/{session_id}/preview")
def clear_preview(session_id: str, user: dict = Depends(me)):
    s = _session_for(user, session_id)
    db.update("sessions", session_id, preview={}, preview_revision=int(s.get("preview_revision") or 0) + 1)
    bus.emit("preview", {"session_id": session_id, "preview": None}, project_id=s["project_id"], session_id=session_id)
    return {"ok": True}


@app.get("/api/sessions/{session_id}/preview/file")
def preview_file(session_id: str, path: str, user: dict = Depends(me)):
    """Serve one workspace file to the preview pane. The path is re-confined
    here, not trusted from the row: the agent wrote it and the workspace may
    have changed since."""
    s = _session_for(user, session_id)
    run = db.one("SELECT * FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1", [session_id])
    ws = workspaces.get(run["workspace_id"]) if run else None
    try:
        f = preview.safe_path((ws or {}).get("path"), path)
    except preview.PreviewError as e:
        raise HTTPException(404, str(e))
    return FileResponse(f, media_type=preview.content_type(path))


class JoinRequestIn(BaseModel):
    note: str = ""


@app.post("/api/sessions/{session_id}/join-request")
def request_to_join(session_id: str, body: JoinRequestIn, user: dict = Depends(me)):
    """The one thing a non-member may do with a locked card: ask its owner in.
    Nothing about the session comes back -- only whether the ask was sent."""
    sess = _get("sessions", session_id)
    _project_for(user, sess["project_id"])
    conv = teams.conv_of_session(session_id)
    if conv is None or teams.is_member(user, conv["id"]):
        return {"ok": True, "already_member": True}
    if not conv["owner_id"]:
        raise HTTPException(409, "这个会话没有 owner，找项目管理员加你")
    # one open ask per person per session: the owner gets a reminder, not a queue
    dup = db.one("SELECT 1 FROM notifications WHERE user_id = ? AND kind = 'join_request' AND conversation_id = ? "
                 "AND actor_id = ? AND read_at IS NULL", [conv["owner_id"], conv["id"], user["id"]])
    if dup:
        return {"ok": True, "already_sent": True}
    notify.send(conv["owner_id"], "join_request", f"{user['display_name']} 想加入「{conv['title']}」",
                (body.note or "").strip()[:200], actor_id=user["id"], conversation_id=conv["id"],
                project_id=sess["project_id"], session_id=session_id)
    return {"ok": True, "sent": True}


class SessionPatch(BaseModel):
    agent_definition_id: str | None = None
    title: str | None = None


@app.patch("/api/sessions/{session_id}")
def patch_session(session_id: str, body: SessionPatch, user: dict = Depends(me)):
    """Rebinding takes effect on the next run, so a live run would silently
    keep the old definition -- refuse instead of lying about it."""
    s = _session_for(user, session_id)
    fields: dict = {}
    if body.agent_definition_id is not None:
        _session_owner(user, s, "换 agent 定义")
        if runs.active_run(session_id):
            raise HTTPException(409, "会话正在运行，等它结束再换")
        fields["agent_definition_id"] = agents.check_for_kind(user, body.agent_definition_id, s["kind"])["id"]
    if body.title is not None and body.title.strip():
        fields["title"] = body.title.strip()[:80]
    if fields:
        db.update("sessions", session_id, **fields)
        s = db.one("SELECT * FROM sessions WHERE id = ?", [session_id])
        bus.emit("session", s, project_id=s["project_id"], session_id=session_id)
    return _session_view(s)


@app.get("/api/sessions/{session_id}/messages")
def session_messages(session_id: str, limit: int = 500, user: dict = Depends(me)):
    _session_for(user, session_id)
    return db.all("SELECT * FROM messages WHERE session_id = ? ORDER BY created_at, rowid LIMIT ?", [session_id, limit])


class MessageIn(BaseModel):
    text: str
    profile_id: str | None = None
    model: str | None = None


@app.post("/api/sessions/{session_id}/messages")
async def post_message(session_id: str, body: MessageIn, user: dict = Depends(me)):
    s = _session_for(user, session_id)
    if not body.text.strip():
        raise HTTPException(400, "empty")
    if body.profile_id or body.model:
        _session_owner(user, s, "换模型")
    return await runs.send_human(s, user, body.text, profile_id=body.profile_id, model=body.model)


# ---- tasks / workers -----------------------------------------------------------
class TaskIn(BaseModel):
    title: str
    instructions: str
    isolation: str = "worktree"          # worktree | main
    depends_on: list[str] = []
    profile_id: str | None = None
    model: str | None = None
    agent_definition_id: str | None = None
    edit_mode: str = "exclusive"         # exclusive | shared (experimental)
    workspace_id: str | None = None


def _session_open(session_id: str | None, visible: set[str]) -> bool:
    return session_id is None or session_id in visible or teams.conv_of_session(session_id) is None


# What a non-member sees of a locked worker session's card: enough to know it
# exists and whose it is, none of the instructions, prompts or results.
_CARD_KEYS = ("id", "project_id", "title", "kind", "status", "created_by", "branch", "isolation", "session_id",
              "review_status", "merge_status", "created_at", "updated_at", "runs_count")


def _task_card(view: dict, visible: set[str]) -> dict:
    view["member"] = _session_open(view["session_id"], visible)
    conv = teams.conv_of_session(view["session_id"]) if view.get("session_id") else None
    view["members"] = [{"id": m["member_id"], "name": m["name"], "handle": m["handle"], "member_kind": m["member_kind"]}
                       for m in teams.conv_members(conv["id"])] if conv else []
    return view if view["member"] else {k: view.get(k) for k in (*_CARD_KEYS, "members")} | {"member": False}


@app.get("/api/projects/{project_id}/tasks")
def list_tasks(project_id: str, user: dict = Depends(me)):
    _project_for(user, project_id)
    visible = teams.visible_sessions(user)
    return [_task_card(runs.task_view(t), visible) for t in db.all("SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at", [project_id])]


@app.post("/api/projects/{project_id}/tasks")
def create_task(project_id: str, body: TaskIn, user: dict = Depends(me)):
    p = _project_for(user, project_id, min_role="member")
    definition = (agents.check_for_kind(user, body.agent_definition_id, "worker")["id"] if body.agent_definition_id
                  else agents.default_for(p.get("team_id"), "worker"))
    try:
        return runs.spawn_worker(p, body.title, body.instructions, isolation=body.isolation, depends_on=body.depends_on,
                                 profile_id=body.profile_id, edit_mode=body.edit_mode, workspace_id=body.workspace_id, model=body.model,
                                 created_by=user["id"], agent_definition_id=definition)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, user: dict = Depends(me)):
    t = _task_member(user, _get("tasks", task_id))
    return {**runs.task_view(t), "runs": db.all("SELECT * FROM runs WHERE task_id = ? ORDER BY created_at", [task_id]),
            "workspace": workspaces.get(t["workspace_id"]) if t["workspace_id"] else None}


class RetryIn(BaseModel):
    prompt: str | None = None
    profile_id: str | None = None


@app.post("/api/tasks/{task_id}/retry")
def retry_task(task_id: str, body: RetryIn, user: dict = Depends(me)):
    t = _task_member(user, _get("tasks", task_id))
    if body.profile_id:
        sess = db.one("SELECT * FROM sessions WHERE task_id = ? ORDER BY created_at DESC LIMIT 1", [task_id])
        if sess:
            _session_owner(user, sess, "换模型")
    r = runs.retry_task(t, body.prompt, body.profile_id, actor=user)
    if not r.get("ok"):
        raise HTTPException(409, r["error"])
    return r


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, user: dict = Depends(me)):
    _task_member(user, _get("tasks", task_id))
    last = runs.latest_run(task_id)
    if not last:
        raise HTTPException(404, "task has no run")
    return await runs.cancel(last["id"], actor=user["display_name"])


@app.get("/api/tasks/{task_id}/diff")
def task_diff(task_id: str, user: dict = Depends(me)):
    t = _task_member(user, _get("tasks", task_id))
    return workspaces.diff(_get("workspaces", t["workspace_id"]), _get("projects", t["project_id"]))


class ReviewIn(BaseModel):
    verdict: str            # approve | request_changes | unreviewed
    note: str = ""


@app.post("/api/tasks/{task_id}/review")
async def review_task(task_id: str, body: ReviewIn, user: dict = Depends(me)):
    t = _task_member(user, _get("tasks", task_id))
    status = {"approve": "approved", "request_changes": "changes_requested", "unreviewed": "unreviewed"}.get(body.verdict)
    if not status:
        raise HTTPException(400, "verdict must be approve | request_changes | unreviewed")
    db.update("tasks", task_id, review_status=status, updated_at=now())
    view = runs.task_view(_get("tasks", task_id))
    bus.emit("task", view, project_id=t["project_id"], task_id=task_id)
    delivery = None
    if body.note and body.verdict == "request_changes" and view["latest_run"]:
        delivery = await runs.deliver(view["latest_run"]["id"], f"[Review] Changes requested by {user['display_name']}: {body.note}",
                                      author=user["display_name"], user_id=user["id"])
    return {"ok": True, "task": view, "delivery": delivery}


class MergeIn(BaseModel):
    message: str | None = None


@app.post("/api/tasks/{task_id}/merge")
def merge_task(task_id: str, body: MergeIn, user: dict = Depends(me)):
    t = _task_member(user, _get("tasks", task_id))
    if t["review_status"] != "approved":
        raise HTTPException(409, "review must be approved before merge (approval is not merge; merge is a separate decision)")
    ws = _get("workspaces", t["workspace_id"])
    p = _get("projects", t["project_id"])
    r = workspaces.merge_into_main(ws, p, body.message or f"Merge task: {t['title']}")
    if r.get("ok"):
        db.update("tasks", task_id, merge_status="merged", updated_at=now())
        bus.emit("task", runs.task_view(_get("tasks", task_id)), project_id=p["id"], task_id=task_id)
    return r


@app.post("/api/tasks/{task_id}/workspace/remove")
def remove_workspace(task_id: str, user: dict = Depends(me)):
    t = _get("tasks", task_id)
    _project_for(user, t["project_id"], min_role="admin")
    if runs.task_status(task_id) in ("queued", "running"):
        raise HTTPException(409, "task has an active run")
    workspaces.remove(_get("workspaces", t["workspace_id"]), _get("projects", t["project_id"]))
    return {"ok": True}


# ---- runs ------------------------------------------------------------------------
@app.get("/api/projects/{project_id}/runs")
def list_runs(project_id: str, user: dict = Depends(me)):
    _project_for(user, project_id)
    visible = teams.visible_sessions(user)
    return [r for r in db.all("SELECT * FROM runs WHERE project_id = ? ORDER BY created_at DESC LIMIT 200", [project_id]) if r["session_id"] in visible]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, user: dict = Depends(me)):
    r = _get("runs", run_id)
    _session_for(user, r["session_id"])
    return r


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str, user: dict = Depends(me)):
    r = _get("runs", run_id)
    _session_for(user, r["session_id"])
    return await runs.cancel(run_id, actor=user["display_name"])


# ---- artifacts / feedback -----------------------------------------------------
# An artifact belongs to the session that produced it: same gate as the session.
@app.get("/api/projects/{project_id}/artifacts")
def list_artifacts(project_id: str, task_id: str | None = None, user: dict = Depends(me)):
    _project_for(user, project_id)
    visible = teams.visible_sessions(user)
    session_of = {r["id"]: r["session_id"] for r in db.all("SELECT id, session_id FROM runs WHERE project_id = ?", [project_id])}
    return [a for a in artifacts.list(project_id, task_id) if _session_open(session_of.get(a["run_id"]), visible)]


@app.get("/api/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, user: dict = Depends(me)):
    a = _get("artifacts", artifact_id)
    _session_for(user, _get("runs", a["run_id"])["session_id"])
    return artifacts.public(a)


@app.get("/api/artifacts/{artifact_id}/file")
def artifact_file(artifact_id: str, user: dict = Depends(me)):
    a = _get("artifacts", artifact_id)
    _session_for(user, _get("runs", a["run_id"])["session_id"])
    if not a["file_path"] or not Path(a["file_path"]).is_file():
        raise HTTPException(404, "artifact has no file")
    return FileResponse(a["file_path"], media_type=(a["meta"] or {}).get("mime"))


class FeedbackIn(BaseModel):
    text: str
    verdict: str = "comment"      # comment | approve | request_changes


@app.post("/api/artifacts/{artifact_id}/feedback")
async def artifact_feedback(artifact_id: str, body: FeedbackIn, user: dict = Depends(me)):
    a = _get("artifacts", artifact_id)
    run = _get("runs", a["run_id"])
    _session_for(user, run["session_id"])
    if body.verdict not in ("comment", "approve", "request_changes"):
        raise HTTPException(400, "verdict must be comment | approve | request_changes")
    fb = artifacts.record_feedback(a, user["display_name"], body.verdict, body.text, user_id=user["id"])
    text = f"[Feedback on '{a['title']}' v{a['version']} ({a['kind']}) from {user['display_name']}: {body.verdict}] {body.text}"
    d = await runs.deliver(a["run_id"], text, author=user["display_name"], user_id=user["id"])
    fb = artifacts.mark_delivered(fb["id"], d.get("run_id"), d.get("how", "failed"))
    if a["task_id"]:
        t = db.one("SELECT title, created_by FROM tasks WHERE id = ?", [a["task_id"]]) or {}
        notify.send(t.get("created_by"), "feedback", f"{user['display_name']} 对「{a['title']}」提了反馈：{body.verdict}", body.text[:200],
                    actor_id=user["id"], project_id=a["project_id"], link=f"?p={a['project_id']}&s={run['session_id']}")
    return {"ok": True, "feedback": fb, "delivery": d}


@app.post("/api/devservers/{ds_id}/stop")
def stop_devserver(ds_id: str, user: dict = Depends(me)):
    ds = _get("devservers", ds_id)
    _session_for(user, _get("runs", ds["run_id"])["session_id"])
    return devservers.stop(ds_id)


@app.get("/api/devservers/{ds_id}/logs")
def devserver_logs(ds_id: str, tail: int = 200, user: dict = Depends(me)):
    ds = _get("devservers", ds_id)
    _session_for(user, _get("runs", ds["run_id"])["session_id"])
    return {"id": ds_id, "log": devservers.logs(ds_id, tail)}


# ---- room / decisions -----------------------------------------------------------
@app.get("/api/projects/{project_id}/room")
def room_state(project_id: str, user: dict = Depends(me)):
    _project_for(user, project_id)
    s = room.summary(project_id)
    ident = {r["id"]: room.identity(r) for r in db.all("SELECT * FROM runs WHERE project_id = ? AND status IN ('queued','running')", [project_id])}
    return {**s, "members": list(ident.values())}


class DecisionIn(BaseModel):
    decision: str           # approve | reject
    reason: str = ""


@app.post("/api/decisions/{decision_id}/decide")
async def decide(decision_id: str, body: DecisionIn, user: dict = Depends(me)):
    d = _get("decisions", decision_id)
    _project_for(user, d["project_id"], min_role="member")   # the human at the team boundary (hard rule 1)
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve | reject")
    r = await room.decide(decision_id, body.decision, body.reason, user["display_name"], actor_id=user["id"])
    if not r.get("ok"):
        raise HTTPException(409, r["error"])
    return r


@app.get("/api/projects/{project_id}/decisions")
def list_decisions(project_id: str, user: dict = Depends(me)):
    _project_for(user, project_id)
    return db.all("SELECT * FROM decisions WHERE project_id = ? ORDER BY created_at DESC", [project_id])


# ---- provider profiles -----------------------------------------------------------
class ProfileIn(BaseModel):
    name: str                         # provider id, lowercase
    kind: str
    preset: str | None = None         # a name from GET /api/presets when kind is "preset"
    model_map: dict[str, str] = {}
    display_name: str | None = None
    base_url: str | None = None
    model: str | None = None
    models: list[str] = []
    credential_ref: str | None = None # env var name; omit to store the key in the secret store
    secret: str | None = None         # write-only
    extra_env: dict[str, str] = {}
    team_id: str | None = None
    shared: bool = False


class ProfilePatch(BaseModel):
    display_name: str | None = None
    model_map: dict[str, str] | None = None
    base_url: str | None = None
    model: str | None = None
    models: list[str] | None = None
    secret: str | None = None
    extra_env: dict[str, str] | None = None
    shared: bool | None = None


def _profile_editable(user: dict, profile_id: str) -> dict:
    p = _get("provider_profiles", profile_id)
    if not profiles.can_edit(p, user):
        raise HTTPException(403, "只有 Provider 的 owner 或管理员可以改")
    return p


@app.get("/api/profiles")
def list_profiles(user: dict = Depends(me)):
    prefs = user.get("prefs") or {}
    team = db.one("SELECT * FROM teams WHERE id = ?", [teams.default_team_for(user)]) if not user.get("bootstrap") else None
    return {"profiles": profiles.list(user), "kinds": PROVIDER_KINDS, "default_models": DEFAULT_MODELS,
            "default_profile_id": prefs.get("default_profile_id") or (team or {}).get("default_profile_id") or db.setting("default_profile_id"),
            "default_model": prefs.get("default_model") or (team or {}).get("default_model") or db.setting("default_model"),
            "global_default_profile_id": db.setting("default_profile_id"), "global_default_model": db.setting("default_model"),
            "team": {"id": team["id"], "name": team["name"], "default_profile_id": team["default_profile_id"],
                     "default_model": team["default_model"]} if team else None}


@app.post("/api/profiles")
def create_profile(body: ProfileIn, user: dict = Depends(me)):
    team_id = body.team_id or teams.default_team_for(user)
    if team_id:
        teams.require_team(user, team_id)
    try:
        return profiles.create(body.name, body.kind, display_name=body.display_name, base_url=body.base_url, model=body.model,
                               models=body.models, credential_ref=body.credential_ref, extra_env=body.extra_env, secret=body.secret,
                               owner=user, team_id=team_id, shared=body.shared, preset=body.preset, model_map=body.model_map)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.patch("/api/profiles/{profile_id}")
def patch_profile(profile_id: str, body: ProfilePatch, user: dict = Depends(me)):
    _profile_editable(user, profile_id)
    try:
        return profiles.update(profile_id, display_name=body.display_name, base_url=body.base_url, model=body.model, models=body.models,
                               extra_env=body.extra_env, secret=body.secret, shared=body.shared, model_map=body.model_map)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/profiles/{profile_id}/secret")
def delete_profile_secret(profile_id: str, user: dict = Depends(me)):
    _profile_editable(user, profile_id)
    profiles.clear_secret(profile_id)
    return {"ok": True}


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: str, user: dict = Depends(me)):
    _profile_editable(user, profile_id)
    profiles.delete(profile_id)
    if db.setting("default_profile_id") == profile_id:
        db.set_setting("default_profile_id", None)
    return {"ok": True}


@app.post("/api/profiles/{profile_id}/deep-check")
async def deep_check_profile(profile_id: str, user: dict = Depends(me)):
    """Compatibility check: streaming text, a tool call, and a clean failure
    message -- run in a throwaway directory, never persisted as a Run."""
    p = _get("provider_profiles", profile_id)
    if not profiles.visible_to(p, user):
        raise HTTPException(403, "这个 Provider 没有共享给你")
    env, snap = profiles.env_for(p)
    result = {"profile": snap, "stream_text": False, "tool_call": False, "ok": False, "error": None, "model": None}
    if snap.get("credential_present") is False:
        result["error"] = "没有密钥：在这个提供方的卡片里填入密钥，或在服务器环境里设置它引用的变量"
        profiles.record_compat(profile_id, result)
        return result
    with tempfile.TemporaryDirectory() as tmp:
        spec = RunSpec(run_id="check", cwd=tmp, model=p["model"], env=env, max_turns=3, allowed_tools=["Bash"],
                       prompt="Run the shell command `echo compat-ok` with the Bash tool, then reply with exactly the command's output.")
        cc = CCRun(spec)

        async def on_event(t: str, payload: dict) -> None:
            if t == "stream_delta":
                result["stream_text"] = True
            if t == "run_init":
                result["model"] = payload.get("model")
            if t == "assistant_message" and any(b["type"] == "tool_use" and b["name"] == "Bash" for b in payload["blocks"]):
                result["tool_call"] = True

        try:
            out = await asyncio.wait_for(cc.start(on_event), timeout=180)
            result["ok"] = out.status == "succeeded" and result["tool_call"] and "compat-ok" in (out.result_text or "")
            result["error"] = scrub(out.error, env)
            result["result_text"] = scrub((out.result_text or "")[:300], env)
        except asyncio.TimeoutError:
            await cc.close()
            result["error"] = "timed out after 180s"
    profiles.record_compat(profile_id, result)
    return result


# ---- events / websocket --------------------------------------------------------------
@app.get("/api/events")
def events_since(since: int = 0, project_id: str | None = None, user: dict = Depends(me)):
    if project_id:
        _project_for(user, project_id)
    ctx = teams.ws_context(user)
    return [ev for ev in bus.since(since, project_id) if event_visible(ev, ctx)]


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, since: int = -1, project_id: str | None = None, token: str | None = None):
    user = auth.principal(bearer_of(websocket.headers.get("authorization"), token, "/ws"))
    if user is None or user.get("bootstrap"):
        await websocket.close(code=4401)
        return
    ctx = teams.ws_context(user)
    if project_id and project_id not in ctx["projects"]:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def listener(ev: dict) -> None:
        if project_id and ev.get("project_id") not in (None, project_id):
            return
        # being added to (or removed from) a conversation or a team changes what this socket may see
        p = ev.get("payload") or {}
        if ev["type"] in ("conversation_member", "team_member") and (p.get("member_id") == user["id"] or p.get("user_id") == user["id"]):
            ctx.update(teams.ws_context(user))
        if event_visible(ev, ctx):
            queue.put_nowait(ev)

    async def replay(from_seq: int) -> None:
        for ev in bus.since(from_seq, project_id):
            if event_visible(ev, ctx):
                await websocket.send_text(json.dumps(ev, default=str))
        await websocket.send_text(json.dumps({"type": "replay_done", "last_seq": bus.last_seq()}))

    unsub = bus.subscribe(listener)
    try:
        await websocket.send_text(json.dumps({"type": "hello", "last_seq": bus.last_seq(), "user_id": user["id"]}))
        if since >= 0:
            await replay(since)

        async def pump():
            while True:
                ev = await queue.get()
                await websocket.send_text(json.dumps(ev, default=str))

        async def listen():
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("type") == "replay":
                    await replay(int(msg.get("since", 0)))

        done, pending = await asyncio.wait([asyncio.ensure_future(pump()), asyncio.ensure_future(listen())], return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            t.exception()      # a client hanging up ends listen() with WebSocketDisconnect; that is not an error to log
    except WebSocketDisconnect:
        pass
    finally:
        unsub()


# Production: `npm run build` once and this process serves the UI too, so a
# deployment is one port and one origin. Absent in development (Vite serves it).
_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="ui")
