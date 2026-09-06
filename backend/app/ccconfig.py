"""Discover the Claude Code installation and configuration this machine
already has, and say honestly which parts a Run actually inherits.

Values under `env` in settings files are reported by *name* only; the file
contents never leave this function whole.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

_SECRETISH = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")

# What a Run loads and what has been exercised end-to-end on this codebase.
SUPPORT = {
    "settings.json (user/project/local)": {"loaded_by_run": True, "how": "SDK setting_sources=['user','project','local']",
                                           "verified": "user: yes (auth + proxy env came from ~/.claude/settings.json in the probe). project/local: loaded by CC, not separately asserted"},
    "CLAUDE.md (user/project/nested)": {"loaded_by_run": True, "how": "CC loads it natively for the run's cwd when 'project' settings source is on",
                                        "verified": "not separately asserted; a worktree carries the project's CLAUDE.md so it applies there"},
    ".mcp.json (project MCP servers)": {"loaded_by_run": True, "how": "CC-native, plus the workbench's own run-bound SDK MCP servers",
                                        "verified": "workbench SDK servers: yes. Project .mcp.json servers: not asserted"},
    "skills": {"loaded_by_run": True, "how": "CC-native discovery from ~/.claude/skills and .claude/skills", "verified": "not asserted"},
    "hooks": {"loaded_by_run": True, "how": "CC-native from settings; the workbench adds its own PostToolUse heartbeat hook",
              "verified": "workbench hook: yes. User hooks: not asserted"},
    "plugins": {"loaded_by_run": True, "how": "CC-native", "verified": "not asserted"},
    "OAuth login": {"loaded_by_run": True, "how": "inherited from CC's own credential store / settings env; never read by the workbench",
                    "verified": "yes"},
}


def _mask_settings(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        return {"error": f"unreadable: {e}"}
    out: dict = {"keys": sorted(data.keys())}
    env = data.get("env") or {}
    out["env_names"] = sorted(env.keys())
    out["env_secretish_set"] = sorted(k for k, v in env.items() if any(s in k.upper() for s in _SECRETISH) and v)
    if "hooks" in data:
        out["hook_events"] = sorted(data["hooks"].keys())
    if "permissions" in data:
        out["permission_mode"] = data["permissions"].get("defaultMode")
    if "model" in data:
        out["model"] = data["model"]
    if "enabledPlugins" in data:
        out["plugins"] = sorted(data["enabledPlugins"].keys())
    return out


def discover(project_root: str | None = None) -> dict:
    home = Path.home()
    cc_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude"))
    binary = shutil.which("claude")
    version = None
    if binary:
        try:
            version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            version = f"error: {e}"
    global_json = home / ".claude.json"
    gj: dict = {}
    if global_json.is_file():
        try:
            data = json.loads(global_json.read_text())
            gj = {"exists": True, "has_oauth_account": bool(data.get("oauthAccount")), "user_mcp_servers": sorted((data.get("mcpServers") or {}).keys()),
                  "projects_known": len(data.get("projects") or {})}
        except (OSError, ValueError):
            gj = {"exists": True, "error": "unreadable"}
    result = {
        "platform": platform.system(), "claude_binary": binary, "claude_version": version,
        "config_dir": str(cc_home),
        "user": {"settings": _mask_settings(cc_home / "settings.json"), "settings_local": _mask_settings(cc_home / "settings.local.json"),
                 "claude_md": (cc_home / "CLAUDE.md").is_file(), "skills": _names(cc_home / "skills"), "commands": _names(cc_home / "commands"),
                 "agents": _names(cc_home / "agents"), "claude_json": gj},
        "project": None, "support": SUPPORT,
    }
    if project_root:
        root = Path(project_root)
        mcp = root / ".mcp.json"
        servers = []
        if mcp.is_file():
            try:
                servers = sorted((json.loads(mcp.read_text()).get("mcpServers") or {}).keys())
            except ValueError:
                servers = ["<unparseable>"]
        result["project"] = {"root": str(root), "settings": _mask_settings(root / ".claude" / "settings.json"),
                             "settings_local": _mask_settings(root / ".claude" / "settings.local.json"),
                             "claude_md": [str(p.relative_to(root)) for p in root.rglob("CLAUDE.md") if "node_modules" not in p.parts][:50],
                             "mcp_servers": servers, "skills": _names(root / ".claude" / "skills"), "agents": _names(root / ".claude" / "agents")}
    return result


def auth_status(env: dict | None = None) -> dict:
    """What `claude` itself says about its login, with the given env on top
    (so a saved token can be checked without touching the server's login)."""
    binary = shutil.which("claude")
    if not binary:
        return {"logged_in": False, "method": "none", "error": "claude binary not found"}
    try:
        full = {**os.environ, **(env or {})}
        for k in ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION"):
            full.pop(k, None)
        r = subprocess.run([binary, "auth", "status", "--json"], capture_output=True, text=True, timeout=30, env=full)
        data = json.loads(r.stdout or "{}")
        return {"logged_in": bool(data.get("loggedIn")), "method": data.get("authMethod"), "provider": data.get("apiProvider"),
                "email": data.get("email") or data.get("account", {}).get("email") if isinstance(data.get("account"), dict) else data.get("email"),
                "subscription": data.get("subscriptionType")}
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return {"logged_in": False, "method": "unknown", "error": str(e)[:200]}


def _names(d: Path) -> list[str]:
    if not d.is_dir():
        return []
    return sorted(p.stem if p.is_file() else p.name for p in d.iterdir() if not p.name.startswith("."))[:100]
