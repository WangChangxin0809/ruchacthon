"""Who is asking. Passwords are pbkdf2-sha256 (stdlib, 310k rounds); bearer
tokens are `wbs_` + 32 random bytes and the database keeps only their sha256,
so a copied database cannot log anybody in. Sessions slide: 30 days from the
last request.

Two special principals: the deploy-time WORKBENCH_TOKEN is a *bootstrap*
principal that can register the first user and reach /api/admin/* and nothing
else -- it cannot post as a person or create accounts once one exists, but
/api/admin/* includes password reset, so whoever holds it can become anybody:
treat it as root and keep it out of browsers. WORKBENCH_SINGLE_USER=1 makes
every request the local admin user `local` so the laptop quick start needs no
password; the first real registration on such a data dir takes that account
over, so a laptop database can be served to a team later.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from .db import Database, new_id, now

PBKDF2_ROUNDS = 310_000
SESSION_DAYS = 30
HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")
MIN_PASSWORD = 8
# names the machinery writes as `author` or answers to as a principal; a person
# with one of these would claim the tool's legacy rows or become `local`
RESERVED_HANDLES = {"local", "bootstrap", "system", "tool", "room", "claude", "agent", "ai", "human", "main-agent", "admin"}
LOCAL_HANDLE = "local"
BOOTSTRAP_PATHS = ("/api/auth", "/api/auth/register", "/api/admin/")
BOOTSTRAP = {"id": "bootstrap", "handle": "bootstrap", "display_name": "管理员(引导令牌)", "is_admin": True, "bootstrap": True, "prefs": {}}


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str | None) -> bool:
    try:
        algo, rounds, salt, h = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(got.hex(), h)
    except (ValueError, TypeError):
        return False


def new_token() -> tuple[str, str]:
    """(plaintext handed to the client once, sha256 hex kept in the database)."""
    tok = "wbs_" + secrets.token_urlsafe(32)
    return tok, token_hash(tok)


def token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="milliseconds")


def public_user(u: dict) -> dict:
    return {"id": u["id"], "handle": u["handle"], "display_name": u["display_name"], "is_admin": bool(u.get("is_admin")),
            "prefs": u.get("prefs") or {}, "created_at": u.get("created_at"), "bootstrap": bool(u.get("bootstrap"))}


# ?token= exists for the places a browser cannot send a header: the WebSocket,
# <img src>/<a href> of an artifact file, and the <iframe src>/<img src> of a
# previewed workspace file. Nowhere else.
QUERY_TOKEN_PATHS = re.compile(r"^(/ws|/api/artifacts/[^/]+/file|/api/sessions/[^/]+/preview/file)$")


def bearer_of(auth_header: str | None, query_token: str | None, path: str | None = None) -> str | None:
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:].strip() or None
    if query_token and (path is None or QUERY_TOKEN_PATHS.match(path)):
        return query_token
    return None


class Auth:
    def __init__(self, db: Database, teams=None):
        self.db = db
        self.teams = teams                       # Teams: the first user claims legacy data through it
        self.bootstrap_token = os.environ.get("WORKBENCH_TOKEN", "")
        self.single_user = os.environ.get("WORKBENCH_SINGLE_USER", "") in ("1", "true", "yes")
        if self.single_user:
            self.ensure_local_user()

    # ---- users ------------------------------------------------------------
    def user(self, user_id: str) -> dict | None:
        return self.db.one("SELECT * FROM users WHERE id = ?", [user_id])

    def by_handle(self, handle: str) -> dict | None:
        return self.db.one("SELECT * FROM users WHERE handle = ? COLLATE NOCASE", [handle.strip()])

    def user_count(self) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM users")["n"]

    def _seed_only(self) -> dict | None:
        """The `local` account single-user mode made, when it is the only user:
        a laptop database being served to a team for the first time."""
        if self.single_user or self.user_count() != 1:
            return None
        return self.by_handle(LOCAL_HANDLE)

    def needs_first_user(self) -> bool:
        return self.user_count() == 0 or self._seed_only() is not None

    def register(self, handle: str, display_name: str, password: str, *, is_admin: bool = False,
                 only_if_first: bool = False, _seed: bool = False) -> dict:
        """`only_if_first` makes the "nobody exists yet" check part of the same
        transaction as the insert, so two racing first registrations cannot
        both pass the route's open-registration check."""
        handle = (handle or "").strip().lower()
        if not HANDLE_RE.match(handle):
            raise ValueError("用户名只能是 2-32 位小写字母、数字、_ . -，且以字母或数字开头")
        if handle in RESERVED_HANDLES and not _seed:
            raise ValueError("这个用户名是保留的")
        if len(password or "") < MIN_PASSWORD:
            raise ValueError(f"密码至少 {MIN_PASSWORD} 位")
        pw_hash = hash_password(password)              # 0.1s of pbkdf2: outside the lock
        display = (display_name or handle).strip()[:64] or handle
        with self.db.transaction():
            if self.by_handle(handle):
                raise ValueError("这个用户名已被占用")
            first = self.needs_first_user()
            if only_if_first and not first:
                raise PermissionError("注册需要邀请链接；请向团队 owner 要一个")
            seed = self._seed_only() if first else None
            if seed:
                # take the laptop-era account over: every ownership row already points at its id
                self.db.update("users", seed["id"], handle=handle, display_name=display, password_hash=pw_hash, is_admin=1,
                               prefs={}, created_at=now())
                u = self.user(seed["id"])
            else:
                u = self.db.insert("users", {"id": new_id("usr"), "handle": handle, "display_name": display, "email": None,
                                             "password_hash": pw_hash, "is_admin": 1 if (is_admin or first) else 0,
                                             "prefs": {}, "created_at": now(), "last_seen_at": None})
            if self.teams is not None:
                if first:
                    self.teams.claim_legacy(u)
                self.teams.claim_authored(u)
        return u

    def ensure_local_user(self) -> dict:
        u = self.by_handle(LOCAL_HANDLE)
        if u:
            return u
        # the password is unusable on purpose: single-user mode never asks for one
        return self.register(LOCAL_HANDLE, os.environ.get("WORKBENCH_LOCAL_NAME") or "我", secrets.token_urlsafe(24), is_admin=True, _seed=True)

    def set_password(self, user_id: str, password: str) -> None:
        if len(password or "") < MIN_PASSWORD:
            raise ValueError(f"密码至少 {MIN_PASSWORD} 位")
        self.db.update("users", user_id, password_hash=hash_password(password))
        self.db.execute("DELETE FROM auth_sessions WHERE user_id = ?", [user_id])   # a new password logs every device out

    # ---- sessions ---------------------------------------------------------
    def login(self, handle: str, password: str, user_agent: str | None = None) -> tuple[dict, str, str]:
        u = self.by_handle(handle or "")
        if not u or not verify_password(password or "", u["password_hash"]):
            raise PermissionError("用户名或密码不对")
        tok, h = new_token()
        exp = _expiry()
        self.db.insert("auth_sessions", {"id": new_id("as"), "user_id": u["id"], "token_hash": h, "user_agent": (user_agent or "")[:200],
                                         "created_at": now(), "expires_at": exp, "last_used_at": now()})
        self.db.update("users", u["id"], last_seen_at=now())
        return u, tok, exp

    def logout(self, tok_hash: str) -> None:
        self.db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", [tok_hash])

    def principal(self, bearer: str | None) -> dict | None:
        """The user behind a bearer, or the bootstrap / single-user principal.
        A malformed credential is no credential, never an exception."""
        try:
            return self._principal(bearer)
        except (TypeError, ValueError, UnicodeError):
            return None

    def _principal(self, bearer: str | None) -> dict | None:
        if bearer and bearer.startswith("wbs_"):
            h = token_hash(bearer)
            s = self.db.one("SELECT * FROM auth_sessions WHERE token_hash = ?", [h])
            if s and s["expires_at"] > now():
                u = self.user(s["user_id"])
                if u:
                    last = s.get("last_used_at") or ""
                    if last < (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="milliseconds"):
                        self.db.update("auth_sessions", s["id"], last_used_at=now(), expires_at=_expiry())
                        self.db.update("users", u["id"], last_seen_at=now())
                    return {**u, "token_hash": h}
            return None
        if self.bootstrap_token and bearer and hmac.compare_digest(bearer.encode("utf-8", "surrogateescape"), self.bootstrap_token.encode()):
            return dict(BOOTSTRAP)
        if self.single_user:
            return dict(self.ensure_local_user())
        return None

    @staticmethod
    def bootstrap_allowed(path: str) -> bool:
        return path in BOOTSTRAP_PATHS[:2] or path.startswith(BOOTSTRAP_PATHS[2])


# ---- route dependencies ---------------------------------------------------
def me(request: Request) -> dict:
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "login required")
    return u


def admin(request: Request) -> dict:
    u = me(request)
    if not u.get("is_admin"):
        raise HTTPException(403, "需要管理员权限")
    return u
