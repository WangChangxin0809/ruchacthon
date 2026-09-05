"""Thin HTTP surface (实况文档 §3.4). Business rules live in exec/ and
store/ -- these handlers just translate requests into calls on those and
shape the response.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from ..exec.workspace import ensure_workspace
from ..store.events import get_event_bus
from ..store.repo import get_repo
from .deps import get_registry
from .schemas import CreateProject, CreateTask, SendMessage

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/projects")
def list_projects():
    return get_repo().list_projects()


@router.post("/projects")
def create_project(body: CreateProject):
    return get_repo().create_project(body.name, body.root_path, body.vcs)


@router.get("/projects/{project_id}/tasks")
def list_tasks(project_id: str):
    return get_repo().list_tasks(project_id)


@router.post("/tasks")
def create_task_for_project(project_id: str, body: CreateTask):
    repo = get_repo()
    if not repo.get_project(project_id):
        raise HTTPException(404, "unknown project")
    return repo.create_task(project_id, body.title, body.deps)


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    repo = get_repo()
    task = repo.get_task(task_id)
    if not task:
        raise HTTPException(404, "unknown task")
    task["runs"] = repo.list_runs(task_id)
    return task


def _default_executor() -> str:
    return os.environ.get("AGENTROOM_EXECUTOR", "cc")


def _workspace_for_task(repo, project: dict, task_id: str) -> dict:
    runs = repo.list_runs(task_id)
    if runs:
        return repo.get_workspace(runs[-1]["workspace_id"])
    return ensure_workspace(repo, project)


@router.post("/tasks/{task_id}/messages")
async def send_message(task_id: str, body: SendMessage):
    repo = get_repo()
    task = repo.get_task(task_id)
    if not task:
        raise HTTPException(404, "unknown task")
    project = repo.get_project(task["project_id"])
    sessions = repo.db.query("SELECT id FROM sessions WHERE task_id = ? ORDER BY created_at", (task_id,))
    session = repo.get_session(sessions[-1]["id"]) if sessions else repo.create_session(task_id, title=body.message[:60])
    workspace = _workspace_for_task(repo, project, task_id)
    run_options = {**body.options, "workspace_path": workspace["path"]}
    run = await get_registry().start_run(
        task_id=task_id, session_id=session["id"], workspace_id=workspace["id"],
        prompt=body.message, executor_name=body.executor or _default_executor(), run_options=run_options,
    )
    return run


@router.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str):
    repo = get_repo()
    task = repo.get_task(task_id)
    if not task:
        raise HTTPException(404, "unknown task")
    runs = repo.list_runs(task_id)
    if not runs:
        raise HTTPException(400, "task has no prior run to retry")
    last = runs[-1]
    session = repo.get_session(last["session_id"])
    prompt = last["config_snapshot"].get("prompt", "")
    run_options = dict(last["config_snapshot"].get("options", {}))
    if session.get("cc_session_id"):
        run_options["resume"] = session["cc_session_id"]
        run_options["fork_session"] = True
    run = await get_registry().start_run(
        task_id=task_id, session_id=session["id"], workspace_id=last["workspace_id"],
        prompt=prompt, executor_name=last["config_snapshot"].get("executor", _default_executor()),
        run_options=run_options,
    )
    return run


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    run = get_repo().get_run(run_id)
    if not run:
        raise HTTPException(404, "unknown run")
    return run


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    if not get_repo().get_run(run_id):
        raise HTTPException(404, "unknown run")
    cancelled = await get_registry().cancel(run_id)
    return {"ok": cancelled}


@router.get("/runs/{run_id}/events")
def run_events(run_id: str, since_seq: int = 0, limit: int = 500):
    run = get_repo().get_run(run_id)
    if not run:
        raise HTTPException(404, "unknown run")
    task = get_repo().get_task(run["task_id"])
    events = get_event_bus().since(task["project_id"], since_seq, limit)
    return [e for e in events if e["run_id"] == run_id]


@router.get("/events")
def project_events(project_id: str, since_seq: int = 0, limit: int = 500):
    if not get_repo().get_project(project_id):
        raise HTTPException(404, "unknown project")
    return get_event_bus().since(project_id, since_seq, limit)


@router.get("/tasks/{task_id}/artifacts")
def list_artifacts(task_id: str):
    return get_repo().list_artifacts(task_id)
