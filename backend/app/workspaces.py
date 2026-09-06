"""Where a Run is allowed to write.

Default for a code-writing worker: its own git worktree on its own branch, so
two workers never touch the same checkout and their work can be diffed and
merged as branches. A non-git project gets a plain copied directory instead,
and the workspace says so (`merge_available = False`): there is nothing to
merge back, only files to copy by hand.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .db import DATA_DIR, Database, new_id, now

COPY_IGNORE = shutil.ignore_patterns("node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build", ".next")


def _git(args: list[str], cwd: str | Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120)


def commit_identity(cwd: str | Path) -> list[str]:
    """`git -c` flags that give a commit an author when the machine has none.

    A freshly provisioned server has no `user.email`, and git then refuses to
    commit -- so merging a finished task fails at the one step the human just
    pressed a button for, with git's "Please tell me who you are". Returns an
    empty list where an identity is configured, so a real one always wins; the
    fallback matches the one main.py stamps on a project's initial commit.
    """
    if _git(["config", "user.email"], cwd).returncode == 0:
        return []
    return ["-c", "user.name=workbench", "-c", "user.email=workbench@local"]


def is_git_repo(path: str | Path) -> bool:
    r = _git(["rev-parse", "--is-inside-work-tree"], path)
    return r.returncode == 0 and r.stdout.strip() == "true"


class WorkspaceManager:
    def __init__(self, db: Database):
        self.db = db
        self.root = DATA_DIR / "workspaces"

    def get(self, ws_id: str) -> dict | None:
        return self.db.one("SELECT * FROM workspaces WHERE id = ?", [ws_id])

    def main_workspace(self, project: dict) -> dict:
        ws = self.db.one("SELECT * FROM workspaces WHERE project_id = ? AND kind = 'main'", [project["id"]])
        if ws:
            return ws
        branch = None
        if project["is_git"]:
            r = _git(["rev-parse", "--abbrev-ref", "HEAD"], project["root_path"])
            branch = r.stdout.strip() if r.returncode == 0 else None
        return self.db.insert("workspaces", {"id": new_id("ws"), "project_id": project["id"], "kind": "main",
                                             "path": project["root_path"], "branch": branch, "base_ref": None,
                                             "status": "active", "edit_mode": "exclusive", "created_at": now()})

    def create_isolated(self, project: dict, label: str, edit_mode: str = "exclusive") -> dict:
        ws_id = new_id("ws")
        target = self.root / project["id"] / ws_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if project["is_git"]:
            branch = f"wb/{_slug(label)}-{ws_id[-6:]}"
            head = _git(["rev-parse", "HEAD"], project["root_path"]).stdout.strip()
            r = _git(["worktree", "add", "-b", branch, str(target), "HEAD"], project["root_path"])
            if r.returncode != 0:
                raise RuntimeError(f"git worktree add failed: {r.stderr.strip()}")
            row = {"kind": "worktree", "branch": branch, "base_ref": head}
        else:
            shutil.copytree(project["root_path"], target, ignore=COPY_IGNORE, dirs_exist_ok=True)
            row = {"kind": "dir", "branch": None, "base_ref": None}
        return self.db.insert("workspaces", {"id": ws_id, "project_id": project["id"], "path": str(target),
                                             "status": "active", "edit_mode": edit_mode, "created_at": now(), **row})

    def remove(self, ws: dict, project: dict) -> None:
        if ws["kind"] == "main":
            return
        if ws["kind"] == "worktree":
            _git(["worktree", "remove", "--force", ws["path"]], project["root_path"])
        else:
            shutil.rmtree(ws["path"], ignore_errors=True)
        self.db.update("workspaces", ws["id"], status="removed")

    def diff(self, ws: dict, project: dict) -> dict:
        """The workspace's changes relative to where it started."""
        if ws["kind"] == "main" and project["is_git"]:
            r = _git(["diff", "HEAD"], ws["path"])
            files = _git(["status", "--porcelain"], ws["path"]).stdout
            return {"available": True, "diff": r.stdout, "status": files, "base": "HEAD"}
        if ws["kind"] == "worktree":
            _git(["add", "-A", "--intent-to-add"], ws["path"])
            r = _git(["diff", ws["base_ref"]], ws["path"])
            files = _git(["status", "--porcelain"], ws["path"]).stdout
            return {"available": True, "diff": r.stdout, "status": files, "base": ws["base_ref"]}
        return {"available": False, "reason": "workspace is a plain directory copy; no git history to diff against", "diff": "", "status": ""}

    def merge_available(self, ws: dict) -> bool:
        return ws["kind"] == "worktree"

    def merge_into_main(self, ws: dict, project: dict, message: str) -> dict:
        if not self.merge_available(ws):
            return {"ok": False, "error": "no merge capability for this workspace (not a git worktree)"}
        _git(["add", "-A"], ws["path"])
        ident = commit_identity(ws["path"])
        c = _git([*ident, "commit", "-q", "-m", f"Workbench task: {ws['branch']}", "--allow-empty"], ws["path"])
        if c.returncode != 0 and "nothing to commit" not in c.stdout + c.stderr:
            return {"ok": False, "error": f"commit in worktree failed: {(c.stderr or c.stdout).strip()}"}
        # --no-ff always writes a merge commit, so it needs an author too
        r = _git([*commit_identity(project["root_path"]), "merge", "--no-ff", "-m", message, ws["branch"]],
                 project["root_path"])
        if r.returncode != 0:
            _git(["merge", "--abort"], project["root_path"])
            return {"ok": False, "error": f"merge conflict, aborted: {(r.stdout + r.stderr).strip()[:2000]}"}
        return {"ok": True, "output": r.stdout.strip()}

    def sweep_orphans(self) -> None:
        """Worktrees git still lists but whose directory is gone (a reboot, a
        manual rm) make later `worktree add` calls fail; prune them."""
        for p in self.db.all("SELECT DISTINCT project_id FROM workspaces WHERE kind = 'worktree'"):
            proj = self.db.one("SELECT * FROM projects WHERE id = ?", [p["project_id"]])
            if proj and Path(proj["root_path"]).exists():
                _git(["worktree", "prune"], proj["root_path"])


def _slug(s: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in s).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:32] or "task"
