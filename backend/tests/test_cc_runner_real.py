"""Acceptance criterion 10: the real path really spawns a `claude`
subprocess through claude_agent_sdk (D1) -- this sandbox has no
credentials (verified: `claude -p` returns "Not logged in", apiKeySource
is "none"), so the honest, correct outcome is a `failed` run recording
that, not a mocked success.

Only one such subprocess is spawned, by exactly this one test module, per
the room's shared-container memory constraint -- do not add more.
"""
from __future__ import annotations

import asyncio
import shutil

import pytest

pytestmark = pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH")


async def test_real_cc_runner_reports_not_logged_in_as_failed(registry, project, repo):
    task = repo.create_task(project["id"], "real cc smoke test")
    session = repo.create_session(task["id"])
    workspace = repo.create_workspace(project["id"], "dir", "/tmp")

    run = await registry.start_run(
        task["id"], session["id"], workspace["id"], "say PONG",
        executor_name="cc", run_options={"max_turns": 1},
    )

    for _ in range(200):  # up to 20s for the subprocess to start, fail, and exit
        final = repo.get_run(run["id"])
        if final["status"] != "running" and final["status"] != "starting" and final["status"] != "queued":
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("real cc_runner run did not reach a terminal status in time")

    assert final["status"] == "failed", (
        f"expected the credential-less sandbox to fail honestly, got {final['status']!r} "
        f"(terminal_reason={final['terminal_reason']!r})"
    )
    assert final["terminal_reason"] == "api_error"
