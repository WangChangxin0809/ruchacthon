"""Agent definitions: one row is one set of Agent SDK options (role prompt,
tool allow/deny lists, permission mode, model, turn and budget caps, whether
it may spawn workers). Four built-ins ship with the code (`trust=system`,
never editable, re-seeded on every boot from `BUILTINS`); everything else
is a copy of one of them that a person then edits -- dsh's agent-preset
roster: copy-to-create, a default per role per team, fixed at session
creation (`sessions.agent_definition_id`), and a deleted definition resolves
to the built-in of the same session kind so old sessions keep running.

`prompt_role` (which of prompts.py's four role prompts to render) is the one
column beyond ADR 0004 §4: a copy of 审查者 must keep the reviewer prompt,
and `role` only says where a definition may be used (docs/decisions/0006).
Nothing here calls a model; `RunManager._spec` reads the row.
"""
from __future__ import annotations

import re
import secrets

from fastapi import HTTPException

from .db import Database, _enc, now
from .prompts import ROLES as PROMPT_ROLES

ROLES = ("orchestrator", "worker", "any")
PERMISSION_MODES = ("default", "plan", "acceptEdits", "bypassPermissions")
PERMISSION_LABELS = {"plan": "仅可查看", "acceptEdits": "工作区内修改", "bypassPermissions": "完全权限"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# which roles a session of each kind may be bound to
KIND_ROLES = {"main": ("orchestrator", "any"), "worker": ("worker", "any")}
BUILTIN_FOR_KIND = {"main": "orchestrator", "worker": "worker"}
BUILTIN_FOR_ROLE = {"orchestrator": "orchestrator", "worker": "worker", "any": "chat"}
SYSTEM_LOCKED = "内置定义不能改，复制一份再改"
NAME_MAX = 40

EDIT_TOOLS = ["Edit", "Write", "MultiEdit", "NotebookEdit"]
# A run has no permission prompt (nobody sits at the terminal), so under
# acceptEdits everything a worker needs must be pre-allowed here; `mcp__room`
# / `mcp__workbench` allow every tool of those in-process servers.
WORK_TOOLS = ["Bash", *EDIT_TOOLS, "WebFetch", "WebSearch", "mcp__room", "mcp__workbench"]

BUILTINS: list[dict] = [
    {"id": "orchestrator", "name": "主控", "role": "orchestrator", "prompt_role": "orchestrator", "can_spawn": 1,
     "permission_mode": "acceptEdits", "allowed_tools": WORK_TOOLS, "disallowed_tools": [], "max_turns": 80,
     "description": "主会话的 agent：规划、派 worker、汇报，自己不改代码。"},
    {"id": "worker", "name": "工作者", "role": "worker", "prompt_role": "worker", "can_spawn": 0,
     "permission_mode": "acceptEdits", "allowed_tools": WORK_TOOLS, "disallowed_tools": [], "max_turns": 100,
     "description": "在自己的 worktree 里完成一个任务，通过 Room 与其他 worker 协调，交成果给人审。"},
    {"id": "reviewer", "name": "审查者", "role": "worker", "prompt_role": "reviewer", "can_spawn": 0,
     "permission_mode": "plan", "allowed_tools": ["WebFetch", "WebSearch", "mcp__room"],
     "disallowed_tools": [*EDIT_TOOLS, "Bash", "mcp__room__room_claim", "mcp__room__room_handoff", "mcp__room__start_devserver"],
     "max_turns": 60, "description": "只读：看 diff 和测试，给出结论，不改文件、不认领路径。"},
    {"id": "chat", "name": "对话", "role": "any", "prompt_role": "chat", "can_spawn": 0,
     "permission_mode": "plan", "allowed_tools": ["Read", "Grep", "Glob", "WebFetch", "WebSearch"],
     "disallowed_tools": [*EDIT_TOOLS, "Bash"], "max_turns": 30,
     "description": "纯问答：可以读和搜索项目，没有编辑、shell 和 worker 工具。"},
]

EDITABLE_FIELDS = ("name", "description", "role", "system_prompt", "allowed_tools", "disallowed_tools", "model",
                   "permission_mode", "max_turns", "max_budget_usd", "can_spawn", "effort")


def seed_builtins(db: Database) -> None:
    """Idempotent: a missing built-in is inserted, an existing one gets the
    code's current fields (system rows are never edited by people, so the
    code is their source of truth)."""
    t = now()
    for b in BUILTINS:
        row = {"id": b["id"], "team_id": None, "owner_id": None, "name": b["name"], "description": b["description"], "trust": "system",
               "is_default": 0, "role": b["role"], "prompt_role": b["prompt_role"], "system_prompt": "", "allowed_tools": b["allowed_tools"],
               "disallowed_tools": b["disallowed_tools"], "mcp_servers": [], "model": None, "permission_mode": b["permission_mode"],
               "max_turns": b["max_turns"], "max_budget_usd": None, "can_spawn": b["can_spawn"], "effort": None, "created_at": t, "updated_at": t}
        cols = list(row)
        sets = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("id", "created_at", "is_default"))
        db.execute(f"INSERT INTO agent_definitions ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
                   f"ON CONFLICT(id) DO UPDATE SET {sets}", [_enc(v) for v in row.values()])


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:24]
    return f"{s or 'agent'}-{secrets.token_hex(3)}"


