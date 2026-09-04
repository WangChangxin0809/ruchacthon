#!/usr/bin/env python3
"""Drive the real AgentRoom MCP server the same way two real Claude Code
agents would -- over the actual streamable-HTTP MCP protocol, not a fake
in-process call.

Why this script exists instead of two `claude -p` processes: this sandbox's
shell has no usable Anthropic credentials (by design -- the harness keeps
them out of subprocess reach), so a headless `claude` CLI spawned from here
always answers "Not logged in", and the Agent tool's subagents don't pick up
this repo's .mcp.json. Neither constraint exists outside this sandbox: point
a real Claude Code session (a laptop, a `cheese split` sandbox, a teammate's
machine) at this same `.mcp.json` and `room_claim` etc. show up as ordinary
tools -- verified by hand with the `mcp` Python client against this exact
server before writing this file.

This script exercises the identical MCP tool calls a real agent would make,
against the identical running server, so everything downstream of "an agent
called room_claim" (CRDT merge, escalation, Gate) is exercised for real.
Only the "an LLM decided to make this call" part is stood in for.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MCP_URL = "http://localhost:8787/mcp/"
TARGET = Path(__file__).resolve().parent.parent.parent / "demo" / "target-app" / "app.py"


async def agent_turn(actor_id: str, owner_id: str, worktree_id: str, path: str,
                      edit_note: str) -> None:
    async with streamable_http_client(MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            claim = await session.call_tool("room_claim", {
                "path": path, "actor_id": actor_id, "owner_id": owner_id,
                "worktree_id": worktree_id, "scope": "team",
            })
            print(f"[{actor_id}] room_claim -> {claim.content[0].text}")
            await session.call_tool("room_broadcast", {
                "actor_id": actor_id, "owner_id": owner_id, "worktree_id": worktree_id,
                "scope": "team", "path": path, "message": edit_note,
            })
            await asyncio.sleep(0.3)  # let the other side's claim land first
            await session.call_tool("room_release", {"path": path, "actor_id": actor_id})
            print(f"[{actor_id}] released {path}")


def apply_edit(pattern: str, replacement: str) -> None:
    text = TARGET.read_text()
    TARGET.write_text(re.sub(pattern, replacement, text, count=1))


async def main() -> None:
    text = TARGET.read_text()
    if "See you soon" in text and text.count("!") >= 1:
        print("demo file already edited by a previous run -- re-run "
              "scripts/demo/reset_demo.sh first if you want a clean pass")

    await asyncio.gather(
        agent_turn("agent-zhangsan-1", "zhangsan", "wt-zhangsan",
                   "demo/target-app/app.py",
                   "adding '!' to greet()'s return value"),
        agent_turn("agent-lisi-1", "lisi", "wt-lisi",
                   "demo/target-app/app.py",
                   "appending 'See you soon' to farewell()'s return value"),
    )

    apply_edit(r'return f"Hello, \{name\}"', 'return f"Hello, {name}!"')
    apply_edit(r'return f"Bye, \{name\}"', 'return f"Bye, {name}. See you soon"')
    print("\napplied both edits to", TARGET)
    print("check the dashboard's escalation queue -- the team-scope claim "
          "collision on this file needs a human decision before "
          "scripts/gates/check_escalation_decisions.py will pass")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
