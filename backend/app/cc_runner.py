"""The one place a model is called: one `claude` subprocess per Run, driven
through the official Agent SDK. Everything above this file speaks in Runs
and events; nothing above it imports the SDK.

Why the SDK and not `claude --output-format stream-json` by hand: interrupt,
in-process MCP servers (how Room/worker tools get a run-bound identity), and
a parser somebody else maintains. See docs/decisions/0003.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, RateLimitEvent,
                              ResultMessage, StreamEvent, SystemMessage, TextBlock, ThinkingBlock, ToolResultBlock,
                              ToolUseBlock, UserMessage)

# CC's own subagent scheduler is switched off for every run: the server's
# RunManager is the only thing that decides what runs where.
ALWAYS_DISALLOWED = ["Agent", "Task"]


@dataclass
class RunSpec:
    run_id: str
    cwd: str
    prompt: str
    resume: str | None = None
    model: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    system_prompt_append: str = ""
    max_turns: int | None = 60
    max_budget_usd: float | None = None
    setting_sources: list[str] = field(default_factory=lambda: ["user", "project", "local"])
    permission_mode: str = "bypassPermissions"
    hooks: dict[str, list[HookMatcher]] = field(default_factory=dict)
    add_dirs: list[str] = field(default_factory=list)
    effort: str | None = None


@dataclass
class RunOutcome:
    status: str                      # succeeded | failed | cancelled | exhausted | interrupted
    subtype: str | None = None
    is_error: bool = False
    result_text: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    error: str | None = None


OnEvent = Callable[[str, dict], Awaitable[None]]


class CCRun:
    """One live execution. `start()` streams until the turn (and any
    follow-up turns queued with `send()`) completes."""

    def __init__(self, spec: RunSpec):
        self.spec = spec
        self.client: ClaudeSDKClient | None = None
        self.cc_session_id: str | None = None
        self.pid: int | None = None
        self.cancel_requested = False
        self.finished = False
        # `client` exists before connect() returns (0.5s+ with MCP servers) and
        # the SDK refuses query() until then; sends in that window wait here.
        self.ready = False
        self._pending: list[str] = []
        # Messages `send()` pushed into a turn that may have ended before the
        # model saw them; RunManager re-delivers them as a follow-up run.
        self.unconsumed: list[str] = []
        self._sends_since_output: list[str] = []
        self._stderr_tail: list[str] = []

    def _options(self) -> ClaudeAgentOptions:
        s = self.spec
        env = dict(s.env)
        # Never let a nested run think it is a subprocess of an interactive CC
        # session -- that would make it inherit that session's transport.
        for k in ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET"):
            env.setdefault(k, "")
        return ClaudeAgentOptions(
            cwd=s.cwd, model=s.model, resume=s.resume, env=env, mcp_servers=s.mcp_servers,
            allowed_tools=s.allowed_tools, disallowed_tools=[*ALWAYS_DISALLOWED, *s.disallowed_tools],
            system_prompt={"type": "preset", "preset": "claude_code", "append": s.system_prompt_append} if s.system_prompt_append else None,
            max_turns=s.max_turns, max_budget_usd=s.max_budget_usd, setting_sources=s.setting_sources,  # type: ignore[arg-type]
            permission_mode=s.permission_mode,  # type: ignore[arg-type]
            include_partial_messages=True, hooks=s.hooks or None, add_dirs=s.add_dirs,
            effort=s.effort,  # type: ignore[arg-type]
            stderr=self._on_stderr,
        )

    def _on_stderr(self, line: str) -> None:
        self._stderr_tail.append(line[:500])
        del self._stderr_tail[:-20]

    async def start(self, on_event: OnEvent) -> RunOutcome:
        self.client = ClaudeSDKClient(self._options())
        try:
            await self.client.connect()
        except Exception as e:  # noqa: BLE001 -- spawn failure is a run failure, reported not raised
            return RunOutcome(status="failed", error=f"could not start claude: {e}")
        self.pid = _pid_of(self.client)
        await self.client.query(self.spec.prompt)
        self.ready = True
        for text in self._pending:
            await self.client.query(text)
        self._pending.clear()
        outcome: RunOutcome | None = None
        try:
            async for m in self.client.receive_messages():
                done = await self._handle(m, on_event)
                if done is not None:
                    outcome = done
                    self.finished = True
                    self.unconsumed = list(self._sends_since_output)
                    break
        except Exception as e:  # noqa: BLE001
            outcome = RunOutcome(status="cancelled" if self.cancel_requested else "failed",
                                 error=f"{type(e).__name__}: {e}"[:1000])
        finally:
            await self.close()
        return outcome or RunOutcome(status="failed", error="claude exited without a result", subtype=None)

    async def _handle(self, m: Any, on_event: OnEvent) -> RunOutcome | None:
        if isinstance(m, SystemMessage):
            if m.subtype == "init":
                self.cc_session_id = m.data.get("session_id")
                await on_event("run_init", {"cc_session_id": self.cc_session_id, "model": m.data.get("model"),
                                            "tools": m.data.get("tools", []), "cwd": m.data.get("cwd"),
                                            "mcp_servers": m.data.get("mcp_servers", [])})
            elif m.subtype == "compact_boundary":
                await on_event("compact", m.data)
            return None
        if isinstance(m, StreamEvent):
            ev = m.event
            if ev.get("type") == "content_block_delta":
                d = ev.get("delta", {})
                if d.get("type") == "text_delta" and d.get("text"):
                    await on_event("stream_delta", {"text": d["text"], "index": ev.get("index")})
                elif d.get("type") == "thinking_delta" and d.get("thinking"):
                    await on_event("stream_thinking", {"text": d["thinking"], "index": ev.get("index")})
            return None
        if isinstance(m, AssistantMessage):
            self._sends_since_output.clear()
            await on_event("assistant_message", {"blocks": [_block(b) for b in m.content], "model": m.model,
                                                 "error": getattr(m, "error", None)})
            return None
        if isinstance(m, UserMessage):
            content = m.content
            blocks = [_block(b) for b in content] if isinstance(content, list) else [{"type": "text", "text": str(content)}]
            await on_event("user_message", {"blocks": blocks, "origin": "tool_result" if any(b["type"] == "tool_result" for b in blocks) else "user"})
            return None
        if isinstance(m, RateLimitEvent):
            await on_event("rate_limit", {"info": _asdict(m.rate_limit_info)})
            return None
        if isinstance(m, ResultMessage):
            return self._outcome(m)
        return None

    def _outcome(self, r: ResultMessage) -> RunOutcome:
        text = r.result
        base = dict(subtype=r.subtype, is_error=bool(r.is_error), result_text=text, cost_usd=r.total_cost_usd, num_turns=r.num_turns)
        if self.cancel_requested:
            return RunOutcome(status="cancelled", **base)
        if r.subtype == "error_max_turns":
            return RunOutcome(status="exhausted", error="max_turns reached", **base)
        if r.subtype == "error_max_budget_usd":
            return RunOutcome(status="exhausted", error="budget reached", **base)
        if r.is_error or r.subtype != "success":
            err = text or r.subtype or "unknown error"
            if self._stderr_tail:
                err = f"{err}\n" + "\n".join(self._stderr_tail[-5:])
            return RunOutcome(status="failed", error=err[:2000], **base)
        return RunOutcome(status="succeeded", **base)

    async def send(self, text: str) -> None:
        """Push a user message into the live turn (feedback, a Room decision,
        a message from the main agent). Claude Code merges it into the turn
        in progress -- verified: a worker mid-Bash saw and acted on a Room
        decision -- so there is no second ResultMessage to wait for."""
        if self.client is None or self.finished:
            raise RuntimeError("run is not live")
        self._sends_since_output.append(text)
        if not self.ready:
            self._pending.append(text)
            return
        await self.client.query(text)

    async def interrupt(self) -> None:
        self.cancel_requested = True
        if self.client is not None:
            try:
                await asyncio.wait_for(self.client.interrupt(), timeout=10)
            except Exception:  # noqa: BLE001 -- the process may already be gone; close() finishes the job
                pass

    async def close(self) -> None:
        if self.client is not None:
            try:
                await asyncio.wait_for(self.client.disconnect(), timeout=15)
            except Exception:  # noqa: BLE001
                pass
            self.client = None


def _block(b: Any) -> dict:
    if isinstance(b, TextBlock):
        return {"type": "text", "text": b.text}
    if isinstance(b, ThinkingBlock):
        return {"type": "thinking", "thinking": b.thinking}
    if isinstance(b, ToolUseBlock):
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    if isinstance(b, ToolResultBlock):
        return {"type": "tool_result", "tool_use_id": b.tool_use_id, "content": b.content, "is_error": b.is_error}
    return {"type": "unknown", "repr": repr(b)[:500]}


def _asdict(x: Any) -> Any:
    try:
        import dataclasses
        return dataclasses.asdict(x)
    except Exception:  # noqa: BLE001
        return str(x)


def _pid_of(client: ClaudeSDKClient) -> int | None:
    t = getattr(client, "_transport", None)
    p = getattr(t, "_process", None)
    pid = getattr(p, "pid", None)
    return int(pid) if isinstance(pid, int) else None


def pid_alive(pid: int | None) -> bool:
    # psutil rather than os.kill(pid, 0): on Windows os.kill with any signal
    # other than CTRL_* *terminates* the process instead of probing it.
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except ImportError:
        if os.name == "nt":
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    except Exception:  # noqa: BLE001
        return False


def kill_pid(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        import psutil
        p = psutil.Process(pid)
        for c in p.children(recursive=True):
            c.kill()
        p.kill()
        return True
    except Exception:  # noqa: BLE001
        return False
