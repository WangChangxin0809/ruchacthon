"""Provider Profiles: which model endpoint a Run talks to, injected as
per-run environment on top of the user's untouched Claude Code config.

Modelled on dsh's settings page: built-in providers need only a key; a
custom provider is an Anthropic-Messages-compatible gateway with an id, a
display name, a base URL, a key and a model list. Keys are write-only: a
profile stores a `credential_ref` (a name in the secret store, or an
environment variable); the UI only ever learns whether it is set.

Only paths Claude Code itself supports are offered -- an OpenAI-style
endpoint is not one of them and fails the compatibility check.
"""
from __future__ import annotations

import re

from . import provider_presets as presets
from .db import Database, new_id, now
from .secrets_store import SecretStore

# kind -> how CC is pointed at it. `credential` is the variable CC reads.
KINDS: dict[str, dict] = {
    "claude_code_default": {"label": "Claude Code 登录", "credential": None, "builtin": True, "needs_base_url": False,
                            "note": "使用服务器上 claude 已有的登录，或下面「Claude Code」页里保存的登录令牌；不注入其他变量"},
    "claude_code_oauth": {"label": "Claude Code 登录令牌 (OAuth)", "credential": "CLAUDE_CODE_OAUTH_TOKEN", "builtin": True, "needs_base_url": False,
                          "note": "用 `claude setup-token` 生成的长期令牌；按运行注入 CLAUDE_CODE_OAUTH_TOKEN"},
    "anthropic_api_key": {"label": "Anthropic API 密钥", "credential": "ANTHROPIC_API_KEY", "builtin": True, "needs_base_url": False,
                          "note": "直连 Anthropic API；按运行注入 ANTHROPIC_API_KEY"},
    "anthropic_compatible_gateway": {"label": "自定义：Anthropic Messages 兼容网关", "credential": "ANTHROPIC_AUTH_TOKEN", "builtin": False, "needs_base_url": True,
                                     "note": "说 Anthropic Messages 协议的网关/代理（LiteLLM、公司网关等），注入 ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN。"
                                             "纯 OpenAI 风格接口不是这一类，兼容性检查会失败。"},
    "bedrock": {"label": "Amazon Bedrock", "credential": None, "builtin": True, "needs_base_url": False,
                "note": "注入 CLAUDE_CODE_USE_BEDROCK=1；AWS 凭据来自服务器环境（AWS_PROFILE / AWS_REGION 写在 extra_env）"},
    "vertex": {"label": "Google Vertex AI", "credential": None, "builtin": True, "needs_base_url": False,
               "note": "注入 CLAUDE_CODE_USE_VERTEX=1；ADC 凭据来自服务器环境（CLOUD_ML_REGION / ANTHROPIC_VERTEX_PROJECT_ID 写在 extra_env）"},
    "foundry": {"label": "Microsoft Foundry", "credential": "ANTHROPIC_FOUNDRY_API_KEY", "builtin": True, "needs_base_url": True,
                "note": "注入 CLAUDE_CODE_USE_FOUNDRY=1 和 ANTHROPIC_FOUNDRY_BASE_URL"},
    "preset": {"label": "预置服务商", "credential": "ANTHROPIC_AUTH_TOKEN", "builtin": False, "needs_base_url": False,
               "note": "从内置的服务商列表里选一个，只需要填自己的密钥。说 OpenAI 协议的服务商会自动经过本机转换代理。"},
}

# api_format values the runtime can actually drive today; the rest are listed
# in the picker but cannot be selected (ADR 0004 §5).
RUNNABLE_FORMATS = ("anthropic", "openai_chat")

DEFAULT_MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"]

def _openai_base(url: str) -> str:
    """cc-switch's presets store the base Claude Code is pointed at, and Claude
    Code appends `/v1/messages` itself. An OpenAI-chat upstream needs the same
    `/v1` in front of `/chat/completions`, so add it when the preset's URL
    stops short of a version segment (NVIDIA NIM: `.../nvidia.com` ->
    `.../nvidia.com/v1`)."""
    url = (url or "").rstrip("/")
    last = url.rsplit("/", 1)[-1]
    if len(last) > 1 and last[0] == "v" and last[1:].isdigit():
        return url
    for suffix in presets.KNOWN_COMPAT_SUFFIXES:
        if url.endswith(suffix):
            return url
    return url + "/v1"


