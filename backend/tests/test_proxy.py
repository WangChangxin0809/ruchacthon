#!/usr/bin/env python3
"""Offline proof of the Anthropic<->OpenAI proxy: request conversion,
non-stream response conversion, SSE conversion from recorded OpenAI chunks,
and the FastAPI router against a fake upstream. No network.

Run: python3 backend/tests/test_proxy.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.proxy import convert, router as proxy_router, routes, set_client  # noqa: E402
from app.proxy.streaming import anthropic_message_to_sse, create_anthropic_sse_stream, parse_anthropic_sse  # noqa: E402

TOOL = {"name": "get_weather", "description": "Get weather", "input_schema": {"type": "object", "properties": {"location": {"type": "string", "format": "uri"}}, "required": ["location"]}}


def test_request_conversion() -> None:
    a2o = convert.anthropic_to_openai_request
    # system string, billing header stripped, params, tools, model map wildcard
    r = a2o({"model": "claude-sonnet-4-5", "max_tokens": 100, "temperature": 0.3, "top_p": 0.9, "stop_sequences": ["END"],
             "system": "x-anthropic-billing-header: cc_version=2; cch=abc;\n\nBe terse.",
             "metadata": {"user_id": "u"}, "thinking": {"type": "enabled", "budget_tokens": 1024},
             "messages": [{"role": "user", "content": "hi"}], "tools": [TOOL], "tool_choice": {"type": "auto"}},
            {"*": "vendor/model"})
    assert r["model"] == "vendor/model"
    assert r["messages"][0] == {"role": "system", "content": "Be terse."}
    assert r["messages"][1] == {"role": "user", "content": "hi"}
    assert r["max_tokens"] == 100 and r["temperature"] == 0.3 and r["top_p"] == 0.9 and r["stop"] == ["END"]
    assert "metadata" not in r and "thinking" not in r and "stream_options" not in r and "reasoning_effort" not in r
    assert r["tools"] == [{"type": "function", "function": {"name": "get_weather", "description": "Get weather",
                           "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}}]
    assert r["tool_choice"] == "auto"

    # system array with cache_control merges into one system message; cache_control never leaks
    r = a2o({"model": "m", "system": [{"type": "text", "text": "A", "cache_control": {"type": "ephemeral"}},
                                       {"type": "text", "text": "x-anthropic-billing-header: cch=1;\n", "cache_control": {"type": "ephemeral"}},
                                       {"type": "text", "text": "B"}],
             "messages": [{"role": "user", "content": [{"type": "text", "text": "q", "cache_control": {"type": "ephemeral"}}]}]}, {})
    assert r["messages"][0] == {"role": "system", "content": "A\nB"}
    assert r["messages"][1] == {"role": "user", "content": "q"}
    assert "cache_control" not in json.dumps(r)
    # mid-conversation system messages stay in place
    r = a2o({"model": "m", "system": "S", "messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                                                        {"role": "system", "content": "<t>"}, {"role": "user", "content": "c"}]}, {})
    assert [m["role"] for m in r["messages"]] == ["system", "user", "assistant", "system", "user"]

    # assistant text + tool_use -> content string + tool_calls with canonical JSON args
    r = a2o({"model": "m", "messages": [{"role": "assistant", "content": [
        {"type": "text", "text": "Let me check"},
        {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"z": 1, "location": "Tokyo"}}]}]}, {})
    assert r["messages"] == [{"role": "assistant", "content": "Let me check", "tool_calls": [
        {"id": "toolu_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"location":"Tokyo","z":1}'}}]}]
    assert "reasoning_content" not in r["messages"][0]
    # tool_use only -> content null; thinking-only assistant message vanishes
    r = a2o({"model": "m", "messages": [{"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "n", "input": {}}]},
                                        {"role": "assistant", "content": [{"type": "thinking", "thinking": "hm"}]}]}, {})
    assert len(r["messages"]) == 1 and r["messages"][0]["content"] is None
    # DeepSeek-style vendors get reasoning_content on tool-call turns
    r = a2o({"model": "m", "messages": [{"role": "assistant", "content": [{"type": "thinking", "thinking": "I should call"},
                                                                          {"type": "tool_use", "id": "t", "name": "n", "input": {}}]}]}, {}, preserve_reasoning_content=True)
    assert r["messages"][0]["reasoning_content"] == "I should call"
    r = a2o({"model": "m", "messages": [{"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "n", "input": {}}]}]}, {}, preserve_reasoning_content=True)
    assert r["messages"][0]["reasoning_content"] == "tool call"

    # tool_result: string, array content (canonical JSON), is_error, consecutive results, trailing user text
    r = a2o({"model": "m", "messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "Sunny"},
        {"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "plain"}], "is_error": True},
        {"type": "tool_result", "tool_use_id": "t3"},
        {"type": "text", "text": "and now?"}]}]}, {})
    assert r["messages"] == [{"role": "tool", "tool_call_id": "t1", "content": "Sunny"},
                             {"role": "tool", "tool_call_id": "t2", "content": '[{"text":"plain","type":"text"}]'},
                             {"role": "tool", "tool_call_id": "t3", "content": ""},
                             {"role": "user", "content": "and now?"}]
    # images: base64 -> data URL, url source, non-image media dropped; tool_result images moved to a user turn
    r = a2o({"model": "m", "messages": [{"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}, "cache_control": {"type": "ephemeral"}},
        {"type": "image", "source": {"type": "url", "url": "https://x/y.png"}},
        {"type": "image", "source": {"type": "base64", "media_type": "application/pdf", "data": "BBBB"}}]}]}, {})
    assert r["messages"][0]["content"] == [{"type": "text", "text": "look"},
                                           {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                                           {"type": "image_url", "image_url": {"url": "https://x/y.png"}}]
    r = a2o({"model": "m", "messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": [
        {"type": "text", "text": "cap"}, {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "SENTINEL"}}]}]}]}, {})
    assert r["messages"][0]["role"] == "tool" and "SENTINEL" not in r["messages"][0]["content"] and "moved" in r["messages"][0]["content"]
    assert r["messages"][1]["role"] == "user" and r["messages"][1]["content"][1]["image_url"]["url"] == "data:image/png;base64,SENTINEL"

    # tool_choice variants, BatchTool filtered, schema defaults
    assert convert.map_tool_choice_to_chat({"type": "any"}) == "required"
    assert convert.map_tool_choice_to_chat("any") == "required"
    assert convert.map_tool_choice_to_chat({"type": "none"}) == "none"
    assert convert.map_tool_choice_to_chat({"type": "tool", "name": "get_weather"}) == {"type": "function", "function": {"name": "get_weather"}}
    r = a2o({"model": "m", "messages": [], "tools": [{"type": "BatchTool", "name": "b"}, {"name": "x", "input_schema": {}}]}, {})
    assert r["tools"][0]["function"]["parameters"] == {"type": "object", "properties": {}}
    assert convert.clean_schema({"properties": {"n": {"anyOf": [{"type": "string"}, {"type": "null"}]}, "l": {"items": {"format": "uri"}}}}) == \
        {"type": "object", "properties": {"n": {"anyOf": [{"type": "string"}, {"type": "null"}]}, "l": {"items": {}}}}

    # max_tokens -> max_completion_tokens on o-series; reasoning_effort; stream_options injected
    r = a2o({"model": "o3-mini", "max_tokens": 5, "stream": True, "thinking": {"type": "enabled", "budget_tokens": 20000}, "messages": []}, {})
    assert r["max_completion_tokens"] == 5 and "max_tokens" not in r and r["reasoning_effort"] == "high"
    assert r["stream"] is True and r["stream_options"] == {"include_usage": True}
    assert convert.resolve_reasoning_effort({"output_config": {"effort": "max"}, "thinking": {"type": "enabled"}}) == "xhigh"
    assert convert.resolve_reasoning_effort({"thinking": {"type": "adaptive"}}) == "xhigh"
    assert convert.resolve_reasoning_effort({"thinking": {"type": "enabled", "budget_tokens": 100}}) == "low"
    assert convert.resolve_reasoning_effort({"thinking": {"type": "disabled"}}) is None
    assert convert.supports_reasoning_effort("gpt-5.4") and not convert.supports_reasoning_effort("gpt-4o")
    # Claude Code >= 2.1.259 default effort "xhigh"; an unknown value falls back to the thinking estimate
    assert convert.resolve_reasoning_effort({"output_config": {"effort": "xhigh"}, "thinking": {"type": "adaptive"}}) == "xhigh"
    assert convert.resolve_reasoning_effort({"output_config": {"effort": "turbo"}, "thinking": {"type": "enabled", "budget_tokens": 5000}}) == "medium"
    assert convert.resolve_reasoning_effort({"output_config": {"effort": "turbo"}}) is None
    r = a2o({"model": "gpt-5.4", "output_config": {"effort": "xhigh"}, "thinking": {"type": "adaptive", "display": "omitted"}, "messages": []}, {})
    assert r["reasoning_effort"] == "xhigh"
    # output_config.format json_schema -> response_format; absent otherwise
    schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"], "additionalProperties": False}
    r = a2o({"model": "m", "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": schema}}, "messages": []}, {})
    assert r["response_format"] == {"type": "json_schema", "json_schema": {"name": "output", "schema": schema}}
    assert "output_config" not in r
    r = a2o({"model": "m", "output_config": {"effort": "high"}, "messages": []}, {})
    assert "response_format" not in r
    assert convert.map_output_format({"format": {"type": "text"}}) is None

    # MCP-shaped image (no `source`): converted in user content, moved out of tool_result; non-image mime dropped
    mcp = {"type": "image", "mimeType": "image/webp", "data": "ZGVm"}
    assert convert._chat_image_part(mcp) == {"type": "image_url", "image_url": {"url": "data:image/webp;base64,ZGVm"}}
    assert convert._chat_image_part({"type": "image", "mimeType": "application/pdf", "data": "ZGVm"}) is None
    assert convert._chat_image_part({"type": "image", "mimeType": "image/webp", "data": ""}) is None
    r = a2o({"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "see"}, mcp]}]}, {})
    assert r["messages"][0]["content"][1]["image_url"]["url"] == "data:image/webp;base64,ZGVm"
    r = a2o({"model": "m", "messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": [mcp]}]}]}, {})
    assert r["messages"][0]["role"] == "tool" and "ZGVm" not in r["messages"][0]["content"]
    assert r["messages"][1]["content"][1]["image_url"]["url"] == "data:image/webp;base64,ZGVm"

    # model mapping precedence
    mm = {"claude-sonnet-4-5": "exact", "sonnet": "fam", "haiku": "h", "*": "star"}
    assert convert.map_model("claude-sonnet-4-5", mm) == "exact"
    assert convert.map_model("claude-sonnet-4-5-20250929", mm) == "fam"
    assert convert.map_model("claude-haiku-4-5", mm) == "h"
    assert convert.map_model("claude-opus-4", mm) == "star"
    assert convert.map_model("claude-opus-4[1m]", {}) == "claude-opus-4"
    # fable falls back to the opus tier before the wildcard (cc-switch model_mapper.rs)
    assert convert.map_model("claude-fable-5", {"opus": "o"}) == "o"
    assert convert.map_model("claude-fable-5[1m]", {"opus": "o", "sonnet": "s", "*": "star"}) == "o"
    assert convert.map_model("claude-fable-5", {"fable": "f", "opus": "o"}) == "f"
    assert convert.map_model("claude-fable-5", {"sonnet": "s", "*": "star"}) == "star"
    assert convert.map_model("claude-fable-5", {"sonnet": "s"}) == "claude-fable-5"
    assert convert.strip_leading_anthropic_billing_header("Keep:\nx-anthropic-billing-header: e") == "Keep:\nx-anthropic-billing-header: e"
    assert convert.strip_leading_anthropic_billing_header("x-anthropic-billing-header: a\r\n\r\nP") == "P"


def test_response_conversion() -> None:
    o2a = convert.openai_to_anthropic_response
    m = o2a({"id": "chatcmpl-1", "model": "vendor/x", "choices": [{"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 3}}, "claude-sonnet-4-5")
    assert m == {"id": "chatcmpl-1", "type": "message", "role": "assistant", "content": [{"type": "text", "text": "Hello"}],
                 "model": "claude-sonnet-4-5", "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 3}}
    m = o2a({"id": "c", "choices": [{"message": {"content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"location": "Tokyo"}'}}]}, "finish_reason": "tool_calls"}]})
    assert m["content"] == [{"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"location": "Tokyo"}}]
    assert m["stop_reason"] == "tool_use" and m["model"] == "" and m["usage"] == {"input_tokens": 0, "output_tokens": 0}
    m = o2a({"id": "c", "choices": [{"message": {"content": "long", "reasoning_content": "thought"}, "finish_reason": "length"}],
             "usage": {"prompt_tokens": 100, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 40}}})
    assert m["content"] == [{"type": "thinking", "thinking": "thought"}, {"type": "text", "text": "long"}]
    assert m["stop_reason"] == "max_tokens" and m["usage"] == {"input_tokens": 60, "output_tokens": 7, "cache_read_input_tokens": 40}
    assert convert.build_anthropic_usage({"prompt_tokens": 5, "cache_read_input_tokens": 9}) == {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 9}
    m = o2a({"choices": [{"message": {"content": [{"type": "text", "text": "a"}, {"type": "refusal", "refusal": "no"}]}, "finish_reason": "content_filter"}]})
    assert m["content"] == [{"type": "text", "text": "a"}, {"type": "text", "text": "no"}] and m["stop_reason"] == "end_turn"
    m = o2a({"choices": [{"message": {"content": "", "tool_calls": [{"id": "x", "function": {"name": "f", "arguments": "not json"}}]}}]})
    assert m["content"] == [{"type": "tool_use", "id": "x", "name": "f", "input": {}}] and m["stop_reason"] == "tool_use"
    for bad in ({}, {"choices": []}, {"choices": [{}]}):
        try:
            o2a(bad)
            raise AssertionError("expected TransformError")
        except convert.TransformError:
            pass
    assert convert.upstream_error_to_anthropic(401, '{"error": {"message": "bad key", "type": "x"}}') == \
        (401, {"type": "error", "error": {"type": "authentication_error", "message": "upstream 401: bad key"}})
    assert convert.upstream_error_to_anthropic(429, "slow down")[1]["error"]["type"] == "rate_limit_error"
    assert convert.upstream_error_to_anthropic(503, '{"detail": "busy"}')[1]["error"] == {"type": "api_error", "message": "upstream 503: busy"}
    assert convert.estimate_input_tokens({"system": "abcd" * 10, "messages": []}) >= 10


def chunk(delta: dict, finish: str | None = None, usage: dict | None = None, **extra) -> str:
    d = {"id": "chatcmpl-9", "model": "vendor/x", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage is not None:
        d["usage"] = usage
    d.update(extra)
    return "data: " + json.dumps(d) + "\n\n"


async def collect(chunks: list[str], model: str | None = "claude-sonnet-4-5", split: int | None = None) -> list[dict]:
    raw = "".join(chunks).encode()
    pieces = [raw] if split is None else [raw[i:i + split] for i in range(0, len(raw), split)]

    async def upstream():
        for p in pieces:
            yield p
    out = b""
    async for b in create_anthropic_sse_stream(upstream(), model):
        out += b
    return parse_anthropic_sse(out)


def types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


def test_streaming_conversion() -> None:
    # text only, usage in last (choice-less) chunk, [DONE]
    ev = asyncio.run(collect([chunk({"role": "assistant", "content": ""}), chunk({"content": "Hel"}), chunk({"content": "lo"}),
                              chunk({}, "stop"), 'data: {"id":"chatcmpl-9","choices":[],"usage":{"prompt_tokens":12,"completion_tokens":2}}\n\n',
                              "data: [DONE]\n\n"]))
    assert types(ev) == ["message_start", "content_block_start", "content_block_delta", "content_block_delta", "content_block_stop", "message_delta", "message_stop"], types(ev)
    assert ev[0]["message"] == {"id": "chatcmpl-9", "type": "message", "role": "assistant", "content": [], "model": "claude-sonnet-4-5",
                                "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}
    assert ev[1]["content_block"] == {"type": "text", "text": ""} and ev[1]["index"] == 0
    assert [e["delta"]["text"] for e in ev[2:4]] == ["Hel", "lo"]
    assert ev[5]["delta"] == {"stop_reason": "end_turn", "stop_sequence": None} and ev[5]["usage"] == {"input_tokens": 12, "output_tokens": 2}

    # a tool call across several chunks (id+name first, args fragmented), finish tool_calls, usage on finish chunk, no [DONE]
    ev = asyncio.run(collect([
        chunk({"tool_calls": [{"index": 0, "id": "call_a", "type": "function", "function": {"name": "get_weather", "arguments": ""}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"loc'}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'ation": "To'}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'kyo"}'}}]}),
        chunk({}, "tool_calls", usage={"prompt_tokens": 30, "completion_tokens": 9})], split=7))
    assert types(ev) == ["message_start", "content_block_start", "content_block_delta", "content_block_delta", "content_block_delta", "content_block_stop", "message_delta", "message_stop"], types(ev)
    assert ev[1]["content_block"] == {"type": "tool_use", "id": "call_a", "name": "get_weather", "input": {}}
    assert json.loads("".join(e["delta"]["partial_json"] for e in ev if e["type"] == "content_block_delta")) == {"location": "Tokyo"}
    assert all(e["delta"]["type"] == "input_json_delta" for e in ev if e["type"] == "content_block_delta")
    assert ev[6]["delta"]["stop_reason"] == "tool_use" and ev[6]["usage"] == {"input_tokens": 30, "output_tokens": 9}

    # text then two interleaved tool calls routed by index; args before id/name are buffered; duplicate finish chunks -> one message_delta
    ev = asyncio.run(collect([
        chunk({"content": "Checking"}),
        chunk({"tool_calls": [{"index": 0, "id": "c0", "function": {"name": "first"}}, {"index": 1, "function": {"arguments": '{"b":'}}]}),
        chunk({"tool_calls": [{"index": 1, "id": "c1", "function": {"name": "second", "arguments": "2}"}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"a":1}'}}]}),
        chunk({}, "tool_calls"), chunk({}, "tool_calls", usage={"prompt_tokens": 3, "completion_tokens": 4}), "data: [DONE]\n\n"]))
    assert types(ev) == ["message_start", "content_block_start", "content_block_delta", "content_block_stop",
                         "content_block_start", "content_block_start", "content_block_delta", "content_block_delta", "content_block_delta",
                         "content_block_stop", "content_block_stop", "message_delta", "message_stop"], types(ev)
    assert ev[1]["content_block"]["type"] == "text" and ev[1]["index"] == 0
    starts = {e["content_block"]["id"]: e["index"] for e in ev if e["type"] == "content_block_start" and e["content_block"]["type"] == "tool_use"}
    assert starts == {"c0": 1, "c1": 2}
    args = {}
    for e in ev:
        if e["type"] == "content_block_delta" and e["delta"]["type"] == "input_json_delta":
            args[e["index"]] = args.get(e["index"], "") + e["delta"]["partial_json"]
    assert json.loads(args[1]) == {"a": 1} and json.loads(args[2]) == {"b": 2}
    assert [e["index"] for e in ev if e["type"] == "content_block_stop"] == [0, 1, 2]
    assert ev[11]["usage"] == {"input_tokens": 3, "output_tokens": 4}

    # reasoning -> thinking block, then text; finish length -> max_tokens; multibyte split across chunks
    ev = asyncio.run(collect([chunk({"reasoning_content": "hmm"}), chunk({"reasoning": " more"}), chunk({"content": "你好"}), chunk({}, "length"), "data: [DONE]\n\n"], split=5))
    assert types(ev) == ["message_start", "content_block_start", "content_block_delta", "content_block_delta", "content_block_stop",
                         "content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"], types(ev)
    assert ev[1]["content_block"] == {"type": "thinking", "thinking": ""} and ev[2]["delta"] == {"type": "thinking_delta", "thinking": "hmm"}
    assert ev[6]["delta"] == {"type": "text_delta", "text": "你好"} and ev[8]["delta"]["stop_reason"] == "max_tokens"

    # tool call whose id/name never arrive gets a late start; stream without finish_reason emits no terminal events
    ev = asyncio.run(collect([chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}), chunk({}, "tool_calls"), "data: [DONE]\n\n"]))
    assert ev[1]["content_block"] == {"type": "tool_use", "id": "tool_call_0", "name": "unknown_tool", "input": {}}
    ev = asyncio.run(collect([chunk({"content": "partial"})]))
    assert types(ev) == ["message_start", "content_block_start", "content_block_delta"]
    # in-band error payload -> error event
    ev = asyncio.run(collect([chunk({"content": "x"}), 'data: {"error": {"message": "quota"}}\n\n']))
    assert types(ev)[-1] == "error" and ev[-1]["error"]["message"] == "quota"
    # U+2028 / U+0085 raw inside a JSON string must not split the SSE line (str.splitlines would)
    ev = asyncio.run(collect([chunk({"content": "A"}), chunk({"content": "\u2028B\u0085C"}, "stop"), "data: [DONE]\n\n"]))
    assert types(ev) == ["message_start", "content_block_start", "content_block_delta", "content_block_delta", "content_block_stop", "message_delta", "message_stop"], types(ev)
    assert ev[3]["delta"]["text"] == "\u2028B\u0085C" and ev[5]["delta"]["stop_reason"] == "end_turn"
    ev = asyncio.run(collect([chunk({"tool_calls": [{"index": 0, "id": "c", "function": {"name": "Write", "arguments": '{"t":"x\u2028y"}'}}]}, "tool_calls"), "data: [DONE]\n\n"]))
    assert types(ev) == ["message_start", "content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"], types(ev)
    assert json.loads(ev[2]["delta"]["partial_json"]) == {"t": "x\u2028y"}
    # CRLF line endings are still fine
    ev = asyncio.run(collect([chunk({"content": "crlf"}).replace("\n\n", "\r\n\r\n"), chunk({}, "stop").replace("\n\n", "\r\n\r\n"), "data: [DONE]\r\n\r\n"]))
    assert types(ev) == ["message_start", "content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"], types(ev)

    # two whole tool calls without `index` in one delta -> two blocks, keyed by position
    ev = asyncio.run(collect([chunk({"tool_calls": [{"id": "call_A", "function": {"name": "get_weather", "arguments": '{"city":"Tokyo"}'}},
                                                    {"id": "call_B", "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'}}]}, "tool_calls"), "data: [DONE]\n\n"]))
    starts = [e["content_block"]["id"] for e in ev if e["type"] == "content_block_start"]
    assert starts == ["call_A", "call_B"], types(ev)
    args = {e["index"]: e["delta"]["partial_json"] for e in ev if e["type"] == "content_block_delta"}
    assert json.loads(args[0]) == {"city": "Tokyo"} and json.loads(args[1]) == {"city": "Paris"}
    assert [e["index"] for e in ev if e["type"] == "content_block_stop"] == [0, 1]

    # a lone-surrogate escape from upstream degrades to a JSON escape, the stream survives
    ev = asyncio.run(collect([chunk({"content": "ok"}), 'data: {"id":"chatcmpl-9","choices":[{"index":0,"delta":{"content":"\\ud83d"},"finish_reason":null}]}\n\n',
                              chunk({}, "stop"), "data: [DONE]\n\n"]))
    assert types(ev) == ["message_start", "content_block_start", "content_block_delta", "content_block_delta", "content_block_stop", "message_delta", "message_stop"], types(ev)
    assert ev[3]["delta"]["text"] == "\ud83d"
    from app.proxy.streaming import sse_event
    assert sse_event("x", {"t": "\ud83d"}) == b'event: x\ndata: {"t": "\\ud83d"}\n\n'

    # synthetic replay of a complete message
    ev = parse_anthropic_sse(b"".join(anthropic_message_to_sse({"id": "m", "type": "message", "role": "assistant", "model": "x", "stop_reason": "tool_use",
                                                                  "usage": {"input_tokens": 1, "output_tokens": 2},
                                                                  "content": [{"type": "text", "text": "t"}, {"type": "tool_use", "id": "i", "name": "n", "input": {"k": 1}}]})))
    assert types(ev) == ["message_start", "content_block_start", "content_block_delta", "content_block_stop", "content_block_start",
                         "content_block_delta", "content_block_stop", "message_delta", "message_stop"]
    assert json.loads(ev[5]["delta"]["partial_json"]) == {"k": 1}


def test_router() -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        if request.url.path.endswith("/chat/completions") and body.get("model") == "vendor/boom":
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        if body.get("stream"):
            sse = chunk({"content": "Hi "}) + chunk({"content": "there"}) + chunk({}, "stop", usage={"prompt_tokens": 4, "completion_tokens": 2}) + "data: [DONE]\n\n"
            if body["messages"][-1]["content"] == "sse-as-text":
                return httpx.Response(200, headers={"content-type": "text/plain"}, content=sse.encode())
            if body["messages"][-1]["content"] == "json-ignores-stream":
                return httpx.Response(200, headers={"content-type": "application/octet-stream"},
                                      content=json.dumps({"id": "c", "choices": [{"message": {"content": "whole"}, "finish_reason": "stop"}]}).encode())
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())
        return httpx.Response(200, json={"id": "chatcmpl-n", "model": body["model"], "choices": [{"message": {"content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"location":"Tokyo"}'}}]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 3}})

    set_client(httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    routes.register("tok-1", "https://upstream.test/v1", "sk-secret-value", {"*": "vendor/model"}, {"x-extra": "1"})
    routes.register("tok-boom", "https://upstream.test/v1/", "sk-secret-value", {"*": "vendor/boom"})
    app = FastAPI()
    app.include_router(proxy_router)
    c = TestClient(app)
    req = {"model": "claude-sonnet-4-5", "max_tokens": 50, "system": [{"type": "text", "text": "S", "cache_control": {"type": "ephemeral"}}],
           "messages": [{"role": "user", "content": "weather?"}], "tools": [TOOL], "metadata": {"user_id": "u"}}

    # auth: unknown token in path, mismatched header token, then all three accepted forms
    assert c.post("/proxy/nope/v1/messages", json=req).status_code == 401
    r = c.post("/proxy/tok-1/v1/messages", json=req, headers={"authorization": "Bearer other"})
    assert r.status_code == 401 and r.json()["error"]["type"] == "authentication_error"
    for headers in ({}, {"authorization": "Bearer tok-1"}, {"x-api-key": "tok-1"}):
        r = c.post("/proxy/tok-1/v1/messages", json=req, headers=headers)
        assert r.status_code == 200, r.text
    m = r.json()
    assert m["type"] == "message" and m["model"] == "claude-sonnet-4-5" and m["stop_reason"] == "tool_use"
    assert m["content"] == [{"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"location": "Tokyo"}}]
    assert m["usage"] == {"input_tokens": 8, "output_tokens": 3}
    up = seen[-1]
    assert str(up.url) == "https://upstream.test/v1/chat/completions"
    assert up.headers["authorization"] == "Bearer sk-secret-value" and up.headers["x-extra"] == "1"
    assert "x-api-key" not in up.headers and up.headers["authorization"] != "Bearer tok-1"
    sent = json.loads(up.content)
    assert sent["model"] == "vendor/model" and sent["messages"][0] == {"role": "system", "content": "S"}
    assert "metadata" not in sent and "cache_control" not in up.content.decode() and sent["tools"][0]["function"]["name"] == "get_weather"

    # stream
    r = c.post("/proxy/tok-1/v1/messages", json={**req, "stream": True})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = parse_anthropic_sse(r.content)
    assert types(ev) == ["message_start", "content_block_start", "content_block_delta", "content_block_delta", "content_block_stop", "message_delta", "message_stop"], types(ev)
    assert "".join(e["delta"]["text"] for e in ev if e["type"] == "content_block_delta") == "Hi there"
    assert ev[0]["message"]["model"] == "claude-sonnet-4-5" and ev[5]["usage"] == {"input_tokens": 4, "output_tokens": 2}
    assert json.loads(seen[-1].content)["stream_options"] == {"include_usage": True}
    # streaming asks for identity encoding; the non-stream request keeps httpx's default
    assert seen[-1].headers["accept-encoding"] == "identity" and up.headers["accept-encoding"] != "identity"
    # SSE served with a wrong content-type is still streamed; a JSON body is replayed as SSE
    r = c.post("/proxy/tok-1/v1/messages", json={**req, "stream": True, "messages": [{"role": "user", "content": "sse-as-text"}]})
    ev = parse_anthropic_sse(r.content)
    assert r.headers["content-type"].startswith("text/event-stream") and "".join(e["delta"]["text"] for e in ev if e["type"] == "content_block_delta") == "Hi there"
    assert types(ev)[-2:] == ["message_delta", "message_stop"]
    r = c.post("/proxy/tok-1/v1/messages", json={**req, "stream": True, "messages": [{"role": "user", "content": "json-ignores-stream"}]})
    ev = parse_anthropic_sse(r.content)
    assert types(ev) == ["message_start", "content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"], types(ev)
    assert ev[2]["delta"]["text"] == "whole"
    # a lone surrogate in the client body (Claude Code truncating inside an emoji) is not a 500
    r = c.post("/proxy/tok-1/v1/messages", content=b'{"model":"claude-sonnet-5","max_tokens":10,"messages":[{"role":"user","content":"a\\ud83d"}]}',
               headers={"content-type": "application/json"})
    assert r.status_code == 200, r.text
    assert json.loads(seen[-1].content)["messages"][0]["content"] == "a\ufffd"

    # upstream error -> Anthropic error with the same status; key never echoed
    r = c.post("/proxy/tok-boom/v1/messages", json=req)
    assert r.status_code == 429 and r.json() == {"type": "error", "error": {"type": "rate_limit_error", "message": "upstream 429: rate limited"}}
    assert "sk-secret-value" not in r.text
    assert str(seen[-1].url) == "https://upstream.test/v1/chat/completions"

    # count_tokens is a local estimate; models lists the map; bad JSON is 400
    r = c.post("/proxy/tok-1/v1/messages/count_tokens", json=req)
    assert r.status_code == 200 and r.json()["input_tokens"] > 10
    assert c.get("/proxy/tok-1/v1/models").json()["data"] == []
    assert c.post("/proxy/tok-1/v1/messages", content=b"{oops", headers={"content-type": "application/json"}).status_code == 400
    assert repr(routes.get("tok-1")).count("sk-secret-value") == 0 and "tok-1" not in repr(routes.get("tok-1"))

    # anthropic passthrough: body and SSE untouched, x-api-key injected, model mapped, client credentials dropped
    def anthropic_upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/v1/messages/count_tokens" or request.url.path == "/v1/messages"
        if request.url.path.endswith("count_tokens"):
            return httpx.Response(200, json={"input_tokens": 42})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b"event: message_start\ndata: {\"type\":\"message_start\"}\n\n")

    set_client(httpx.AsyncClient(transport=httpx.MockTransport(anthropic_upstream)))
    routes.register("tok-a", "https://gw.test", "gw-key", {"sonnet": "claude-sonnet-4-5"}, api_format="anthropic")
    r = c.post("/proxy/tok-a/v1/messages", json={**req, "stream": True}, headers={"authorization": "Bearer tok-a", "anthropic-beta": "b1"})
    assert r.status_code == 200 and r.content.startswith(b"event: message_start")
    up = seen[-1]
    assert up.headers["x-api-key"] == "gw-key" and "authorization" not in up.headers and up.headers["anthropic-beta"] == "b1"
    assert up.headers["anthropic-version"] == "2023-06-01" and json.loads(up.content)["model"] == "claude-sonnet-4-5"
    assert up.headers["accept-encoding"] == "identity"
    assert c.post("/proxy/tok-a/v1/messages/count_tokens", json=req).json() == {"input_tokens": 42}
    routes.unregister("tok-a")
    assert c.post("/proxy/tok-a/v1/messages", json=req).status_code == 401
    set_client(None)


if __name__ == "__main__":
    test_request_conversion()
    test_response_conversion()
    test_streaming_conversion()
    test_router()
    print("all proxy assertions passed")
