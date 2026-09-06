"""Preset catalogue, upstream model discovery, and the cheap connection check.

The catalogue is cc-switch's provider list as data (`provider_presets.py`),
served so the settings page can offer the same picker. Nothing here returns a
key: the discovery and check calls use the stored key server side and report
only what happened.
"""
from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException

from . import provider_presets as presets
from .auth import me
from .providers import RUNNABLE_FORMATS

log = logging.getLogger("workbench.providers")

CATEGORY_LABELS = {"official": "官方", "cn_official": "国内官方", "third_party": "第三方",
                   "aggregator": "聚合", "cloud_provider": "云厂商", "custom": "自定义"}
CATEGORY_ORDER = ("official", "cn_official", "third_party", "aggregator", "cloud_provider", "custom")
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


def _preset_item(p) -> dict:
    d = p.to_dict()
    return {"name": d["name"], "category": d["category"], "website_url": d["website_url"], "api_key_url": d["api_key_url"],
            "api_format": d["api_format"], "needs_routing": d["needs_routing"], "api_key_field": d["api_key_field"],
            "base_url": d["base_url"], "models": {k: v for k, v in presets.model_role_map(p.env).items() if v},
            "requires_oauth": d["requires_oauth"], "is_partner": d["is_partner"], "notes": d["notes"],
            "runnable": d["api_format"] in RUNNABLE_FORMATS and not d["requires_oauth"],
            "template_values": d["template_values"]}


def make_router(svc) -> APIRouter:
    r = APIRouter()
    profiles, db = svc.profiles, svc.db

    def _visible(profile_id: str, user: dict) -> dict:
        p = db.one("SELECT * FROM provider_profiles WHERE id = ?", [profile_id])
        if not p:
            raise HTTPException(404, "没有这个 Provider")
        if not profiles.visible_to(p, user):
            raise HTTPException(403, "这个 Provider 没有共享给你")
        return p

    @r.get("/api/presets")
    def list_presets(user: dict = Depends(me)):
        counts = presets.categories()
        cats = [{"id": c, "label": CATEGORY_LABELS.get(c, c), "count": counts.get(c, 0)} for c in CATEGORY_ORDER if counts.get(c)]
        return {"categories": cats, "items": [_preset_item(p) for p in presets.PRESETS],
                "key_prefixes": [{"prefix": pre, "preset": name} for pre, name, _ in presets.KEY_PREFIX_VENDORS]}

    @r.post("/api/profiles/{profile_id}/models")
    async def fetch_models(profile_id: str, user: dict = Depends(me)):
        """dsh's "fetch available models": ask the vendor what it has, with
        the key we already hold, and let the person pick from the answer."""
        p = _visible(profile_id, user)
        pre = profiles.preset_of(p)
        base = p.get("base_url") or (pre.base_url if pre else None)
        if not base:
            raise HTTPException(400, "这个 Provider 没有 API 地址，填一个再拉取")
        key = profiles.secrets.get(profiles._store_ref(p), profiles.env_allowed(p))
        if not key:
            raise HTTPException(400, "先填密钥再拉取模型列表")
        try:
            candidates = presets.models_url_candidates(base, pre)
        except ValueError as e:
            raise HTTPException(400, str(e))
        errors: list[str] = []
        async with httpx.AsyncClient(timeout=TIMEOUT, trust_env=False, follow_redirects=False) as client:
            for url in candidates:
                try:
                    resp = await client.get(url, headers={"Authorization": f"Bearer {key}", "x-api-key": key,
                                                          "anthropic-version": "2023-06-01"})
                except httpx.HTTPError as e:
                    errors.append(f"{url}: {type(e).__name__}")
                    continue
                if resp.status_code >= 400:
                    errors.append(f"{url}: HTTP {resp.status_code}")
                    continue
                try:
                    body = resp.json()
                except ValueError:
                    errors.append(f"{url}: 返回的不是 JSON")
                    continue
                rows = body.get("data") if isinstance(body, dict) else body
                models = [{"id": m.get("id") or m.get("name"), "name": m.get("display_name") or m.get("name")}
                          for m in (rows or []) if isinstance(m, dict) and (m.get("id") or m.get("name"))]
                if models:
                    return {"models": models, "source_url": url}
                errors.append(f"{url}: 列表是空的")
        # the key must not travel back to the browser inside a URL echo
        raise HTTPException(502, "拉取失败：" + "；".join(e.split("?")[0] for e in errors[:4]))

    @r.post("/api/profiles/{profile_id}/check")
    async def check(profile_id: str, user: dict = Depends(me)):
        """One tiny round trip on the same path a run would take, direct or
        through the conversion proxy's own converter. No Claude Code process,
        so it is fast and costs a token or two."""
        p = _visible(profile_id, user)
        started = time.monotonic()
        key = profiles.secrets.get(profiles._store_ref(p), profiles.env_allowed(p))
        if profiles.needs_routing(p):
            up = profiles.upstream_for(p)
            if not up or not up["api_key"]:
                return {"ok": False, "error": "没有密钥"}
            model = up["model_map"].get("*") or next(iter(up["model_map"].values()), None)
            if not model:
                return {"ok": False, "error": "还没有选模型：填一个模型名或拉取模型列表"}
            url = f"{up['base_url'].rstrip('/')}/chat/completions"
            body = {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": "Reply with the single word OK."}]}
            headers = {"Authorization": f"Bearer {up['api_key']}"}
            pick = lambda d: ((d.get("choices") or [{}])[0].get("message") or {}).get("content")  # noqa: E731
        else:
            base = p.get("base_url") or (profiles.preset_of(p).base_url if profiles.preset_of(p) else "https://api.anthropic.com")
            if not key and p["kind"] not in ("claude_code_default",):
                return {"ok": False, "error": "没有密钥"}
            model = p.get("model") or "claude-haiku-4-5-20251001"
            url = f"{base.rstrip('/')}/v1/messages"
            body = {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": "Reply with the single word OK."}]}
            headers = {"x-api-key": key or "", "authorization": f"Bearer {key or ''}", "anthropic-version": "2023-06-01"}
            pick = lambda d: " ".join(b.get("text", "") for b in (d.get("content") or []) if isinstance(b, dict))  # noqa: E731
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, trust_env=False, follow_redirects=False) as client:
                resp = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as e:
            return {"ok": False, "error": f"连不上：{type(e).__name__}", "model": model}
        ms = int((time.monotonic() - started) * 1000)
        if resp.status_code >= 400:
            detail = resp.text[:300]
            if key:
                detail = detail.replace(key, "***")
            return {"ok": False, "error": f"HTTP {resp.status_code}: {detail}", "latency_ms": ms, "model": model}
        try:
            reply = (pick(resp.json()) or "").strip()
        except ValueError:
            return {"ok": False, "error": "返回的不是 JSON", "latency_ms": ms, "model": model}
        result = {"ok": True, "latency_ms": ms, "model": model, "reply": reply[:200]}
        profiles.record_compat(profile_id, {**result, "kind": "ping"})
        return result

    return r
