"""Auth, users, admin, teams, invites and notifications routes. Built by
`make_router(svc)` so the module needs nothing from main.py at import time.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .auth import admin, me, public_user

PREF_KEYS = {"default_profile_id", "default_model", "current_team_id"}


class RegisterIn(BaseModel):
    handle: str
    display_name: str = ""
    password: str
    invite: str | None = None
    is_admin: bool = False          # honoured only when a logged-in admin registers someone


class LoginIn(BaseModel):
    handle: str
    password: str


class PasswordChange(BaseModel):
    old: str
    new: str


class MeIn(BaseModel):
    display_name: str | None = None
    prefs: dict | None = None
    password: PasswordChange | None = None


class TeamIn(BaseModel):
    name: str
    slug: str | None = None


class TeamPatch(BaseModel):
    name: str | None = None
    default_profile_id: str | None = None
    default_model: str | None = None


class TeamMemberIn(BaseModel):
    user_id: str | None = None
    handle: str | None = None
    role: str = "member"


class RoleIn(BaseModel):
    role: str


class InviteIn(BaseModel):
    role: str = "member"
    expires_in_hours: int = 168
    max_uses: int | None = None


class AdminUserPatch(BaseModel):
    is_admin: bool | None = None
    display_name: str | None = None


class ResetPasswordIn(BaseModel):
    password: str


class ReadIn(BaseModel):
    ids: list[str] = []
    all: bool = False


def _origin(request: Request) -> str:
    o = request.headers.get("origin")
    if o:
        return o.rstrip("/")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost"
    return f"{request.headers.get('x-forwarded-proto') or request.url.scheme}://{host}"


def make_router(svc) -> APIRouter:
    db, auth, teams, notify = svc.db, svc.auth, svc.teams, svc.notify
    r = APIRouter(prefix="/api")

    def _require_profile_visible(profile_id: str, user: dict) -> None:
        p = svc.profiles.get(profile_id)
        if not p or not svc.profiles.visible_to(p, user):
            raise HTTPException(403, "这个 Provider 不属于你，也没有共享给你的团队")

    def _me_view(u: dict) -> dict:
        full = auth.user(u["id"]) or u
        return {**public_user(full), "teams": [{"id": t["id"], "slug": t["slug"], "name": t["name"], "role": t["role"], "member_count": t["member_count"]}
                                               for t in teams.my_teams(u)],
                "unread_notifications": notify.unread_count(u["id"]) if not u.get("bootstrap") else 0}

    # ---- auth ---------------------------------------------------------------
    @r.get("/auth")
    def auth_probe(request: Request):
        """Public: whether a login is needed and whether the caller's token works (also the deploy health check)."""
        u = getattr(request.state, "user", None)
        first = not auth.single_user and auth.needs_first_user()
        gated = first and bool(auth.bootstrap_token) and not (u or {}).get("bootstrap")
        return {"required": not auth.single_user, "mode": "users", "registration_open": first and not gated,
                "needs_deploy_token": gated, "single_user": auth.single_user,
                "ok": u is not None, "me": public_user(u) if u else None}

    @r.post("/auth/register", status_code=201)
    def register(body: RegisterIn, request: Request):
        caller = getattr(request.state, "user", None)
        first = auth.needs_first_user()
        # the deploy token may open the door once; only a person who is admin creates accounts after that
        by_admin = bool(caller and caller.get("is_admin") and not caller.get("bootstrap"))
        invite = None
        if body.invite:
            invite, ok, why = teams.invite_status(body.invite)
            if not ok:
                raise HTTPException(410 if invite else 404, why)
        # A deployment that configured WORKBENCH_TOKEN keeps the very first
        # registration behind it (docs/decisions/0005): otherwise, between the
        # deploy and the owner getting to a browser, whoever reaches the box
        # first becomes its administrator. With no token set (the laptop quick
        # start) the first registration stays open, because nothing else could
        # open it.
        if first and auth.bootstrap_token and not (caller or {}).get("bootstrap") and not invite:
            raise HTTPException(403, "第一个账号需要部署令牌：打开 http://<地址>/?token=<WORKBENCH_TOKEN>（在服务器的 /etc/workbench.env 里）再注册")
        if not (first or invite or by_admin):
            raise HTTPException(403, "注册需要邀请链接；请向团队 owner 要一个")
        try:
            u = auth.register(body.handle, body.display_name, body.password, is_admin=body.is_admin and by_admin,
                              only_if_first=not (invite or by_admin))
        except ValueError as e:
            raise HTTPException(400, str(e))
        except PermissionError as e:
            raise HTTPException(403, str(e))
        if invite:
            teams.accept_invite(body.invite, u)
        _, tok, exp = auth.login(u["handle"], body.password, request.headers.get("user-agent"))
        return {"user": public_user(u), "token": tok, "expires_at": exp, "first": first}

    @r.post("/auth/login")
    def login(body: LoginIn, request: Request):
        try:
            u, tok, exp = auth.login(body.handle, body.password, request.headers.get("user-agent"))
        except PermissionError as e:
            raise HTTPException(401, str(e))
        return {"user": public_user(u), "token": tok, "expires_at": exp}

    @r.post("/auth/logout")
    def logout(user: dict = Depends(me)):
        if user.get("token_hash"):
            auth.logout(user["token_hash"])
        return {"ok": True}

    @r.get("/auth/me")
    def get_me(user: dict = Depends(me)):
        return _me_view(user)

    @r.patch("/auth/me")
    def patch_me(body: MeIn, user: dict = Depends(me)):
        if user.get("bootstrap"):
            raise HTTPException(403, "引导令牌不是用户")
        fields: dict = {}
        if body.display_name is not None and body.display_name.strip():
            fields["display_name"] = body.display_name.strip()[:64]
        if body.prefs is not None:
            cur = (auth.user(user["id"]) or {}).get("prefs") or {}
            for k, v in body.prefs.items():
                if k not in PREF_KEYS:
                    raise HTTPException(400, f"未知偏好 {k}")
                if k == "current_team_id" and v and not teams.role(user, v):
                    raise HTTPException(400, "你不在那个团队里")
                if k == "default_profile_id" and v:
                    _require_profile_visible(v, user)
                cur[k] = v
            fields["prefs"] = cur
        if body.password is not None:
            full = auth.user(user["id"])
            try:
                auth.login(full["handle"], body.password.old)
            except PermissionError:
                raise HTTPException(400, "旧密码不对")
            try:
                auth.set_password(user["id"], body.password.new)
            except ValueError as e:
                raise HTTPException(400, str(e))
        if fields:
            db.update("users", user["id"], **fields)
        return _me_view(user)

    @r.get("/users")
    def list_users(team_id: str | None = None, q: str = "", user: dict = Depends(me)):
        if team_id:
            teams.require_team(user, team_id)
        return teams.users_visible_to(user, team_id, q)

    # ---- admin --------------------------------------------------------------
    @r.get("/admin/users")
    def admin_users(user: dict = Depends(admin)):
        return [public_user(u) | {"last_seen_at": u["last_seen_at"]} for u in db.all("SELECT * FROM users ORDER BY created_at")]

    @r.patch("/admin/users/{user_id}")
    def admin_patch_user(user_id: str, body: AdminUserPatch, user: dict = Depends(admin)):
        target = auth.user(user_id)
        if not target:
            raise HTTPException(404, "没有这个用户")
        fields: dict = {}
        if body.is_admin is not None:
            if not body.is_admin and db.one("SELECT COUNT(*) AS n FROM users WHERE is_admin = 1")["n"] <= 1 and target["is_admin"]:
                raise HTTPException(409, "至少要留一个管理员")
            fields["is_admin"] = 1 if body.is_admin else 0
        if body.display_name:
            fields["display_name"] = body.display_name.strip()[:64]
        if fields:
            db.update("users", user_id, **fields)
        return public_user(auth.user(user_id))

    @r.post("/admin/users/{user_id}/reset-password")
    def admin_reset_password(user_id: str, body: ResetPasswordIn, user: dict = Depends(admin)):
        if not auth.user(user_id):
            raise HTTPException(404, "没有这个用户")
        try:
            auth.set_password(user_id, body.password)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    # ---- teams --------------------------------------------------------------
    @r.get("/teams")
    def my_teams(user: dict = Depends(me)):
        return [{"id": t["id"], "slug": t["slug"], "name": t["name"], "role": t["role"], "member_count": t["member_count"],
                 "owner_id": t["owner_id"], "default_profile_id": t["default_profile_id"], "default_model": t["default_model"]}
                for t in teams.my_teams(user)]

    @r.post("/teams", status_code=201)
    def create_team(body: TeamIn, user: dict = Depends(me)):
        if user.get("bootstrap"):
            raise HTTPException(403, "引导令牌不是用户")
        return teams.create_team(user, body.name, body.slug)

    @r.get("/teams/{team_id}")
    def get_team(team_id: str, user: dict = Depends(me)):
        t = teams.require_team(user, team_id)
        return {**t, "role": teams.role(user, team_id), "members": teams.members(team_id)}

    @r.patch("/teams/{team_id}")
    def patch_team(team_id: str, body: TeamPatch, user: dict = Depends(me)):
        teams.require_team(user, team_id, "admin")
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        if "name" in fields and not fields["name"].strip():
            raise HTTPException(400, "团队名不能为空")
        if fields.get("default_profile_id"):
            _require_profile_visible(fields["default_profile_id"], user)
        if fields:
            db.update("teams", team_id, **fields)
        return teams.team(team_id)

    @r.delete("/teams/{team_id}")
    def delete_team(team_id: str, user: dict = Depends(me)):
        t = teams.require_team(user, team_id, "owner")
        teams.delete_team(t)
        return {"ok": True}

    @r.post("/teams/{team_id}/members", status_code=201)
    def add_team_member(team_id: str, body: TeamMemberIn, user: dict = Depends(me)):
        t = teams.require_team(user, team_id, "admin")
        uid = body.user_id
        if not uid and body.handle:
            u = auth.by_handle(body.handle)
            uid = u["id"] if u else None
        if not uid:
            raise HTTPException(404, "没有这个用户")
        if body.role == "owner" and teams.role(user, team_id) != "owner" and not user.get("is_admin"):
            raise HTTPException(403, "只有 owner 能任命 owner")
        return teams.add_member(t, uid, body.role, added_by=user["id"])

    @r.patch("/teams/{team_id}/members/{user_id}")
    def set_team_role(team_id: str, user_id: str, body: RoleIn, user: dict = Depends(me)):
        t = teams.require_team(user, team_id, "owner")
        teams.set_role(t, user_id, body.role)
        return {"ok": True}

    @r.delete("/teams/{team_id}/members/{user_id}")
    def remove_team_member(team_id: str, user_id: str, user: dict = Depends(me)):
        t = teams.require_team(user, team_id, "member" if user_id == user["id"] else "admin")
        teams.remove_member(t, user_id)
        return {"ok": True}

    # ---- invites ------------------------------------------------------------
    @r.post("/teams/{team_id}/invites", status_code=201)
    def create_invite(team_id: str, body: InviteIn, request: Request, user: dict = Depends(me)):
        t = teams.require_team(user, team_id, "admin")
        inv = teams.create_invite(t, user["id"], body.role, body.expires_in_hours, body.max_uses)
        return {"invite": inv, "url": f"{_origin(request)}/?invite={inv['token']}"}

    @r.get("/teams/{team_id}/invites")
    def list_invites(team_id: str, request: Request, user: dict = Depends(me)):
        teams.require_team(user, team_id, "admin")
        return [{**i, "url": f"{_origin(request)}/?invite={i['token']}", "valid": teams.invite_status(i["token"])[1]} for i in teams.list_invites(team_id)]

    @r.delete("/teams/{team_id}/invites/{invite_id}")
    def revoke_invite(team_id: str, invite_id: str, user: dict = Depends(me)):
        teams.require_team(user, team_id, "admin")
        teams.revoke_invite(team_id, invite_id)
        return {"ok": True}

    @r.get("/invites/{token}")
    def invite_info(token: str):
        """Public: what the landing page shows before the person has an account."""
        inv, ok, why = teams.invite_status(token)
        if not inv:
            raise HTTPException(404, why)
        t = teams.team(inv["team_id"])
        return {"team": {"id": t["id"], "name": t["name"]}, "invited_by": teams.name_of(inv["created_by"]), "role": inv["role"],
                "valid": ok, "reason": why}

    @r.post("/invites/{token}/accept")
    def accept_invite(token: str, user: dict = Depends(me)):
        if user.get("bootstrap"):
            raise HTTPException(403, "引导令牌不是用户")
        t = teams.accept_invite(token, user)
        return {"ok": True, "team": {"id": t["id"], "name": t["name"], "slug": t["slug"]}}

    # ---- notifications ------------------------------------------------------
    @r.get("/notifications")
    def list_notifications(unread: int = 0, limit: int = 50, user: dict = Depends(me)):
        return {"items": notify.list(user["id"], bool(unread), min(int(limit), 200)), "unread": notify.unread_count(user["id"])}

    @r.post("/notifications/read")
    def read_notifications(body: ReadIn, user: dict = Depends(me)):
        n = notify.mark_read(user["id"], body.ids, body.all)
        return {"ok": True, "marked": n, "unread": notify.unread_count(user["id"])}

    return r
