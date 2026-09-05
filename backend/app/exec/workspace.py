"""Give a Run a real working directory (every Run must bind one, per
实况文档 §3.1). Full worktree lifecycle management (cleanup, per-agent
pooling, non-git fallback UI) is the next round's job (D6) -- this is the
minimum that makes "real directory" true: a git worktree when the project
is a git repo, a plain directory otherwise.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..store.repo import Repo, new_id

WORKSPACES_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "workspaces"


def ensure_workspace(repo: Repo, project: dict, kind: str | None = None) -> dict:
    kind = kind or ("worktree" if project["vcs"] == "git" else "dir")
    wid = new_id("wsp")
    path = WORKSPACES_ROOT / wid
    WORKSPACES_ROOT.mkdir(parents=True, exist_ok=True)

    if kind == "worktree" and project["vcs"] == "git":
        branch = f"agentroom/{wid}"
        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(path)],
            cwd=project["root_path"], capture_output=True, text=True,
        )
        if result.returncode != 0:
            # A non-git-repo root_path or a dirty worktree state shouldn't
            # crash run creation -- fall back to a plain directory and say
            # so, per D6 ("非 Git 项目退化成独立目录，明说没有合并能力").
            path.mkdir(parents=True, exist_ok=True)
            return repo.create_workspace(project["id"], "dir", str(path), branch=None, base_commit=None)
        return repo.create_workspace(project["id"], "worktree", str(path), branch=branch, base_commit=None)

    path.mkdir(parents=True, exist_ok=True)
    return repo.create_workspace(project["id"], "dir", str(path), branch=None, base_commit=None)
