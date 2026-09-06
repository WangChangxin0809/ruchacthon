#!/usr/bin/env python3
"""Preset profiles, the per-run conversion route, and the guards around it,
with no network and no Claude Code process: preset validation, the OpenAI
base-URL normalisation, the model alias map, the env a routed run gets,
route issue and revoke, the loopback rule, and log redaction.

The live half (a real key against a real vendor) is the scratchpad scripts
b4-live.sh and b4-run.sh, recorded in docs/reference/.

Run: python3 backend/tests/test_providers.py
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
TMP = tempfile.mkdtemp(prefix="wbprov-")
os.environ["WORKBENCH_DATA_DIR"] = os.path.join(TMP, "data")

from app.db import Database  # noqa: E402
from app.providers import RUNNABLE_FORMATS, ProviderProfiles, _openai_base  # noqa: E402
from app.proxy_gateway import ProxyGateway, TOKEN_PREFIX, _loopback, base_url  # noqa: E402
from app.secrets_store import SecretStore  # noqa: E402
from app import provider_presets as presets  # noqa: E402
from app import routes_providers  # noqa: E402


def expect(fn, needle: str) -> None:
    try:
        fn()
    except ValueError as e:
        assert needle in str(e), f"expected {needle!r} in {e}"
        return
    raise AssertionError(f"expected a ValueError containing {needle!r}")


def main() -> None:
    db = Database(Path(TMP, "data", "t.db"))
    store = SecretStore(Path(TMP, "data", "secrets.json"))
    profiles = ProviderProfiles(db, store)
    # a real row: ref_allowed looks the owner up to decide which store names
    # that profile may read, so a fake dict would silently fail the lookup
    admin = db.insert("users", {"id": "usr_a", "handle": "amy", "display_name": "Amy", "email": None, "password_hash": "x",
                                "is_admin": 1, "prefs": {}, "created_at": "2026-01-01T00:00:00.000+00:00", "last_seen_at": None})

    # --- the base URL an OpenAI-chat vendor actually answers on
    assert _openai_base("https://integrate.api.nvidia.com") == "https://integrate.api.nvidia.com/v1"
    assert _openai_base("https://integrate.api.nvidia.com/") == "https://integrate.api.nvidia.com/v1"
    assert _openai_base("https://x.example/v1") == "https://x.example/v1", "an existing version segment is left alone"
    assert _openai_base("https://x.example/v3") == "https://x.example/v3"
    for suffix in presets.KNOWN_COMPAT_SUFFIXES:
        assert _openai_base("https://x.example" + suffix).endswith(suffix), "a known compatibility path is not a version segment"

    # --- creating a preset profile
    p = profiles.create("nim", "preset", preset="Nvidia", display_name="NVIDIA", model="nvidia/demo-model",
                        secret="nvapi-not-a-real-key", owner=admin, model_map={"*": "nvidia/demo-model"})
    assert p["preset"] == "Nvidia" and p["api_format"] == "openai_chat" and p["needs_routing"] is True
    assert p["credential_set"] is True and p["runnable"] is True
    assert p["kind_label"] == "Nvidia" and p["website_url"].startswith("http")
    expect(lambda: profiles.create("nope", "preset", preset="No Such Vendor", owner=admin), "没有这个预置")
    expect(lambda: profiles.create("bad", "preset", preset="Nvidia", owner=admin, model_map={"a": 3}), "字符串")

    # a preset the runtime cannot drive is refused at creation, not at run time
    unrunnable = next((x for x in presets.PRESETS if x.api_format not in RUNNABLE_FORMATS), None)
    if unrunnable:
        expect(lambda: profiles.create("weird", "preset", preset=unrunnable.name, owner=admin), "暂不支持")

    row = profiles.get(p["id"])
    assert profiles.needs_routing(row) is True
    direct = profiles.create("direct", "anthropic_api_key", secret="sk-ant-not-real", owner=admin)
    assert profiles.needs_routing(profiles.get(direct["id"])) is False, "a direct Anthropic profile needs no proxy"
    assert profiles.upstream_for(profiles.get(direct["id"])) is None

    # --- what the proxy is told to do
    up = profiles.upstream_for(row)
    assert up["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert up["api_key"] == "nvapi-not-a-real-key" and up["credential_present"] is True
    assert up["model_map"]["*"] == "nvidia/demo-model"
    assert up["api_format"] == "openai_chat"

    # --- what Claude Code is told to ask for: alias names the map resolves
    alias = profiles.alias_env(row)
    assert alias["ANTHROPIC_MODEL"] == "nvidia/demo-model"
    assert set(alias) >= {"ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL"}
    for var, name in alias.items():
        if var != "ANTHROPIC_MODEL":
            assert name in up["model_map"] or name == up["model_map"]["*"], f"{var} must be something the proxy can map"

    # --- env_for never puts the vendor key in a routed run's environment
    env, snap = profiles.env_for(row, "nvidia/demo-model")
    assert snap["routed"] is True and snap["preset"] == "Nvidia"
    assert "nvapi-not-a-real-key" not in str(snap), "the snapshot is persisted, so it must hold no key"

    # --- route issue and revoke
    gw = ProxyGateway()
    url, token = gw.issue("run_1", up)
    assert token.startswith(TOKEN_PREFIX) and len(token) > 20
    assert url.endswith("/proxy/" + token) and url.startswith(base_url())
    route = gw.routes.get(token)
    assert route.base_url == up["base_url"] and route.api_key == up["api_key"]
    url2, token2 = gw.issue("run_1", up)
    assert token2 != token and gw.routes.get(token) is None, "re-issuing must not leave the old route alive"
    assert gw.active() == 1
    gw.revoke("run_1")
    assert gw.routes.get(token2) is None and gw.active() == 0
    gw.revoke("run_1")      # idempotent: a crashed run revokes twice
    gw.issue("run_2", up)
    gw.revoke_all()
    assert gw.active() == 0

    # --- only the socket peer decides loopback; headers cannot
    assert _loopback("127.0.0.1") and _loopback("::1") and _loopback("127.5.5.5")
    assert _loopback("::ffff:127.0.0.1"), "an IPv4-mapped loopback address is still loopback"
    assert not _loopback("8.8.8.8") and not _loopback("192.168.1.10") and not _loopback("::ffff:8.8.8.8")
    assert not _loopback(None) and not _loopback("") and not _loopback("localhost"), "a name is not an address"

    # --- the environment the proxy base URL is derived from
    os.environ["WORKBENCH_PORT"] = "9999"
    assert base_url() == "http://127.0.0.1:9999"
    os.environ["WORKBENCH_PROXY_BASE"] = "http://host.docker.internal:8787/"
    assert base_url() == "http://host.docker.internal:8787"
    del os.environ["WORKBENCH_PROXY_BASE"], os.environ["WORKBENCH_PORT"]

    # --- the preset catalogue the settings page renders
    items = [routes_providers._preset_item(x) for x in presets.PRESETS]
    assert len(items) == len(presets.PRESETS) > 80
    nvidia = [i for i in items if i["name"].lower() == "nvidia"][0]
    assert nvidia["needs_routing"] and nvidia["api_format"] == "openai_chat" and nvidia["runnable"]
    official = [i for i in items if i["category"] == "official"][0]
    assert not official["needs_routing"], "the official endpoint never goes through the proxy"
    assert all(set(i) >= {"name", "category", "api_format", "needs_routing", "runnable", "base_url"} for i in items)
    assert not any("api_key" in str(v).lower() and "url" not in k for i in items for k, v in i.items() if k == "env"), \
        "the catalogue carries no key material"

    # --- a route token must not survive in a log line
    from app.main import _RedactToken
    f = _RedactToken()
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s" %d',
                            ("127.0.0.1:1", "POST", "/proxy/wbp_abcDEF123-_/v1/messages", 200), None)
    f.filter(rec)
    assert "wbp_abcDEF123" not in str(rec.args), "the route token is a bearer credential while the run is live"
    assert "/proxy/<redacted>" in str(rec.args)
    rec2 = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s", ("/ws?since=3&token=wbs_secretvalue",), None)
    f.filter(rec2)
    assert "wbs_secretvalue" not in str(rec2.args)

    print("all provider and proxy-wiring assertions passed")


if __name__ == "__main__":
    main()
