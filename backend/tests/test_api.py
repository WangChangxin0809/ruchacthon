#!/usr/bin/env python3
"""Exercise the FastAPI app's REST surface directly (no server process, no
network) with Starlette's TestClient. Covers what
backend/tests/test_agent_loop_fake.py doesn't: HTTP status codes, request/
response shapes, and the escalate -> decide -> gate-passes round trip.

`app.main` builds `room`/`subagents`/`main_chat` as process-global
singletons (see backend/README.md on why the MCP server and the dashboard
share one `RoomState`), so `reset_state()` below clears them in place
between tests instead of re-importing the module -- `importlib.reload`
would rebind `app.main`'s own names to a fresh `RoomState()` but leave
`app.mcp_tools`'s already-imported reference pointing at the old one, and
those two must always be the same object for the MCP bridge to mean
anything (see backend/README.md, "Why one process").

Run: python3 backend/tests/test_api.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from starlette.testclient import TestClient  # noqa: E402

import app.main as main_module  # noqa: E402

client = TestClient(main_module.app)


def reset_state() -> None:
    room = main_module.room
    room._claims.clear()  # noqa: SLF001 -- test-only direct reset, not a public API
    room._escalations.clear()  # noqa: SLF001
    room._agents.clear()  # noqa: SLF001
    room._previews.clear()  # noqa: SLF001
    room._log.clear()  # noqa: SLF001
    room._docs.clear()  # noqa: SLF001
    main_module.subagents.subagents.clear()
    main_module.main_chat.history.clear()


def test_health():
    reset_state()
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["llm_configured"] is False, "no LLM_API_KEY should be set in the test environment"


def test_agents_and_escalations_start_empty():
    reset_state()
    assert client.get("/api/agents").json() == []
    assert client.get("/api/escalations").json() == []
    assert client.get("/api/previews").json() == []
    assert client.get("/api/subagents").json() == []


def test_chat_without_key_fails_clearly_not_a_500():
    reset_state()
    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200, "a missing key is an ok:false body, not an HTTP error"
    body = r.json()
    assert body["ok"] is False
    assert "LLM_API_KEY" in body["error"]


def test_spawn_subagent_without_key_reports_failed_not_500():
    reset_state()
    r = client.post("/api/subagents", json={"owner_id": "zhangsan", "worktree_id": "wt-1",
                                             "task": "do something"})
    assert r.status_code == 200
    actor_id = r.json()["actor_id"]

    subs = client.get("/api/subagents").json()
    assert len(subs) == 1
    assert subs[0]["actor_id"] == actor_id
    assert subs[0]["status"] == "failed"
    assert "LLM_API_KEY" in subs[0]["error"]

    # A failed subagent must still be visible on the board, not silently
    # dropped -- the dashboard's whole job is surfacing exactly this.
    agents = client.get("/api/agents").json()
    assert any(a["agent_id"] == actor_id and a["status"] == "needs_input" for a in agents)


def test_escalation_decide_round_trip_unblocks_the_gate():
    reset_state()
    room = main_module.room

    import asyncio
    asyncio.run(room.claim("shared.py", "agent-a", "zhangsan", "wt-a", "team"))
    result = asyncio.run(room.claim("shared.py", "agent-b", "lisi", "wt-b", "team"))
    assert result["conflict"] is True
    esc_id = result["escalation_id"]

    escs = client.get("/api/escalations").json()
    assert len(escs) == 1
    assert escs[0]["id"] == esc_id

    r = client.post(f"/api/escalations/{esc_id}/decide",
                     json={"decision": "approve", "reason": "zhangsan first", "actor": "human"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    assert client.get("/api/escalations").json() == [], "approving must clear it from the pending queue"


def test_decide_unknown_escalation_id_is_a_clean_failure_not_500():
    reset_state()
    r = client.post("/api/escalations/does-not-exist/decide",
                     json={"decision": "approve", "reason": "", "actor": "human"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


TESTS = [
    test_health,
    test_agents_and_escalations_start_empty,
    test_chat_without_key_fails_clearly_not_a_500,
    test_spawn_subagent_without_key_reports_failed_not_500,
    test_escalation_decide_round_trip_unblocks_the_gate,
    test_decide_unknown_escalation_id_is_a_clean_failure_not_500,
]


def main() -> int:
    failures = []
    for t in TESTS:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception as exc:  # noqa: BLE001 -- report and keep going
            failures.append((t.__name__, exc))
            print(f"FAIL  {t.__name__}: {exc}")
    if failures:
        print(f"\n{len(failures)} of {len(TESTS)} test(s) failed")
        return 1
    print(f"\nall {len(TESTS)} API test(s) passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
