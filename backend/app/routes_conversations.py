"""Conversations: DMs and groups (messages in `chat_messages`) and the
conversation face of a work session (messages in `messages`, sent through
the session's single send path). One member table, one visibility rule.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import me
from .db import new_id, now
from .runs import human_prefix
from .teams import AGENT_REPLY, mentioned_names, should_run


class ConversationIn(BaseModel):
    kind: str = "group"
    team_id: str | None = None
    title: str
    member_ids: list[str] = []


class DmIn(BaseModel):
    user_id: str


class ConversationPatch(BaseModel):
    title: str | None = None
    agent_reply: str | None = None
    owner_id: str | None = None


class MemberIn(BaseModel):
    user_id: str | None = None
    member_kind: str = "user"           # "agent" puts a work session's agent into a group
    session_id: str | None = None


class ChatIn(BaseModel):
    text: str
    profile_id: str | None = None
    model: str | None = None


def make_router(svc) -> APIRouter:
    db, bus, teams, notify = svc.db, svc.bus, svc.teams, svc.notify
    r = APIRouter(prefix="/api/conversations")

    def _conv(user: dict, conv_id: str, owner: bool = False) -> dict:
        if user.get("bootstrap"):
            raise HTTPException(403, "引导令牌不是用户")
        return teams.require_conv(user, conv_id, owner=owner)

    @r.get("")
    def list_conversations(team_id: str | None = None, kind: str | None = None, user: dict = Depends(me)):
        if kind and kind not in ("dm", "group", "session"):
            raise HTTPException(400, "kind 必须是 dm | group | session")
        return teams.list_conversations(user, team_id, kind)

    @r.post("", status_code=201)
    def create_conversation(body: ConversationIn, user: dict = Depends(me)):
        if body.kind != "group":
            raise HTTPException(400, "这里只能建群；私聊用 /dm，工作会话来自任务")
        team_id = body.team_id or teams.default_team_for(user)
        if not team_id:
            raise HTTPException(400, "先加入或创建一个团队")
        conv = teams.create_group(user, team_id, body.title, body.member_ids)
        return teams.view(conv, user)

    @r.post("/dm")
    def dm(body: DmIn, user: dict = Depends(me)):
        return teams.view(teams.get_or_create_dm(user, body.user_id), user)

    @r.get("/{conv_id}")
    def get_conversation(conv_id: str, user: dict = Depends(me)):
        conv = _conv(user, conv_id)
        v = teams.view(conv, user)
        if conv["kind"] == "session":
            v["session"] = db.one("SELECT * FROM sessions WHERE id = ?", [conv["session_id"]])
            v["project"] = db.one("SELECT id, name, team_id FROM projects WHERE id = ?", [conv["project_id"]])
        return v

    @r.patch("/{conv_id}")
    def patch_conversation(conv_id: str, body: ConversationPatch, user: dict = Depends(me)):
        conv = _conv(user, conv_id, owner=True)
        if body.agent_reply is not None and body.agent_reply not in AGENT_REPLY:
            raise HTTPException(400, "agent_reply 必须是 auto | always | mention | never")
        return teams.view(teams.update_conversation(user, conv, title=body.title, agent_reply=body.agent_reply, owner_id=body.owner_id), user)

    @r.post("/{conv_id}/members", status_code=201)
    def add_member(conv_id: str, body: MemberIn, user: dict = Depends(me)):
        conv = _conv(user, conv_id)
        if body.member_kind == "agent":
            if not body.session_id:
                raise HTTPException(400, "agent 成员需要 session_id")
            return teams.add_conv_agent(user, conv, body.session_id)
        if body.member_kind != "user" or not body.user_id:
            raise HTTPException(400, "member_kind 必须是 user（带 user_id）或 agent（带 session_id）")
        return teams.add_conv_member(user, conv, body.user_id)

    @r.delete("/{conv_id}/members/{user_id}")
    def remove_member(conv_id: str, user_id: str, user: dict = Depends(me)):
        conv = _conv(user, conv_id)
        return teams.remove_conv_member(user, conv, user_id)

    @r.post("/{conv_id}/read")
    def mark_read(conv_id: str, user: dict = Depends(me)):
        _conv(user, conv_id)
        teams.mark_read(user, conv_id)
        return {"ok": True}

    @r.get("/{conv_id}/messages")
    def messages(conv_id: str, limit: int = 200, user: dict = Depends(me)):
        conv = _conv(user, conv_id)
        if conv["kind"] == "session":
            return db.all("SELECT * FROM messages WHERE session_id = ? ORDER BY created_at, rowid LIMIT ?", [conv["session_id"], limit])
        return db.all("SELECT * FROM chat_messages WHERE channel_id = ? ORDER BY created_at DESC LIMIT ?", [conv_id, limit])[::-1]

    @r.post("/{conv_id}/messages", status_code=201)
    async def post(conv_id: str, body: ChatIn, user: dict = Depends(me)):
        conv = _conv(user, conv_id)
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "empty")
        if conv["kind"] == "session":
            session = db.one("SELECT * FROM sessions WHERE id = ?", [conv["session_id"]])
            return await svc.runs().send_human(session, user, text, profile_id=body.profile_id, model=body.model)
        m = db.insert("chat_messages", {"id": new_id("cm"), "channel_id": conv_id, "author": user["display_name"], "user_id": user["id"],
                                        "text": text, "created_at": now()})
        db.update("conversations", conv_id, updated_at=now())
        bus.emit("chat_message", {**m, "conversation_id": conv_id})
        members = teams.conv_members(conv_id)
        humans = [x for x in members if x["member_kind"] == "user" and x["member_id"] != user["id"]]
        names = mentioned_names(text)
        link = f"?area=chat&c={conv_id}"
        for h in humans:
            if conv["kind"] == "dm":
                notify.send(h["member_id"], "dm", f"{user['display_name']} 给你发了私聊", text[:200], actor_id=user["id"], conversation_id=conv_id, link=link)
            elif (h.get("handle") or "").lower() in names:
                notify.send(h["member_id"], "mention", f"{user['display_name']} 在「{conv['title']}」里 @ 了你", text[:200],
                            actor_id=user["id"], conversation_id=conv_id, link=link)
        # agent members of a group: the same @ rule, delivered into the agent's own session (design §2.1)
        deliveries = []
        for a in [x for x in members if x["member_kind"] == "agent"]:
            session = db.one("SELECT * FROM sessions WHERE id = ?", [a["member_id"]])
            if session and should_run(conv["agent_reply"], len(humans) + 1, text, session):
                d = await svc.runs().route_to_agent(session, text, f"[from 群「{conv['title']}」] " + human_prefix(user) + text,
                                                    author=user["display_name"], user_id=user["id"])
                deliveries.append({"session_id": session["id"], **d})
        return {**m, "conversation_id": conv_id, "deliveries": deliveries}

    return r
