"""One in-memory SQLite database for the whole test session, shared by
every module-level singleton (room, repo, event bus, registry) -- reset by
truncating tables between tests, not by swapping objects, since
app.room.state's module-level `room` is constructed once at import time
and a swapped-out Database would leave it pointing at stale state.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.api.deps import reset_deps_for_tests
from app.store.db import reset_db_for_tests
from app.store.events import reset_event_bus_for_tests
from app.store.repo import reset_repo_for_tests

TEST_DB = reset_db_for_tests(Path(":memory:"))
TEST_REPO = reset_repo_for_tests(TEST_DB)
TEST_BUS = reset_event_bus_for_tests(TEST_DB)
TEST_REGISTRY = reset_deps_for_tests(TEST_REPO, TEST_BUS)

import app.room.state as room_state_module  # noqa: E402

room_state_module.room.db = TEST_DB

# Children before parents, or SQLite's FK enforcement rejects the DELETE.
_TABLES = [
    "artifacts", "decisions", "runs", "task_deps", "sessions", "tasks", "workspaces", "projects",
    "claims", "escalations", "room_agents", "previews", "room_log", "provider_profiles", "events",
]


@pytest.fixture(autouse=True)
def _clean_state():
    yield
    with TEST_DB.transaction() as conn:
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table}")
    room_state_module.room._docs.clear()


@pytest.fixture
def repo():
    return TEST_REPO


@pytest.fixture
def bus():
    return TEST_BUS


@pytest.fixture
def registry():
    return TEST_REGISTRY


@pytest.fixture
def project(repo):
    return repo.create_project("demo", "/tmp/agentroom-test-project", "none")


@pytest.fixture(scope="session")
def client():
    # session-scoped and entered exactly once: the mounted MCP server's
    # StreamableHTTPSessionManager forbids .run() being called a second
    # time on the same instance, so the lifespan (which calls it) must not
    # cycle per test the way a fresh `with TestClient(...)` per test would.
    from starlette.testclient import TestClient

    import app.main as main_module

    with TestClient(main_module.app) as c:
        yield c
