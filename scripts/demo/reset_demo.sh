#!/usr/bin/env bash
# Restore demo/target-app/app.py and clear AgentRoom's runtime log so
# run_two_agents.py can be replayed from a clean state.
set -euo pipefail
cd "$(dirname "$0")/../.."

cat > demo/target-app/app.py <<'PY'
"""Toy Flask-less app used only to demo two concurrent Claude Code agents
editing the same file through AgentRoom."""


def greet(name: str) -> str:
    # TODO(zhangsan): add an exclamation mark at the end of the greeting
    return f"Hello, {name}"


def farewell(name: str) -> str:
    # TODO(lisi): add a "See you soon" suffix
    return f"Bye, {name}"
PY

rm -f backend/data/room_log.jsonl backend/data/decisions.jsonl
echo "demo/target-app/app.py and backend/data/*.jsonl reset. Restart the backend to clear its in-memory escalation queue too."
