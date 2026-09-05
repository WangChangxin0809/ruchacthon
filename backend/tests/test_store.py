"""Acceptance criterion 3: the ten 实况文档 §3.1 entities each have a table
(Room is a coordination scope equal to a project, not its own table)."""
from __future__ import annotations

EXPECTED_TABLES = {
    "projects", "workspaces", "tasks", "sessions", "runs", "artifacts",
    "claims", "decisions", "provider_profiles", "events",
}


def test_all_entity_tables_exist(repo):
    names = set(repo.db.table_names())
    missing = EXPECTED_TABLES - names
    assert not missing, f"missing tables: {missing}"


def test_task_and_run_round_trip(repo):
    project = repo.create_project("demo", "/tmp/x", "none")
    task = repo.create_task(project["id"], "write the thing")
    assert task["review"] == "unreviewed"
    assert task["merge"] == "not_merged"

    session = repo.create_session(task["id"])
    workspace = repo.create_workspace(project["id"], "dir", "/tmp/x/ws")
    run = repo.create_run(task["id"], session["id"], workspace["id"], {"executor": "fake-cc"})
    assert run["attempt"] == 1
    assert run["status"] == "queued"

    retry = repo.create_run(task["id"], session["id"], workspace["id"], {"executor": "fake-cc"})
    assert retry["attempt"] == 2
    assert repo.get_run(run["id"])["status"] == "queued", "creating a retry must not touch the original run"


async def test_events_have_monotonic_per_project_seq(bus, project):
    e1 = await bus.publish(project["id"], "task.created", {"a": 1})
    e2 = await bus.publish(project["id"], "task.created", {"a": 2})
    assert e2["seq"] == e1["seq"] + 1
