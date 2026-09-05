"""HTTP surface tests. D1 requires Claude Code to be the only execution
harness -- test_no_self_written_llm_client below is the machine-checkable
form of that rule, not just a comment."""
from __future__ import annotations

import asyncio
from pathlib import Path


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_no_self_written_llm_client_imports():
    app_dir = Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "import anthropic" in text or "import openai" in text:
            offenders.append(str(path))
    assert not offenders, f"self-written LLM client imports found in: {offenders}"

    requirements = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text()
    assert "anthropic" not in requirements
    assert "openai" not in requirements


def test_project_and_task_round_trip(client):
    r = client.post("/api/projects", json={"name": "demo", "root_path": "/tmp/x", "vcs": "none"})
    assert r.status_code == 200
    project = r.json()

    r = client.post(f"/api/tasks?project_id={project['id']}", json={"title": "do the thing"})
    assert r.status_code == 200
    task = r.json()
    assert task["review"] == "unreviewed"

    r = client.get(f"/api/tasks/{task['id']}")
    assert r.status_code == 200
    assert r.json()["runs"] == []


def test_send_message_runs_fake_executor_end_to_end(client):
    project = client.post("/api/projects", json={"name": "demo2", "root_path": "/tmp/y", "vcs": "none"}).json()
    task = client.post(f"/api/tasks?project_id={project['id']}", json={"title": "demo task"}).json()

    r = client.post(f"/api/tasks/{task['id']}/messages",
                     json={"message": "hello", "executor": "fake", "options": {"scenario": "success"}})
    assert r.status_code == 200
    run = r.json()
    assert run["status"] == "queued"

    import time
    for _ in range(20):
        got = client.get(f"/api/runs/{run['id']}").json()
        if got["status"] == "succeeded":
            break
        time.sleep(0.05)
    assert got["status"] == "succeeded"

    events = client.get(f"/api/runs/{run['id']}/events").json()
    assert [e["type"] for e in events][0] == "run.started"
    assert events[-1]["type"] == "run.finished"


def test_escalation_decide_round_trip_unblocks_the_gate(client):
    from app.room.state import room

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


def test_ws_reconnect_with_since_seq_has_no_loss_or_duplication(client, bus, project):
    with client.websocket_connect(f"/ws?project_id={project['id']}&since_seq=0") as ws:
        asyncio.run(bus.publish(project["id"], "task.created", {"n": 1}))
        asyncio.run(bus.publish(project["id"], "task.created", {"n": 2}))
        first = ws.receive_json()
        second = ws.receive_json()
    seen_before_disconnect = [first["seq"], second["seq"]]

    # More events land while nobody is connected.
    asyncio.run(bus.publish(project["id"], "task.created", {"n": 3}))
    asyncio.run(bus.publish(project["id"], "task.created", {"n": 4}))

    with client.websocket_connect(f"/ws?project_id={project['id']}&since_seq={seen_before_disconnect[-1]}") as ws:
        third = ws.receive_json()
        fourth = ws.receive_json()

    all_seqs = seen_before_disconnect + [third["seq"], fourth["seq"]]
    assert all_seqs == sorted(set(all_seqs)) == list(range(all_seqs[0], all_seqs[0] + 4)), \
        "reconnect with since_seq must replay exactly what was missed -- no gap, no duplicate"
