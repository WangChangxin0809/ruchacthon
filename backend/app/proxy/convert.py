"""Anthropic Messages <-> OpenAI Chat Completions conversion.

Pure functions, no I/O. Ported from cc-switch `proxy/providers/transform.rs`
(itself derived from anthropic-proxy-rs); function names mirror the Rust so
the port can be audited side by side. Deviations are marked `PORT NOTE`.
"""
from __future__ import annotations

import json
import re
from typing import Any

ANTHROPIC_BILLING_HEADER_PREFIX = "x-anthropic-billing-header:"
ONE_M_CONTEXT_MARKER = "[1m]"
# Vendors whose chat API wants `reasoning_content` echoed back on assistant
# tool-call turns (DeepSeek / Xiaomi MiMo reject it when missing).
REASONING_VENDOR_HINTS = ("deepseek", "mimo", "xiaomimimo")
TOOL_RESULT_MEDIA_MOVED_MARKER = "[cc-workbench: tool result media moved to the following user message]"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def canonical_json_string(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def strip_leading_anthropic_billing_header(text: str) -> str:
    """Drop only a *leading* Claude Code attribution line from system text.

    Its rotating `cch=` value would change the prompt prefix on every request
    and defeat upstream prefix caching; later occurrences are user text.
    """
    if not text.startswith(ANTHROPIC_BILLING_HEADER_PREFIX):
        return text
    m = re.search(r"\r\n|\r|\n", text)
    if not m:
        return ""
    rest = text[m.end():]
    for sep in ("\r\n", "\n", "\r"):
        if rest.startswith(sep):
            return rest[len(sep):]
    return rest


def is_openai_o_series(model: str) -> bool:
    return len(model) > 1 and model[0] == "o" and model[1].isdigit()


def supports_reasoning_effort(model: str) -> bool:
    m = model.lower()
    if is_openai_o_series(m):
        return True
    if m.startswith("gpt-") and len(m) > 4 and m[4].isdigit() and m[4] >= "5":
        return True
    return m == "grok-4.5" or m.startswith("grok-4.5-") or m.startswith("grok-build-")


def resolve_reasoning_effort(body: dict) -> str | None:
    effort = (body.get("output_config") or {}).get("effort") if isinstance(body.get("output_config"), dict) else None
    if isinstance(effort, str):
        # PORT NOTE: cc-switch has no "xhigh" (Claude Code >= 2.1.259 sends it
        # as its default) and returns None on an unknown value; here an unknown
        # value falls through to the `thinking` estimate instead.
        mapped = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "xhigh"}.get(effort)
        if mapped:
            return mapped
    thinking = body.get("thinking")
    if not isinstance(thinking, dict):
        return None
    ttype = thinking.get("type")
    if ttype == "adaptive":
        return "xhigh"
    if ttype == "enabled":
        budget = thinking.get("budget_tokens")
        if not isinstance(budget, int):
            return "high"
        return "low" if budget < 4_000 else ("medium" if budget < 16_000 else "high")
    return None


def is_reasoning_vendor_identifier(value: str | None) -> bool:
    v = (value or "").lower()
    return any(h in v for h in REASONING_VENDOR_HINTS)


def strip_one_m_suffix_for_upstream(model: str) -> str:
    trimmed = model.rstrip()
    if trimmed.lower().endswith(ONE_M_CONTEXT_MARKER):
        return trimmed[: -len(ONE_M_CONTEXT_MARKER)].rstrip()
    return model


def map_model(model: str, model_map: dict[str, str] | None) -> str:
    """Resolve the upstream model name.

    Precedence mirrors cc-switch ModelMapping: exact key, then family keys
    (fable > haiku > opus > sonnet, matched as substrings), then `*`.
    Unmapped models only lose the local `[1m]` context marker.
    """
    model_map = model_map or {}
    if model in model_map:
        return model_map[model]
    lower = model.lower()
    if "fable" in lower:
        # A route configured with only the opus/sonnet/haiku trio must still
        # catch the fable tier: Claude Code's own classifier degrades fable->opus.
        for family in ("fable", "opus"):
            if family in model_map:
                return model_map[family]
    for family in ("haiku", "opus", "sonnet"):
        if family in lower and family in model_map:
            return model_map[family]
    if lower.endswith(ONE_M_CONTEXT_MARKER) and strip_one_m_suffix_for_upstream(model) in model_map:
        return model_map[strip_one_m_suffix_for_upstream(model)]
    if "*" in model_map:
        return model_map["*"]
    return strip_one_m_suffix_for_upstream(model)


