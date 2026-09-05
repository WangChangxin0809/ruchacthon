# 0003 — Claude Code is the only execution harness

Date: 2026-09-05
Status: accepted; supersedes 0002

## Context

0002 chose a hand-written model loop (`agent_loop.py` + `llm.py`) because the
build sandbox at the time had no credentials. That constraint is gone: this
machine has a logged-in Claude Code (`claude 2.1.259`) and the official Python
Agent SDK (`claude-agent-sdk 0.2.152`) installs from PyPI. Continuing to grow a
second agent runtime next to Claude Code means re-implementing tools,
permissions, hooks, MCP, sessions and compaction that CC already ships.

Probe results (scratch script, real `claude` binary, 2026-09-05):

| Capability | Result |
|---|---|
| Streaming text deltas (`include_partial_messages`) | works |
| Real tool use in a bound `cwd` (Write/Bash) | works |
| `session_id` + `resume` keeps context | works |
| `interrupt()` mid-Bash | stops in ~6 s, result `error_during_execution`, `is_error=true` |
| In-process MCP tool (`create_sdk_mcp_server`) called by the model | works |
| Per-run `env` injection | works (`echo $VAR` returned the injected value) |
| `setting_sources=[]` | **auth fails**: the OAuth token lives in `~/.claude/settings.json` `env`, so runs must load `user` settings |

## Decision

- One execution channel: the **Python Agent SDK** driving one `claude`
  subprocess per Run. The CLI's `stream-json` is what the SDK parses; going
  below it buys nothing and loses `interrupt`, in-process MCP tools and typed
  messages. Windows works because the SDK spawns the native `claude` binary —
  no tmux, no pty.
- Runs load CC's **user** settings (credentials, proxy) and, for the project,
  CC's own `project`/`local` sources so `CLAUDE.md`, `.mcp.json`, skills and
  hooks apply the way CC applies them. Provider Profiles are injected as
  per-run `env` on top; the user's global CC config is never rewritten.
- The main agent's worker-control tools and every worker's Room tools are
  **in-process SDK MCP servers bound to the Run** — identity is a closure, not
  an argument the model fills in. The old HTTP `/mcp` endpoint and
  `.mcp.json` are removed with it.
- CC's native `Agent` tool is disallowed for main and worker runs so there is
  exactly one scheduler (the server's RunManager).
- Storage is one SQLite file; events carry a monotonic `seq` so a browser can
  reconnect with `since=` and miss nothing.

## Fresh Build

0002 recorded a hackathon "Fresh Build" rule. We have not seen the rule text
and do not claim it lapsed. What this decision does: depend on the official
SDK as a package (like FastAPI), write all application code here, and copy
nothing from `deepseek-harness` or `agent-orchestrator` — ideas only, credited
in `ARCHITECTURE.md`. If the rule forbids even package dependencies on
pre-existing SDKs, the SDK is a thin layer over `claude --output-format
stream-json` and `cc_runner.py` is the one file to swap.

## Rejected

- **Keep `agent_loop.py` for the main agent, CC for workers.** Two runtimes,
  two tool surfaces, two transcripts; the main agent could not read the
  project's `CLAUDE.md` the way its workers do.
- **Drive `claude` over stdio ourselves.** Loses `interrupt`, SDK MCP servers,
  hook callbacks, and the maintained parser.
- **Adopt `deepseek-harness` as the app shell.** Its `agentTeams` is a second
  scheduler; and see Fresh Build above.

## Revisit when

The SDK's `SessionStore` API (already present) makes it worth mirroring CC
transcripts into our SQLite instead of storing our own message rows; or a
provider path other than `ANTHROPIC_BASE_URL`-style gateways needs testing.
