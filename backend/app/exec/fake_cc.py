"""A test double for the real CC executor (cc_runner.py). This sandbox has
no Claude Code credentials (apiKeySource=none, verified against the real
CLI), so every event-pipeline test that needs a *successful* run, a
budget-exhausted run, or a cancellable long-running run goes through here
instead.

It is explicitly marked: callers must pass executor="fake-cc" in the run's
config_snapshot, and it emits the exact same claude_agent_sdk message
dataclasses the real CLI's stream-json gets parsed into (see normalize.py),
so nothing downstream can mix this up with a real run's output.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    Message,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

MODEL = "fake-model"


class UnknownScenario(ValueError):
    pass


async def run(*, run: dict, prompt: str, options: dict | None = None) -> AsyncIterator[Message]:
    options = options or {}
    scenario = options.get("scenario", "success")
    session_id = f"fake-session-{run['id']}"

    yield SystemMessage(subtype="init", data={
        "session_id": session_id, "apiKeySource": "none", "model": MODEL, "cwd": options.get("workspace_path", "."),
    })
    await asyncio.sleep(0)

    if scenario == "success":
        yield AssistantMessage(content=[TextBlock(text=f"Working on: {prompt}")], model=MODEL, session_id=session_id)
        await asyncio.sleep(0)
        yield AssistantMessage(
            content=[ToolUseBlock(id="tu_1", name="Bash", input={"command": "echo hi"})],
            model=MODEL, session_id=session_id,
        )
        await asyncio.sleep(0)
        yield UserMessage(content=[ToolResultBlock(tool_use_id="tu_1", content="hi", is_error=False)])
        await asyncio.sleep(0)
        yield AssistantMessage(content=[TextBlock(text="Done.")], model=MODEL, session_id=session_id)
        yield ResultMessage(subtype="success", duration_ms=10, duration_api_ms=5, is_error=False,
                             num_turns=2, session_id=session_id, result="Done.")

    elif scenario == "failure":
        yield AssistantMessage(content=[TextBlock(text="Trying...")], model=MODEL, session_id=session_id)
        await asyncio.sleep(0)
        yield ResultMessage(subtype="error_during_execution", duration_ms=5, duration_api_ms=2, is_error=True,
                             num_turns=1, session_id=session_id, terminal_reason="api_error",
                             result="fake failure: simulated crash")

    elif scenario == "budget_exhausted":
        for i in range(3):
            yield AssistantMessage(content=[TextBlock(text=f"Turn {i}")], model=MODEL, session_id=session_id)
            await asyncio.sleep(0)
        yield ResultMessage(subtype="error_max_turns", duration_ms=5, duration_api_ms=2, is_error=True,
                             num_turns=3, session_id=session_id, terminal_reason="max_turns",
                             result="max turns reached")

    elif scenario == "slow":
        # Real awaits (not sleep(0)) so an asyncio.Task.cancel() actually
        # lands mid-stream -- a cancel test against an already-finished
        # generator would prove nothing.
        for i in range(10_000):
            yield AssistantMessage(content=[TextBlock(text=f"tick {i}")], model=MODEL, session_id=session_id)
            await asyncio.sleep(0.05)
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                             num_turns=10_000, session_id=session_id, result="finished without being cancelled")

    else:
        raise UnknownScenario(scenario)