# ---------------------------------------------------------------------------
# Anthropic request -> OpenAI Chat request
# ---------------------------------------------------------------------------
def anthropic_to_openai_request(body: dict, model_map: dict[str, str] | None = None, *,
                                preserve_reasoning_content: bool = False) -> dict:
    result: dict[str, Any] = {}
    requested = body.get("model")
    model = map_model(requested, model_map) if isinstance(requested, str) else ""
    if model:
        result["model"] = model

    messages: list[dict] = []
    system = body.get("system")
    if isinstance(system, str):
        text = strip_leading_anthropic_billing_header(system)
        if text:
            messages.append({"role": "system", "content": text})
    elif isinstance(system, list):
        # One merged system message: byte-stable across turns, cache_control dropped.
        parts = []
        for block in system:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                text = strip_leading_anthropic_billing_header(block["text"])
                if text:
                    parts.append(text)
        if parts:
            messages.append({"role": "system", "content": "\n".join(parts)})

    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") if isinstance(msg.get("role"), str) else "user"
        messages.extend(convert_message_to_openai(role, msg.get("content"), preserve_reasoning_content))
    result["messages"] = messages

    if "max_tokens" in body:
        result["max_completion_tokens" if is_openai_o_series(model) else "max_tokens"] = body["max_tokens"]
    for src, dst in (("temperature", "temperature"), ("top_p", "top_p"), ("stop_sequences", "stop"), ("stream", "stream")):
        if src in body:
            result[dst] = body[src]

    if supports_reasoning_effort(model):
        effort = resolve_reasoning_effort(body)
        if effort:
            result["reasoning_effort"] = effort
    response_format = map_output_format(body.get("output_config"))
    if response_format is not None:
        result["response_format"] = response_format

    tools = body.get("tools")
    if isinstance(tools, list):
        openai_tools = [
            {"type": "function",
             "function": {"name": t.get("name") or "",
                          "description": t.get("description"),
                          "parameters": clean_schema(t.get("input_schema") if isinstance(t.get("input_schema"), dict) else {})}}
            for t in tools
            if isinstance(t, dict) and t.get("type") != "BatchTool"
        ]
        if openai_tools:
            result["tools"] = openai_tools

    if "tool_choice" in body:
        result["tool_choice"] = map_tool_choice_to_chat(body["tool_choice"])

    inject_openai_stream_include_usage(result)
    return result


def map_output_format(output_config: Any) -> dict | None:
    """`output_config.format` (json_schema) -> OpenAI `response_format`.

    PORT NOTE: cc-switch drops it. Claude Code's session-title request relies
    on it and parses the reply as JSON, so it is forwarded (verified accepted
    by NVIDIA NIM); vendors without structured output answer 400 for that one
    non-essential call rather than prose the client cannot parse.
    """
    if not isinstance(output_config, dict):
        return None
    fmt = output_config.get("format")
    if not isinstance(fmt, dict) or fmt.get("type") != "json_schema" or not isinstance(fmt.get("schema"), dict):
        return None
    return {"type": "json_schema", "json_schema": {"name": "output", "schema": fmt["schema"]}}


def inject_openai_stream_include_usage(result: dict) -> None:
    # Without include_usage most OpenAI-compatible upstreams omit the usage
    # chunk, and the stream would report 0 tokens.
    if result.get("stream") is not True:
        return
    opts = result.get("stream_options")
    if isinstance(opts, dict):
        opts["include_usage"] = True
    else:
        result["stream_options"] = {"include_usage": True}


