"""Notifications: one row per person per thing worth their attention, and a
private `notification` event so the bell updates live. Kinds (design §6):
mention, dm, member_added, invite_accepted, decision_pending, artifact,
run_finished, feedback. Producers call `send()` at the spot where the
underlying event is already emitted; there is no second scan.
"""
from __future__ import annotations

from .db import Database, new_id, now
from .events import EventBus

KINDS = {"mention", "dm", "member_added", "invite_accepted", "decision_pending", "artifact", "run_finished", "feedback"}


class Notifier:
    def __init__(self, db: Database, bus: EventBus):
        self.db, self.bus = db, bus

    def send(self, user_id: str, kind: str, title: str, body: str = "", *, link: str | None = None,
             conversation_id: str | None = None, project_id: str | None = None, actor_id: str | None = None) -> dict | None:
        if kind not in KINDS:
            raise ValueError(f"unknown notification kind {kind}")
        if not user_id or user_id == actor_id:      # nobody needs to be told about their own action
            return None
        row = self.db.insert("notifications", {"id": new_id("ntf"), "user_id": user_id, "kind": kind, "title": title[:200],
                                               "body": (body or "")[:2000], "link": link, "conversation_id": conversation_id,
                                               "project_id": project_id, "actor_id": actor_id, "read_at": None, "created_at": now()})
        self.bus.emit("notification", row, project_id=project_id, user_id=user_id)
        return row

    def send_many(self, user_ids, kind: str, title: str, body: str = "", **kw) -> list[dict]:
        return [r for uid in dict.fromkeys(user_ids) if (r := self.send(uid, kind, title, body, **kw))]

    def list(self, user_id: str, unread_only: bool = False, limit: int = 50) -> list[dict]:
        q = "SELECT * FROM notifications WHERE user_id = ?" + (" AND read_at IS NULL" if unread_only else "")
        return self.db.all(q + " ORDER BY created_at DESC LIMIT ?", [user_id, limit])

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
