"""OpenAI Chat Completions SSE -> Anthropic Messages SSE.

Ported from cc-switch `proxy/providers/streaming.rs` (`create_anthropic_sse_stream`)
and `proxy/sse.rs`. Event ordering:
message_start, [content_block_start / content_block_delta / content_block_stop]*,
message_delta(stop_reason, usage), message_stop.
"""
from __future__ import annotations

import codecs
import json
import logging
import re
from typing import Any, AsyncIterator

from .convert import build_anthropic_usage, map_stop_reason

log = logging.getLogger("workbench.proxy.streaming")

# A runaway tool-call argument stream (Copilot bug) is cut after this many
# consecutive whitespace characters.
INFINITE_WHITESPACE_THRESHOLD = 500
_BLOCK_DELIMITER = re.compile(r"\r\n\r\n|\n\n")


# ---------------------------------------------------------------------------
# sse.rs
# ---------------------------------------------------------------------------
def strip_sse_field(line: str, field: str) -> str | None:
    if line.startswith(field + ": "):
        return line[len(field) + 2:]
    if line.startswith(field + ":"):
        return line[len(field) + 1:]
    return None


def sse_lines(block: str) -> list[str]:
    """Split on \n / \r\n only, like Rust `str::lines`. `str.splitlines()`
    would also break on U+2028/U+2029/U+0085, which upstreams emit raw inside
    JSON strings, and the chunk would be lost."""
    return [line[:-1] if line.endswith("\r") else line for line in block.split("\n")]


def take_sse_block(buffer: str) -> tuple[str | None, str]:
    """Split off the first complete SSE block; returns (block, remaining)."""
    m = _BLOCK_DELIMITER.search(buffer)
    if not m:
        return None, buffer
    return buffer[: m.start()], buffer[m.end():]


def sse_event(event_type: str, data: dict) -> bytes:
    # backslashreplace: a lone surrogate (an emoji split across chunks and
    # JSON-escaped per chunk) is re-emitted as the `\udXXX` escape, which the
    # JS client accepts and can rejoin, instead of aborting the stream.
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8", "backslashreplace")


# ---------------------------------------------------------------------------
# streaming.rs
# ---------------------------------------------------------------------------
class _ToolBlockState:
    __slots__ = ("anthropic_index", "id", "name", "started", "pending_args", "consecutive_whitespace", "aborted")

    def __init__(self, anthropic_index: int):
        self.anthropic_index = anthropic_index
        self.id = ""
        self.name = ""
        self.started = False
        self.pending_args = ""
        self.consecutive_whitespace = 0
        self.aborted = False


