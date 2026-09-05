"""CRUD for the domain entities in schema.sql. Thin -- callers in exec/ and
api/ own the business rules (e.g. what counts as a valid status transition);
this module just reads and writes rows.
"""
from __future__ import annotations

import json
import time
import uuid

from .db import Database, get_db


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Repo:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    # ---- projects ----

    def create_project(self, name: str, root_path: str, vcs: str = "git") -> dict:
        pid = new_id("prj")
        now = _now_iso()
        self.db.execute(
            "INSERT INTO projects (id, name, root_path, vcs, created_at) VALUES (?, ?, ?, ?, ?)",
            (pid, name, root_path, vcs, now),
        )
        return self.get_project(pid)

    def get_project(self, project_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        return dict(row) if row else None

    def list_projects(self) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM projects ORDER BY created_at")]

    # ---- workspaces ----

    def create_workspace(self, project_id: str, kind: str, path: str,
                          branch: str | None = None, base_commit: str | None = None) -> dict:
        wid = new_id("wsp")
        now = _now_iso()
        self.db.execute(
            "INSERT INTO workspaces (id, project_id, kind, path, branch, base_commit, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (wid, project_id, kind, path, branch, base_commit, now),
        )
        return self.get_workspace(wid)

    def get_workspace(self, workspace_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))
        return dict(row) if row else None

    # ---- tasks ----

    def create_task(self, project_id: str, title: str, deps: list[str] | None = None) -> dict:
        tid = new_id("tsk")
        now = _now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO tasks (id, project_id, title, review, merge, created_at, updated_at) "
                "VALUES (?, ?, ?, 'unreviewed', 'not_merged', ?, ?)",
                (tid, project_id, title, now, now),
            )
            for dep in deps or []:
                conn.execute(
                    "INSERT INTO task_deps (task_id, depends_on_task_id) VALUES (?, ?)",
                    (tid, dep),
                )
        return self.get_task(tid)

    def get_task(self, task_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not row:
            return None
        task = dict(row)
        task["deps"] = [r["depends_on_task_id"] for r in
                         self.db.query("SELECT depends_on_task_id FROM task_deps WHERE task_id = ?", (task_id,))]
        return task

    def list_tasks(self, project_id: str) -> list[dict]:
        rows = self.db.query("SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at", (project_id,))
        return [self.get_task(r["id"]) for r in rows]

    def update_task_status(self, task_id: str, review: str | None = None, merge: str | None = None) -> dict:
        task = self.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        review = review or task["review"]
        merge = merge or task["merge"]
        self.db.execute(
            "UPDATE tasks SET review = ?, merge = ?, updated_at = ? WHERE id = ?",
            (review, merge, _now_iso(), task_id),
        )
        return self.get_task(task_id)

    # ---- sessions ----

    def create_session(self, task_id: str, title: str | None = None,
                        cc_session_id: str | None = None) -> dict:
        sid = new_id("ses")
        now = _now_iso()
        self.db.execute(
            "INSERT INTO sessions (id, task_id, cc_session_id, title, created_at) VALUES (?, ?, ?, ?, ?)",
            (sid, task_id, cc_session_id, title, now),
        )
        return self.get_session(sid)

    def get_session(self, session_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return dict(row) if row else None

    def set_session_cc_id(self, session_id: str, cc_session_id: str) -> None:
        self.db.execute("UPDATE sessions SET cc_session_id = ? WHERE id = ?", (cc_session_id, session_id))

    # ---- runs ----

    def next_attempt(self, task_id: str) -> int:
        row = self.db.query_one("SELECT COALESCE(MAX(attempt), 0) AS m FROM runs WHERE task_id = ?", (task_id,))
        return row["m"] + 1

    def create_run(self, task_id: str, session_id: str, workspace_id: str,
                    config_snapshot: dict, profile_id: str | None = None) -> dict:
        rid = new_id("run")
        now = _now_iso()
        attempt = self.next_attempt(task_id)
        self.db.execute(
            "INSERT INTO runs (id, task_id, session_id, workspace_id, profile_id, status, attempt, "
            "started_at, ended_at, cc_session_id, pid, terminal_reason, config_snapshot, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'queued', ?, NULL, NULL, NULL, NULL, NULL, ?, ?)",
            (rid, task_id, session_id, workspace_id, profile_id, attempt,
             json.dumps(config_snapshot, ensure_ascii=False), now),
        )
        return self.get_run(rid)

    def get_run(self, run_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
        if not row:
            return None
        run = dict(row)
        run["config_snapshot"] = json.loads(run["config_snapshot"])
        return run

    def list_runs(self, task_id: str) -> list[dict]:
        rows = self.db.query("SELECT id FROM runs WHERE task_id = ? ORDER BY attempt", (task_id,))
        return [self.get_run(r["id"]) for r in rows]

    def list_runs_by_status(self, status: str) -> list[dict]:
        rows = self.db.query("SELECT id FROM runs WHERE status = ?", (status,))
        return [self.get_run(r["id"]) for r in rows]

    def update_run(self, run_id: str, **fields) -> dict:
        if not fields:
            return self.get_run(run_id)
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))
        return self.get_run(run_id)

    # ---- artifacts ----

    def create_artifact(self, task_id: str, run_id: str, workspace_id: str,
                         kind: str, ref: str | None = None) -> dict:
        aid = new_id("art")
        now = _now_iso()
        row = self.db.query_one(
            "SELECT COALESCE(MAX(version), 0) AS m FROM artifacts WHERE task_id = ?", (task_id,))
        version = row["m"] + 1
        self.db.execute(
            "INSERT INTO artifacts (id, task_id, run_id, workspace_id, kind, version, ref, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, task_id, run_id, workspace_id, kind, version, ref, now),
        )
        return self.get_artifact(aid)

    def get_artifact(self, artifact_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM artifacts WHERE id = ?", (artifact_id,))
        return dict(row) if row else None

    def list_artifacts(self, task_id: str) -> list[dict]:
        return [dict(r) for r in
                self.db.query("SELECT * FROM artifacts WHERE task_id = ? ORDER BY version", (task_id,))]


_default_repo: Repo | None = None


def get_repo() -> Repo:
    global _default_repo
    if _default_repo is None:
        _default_repo = Repo()
    return _default_repo


def reset_repo_for_tests(db: Database) -> Repo:
    global _default_repo
    _default_repo = Repo(db)
    return _default_repo
