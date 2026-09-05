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
}

DEFAULT_MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"]

_SAFE_EXTRA = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SECRETISH = re.compile(r"(^|_)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)($|_)", re.I)


class ProviderProfiles:
    def __init__(self, db: Database, secrets: SecretStore):
        self.db = db
        self.secrets = secrets

    # ---- CRUD ----------------------------------------------------------------
    def list(self) -> list[dict]:
        return [self.public(p) for p in self.db.all("SELECT * FROM provider_profiles ORDER BY created_at")]

    def get(self, pid: str) -> dict | None:
        return self.db.one("SELECT * FROM provider_profiles WHERE id = ?", [pid])

    def create(self, name: str, kind: str, *, display_name: str | None = None, base_url: str | None = None, model: str | None = None,
               models: list[str] | None = None, credential_ref: str | None = None, extra_env: dict | None = None,
               secret: str | None = None) -> dict:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {sorted(KINDS)}")
        if not re.match(r"^[a-z0-9][a-z0-9-]{0,63}$", name):
            raise ValueError("Provider ID 必须是小写字母/数字/连字符")
        if KINDS[kind]["needs_base_url"] and not base_url:
            raise ValueError("这类提供方需要 API 地址")
        extra_env = self._check_extra(extra_env or {})
        models = [m for m in (models or []) if m] or ([model] if model else [])
        model = model or (models[0] if models else None)
        ref = credential_ref or (f"profile.{name}" if KINDS[kind]["credential"] else None)
        if secret:
            if not KINDS[kind]["credential"]:
                raise ValueError("这类提供方不接受密钥")
            self.secrets.set(ref, secret)
        row = self.db.insert("provider_profiles", {"id": new_id("pp"), "name": name, "display_name": display_name or name, "kind": kind,
                                                   "base_url": base_url or None, "model": model, "models": models,
                                                   "credential_env": KINDS[kind]["credential"], "credential_ref": ref,
                                                   "extra_env": extra_env, "compat": {}, "created_at": now()})
        return self.public(row)

    def update(self, pid: str, *, display_name: str | None = None, base_url: str | None = None, model: str | None = None,
               models: list[str] | None = None, extra_env: dict | None = None, secret: str | None = None) -> dict:
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
        if secret:
            if not p["credential_ref"]:
                raise ValueError("这类提供方不接受密钥")
            self.secrets.set(p["credential_ref"], secret)
        if fields:
            self.db.update("provider_profiles", pid, **fields)
        return self.public(self.get(pid))

    def clear_secret(self, pid: str) -> None:
        p = self.get(pid)
        if p and p["credential_ref"]:
            self.secrets.delete(p["credential_ref"])

    def delete(self, pid: str) -> None:
        self.clear_secret(pid)
        self.db.execute("DELETE FROM provider_profiles WHERE id = ?", [pid])

    def _check_extra(self, extra_env: dict) -> dict:
        for k, v in extra_env.items():
            if not _SAFE_EXTRA.match(k) or _SECRETISH.search(k) or not isinstance(v, str):
                raise ValueError(f"extra_env 只能放非密钥的大写变量，拒绝 {k!r}；密钥请填在密钥框里")
        return extra_env

    def public(self, p: dict) -> dict:
        d = dict(p)
        d["credential_set"] = self.secrets.is_set(p.get("credential_ref"))
        d["credential_source"] = ("store" if any(s["name"] == p.get("credential_ref") for s in self.secrets.names())
                                  else ("env" if d["credential_set"] else None))
        d["accepts_secret"] = bool(KINDS.get(p["kind"], {}).get("credential"))
        d["kind_label"] = KINDS.get(p["kind"], {}).get("label", p["kind"])
        d["models"] = p.get("models") or ([p["model"]] if p.get("model") else [])
        return d

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
        target = KINDS[kind]["credential"]
        cred_missing = False
        if target:
            val = self.secrets.get(p.get("credential_ref"))
            if val:
                env[target] = val
                if target == "ANTHROPIC_AUTH_TOKEN":     # a gateway token must not fall through to a stray API key
                    env.setdefault("ANTHROPIC_API_KEY", "")
                if target != "CLAUDE_CODE_OAUTH_TOKEN":  # an API-key path must not be shadowed by the saved login
                    env.setdefault("CLAUDE_CODE_OAUTH_TOKEN", "")
            else:
                cred_missing = True
        snap = {"profile_id": p["id"], "name": p["name"], "kind": kind, "base_url": p["base_url"], "model": chosen,
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