def _message_delta_event(stop_reason: str | None, usage: dict | None) -> bytes:
    return sse_event("message_delta", {"type": "message_delta",
                                       "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                                       "usage": usage if isinstance(usage, dict) else {"input_tokens": 0, "output_tokens": 0}})


def _content_block_stop(index: int) -> bytes:
    return sse_event("content_block_stop", {"type": "content_block_stop", "index": index})


def _delta_reasoning(delta: dict) -> str | None:
    # OpenRouter/Kimi use `reasoning`, DeepSeek uses `reasoning_content`.
    for key in ("reasoning", "reasoning_content"):
        v = delta.get(key)
        if isinstance(v, str):
            return v
    return None


async def create_anthropic_sse_stream(upstream: AsyncIterator[bytes], requested_model: str | None = None) -> AsyncIterator[bytes]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buffer = ""
    message_id: str | None = None
    current_model: str | None = None
    next_content_index = 0
    has_sent_message_start = False
    # Some upstreams send several finish_reason chunks; Anthropic allows exactly
    # one message_delta, so it is deduplicated and deferred to [DONE] so the
    # trailing usage chunk can still be folded in.
    has_emitted_message_delta = False
    pending_message_delta: tuple[str | None, dict | None] | None = None
    has_sent_message_stop = False
    stream_ended_with_error = False
    latest_usage: dict | None = None
    current_non_tool_block_type: str | None = None
    current_non_tool_block_index: int | None = None
    tool_blocks_by_index: dict[int, _ToolBlockState] = {}
    open_tool_block_indices: set[int] = set()

    async def handle_data(data: str):
        nonlocal buffer, message_id, current_model, next_content_index, has_sent_message_start
        nonlocal has_emitted_message_delta, pending_message_delta, has_sent_message_stop, stream_ended_with_error
        nonlocal latest_usage, current_non_tool_block_type, current_non_tool_block_index

        if data.strip() == "[DONE]":
            if pending_message_delta is not None:
                stop_reason, usage_json = pending_message_delta
                pending_message_delta = None
                yield _message_delta_event(stop_reason, usage_json)
            yield sse_event("message_stop", {"type": "message_stop"})
            has_sent_message_stop = True
            return

        try:
            chunk = json.loads(data)
        except ValueError:
            return
        if not isinstance(chunk, dict):
            return

        choices = chunk.get("choices")
        # PORT NOTE: an in-band `{"error": ...}` payload is surfaced as an
        # Anthropic error event instead of being silently ignored.
        if isinstance(chunk.get("error"), (dict, str)) and not choices:
            err = chunk["error"]
            message = err.get("message") if isinstance(err, dict) else err
            stream_ended_with_error = True
            yield sse_event("error", {"type": "error", "error": {"type": "api_error", "message": str(message or "upstream stream error")}})
            return

        if message_id is None and chunk.get("id"):
            message_id = str(chunk["id"])
        if current_model is None and chunk.get("model"):
            current_model = str(chunk["model"])

        chunk_usage_json = build_anthropic_usage(chunk["usage"]) if isinstance(chunk.get("usage"), dict) else None
        if chunk_usage_json is not None:
            latest_usage = chunk_usage_json
            if pending_message_delta is not None:
                pending_message_delta = (pending_message_delta[0], chunk_usage_json)

        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return
        choice = choices[0]
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}

        if not has_sent_message_start:
            start_usage = {"input_tokens": 0, "output_tokens": 0}
            if chunk_usage_json is not None:
                start_usage = {**chunk_usage_json, "output_tokens": 0}
            yield sse_event("message_start", {"type": "message_start", "message": {
                "id": message_id or "", "type": "message", "role": "assistant", "content": [],
                "model": requested_model or current_model or "", "stop_reason": None, "stop_sequence": None,
                "usage": start_usage}})
            has_sent_message_start = True

        reasoning = _delta_reasoning(delta)
        if reasoning is not None:
            if current_non_tool_block_type != "thinking":
                if current_non_tool_block_index is not None:
                    yield _content_block_stop(current_non_tool_block_index)
                    current_non_tool_block_index = None
                index = next_content_index
                next_content_index += 1
                yield sse_event("content_block_start", {"type": "content_block_start", "index": index,
                                                        "content_block": {"type": "thinking", "thinking": ""}})
                current_non_tool_block_type = "thinking"
                current_non_tool_block_index = index
            yield sse_event("content_block_delta", {"type": "content_block_delta", "index": current_non_tool_block_index,
                                                    "delta": {"type": "thinking_delta", "thinking": reasoning}})

        content = delta.get("content")
        if isinstance(content, str) and content:
            if current_non_tool_block_type != "text":
                if current_non_tool_block_index is not None:
                    yield _content_block_stop(current_non_tool_block_index)
                    current_non_tool_block_index = None
                index = next_content_index
                next_content_index += 1
                yield sse_event("content_block_start", {"type": "content_block_start", "index": index,
                                                        "content_block": {"type": "text", "text": ""}})
                current_non_tool_block_type = "text"
                current_non_tool_block_index = index
            yield sse_event("content_block_delta", {"type": "content_block_delta", "index": current_non_tool_block_index,
                                                    "delta": {"type": "text_delta", "text": content}})

        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            if current_non_tool_block_index is not None:
                yield _content_block_stop(current_non_tool_block_index)
                current_non_tool_block_index = None
            current_non_tool_block_type = None

            for pos, tc in enumerate(tool_calls):
                if not isinstance(tc, dict):
                    continue
                tc_index = tc["index"] if isinstance(tc.get("index"), int) else pos
                state = tool_blocks_by_index.get(tc_index)
                if state is None:
                    state = _ToolBlockState(next_content_index)
                    next_content_index += 1
                    tool_blocks_by_index[tc_index] = state
                if state.aborted:
                    continue
                if isinstance(tc.get("id"), str) and tc["id"]:
                    state.id = tc["id"]
                func = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                if isinstance(func.get("name"), str) and func["name"]:
                    state.name = func["name"]

                # The block only opens once both id and name are known;
                # argument fragments arriving earlier are buffered.
                should_start = not state.started and bool(state.id) and bool(state.name)
                if should_start:
                    state.started = True
                pending_after_start = None
                if should_start and state.pending_args:
                    pending_after_start, state.pending_args = state.pending_args, ""
                immediate_delta = None
                args = func.get("arguments")
                if isinstance(args, str):
                    for ch in args:
                        state.consecutive_whitespace = state.consecutive_whitespace + 1 if ch.isspace() else 0
                    if state.consecutive_whitespace >= INFINITE_WHITESPACE_THRESHOLD:
                        log.warning("infinite whitespace in tool-call arguments (tool %s); aborting this tool call", state.name)
                        state.aborted = True
                    elif state.started:
                        immediate_delta = args
                    else:
                        state.pending_args += args

                if should_start:
                    yield sse_event("content_block_start", {"type": "content_block_start", "index": state.anthropic_index,
                                                            "content_block": {"type": "tool_use", "id": state.id, "name": state.name, "input": {}}})
                    open_tool_block_indices.add(state.anthropic_index)
                for partial in (pending_after_start, immediate_delta):
                    if partial:
                        yield sse_event("content_block_delta", {"type": "content_block_delta", "index": state.anthropic_index,
                                                                "delta": {"type": "input_json_delta", "partial_json": partial}})

        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str):
            stop_reason = map_stop_reason(finish_reason)
            usage_json = chunk_usage_json if chunk_usage_json is not None else latest_usage
            if has_emitted_message_delta:
                if pending_message_delta is not None and usage_json is not None:
                    pending_message_delta = (pending_message_delta[0], usage_json)
                return
            has_emitted_message_delta = True

            if current_non_tool_block_index is not None:
                yield _content_block_stop(current_non_tool_block_index)
                current_non_tool_block_index = None
            current_non_tool_block_type = None

            # Late start for tool blocks whose id/name never arrived.
            late_starts = []
            for tool_idx, state in tool_blocks_by_index.items():
                if state.started or not (state.pending_args or state.id or state.name):
                    continue
                state.started = True
                pending, state.pending_args = state.pending_args, ""
                late_starts.append((state.anthropic_index, state.id or f"tool_call_{tool_idx}", state.name or "unknown_tool", pending))
            for index, tid, name, pending in sorted(late_starts):
                yield sse_event("content_block_start", {"type": "content_block_start", "index": index,
                                                        "content_block": {"type": "tool_use", "id": tid, "name": name, "input": {}}})
                open_tool_block_indices.add(index)
                if pending:
                    yield sse_event("content_block_delta", {"type": "content_block_delta", "index": index,
                                                            "delta": {"type": "input_json_delta", "partial_json": pending}})
            for index in sorted(open_tool_block_indices):
                yield _content_block_stop(index)
            open_tool_block_indices.clear()

            pending_message_delta = (stop_reason, usage_json)

    try:
        async for raw in upstream:
            buffer += decoder.decode(raw)
            while True:
                block, buffer = take_sse_block(buffer)
                if block is None:
                    break
                if not block.strip():
                    continue
                for line in sse_lines(block):
                    data = strip_sse_field(line, "data")
                    if data is None:
                        continue
                    async for out in handle_data(data):
                        yield out
                    if stream_ended_with_error:
                        return
        # A final block without a trailing blank line still counts.
        buffer += decoder.decode(b"", final=True)
        if buffer.strip():
            for line in sse_lines(buffer):
                data = strip_sse_field(line, "data")
                if data is not None:
                    async for out in handle_data(data):
                        yield out
    except Exception as e:  # noqa: BLE001 - any transport failure becomes an SSE error event
        log.error("stream error: %s", e)
        stream_ended_with_error = True
        yield sse_event("error", {"type": "error", "error": {"type": "stream_error", "message": f"Stream error: {e}"}})
        return

    # Stream ended without [DONE]: flush the deferred message_delta and close
    # the message, but only when the upstream did not already fail.
    if not stream_ended_with_error and pending_message_delta is not None:
        stop_reason, usage_json = pending_message_delta
        pending_message_delta = None
        yield _message_delta_event(stop_reason, usage_json)
        if not has_sent_message_stop:
            yield sse_event("message_stop", {"type": "message_stop"})