def map_tool_choice_to_chat(tool_choice: Any) -> Any:
    if isinstance(tool_choice, str):
        return "required" if tool_choice == "any" else tool_choice
    if isinstance(tool_choice, dict):
        t = tool_choice.get("type")
        if t == "any":
            return "required"
        if t in ("auto", "none"):
            return t
        if t == "tool":
            return {"type": "function", "function": {"name": tool_choice.get("name") or ""}}
    return tool_choice


def _chat_image_part(block: dict) -> dict | None:
    """Anthropic/OpenAI image block -> OpenAI `image_url` part (images only)."""
    btype = block.get("type")
    if btype in ("image_url", "input_image"):
        iu = block.get("image_url")
        if isinstance(iu, str) and iu.strip():
            return {"type": "image_url", "image_url": {"url": iu}}
        if isinstance(iu, dict) and isinstance(iu.get("url"), str) and iu["url"].strip():
            return {"type": "image_url", "image_url": dict(iu)}
        return None
    if btype != "image":
        return None
    source = block.get("source")
    if not isinstance(source, dict):
        # MCP image shape: {type:"image", data, mimeType} with no `source`.
        data = block.get("data")
        media_type = block.get("mimeType") or block.get("mime_type")
        if isinstance(data, str) and data and isinstance(media_type, str) and media_type.lower().startswith("image/"):
            return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}}
        return None
    media_type = source.get("media_type") or source.get("mime_type") or source.get("mimeType")
    if isinstance(media_type, str) and media_type and not media_type.lower().startswith("image/"):
        return None
    url = source.get("url")
    if isinstance(url, str) and url.strip():
        return {"type": "image_url", "image_url": {"url": url}}
    data = source.get("data")
    if isinstance(data, str) and data:
        if data[:5].lower() == "data:":
            return {"type": "image_url", "image_url": {"url": data}}
        return {"type": "image_url", "image_url": {"url": f"data:{media_type or 'image/png'};base64,{data}"}}
    return None


def _plan_chat_tool_output_media(content: Any) -> tuple[str, list[dict]] | None:
    """Chat `tool` messages are text-only: pull image blocks out of a
    tool_result array, leaving a marker in their place.

    PORT NOTE: cc-switch also digs into nested JSON strings and whole-string
    data URLs; only top-level blocks of the array are handled here.
    """
    if not isinstance(content, list):
        return None
    media: list[dict] = []
    replaced: list[Any] = []
    for block in content:
        part = _chat_image_part(block) if isinstance(block, dict) else None
        if part is None:
            replaced.append(block)
        else:
            media.append(part)
            replaced.append({"type": "text", "text": TOOL_RESULT_MEDIA_MOVED_MARKER})
    if not media:
        return None
    return canonical_json_string(replaced), media


