"""Teams, invites and conversations -- the Feishu/Slack shape: one deployment
is one organisation, a team is who can see each other, a conversation is
who is in a room. DMs, groups and work sessions are all `conversations`
with one member table, so there is one visibility rule and one unread rule;
the two message tables (`chat_messages`, `messages`) stay as they are.

Also here: the @ rule (design §2.1) and the event visibility filter the
WebSocket applies, both pure functions so tests can hit them directly.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from .db import Database, new_id, now

ROLE_RANK = {"member": 0, "admin": 1, "owner": 2}
DEFAULT_TEAM = {"slug": "default", "name": "默认团队"}
DEFAULT_GROUP = "全员"
AGENT_REPLY = ("auto", "always", "mention", "never")
NOT_MEMBER = "你不是这个会话的成员"

# `@name` where name may be Chinese; the lookbehind is ASCII-only so 喂@agent
# (no space before the @, normal in Chinese) still counts and a@b.com does not.
MENTION_RE = re.compile(r"(?<![A-Za-z0-9_@.])@([\w一-鿿][\w一-鿿.-]{0,31})")
AGENT_ALIASES = ("agent", "claude", "ai", "主 agent", "主agent")
MACHINE_AUTHORS = {"tool", "room", "system", "main-agent", "claude"}   # what runs.py / room.py write as `author`


def mentioned_names(text: str) -> set[str]:
    return {m.group(1).lower() for m in MENTION_RE.finditer(text or "")}


def agent_mentioned(text: str, session: dict) -> bool:
    names = list(AGENT_ALIASES)
    if session.get("agent_name"):
        names.append(session["agent_name"].strip())
    for n in names:
        pat = r"(?<![A-Za-z0-9_@.])@" + r"\s*".join(re.escape(p) for p in n.split()) + r"(?![A-Za-z0-9_])"
        if re.search(pat, text or "", re.I):
            return True
    return False


def should_run(mode: str, humans: int, text: str, session: dict) -> bool:
    """One human: the agent answers everything. Several: only when @-ed,
    unless the conversation owner set agent_reply=always (or never)."""
    if mode == "never":
        return False
    if mode == "always":
        return True
    if mode == "auto" and humans <= 1:
        return True
    return agent_mentioned(text, session)


# Visible to everyone logged in: nothing in them belongs to a project or a person.
GLOBAL_EVENTS = {"server_started", "cc_status"}
# Visible to every member of the project: the Room is the project's shared
# coordination state (GET /api/projects/{id}/room shows the same), and a
# pending decision is for any member to take (hard rule 1).
PROJECT_EVENTS = {"project", "decision", "run_blocked", "run_unblocked", "claim", "claims_released", "claim_expired",
                  "room_message", "handoff"}


def event_visible(ev: dict, ctx: dict) -> bool:
    """Default deny. ctx = {"user", "sessions": visible session ids, "convs": my
    conversation ids, "projects": visible project ids, "teams": my team ids}.
    Anything about a run or task carries its session_id (events.py fills it
    in) and is gated like GET /api/sessions/{id}; the rest is listed above."""
    user = ctx["user"]
    if ev.get("user_id"):
        return ev["user_id"] == user["id"]
    if user.get("is_admin"):
        return True
    p = ev.get("payload") or {}
    t = ev.get("type")
    if t == "conversation_member":
        return (p.get("member_kind") == "user" and p.get("member_id") == user["id"]) or p.get("conversation_id") in ctx["convs"]
    if t in ("chat_message", "conversation"):
        return (p.get("conversation_id") or p.get("id")) in ctx["convs"]
    if t in ("team", "team_member"):
        return (p.get("team_id") or p.get("id")) in ctx.get("teams", set()) or p.get("user_id") == user["id"]
    if t in GLOBAL_EVENTS:
        return True
    if ev.get("project_id") and ev["project_id"] not in ctx.get("projects", set()):
        return False
    if ev.get("session_id"):
        return ev["session_id"] in ctx["sessions"]
    return t in PROJECT_EVENTS and bool(ev.get("project_id"))


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:32] or "team"


class Teams:
    def __init__(self, db: Database, bus=None, notify=None):
        self.db, self.bus, self.notify = db, bus, notify

    def _emit(self, type_: str, payload: dict, **ids) -> None:
        if self.bus is not None:
            self.bus.emit(type_, payload, **ids)

    def _notify(self, user_id: str | None, kind: str, title: str, body: str = "", **kw) -> None:
        if self.notify is not None and user_id:
            self.notify.send(user_id, kind, title, body, **kw)

    # ---- users --------------------------------------------------------------
    def user(self, user_id: str | None) -> dict | None:
        return self.db.one("SELECT id, handle, display_name, is_admin FROM users WHERE id = ?", [user_id]) if user_id else None

    def name_of(self, user_id: str | None) -> str:
        u = self.user(user_id)
        return u["display_name"] if u else (user_id or "?")

    def users_visible_to(self, user: dict, team_id: str | None = None, q: str = "") -> list[dict]:
        teams = [team_id] if team_id else list(self.team_ids(user))
        if user.get("is_admin") and not team_id:
            rows = self.db.all("SELECT id, handle, display_name FROM users ORDER BY handle")
        elif not teams:
            rows = [self.user(user["id"])] if self.user(user["id"]) else []
        else:
            marks = ",".join("?" for _ in teams)
            rows = self.db.all(f"SELECT DISTINCT u.id, u.handle, u.display_name FROM users u JOIN team_members m ON m.user_id = u.id "
                               f"WHERE m.team_id IN ({marks}) ORDER BY u.handle", teams)
        q = (q or "").lower()
        return [{"id": r["id"], "handle": r["handle"], "display_name": r["display_name"]} for r in rows
                if not q or q in r["handle"].lower() or q in r["display_name"].lower()]

    # ---- teams --------------------------------------------------------------
    def team(self, team_id: str) -> dict:
        t = self.db.one("SELECT * FROM teams WHERE id = ?", [team_id])
        if not t:
            raise HTTPException(404, "没有这个团队")
        return t

    def team_ids(self, user: dict) -> set[str]:
        return {r["team_id"] for r in self.db.all("SELECT team_id FROM team_members WHERE user_id = ?", [user["id"]])}

    def role(self, user: dict, team_id: str) -> str | None:
        r = self.db.one("SELECT role FROM team_members WHERE team_id = ? AND user_id = ?", [team_id, user["id"]])
        return r["role"] if r else None

    def require_team(self, user: dict, team_id: str | None, min_role: str = "member") -> dict | None:
        """Legacy rows (team_id NULL) stay open to everybody; instance admins pass everywhere."""
        if team_id is None:
            return None
        t = self.team(team_id)
        if user.get("is_admin"):
            return t
        r = self.role(user, team_id)
        if r is None or ROLE_RANK[r] < ROLE_RANK[min_role]:
            raise HTTPException(403, "你不是这个团队的成员" if r is None else f"需要团队 {min_role} 权限")
        return t

    def my_teams(self, user: dict) -> list[dict]:
        rows = self.db.all("SELECT t.*, m.role FROM teams t JOIN team_members m ON m.team_id = t.id WHERE m.user_id = ? ORDER BY t.created_at",
                           [user["id"]])
        for r in rows:
            r["member_count"] = self.db.one("SELECT COUNT(*) AS n FROM team_members WHERE team_id = ?", [r["id"]])["n"]
        return rows

    def members(self, team_id: str) -> list[dict]:
        return self.db.all("SELECT m.user_id, u.handle, u.display_name, m.role, m.joined_at FROM team_members m JOIN users u ON u.id = m.user_id "
                           "WHERE m.team_id = ? ORDER BY m.joined_at", [team_id])

    def create_team(self, user: dict, name: str, slug: str | None = None) -> dict:
        name = (name or "").strip()
        if not name:
            raise HTTPException(400, "团队名不能为空")
        slug = _slug(slug or name)
        if self.db.one("SELECT id FROM teams WHERE slug = ?", [slug]):
            slug = f"{slug}-{secrets.token_hex(2)}"
        t = self.db.insert("teams", {"id": new_id("team"), "slug": slug, "name": name, "owner_id": user["id"],
                                     "default_profile_id": None, "default_model": None, "created_at": now()})
        self.db.insert("team_members", {"team_id": t["id"], "user_id": user["id"], "role": "owner", "joined_at": now()})
        self._default_group(t, user["id"])
        self._emit("team", t)
        return t

    def _default_group(self, team: dict, owner_id: str) -> dict:
        conv = self.db.one("SELECT * FROM conversations WHERE team_id = ? AND kind = 'group' AND is_default = 1", [team["id"]])
        if not conv:
            conv = self.db.insert("conversations", {"id": new_id("conv"), "kind": "group", "team_id": team["id"], "project_id": None,
                                                    "session_id": None, "title": DEFAULT_GROUP, "owner_id": owner_id, "dm_key": None,
                                                    "is_default": 1, "agent_reply": "auto", "created_at": now(), "updated_at": now()})
        self._add_member_row(conv["id"], owner_id, "owner", None)
        return conv

    def add_member(self, team: dict, user_id: str, role: str = "member", added_by: str | None = None) -> dict:
        if role not in ROLE_RANK:
            raise HTTPException(400, "role 必须是 owner | admin | member")
        if not self.user(user_id):
            raise HTTPException(404, "没有这个用户")
        self.db.execute("INSERT OR IGNORE INTO team_members (team_id, user_id, role, joined_at) VALUES (?,?,?,?)",
                        [team["id"], user_id, role, now()])
        conv = self._default_group(team, team["owner_id"])
        self._add_member_row(conv["id"], user_id, "member", added_by)
        self._emit("team_member", {"team_id": team["id"], "user_id": user_id, "role": role})
        self._notify(user_id, "member_added", f"你加入了团队「{team['name']}」", actor_id=added_by, link=f"?area=chat&c={conv['id']}")
        return {"team_id": team["id"], "user_id": user_id, "role": role}

    def set_role(self, team: dict, user_id: str, role: str) -> None:
        if role not in ROLE_RANK:
            raise HTTPException(400, "role 必须是 owner | admin | member")
        cur = self.db.one("SELECT role FROM team_members WHERE team_id = ? AND user_id = ?", [team["id"], user_id])
        if not cur:
            raise HTTPException(404, "不是团队成员")
        owners = self.db.one("SELECT COUNT(*) AS n FROM team_members WHERE team_id = ? AND role = 'owner'", [team["id"]])["n"]
        if cur["role"] == "owner" and role != "owner" and owners <= 1:
            raise HTTPException(409, "团队至少要有一个 owner")
        self.db.execute("UPDATE team_members SET role = ? WHERE team_id = ? AND user_id = ?", [role, team["id"], user_id])
        self._emit("team_member", {"team_id": team["id"], "user_id": user_id, "role": role})

    def _team_conversations(self, team_id: str) -> list[dict]:
        """Groups and work sessions that belong to the team (by team_id or through
        the project); DMs are between two people and are not team content."""
        return self.db.all("SELECT c.id FROM conversations c LEFT JOIN projects p ON p.id = c.project_id "
                           "WHERE c.kind != 'dm' AND (c.team_id = ? OR p.team_id = ?)", [team_id, team_id])

    def _drop_member_rows(self, conv_id: str, user_id: str | None = None) -> None:
        q, params = "SELECT member_kind, member_id FROM conversation_members WHERE conversation_id = ?", [conv_id]
        if user_id:
            q += " AND member_kind = 'user' AND member_id = ?"
            params.append(user_id)
        for r in self.db.all(q, params):
            self.db.execute("DELETE FROM conversation_members WHERE conversation_id = ? AND member_kind = ? AND member_id = ?",
                            [conv_id, r["member_kind"], r["member_id"]])
            # one event per row: an open socket of the removed person refreshes on it
            self._emit("conversation_member", {"conversation_id": conv_id, "member_kind": r["member_kind"], "member_id": r["member_id"], "removed": True})

    def remove_member(self, team: dict, user_id: str) -> None:
        cur = self.db.one("SELECT role FROM team_members WHERE team_id = ? AND user_id = ?", [team["id"], user_id])
        if not cur:
            raise HTTPException(404, "不是团队成员")
        if cur["role"] == "owner":
            raise HTTPException(409, "owner 不能被移除，先转让")
        self.db.execute("DELETE FROM team_members WHERE team_id = ? AND user_id = ?", [team["id"], user_id])
        for c in self._team_conversations(team["id"]):
            self._drop_member_rows(c["id"], user_id)
        self._emit("team_member", {"team_id": team["id"], "user_id": user_id, "role": None, "removed": True})

    def delete_team(self, team: dict) -> None:
        if self.db.one("SELECT id FROM projects WHERE team_id = ? LIMIT 1", [team["id"]]):
            raise HTTPException(409, "团队下还有项目，不能删除")
        for c in self.db.all("SELECT id FROM conversations WHERE team_id = ?", [team["id"]]):
            self._drop_member_rows(c["id"])
        self.db.execute("DELETE FROM conversations WHERE team_id = ?", [team["id"]])
        self.db.execute("DELETE FROM invites WHERE team_id = ?", [team["id"]])
        self.db.execute("DELETE FROM team_members WHERE team_id = ?", [team["id"]])
        self.db.execute("DELETE FROM teams WHERE id = ?", [team["id"]])
        self._emit("team", {"id": team["id"], "deleted": True})

    def default_team_for(self, user: dict) -> str | None:
        pref = (user.get("prefs") or {}).get("current_team_id")
        if pref and self.role(user, pref):
            return pref
        mine = self.my_teams(user)
        return mine[0]["id"] if mine else None

    def project_visible(self, user: dict, project: dict) -> bool:
        return user.get("is_admin") or project.get("team_id") is None or project["team_id"] in self.team_ids(user)

    # ---- invites ------------------------------------------------------------
    def create_invite(self, team: dict, created_by: str, role: str = "member", expires_in_hours: int = 168, max_uses: int | None = None) -> dict:
        if role not in ("member", "admin"):
            raise HTTPException(400, "邀请只能是 member 或 admin")
        exp = (datetime.now(timezone.utc) + timedelta(hours=max(1, int(expires_in_hours or 168)))).isoformat(timespec="milliseconds")
        # stored in the clear on purpose: it is a share link the owner copies again later, not a credential
        return self.db.insert("invites", {"id": new_id("inv"), "team_id": team["id"], "token": secrets.token_urlsafe(24), "created_by": created_by,
                                          "role": role, "max_uses": max_uses, "uses": 0, "expires_at": exp, "revoked_at": None, "created_at": now()})

    def list_invites(self, team_id: str) -> list[dict]:
        return self.db.all("SELECT * FROM invites WHERE team_id = ? ORDER BY created_at DESC", [team_id])

    def revoke_invite(self, team_id: str, invite_id: str) -> None:
        if not self.db.one("SELECT id FROM invites WHERE id = ? AND team_id = ?", [invite_id, team_id]):
            raise HTTPException(404, "没有这个邀请")
        self.db.update("invites", invite_id, revoked_at=now())

    def invite_status(self, token: str) -> tuple[dict | None, bool, str | None]:
        inv = self.db.one("SELECT * FROM invites WHERE token = ?", [token])
        if not inv:
            return None, False, "邀请不存在"
        if inv["revoked_at"]:
            return inv, False, "邀请已撤销"
        if inv["expires_at"] <= now():
            return inv, False, "邀请已过期"
        if inv["max_uses"] is not None and inv["uses"] >= inv["max_uses"]:
            return inv, False, "邀请已用完"
        return inv, True, None

    def accept_invite(self, token: str, user: dict) -> dict:
        inv, ok, why = self.invite_status(token)
        if not ok:
            raise HTTPException(410 if inv else 404, why)
        team = self.team(inv["team_id"])
        if self.role(user, team["id"]):
            return team
        self.add_member(team, user["id"], inv["role"], added_by=inv["created_by"])
        self.db.execute("UPDATE invites SET uses = uses + 1 WHERE id = ?", [inv["id"]])
        self._notify(inv["created_by"], "invite_accepted", f"{user['display_name']} 通过邀请加入了「{team['name']}」", actor_id=user["id"])
        return team

    # ---- conversations ------------------------------------------------------
    def conversation(self, conv_id: str) -> dict:
        c = self.db.one("SELECT * FROM conversations WHERE id = ?", [conv_id])
        if not c:
            raise HTTPException(404, "没有这个会话")
        return c

    def conv_of_session(self, session_id: str) -> dict | None:
        return self.db.one("SELECT * FROM conversations WHERE session_id = ?", [session_id])

    def conv_members(self, conv_id: str) -> list[dict]:
        rows = self.db.all("SELECT * FROM conversation_members WHERE conversation_id = ? ORDER BY joined_at", [conv_id])
        for r in rows:
            if r["member_kind"] == "user":
                u = self.user(r["member_id"])
                r["name"], r["handle"] = (u["display_name"], u["handle"]) if u else (r["member_id"], None)
            else:
                s = self.db.one("SELECT agent_name, title FROM sessions WHERE id = ?", [r["member_id"]])
                r["name"], r["handle"] = ((s["agent_name"] or s["title"]) if s else r["member_id"]), None
        return rows

    def human_count(self, conv_id: str) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM conversation_members WHERE conversation_id = ? AND member_kind = 'user'", [conv_id])["n"]

    def is_member(self, user: dict, conv_id: str) -> bool:
        if user.get("is_admin"):
            return True
        return bool(self.db.one("SELECT 1 FROM conversation_members WHERE conversation_id = ? AND member_kind = 'user' AND member_id = ?",
                                [conv_id, user["id"]]))

    def require_conv(self, user: dict, conv_id: str, owner: bool = False) -> dict:
        c = self.conversation(conv_id)
        if not self.is_member(user, conv_id):
            raise HTTPException(403, NOT_MEMBER)
        if owner and not user.get("is_admin") and c["owner_id"] != user["id"]:
            raise HTTPException(403, "只有会话 owner 可以这样做")
        return c

    def require_session_member(self, user: dict, session: dict) -> dict | None:
        conv = self.conv_of_session(session["id"])
        if conv is None:                      # a session without a conversation is legacy data: open
            return None
        # a stale member row must never outrank the team: leaving the team ends the session too
        project = self.db.one("SELECT team_id FROM projects WHERE id = ?", [conv["project_id"]]) if conv.get("project_id") else None
        if not self.is_member(user, conv["id"]) or (project and not self.project_visible(user, project)):
            raise HTTPException(403, NOT_MEMBER)
        return conv

    def agent_may_reach(self, user_id: str | None, session: dict) -> bool:
        """May a run created by `user_id` act on `session` (message it, read
        it, kill it)? The agent inherits its creator's reach: a member of the
        session's conversation, or any team member for the project's main
        session, which is the project's public room."""
        conv = self.conv_of_session(session["id"])
        if conv is None:                      # legacy session without a conversation: open
            return True
        user = self.user(user_id)
        if not user:
            return False
        if session["kind"] == "main":
            project = self.db.one("SELECT team_id FROM projects WHERE id = ?", [session["project_id"]]) or {}
            return self.project_visible(user, project)
        return self.is_member(user, conv["id"])

    def visible_sessions(self, user: dict) -> set[str]:
        if user.get("is_admin"):
            return {r["id"] for r in self.db.all("SELECT id FROM sessions")}
        projects = self.visible_project_ids(user)
        return {r["session_id"] for r in self.db.all(
            "SELECT c.session_id, c.project_id FROM conversation_members m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.member_kind = 'user' AND m.member_id = ? AND c.session_id IS NOT NULL", [user["id"]])
            if r["project_id"] is None or r["project_id"] in projects}

    def my_conversation_ids(self, user: dict) -> set[str]:
        return {r["conversation_id"] for r in self.db.all(
            "SELECT conversation_id FROM conversation_members WHERE member_kind = 'user' AND member_id = ?", [user["id"]])}

    def visible_project_ids(self, user: dict) -> set[str]:
        return {p["id"] for p in self.db.all("SELECT id, team_id FROM projects") if self.project_visible(user, p)}

    def ws_context(self, user: dict) -> dict:
        return {"user": user, "sessions": self.visible_sessions(user), "convs": self.my_conversation_ids(user),
                "projects": self.visible_project_ids(user), "teams": self.team_ids(user)}

    def _add_member_row(self, conv_id: str, member_id: str, role: str, added_by: str | None, kind: str = "user") -> bool:
        cur = self.db.execute("INSERT OR IGNORE INTO conversation_members (conversation_id, member_kind, member_id, role, added_by, joined_at) "
                              "VALUES (?,?,?,?,?,?)", [conv_id, kind, member_id, role, added_by, now()])
        if cur.rowcount:
            self._emit("conversation_member", {"conversation_id": conv_id, "member_kind": kind, "member_id": member_id, "role": role, "added_by": added_by})
        return bool(cur.rowcount)

    def ensure_session_conversation(self, session: dict, owner_id: str | None, members: list[str] | None = None,
                                    agent_reply: str = "auto") -> dict:
        conv = self.conv_of_session(session["id"])
        seat_owner = False        # people are seated once, when the room is made: a later call must not undo a removal
        if not conv:
            project = self.db.one("SELECT team_id FROM projects WHERE id = ?", [session["project_id"]]) or {}
            conv = self.db.insert("conversations", {"id": "conv_" + session["id"].split("_", 1)[-1], "kind": "session", "team_id": project.get("team_id"),
                                                    "project_id": session["project_id"], "session_id": session["id"], "title": session["title"],
                                                    "owner_id": owner_id, "dm_key": None, "is_default": 0, "agent_reply": agent_reply,
                                                    "created_at": now(), "updated_at": now()})
            self._emit("conversation", conv, project_id=session["project_id"])
            seat_owner = True
        elif owner_id and not conv["owner_id"]:
            self.db.update("conversations", conv["id"], owner_id=owner_id)
            seat_owner = True
        self._add_member_row(conv["id"], session["id"], "agent", None, kind="agent")
        if owner_id and seat_owner:
            self._add_member_row(conv["id"], owner_id, "owner", None)
        for uid in members or []:
            if uid != owner_id:
                self._add_member_row(conv["id"], uid, "member", owner_id)
        return conv

    def join_main_session(self, user: dict, project: dict, session: dict) -> dict | None:
        """The main session is the project's shared room: a team member who opens it is in."""
        conv = self.ensure_session_conversation(session, session.get("created_by"))
        if self.project_visible(user, project) and not user.get("bootstrap"):
            self._add_member_row(conv["id"], user["id"], "member", None)
        return conv

    def create_group(self, user: dict, team_id: str, title: str, member_ids: list[str]) -> dict:
        team = self.require_team(user, team_id)
        title = (title or "").strip()
        if not title:
            raise HTTPException(400, "群名不能为空")
        ids = [i for i in dict.fromkeys(member_ids or []) if i != user["id"]]
        for i in ids:
            if not self.role({"id": i}, team["id"]):
                raise HTTPException(400, f"{self.name_of(i)} 不在这个团队里")
        conv = self.db.insert("conversations", {"id": new_id("conv"), "kind": "group", "team_id": team["id"], "project_id": None, "session_id": None,
                                                "title": title, "owner_id": user["id"], "dm_key": None, "is_default": 0, "agent_reply": "auto",
                                                "created_at": now(), "updated_at": now()})
        self._add_member_row(conv["id"], user["id"], "owner", None)
        for i in ids:
            self._add_member_row(conv["id"], i, "member", user["id"])
            self._notify(i, "member_added", f"{user['display_name']} 把你拉进了群「{title}」", actor_id=user["id"],
                         conversation_id=conv["id"], link=f"?area=chat&c={conv['id']}")
        self._emit("conversation", conv)
        return conv

    def get_or_create_dm(self, user: dict, other_id: str) -> dict:
        other = self.user(other_id)
        if not other or other_id == user["id"]:
            raise HTTPException(400, "选一个别人")
        shared = self.team_ids(user) & self.team_ids(other)
        if not shared and not user.get("is_admin"):
            raise HTTPException(403, "你们不在同一个团队")
        key = "|".join(sorted([user["id"], other_id]))
        conv = self.db.one("SELECT * FROM conversations WHERE dm_key = ?", [key])
        if conv:
            return conv
        conv = self.db.insert("conversations", {"id": new_id("conv"), "kind": "dm", "team_id": sorted(shared)[0] if shared else None, "project_id": None,
                                                "session_id": None, "title": "私聊", "owner_id": None, "dm_key": key, "is_default": 0,
                                                "agent_reply": "auto", "created_at": now(), "updated_at": now()})
        self._add_member_row(conv["id"], user["id"], "member", None)
        self._add_member_row(conv["id"], other_id, "member", user["id"])
        self._emit("conversation", conv)
        return conv

    def add_conv_member(self, user: dict, conv: dict, user_id: str) -> dict:
        if conv["kind"] == "dm":
            raise HTTPException(400, "私聊不能加人，建个群")
        if conv["kind"] == "session":
            self.require_conv(user, conv["id"], owner=True)
        else:
            self.require_conv(user, conv["id"])
        if not self.user(user_id):
            raise HTTPException(404, "没有这个用户")
        if conv["team_id"] and not self.role({"id": user_id}, conv["team_id"]) and not user.get("is_admin"):
            raise HTTPException(400, f"{self.name_of(user_id)} 不在这个团队里")
        added = self._add_member_row(conv["id"], user_id, "member", user["id"])
        if added:
            link = f"?p={conv['project_id']}&s={conv['session_id']}" if conv["kind"] == "session" else f"?area=chat&c={conv['id']}"
            self._notify(user_id, "member_added", f"{user['display_name']} 把你加进了「{conv['title']}」", actor_id=user["id"],
                         conversation_id=conv["id"], project_id=conv.get("project_id"), link=link)
        return {"ok": True, "added": added}

    def add_conv_agent(self, user: dict, conv: dict, session_id: str) -> dict:
        """Put a work session's agent into a group (design §2.1: `@主 agent` in a
        group sends into its session). The caller must be in the group and in
        that session, and the session's project must be the group's team's."""
        if conv["kind"] != "group":
            raise HTTPException(400, "只有群可以加 agent")
        self.require_conv(user, conv["id"])
        session = self.db.one("SELECT * FROM sessions WHERE id = ?", [session_id])
        if not session:
            raise HTTPException(404, "没有这个会话")
        self.require_session_member(user, session)
        project = self.db.one("SELECT team_id FROM projects WHERE id = ?", [session["project_id"]]) or {}
        if conv["team_id"] and project.get("team_id") not in (None, conv["team_id"]):
            raise HTTPException(400, "这个 agent 的项目不属于这个群的团队")
        added = self._add_member_row(conv["id"], session_id, "agent", user["id"], kind="agent")
        return {"ok": True, "added": added, "member_kind": "agent"}

    def remove_conv_member(self, user: dict, conv: dict, user_id: str) -> dict:
        if self.db.one("SELECT 1 FROM conversation_members WHERE conversation_id = ? AND member_kind = 'agent' AND member_id = ?", [conv["id"], user_id]):
            if conv["kind"] != "group":
                raise HTTPException(400, "工作会话的 agent 不能被移出")
            self.require_conv(user, conv["id"])
            n = self.db.execute("DELETE FROM conversation_members WHERE conversation_id = ? AND member_kind = 'agent' AND member_id = ?",
                                [conv["id"], user_id]).rowcount
            self._emit("conversation_member", {"conversation_id": conv["id"], "member_kind": "agent", "member_id": user_id, "removed": True})
            return {"ok": True, "removed": n}
        if user_id != user["id"]:
            self.require_conv(user, conv["id"], owner=True)
        elif conv["owner_id"] == user["id"]:
            raise HTTPException(409, "owner 不能退出，先用 PATCH 转让 owner_id")
        n = self.db.execute("DELETE FROM conversation_members WHERE conversation_id = ? AND member_kind = 'user' AND member_id = ?",
                            [conv["id"], user_id]).rowcount
        self._emit("conversation_member", {"conversation_id": conv["id"], "member_kind": "user", "member_id": user_id, "removed": True})
        return {"ok": True, "removed": n}

    def update_conversation(self, user: dict, conv: dict, *, title: str | None = None, agent_reply: str | None = None,
                            owner_id: str | None = None) -> dict:
        self.require_conv(user, conv["id"], owner=True)
        fields: dict = {}
        if title is not None and title.strip():
            fields["title"] = title.strip()
        if agent_reply is not None:
            if agent_reply not in AGENT_REPLY:
                raise HTTPException(400, "agent_reply 必须是 auto | always | mention | never")
            fields["agent_reply"] = agent_reply
        if owner_id is not None:
            if not self.db.one("SELECT 1 FROM conversation_members WHERE conversation_id = ? AND member_kind = 'user' AND member_id = ?",
                               [conv["id"], owner_id]):
                raise HTTPException(400, "新 owner 必须已经是成员")
            fields["owner_id"] = owner_id
            self.db.execute("UPDATE conversation_members SET role = 'member' WHERE conversation_id = ? AND member_kind = 'user' AND role = 'owner'", [conv["id"]])
            self.db.execute("UPDATE conversation_members SET role = 'owner' WHERE conversation_id = ? AND member_kind = 'user' AND member_id = ?",
                            [conv["id"], owner_id])
        if fields:
            self.db.update("conversations", conv["id"], updated_at=now(), **fields)
        conv = self.conversation(conv["id"])
        self._emit("conversation", conv, project_id=conv.get("project_id"))
        return conv

    def mark_read(self, user: dict, conv_id: str) -> None:
        self.db.execute("UPDATE conversation_members SET last_read_at = ? WHERE conversation_id = ? AND member_kind = 'user' AND member_id = ?",
                        [now(), conv_id, user["id"]])

    def view(self, conv: dict, user: dict) -> dict:
        members = self.conv_members(conv["id"])
        mine = next((m for m in members if m["member_kind"] == "user" and m["member_id"] == user["id"]), None)
        since = (mine or {}).get("last_read_at") or ""
        if conv["kind"] == "session":
            last = self.db.one("SELECT author, blocks, created_at FROM messages WHERE session_id = ? AND role IN ('user','assistant') "
                               "ORDER BY created_at DESC LIMIT 1", [conv["session_id"]])
            if last:
                last = {"author": last["author"], "created_at": last["created_at"],
                        "text": " ".join(b.get("text", "") for b in (last["blocks"] or []) if b.get("type") == "text")[:200]}
            unread = self.db.one("SELECT COUNT(*) AS n FROM messages WHERE session_id = ? AND created_at > ? AND role IN ('user','assistant')",
                                 [conv["session_id"], since])["n"]
        else:
            last = self.db.one("SELECT author, text, created_at FROM chat_messages WHERE channel_id = ? ORDER BY created_at DESC LIMIT 1", [conv["id"]])
            unread = self.db.one("SELECT COUNT(*) AS n FROM chat_messages WHERE channel_id = ? AND created_at > ? AND (user_id IS NULL OR user_id != ?)",
                                 [conv["id"], since, user["id"]])["n"]
        title = conv["title"]
        if conv["kind"] == "dm":
            other = next((m for m in members if m["member_kind"] == "user" and m["member_id"] != user["id"]), None)
            title = other["name"] if other else title
        return {**conv, "title": title, "members": [{k: m.get(k) for k in ("member_kind", "member_id", "name", "handle", "role")} for m in members],
                "human_count": sum(1 for m in members if m["member_kind"] == "user"), "last": last, "unread": unread}

    def list_conversations(self, user: dict, team_id: str | None = None, kind: str | None = None) -> list[dict]:
        q = ("SELECT c.* FROM conversations c JOIN conversation_members m ON m.conversation_id = c.id "
             "WHERE m.member_kind = 'user' AND m.member_id = ?")
        params: list = [user["id"]]
        if team_id:
            q += " AND (c.team_id = ? OR c.team_id IS NULL)"
            params.append(team_id)
        if kind:
            q += " AND c.kind = ?"
            params.append(kind)
        rows = self.db.all(q + " ORDER BY c.updated_at DESC", params)
        return [self.view(c, user) for c in rows]

    # ---- claiming legacy data ------------------------------------------------
    def claim_legacy(self, user: dict) -> dict:
        """The first registered user becomes admin, owns 默认团队, and every
        row that predates users (projects, sessions, tasks, runs, channels)
        becomes theirs -- so a demo database keeps working after the upgrade."""
        # INSERT OR IGNORE + re-select: tolerant of a racing first registration even outside the transaction
        self.db.execute("INSERT OR IGNORE INTO teams (id, slug, name, owner_id, default_profile_id, default_model, created_at) VALUES (?,?,?,?,NULL,NULL,?)",
                        [new_id("team"), DEFAULT_TEAM["slug"], DEFAULT_TEAM["name"], user["id"], now()])
        team = self.db.one("SELECT * FROM teams WHERE slug = ?", [DEFAULT_TEAM["slug"]])
        self.db.execute("INSERT OR IGNORE INTO team_members (team_id, user_id, role, joined_at) VALUES (?,?,?,?)", [team["id"], user["id"], "owner", now()])
        legacy_default = self.db.one("SELECT * FROM conversations WHERE kind = 'group' AND is_default = 1 AND team_id IS NULL ORDER BY created_at LIMIT 1")
        if legacy_default:
            self.db.update("conversations", legacy_default["id"], team_id=team["id"])
        self._default_group(team, user["id"])
        uid, tid = user["id"], team["id"]
        self.db.execute("UPDATE projects SET team_id = ?, created_by = COALESCE(created_by, ?) WHERE team_id IS NULL", [tid, uid])
        self.db.execute("UPDATE conversations SET owner_id = ?, team_id = COALESCE(team_id, ?) WHERE owner_id IS NULL AND kind != 'dm'", [uid, tid])
        for c in self.db.all("SELECT id FROM conversations WHERE owner_id = ? AND kind != 'dm'", [uid]):
            self._add_member_row(c["id"], uid, "owner", None)
        self.db.execute("UPDATE sessions SET created_by = ? WHERE created_by IS NULL", [uid])
        self.db.execute("UPDATE tasks SET created_by = ? WHERE created_by IS NULL", [uid])
        self.db.execute("UPDATE runs SET created_by = ? WHERE created_by IS NULL", [uid])
        self.db.execute("UPDATE provider_profiles SET shared = 1 WHERE owner_id IS NULL")
        return team

    def claim_authored(self, user: dict) -> None:
        """A later registrant whose handle equals the free-text author of legacy
        rows gets those rows attributed (display stays the snapshot string).
        Authors the machinery itself writes are never a person's."""
        uid, h = user["id"], user["handle"]
        if h.lower() in MACHINE_AUTHORS:
            return
        self.db.execute("UPDATE messages SET user_id = ? WHERE user_id IS NULL AND role = 'user' AND author = ? COLLATE NOCASE "
                        "AND lower(author) NOT IN ('tool', 'room', 'system', 'main-agent', 'claude')", [uid, h])
        self.db.execute("UPDATE chat_messages SET user_id = ? WHERE user_id IS NULL AND author = ? COLLATE NOCASE", [uid, h])
        self.db.execute("UPDATE artifact_feedback SET user_id = ? WHERE user_id IS NULL AND author = ? COLLATE NOCASE", [uid, h])
        self.db.execute("UPDATE decisions SET actor_id = ? WHERE actor_id IS NULL AND actor = ? COLLATE NOCASE", [uid, h])
