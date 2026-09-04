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
