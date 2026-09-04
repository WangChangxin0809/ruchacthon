"""Provider-agnostic chat client. Not tied to Anthropic or DeepSeek by
construction -- pick a provider with an env var, plug in whichever key is
on hand when one shows up, and the agent loop above this never changes.

    LLM_PROVIDER=anthropic|openai   (openai covers DeepSeek/GPT: OpenAI-wire-compatible)
    LLM_API_KEY=...
    LLM_MODEL=...                   (defaults per provider below)
    LLM_BASE_URL=...                (optional override, e.g. DeepSeek's endpoint)

No key configured is a normal, expected state during this hackathon (see
docs/decisions/0002-llm-client-abstraction.md) -- callers see a clear
NotConfiguredError instead of a crash, so the rest of the app (AgentRoom,
the dashboard, the Gate) keeps working without one.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


class NotConfiguredError(RuntimeError):
    """No LLM_API_KEY is set. Raised lazily, only when a chat is attempted."""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema, `{"type": "object", "properties": {...}}`


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None


DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "deepseek-chat",
}
DEFAULT_BASE_URLS = {
    "openai": "https://api.deepseek.com",
}
# Fall back to whatever's already exported for that provider's own SDK/CLI --
# `LLM_API_KEY` is this app's own name, but someone who already has
# `ANTHROPIC_API_KEY` set for Claude Code's API-key auth mode, or
# `DEEPSEEK_API_KEY`/`OPENAI_API_KEY` for another tool, shouldn't have to
# export a second copy of the same secret under a new name.
# Deliberately NOT supported: reading Claude Code's own OAuth/subscription
# session out of the OS keychain. That credential is scoped to the Claude
# Code app itself; pulling it out to drive a separate app's API calls is a
# different use of it, not a config convenience, and it's also not portable
# (macOS Keychain vs. Windows Credential Manager vs. Linux secret-service).
FALLBACK_ENV_VARS = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["DEEPSEEK_API_KEY", "OPENAI_API_KEY"],
}


class LLMClient:
    def __init__(self, provider: str | None = None, api_key: str | None = None,
                 model: str | None = None, base_url: str | None = None):
        self.provider = provider or os.environ.get("LLM_PROVIDER", "openai")
        self.api_key = api_key or os.environ.get("LLM_API_KEY") or self._fallback_key()
        self.model = model or os.environ.get("LLM_MODEL") or DEFAULT_MODELS.get(self.provider, "")
        self.base_url = base_url or os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URLS.get(self.provider)

    def _fallback_key(self) -> str:
        for name in FALLBACK_ENV_VARS.get(self.provider, []):
            value = os.environ.get(name)
            if value:
                return value
        return ""

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def chat(self, messages: list[dict], tools: list[ToolSpec] | None = None,
                    system: str = "") -> ChatResult:
        if not self.is_configured():
            raise NotConfiguredError(
                f"LLM_API_KEY is not set (provider={self.provider}). "
                "Set LLM_PROVIDER + LLM_API_KEY (+ optionally LLM_MODEL/LLM_BASE_URL) "
                "once a real key is available -- everything upstream of this call "
                "already works without one."
            )
        if self.provider == "anthropic":
            return await self._chat_anthropic(messages, tools, system)
        return await self._chat_openai(messages, tools, system)

    async def _chat_anthropic(self, messages, tools, system) -> ChatResult:
        import anthropic  # imported lazily: not a hard dependency until used

        client = anthropic.AsyncAnthropic(api_key=self.api_key, base_url=self.base_url)
        kwargs: dict = dict(model=self.model, max_tokens=4096, messages=messages)
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        resp = await client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=b.input)
            for b in resp.content if b.type == "tool_use"
        ]
        return ChatResult(text=text, tool_calls=calls, raw=resp)

    async def _chat_openai(self, messages, tools, system) -> ChatResult:
        import openai  # imported lazily; DeepSeek/GPT both speak this wire format

        client = openai.AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        full_messages = ([{"role": "system", "content": system}] if system else []) + messages
        kwargs: dict = dict(model=self.model, messages=full_messages)
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": {
                    "name": t.name, "description": t.description, "parameters": t.parameters,
                }}
                for t in tools
            ]
        resp = await client.chat.completions.create(**kwargs)
        choice = resp.choices[0].message
        calls = [
            ToolCall(id=c.id, name=c.function.name,
                     arguments=_safe_json(c.function.arguments))
            for c in (choice.tool_calls or [])
        ]
        return ChatResult(text=choice.content or "", tool_calls=calls, raw=resp)


def _safe_json(s: str) -> dict:
    import json
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return {}
