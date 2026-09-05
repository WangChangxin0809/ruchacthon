"""What the agent points the preview pane at.

Copied in spirit from AO's `ao preview` (backend/internal/preview/entry.go):
the agent chooses what is worth looking at, not the UI. A target is either a
workspace-relative file or an http(s) URL; anything that would leave the
workspace is refused rather than served.

The revision counter exists for the same reason AO's does: calling preview
again on the same file must re-navigate the pane, and a URL comparison alone
cannot tell "unchanged" from "asked for again".
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

# What a browser can show without help. Everything else is a download.
VIEWABLE = {".html": "html", ".htm": "html", ".md": "markdown", ".markdown": "markdown", ".txt": "text",
            ".svg": "image", ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
            ".pdf": "pdf", ".json": "text", ".csv": "text", ".log": "text"}
# In order: the entry a project usually means by "open it".
ENTRIES = ("index.html", "public/index.html", "dist/index.html", "build/index.html", "README.md")
_SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "target", ".next", ".cache"}
_MAX_SCAN = 5000


class PreviewError(ValueError):
    """A target the workbench refuses to point at."""


def kind_of(path: str) -> str:
    return VIEWABLE.get(Path(path).suffix.lower(), "file")


def content_type(path: str) -> str:
    if Path(path).suffix.lower() in (".md", ".markdown"):
        return "text/markdown; charset=utf-8"
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def resolve(root: str | None, target: str) -> dict:
    """A target as the session should store it.

    `{"kind": "url", "url": ...}` for http(s), `{"kind": "file", "path": ...}`
    for something inside `root`. Raises PreviewError with a sentence the agent
    can act on -- it is the one who mistyped the path.
    """
    target = (target or "").strip()
    if target.startswith(("http://", "https://")):
        return {"kind": "url", "url": target, "path": None}
    if not root:
        raise PreviewError("这个会话没有工作区，只能预览 http(s) 链接")
    base = Path(root).resolve()
    if not target:
        found = discover(base)
        if not found:
            raise PreviewError("工作区里没有找到可预览的文件（index.html / README.md / 最近改过的 .html 或 .md），"
                               "给 preview 一个明确的路径或 URL")
        target = found
    raw = target[7:] if target.startswith("file://") else target
    p = (base / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    if p != base and base not in p.parents:
        raise PreviewError(f"{target} 不在这个会话的工作区里；预览只能指向工作区内的文件或 http(s) 链接")
    if not p.is_file():
        raise PreviewError(f"{target} 不存在或不是文件")
    return {"kind": "file", "url": None, "path": p.relative_to(base).as_posix()}


def discover(base: Path) -> str | None:
    """The file `preview` with no argument means: a conventional entry point,
    else the most recently touched page in the workspace."""
    for name in ENTRIES:
        if (base / name).is_file():
            return name
    best, best_at, seen = None, -1.0, 0
    for p in base.rglob("*"):
        seen += 1
        if seen > _MAX_SCAN:
            break
        if not p.is_file() or p.suffix.lower() not in (".html", ".htm", ".md", ".markdown"):
            continue
        if any(part in _SKIP_DIRS or part.startswith(".") for part in p.relative_to(base).parts[:-1]):
            continue
        at = p.stat().st_mtime
        if at > best_at:
            best, best_at = p, at
    return best.relative_to(base).as_posix() if best else None


def safe_path(root: str | None, rel: str) -> Path:
    """Re-check confinement at serve time: the row was written by an agent and
    the workspace may have changed under it since."""
    if not root:
        raise PreviewError("这个会话没有工作区")
    base = Path(root).resolve()
    p = (base / rel).resolve()
    if p != base and base not in p.parents:
        raise PreviewError("路径不在工作区里")
    if not p.is_file():
        raise PreviewError("文件不存在")
    return p
