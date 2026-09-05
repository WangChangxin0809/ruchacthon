"""FastAPI entry point: REST for state, one WebSocket for the ordered event
stream. All state lives in `Database`; all execution goes through
`RunManager`. Restart recovery happens in `lifespan` before any request is
served, so a client never sees a run as `running` that no process backs.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ccconfig
from .artifacts import ArtifactStore
from .cc_runner import CCRun, RunSpec
from .db import Database, new_id, now
from .devservers import DevServerManager
from .events import EventBus
from .providers import KINDS as PROVIDER_KINDS, ProviderProfiles, scrub
from .room import Room
from .runs import RunManager
from .workspaces import WorkspaceManager, is_git_repo

db = Database()
bus = EventBus(db)
workspaces = WorkspaceManager(db)
room = Room(db, bus)
artifacts = ArtifactStore(db, bus, workspaces)
devservers = DevServerManager(db, bus)
profiles = ProviderProfiles(db)
runs: RunManager  # built in lifespan: it needs the running loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    global runs
    bus.bind_loop(asyncio.get_running_loop())
    runs = RunManager(db, bus, workspaces, room, artifacts, devservers, profiles)
    recovered = runs.reconcile()
    devservers.reconcile_after_restart()
    workspaces.sweep_orphans()
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
# filesystem, so anything reachable over a network must present the token.
# Unset = local single-user mode (bind to 127.0.0.1 and it is yours alone).
TOKEN = os.environ.get("WORKBENCH_TOKEN", "")


def _authorized(auth_header: str | None, query_token: str | None) -> bool:
    if not TOKEN:
        return True
    if auth_header and auth_header.startswith("Bearer ") and auth_header[7:] == TOKEN:
        return True
    return query_token == TOKEN


@app.middleware("http")
async def _require_token(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path != "/api/auth" \
            and not _authorized(request.headers.get("authorization"), request.query_params.get("token")):
        return JSONResponse({"detail": "workbench token required"}, status_code=401)
    return await call_next(request)


@app.get("/api/auth")
def auth_probe(request: Request):
    """Lets the UI find out whether a token is needed and whether its token works."""
    return {"required": bool(TOKEN), "ok": _authorized(request.headers.get("authorization"), request.query_params.get("token"))}


def _get(table: str, id_: str) -> dict:
    row = db.one(f"SELECT * FROM {table} WHERE id = ?", [id_])
    if not row:
        raise HTTPException(404, f"no such {table[:-1]}: {id_}")
    return row


# ---- health / discovery -----------------------------------------------------
@app.get("/api/health")
def health():
    d = ccconfig.discover()
    return {"ok": True, "claude_binary": d["claude_binary"], "claude_version": d["claude_version"], "live_runs": len(runs.live),
            "last_seq": bus.last_seq()}


@app.get("/api/cc/discovery")
def cc_discovery(project_id: str | None = None):
    root = _get("projects", project_id)["root_path"] if project_id else None
    return ccconfig.discover(root)


# ---- projects -----------------------------------------------------------------
class ProjectIn(BaseModel):
    root_path: str
    name: str | None = None


@app.get("/api/projects")
def list_projects():
    return db.all("SELECT * FROM projects ORDER BY created_at")


@app.post("/api/projects")
def create_project(body: ProjectIn):
    root = Path(body.root_path).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(400, f"not a directory: {root}")
    existing = db.one("SELECT * FROM projects WHERE root_path = ?", [str(root)])
    if existing:
        return existing
    p = db.insert("projects", {"id": new_id("prj"), "name": body.name or root.name, "root_path": str(root),
                               "is_git": 1 if is_git_repo(root) else 0, "created_at": now()})
    workspaces.main_workspace(p)
    bus.emit("project", p, project_id=p["id"])
    return p


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    p = _get("projects", project_id)
    return {**p, "main_workspace": workspaces.main_workspace(p)}


# ---- sessions / chat ------------------------------------------------------------
@app.get("/api/projects/{project_id}/main-session")
def main_session(project_id: str):
    p = _get("projects", project_id)
    s = db.one("SELECT * FROM sessions WHERE project_id = ? AND kind = 'main' ORDER BY created_at DESC LIMIT 1", [project_id])
    if not s:
        s = db.insert("sessions", {"id": new_id("ses"), "project_id": project_id, "task_id": None, "kind": "main",
                                   "title": f"{p['name']} · main", "cc_session_id": None, "created_at": now()})
        bus.emit("session", s, project_id=project_id, session_id=s["id"])
    return s


@app.get("/api/projects/{project_id}/sessions")
def list_sessions(project_id: str):
    return db.all("SELECT * FROM sessions WHERE project_id = ? ORDER BY created_at", [project_id])


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    s = _get("sessions", session_id)
    return {**s, "runs": db.all("SELECT * FROM runs WHERE session_id = ? ORDER BY created_at", [session_id])}


@app.get("/api/sessions/{session_id}/messages")
def session_messages(session_id: str, limit: int = 500):
    _get("sessions", session_id)
    return db.all("SELECT * FROM messages WHERE session_id = ? ORDER BY created_at, rowid LIMIT ?", [session_id, limit])


class MessageIn(BaseModel):
    text: str
    author: str = "human"
    profile_id: str | None = None


@app.post("/api/sessions/{session_id}/messages")
async def post_message(session_id: str, body: MessageIn):
    s = _get("sessions", session_id)
    p = _get("projects", s["project_id"])
    active = db.one("SELECT * FROM runs WHERE session_id = ? AND status IN ('queued','running')", [session_id])
    if active:
        return await runs.deliver(active["id"], body.text, author=body.author)
    if s["kind"] == "worker":
        last = db.one("SELECT * FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1", [session_id])
        if last:
            return await runs.deliver(last["id"], body.text, author=body.author)
    ws = workspaces.main_workspace(p)
    msg = db.insert("messages", {"id": new_id("msg"), "session_id": session_id, "run_id": None, "role": "user", "author": body.author,
                                 "blocks": [{"type": "text", "text": body.text}], "created_at": now()})
    bus.emit("message", msg, project_id=p["id"], session_id=session_id)
    run = runs.create_run(project=p, session=s, workspace=ws, kind=s["kind"], prompt=body.text, task_id=s["task_id"], profile_id=body.profile_id)
    return {"ok": True, "how": "new_run", "run_id": run["id"]}


# ---- tasks / workers -----------------------------------------------------------
class TaskIn(BaseModel):
    title: str
    instructions: str
    isolation: str = "worktree"          # worktree | main
    depends_on: list[str] = []
    profile_id: str | None = None
    edit_mode: str = "exclusive"         # exclusive | shared (experimental)
    workspace_id: str | None = None


@app.get("/api/projects/{project_id}/tasks")
def list_tasks(project_id: str):
    _get("projects", project_id)
    return [runs.task_view(t) for t in db.all("SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at", [project_id])]


@app.post("/api/projects/{project_id}/tasks")
def create_task(project_id: str, body: TaskIn):
    p = _get("projects", project_id)
    try:
        return runs.spawn_worker(p, body.title, body.instructions, isolation=body.isolation, depends_on=body.depends_on,
                                 profile_id=body.profile_id, edit_mode=body.edit_mode, workspace_id=body.workspace_id)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    t = _get("tasks", task_id)
    return {**runs.task_view(t), "runs": db.all("SELECT * FROM runs WHERE task_id = ? ORDER BY created_at", [task_id]),
            "workspace": workspaces.get(t["workspace_id"]) if t["workspace_id"] else None}


class RetryIn(BaseModel):
    prompt: str | None = None
    profile_id: str | None = None


@app.post("/api/tasks/{task_id}/retry")
def retry_task(task_id: str, body: RetryIn):
    r = runs.retry_task(_get("tasks", task_id), body.prompt, body.profile_id)
    if not r.get("ok"):
        raise HTTPException(409, r["error"])
    return r


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    last = runs.latest_run(task_id)
    if not last:
        raise HTTPException(404, "task has no run")
    return await runs.cancel(last["id"])


@app.get("/api/tasks/{task_id}/diff")
def task_diff(task_id: str):
    t = _get("tasks", task_id)
    return workspaces.diff(_get("workspaces", t["workspace_id"]), _get("projects", t["project_id"]))


class ReviewIn(BaseModel):
    verdict: str            # approve | request_changes | unreviewed
    note: str = ""
    author: str = "human"


@app.post("/api/tasks/{task_id}/review")
async def review_task(task_id: str, body: ReviewIn):
    t = _get("tasks", task_id)
    status = {"approve": "approved", "request_changes": "changes_requested", "unreviewed": "unreviewed"}.get(body.verdict)
    if not status:
        raise HTTPException(400, "verdict must be approve | request_changes | unreviewed")
    db.update("tasks", task_id, review_status=status, updated_at=now())
    view = runs.task_view(_get("tasks", task_id))
    bus.emit("task", view, project_id=t["project_id"], task_id=task_id)
    delivery = None
    if body.note and body.verdict == "request_changes" and view["latest_run"]:
        delivery = await runs.deliver(view["latest_run"]["id"], f"[Review] Changes requested by {body.author}: {body.note}", author=body.author)
    return {"ok": True, "task": view, "delivery": delivery}


class MergeIn(BaseModel):
    message: str | None = None


@app.post("/api/tasks/{task_id}/merge")
def merge_task(task_id: str, body: MergeIn):
    t = _get("tasks", task_id)
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
def remove_workspace(task_id: str):
    t = _get("tasks", task_id)
    if runs.task_status(task_id) in ("queued", "running"):
        raise HTTPException(409, "task has an active run")
    workspaces.remove(_get("workspaces", t["workspace_id"]), _get("projects", t["project_id"]))
    return {"ok": True}


# ---- runs ------------------------------------------------------------------------
@app.get("/api/projects/{project_id}/runs")
def list_runs(project_id: str):
    return db.all("SELECT * FROM runs WHERE project_id = ? ORDER BY created_at DESC LIMIT 200", [project_id])


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    return _get("runs", run_id)


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    return await runs.cancel(run_id)


# ---- artifacts / feedback -----------------------------------------------------
@app.get("/api/projects/{project_id}/artifacts")
def list_artifacts(project_id: str, task_id: str | None = None):
    return artifacts.list(project_id, task_id)


@app.get("/api/artifacts/{artifact_id}")
def get_artifact(artifact_id: str):
    return artifacts.public(_get("artifacts", artifact_id))


@app.get("/api/artifacts/{artifact_id}/file")
def artifact_file(artifact_id: str):
    a = _get("artifacts", artifact_id)
    if not a["file_path"] or not Path(a["file_path"]).is_file():
        raise HTTPException(404, "artifact has no file")
    return FileResponse(a["file_path"], media_type=(a["meta"] or {}).get("mime"))


class FeedbackIn(BaseModel):
    text: str
    verdict: str = "comment"      # comment | approve | request_changes
    author: str = "human"


@app.post("/api/artifacts/{artifact_id}/feedback")
async def artifact_feedback(artifact_id: str, body: FeedbackIn):
    a = _get("artifacts", artifact_id)
    if body.verdict not in ("comment", "approve", "request_changes"):
        raise HTTPException(400, "verdict must be comment | approve | request_changes")
    fb = artifacts.record_feedback(a, body.author, body.verdict, body.text)
    text = f"[Feedback on '{a['title']}' v{a['version']} ({a['kind']}) from {body.author}: {body.verdict}] {body.text}"
    d = await runs.deliver(a["run_id"], text, author=body.author)
    fb = artifacts.mark_delivered(fb["id"], d.get("run_id"), d.get("how", "failed"))
    return {"ok": True, "feedback": fb, "delivery": d}


@app.post("/api/devservers/{ds_id}/stop")
def stop_devserver(ds_id: str):
    return devservers.stop(ds_id)


@app.get("/api/devservers/{ds_id}/logs")
def devserver_logs(ds_id: str, tail: int = 200):
    return {"id": ds_id, "log": devservers.logs(ds_id, tail)}


# ---- room / decisions -----------------------------------------------------------
@app.get("/api/projects/{project_id}/room")
def room_state(project_id: str):
    _get("projects", project_id)
    s = room.summary(project_id)
    ident = {r["id"]: room.identity(r) for r in db.all("SELECT * FROM runs WHERE project_id = ? AND status IN ('queued','running')", [project_id])}
    return {**s, "members": list(ident.values())}


class DecisionIn(BaseModel):
    decision: str           # approve | reject
    reason: str = ""
    actor: str = "human"


@app.post("/api/decisions/{decision_id}/decide")
async def decide(decision_id: str, body: DecisionIn):
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve | reject")
    r = await room.decide(decision_id, body.decision, body.reason, body.actor)
    if not r.get("ok"):
        raise HTTPException(409, r["error"])
    return r


@app.get("/api/projects/{project_id}/decisions")
def list_decisions(project_id: str):
    return db.all("SELECT * FROM decisions WHERE project_id = ? ORDER BY created_at DESC", [project_id])


# ---- provider profiles -----------------------------------------------------------
class ProfileIn(BaseModel):
    name: str
    kind: str
    base_url: str | None = None
    model: str | None = None
    credential_env: str | None = None
    extra_env: dict[str, str] = {}


@app.get("/api/profiles")
def list_profiles():
    return {"profiles": profiles.list(), "kinds": PROVIDER_KINDS}


@app.post("/api/profiles")
def create_profile(body: ProfileIn):
    try:
        return profiles.create(body.name, body.kind, body.base_url, body.model, body.credential_env, body.extra_env)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: str):
    _get("provider_profiles", profile_id)
    profiles.delete(profile_id)
    return {"ok": True}


@app.post("/api/profiles/{profile_id}/check")
async def check_profile(profile_id: str):
    """Compatibility check: streaming text, a tool call, and a clean failure
    message -- run in a throwaway directory, never persisted as a Run."""
    p = _get("provider_profiles", profile_id)
    env, snap = profiles.env_for(p)
    result = {"profile": snap, "stream_text": False, "tool_call": False, "ok": False, "error": None, "model": None}
    if snap.get("credential_present") is False:
        result["error"] = f"credential environment variable {p['credential_env']} is not set in the server process"
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
def events_since(since: int = 0, project_id: str | None = None):
    return bus.since(since, project_id)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, since: int = -1, project_id: str | None = None, token: str | None = None):
    if not _authorized(websocket.headers.get("authorization"), token):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def listener(ev: dict) -> None:
        if project_id and ev.get("project_id") not in (None, project_id):
            return
        queue.put_nowait(ev)

    unsub = bus.subscribe(listener)
    try:
        await websocket.send_text(json.dumps({"type": "hello", "last_seq": bus.last_seq()}))
        if since >= 0:
            for ev in bus.since(since, project_id):
                await websocket.send_text(json.dumps(ev, default=str))
            await websocket.send_text(json.dumps({"type": "replay_done", "last_seq": bus.last_seq()}))

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
                    for ev in bus.since(int(msg.get("since", 0)), project_id):
                        await websocket.send_text(json.dumps(ev, default=str))
                    await websocket.send_text(json.dumps({"type": "replay_done", "last_seq": bus.last_seq()}))

        done, pending = await asyncio.wait([asyncio.ensure_future(pump()), asyncio.ensure_future(listen())], return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        unsub()


# Production: `npm run build` once and this process serves the UI too, so a
# deployment is one port and one origin. Absent in development (Vite serves it).
_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="ui")