def convert_message_to_openai(role: str, content: Any, preserve_reasoning_content: bool = False) -> list[dict]:
    """One Anthropic message -> zero or more OpenAI messages."""
    if content is None:
        return [{"role": role, "content": None}]
    if isinstance(content, str):
        return [{"role": role, "content": content}]
    if not isinstance(content, list):
        return [{"role": role, "content": content}]

    result: list[dict] = []
    content_parts: list[dict] = []
    tool_calls: list[dict] = []
    pending_tool_media: list[dict] = []
    reasoning_parts: list[str] = []

    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            if isinstance(block.get("text"), str):
                content_parts.append({"type": "text", "text": block["text"]})
        elif btype == "image":
            part = _chat_image_part(block)
            if part is not None:
                content_parts.append(part)
        elif btype == "tool_use":
            inp = block.get("input")
            tool_calls.append({"id": block.get("id") or "", "type": "function",
                               "function": {"name": block.get("name") or "",
                                            "arguments": canonical_json_string(inp if inp is not None else {})}})
        elif btype == "tool_result":
            tool_use_id = block.get("tool_use_id") or ""
            cval = block.get("content")
            plan = _plan_chat_tool_output_media(cval)
            if plan is not None:
                content_str, media_parts = plan
                pending_tool_media.append({"type": "text", "text": f"[cc-workbench: media output of tool call {tool_use_id}]"})
                pending_tool_media.extend(media_parts)
            elif isinstance(cval, str):
                content_str = cval
            elif cval is None:
                content_str = ""
            else:
                # Array content is kept as canonical JSON, exactly like the
                # legacy converter, so prompt prefixes stay byte-stable.
                content_str = canonical_json_string(cval)
            result.append({"role": "tool", "tool_call_id": tool_use_id, "content": content_str})
        elif btype == "thinking":
            if isinstance(block.get("thinking"), str) and block["thinking"]:
                reasoning_parts.append(block["thinking"])
        elif btype == "redacted_thinking" and preserve_reasoning_content:
            reasoning_parts.append("[redacted thinking]")

    # Parallel tool results stay adjacent; their media follows in one user turn
    # placed before any ordinary content from the same Anthropic turn.
    if pending_tool_media:
        result.append({"role": "user", "content": pending_tool_media})

    if content_parts or tool_calls:
        msg: dict[str, Any] = {"role": role}
        if not content_parts:
            msg["content"] = None
        elif len(content_parts) == 1 and "text" in content_parts[0]:
            msg["content"] = content_parts[0]["text"]
        else:
            msg["content"] = content_parts
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if preserve_reasoning_content and role == "assistant" and tool_calls:
            msg["reasoning_content"] = "\n".join(reasoning_parts) if reasoning_parts else "tool call"
        result.append(msg)
    return result


def clean_schema(schema: Any) -> Any:
    return _clean_schema_inner(schema, True)


def _clean_schema_inner(schema: Any, is_root: bool) -> Any:
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    missing_type = is_root and "type" not in out
    if missing_type:
        out["type"] = "object"
        out.setdefault("properties", {})
    if out.get("format") == "uri":
        del out["format"]
    props = out.get("properties")
    if isinstance(props, dict):
        out["properties"] = {k: _clean_schema_inner(v, False) for k, v in props.items()}
    if "items" in out:
        out["items"] = _clean_schema_inner(out["items"], False)
    return out


# ---------------------------------------------------------------------------
# OpenAI response -> Anthropic response
# ---------------------------------------------------------------------------
class TransformError(ValueError):
    pass


def map_stop_reason(finish_reason: str | None) -> str | None:
    if finish_reason is None:
        return None
    return {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use",
            "function_call": "tool_use", "content_filter": "end_turn"}.get(finish_reason, "end_turn")


def _as_int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def build_anthropic_usage(usage: Any) -> dict:
    """OpenAI usage -> Anthropic usage.

    OpenAI `prompt_tokens` includes cached tokens, Anthropic `input_tokens`
    does not, so cache buckets are subtracted to keep
    input + cache_read + cache_creation == prompt_tokens.
    """
    if not isinstance(usage, dict):
        usage = {}
    details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    input_details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}
    cached = _as_int(usage.get("cache_read_input_tokens"))
    if cached is None:
        cached = _as_int(details.get("cached_tokens")) or 0
    cache_creation = _as_int(usage.get("cache_creation_input_tokens"))
    if cache_creation is None:
        cache_creation = _as_int(details.get("cache_write_tokens")) or _as_int(input_details.get("cache_write_tokens")) or 0
    prompt = _as_int(usage.get("prompt_tokens")) or 0
    out = {"input_tokens": max(0, prompt - cached - cache_creation),
           "output_tokens": _as_int(usage.get("completion_tokens")) or 0}
    if cached > 0:
        out["cache_read_input_tokens"] = cached
    if cache_creation > 0:
        out["cache_creation_input_tokens"] = cache_creation
    return out


def _parse_arguments(args: Any) -> Any:
    if isinstance(args, str):
        try:
            return json.loads(args) if args.strip() else {}
        except ValueError:
            return {}
    if isinstance(args, (dict, list)):
        return args
    return {}


