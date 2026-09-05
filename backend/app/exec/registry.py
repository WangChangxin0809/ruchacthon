"""Owns every in-flight run: which executor drives it, the asyncio.Task
running its event loop, and the one place status transitions happen so
run.status/task.review/task.merge (D3) never get conflated.

Cancellation and restart-reaping both come from the same fact: an asyncio
Task cannot survive a process restart. So `reap_interrupted()` on startup
needs no pid/liveness probing -- any run still non-terminal in the DB was,
by definition, orphaned by the previous process exiting.
"""
from __future__ import annotations

import asyncio
import os
import time

from ..domain.status import RUN_TRANSITIONS, InvalidTransition, is_run_terminal
from ..store.events import EventBus
from ..store.repo import Repo
from . import cc_runner, fake_cc, normalize

EXECUTORS = {"cc": cc_runner.run, "fake": fake_cc.run}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Registry:
    def __init__(self, repo: Repo, bus: EventBus) -> None:
        self.repo = repo
        self.bus = bus
        self._tasks: dict[str, asyncio.Task] = {}

    def _set_status(self, run_id: str, new_status: str, **extra) -> dict:
        run = self.repo.get_run(run_id)
        if run["status"] not in RUN_TRANSITIONS:
            raise InvalidTransition(f"unknown current status {run['status']!r}")
        if new_status not in RUN_TRANSITIONS[run["status"]]:
            raise InvalidTransition(f"run {run_id} cannot go {run['status']!r} -> {new_status!r}")
        return self.repo.update_run(run_id, status=new_status, **extra)

    async def start_run(self, task_id: str, session_id: str, workspace_id: str,
                         prompt: str, executor_name: str = "fake", run_options: dict | None = None) -> dict:
        if executor_name not in EXECUTORS:
            raise ValueError(f"unknown executor: {executor_name}")
        task = self.repo.get_task(task_id)
        config_snapshot = {"executor": executor_name, "prompt": prompt, "options": run_options or {}}
        run = self.repo.create_run(task_id, session_id, workspace_id, config_snapshot)
        project_id = task["project_id"]
        await self.bus.publish(project_id, "run.started", {"run_id": run["id"], "attempt": run["attempt"]},
                                task_id=task_id, run_id=run["id"])
        coro = self._drive(run["id"], project_id, task_id, executor_name, prompt, run_options or {})
        self._tasks[run["id"]] = asyncio.create_task(coro)
        return run

    async def _drive(self, run_id: str, project_id: str, task_id: str,
                      executor_name: str, prompt: str, run_options: dict) -> None:
        try:
            self._set_status(run_id, "starting", pid=os.getpid())
            run = self.repo.get_run(run_id)
            executor = EXECUTORS[executor_name]
            self._set_status(run_id, "running", started_at=_now_iso())
            async for msg in executor(run=run, prompt=prompt, options=run_options):
                if normalize.is_result(msg):
                    status = normalize.result_status(msg)
                    self._set_status(run_id, status, ended_at=_now_iso(),
                                      terminal_reason=msg.terminal_reason, cc_session_id=msg.session_id)
                    await self.bus.publish(project_id, "run.finished",
                                            {"status": status, **normalize.result_payload(msg)},
                                            task_id=task_id, run_id=run_id)
                else:
                    for etype, payload in normalize.message_to_events(msg):
                        await self.bus.publish(project_id, etype, payload, task_id=task_id, run_id=run_id)
        except asyncio.CancelledError:
            run = self.repo.get_run(run_id)
            if not is_run_terminal(run["status"]):
                self._set_status(run_id, "cancelled", ended_at=_now_iso())
                await self.bus.publish(project_id, "run.finished", {"status": "cancelled"},
                                        task_id=task_id, run_id=run_id)
            raise
        except Exception as exc:  # noqa: BLE001 -- normalized into the run row, not swallowed
            run = self.repo.get_run(run_id)
            if not is_run_terminal(run["status"]):
                self._set_status(run_id, "failed", ended_at=_now_iso(), terminal_reason=str(exc))
                await self.bus.publish(project_id, "run.finished", {"status": "failed", "error": str(exc)},
                                        task_id=task_id, run_id=run_id)
        finally:
            self._tasks.pop(run_id, None)

    async def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if not task:
            return False
        run = self.repo.get_run(run_id)
        if not is_run_terminal(run["status"]):
            self._set_status(run_id, "cancelling")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return True

    def reap_interrupted(self) -> list[dict]:
        """Call once at startup, before any new run() is accepted. A run
        left in a non-terminal status was mid-flight when the previous
        process died -- it is not "still running", it is interrupted."""
        reaped = []
        for status in ("queued", "starting", "running", "cancelling"):
            for run in self.repo.list_runs_by_status(status):
                self.repo.update_run(run["id"], status="interrupted", ended_at=_now_iso())
                reaped.append(run)
        return reaped
