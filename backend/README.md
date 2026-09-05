# AgentRoom backend

FastAPI dashboard API + WebSocket, with the AgentRoom MCP server (5 tools,
`room_claim/release/broadcast/read/state`, plus `room_edit_text` for the
worktree-scope CRDT demo) mounted in the same process at `/mcp`.

## Run

```bash
python3 -m pip install --user --break-system-packages -r requirements.txt   # first time only
python3 -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8787
```

- Dashboard REST: `http://localhost:8787/api/...`
- Dashboard WebSocket: `ws://localhost:8787/ws`
- MCP endpoint (point a Claude Code `--mcp-config` at this): `http://localhost:8787/mcp`

If `pip` itself is missing and `apt install python3-pip` fails (no route to
Debian's mirrors, common in locked-down sandboxes): `pypi.org` is usually
still reachable, so `curl -sS -o get-pip.py https://bootstrap.pypa.io/get-pip.py
&& python3 get-pip.py --user --break-system-packages` bootstraps it directly.

## Why one process

The AgentRoom paper's premise is that concurrent agents see each other's
claims and edits immediately. Two separate processes (an MCP server per
agent, or per transport) would need to synchronize `RoomState` over IPC.
Mounting the MCP app inside the same FastAPI process means every `room_*`
tool call and every dashboard request read and write the exact same
in-memory object -- correct by construction, not by polling.

## Gate

`../scripts/gates/check_escalation_decisions.py` reads
`backend/data/room_log.jsonl` and `backend/data/decisions.jsonl`: any
`scope=team` claim conflict without a matching decision fails the gate.
That is the "no merge without a human" enforcement point.

## Chat, subagents, previews

`app/llm.py` + `app/agent_loop.py` + `app/chat.py` + `app/subagents.py` add a
real chat agent that can edit files itself or delegate to background
subagents, all sharing the same `RoomState` and AgentRoom tools as any
external MCP client. None of it needs a model key to boot -- `/api/chat`
and `/api/subagents` fail with a clear `NotConfiguredError` message until
one is set, everything else on the dashboard keeps working. Set:

```bash
export LLM_PROVIDER=openai        # or anthropic; openai covers DeepSeek/GPT (OpenAI-wire-compatible)
export LLM_API_KEY=...
export LLM_MODEL=deepseek-chat    # optional, provider has a default
export LLM_BASE_URL=https://api.deepseek.com   # optional, provider has a default for openai
```

If you already export `ANTHROPIC_API_KEY` (for `LLM_PROVIDER=anthropic`) or
`DEEPSEEK_API_KEY`/`OPENAI_API_KEY` (for `LLM_PROVIDER=openai`) for some
other tool, `LLM_API_KEY` is optional -- `llm.py` falls back to those
standard names so the same key doesn't need exporting twice. **Deliberately
not supported:** reading Claude Code's own OAuth/subscription session out
of the OS keychain. That credential is scoped to the Claude Code app
itself; pulling it out to drive a separate app's API calls is a different
use of it, not a config convenience, and it isn't portable across
platforms either. Use a real API key.

The chat agent and subagents can also read and write real files
(`app/fs_tools.py`: `read_file`/`write_file`/`list_dir`, scoped to the
project root -- a path that tries to leave it, absolute or via `..`, is
refused before touching disk). `write_file` additionally refuses unless
the caller currently holds a `room_claim` on that exact path: the claim
is the actual permission check, not a status display next to one.
`backend/tests/test_fs_tools.py` covers both the claim gate and the path
escape (including the case where an actor claims a string that happens to
look like an absolute path -- room_claim doesn't validate path shape, so
the safety has to live in `fs_tools`, not be inherited from the claim
check).

`backend/tests/test_agent_loop_fake.py` proves the loop's tool-calling
mechanics against a scripted fake model, with no key needed -- run it after
touching `agent_loop.py`, `chat.py`, or `subagents.py`. See
[docs/decisions/0002-llm-client-abstraction.md](../docs/decisions/0002-llm-client-abstraction.md)
for why this is a small hand-written loop rather than an adopted framework.

`backend/tests/test_api.py` drives the FastAPI app directly with Starlette's
`TestClient` (no server process) and covers the REST surface: status codes,
the missing-key error shape, and the claim -> escalate -> decide -> gate
round trip. Both test files run in CI (`.github/workflows/ci.yml`'s
`backend-tests` job, which installs `requirements.txt` first -- the bare
`harness` job intentionally has no project dependencies, see its comments).

Agents can also call `submit_preview(title, summary, html)` to show a human
what they made -- it lands on the dashboard's preview panel, sandboxed
(the `<iframe sandbox="">` allows no scripts) since the HTML comes from a
model.
