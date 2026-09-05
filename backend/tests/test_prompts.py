#!/usr/bin/env python3
"""The prompt builder never tells a model about a tool it does not have,
leaves no unfilled placeholder, and carries the standing sections.

Run: python3 backend/tests/test_prompts.py
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import prompts  # noqa: E402
from app.prompts import (KNOWN_TOOLS, MULTI_HUMAN_NOTE, PROMPT_GUARD, ROLES, TRUST_BOUNDARY,  # noqa: E402
                         build_system_prompt)

TOOL_RE = re.compile(r"\b(" + "|".join(sorted(KNOWN_TOOLS, key=len, reverse=True)) + r")\b")
MAIN_TOOLS = ["spawn_worker", "list_workers", "message_worker", "cancel_worker", "worker_transcript", "submit_artifact",
              "room_claim", "room_release", "room_state", "room_broadcast", "room_inbox", "room_handoff",
              "message_agent", "list_agents", "ask_human"]
WORKER_TOOLS = ["room_claim", "room_release", "room_state", "room_broadcast", "room_inbox", "room_handoff",
                "submit_artifact", "start_devserver", "message_agent", "list_agents", "ask_human"]
REVIEWER_TOOLS = ["submit_artifact", "ask_human"]
ROLE_TOOLS = {"orchestrator": MAIN_TOOLS, "worker": WORKER_TOOLS, "reviewer": REVIEWER_TOOLS, "chat": []}


def mentioned(text: str) -> set[str]:
    return set(TOOL_RE.findall(text))


def main() -> None:
    for role in ROLES:
        for tools in (ROLE_TOOLS[role], [], ["send_message", "list_sessions", "kill_session"]):
            out = build_system_prompt(role, project_name="demo", project_root="/srv/demo", main_session_id="ses_1",
                                      tools=tools, extra="## Project Rules\nrun ./ci.sh --fast")
            assert out.strip(), f"{role} rendered empty"
            assert "{" not in out and "}" not in out, f"{role}: unfilled placeholder in prompt"
            leaked = mentioned(out) - set(tools)
            assert not leaked, f"{role} with tools={tools}: mentions unavailable tools {sorted(leaked)}"
            assert MULTI_HUMAN_NOTE in out, f"{role}: MULTI_HUMAN_NOTE missing"
            assert PROMPT_GUARD in out, f"{role}: PROMPT_GUARD missing"
            assert out.rstrip().endswith("run ./ci.sh --fast"), f"{role}: extra section must come last"
            assert "demo" in out and "/srv/demo" in out

    # aliases from the design doc stand in for the canonical names
    out = build_system_prompt("worker", project_name="d", project_root="/d", tools=["send_message", "room_claim"])
    assert "send_message" in out and "message_agent" not in out
    assert "session ses_9" in build_system_prompt("worker", project_name="d", project_root="/d", main_session_id="ses_9",
                                                  tools=["message_agent"])
    # 'main' is the run kind runs.py uses for the orchestrator
    assert build_system_prompt("main", project_name="d", project_root="/d", tools=MAIN_TOOLS) == \
        build_system_prompt("orchestrator", project_name="d", project_root="/d", tools=MAIN_TOOLS)
    try:
        build_system_prompt("wizard", project_name="d", project_root="/d", tools=[])
        raise AssertionError("unknown role must be rejected")
    except ValueError:
        pass

    # the policy AO's prompts carry survives the adaptation
    full = build_system_prompt("orchestrator", project_name="d", project_root="/d", tools=MAIN_TOOLS)
    assert "Never ever make code changes directly in the orchestrator session." in full
    assert "20 characters or fewer" in full
    assert "built-in subagent" in full
    assert "only a human moves it past that" in full
    worker = build_system_prompt("worker", project_name="d", project_root="/d", tools=WORKER_TOOLS)
    assert "expect a human to decide same-workspace conflicts, not you" in worker
    assert "target 'main'" in worker
    assert "own branch" in worker
    reviewer = build_system_prompt("reviewer", project_name="d", project_root="/d", tools=REVIEWER_TOOLS)
    assert "do not claim" in reviewer.lower() or "do not edit" in reviewer.lower()
    chat = build_system_prompt("chat", project_name="d", project_root="/d", tools=[])
    assert "Available Tools" not in chat
    assert "standing instructions" in TRUST_BOUNDARY
    assert set(prompts.ALIASES) <= set(KNOWN_TOOLS)
    assert all(a in KNOWN_TOOLS for alts in prompts.ALIASES.values() for a in alts)
    print("test_prompts: ok")


if __name__ == "__main__":
    main()
