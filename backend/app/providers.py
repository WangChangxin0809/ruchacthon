"""Provider Profiles: which model endpoint a Run talks to, injected as
per-run environment on top of the user's untouched Claude Code config.

Only paths Claude Code itself supports are offered. A profile never stores a
secret: `credential_env` is the *name* of an environment variable the server
process must have; the value is read at run start and handed to the child
process only. API responses report whether it is set, never what it is.
"""
from __future__ import annotations

import os
import re

from .db import Database, new_id, now

# kind -> (env vars the profile sets, the credential var CC reads, docs note)
KINDS: dict[str, dict] = {
    "claude_code_default": {"label": "Claude Code login (no override)", "credential": None,
                            "note": "uses whatever `claude` is already logged in with; nothing injected"},
    "anthropic_api_key": {"label": "Anthropic API key", "credential": "ANTHROPIC_API_KEY",
                          "note": "direct Anthropic API; CC picks the key up from ANTHROPIC_API_KEY"},
    "anthropic_compatible_gateway": {"label": "Anthropic-Messages-compatible gateway (ANTHROPIC_BASE_URL)", "credential": "ANTHROPIC_AUTH_TOKEN",
                                     "note": "a proxy/gateway that speaks the Anthropic Messages API (LiteLLM, corporate gateways). "
                                             "Plain OpenAI-style endpoints are NOT this and will fail the compatibility check."},
    "bedrock": {"label": "Amazon Bedrock", "credential": None,
                "note": "sets CLAUDE_CODE_USE_BEDROCK=1; AWS credentials come from the server's environment/profile (AWS_PROFILE, AWS_REGION in extra_env)"},
    "vertex": {"label": "Google Vertex AI", "credential": None,
               "note": "sets CLAUDE_CODE_USE_VERTEX=1; ADC credentials from the server environment (CLOUD_ML_REGION, ANTHROPIC_VERTEX_PROJECT_ID in extra_env)"},
    "foundry": {"label": "Microsoft Foundry", "credential": "ANTHROPIC_FOUNDRY_API_KEY",
                "note": "sets CLAUDE_CODE_USE_FOUNDRY=1 and ANTHROPIC_FOUNDRY_BASE_URL from base_url"},
}

_SAFE_EXTRA = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SECRETISH = re.compile(r"(^|_)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)($|_)", re.I)


class ProviderProfiles:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[dict]:
        return [self.public(p) for p in self.db.all("SELECT * FROM provider_profiles ORDER BY created_at")]

    def get(self, pid: str) -> dict | None:
        return self.db.one("SELECT * FROM provider_profiles WHERE id = ?", [pid])

    def create(self, name: str, kind: str, base_url: str | None, model: str | None, credential_env: str | None, extra_env: dict | None) -> dict:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {sorted(KINDS)}")
        extra_env = extra_env or {}
        for k, v in extra_env.items():
            if not _SAFE_EXTRA.match(k) or _SECRETISH.search(k) or not isinstance(v, str):
                raise ValueError(f"extra_env may only hold non-secret UPPER_CASE variables with string values; rejected {k!r}. "
                                 f"Put secrets in the server environment and reference them by name in credential_env.")
        if credential_env and not _SAFE_EXTRA.match(credential_env):
            raise ValueError("credential_env must be an environment variable NAME")
        row = self.db.insert("provider_profiles", {"id": new_id("pp"), "name": name, "kind": kind, "base_url": base_url or None,
                                                   "model": model or None, "credential_env": credential_env or KINDS[kind]["credential"],
                                                   "extra_env": extra_env, "compat": {}, "created_at": now()})
        return self.public(row)

    def delete(self, pid: str) -> None:
        self.db.execute("DELETE FROM provider_profiles WHERE id = ?", [pid])

    def public(self, p: dict) -> dict:
        d = dict(p)
        d["credential_set"] = bool(p["credential_env"] and os.environ.get(p["credential_env"]))
        d["kind_label"] = KINDS.get(p["kind"], {}).get("label", p["kind"])
        return d

    def env_for(self, p: dict | None) -> tuple[dict[str, str], dict]:
        """(env for the child process, snapshot safe to persist/show)."""
        if p is None or p["kind"] == "claude_code_default":
            snap = {"profile_id": p["id"] if p else None, "kind": "claude_code_default", "model": p["model"] if p else None}
            env = {"ANTHROPIC_MODEL": p["model"]} if p and p["model"] else {}
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
        if p["model"]:
            env["ANTHROPIC_MODEL"] = p["model"]
        env.update(p["extra_env"] or {})
        # credential_env names where the server keeps the secret; CC reads it
        # under the variable its provider path expects.
        cred_name = p["credential_env"]
        target = KINDS[kind]["credential"] or cred_name
        cred_missing = False
        if cred_name:
            val = os.environ.get(cred_name)
            if val:
                env[target] = val
                if target == "ANTHROPIC_AUTH_TOKEN":     # a gateway token must not fall through to a stray API key
                    env.setdefault("ANTHROPIC_API_KEY", "")
            else:
                cred_missing = True
        snap = {"profile_id": p["id"], "name": p["name"], "kind": kind, "base_url": p["base_url"], "model": p["model"],
                "credential_env": cred_name, "credential_present": not cred_missing,
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
