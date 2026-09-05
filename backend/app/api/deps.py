"""Process-wide singletons the routers share. One Repo/EventBus/Registry
per process, all backed by the one Database (D2) -- not per-request
instances, so a run started by one request is visible to the next.
"""
from __future__ import annotations

from ..exec.registry import Registry
from ..store.events import EventBus, get_event_bus
from ..store.repo import Repo, get_repo

_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry(get_repo(), get_event_bus())
    return _registry


def reset_deps_for_tests(repo: Repo, bus: EventBus) -> Registry:
    global _registry
    _registry = Registry(repo, bus)
    return _registry
