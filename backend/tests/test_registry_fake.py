"""Acceptance criteria 4-8, driven through the fake executor since this
sandbox cannot complete a real Claude Code model call (see
exec/fake_cc.py's docstring)."""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def task_and_workspace(repo, project):
    task = repo.create_task(project["id"], "demo task")
    session = repo.create_session(task["id"])
    workspace = repo.create_workspace(project["id"], "dir", "/tmp/agentroom-test-ws")
    return task, session, workspace


async def test_fake_run_emits_full_event_sequence_with_increasing_seq(registry, bus, project, task_and_workspace):
    task, session, workspace = task_and_workspace
    run = await registry.start_run(task["id"], session["id"], workspace["id"], "do the thing",
                                    executor_name="fake", run_options={"scenario": "success"})
    await asyncio.sleep(0.2)

    final = registry.repo.get_run(run["id"])
    assert final["status"] == "succeeded"

    events = bus.since(project["id"], 0)
    types = [e["type"] for e in events]
    assert types == [
        "run.started", "run.output.text", "run.tool.started",
        "run.tool.result", "run.output.text", "run.finished",
    ]
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "seq must be strictly increasing, no duplicates"


async def test_retry_creates_new_attempt_and_preserves_the_old_run(registry, task_and_workspace):
    task, session, workspace = task_and_workspace
    run1 = await registry.start_run(task["id"], session["id"], workspace["id"], "attempt one",
                                     executor_name="fake", run_options={"scenario": "success"})
    await asyncio.sleep(0.2)
    assert registry.repo.get_run(run1["id"])["status"] == "succeeded"

    run2 = await registry.start_run(task["id"], session["id"], workspace["id"], "attempt two",
                                     executor_name="fake", run_options={"scenario": "failure"})
    await asyncio.sleep(0.2)

    assert run2["attempt"] == run1["attempt"] + 1
    assert registry.repo.get_run(run2["id"])["status"] == "failed"
    # the retry must not have touched the first attempt's recorded outcome
    assert registry.repo.get_run(run1["id"])["status"] == "succeeded"
    assert registry.repo.get_run(run1["id"])["id"] == run1["id"]


async def test_cancel_marks_cancelled_not_succeeded(registry, task_and_workspace):
    task, session, workspace = task_and_workspace
    run = await registry.start_run(task["id"], session["id"], workspace["id"], "long task",
                                    executor_name="fake", run_options={"scenario": "slow"})
    await asyncio.sleep(0.15)
    ok = await registry.cancel(run["id"])
    assert ok is True
    assert registry.repo.get_run(run["id"])["status"] == "cancelled"


async def test_failed_and_budget_exhausted_are_distinct_outcomes(registry, task_and_workspace):
    task, session, workspace = task_and_workspace
    failed_run = await registry.start_run(task["id"], session["id"], workspace["id"], "will fail",
                                           executor_name="fake", run_options={"scenario": "failure"})
    budget_run = await registry.start_run(task["id"], session["id"], workspace["id"], "will exhaust budget",
                                           executor_name="fake", run_options={"scenario": "budget_exhausted"})
    await asyncio.sleep(0.3)

    assert registry.repo.get_run(failed_run["id"])["status"] == "failed"
    assert registry.repo.get_run(budget_run["id"])["status"] == "budget_exhausted"


async def test_restart_reaps_non_terminal_runs_to_interrupted(repo, bus, task_and_workspace):
    from app.exec.registry import Registry

    task, session, workspace = task_and_workspace
    reg = Registry(repo, bus)
    run = await reg.start_run(task["id"], session["id"], workspace["id"], "stuck forever",
                               executor_name="fake", run_options={"scenario": "slow"})
    await asyncio.sleep(0.05)
    assert repo.get_run(run["id"])["status"] == "running"

    # A fresh Registry with no tracked asyncio.Task models exactly what a
    # process restart looks like: the run is orphaned, not "still running".
    fresh_registry = Registry(repo, bus)
    reaped = fresh_registry.reap_interrupted()

    assert run["id"] in {r["id"] for r in reaped}
    assert repo.get_run(run["id"])["status"] == "interrupted"

    # Clean up the still-live task from the first registry so it doesn't
    # leak into the next test.
    await reg.cancel(run["id"])
