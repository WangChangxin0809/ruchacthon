"""The real execution path: spawn an actual `claude` subprocess through the
official claude_agent_sdk (D1). This sandbox has no credentials, so here
`query()` reliably returns a ResultMessage with is_error=True and
terminal_reason="api_error" -- that is the correct, honest outcome to
record as a `failed` run, not something to work around.

Every option is injected per-run (D5): nothing here writes to
~/.claude/settings.json or reads the user's login credentials.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

from claude_agent_sdk import ClaudeAgentOptions, Message, query


async def run(*, run: dict, prompt: str, options: dict | None = None) -> AsyncIterator[Message]:
    options = options or {}
    sdk_options = ClaudeAgentOptions(
        cwd=options.get("workspace_path") or ".",
        env=options.get("env") or {},
        settings=json.dumps(options["settings_json"]) if options.get("settings_json") else None,
        model=options.get("model"),
        permission_mode=options.get("permission_mode", "acceptEdits"),
        tools=options.get("tools"),
        max_turns=options.get("max_turns"),
        max_budget_usd=options.get("max_budget_usd"),
        session_id=options.get("session_id"),
        resume=options.get("resume"),
        fork_session=options.get("fork_session", False),
        mcp_servers=options.get("mcp_servers") or {},
        strict_mcp_config=options.get("strict_mcp_config", False),
        include_partial_messages=True,
        extra_args=options.get("extra_args") or {},
    )
    async for msg in query(prompt=prompt, options=sdk_options):
        yield msg