_SAFE_EXTRA = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SECRETISH = re.compile(r"(^|_)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)($|_)", re.I)


class ProviderProfiles:
    def __init__(self, db: Database, secrets: SecretStore):
        self.db = db
        self.secrets = secrets

    # ---- CRUD ----------------------------------------------------------------
    def list(self, user: dict | None = None) -> list[dict]:
        rows = self.db.all("SELECT * FROM provider_profiles ORDER BY created_at")
        return [self.public(p, user) for p in rows if user is None or self.visible_to(p, user)]

    def get(self, pid: str) -> dict | None:
        return self.db.one("SELECT * FROM provider_profiles WHERE id = ?", [pid])

    # ---- ownership (design §5.2) ---------------------------------------------
    def _team_ids(self, user: dict) -> set[str]:
        return {r["team_id"] for r in self.db.all("SELECT team_id FROM team_members WHERE user_id = ?", [user["id"]])}

    def visible_to(self, p: dict, user: dict) -> bool:
        """Mine, shared inside one of my teams, or a legacy server profile (owner NULL)."""
        if user.get("is_admin") or p.get("owner_id") in (None, user["id"]):
            return True
        return bool(p.get("shared")) and (p.get("team_id") in self._team_ids(user) or p.get("team_id") is None)

    def can_edit(self, p: dict, user: dict) -> bool:
        return bool(user.get("is_admin")) or p.get("owner_id") == user["id"]

    def _owner(self, p: dict) -> dict | None:
        return self.db.one("SELECT id, handle, is_admin FROM users WHERE id = ?", [p["owner_id"]]) if p.get("owner_id") else None

    def env_allowed(self, p: dict | None) -> bool:
        """The server-environment fallback for a credential_ref is for admin-owned
        (or legacy) profiles only."""
        if p is None or p.get("owner_id") is None:
            return True
        o = self._owner(p)
        return bool(o and o["is_admin"])

    def ref_allowed(self, p: dict | None) -> bool:
        """A non-admin's profile may only read/write its own `profile.<handle>.*`
        names in the store: store names are predictable (`claude_code.oauth_token`,
        another person's `profile.x.y`), so a client-chosen ref is an oracle and a
        theft path otherwise. Admin-owned and legacy profiles may name anything."""
        if p is None or p.get("owner_id") is None:
            return True
        o = self._owner(p)
        if not o:
            return False
        return bool(o["is_admin"]) or (p.get("credential_ref") or "").startswith(f"profile.{o['handle']}.")

    def _store_ref(self, p: dict) -> str | None:
        return p.get("credential_ref") if self.ref_allowed(p) else None

    def resolve(self, profile_id: str | None, model: str | None, user_id: str | None, project: dict | None) -> tuple[dict | None, str | None]:
        """Explicit profile -> the user's default -> the project's team default -> global -> server login; same chain for the model."""
        user = self.db.one("SELECT * FROM users WHERE id = ?", [user_id]) if user_id else None
        if profile_id:
            p = self.get(profile_id)
            if p and user and not self.visible_to(p, user):
                raise PermissionError("这个 Provider 不属于你，也没有共享给你的团队")
        else:
            p = None
        prefs = (user or {}).get("prefs") or {}
        if isinstance(prefs, str):
            import json
            prefs = json.loads(prefs or "{}")
        team = self.db.one("SELECT * FROM teams WHERE id = ?", [(project or {}).get("team_id")]) if (project or {}).get("team_id") else None
        for cand in (prefs.get("default_profile_id"), (team or {}).get("default_profile_id"), self.db.setting("default_profile_id")):
            if p:
                break
            if cand:
                p = self.get(cand)
                if p and user and not self.visible_to(p, user):   # a default somebody else set is not a licence
                    p = None
        model = model or prefs.get("default_model") or (team or {}).get("default_model") or self.db.setting("default_model") or None
        return p, model

    def create(self, name: str, kind: str, *, display_name: str | None = None, base_url: str | None = None, model: str | None = None,
               models: list[str] | None = None, credential_ref: str | None = None, extra_env: dict | None = None,
               secret: str | None = None, owner: dict | None = None, team_id: str | None = None, shared: bool = False,
               preset: str | None = None, model_map: dict | None = None) -> dict:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {sorted(KINDS)}")
        if not re.match(r"^[a-z0-9][a-z0-9-]{0,63}$", name):
            raise ValueError("Provider ID 必须是小写字母/数字/连字符")
        if self.db.one("SELECT id FROM provider_profiles WHERE name = ?", [name]):
            raise ValueError("这个 Provider ID 已被占用")
        if KINDS[kind]["needs_base_url"] and not base_url:
            raise ValueError("这类提供方需要 API 地址")
        preset_row = self._preset_or_raise(kind, preset)
        model_map = self._check_model_map(model_map)
        extra_env = self._check_extra(extra_env or {})
        models = [m for m in (models or []) if m] or ([model] if model else [])
        model = model or (models[0] if models else None)
        default_ref = f"profile.{owner['handle']}.{name}" if owner else f"profile.{name}"
        if owner and not owner.get("is_admin"):
            credential_ref = None                # only an admin may point a profile at an arbitrary name
        ref = credential_ref or (default_ref if KINDS[kind]["credential"] else None)
        if secret:
            if not KINDS[kind]["credential"]:
                raise ValueError("这类提供方不接受密钥")
            self.secrets.set(ref, secret)
        row = self.db.insert("provider_profiles", {"id": new_id("pp"), "name": name, "display_name": display_name or name, "kind": kind,
                                                   "base_url": base_url or None, "model": model, "models": models,
                                                   "credential_env": KINDS[kind]["credential"], "credential_ref": ref,
                                                   "extra_env": extra_env, "compat": {}, "created_at": now(),
                                                   "preset": preset_row.name if preset_row else None, "model_map": model_map,
                                                   "owner_id": owner["id"] if owner else None, "team_id": team_id, "shared": 1 if shared else 0})
        return self.public(row, owner)

    def update(self, pid: str, *, display_name: str | None = None, base_url: str | None = None, model: str | None = None,
               models: list[str] | None = None, extra_env: dict | None = None, secret: str | None = None,
               shared: bool | None = None, model_map: dict | None = None) -> dict:
        p = self.get(pid)
        if not p:
            raise KeyError(pid)
        fields: dict = {}
        if display_name is not None:
            fields["display_name"] = display_name
        if base_url is not None:
            fields["base_url"] = base_url or None
        if models is not None:
            fields["models"] = [m for m in models if m]
        if model is not None:
            fields["model"] = model or None
        if extra_env is not None:
            fields["extra_env"] = self._check_extra(extra_env)
        if shared is not None:
            fields["shared"] = 1 if shared else 0
        if model_map is not None:
            fields["model_map"] = self._check_model_map(model_map)
        if secret:
            if not p["credential_ref"]:
                raise ValueError("这类提供方不接受密钥")
            if not self.ref_allowed(p):
                raise PermissionError("这个 Provider 引用的密钥名不属于你")
            self.secrets.set(p["credential_ref"], secret)
        if fields:
            self.db.update("provider_profiles", pid, **fields)
        return self.public(self.get(pid))

    def clear_secret(self, pid: str) -> None:
        p = self.get(pid)
        if p and p["credential_ref"]:
            if not self.ref_allowed(p):
                raise PermissionError("这个 Provider 引用的密钥名不属于你")
            self.secrets.delete(p["credential_ref"])

    def delete(self, pid: str) -> None:
        self.clear_secret(pid)
        self.db.execute("DELETE FROM provider_profiles WHERE id = ?", [pid])

    def _check_extra(self, extra_env: dict) -> dict:
        for k, v in extra_env.items():
            if not _SAFE_EXTRA.match(k) or _SECRETISH.search(k) or not isinstance(v, str):
                raise ValueError(f"extra_env 只能放非密钥的大写变量，拒绝 {k!r}；密钥请填在密钥框里")
        return extra_env

    def public(self, p: dict, user: dict | None = None) -> dict:
        d = dict(p)
        ref = self._store_ref(p)
        d["credential_set"] = self.secrets.is_set(ref, self.env_allowed(p))
        d["credential_source"] = ("store" if ref and any(s["name"] == ref for s in self.secrets.names())
                                  else ("env" if d["credential_set"] else None))
        d["shared"] = bool(p.get("shared"))
        d["mine"] = bool(user) and p.get("owner_id") == user["id"]
        d["editable"] = bool(user) and self.can_edit(p, user)
        o = self.db.one("SELECT display_name FROM users WHERE id = ?", [p.get("owner_id")]) if p.get("owner_id") else None
        d["owner_name"] = o["display_name"] if o else None
        d["accepts_secret"] = bool(KINDS.get(p["kind"], {}).get("credential"))
        d["kind_label"] = KINDS.get(p["kind"], {}).get("label", p["kind"])
        d["models"] = p.get("models") or ([p["model"]] if p.get("model") else [])
        d["model_map"] = p.get("model_map") or {}
        pre = self.preset_of(p)
        d["preset"] = p.get("preset")
        d["api_format"] = pre.api_format if pre else "anthropic"
        d["needs_routing"] = self.needs_routing(p)
        d["runnable"] = (pre.api_format in RUNNABLE_FORMATS) if pre else True
        if pre:
            d["kind_label"] = pre.name
            d["website_url"], d["api_key_url"] = pre.website_url, pre.api_key_url
            d["preset_base_url"] = pre.base_url
        return d

    # ---- presets (cc-switch's list as data) ----------------------------------
    def _preset_or_raise(self, kind: str, name: str | None):
        if kind != "preset":
            return None
        pre = presets.by_name(name or "")
        if pre is None:
            raise ValueError("没有这个预置服务商")
        if pre.api_format not in RUNNABLE_FORMATS:
            raise ValueError(f"{pre.name} 用的是 {pre.api_format} 协议，暂不支持")
        if pre.requires_oauth:
            raise ValueError(f"{pre.name} 需要 OAuth 登录，不能只填密钥")
        return pre

    def _check_model_map(self, m: dict | None) -> dict:
        """role/alias -> upstream model id. '*' is the catch-all; values are
        opaque vendor strings, so only their shape is checked."""
        if m is None:
            return {}
        out = {}
        for k, v in m.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError("模型映射必须是字符串到字符串")
            k, v = k.strip(), v.strip()
            if k and v:
                out[k[:64]] = v[:200]
        return out

    def preset_of(self, p: dict | None):
        return presets.by_name(p.get("preset") or "") if p and p.get("kind") == "preset" else None

    def needs_routing(self, p: dict | None) -> bool:
        """True when Claude Code cannot talk to this vendor directly and the
        local conversion proxy has to sit in between."""
        pre = self.preset_of(p)
        return bool(pre and pre.api_format == "openai_chat")

    def upstream_for(self, p: dict, model: str | None = None) -> dict | None:
        """What the proxy needs to forward this profile's traffic: the vendor
        base URL, the key, and the alias -> model map Claude Code's own model
        names are translated through. None when no proxy is involved."""
        pre = self.preset_of(p)
        if not pre or not self.needs_routing(p):
            return None
        key = self.secrets.get(self._store_ref(p), self.env_allowed(p)) or ""
        chosen = model or p.get("model")
        role_map = {k: v for k, v in presets.model_role_map(pre.env).items() if v}
        model_map = {**role_map, **(p.get("model_map") or {})}
        if chosen:
            model_map.setdefault("*", chosen)
        return {"base_url": _openai_base(p.get("base_url") or pre.base_url), "api_key": key,
                "model_map": model_map, "api_format": "openai_chat", "credential_present": bool(key)}

    def alias_env(self, p: dict, model: str | None = None) -> dict[str, str]:
        """The model names Claude Code should ask for. They are aliases the
        proxy maps, so they must be keys of the map, not vendor ids."""
        up = self.upstream_for(p, model) or {}
        m = up.get("model_map") or {}
        chosen = model or p.get("model")
        env = {}
        for var, role in (("ANTHROPIC_DEFAULT_HAIKU_MODEL", "haiku"), ("ANTHROPIC_DEFAULT_SONNET_MODEL", "sonnet"),
                          ("ANTHROPIC_DEFAULT_OPUS_MODEL", "opus"), ("ANTHROPIC_DEFAULT_FABLE_MODEL", "fable")):
            if m.get(role) or m.get("*"):
                env[var] = role if m.get(role) else (chosen or m["*"])
        if chosen:
            env["ANTHROPIC_MODEL"] = chosen
        return env

    # ---- what a run gets ------------------------------------------------------
    def env_for(self, p: dict | None, model: str | None = None) -> tuple[dict[str, str], dict]:
        """(env for the child process, snapshot safe to persist/show)."""
        chosen = model or (p or {}).get("model")
        if p is None or p["kind"] == "claude_code_default":
            env = {"ANTHROPIC_MODEL": chosen} if chosen else {}
            login = self.secrets.get("claude_code.oauth_token")
            if login:                       # the token saved on the Claude Code settings page
                env["CLAUDE_CODE_OAUTH_TOKEN"] = login
            snap = {"profile_id": p["id"] if p else None, "kind": "claude_code_default", "model": chosen,
                    "login": "saved_token" if login else "server_claude_login"}
            return env, snap
        env: dict[str, str] = {}
        kind = p["kind"]
        pre = self.preset_of(p)
        if pre is not None and not self.needs_routing(p):
            # an Anthropic-protocol preset is exactly cc-switch's env block with
            # the key filled in; the proxy is not involved
            for k, v in presets.apply_template_values(pre.env, None).items():
                if k != pre.api_key_field and v:
                    env[k] = v
            if p.get("base_url"):
                env["ANTHROPIC_BASE_URL"] = p["base_url"]
        if kind == "anthropic_compatible_gateway":
            env["ANTHROPIC_BASE_URL"] = p["base_url"] or ""
        if kind == "bedrock":
            env["CLAUDE_CODE_USE_BEDROCK"] = "1"
        if kind == "vertex":
            env["CLAUDE_CODE_USE_VERTEX"] = "1"
        if kind == "foundry":
            env["CLAUDE_CODE_USE_FOUNDRY"] = "1"
            env["ANTHROPIC_FOUNDRY_BASE_URL"] = p["base_url"] or ""
        if chosen:
            env["ANTHROPIC_MODEL"] = chosen
        env.update(p["extra_env"] or {})
        target = pre.api_key_field if pre is not None else KINDS[kind]["credential"]
        cred_missing = False
        if target:
            val = self.secrets.get(self._store_ref(p), self.env_allowed(p))
            if val:
                env[target] = val
                if target == "ANTHROPIC_AUTH_TOKEN":     # a gateway token must not fall through to a stray API key
                    env.setdefault("ANTHROPIC_API_KEY", "")
                if target != "CLAUDE_CODE_OAUTH_TOKEN":  # an API-key path must not be shadowed by the saved login
                    env.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "")
            else:
                cred_missing = True
        snap = {"profile_id": p["id"], "name": p["name"], "kind": kind, "base_url": p["base_url"], "model": chosen,
                "preset": p.get("preset"), "routed": self.needs_routing(p),
                "credential_ref": p.get("credential_ref"), "credential_present": not cred_missing,
                "env_keys": sorted(k for k in env if not _SECRETISH.search(k))}
        return env, snap

    def record_compat(self, pid: str, result: dict) -> None:
        self.db.update("provider_profiles", pid, compat={**result, "checked_at": now()})


def scrub(text: str | None, env: dict[str, str]) -> str | None:
    """Replace any secret value that might have leaked into an error string."""
    if not text:
        return text
    for k, v in env.items():
        if v and len(v) >= 8 and _SECRETISH.search(k):
            text = text.replace(v, f"<{k}>")
    return text
