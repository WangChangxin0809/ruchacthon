# 0002 — Provider-agnostic LLM client, and why we wrote our own agent loop

Date: 2026-09-04
Status: superseded by [0003](0003-claude-code-only-harness.md) — the hand-written loop was removed on 2026-09-05

## Context

The chat agent and its subagents need to call a real model. Two constraints
collided:

1. This sandbox's shell has no usable Anthropic (or any provider's)
   credentials -- confirmed by hand: `ANTHROPIC_AUTH_TOKEN` is present but
   empty in the Bash tool's environment, and a raw `claude -p` subprocess
   answers "Not logged in". This is a deliberate isolation boundary, not a
   bug, and it does not go away by switching providers.
2. We looked at DeepSeek's own open-source agent harness (`dsh`,
   github.com/deepseek-ai/deepseek-harness) as a foundation -- it already
   has a chat UI, a plugin architecture, and an `agentTeams` subsystem with
   a task board and write-scope overlap warnings that overlaps a lot with
   what AgentRoom does. We could not adopt it directly: this hackathon's
   Fresh Build rule requires code written after Hack Start, and `dsh`'s own
   code predates it. Its ideas are fair game; its repository is not.

## Decision

Write our own minimal agent loop (`agent_loop.py`) against a small
provider-agnostic client (`llm.py`) that reads `LLM_PROVIDER`/`LLM_API_KEY`/
`LLM_MODEL`/`LLM_BASE_URL` from the environment and does nothing else until
a real key exists. Every layer above it (AgentRoom tools, the chat
endpoint, subagent spawning) is fully built and tested against a scripted
fake client (`backend/tests/test_agent_loop_fake.py`) so the mechanics are
proven without a key; only "does a real model call these tools well" is
untested, because it is the one thing this sandbox cannot test.

## Rejected

- **Depend on `deepseek-harness` directly** (as a package or a fork). Why
  not: Fresh Build. Its architecture is credited and imitated where it's
  genuinely better (the task-board-with-overlap-warnings idea shaped
  AgentRoom's `team`-scope escalation design), never copied verbatim.
- **Block on getting a real API key before writing this layer.** Why not:
  neither we nor the user had one at build time, and the hackathon's own
  model access opens "现场" (on-site) on a schedule outside our control.
  Building the plumbing now and plugging in a key later costs nothing if
  the abstraction is honest about what it can't yet prove.

## Revisit when

A real `LLM_API_KEY` becomes available: run
`backend/tests/test_agent_loop_fake.py`'s scenarios again against the real
`LLMClient.chat()` (not the fake), confirm a real model actually chooses
to call `room_claim` before editing and `submit_preview` when it finishes,
and update `scripts/demo/run_two_agents.py`'s successor (real subagents via
`/api/subagents`) to replace the scripted MCP-client demo as the primary
one.
