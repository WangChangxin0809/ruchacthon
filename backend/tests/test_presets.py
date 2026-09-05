#!/usr/bin/env python3
"""Plain-assert checks for backend/app/provider_presets.py.

Run: python3 backend/tests/test_presets.py

The entry count is pinned to the cc-switch source this table was
transliterated from (src/config/claudeProviderPresets.ts, 1814 lines, 88
entries). Point CC_SWITCH_PRESETS_TS at a checkout of that file to recount it
from source instead of trusting the constant.
"""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import provider_presets as pp  # noqa: E402

EXPECTED_COUNT = 88


def expected_count() -> int:
    path = os.environ.get("CC_SWITCH_PRESETS_TS")
    if not path or not os.path.exists(path):
        return EXPECTED_COUNT
    src = open(path, encoding="utf-8").read()
    body = src[src.index("export const providerPresets"):]
    # every entry is an object literal opened at exactly two-space indent
    return len(re.findall(r"^  \{\s*$", body, flags=re.M))


def main() -> None:
    presets = pp.PRESETS
    assert len(presets) == expected_count(), (len(presets), expected_count())
    assert len({p.name for p in presets}) == len(presets), "names must be unique"
    assert pp.by_name("claude official") is presets[0] and presets[0].is_official
    assert pp.by_name("nope") is None

    cats = pp.categories()
    assert sum(cats.values()) == len(presets)
    assert set(cats) <= {"official", "cn_official", "cloud_provider", "aggregator", "third_party", "custom"}

    affiliate = {"aff", "ref", "invitecode", "from", "ic", "ac", "rc", "ytag", "code", "source", "ch"}
    for p in presets:
        for url in (p.website_url, p.api_key_url, *p.endpoint_candidates):
            for k, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True):
                assert k not in affiliate and not k.startswith("utm_"), (p.name, url)
        for k, v in p.env.items():
            assert isinstance(k, str) and isinstance(v, str), (p.name, k, v)
        assert "ANTHROPIC_SMALL_FAST_MODEL" not in p.env, p.name
        assert not ("ANTHROPIC_AUTH_TOKEN" in p.env and "ANTHROPIC_API_KEY" in p.env), p.name
        assert p.api_key_field in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"), p.name
        assert p.api_format in ("anthropic", "openai_chat", "openai_responses", "gemini_native"), p.name
        if p.requires_oauth:
            assert p.provider_type in pp.OAUTH_PROVIDER_TYPES, p.name

    # routing: official never, non-anthropic formats and OAuth types always
    assert not pp.needs_routing(pp.by_name("Claude Official"))
    assert not pp.needs_routing(pp.by_name("DeepSeek"))
    for p in presets:
        if p.api_format == "openai_chat":
            assert pp.needs_routing(p), p.name
    assert pp.needs_routing(pp.by_name("Nvidia")) and pp.by_name("Nvidia").api_format == "openai_chat"
    assert pp.needs_routing(pp.by_name("GitHub Copilot")) and pp.needs_routing(pp.by_name("Codex"))
    assert pp.needs_routing(pp.by_name("DeepSeek"), is_full_url=True)

    # template substitution
    kat = pp.by_name("KAT-Coder")
    env = pp.apply_template_values(kat.env, {"ENDPOINT_ID": "ep-123"})
    assert env["ANTHROPIC_BASE_URL"].endswith("/endpoints/ep-123/claude-code-proxy"), env["ANTHROPIC_BASE_URL"]
    assert "${" not in env["ANTHROPIC_BASE_URL"]
    aws = pp.by_name("AWS Bedrock (API Key)")
    env = pp.apply_template_values(aws.env, pp.template_defaults(aws))
    assert env["ANTHROPIC_BASE_URL"] == "https://bedrock-runtime.us-west-2.amazonaws.com" and env["AWS_REGION"] == "us-west-2"
    assert pp.apply_template_values({"a": ["${X}", {"b": "${X}${Y}"}]}, {"X": "1"}) == {"a": ["1", {"b": "1${Y}"}]}

    # model role map + mapper with the [1m] marker
    rm = pp.model_role_map({"ANTHROPIC_MODEL": "d", "ANTHROPIC_DEFAULT_HAIKU_MODEL": "h",
                            "ANTHROPIC_DEFAULT_SONNET_MODEL": "s", "ANTHROPIC_DEFAULT_OPUS_MODEL": "o",
                            "ANTHROPIC_DEFAULT_FABLE_MODEL": ""})
    assert rm == {"haiku": "h", "sonnet": "s", "opus": "o", "fable": None, "default": "d"}
    assert pp.model_role_map({}) == {"haiku": None, "sonnet": None, "opus": None, "fable": None, "default": None}
    assert pp.map_model(rm, "claude-haiku-4-5") == "h"
    assert pp.map_model(rm, "claude-sonnet-5[1M]") == "s"
    assert pp.map_model(rm, "claude-fable-5") == "o", "fable falls back to the opus slot"
    assert pp.map_model(rm, "gpt-x") == "d"
    assert pp.map_model(pp.model_role_map({}), "claude-opus-5 [1m]") == "claude-opus-5"
    assert pp.map_model({"default": "up[1M]"}, "anything") == "up"
    assert pp.strip_one_m_suffix("m[1m]") == "m" and pp.strip_one_m_suffix("m") == "m"

    # suggestions from key prefixes (placeholders, not credentials)
    assert pp.suggest_preset("sk-ant-placeholder", None) == "Claude Official"
    assert pp.suggest_preset("nvapi-placeholder", None) == "Nvidia"
    assert pp.suggest_preset("sk-or-placeholder", None) == "OpenRouter"
    assert pp.suggest_preset("AIzaPlaceholder", None) == "Gemini Native"
    assert pp.suggest_preset("xai-placeholder", None) == "xAI (Grok)"
    assert pp.suggest_preset("gsk_placeholder", None) is None and pp.detect_key_vendor("gsk_placeholder") == "groq"
    assert pp.suggest_preset("csk-placeholder", None) is None and pp.detect_key_vendor("csk-placeholder") == "cerebras"
    assert pp.suggest_preset("", "") is None and pp.suggest_preset(None, None) is None
    # base-URL host beats key prefix; longest path wins on a shared host
    assert pp.suggest_preset("sk-ant-placeholder", "https://api.deepseek.com/anthropic") == "DeepSeek"
    assert pp.suggest_preset(None, "https://ark.cn-beijing.volces.com/api/coding/") == "火山 Coding Plan"
    assert pp.suggest_preset(None, "https://ark.cn-beijing.volces.com/api/plan") == "火山 Agent Plan"
    assert pp.suggest_preset(None, "https://cf.api.fan") == "PackyCode", "endpoint candidates count"
    assert pp.suggest_preset(None, "https://example.invalid/v1") is None

    # models_url_candidates order (model_fetch.rs)
    assert pp.models_url_candidates("https://open.bigmodel.cn/api/anthropic/") == [
        "https://open.bigmodel.cn/api/anthropic/v1/models",
        "https://open.bigmodel.cn/v1/models",
        "https://open.bigmodel.cn/models",
    ]
    assert pp.models_url_candidates("https://integrate.api.nvidia.com/v1") == ["https://integrate.api.nvidia.com/v1/models"]
    assert pp.models_url_candidates("https://open.bigmodel.cn/api/coding/paas/v4") == [
        "https://open.bigmodel.cn/api/coding/paas/v4/models",
        "https://open.bigmodel.cn/api/coding/paas/v4/v1/models",
    ]
    assert pp.models_url_candidates("https://x.example.com") == ["https://x.example.com/v1/models"]
    assert pp.models_url_candidates("https://x.example.com/v1/messages", is_full_url=True) == ["https://x.example.com/v1/models"]
    assert pp.models_url_candidates("ignored", pp.by_name("DeepSeek")) == ["https://api.deepseek.com/models"]
    for bad in ("", "   "):
        try:
            pp.models_url_candidates(bad)
            raise AssertionError("empty base must raise")
        except ValueError:
            pass

    # coding plan detection (codingPlanProviders.ts)
    assert pp.detect_coding_plan("https://api.kimi.com/coding/") == "kimi"
    assert pp.detect_coding_plan("https://open.bigmodel.cn/api/anthropic") == "zhipu"
    assert pp.detect_coding_plan("https://api.minimaxi.com/anthropic") == "minimax"
    assert pp.detect_coding_plan("https://ark.cn-beijing.volces.com/api/plan") == "volcengine"
    assert pp.detect_coding_plan("https://opencode.ai/zen/go") == "opencode_go"
    assert pp.detect_coding_plan("https://opencode.ai/zen/v1") is None
    assert pp.detect_coding_plan(None) is None

    # to_dict is JSON-shaped and carries the routing verdict
    d = pp.by_name("Nvidia").to_dict()
    assert d["needs_routing"] is True and d["base_url"] == "https://integrate.api.nvidia.com"

    print(f"ok: {len(presets)} presets, {cats}, {sum(pp.needs_routing(p) for p in presets)} need routing")


if __name__ == "__main__":
    main()
