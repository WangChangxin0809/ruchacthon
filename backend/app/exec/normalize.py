"""Turn a claude_agent_sdk Message into our event envelope's (type, payload)
pairs. One code path for both executors: fake_cc yields the SAME sdk
dataclasses (AssistantMessage/UserMessage/ResultMessage/...) that the real
`claude` subprocess's stream-json gets parsed into, so this module can't
silently diverge between the two.
"""
from __future__ import annotations

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

# terminal_reason/subtype substrings that mean "ran out of turns or money",
# not "crashed" -- D3 requires these stay a distinct outcome from failed.
_BUDGET_MARKERS = ("max_turn", "max_budget", "budget")


def message_to_events(msg) -> list[tuple[str, dict]]:
    if isinstance(msg, AssistantMessage):
        events = []
        for block in msg.content:
            if isinstance(block, TextBlock):
                events.append(("run.output.text", {"text": block.text}))
            elif isinstance(block, ThinkingBlock):
                events.append(("run.output.thinking", {"text": block.thinking}))
            elif isinstance(block, ToolUseBlock):
                events.append(("run.tool.started",
                                {"tool_use_id": block.id, "name": block.name, "input": block.input}))
        return events
    if isinstance(msg, UserMessage):
        content = msg.content if isinstance(msg.content, list) else []
        return [
            ("run.tool.result", {"tool_use_id": b.tool_use_id, "content": b.content, "is_error": bool(b.is_error)})
            for b in content if isinstance(b, ToolResultBlock)
        ]
    return []


def is_result(msg) -> bool:
    return isinstance(msg, ResultMessage)


def result_status(msg: ResultMessage) -> str:
    subtype = (msg.subtype or "").lower()
    terminal_reason = (msg.terminal_reason or "").lower()
    if any(m in subtype or m in terminal_reason for m in _BUDGET_MARKERS):
        return "budget_exhausted"
    if msg.is_error:
        return "failed"
    return "succeeded"


def result_payload(msg: ResultMessage) -> dict:
    return {
        "subtype": msg.subtype,
        "is_error": msg.is_error,
        "num_turns": msg.num_turns,
        "terminal_reason": msg.terminal_reason,
        "result": msg.result,
        "session_id": msg.session_id,
    }