def anthropic_message_to_sse(message: dict) -> list[bytes]:
    """Replay a complete Anthropic message as the SSE sequence a streaming
    client expects (used when the upstream ignored `stream: true`)."""
    usage = message.get("usage") or {"input_tokens": 0, "output_tokens": 0}
    events = [sse_event("message_start", {"type": "message_start", "message": {
        **{k: message.get(k) for k in ("id", "type", "role", "model")}, "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {**usage, "output_tokens": 0}}})]
    for index, block in enumerate(message.get("content") or []):
        btype = block.get("type")
        if btype == "text":
            events.append(sse_event("content_block_start", {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}}))
            events.append(sse_event("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": block.get("text", "")}}))
        elif btype == "thinking":
            events.append(sse_event("content_block_start", {"type": "content_block_start", "index": index, "content_block": {"type": "thinking", "thinking": ""}}))
            events.append(sse_event("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "thinking_delta", "thinking": block.get("thinking", "")}}))
        elif btype == "tool_use":
            events.append(sse_event("content_block_start", {"type": "content_block_start", "index": index,
                                                            "content_block": {"type": "tool_use", "id": block.get("id", ""), "name": block.get("name", ""), "input": {}}}))
            events.append(sse_event("content_block_delta", {"type": "content_block_delta", "index": index,
                                                            "delta": {"type": "input_json_delta", "partial_json": json.dumps(block.get("input", {}), ensure_ascii=False)}}))
        else:
            continue
        events.append(_content_block_stop(index))
    events.append(_message_delta_event(message.get("stop_reason"), usage))
    events.append(sse_event("message_stop", {"type": "message_stop"}))
    return events


def parse_anthropic_sse(raw: bytes | str) -> list[dict[str, Any]]:
    """Test/diagnostic helper: decode an Anthropic SSE byte stream to event dicts."""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    events = []
    for block in _BLOCK_DELIMITER.split(text):
        for line in sse_lines(block):
            data = strip_sse_field(line, "data")
            if data is not None:
                try:
                    events.append(json.loads(data))
                except ValueError:
                    pass
    return events
