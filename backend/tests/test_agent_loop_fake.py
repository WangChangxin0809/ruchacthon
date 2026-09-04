#!/usr/bin/env python3
"""Exercise agent_loop's tool-calling mechanics against a scripted fake
LLMClient -- no API key needed. This proves the loop, tool dispatch, and
message threading are correct; it does not and cannot prove a real model
will call these tools well, which is the one thing this sandbox has no
credentials to test (see docs/decisions/0002-llm-client-abstraction.md).

Run: python3 backend/tests/test_agent_loop_fake.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent_loop import AgentIdentity, room_tools, run_turn  # noqa: E402
from app.llm import ChatResult, ToolCall  # noqa: E402
from app.room_state import RoomState  # noqa: E402


class ScriptedLLM:
    """Stands in for LLMClient: returns a fixed sequence of ChatResults,
    one per call, regardless of what messages/tools it's given."""

    provider = "openai"

    def __init__(self, script: list[ChatResult]):
        self._script = list(script)
        self.calls = 0

    async def chat(self, messages, tools=None, system=""):
        self.calls += 1
        assert self._script, "ScriptedLLM ran out of scripted turns"
        return self._script.pop(0)


async def main() -> None:
    room = RoomState()
    identity = AgentIdentity(actor_id="agent-test", owner_id="tester", worktree_id="wt-test")
    tools, fns = room_tools(room, identity)

    # Turn 1: model claims a file, then (once it sees the result) answers plainly.
    scripted = ScriptedLLM([
        ChatResult(text="", tool_calls=[
            ToolCall(id="call_1", name="room_claim", arguments={"path": "app.py", "scope": "team"}),
        ]),
        ChatResult(text="Claimed app.py, no conflict. Done."),
    ])

    log = await run_turn(scripted, history=[{"role": "user", "content": "please edit app.py"}],
                          system="you are a coding agent", tools=tools, tool_fns=fns)

    assert scripted.calls == 2, f"expected 2 model calls, got {scripted.calls}"
    tool_events = [e for e in log.events if e["type"] == "tool_call"]
    assert len(tool_events) == 1, f"expected 1 tool call, got {len(tool_events)}"
    assert tool_events[0]["name"] == "room_claim"
    assert tool_events[0]["result"]["ok"] is True
    assert not tool_events[0]["result"]["conflict"]
    assert log.events[-1] == {"type": "assistant_text", "text": "Claimed app.py, no conflict. Done."}
    # message history must be well-formed for a follow-up call: system role never
    # injected into messages (agent_loop passes it separately), tool call/result paired.
    roles = [m["role"] for m in log.messages]
    assert roles == ["user", "assistant", "tool", "assistant"], roles

    # Turn 2: force a real team-scope conflict, confirm the tool layer (not the
    # fake model) is what detects it -- this is the actual thing worth testing here.
    await room.claim("shared.py", "agent-other", "someone-else", "wt-other", "team")
    scripted2 = ScriptedLLM([
        ChatResult(text="", tool_calls=[
            ToolCall(id="call_2", name="room_claim", arguments={"path": "shared.py", "scope": "team"}),
        ]),
        ChatResult(text="Got a conflict, escalated. Stopping here for a human."),
    ])
    log2 = await run_turn(scripted2, history=[{"role": "user", "content": "edit shared.py"}],
                           system="you are a coding agent", tools=tools, tool_fns=fns)
    conflict_result = next(e for e in log2.events if e["type"] == "tool_call")["result"]
    assert conflict_result["conflict"] is True
    assert "escalation_id" in conflict_result
    assert room.list_escalations(), "conflict should have produced a pending escalation"

    print("all agent_loop assertions passed:")
    print(f"  turn 1: {scripted.calls} model calls, "
          f"{len(tool_events)} tool call(s), roles={roles}")
    print(f"  turn 2: conflict detected, escalation_id={conflict_result['escalation_id']}")


if __name__ == "__main__":
    asyncio.run(main())
