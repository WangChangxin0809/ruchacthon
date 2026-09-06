"""Notifications: one row per person per thing worth their attention, and a
private `notification` event so the bell updates live. Kinds (design §6):
mention, dm, member_added, invite_accepted, decision_pending, artifact,
run_finished, feedback, plus `question` for an agent's ask_human. Producers
call `send()` at the spot where the underlying event is already emitted;
there is no second scan.

`link` is stored as an object `{project_id?, session_id?, conversation_id?,
decision_id?}` (batch 3/4 contract §D); producers may still pass the old
`?p=..&s=..` / `?area=chat&c=..` string and it is parsed, and rows written
before this change are parsed on the way out.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from .db import Database, new_id, now
from .events import EventBus

KINDS = {"mention", "dm", "member_added", "join_request", "invite_accepted", "decision_pending", "artifact", "run_finished", "feedback", "question"}
LINK_KEYS = ("project_id", "session_id", "conversation_id", "decision_id")
_QUERY_KEYS = {"p": "project_id", "s": "session_id", "c": "conversation_id", "d": "decision_id"}


def parse_link(link) -> dict:
    if isinstance(link, dict):
        return {k: link[k] for k in LINK_KEYS if link.get(k)}
    if isinstance(link, str) and link.startswith("?"):
        q = parse_qs(link[1:])
        return {v: q[k][0] for k, v in _QUERY_KEYS.items() if q.get(k)}
    return {}


def href_of(link: dict) -> str:
    """The URL form the frontend has always deep-linked with."""
    if link.get("session_id") or link.get("project_id"):
        out = f"?p={link.get('project_id') or ''}"
        return out + (f"&s={link['session_id']}" if link.get("session_id") else "") + (f"&d={link['decision_id']}" if link.get("decision_id") else "")
    if link.get("conversation_id"):
        return f"?area=chat&c={link['conversation_id']}"
    return ""


class Notifier:
    def __init__(self, db: Database, bus: EventBus):
        self.db, self.bus = db, bus

    def send(self, user_id: str, kind: str, title: str, body: str = "", *, link=None, conversation_id: str | None = None,
             project_id: str | None = None, actor_id: str | None = None, session_id: str | None = None,
             decision_id: str | None = None) -> dict | None:
        if kind not in KINDS:
            raise ValueError(f"unknown notification kind {kind}")
        if not user_id or user_id == actor_id:      # nobody needs to be told about their own action
            return None
        link_obj = {**parse_link(link), **{k: v for k, v in (("project_id", project_id), ("session_id", session_id), ("conversation_id", conversation_id),
                                                              ("decision_id", decision_id)) if v}}
        row = self.db.insert("notifications", {"id": new_id("ntf"), "user_id": user_id, "kind": kind, "title": title[:200],
                                               "body": (body or "")[:2000], "link": link_obj, "conversation_id": conversation_id,
                                               "project_id": project_id, "actor_id": actor_id, "read_at": None, "created_at": now()})
        row = self.public(row)
        self.bus.emit("notification", row, project_id=project_id, user_id=user_id)
        return row

    def send_many(self, user_ids, kind: str, title: str, body: str = "", **kw) -> list[dict]:
        return [r for uid in dict.fromkeys(user_ids) if (r := self.send(uid, kind, title, body, **kw))]

    def public(self, row: dict) -> dict:
        link = parse_link(row.get("link"))
        return {**row, "link": link, "href": href_of(link), "read": row.get("read_at") is not None}

    def list(self, user_id: str, unread_only: bool = False, limit: int = 50) -> list[dict]:
        q = "SELECT * FROM notifications WHERE user_id = ?" + (" AND read_at IS NULL" if unread_only else "")
        return [self.public(r) for r in self.db.all(q + " ORDER BY created_at DESC LIMIT ?", [user_id, limit])]

    def unread_count(self, user_id: str) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND read_at IS NULL", [user_id])["n"]

    def mark_read(self, user_id: str, ids: list[str] | None = None, all_: bool = False) -> int:
        t = now()
        if all_:
            return self.db.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL", [t, user_id]).rowcount
        n = 0
        for i in ids or []:
            n += self.db.execute("UPDATE notifications SET read_at = ? WHERE id = ? AND user_id = ? AND read_at IS NULL", [t, i, user_id]).rowcount
        return n
