"""Anthropic-Messages front for OpenAI-chat-compatible vendors (port of the
cc-switch proxy). Mount `router` on the app and `routes.register(...)` a
route token per upstream; see `router.py`."""
from .router import Route, RouteTable, aclose_client, get_client, router, routes, set_client

__all__ = ["Route", "RouteTable", "aclose_client", "get_client", "router", "routes", "set_client"]
