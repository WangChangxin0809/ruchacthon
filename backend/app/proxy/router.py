"""Local Anthropic-Messages front for OpenAI-chat-compatible vendors.

Claude Code is pointed at `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>/proxy/<route_token>`
with `ANTHROPIC_AUTH_TOKEN=<route_token>`. The route token resolves to an
upstream {base_url, api_key, model_map, extra_headers}. Only the route token
ever leaves this process towards Claude Code; the upstream key is read from
the registry at forward time and never logged or echoed.

Wiring (registering routes, mounting the router) is done by the caller.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import convert
from .streaming import anthropic_message_to_sse, create_anthropic_sse_stream

log = logging.getLogger("workbench.proxy")

DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
# Client headers that must never reach the upstream: they carry Claude Code's
# credentials for *us*, or hop-by-hop transport state.
_DROP_CLIENT_HEADERS = {"authorization", "x-api-key", "host", "content-length", "connection", "transfer-encoding",
                        "accept-encoding", "keep-alive", "proxy-authorization", "te", "trailer", "upgrade"}


@dataclass
class Route:
    token: str = field(repr=False)
    base_url: str
    api_key: str = field(repr=False)
    model_map: dict[str, str] = field(default_factory=dict)
    extra_headers: dict[str, str] = field(default_factory=dict)
    # "openai_chat" converts; "anthropic" passes the Messages request through.
    api_format: str = "openai_chat"

    def upstream_url(self, endpoint: str) -> str:
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        while "/v1/v1" in url:
            url = url.replace("/v1/v1", "/v1")
        return url


class RouteTable:
    def __init__(self) -> None:
        self._routes: dict[str, Route] = {}

    def register(self, route_token: str, base_url: str, api_key: str, model_map: dict[str, str] | None = None,
                 extra_headers: dict[str, str] | None = None, api_format: str = "openai_chat") -> Route:
        if not route_token or not base_url:
            raise ValueError("route_token and base_url are required")
        if api_format not in ("openai_chat", "anthropic"):
            raise ValueError(f"unsupported api_format {api_format!r}")
        route = Route(route_token, base_url, api_key or "", dict(model_map or {}), dict(extra_headers or {}), api_format)
        self._routes[route_token] = route
        return route

    def unregister(self, route_token: str) -> None:
        self._routes.pop(route_token, None)

    def get(self, route_token: str) -> Route | None:
        return self._routes.get(route_token)

    def __len__(self) -> int:
        return len(self._routes)


routes = RouteTable()
router = APIRouter(prefix="/proxy/{route_token}")

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0), follow_redirects=False)
    return _client


def set_client(client: httpx.AsyncClient | None) -> None:
    """Swap the upstream client (tests inject an httpx.MockTransport)."""
    global _client
    _client = client


async def aclose_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _error(status: int, message: str, error_type: str | None = None) -> JSONResponse:
    return JSONResponse(convert.anthropic_error(status, message, error_type), status_code=status)


def _presented_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth:
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    key = request.headers.get("x-api-key")
    return key.strip() if key and key.strip() else None


def _resolve_route(route_token: str, request: Request) -> Route | JSONResponse:
    route = routes.get(route_token)
    if route is None:
        return _error(401, "unknown proxy route token", "authentication_error")
    presented = _presented_token(request)
    if presented is not None and presented != route_token:
        return _error(401, "proxy route token mismatch", "authentication_error")
    return route


async def _read_json(request: Request) -> dict | JSONResponse:
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except ValueError as e:
        return _error(400, f"request body is not valid JSON: {e}")
    if not isinstance(body, dict):
        return _error(400, "request body must be a JSON object")
    return body


def _debug_log_request(route: Route, request: Request, body: dict, endpoint: str) -> None:
    if not log.isEnabledFor(logging.DEBUG):
        return
    # Header names and top-level keys only; values may carry secrets or prompts.
    log.debug("proxy %s fmt=%s headers=%s keys=%s model=%s stream=%s", endpoint, route.api_format,
              sorted(request.headers.keys()), sorted(body.keys()), body.get("model"), body.get("stream"))


def _openai_headers(route: Route, stream: bool) -> dict[str, str]:
    headers = {"content-type": "application/json", "accept": "text/event-stream" if stream else "application/json"}
    if stream:
        # A compressing edge would batch several SSE events per gzip block
        # and delay tokens; httpx would otherwise advertise gzip/deflate/br.
        headers["accept-encoding"] = "identity"
    headers.update(route.extra_headers)
    if route.api_key:
        headers["authorization"] = f"Bearer {route.api_key}"
    return headers


def _anthropic_headers(route: Route, request: Request, stream: bool) -> dict[str, str]:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_CLIENT_HEADERS}
    headers["content-type"] = "application/json"
    if stream:
        headers["accept-encoding"] = "identity"
    headers.setdefault("anthropic-version", DEFAULT_ANTHROPIC_VERSION)
    headers.update(route.extra_headers)
    if route.api_key:
        headers["x-api-key"] = route.api_key
    return headers


_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def _json_bytes(obj: dict) -> bytes:
    text = json.dumps(obj, ensure_ascii=False)
    try:
        return text.encode()
    except UnicodeEncodeError:
        # Claude Code cuts long tool output by character count, so a JSON
        # `\udXXX` half of an emoji pair is a normal input; strict UTF-8 refuses
        # it and the same history would 500 on every retry.
        return _LONE_SURROGATE.sub("\ufffd", text).encode()


def _scrub(route: Route, text: str) -> str:
    return text.replace(route.api_key, "<api_key>") if route.api_key else text


def _upstream_failure(route: Route, e: Exception) -> JSONResponse:
    if isinstance(e, httpx.TimeoutException):
        return _error(504, _scrub(route, f"upstream timeout: {e}"), "api_error")
    return _error(502, _scrub(route, f"upstream request failed: {e.__class__.__name__}: {e}"), "api_error")


async def _pipe(resp: httpx.Response, body: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    try:
        async for chunk in body:
            yield chunk
    finally:
        await resp.aclose()


async def _sniff_sse(resp: httpx.Response) -> tuple[bool, AsyncIterator[bytes]]:
    """Decide streaming from the body, not the content-type: some gateways
    serve SSE as text/plain or octet-stream. Returns (is_sse, body iterator
    with the peeked bytes replayed)."""
    body = resp.aiter_bytes()
    head = b""
    is_sse = "text/event-stream" in resp.headers.get("content-type", "")
    if not is_sse:
        async for chunk in body:
            head += chunk
            if head.strip():
                break
        first = head.lstrip()[:1]
        # Only a JSON object is treated as "upstream ignored stream"; anything
        # else (data:/event:/:/id:) is fed to the SSE converter as Rust does.
        is_sse = bool(first) and first != b"{"

    async def replay() -> AsyncIterator[bytes]:
        if head:
            yield head
        async for chunk in body:
            yield chunk
    return is_sse, replay()


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------
@router.post("/v1/messages")
async def messages(route_token: str, request: Request):
    route = _resolve_route(route_token, request)
    if isinstance(route, JSONResponse):
        return route
    body = await _read_json(request)
    if isinstance(body, JSONResponse):
        return body
    _debug_log_request(route, request, body, "/v1/messages")
    if route.api_format == "anthropic":
        return await _forward_anthropic(route, request, body, "/v1/messages")
    return await _forward_openai_chat(route, body)


@router.post("/v1/messages/count_tokens")
async def count_tokens(route_token: str, request: Request):
    route = _resolve_route(route_token, request)
    if isinstance(route, JSONResponse):
        return route
    body = await _read_json(request)
    if isinstance(body, JSONResponse):
        return body
    _debug_log_request(route, request, body, "/v1/messages/count_tokens")
    if route.api_format == "anthropic":
        return await _forward_anthropic(route, request, body, "/v1/messages/count_tokens")
    return JSONResponse({"input_tokens": convert.estimate_input_tokens(body)})


@router.get("/v1/models")
async def models(route_token: str, request: Request):
    route = _resolve_route(route_token, request)
    if isinstance(route, JSONResponse):
        return route
    data = [{"type": "model", "id": k, "display_name": v} for k, v in route.model_map.items() if k != "*"]
    return JSONResponse({"data": data, "has_more": False, "first_id": data[0]["id"] if data else None,
                         "last_id": data[-1]["id"] if data else None})


async def _forward_openai_chat(route: Route, body: dict) -> Response:
    requested_model = body.get("model") if isinstance(body.get("model"), str) else None
    mapped_model = convert.map_model(requested_model, route.model_map) if requested_model else ""
    preserve = convert.is_reasoning_vendor_identifier(route.base_url) or convert.is_reasoning_vendor_identifier(mapped_model)
    try:
        openai_body = convert.anthropic_to_openai_request(body, route.model_map, preserve_reasoning_content=preserve)
    except Exception as e:  # noqa: BLE001 - malformed client body
        return _error(400, f"cannot convert request: {e}")
    stream = openai_body.get("stream") is True
    url = route.upstream_url("/chat/completions")
    client = get_client()

    if not stream:
        try:
            resp = await client.post(url, content=_json_bytes(openai_body), headers=_openai_headers(route, False))
        except httpx.HTTPError as e:
            return _upstream_failure(route, e)
        if resp.status_code >= 400:
            status, err = convert.upstream_error_to_anthropic(resp.status_code, _scrub(route, resp.text))
            return JSONResponse(err, status_code=status)
        try:
            message = convert.openai_to_anthropic_response(resp.json(), requested_model)
        except (ValueError, convert.TransformError) as e:
            return _error(502, _scrub(route, f"upstream response not convertible: {e}"), "api_error")
        return JSONResponse(message)

    req = client.build_request("POST", url, content=_json_bytes(openai_body), headers=_openai_headers(route, True))
    try:
        resp = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        return _upstream_failure(route, e)
    if resp.status_code >= 400:
        raw = await resp.aread()
        await resp.aclose()
        status, err = convert.upstream_error_to_anthropic(resp.status_code, _scrub(route, raw.decode("utf-8", "replace")))
        return JSONResponse(err, status_code=status)
    sse_headers = {"cache-control": "no-cache"}
    is_sse, upstream_body = await _sniff_sse(resp)
    if not is_sse:
        # Upstream ignored `stream`: replay the whole message as SSE.
        raw = b"".join([chunk async for chunk in upstream_body])
        await resp.aclose()
        try:
            message = convert.openai_to_anthropic_response(json.loads(raw), requested_model)
        except (ValueError, convert.TransformError) as e:
            return _error(502, _scrub(route, f"upstream response not convertible: {e}"), "api_error")
        return StreamingResponse(iter(anthropic_message_to_sse(message)), media_type="text/event-stream", headers=sse_headers)
    return StreamingResponse(_pipe(resp, create_anthropic_sse_stream(upstream_body, requested_model)),
                             media_type="text/event-stream", headers=sse_headers)


async def _forward_anthropic(route: Route, request: Request, body: dict, endpoint: str) -> Response:
    if isinstance(body.get("model"), str):
        body = {**body, "model": convert.map_model(body["model"], route.model_map)}
    stream = body.get("stream") is True
    client = get_client()
    req = client.build_request("POST", route.upstream_url(endpoint), content=_json_bytes(body),
                               headers=_anthropic_headers(route, request, stream))
    try:
        resp = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        return _upstream_failure(route, e)
    content_type = resp.headers.get("content-type", "application/json")
    if stream and resp.status_code < 400:
        is_sse, upstream_body = await _sniff_sse(resp)
        if is_sse:
            return StreamingResponse(_pipe(resp, upstream_body), media_type="text/event-stream", headers={"cache-control": "no-cache"})
        raw = b"".join([chunk async for chunk in upstream_body])
    else:
        raw = await resp.aread()
    await resp.aclose()
    if route.api_key and route.api_key.encode() in raw:
        raw = raw.replace(route.api_key.encode(), b"<api_key>")
    return Response(content=raw, status_code=resp.status_code, media_type=content_type)
