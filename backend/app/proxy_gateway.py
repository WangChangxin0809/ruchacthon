"""Mounting and lifecycle for the conversion proxy.

`backend/app/proxy/` converts Anthropic Messages to an OpenAI-chat vendor.
This module is the part that touches the running server: it mounts the
router behind a loopback-only guard, hands each run a route token that
resolves to that run's upstream, and takes the token back when the run ends.

Two rules the proxy exists under:
- The route token is the only credential Claude Code ever sees. The upstream
  key lives in this process's memory (never in the DB as plaintext, never in
  a log, never in an event) and is read at forward time.
- `/proxy` answers loopback clients only. The token is a bearer credential
  for someone else's key, so it must not be reachable from the network even
  though the server itself listens on 0.0.0.0.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import secrets

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .proxy.router import router as proxy_api, routes as proxy_routes, set_client as set_proxy_client

log = logging.getLogger("workbench.proxy")

TOKEN_PREFIX = "wbp_"


def _loopback(host: str | None) -> bool:
    """Only the address of the socket's peer counts. X-Forwarded-For and
    friends are attacker-controlled, so they are never consulted."""
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_loopback


def base_url() -> str:
    """What a child process should use as ANTHROPIC_BASE_URL, without the
    route token. WORKBENCH_PROXY_BASE overrides it for exotic setups (a
    container that reaches the server by another name)."""
    explicit = os.environ.get("WORKBENCH_PROXY_BASE")
    if explicit:
        return explicit.rstrip("/")
    port = os.environ.get("WORKBENCH_PORT", "8787")
    return f"http://127.0.0.1:{port}"


class ProxyGateway:
    def __init__(self) -> None:
        self.routes = proxy_routes
        self._by_run: dict[str, str] = {}

    def issue(self, run_id: str, upstream: dict) -> tuple[str, str]:
        """Register `upstream` under a fresh token for this run and return
        (base_url_with_token, token). Re-issuing for a run replaces the old
        token, so a resumed run never leaves a live one behind."""
        self.revoke(run_id)
        token = TOKEN_PREFIX + secrets.token_urlsafe(24)
        self.routes.register(token, upstream["base_url"], upstream.get("api_key") or "",
                             model_map=upstream.get("model_map") or {}, extra_headers=upstream.get("extra_headers") or {},
                             api_format=upstream.get("api_format") or "openai_chat")
        self._by_run[run_id] = token
        log.info("proxy route issued for run %s -> %s", run_id, upstream["base_url"])
        return f"{base_url()}/proxy/{token}", token

    def revoke(self, run_id: str) -> None:
        token = self._by_run.pop(run_id, None)
        if token:
            self.routes.unregister(token)
            log.info("proxy route revoked for run %s", run_id)

    def revoke_all(self) -> None:
        for run_id in list(self._by_run):
            self.revoke(run_id)

    def active(self) -> int:
        return len(self._by_run)


def mount(app: FastAPI, gateway: ProxyGateway) -> None:
    """Mount /proxy with the loopback guard in front of it, and give the
    upstream client an environment-independent config: trust_env would let a
    server-wide HTTPS_PROXY silently re-route somebody's API key."""

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        if request.url.path.startswith("/proxy/") and not _loopback(request.client.host if request.client else None):
            log.warning("rejected non-loopback /proxy request from %s", request.client.host if request.client else "?")
            return JSONResponse({"type": "error", "error": {"type": "permission_error",
                                                            "message": "the conversion proxy answers loopback clients only"}}, status_code=403)
        return await call_next(request)

    set_proxy_client(httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0), follow_redirects=False, trust_env=False))
    app.include_router(proxy_api)
