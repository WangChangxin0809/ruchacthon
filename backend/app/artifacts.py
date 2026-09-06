"""What a run hands to a human: markdown, an image, an HTML page, a file, a
diff of its workspace, or a running dev server. Each artifact is pinned to
the task, run, workspace and a version; submitting the same title again from
the same task makes version n+1 and marks the old one `superseded`, so the UI
can never show stale work as current.

Feedback is stored against a specific version and then *delivered*: into the
worker's live session if it has one, otherwise as a new run resuming that
worker's session.
"""
from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path

from .db import DATA_DIR, Database, new_id, now
from .events import EventBus
from .workspaces import WorkspaceManager

KINDS = {"markdown", "image", "html", "file", "diff", "devserver"}


class ArtifactStore:
    def __init__(self, db: Database, bus: EventBus, workspaces: WorkspaceManager):
        self.db = db
        self.bus = bus
        self.workspaces = workspaces
        self.root = DATA_DIR / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)

    def submit(self, run: dict, kind: str, title: str, *, content: str | None = None, path: str | None = None,
               url: str | None = None, meta: dict | None = None) -> dict:
        if kind not in KINDS:
            return {"ok": False, "error": f"kind must be one of {sorted(KINDS)}"}
        ws = self.db.one("SELECT * FROM workspaces WHERE id = ?", [run["workspace_id"]])
        proj = self.db.one("SELECT * FROM projects WHERE id = ?", [run["project_id"]])
        meta = dict(meta or {})
        # a task has one live diff regardless of how the model titles it; other kinds version by title
        if kind == "diff":
            prev = self.db.one("SELECT * FROM artifacts WHERE task_id IS ? AND kind = 'diff' AND status = 'current' ORDER BY version DESC LIMIT 1", [run["task_id"]])
        else:
            prev = self.db.one("SELECT * FROM artifacts WHERE task_id IS ? AND kind = ? AND title = ? AND status = 'current' ORDER BY version DESC LIMIT 1",
                               [run["task_id"], kind, title])
        version = (prev["version"] + 1) if prev else 1
        art_id = new_id("art")
        stored_path = None
        if kind == "diff":
            d = self.workspaces.diff(ws, proj)
            if not d["available"]:
                return {"ok": False, "error": d["reason"]}
            content = d["diff"]
            meta["status"] = d["status"]
            meta["base"] = d["base"]
        elif kind in ("image", "file") or (kind == "html" and path and not content):
            if not path:
                return {"ok": False, "error": f"kind={kind} needs `path` (relative to the workspace)"}
            src = (Path(ws["path"]) / path).resolve()
            if not str(src).startswith(str(Path(ws["path"]).resolve())) or not src.is_file():
                return {"ok": False, "error": f"path must be an existing file inside the workspace: {path}"}
            dest_dir = self.root / art_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            stored_path = str(dest_dir / src.name)
            shutil.copy2(src, stored_path)
            meta["source_path"] = path
            meta["mime"] = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
            if kind == "html":
                content = Path(stored_path).read_text(errors="replace")
        elif kind in ("markdown", "html"):
            if not content:
                return {"ok": False, "error": f"kind={kind} needs `content`"}
        elif kind == "devserver":
            if not url:
                return {"ok": False, "error": "devserver artifacts are created through start_devserver"}
        if prev:
            self.db.update("artifacts", prev["id"], status="superseded")
            self.bus.emit("artifact", {**prev, "status": "superseded"}, project_id=run["project_id"], task_id=run["task_id"], run_id=prev["run_id"])
        row = self.db.insert("artifacts", {"id": art_id, "project_id": run["project_id"], "task_id": run["task_id"], "run_id": run["id"],
                                           "workspace_id": ws["id"], "kind": kind, "title": title, "version": version, "status": "current",
                                           "content": content, "file_path": stored_path, "url": url, "meta": meta, "created_at": now()})
        self.bus.emit("artifact", self.public(row), project_id=run["project_id"], task_id=run["task_id"], run_id=run["id"])
        return {"ok": True, "artifact_id": art_id, "version": version, "kind": kind}

    def public(self, row: dict) -> dict:
        d = dict(row)
        if d.get("content") and len(d["content"]) > 200_000:
            d["content"] = d["content"][:200_000] + "\n…(truncated)"
        d.pop("file_path", None)
        d["has_file"] = bool(row.get("file_path"))
        if row["kind"] == "devserver":
            ds = self.db.one("SELECT * FROM devservers WHERE artifact_id = ? ORDER BY started_at DESC LIMIT 1", [row["id"]])
            d["devserver"] = ds
            d["available"] = bool(ds and ds["status"] == "running" and ds["health"] == "healthy" and row["status"] == "current")
        else:
            d["available"] = row["status"] == "current"
        d["feedback"] = self.db.all("SELECT * FROM artifact_feedback WHERE artifact_id = ? ORDER BY created_at", [row["id"]])
        return d

    def list(self, project_id: str, task_id: str | None = None) -> list[dict]:
        if task_id:
            rows = self.db.all("SELECT * FROM artifacts WHERE project_id = ? AND task_id = ? ORDER BY created_at DESC", [project_id, task_id])
        else:
            rows = self.db.all("SELECT * FROM artifacts WHERE project_id = ? ORDER BY created_at DESC", [project_id])
        return [self.public(r) for r in rows]

    def get(self, art_id: str) -> dict | None:
        return self.db.one("SELECT * FROM artifacts WHERE id = ?", [art_id])

    def record_feedback(self, art: dict, author: str, verdict: str, text: str, user_id: str | None = None) -> dict:
        fb = self.db.insert("artifact_feedback", {"id": new_id("fb"), "artifact_id": art["id"], "version": art["version"], "author": author,
                                                  "verdict": verdict, "text": text, "delivered_run_id": None, "delivery": "pending", "created_at": now(),
                                                  "user_id": user_id})
        if verdict in ("approve", "request_changes") and art["task_id"]:
            self.db.update("tasks", art["task_id"], review_status="approved" if verdict == "approve" else "changes_requested", updated_at=now())
            self.bus.emit("task", self.db.one("SELECT * FROM tasks WHERE id = ?", [art["task_id"]]), project_id=art["project_id"], task_id=art["task_id"])
        return fb

    def mark_delivered(self, fb_id: str, run_id: str | None, how: str) -> dict:
        self.db.update("artifact_feedback", fb_id, delivered_run_id=run_id, delivery=how)
        fb = self.db.one("SELECT * FROM artifact_feedback WHERE id = ?", [fb_id])
        art = self.get(fb["artifact_id"])
        self.bus.emit("artifact_feedback", fb, project_id=art["project_id"], task_id=art["task_id"], run_id=run_id)
        return fb
