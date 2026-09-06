"""EXPERIMENTAL shared-edit mode: several runs writing the same workspace,
with whole-file `Write` calls merged through a pycrdt CRDT instead of
last-writer-wins.

How it is actually wired (and therefore what is actually protected):
- Each run has its own replica of each file it touches. CC's `PreToolUse`
  hook for `Write` diffs the run's intended content against that run's
  last-known text, applies the delta to its replica, syncs replicas through
  the shared doc, and rewrites the tool's `content` (via `updatedInput`) to
  the merged text. CC then performs the write itself -- no second writer.
- `Edit` is left alone: it is already an anchored string replacement, and
  CC refuses it when `old_string` no longer matches, so a concurrent edit
  surfaces as a retry, not a clobber.
- Before every merge the shared doc is re-synced from disk, so a change made
  by `Bash`, an editor, or a formatter is picked up *if it landed before the
  next hooked write*. A Bash write that lands during the merge window is
  overwritten. That is the gap, and it is why this mode is experimental:
  nothing here can intercept a shell.
- Text convergence is not correctness; run the project's checks after.
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from claude_agent_sdk import HookMatcher
from pycrdt import Doc, Text


class _Replica:
    def __init__(self):
        self.doc = Doc()
        self.text = self.doc.get("t", type=Text)


class SharedEditor:
    """One per (workspace). Replicas keyed by (run_id, path)."""

    def __init__(self, workspace_path: str):
        self.root = Path(workspace_path)
        self.shared: dict[str, _Replica] = {}
        self.replicas: dict[tuple[str, str], _Replica] = {}
        self.base: dict[tuple[str, str], str] = {}

    def _rel(self, file_path: str) -> str | None:
        p = Path(file_path)
        if not p.is_absolute():
            p = self.root / p
        try:
            return p.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return None

    def _shared(self, rel: str) -> _Replica:
        if rel not in self.shared:
            r = _Replica()
            disk = self._disk(rel)
            if disk:
                r.text += disk
            self.shared[rel] = r
        return self.shared[rel]

    def _disk(self, rel: str) -> str:
        p = self.root / rel
        return p.read_text(errors="replace") if p.is_file() else ""

    @staticmethod
    def _apply_diff(t: Text, old: str, new: str) -> None:
        sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
        offset = 0
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                continue
            if op in ("replace", "delete"):
                del t[i1 + offset:i2 + offset]
                offset -= i2 - i1
            if op in ("replace", "insert"):
                t.insert(i1 + offset, new[j1:j2])
                offset += j2 - j1

    @staticmethod
    def _sync(a: _Replica, b: _Replica) -> None:
        b.doc.apply_update(a.doc.get_update(b.doc.get_state()))
        a.doc.apply_update(b.doc.get_update(a.doc.get_state()))

    def _fork(self, run_id: str, rel: str) -> _Replica:
        """A replica forked from the shared doc *now*: the point this run's
        later delta is relative to."""
        shared = self._shared(rel)
        disk = self._disk(rel)
        if str(shared.text) != disk:                 # pick up Bash/editor writes that landed since
            self._apply_diff(shared.text, str(shared.text), disk)
        rep = _Replica()
        self._sync(shared, rep)
        self.replicas[(run_id, rel)] = rep
        self.base[(run_id, rel)] = str(rep.text)
        return rep

    def note_seen(self, run_id: str, file_path: str, content: str | None = None) -> None:
        """The run just read (or wrote) this file: its next Write is a delta
        against what it saw now."""
        rel = self._rel(file_path)
        if rel is not None:
            self._fork(run_id, rel)

    def merge_write(self, run_id: str, file_path: str, intended: str) -> str:
        """Return the content CC should actually write."""
        rel = self._rel(file_path)
        if rel is None:
            return intended
        key = (run_id, rel)
        rep = self.replicas.get(key) or self._fork(run_id, rel)
        base = self.base[key]
        self._apply_diff(rep.text, base, intended)
        shared = self._shared(rel)
        disk = self._disk(rel)
        if str(shared.text) != disk:
            self._apply_diff(shared.text, str(shared.text), disk)
        self._sync(rep, shared)
        merged = str(shared.text)
        self.base[key] = merged
        return merged


_editors: dict[str, SharedEditor] = {}


def editor_for(workspace_path: str) -> SharedEditor:
    return _editors.setdefault(workspace_path, SharedEditor(workspace_path))


def shared_edit_hooks(mgr: Any, run: dict, ws: dict, project: dict) -> list[HookMatcher]:
    ed = editor_for(ws["path"])

    async def pre_write(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        ti = input_data.get("tool_input") or {}
        fp, content = ti.get("file_path"), ti.get("content")
        if not fp or content is None:
            return {}
        merged = ed.merge_write(run["id"], fp, content)
        mgr.bus.emit("shared_edit", {"path": fp, "merged": merged != content}, project_id=run["project_id"],
                     task_id=run["task_id"], run_id=run["id"])
        if merged == content:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                       "updatedInput": {**ti, "content": merged}}}

    return [HookMatcher(matcher="Write", hooks=[pre_write])]


def shared_edit_post_hooks(run: dict, ws: dict) -> list[HookMatcher]:
    ed = editor_for(ws["path"])

    async def post_read(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        ti = input_data.get("tool_input") or {}
        fp = ti.get("file_path")
        if fp:
            p = Path(fp)
            if p.is_file():
                ed.note_seen(run["id"], fp, p.read_text(errors="replace"))
        return {}

    return [HookMatcher(matcher="Read|Edit|Write", hooks=[post_read])]