def openai_to_anthropic_response(resp: dict, requested_model: str | None = None) -> dict:
    """PORT NOTE: `model` echoes the alias Claude Code asked for (cc-switch
    reports the upstream id) so its model display and cost bucket stay stable
    whichever upstream a route points at; the streaming path does the same."""
    choices = resp.get("choices")
    if not isinstance(choices, list):
        raise TransformError("No choices in response")
    if not choices:
        raise TransformError("Empty choices array")
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message")
    if not isinstance(message, dict):
        raise TransformError("No message in choice")

    content: list[dict] = []
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        content.append({"type": "thinking", "thinking": reasoning})

    mc = message.get("content")
    if isinstance(mc, str):
        if mc:
            content.append({"type": "text", "text": mc})
    elif isinstance(mc, list):
        for part in mc:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype in ("text", "output_text") and isinstance(part.get("text"), str) and part["text"]:
                content.append({"type": "text", "text": part["text"]})
            elif ptype == "refusal" and isinstance(part.get("refusal"), str) and part["refusal"]:
                content.append({"type": "text", "text": part["refusal"]})
    if isinstance(message.get("refusal"), str) and message["refusal"]:
        content.append({"type": "text", "text": message["refusal"]})

    has_tool_use = False
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            func = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            content.append({"type": "tool_use", "id": tc.get("id") or "", "name": func.get("name") or "",
                            "input": _parse_arguments(func.get("arguments", "{}"))})
            has_tool_use = True
    if not has_tool_use and isinstance(message.get("function_call"), dict):
        fc = message["function_call"]
        name = fc.get("name") or ""
        if name or "arguments" in fc:
            content.append({"type": "tool_use", "id": fc.get("id") or "", "name": name,
                            "input": _parse_arguments(fc.get("arguments"))})
            has_tool_use = True

    finish = choice.get("finish_reason")
    stop_reason = map_stop_reason(finish) if isinstance(finish, str) else ("tool_use" if has_tool_use else None)

    return {"id": resp.get("id") or "", "type": "message", "role": "assistant", "content": content,
            "model": requested_model or resp.get("model") or "", "stop_reason": stop_reason,
            "stop_sequence": None, "usage": build_anthropic_usage(resp.get("usage"))}


# ---------------------------------------------------------------------------
# errors and token estimates
# ---------------------------------------------------------------------------
_ERROR_TYPES = {400: "invalid_request_error", 401: "authentication_error", 403: "permission_error",
                404: "not_found_error", 413: "request_too_large", 429: "rate_limit_error",
                500: "api_error", 529: "overloaded_error"}


def anthropic_error_type(status: int) -> str:
    if status in _ERROR_TYPES:
        return _ERROR_TYPES[status]
    return "api_error" if status >= 500 else "invalid_request_error"


def anthropic_error(status: int, message: str, error_type: str | None = None) -> dict:
    return {"type": "error", "error": {"type": error_type or anthropic_error_type(status), "message": message}}


def upstream_error_to_anthropic(status: int, body_text: str | bytes | None) -> tuple[int, dict]:
    """Map an upstream HTTP error to (status, Anthropic error body).

    PORT NOTE: cc-switch passes a JSON upstream body through untouched; Claude
    Code only renders `{type:'error', error:{type,message}}`, so the message is
    extracted and rewrapped instead.
    """
    if isinstance(body_text, bytes):
        body_text = body_text.decode("utf-8", errors="replace")
    text = (body_text or "").strip()
    message = text
    try:
        obj = json.loads(text) if text else None
    except ValueError:
        obj = None
    if isinstance(obj, dict):
        err = obj.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            message = err["message"]
        elif isinstance(err, str):
            message = err
        else:
            for key in ("message", "detail", "msg"):
                if isinstance(obj.get(key), str):
                    message = obj[key]
                    break
    if not message:
        message = f"Upstream error (status {status})"
    if status < 400:
        status = 502
    return status, anthropic_error(status, f"upstream {status}: {message[:2000]}")


def estimate_input_tokens(body: dict) -> int:
    """Rough chars/4 estimate; cc-switch has no count_tokens route at all."""
    parts = [body.get("system"), body.get("messages"), body.get("tools")]
    text = json.dumps([p for p in parts if p is not None], ensure_ascii=False)
    return max(1, len(text) // 4)
