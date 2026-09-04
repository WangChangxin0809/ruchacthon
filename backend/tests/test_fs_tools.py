#!/usr/bin/env python3
"""fs_tools.py has two invariants worth locking down with a real test
rather than trusting the docstring: write_file refuses without a matching
room_claim, and no path can escape the project root -- including the edge
case where an actor claims a string that happens to be an absolute path
(room.claim itself doesn't validate path shape, so the safety has to live
in fs_tools, not upstream of it).

Run: python3 backend/tests/test_fs_tools.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent_loop import AgentIdentity  # noqa: E402
from app.fs_tools import PROJECT_ROOT, fs_tools  # noqa: E402
from app.room_state import RoomState  # noqa: E402

SCRATCH = "demo/target-app/.test-scratch.txt"


async def main() -> None:
    room = RoomState()
    identity = AgentIdentity(actor_id="agent-x", owner_id="tester", worktree_id="wt-x")
    _, fns = fs_tools(room, identity)

    r = await fns["write_file"]({"path": SCRATCH, "content": "hi"})
    assert r["ok"] is False, "write without a claim must refuse"

    r = await fns["read_file"]({"path": "../../etc/passwd"})
    assert r["ok"] is False, "'..' must never resolve outside the project"

    r = await fns["write_file"]({"path": "/etc/passwd", "content": "x"})
    assert r["ok"] is False, "an absolute path must refuse regardless of claim state"

    # The trickier case: room.claim doesn't validate path *shape*, so an
    # actor can hold a claim on a string that happens to look like an
    # absolute path. Safety has to live in fs_tools, not be inherited from
    # the claim check.
    await room.claim("/etc/passwd", "agent-x", "tester", "wt-x", "team")
    r = await fns["write_file"]({"path": "/etc/passwd", "content": "pwned"})
    assert r["ok"] is False, "holding a claim on an unsafe path string must not bypass path safety"

    await room.claim(SCRATCH, "agent-x", "tester", "wt-x", "team")
    r = await fns["write_file"]({"path": SCRATCH, "content": "hello from agent"})
    assert r["ok"] is True, f"write with a matching claim should succeed: {r}"

    r = await fns["read_file"]({"path": SCRATCH})
    assert r["ok"] is True and r["content"] == "hello from agent"

    r = await fns["list_dir"]({"path": "demo/target-app"})
    assert r["ok"] is True and os.path.basename(SCRATCH) in r["entries"]

    os.remove(PROJECT_ROOT / SCRATCH)
    print("all fs_tools assertions passed")


if __name__ == "__main__":
    asyncio.run(main())