class AgentDefinitions:
    def __init__(self, db: Database, bus=None, teams=None):
        self.db, self.bus, self.teams = db, bus, teams
        seed_builtins(db)

    # ---- reading ------------------------------------------------------------
    def get(self, def_id: str | None) -> dict | None:
        return self.db.one("SELECT * FROM agent_definitions WHERE id = ?", [def_id]) if def_id else None

    def require(self, def_id: str) -> dict:
        d = self.get(def_id)
        if not d:
            raise HTTPException(404, "没有这个 agent 定义")
        return d

    def _team_ids(self, user: dict) -> set[str]:
        return {r["team_id"] for r in self.db.all("SELECT team_id FROM team_members WHERE user_id = ?", [user["id"]])}

    def visible_to(self, d: dict, user: dict) -> bool:
        if d["trust"] == "system" or user.get("is_admin"):
            return True
        if d.get("team_id"):
            return d["team_id"] in self._team_ids(user)
        return d.get("owner_id") == user["id"]

    def require_visible(self, user: dict, def_id: str) -> dict:
        d = self.require(def_id)
        if not self.visible_to(d, user):
            raise HTTPException(403, "这个 agent 定义不属于你的团队")
        return d

    def can_edit(self, d: dict, user: dict) -> bool:
        if d["trust"] == "system":
            return False
        if user.get("is_admin") or d.get("owner_id") == user["id"]:
            return True
        if d.get("team_id") and self.teams is not None:
            role = self.teams.role(user, d["team_id"])
            return role in ("admin", "owner")
        return False

    def require_editable(self, user: dict, def_id: str) -> dict:
        d = self.require(def_id)
        if d["trust"] == "system":
            raise HTTPException(403, SYSTEM_LOCKED)
        if not self.can_edit(d, user):
            raise HTTPException(403, "只有定义的 owner 或团队管理员可以改")
        return d

    def visible(self, user: dict, team_id: str | None = None, role: str | None = None) -> list[dict]:
        rows = self.db.all("SELECT * FROM agent_definitions ORDER BY CASE trust WHEN 'system' THEN 0 ELSE 1 END, name")
        order = {b["id"]: i for i, b in enumerate(BUILTINS)}
        rows.sort(key=lambda d: (0, order.get(d["id"], 99)) if d["trust"] == "system" else (1, d["name"]))
        out = []
        for d in rows:
            if not self.visible_to(d, user):
                continue
            if team_id and d.get("team_id") and d["team_id"] != team_id:
                continue
            if role and d["role"] != role:
                continue
            out.append(d)
        return out

    def public(self, d: dict, user: dict | None = None) -> dict:
        owner = self.db.one("SELECT id, handle, display_name FROM users WHERE id = ?", [d["owner_id"]]) if d.get("owner_id") else None
        return {**d, "is_default": bool(d.get("is_default")), "can_spawn": bool(d.get("can_spawn")),
                "allowed_tools": d.get("allowed_tools") or [], "disallowed_tools": d.get("disallowed_tools") or [],
                "mcp_servers": d.get("mcp_servers") or [], "editable": bool(user) and self.can_edit(d, user), "owner": owner,
                "permission_label": PERMISSION_LABELS.get(d.get("permission_mode") or "", d.get("permission_mode"))}

    # ---- defaults: one per role per team, the built-in when none is set -----
    def defaults(self, team_id: str | None) -> dict[str, str]:
        out = dict(BUILTIN_FOR_ROLE)
        if team_id:
            for r in self.db.all("SELECT id, role FROM agent_definitions WHERE team_id = ? AND is_default = 1", [team_id]):
                out[r["role"]] = r["id"]
        return out

    def default_for(self, team_id: str | None, role: str) -> str:
        return self.defaults(team_id)[role]

    def set_default(self, d: dict, team_id: str) -> dict[str, str]:
        if d["trust"] != "system" and d.get("team_id") != team_id:
            raise HTTPException(400, "这个定义不属于这个团队")
        self.db.execute("UPDATE agent_definitions SET is_default = 0 WHERE team_id = ? AND role = ?", [team_id, d["role"]])
        if d["trust"] != "system":        # choosing a built-in just clears the team's own default for that role
            self.db.update("agent_definitions", d["id"], is_default=1, updated_at=now())
        self._emit("updated", self.get(d["id"]))
        return self.defaults(team_id)

    # ---- writing ------------------------------------------------------------
    def create(self, user: dict, body: dict, team_id: str | None) -> dict:
        name = (body.get("name") or "").strip()
        if not name or len(name) > NAME_MAX:
            raise HTTPException(400, f"需要一个名字，最多 {NAME_MAX} 个字")
        src = self.require_visible(user, body["copy_from"]) if body.get("copy_from") else None
        if src is None:
            role = body.get("role") or "worker"
            if role not in ROLES:
                raise HTTPException(400, "role 必须是 orchestrator | worker | any")
            src = self.get(BUILTIN_FOR_ROLE[role])
        fields = {k: src[k] for k in (*EDITABLE_FIELDS, "prompt_role", "mcp_servers")}
        fields.update(self._clean({k: v for k, v in body.items() if k in EDITABLE_FIELDS and v is not None}))
        fields["name"] = name
        t = now()
        row = self.db.insert("agent_definitions", {"id": _slug(name), "team_id": team_id, "owner_id": user["id"], "trust": "user", "is_default": 0,
                                                   **fields, "created_at": t, "updated_at": t})
        self._emit("created", row)
        return row

    def update(self, d: dict, body: dict) -> dict:
        fields = self._clean({k: v for k, v in body.items() if k in EDITABLE_FIELDS and v is not None})
        if "name" in fields:
            fields["name"] = fields["name"].strip()
            if not fields["name"] or len(fields["name"]) > NAME_MAX:
                raise HTTPException(400, f"需要一个名字，最多 {NAME_MAX} 个字")
        if fields:
            self.db.update("agent_definitions", d["id"], updated_at=now(), **fields)
        row = self.get(d["id"])
        self._emit("updated", row)
        return row

    def delete(self, d: dict) -> None:
        if d["trust"] == "system":
            raise HTTPException(403, SYSTEM_LOCKED)
        self.db.execute("DELETE FROM agent_definitions WHERE id = ?", [d["id"]])
        self._emit("deleted", d)

    def _clean(self, f: dict) -> dict:
        if "role" in f and f["role"] not in ROLES:
            raise HTTPException(400, "role 必须是 orchestrator | worker | any")
        if "permission_mode" in f and f["permission_mode"] not in PERMISSION_MODES:
            raise HTTPException(400, "permission_mode 必须是 default | plan | acceptEdits | bypassPermissions")
        if "effort" in f and f["effort"] not in (None, "", *EFFORTS):
            raise HTTPException(400, "effort 必须是 low | medium | high | xhigh | max")
        if f.get("effort") == "":
            f["effort"] = None
        for k in ("allowed_tools", "disallowed_tools"):
            if k in f:
                if not isinstance(f[k], list) or not all(isinstance(x, str) for x in f[k]):
                    raise HTTPException(400, f"{k} 必须是字符串数组")
                f[k] = [x.strip() for x in f[k] if x.strip()]
        if "max_turns" in f:
            if not isinstance(f["max_turns"], int) or f["max_turns"] < 1:
                raise HTTPException(400, "max_turns 必须是正整数")
        if "max_budget_usd" in f:
            if not isinstance(f["max_budget_usd"], (int, float)) or f["max_budget_usd"] < 0:
                raise HTTPException(400, "max_budget_usd 必须是非负数")
        if "can_spawn" in f:
            f["can_spawn"] = 1 if f["can_spawn"] else 0
        if "description" in f:
            f["description"] = (f["description"] or "")[:500]
        if "system_prompt" in f:
            f["system_prompt"] = (f["system_prompt"] or "")[:20000]
        if "model" in f:
            f["model"] = (f["model"] or "").strip() or None
        return f

    # ---- what a session / run gets ------------------------------------------
    def resolve(self, def_id: str | None, kind: str) -> tuple[dict, bool]:
        """(definition, missing): a deleted definition falls back to the
        built-in for the session kind, and the caller can say so."""
        d = self.get(def_id)
        if d:
            return d, False
        return self.get(BUILTIN_FOR_KIND.get(kind, "worker")), def_id is not None

    def binding(self, session: dict) -> dict:
        d, missing = self.resolve(session.get("agent_definition_id"), session["kind"])
        out = {"id": d["id"], "name": d["name"], "role": d["role"], "permission_mode": d["permission_mode"], "trust": d["trust"],
               "permission_label": PERMISSION_LABELS.get(d["permission_mode"], d["permission_mode"])}
        if missing:
            out["missing"] = True
        return out

    def check_for_kind(self, user: dict, def_id: str, kind: str) -> dict:
        d = self.require_visible(user, def_id)
        if d["role"] not in KIND_ROLES[kind]:
            raise HTTPException(400, f"这个定义的角色是 {d['role']}，不能用在 {'主会话' if kind == 'main' else 'worker 会话'}")
        return d

    def _emit(self, op: str, row: dict | None) -> None:
        if self.bus is not None and row:
            self.bus.emit("agent_definition", {"op": op, "item": self.public(row)})


assert set(PROMPT_ROLES) == {b["prompt_role"] for b in BUILTINS}, "every prompts.py role has a built-in"
