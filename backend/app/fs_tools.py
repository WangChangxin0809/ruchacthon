"""Real file read/write for the chat agent and subagents, scoped to the
project root and, for writes, gated on already holding an AgentRoom claim
on that exact path -- `room_claim` stops being a formality and becomes the
actual permission check for touching a file, which is the point of having
it: see docs/decisions/0002-llm-client-abstraction.md and the user's own
"再次确认这个 app 能读取本地文件" ask that prompted adding this module (the
agent previously had claim/release bookkeeping but no way to act on it).
"""
from __future__ import annotations

from pathlib import Path

from .agent_loop import AgentIdentity
from .llm import ToolSpec
from .room_state import RoomState

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MAX_READ_CHARS = 20_000


def _resolve(path: str) -> Path:
    """Reject anything that could escape PROJECT_ROOT *before* touching the
    filesystem. `PROJECT_ROOT / path` alone is not enough: pathlib's `/`
    silently discards the left side when `path` is itself absolute, so
    `_resolve("/etc/passwd")` would otherwise resolve outside the project
    with no error -- this is exactly the mistake to not make when the path
    comes from a model, not a developer."""
    if Path(path).is_absolute() or ".." in Path(path).parts:
        raise ValueError(f"path must be relative and stay inside the project: {path}")
    resolved = (PROJECT_ROOT / path).resolve()
    resolved.relative_to(PROJECT_ROOT)  # raises ValueError if it still escaped
    return resolved


def fs_tools(room: RoomState, identity: AgentIdentity):
    async def read_file(args: dict) -> dict:
        try:
            p = _resolve(args["path"])
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not p.is_file():
            return {"ok": False, "error": f"no such file: {args['path']}"}
        text = p.read_text(errors="replace")
        return {"ok": True, "content": text[:MAX_READ_CHARS],
                "truncated": len(text) > MAX_READ_CHARS}

    async def write_file(args: dict) -> dict:
        path = args["path"]
        if not room.has_claim(path, identity.actor_id):
            return {"ok": False,
                    "error": f"you don't hold a room_claim on {path!r} -- call "
                             f"room_claim(path={path!r}, scope=...) before writing to it"}
        try:
            p = _resolve(path)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"])
        return {"ok": True}

    async def list_dir(args: dict) -> dict:
        try:
            p = _resolve(args.get("path") or ".")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not p.is_dir():
            return {"ok": False, "error": f"not a directory: {args.get('path', '.')}"}
        entries = sorted(
            e.name + ("/" if e.is_dir() else "")
            for e in p.iterdir()
            if e.name not in (".git", "node_modules", "__pycache__")
        )
        return {"ok": True, "entries": entries}

    specs = [
        ToolSpec("read_file", "Read a UTF-8 text file, path relative to the project root.",
                 {"type": "object", "properties": {"path": {"type": "string"}},
                  "required": ["path"]}),
        ToolSpec("write_file",
                 "Write a UTF-8 text file, path relative to the project root. "
                 "Requires an active room_claim on this exact path -- call "
                 "room_claim first, or this refuses.",
                 {"type": "object", "properties": {
                     "path": {"type": "string"}, "content": {"type": "string"},
                 }, "required": ["path", "content"]}),
        ToolSpec("list_dir", "List entries in a directory, relative to the project root.",
                 {"type": "object", "properties": {"path": {"type": "string"}}}),
    ]
    fns = {"read_file": read_file, "write_file": write_file, "list_dir": list_dir}
    return specs, fns
