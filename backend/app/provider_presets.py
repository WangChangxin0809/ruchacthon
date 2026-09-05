"""Claude provider presets, transliterated from cc-switch
(src/config/claudeProviderPresets.ts, 88 entries) plus the small helpers the
workbench needs around them: template substitution, the routing decision
(does a preset need the conversion proxy?), the model-role map the proxy
applies, model-list URL candidates for "test connection", coding-plan
detection, and a vendor *suggestion* from a key prefix or base-URL host.

Design source: docs/design/track3-multi-user-multi-agent.md section 5.

What is deliberately different from cc-switch:

* every env value is a str (settings.json is what Claude Code reads and it
  wants strings; cc-switch has a few bare `1`s);
* affiliate / referral query parameters are stripped from URLs;
* the legacy ANTHROPIC_SMALL_FAST_MODEL key is dropped;
* a preset listing both ANTHROPIC_AUTH_TOKEN and ANTHROPIC_API_KEY keeps only
  the one named by `api_key_field` -- a run injects exactly one credential;
* UI-only fields (icons, theme, i18n keys, promotion keys) are not carried.

Nothing here talks to a model or a network: this is data plus pure functions.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

ApiFormat = str  # 'anthropic' | 'openai_chat' | 'openai_responses' | 'gemini_native'

# cc-switch's isOAuthProviderType: credentials for these are injected by the
# proxy, so they need routing regardless of wire format.
OAUTH_PROVIDER_TYPES = frozenset({"github_copilot", "codex_oauth", "xai_oauth"})

# Longest first: `/anthropic` must not shadow `/api/anthropic` (model_fetch.rs).
KNOWN_COMPAT_SUFFIXES = (
    "/api/claudecode",
    "/api/anthropic",
    "/apps/anthropic",
    "/api/coding",
    "/claudecode",
    "/anthropic",
    "/step_plan",
    "/coding",
    "/claude",
)

ONE_M_CONTEXT_MARKER = "[1m]"

ROLE_ENV_KEYS = {
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "fable": "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "default": "ANTHROPIC_MODEL",
}

# cc-switch codingPlanProviders.ts, first match wins. zhipu_team shares
# open.bigmodel.cn with zhipu and is never auto-detected, as upstream.
CODING_PLAN_PROVIDERS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("kimi", "Kimi For Coding", re.compile(r"api\.kimi\.com/coding", re.I)),
    ("zhipu", "Zhipu GLM (智谱)", re.compile(r"bigmodel\.cn|api\.z\.ai", re.I)),
    ("minimax", "MiniMax", re.compile(r"api\.minimaxi?\.com|api\.minimax\.io", re.I)),
    ("zenmux", "ZenMux", re.compile(r"zenmux\.", re.I)),
    ("volcengine", "火山方舟 (Volcengine)", re.compile(r"volces\.com/api/(plan|coding)", re.I)),
    ("opencode_go", "OpenCode Go", re.compile(r"opencode\.ai/zen/go", re.I)),
)

# Key prefix -> (vendor id, preset name or None when cc-switch has no preset).
# A suggestion only: gateways re-issue keys in any shape, so nothing is
# validated server-side against this table.
KEY_PREFIX_VENDORS: tuple[tuple[str, str, str | None], ...] = (
    ("sk-ant-", "anthropic", "Claude Official"),
    ("nvapi-", "nvidia", "Nvidia"),
    ("sk-or-", "openrouter", "OpenRouter"),
    ("AIza", "google", "Gemini Native"),
    ("xai-", "xai", "xAI (Grok)"),
    ("gsk_", "groq", None),
    ("csk-", "cerebras", None),
)


@dataclass(frozen=True)
class ClaudePreset:
    name: str
    category: str = "custom"
    website_url: str = ""
    api_key_url: str = ""
    env: dict[str, str] = field(default_factory=dict)
    api_key_field: str = "ANTHROPIC_AUTH_TOKEN"
    api_format: ApiFormat = "anthropic"
    provider_type: str = ""
    requires_oauth: bool = False
    endpoint_candidates: list[str] = field(default_factory=list)
    template_values: dict[str, dict[str, str]] = field(default_factory=dict)
    models_url: str = ""
    is_official: bool = False
    is_partner: bool = False
    notes: str = ""

    @property
    def base_url(self) -> str:
        return self.env.get("ANTHROPIC_BASE_URL", "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "category": self.category, "website_url": self.website_url,
            "api_key_url": self.api_key_url, "env": dict(self.env), "api_key_field": self.api_key_field,
            "api_format": self.api_format, "provider_type": self.provider_type,
            "requires_oauth": self.requires_oauth, "endpoint_candidates": list(self.endpoint_candidates),
            "template_values": {k: dict(v) for k, v in self.template_values.items()},
            "models_url": self.models_url, "is_official": self.is_official, "is_partner": self.is_partner,
            "notes": self.notes, "needs_routing": needs_routing(self), "base_url": self.base_url,
        }


def by_name(name: str) -> ClaudePreset | None:
    wanted = (name or "").strip().casefold()
    for p in PRESETS:
        if p.name.casefold() == wanted:
            return p
    return None


def categories() -> dict[str, int]:
    return dict(Counter(p.category for p in PRESETS))


def apply_template_values(env: Any, values: dict[str, str] | None) -> Any:
    """cc-switch applyTemplateValues: replace `${KEY}` in every string,
    recursing into dicts and lists. A missing value substitutes ''."""
    resolved = {k: ("" if v is None else str(v)) for k, v in (values or {}).items()}

    def sub(s: str) -> str:
        for k, v in resolved.items():
            s = s.replace("${" + k + "}", v)
        return s

    def walk(o: Any) -> Any:
        if isinstance(o, str):
            return sub(o)
        if isinstance(o, list):
            return [walk(x) for x in o]
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        return o

    return walk(env)


def template_defaults(preset: ClaudePreset) -> dict[str, str]:
    return {k: v.get("default", "") for k, v in preset.template_values.items()}


def needs_routing(preset: ClaudePreset, is_full_url: bool = False) -> bool:
    """cc-switch providerNeedsRouting for appId 'claude'."""
    if preset.category == "official":
        return False
    if preset.provider_type in OAUTH_PROVIDER_TYPES:
        return True
    return is_full_url or (bool(preset.api_format) and preset.api_format != "anthropic")


def strip_one_m_suffix(model: str) -> str:
    """Claude Code marks 1M-context capability with a `[1m]` suffix that
    upstreams reject; strip it (case-insensitive) before forwarding."""
    trimmed = model.rstrip()
    if trimmed[-len(ONE_M_CONTEXT_MARKER):].lower() == ONE_M_CONTEXT_MARKER:
        return trimmed[: -len(ONE_M_CONTEXT_MARKER)].rstrip()
    return model


def model_role_map(env: dict[str, Any] | None) -> dict[str, str | None]:
    """ModelMapping::from_provider: role -> upstream model, None when unset."""
    env = env or {}
    out: dict[str, str | None] = {}
    for role, key in ROLE_ENV_KEYS.items():
        v = env.get(key)
        out[role] = v if isinstance(v, str) and v else None
    return out


def map_model(role_map: dict[str, str | None], original: str, subagent_model: str | None = None) -> str:
    """ModelMapping::map_model, then the upstream `[1m]` strip."""
    lower = original.lower()
    mapped: str | None = None
    if "fable" in lower:
        # Claude Code's own fallback direction is fable -> opus.
        mapped = role_map.get("fable") or role_map.get("opus")
    if mapped is None and "haiku" in lower:
        mapped = role_map.get("haiku")
    if mapped is None and "opus" in lower:
        mapped = role_map.get("opus")
    if mapped is None and "sonnet" in lower:
        mapped = role_map.get("sonnet")
    if mapped is None and subagent_model and strip_one_m_suffix(original) == strip_one_m_suffix(subagent_model):
        mapped = original
    if mapped is None:
        mapped = role_map.get("default") or original
    return strip_one_m_suffix(mapped)


def _ends_with_version_segment(url: str) -> bool:
    last = url.rsplit("/", 1)[-1]
    return len(last) > 1 and last[0] == "v" and last[1:].isdigit()


def _strip_compat_suffix(url: str) -> str | None:
    for suffix in KNOWN_COMPAT_SUFFIXES:
        if url.endswith(suffix):
            return url[: -len(suffix)]
    return None


def models_url_candidates(base_url: str, preset: ClaudePreset | None = None,
                          is_full_url: bool = False) -> list[str]:
    """model_fetch.rs build_models_url_candidates: ordered, de-duplicated URLs
    to try for GET .../models. Raises ValueError when nothing can be derived."""
    if preset is not None and preset.models_url.strip():
        return [preset.models_url.strip()]
    trimmed = (base_url or "").strip().rstrip("/")
    if not trimmed:
        raise ValueError("Base URL is empty")
    out: list[str] = []
    if is_full_url:
        idx = trimmed.find("/v1/")
        if idx >= 0:
            out.append(trimmed[:idx] + "/v1/models")
        elif "/" in trimmed:
            root = trimmed.rsplit("/", 1)[0]
            if "://" in root and len(root) > root.find("://") + 3:
                out.append(root + "/v1/models")
        if not out:
            raise ValueError("Cannot derive models endpoint from full URL")
        return out
    if _ends_with_version_segment(trimmed):
        out.append(trimmed + "/models")
        if not trimmed.endswith("/v1"):
            out.append(trimmed + "/v1/models")
    else:
        out.append(trimmed + "/v1/models")
    stripped = _strip_compat_suffix(trimmed)
    if stripped is not None:
        root = stripped.rstrip("/")
        if root and "://" in root:
            out.append(root + "/v1/models")
            out.append(root + "/models")
    unique: list[str] = []
    for u in out:
        if u not in unique:
            unique.append(u)
    return unique


def detect_coding_plan(base_url: str | None) -> str | None:
    if not base_url:
        return None
    for pid, _label, pat in CODING_PLAN_PROVIDERS:
        if pat.search(base_url):
            return pid
    return None


def detect_key_vendor(api_key: str | None) -> str | None:
    key = (api_key or "").strip()
    for prefix, vendor, _preset in KEY_PREFIX_VENDORS:
        if key.startswith(prefix):
            return vendor
    return None


def _host_path(url: str) -> tuple[str, str]:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return "", ""
    return p.netloc.lower(), p.path.rstrip("/")


def suggest_preset(api_key: str | None, base_url: str | None) -> str | None:
    """A suggestion for the UI, never a check: the base-URL host beats the key
    prefix because a gateway host says more than a key it merely forwards.
    Among presets on the same host the longest matching path wins."""
    host, path = _host_path(base_url or "")
    if host:
        best: tuple[int, str] | None = None
        for p in PRESETS:
            for url in [p.base_url, *p.endpoint_candidates]:
                h, pth = _host_path(url)
                if h != host or not (path == pth or path.startswith(pth + "/") or pth == ""):
                    continue
                score = len(pth)
                if best is None or score > best[0]:
                    best = (score, p.name)
        if best is not None:
            return best[1]
    for prefix, _vendor, preset_name in KEY_PREFIX_VENDORS:
        if (api_key or "").strip().startswith(prefix):
            return preset_name
    return None


PRESETS: list[ClaudePreset] = [
    ClaudePreset(
        name='Claude Official',
        category='official',
        website_url='https://www.anthropic.com/claude-code',
        env={},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_official=True,
    ),
    ClaudePreset(
        name='Kimi',
        category='cn_official',
        website_url='https://platform.kimi.com',
        env={'ANTHROPIC_BASE_URL': 'https://api.moonshot.cn/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'kimi-k2.7-code', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'kimi-k2.7-code', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'kimi-k2.7-code', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'kimi-k2.7-code'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='Kimi For Coding',
        category='cn_official',
        website_url='https://www.kimi.com/code/',
        env={'ANTHROPIC_BASE_URL': 'https://api.kimi.com/coding/', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'kimi-for-coding', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'kimi-for-coding', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'kimi-for-coding', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'kimi-for-coding', 'CLAUDE_CODE_MAX_CONTEXT_TOKENS': '262144', 'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '262144'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='PackyCode',
        category='third_party',
        website_url='https://www.packyapi.ai',
        api_key_url='https://www.packyapi.ai/register',
        env={'ANTHROPIC_BASE_URL': 'https://www.packyapi.ai', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://www.packyapi.ai', 'https://cf.api.fan', 'https://slb-v1.api.fan', 'https://www.packyapi.com'],
        is_partner=True,
    ),
    ClaudePreset(
        name='ZetaAPI',
        category='aggregator',
        website_url='https://zetaapi.ai',
        api_key_url='https://zetaapi.ai/go/u117',
        env={'ANTHROPIC_BASE_URL': 'https://api.zetaapi.ai', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='APINebula',
        category='third_party',
        website_url='https://apinebula.ai',
        api_key_url='https://apinebula.ai/VjM74M',
        env={'ANTHROPIC_BASE_URL': 'https://apinebula.ai', 'ANTHROPIC_AUTH_TOKEN': '', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://apinebula.ai'],
        is_partner=True,
    ),
    ClaudePreset(
        name='AICodeMirror',
        category='third_party',
        website_url='https://www.aicodemirror.ai',
        api_key_url='https://www.aicodemirror.ai/register',
        env={'ANTHROPIC_BASE_URL': 'https://api.aicodemirror.ai/api/claudecode', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.aicodemirror.ai/api/claudecode'],
        is_partner=True,
    ),
    ClaudePreset(
        name='PatewayAI',
        category='third_party',
        website_url='https://pateway.ai',
        api_key_url='https://pateway.ai/#/',
        env={'ANTHROPIC_BASE_URL': 'https://api.pateway.ai', 'ANTHROPIC_API_KEY': ''},
        api_key_field='ANTHROPIC_API_KEY',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='FennoAI',
        category='aggregator',
        website_url='https://api.fenno.ai',
        api_key_url='https://api.fenno.ai/register',
        env={'ANTHROPIC_BASE_URL': 'https://api.fenno.ai', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='RunAPI',
        category='aggregator',
        website_url='https://runapi.host',
        api_key_url='https://runapi.host/register',
        env={'ANTHROPIC_BASE_URL': 'https://runapi.host', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://runapi.host', 'https://runapi.co'],
        is_partner=True,
    ),
    ClaudePreset(
        name='Shengsuanyun',
        category='aggregator',
        website_url='https://www.shengsuanyun.com/',
        api_key_url='https://www.shengsuanyun.com/',
        env={'ANTHROPIC_BASE_URL': 'https://router.shengsuanyun.com/api', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'anthropic/claude-sonnet-5', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'anthropic/claude-haiku-4.5', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'anthropic/claude-sonnet-5', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'anthropic/claude-opus-5'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='AIGoCode',
        category='third_party',
        website_url='https://aigocode.app',
        api_key_url='https://aigocode.app/invite/CC-SWITCH',
        env={'ANTHROPIC_BASE_URL': 'https://api.aigocode.app', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.aigocode.app'],
        is_partner=True,
    ),
    ClaudePreset(
        name='Qiniu',
        category='aggregator',
        website_url='https://s.qiniu.com/nMvAvy',
        api_key_url='https://s.qiniu.com/nMvAvy',
        env={'ANTHROPIC_BASE_URL': 'https://api.qnaigc.com', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.qnaigc.com', 'https://api.modelink.ai'],
        is_partner=True,
    ),
    ClaudePreset(
        name='AICoding',
        category='third_party',
        website_url='https://aicoding.inc',
        api_key_url='https://aicoding.inc/i/CCSWITCH',
        env={'ANTHROPIC_BASE_URL': 'https://api.aicoding.inc', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.aicoding.inc'],
        is_partner=True,
    ),
    ClaudePreset(
        name='SubRouter',
        category='aggregator',
        website_url='https://subrouter.ai',
        api_key_url='https://subrouter.ai/register',
        env={'ANTHROPIC_BASE_URL': 'https://subrouter.ai', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='APIKEY.FUN',
        category='third_party',
        website_url='https://apikey.fun',
        api_key_url='https://apikey.fun/register',
        env={'ANTHROPIC_BASE_URL': 'https://api.apikey.fun', 'ANTHROPIC_AUTH_TOKEN': '', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.apikey.fun', 'https://slb.apikey.fun'],
        is_partner=True,
    ),
    ClaudePreset(
        name='9527CODE',
        category='aggregator',
        website_url='https://9527.codes',
        api_key_url='https://9527.codes/register',
        env={'ANTHROPIC_BASE_URL': 'https://9527.codes', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://9527.codes', 'https://api.9527.codes', 'https://cdn.9527.codes'],
        is_partner=True,
    ),
    ClaudePreset(
        name='ClaudeAPI',
        category='aggregator',
        website_url='https://www.apito.ai',
        api_key_url='https://console.apito.ai/agent/register/pQBql2buaqiX3dDS',
        env={'ANTHROPIC_BASE_URL': 'https://gw.apito.ai', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='Code0',
        category='aggregator',
        website_url='https://code0.ai',
        api_key_url='https://code0.ai/agent/register/B2XHxGjGmRvqgznY',
        env={'ANTHROPIC_BASE_URL': 'https://code0.ai', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='TeamoRouter',
        category='aggregator',
        website_url='https://teamorouter.cn',
        api_key_url='https://teamorouter.cn/',
        env={'ANTHROPIC_BASE_URL': 'https://api.teamorouter.cn', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.teamorouter.cn', 'https://api.teamorouter.com'],
        is_partner=True,
    ),
    ClaudePreset(
        name='PPIO',
        category='aggregator',
        website_url='https://ppio.com',
        api_key_url='https://ppio.com/activity/ccswitch',
        env={'ANTHROPIC_BASE_URL': 'https://api.ppio.com/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'deepseek/deepseek-v4-flash-0731', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'deepseek/deepseek-v4-flash-0731', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'deepseek/deepseek-v4-flash-0731', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'deepseek/deepseek-v4-flash-0731'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.ppio.com/anthropic'],
        is_partner=True,
    ),
    ClaudePreset(
        name='ClaudeCN',
        category='third_party',
        website_url='https://claudecn.top',
        api_key_url='https://claudecn.ai/register',
        env={'ANTHROPIC_BASE_URL': 'https://claudecn.top', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='火山 Agent Plan',
        category='cn_official',
        website_url='https://www.volcengine.com/activity/agentplan',
        api_key_url='https://www.volcengine.com/activity/agentplan',
        env={'ANTHROPIC_BASE_URL': 'https://ark.cn-beijing.volces.com/api/plan', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'ark-code-latest', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'ark-code-latest', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'ark-code-latest', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'ark-code-latest'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='火山 Coding Plan',
        category='cn_official',
        website_url='https://www.volcengine.com/activity/codingplan',
        api_key_url='https://www.volcengine.com/activity/codingplan',
        env={'ANTHROPIC_BASE_URL': 'https://ark.cn-beijing.volces.com/api/coding', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'ark-code-latest', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'ark-code-latest', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'ark-code-latest', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'ark-code-latest'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='BytePlus',
        category='cn_official',
        website_url='https://www.byteplus.com/en/product/modelark',
        api_key_url='https://www.byteplus.com/en/product/modelark',
        env={'ANTHROPIC_BASE_URL': 'https://ark.ap-southeast.bytepluses.com/api/coding', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'ark-code-latest', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'ark-code-latest', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'ark-code-latest', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'ark-code-latest'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='DouBaoSeed',
        category='cn_official',
        website_url='https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey?apikey=%7B%7D',
        api_key_url='https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey?apikey=%7B%7D',
        env={'ANTHROPIC_BASE_URL': 'https://ark.cn-beijing.volces.com/api/compatible', 'ANTHROPIC_AUTH_TOKEN': '', 'API_TIMEOUT_MS': '3000000', 'ANTHROPIC_MODEL': 'doubao-seed-2-1-pro-260628', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'doubao-seed-2-1-pro-260628', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'doubao-seed-2-1-pro-260628', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'doubao-seed-2-1-pro-260628'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='SiliconFlow',
        category='aggregator',
        website_url='https://siliconflow.cn',
        api_key_url='https://cloud.siliconflow.cn/i/YflgU2Ve',
        env={'ANTHROPIC_BASE_URL': 'https://api.siliconflow.cn', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'Pro/MiniMaxAI/MiniMax-M2.5', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'Pro/MiniMaxAI/MiniMax-M2.5', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'Pro/MiniMaxAI/MiniMax-M2.5', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'Pro/MiniMaxAI/MiniMax-M2.5'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='SiliconFlow en',
        category='aggregator',
        website_url='https://siliconflow.com',
        api_key_url='https://cloud.siliconflow.cn/i/YflgU2Ve',
        env={'ANTHROPIC_BASE_URL': 'https://api.siliconflow.com', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'MiniMaxAI/MiniMax-M3', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'MiniMaxAI/MiniMax-M3', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'MiniMaxAI/MiniMax-M3', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'MiniMaxAI/MiniMax-M3'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='A6API',
        category='aggregator',
        website_url='https://www.a6api.com',
        api_key_url='https://a6api.com/register',
        env={'ANTHROPIC_BASE_URL': 'https://api.a6api.com', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='AtlasCloud',
        category='aggregator',
        website_url='https://www.atlascloud.ai/console/coding-plan',
        api_key_url='https://www.atlascloud.ai/console/coding-plan',
        env={'ANTHROPIC_BASE_URL': 'https://api.atlascloud.ai', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'zai-org/glm-5.1', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'zai-org/glm-5.1', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'zai-org/glm-5.1', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'zai-org/glm-5.1', 'CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS': '1'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.atlascloud.ai'],
        is_partner=True,
    ),
    ClaudePreset(
        name='Compshare',
        category='aggregator',
        website_url='https://www.compshare.cn',
        api_key_url='https://www.compshare.cn/coding-plan',
        env={'ANTHROPIC_BASE_URL': 'https://api.modelverse.cn', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.modelverse.cn'],
        is_partner=True,
    ),
    ClaudePreset(
        name='Compshare Coding Plan',
        category='aggregator',
        website_url='https://www.compshare.cn',
        api_key_url='https://www.compshare.cn/coding-plan',
        env={'ANTHROPIC_BASE_URL': 'https://cp.compshare.cn', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://cp.compshare.cn'],
        is_partner=True,
    ),
    ClaudePreset(
        name='CCSub',
        category='aggregator',
        website_url='https://www.ccsub.net',
        api_key_url='https://www.ccsub.net/register',
        env={'ANTHROPIC_BASE_URL': 'https://www.ccsub.net', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='SSSAiCode',
        category='third_party',
        website_url='https://sssaicodeapi.com',
        api_key_url='https://sssaicodeapi.com/register',
        env={'ANTHROPIC_BASE_URL': 'https://node-hk.sssaicodeapi.com/api', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://node-hk.sssaicodeapi.com/api', 'https://node-hk.sssaiapi.com/api', 'https://node-cf.sssaicodeapi.com/api'],
        is_partner=True,
    ),
    ClaudePreset(
        name='Micu',
        category='third_party',
        website_url='https://www.micuapi.ai',
        api_key_url='https://www.micuapi.ai/register',
        env={'ANTHROPIC_BASE_URL': 'https://www.micuapi.ai', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://www.micuapi.ai'],
        is_partner=True,
    ),
    ClaudePreset(
        name='RightCode',
        category='third_party',
        website_url='https://www.rightapi.ai',
        api_key_url='https://www.rightapi.ai/register',
        env={'ANTHROPIC_BASE_URL': 'https://www.rightapi.ai/claude', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='ETok.ai',
        category='third_party',
        website_url='https://etok.ai',
        api_key_url='https://etok.ai',
        env={'ANTHROPIC_BASE_URL': 'https://api.etok.ai', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        is_partner=True,
    ),
    ClaudePreset(
        name='Cubence',
        category='third_party',
        website_url='https://cubence.com',
        api_key_url='https://cubence.com/signup',
        env={'ANTHROPIC_BASE_URL': 'https://api.cubence.com', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.cubence.com', 'https://api-cf.cubence.com', 'https://api-dmit.cubence.com', 'https://api-bwg.cubence.com'],
        is_partner=True,
    ),
    ClaudePreset(
        name='CrazyRouter',
        category='third_party',
        website_url='https://www.crazyrouter.com',
        api_key_url='https://www.crazyrouter.com/register',
        env={'ANTHROPIC_BASE_URL': 'https://cn.crazyrouter.com', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://cn.crazyrouter.com'],
        is_partner=True,
    ),
    ClaudePreset(
        name='DMXAPI',
        category='aggregator',
        website_url='https://www.dmxapi.cn',
        api_key_url='https://www.dmxapi.cn',
        env={'ANTHROPIC_BASE_URL': 'https://www.dmxapi.cn', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://www.dmxapi.cn', 'https://api.dmxapi.cn'],
        is_partner=True,
    ),
    ClaudePreset(
        name='SudoCode.chat',
        category='third_party',
        website_url='https://sudocode.chat',
        api_key_url='https://sudocode.chat/sign-up',
        env={'ANTHROPIC_BASE_URL': 'https://api.sudocode.chat', 'ANTHROPIC_AUTH_TOKEN': '', 'API_TIMEOUT_MS': '300000'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.sudocode.chat'],
        is_partner=True,
    ),
    ClaudePreset(
        name='SudoCode.us',
        category='third_party',
        website_url='https://sudocode.us',
        api_key_url='https://sudocode.us',
        env={'ANTHROPIC_BASE_URL': 'https://sudocode.us', 'ANTHROPIC_AUTH_TOKEN': '', 'API_TIMEOUT_MS': '300000'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://sudocode.us', 'https://sudocode.run'],
        is_partner=True,
    ),
    ClaudePreset(
        name='XycAi',
        category='aggregator',
        website_url='https://xycai.us',
        api_key_url='https://xycai.us/register',
        env={'ANTHROPIC_BASE_URL': 'https://apicdn.xycai.us', 'ANTHROPIC_API_KEY': ''},
        api_key_field='ANTHROPIC_API_KEY',
        api_format='anthropic',
        endpoint_candidates=['https://apicdn.xycai.us', 'https://apicdn.xyc.ai'],
        is_partner=True,
    ),
    ClaudePreset(
        name='Amux',
        category='aggregator',
        website_url='https://amux.ai',
        api_key_url='https://amux.ai',
        env={'ANTHROPIC_BASE_URL': 'https://api.amux.ai', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='Gemini Native',
        category='third_party',
        website_url='https://ai.google.dev/gemini-api',
        api_key_url='https://aistudio.google.com/app/apikey',
        env={'ANTHROPIC_BASE_URL': 'https://generativelanguage.googleapis.com', 'ANTHROPIC_API_KEY': '', 'ANTHROPIC_MODEL': 'gemini-3.6-flash', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'gemini-3.6-flash', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'gemini-3.6-flash', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'gemini-3.6-flash'},
        api_key_field='ANTHROPIC_API_KEY',
        api_format='gemini_native',
        endpoint_candidates=['https://generativelanguage.googleapis.com'],
    ),
    ClaudePreset(
        name='DeepSeek',
        category='cn_official',
        website_url='https://platform.deepseek.com',
        env={'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'deepseek-v4-pro', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'deepseek-v4-flash', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'deepseek-v4-pro', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'deepseek-v4-pro'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        models_url='https://api.deepseek.com/models',
    ),
    ClaudePreset(
        name='OpenCode Go',
        category='third_party',
        website_url='https://opencode.ai/go',
        api_key_url='https://opencode.ai/go',
        env={'ANTHROPIC_BASE_URL': 'https://opencode.ai/zen/go', 'ANTHROPIC_API_KEY': '', 'ANTHROPIC_MODEL': 'deepseek-v4-flash', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'deepseek-v4-flash', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'deepseek-v4-flash', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'deepseek-v4-flash'},
        api_key_field='ANTHROPIC_API_KEY',
        api_format='anthropic',
        endpoint_candidates=['https://opencode.ai/zen/go'],
    ),
    ClaudePreset(
        name='Tencent Token Plan',
        category='cn_official',
        website_url='https://cloud.tencent.com/product/tokenhub',
        api_key_url='https://console.cloud.tencent.com/tokenhub/tokenplan',
        env={'ANTHROPIC_BASE_URL': 'https://api.lkeap.cloud.tencent.com/plan/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'tc-code-latest', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'tc-code-latest', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'tc-code-latest', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'tc-code-latest'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.lkeap.cloud.tencent.com/plan/anthropic'],
        models_url='https://api.lkeap.cloud.tencent.com/plan/v3/models',
    ),
    ClaudePreset(
        name='Tencent Token Plan (Intl)',
        category='cn_official',
        website_url='https://www.tencentcloud.com/products/tokenhub',
        api_key_url='https://console.tencentcloud.com/tokenhub/tokenplan',
        env={'ANTHROPIC_BASE_URL': 'https://tokenhub-intl.tencentcloudmaas.com/plan/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'auto', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'auto', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'auto', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'auto'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://tokenhub-intl.tencentcloudmaas.com/plan/anthropic'],
    ),
    ClaudePreset(
        name='Tencent Token Plan Enterprise Pro',
        category='cn_official',
        website_url='https://cloud.tencent.com/product/tokenhub',
        api_key_url='https://console.cloud.tencent.com/tokenhub/tokenplan-e',
        env={'ANTHROPIC_BASE_URL': 'https://tokenhub.tencentmaas.com/plan/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'auto', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'auto', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'auto', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'auto'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://tokenhub.tencentmaas.com/plan/anthropic', 'https://tokenhub-intl.tencentmaas.com/plan/anthropic'],
    ),
    ClaudePreset(
        name='Tencent Token Plan Enterprise Pro (Intl)',
        category='cn_official',
        website_url='https://www.tencentcloud.com/products/tokenhub',
        api_key_url='https://console.tencentcloud.com/tokenhub/tokenplan-e',
        env={'ANTHROPIC_BASE_URL': 'https://tokenhub-intl.tencentcloudmaas.com/plan/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'auto', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'auto', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'auto', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'auto'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://tokenhub-intl.tencentcloudmaas.com/plan/anthropic', 'https://tokenhub.tencentcloudmaas.com/plan/anthropic'],
    ),
    ClaudePreset(
        name='Tencent Token Plan Enterprise Lite',
        category='cn_official',
        website_url='https://cloud.tencent.com/product/tokenhub',
        api_key_url='https://console.cloud.tencent.com/tokenhub/tokenplan-e',
        env={'ANTHROPIC_BASE_URL': 'https://tokenhub.tencentmaas.com/plan/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'auto', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'auto', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'auto', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'auto'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://tokenhub.tencentmaas.com/plan/anthropic', 'https://tokenhub-intl.tencentmaas.com/plan/anthropic'],
    ),
    ClaudePreset(
        name='Tencent Token Plan Enterprise Lite (Intl)',
        category='cn_official',
        website_url='https://www.tencentcloud.com/products/tokenhub',
        api_key_url='https://console.tencentcloud.com/tokenhub/tokenplan-e',
        env={'ANTHROPIC_BASE_URL': 'https://tokenhub-intl.tencentcloudmaas.com/plan/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'auto', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'auto', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'auto', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'auto'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://tokenhub-intl.tencentcloudmaas.com/plan/anthropic', 'https://tokenhub.tencentcloudmaas.com/plan/anthropic'],
    ),
    ClaudePreset(
        name='Zhipu GLM',
        category='cn_official',
        website_url='https://open.bigmodel.cn',
        api_key_url='https://www.bigmodel.cn/claude-code',
        env={'ANTHROPIC_BASE_URL': 'https://open.bigmodel.cn/api/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'glm-5.1', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'glm-5.1', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'glm-5.1', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'glm-5.1'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='Zhipu GLM en',
        category='cn_official',
        website_url='https://z.ai',
        api_key_url='https://z.ai/subscribe',
        env={'ANTHROPIC_BASE_URL': 'https://api.z.ai/api/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'glm-5.1', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'glm-5.1', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'glm-5.1', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'glm-5.1'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='Baidu Qianfan Coding Plan',
        category='cn_official',
        website_url='https://cloud.baidu.com/product/qianfan_modelbuilder',
        api_key_url='https://console.bce.baidu.com/qianfan/ais/console/applicationConsole/application',
        env={'ANTHROPIC_BASE_URL': 'https://qianfan.baidubce.com/anthropic/coding', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'qianfan-code-latest', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'qianfan-code-latest', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'qianfan-code-latest', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'qianfan-code-latest'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://qianfan.baidubce.com/anthropic/coding'],
    ),
    ClaudePreset(
        name='Baidu Qianfan Token Plan',
        category='cn_official',
        website_url='https://cloud.baidu.com/product/codingplan.html',
        api_key_url='https://console.bce.baidu.com/qianfan/resource/token-plan',
        env={'ANTHROPIC_BASE_URL': 'https://qianfan.baidubce.com/anthropic/tokenplan/personal', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'deepseek-v4-pro', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'deepseek-v4-pro', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'deepseek-v4-pro', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'deepseek-v4-pro'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://qianfan.baidubce.com/anthropic/tokenplan/personal'],
    ),
    ClaudePreset(
        name='Bailian',
        category='cn_official',
        website_url='https://bailian.console.aliyun.com',
        env={'ANTHROPIC_BASE_URL': 'https://dashscope.aliyuncs.com/apps/anthropic', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='Bailian For Coding',
        category='cn_official',
        website_url='https://bailian.console.aliyun.com',
        env={'ANTHROPIC_BASE_URL': 'https://coding.dashscope.aliyuncs.com/apps/anthropic', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='QwenCloud',
        category='cn_official',
        website_url='https://www.qwencloud.com',
        api_key_url='https://home.qwencloud.com/api-keys',
        env={'ANTHROPIC_BASE_URL': 'https://dashscope-intl.aliyuncs.com/apps/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'qwen3.7-max', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'qwen3.6-flash', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'qwen3.7-max', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'qwen3.7-max'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='QwenCloud For Coding',
        category='cn_official',
        website_url='https://www.qwencloud.com',
        api_key_url='https://home.qwencloud.com/api-keys',
        env={'ANTHROPIC_BASE_URL': 'https://coding-intl.dashscope.aliyuncs.com/apps/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'qwen3.7-plus', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'qwen3.7-plus', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'qwen3.7-plus', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'qwen3.7-plus'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='QwenCloud Token Plan',
        category='cn_official',
        website_url='https://www.qwencloud.com',
        api_key_url='https://home.qwencloud.com/api-keys',
        env={'ANTHROPIC_BASE_URL': 'https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'qwen3.8-max', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'qwen3.6-flash', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'qwen3.8-max', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'qwen3.8-max', 'CLAUDE_CODE_MAX_CONTEXT_TOKENS': '983616'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='StepFun',
        category='cn_official',
        website_url='https://platform.stepfun.com/step-plan',
        api_key_url='https://platform.stepfun.com/interface-key',
        env={'ANTHROPIC_BASE_URL': 'https://api.stepfun.com/step_plan', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'step-3.5-flash-2603', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'step-3.5-flash-2603', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'step-3.5-flash-2603', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'step-3.5-flash-2603'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.stepfun.com/step_plan'],
    ),
    ClaudePreset(
        name='StepFun en',
        category='cn_official',
        website_url='https://platform.stepfun.ai/step-plan',
        api_key_url='https://platform.stepfun.ai/interface-key',
        env={'ANTHROPIC_BASE_URL': 'https://api.stepfun.ai/step_plan', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'step-3.5-flash-2603', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'step-3.5-flash-2603', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'step-3.5-flash-2603', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'step-3.5-flash-2603'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.stepfun.ai/step_plan'],
    ),
    ClaudePreset(
        name='ModelScope',
        category='aggregator',
        website_url='https://modelscope.cn',
        env={'ANTHROPIC_BASE_URL': 'https://api-inference.modelscope.cn', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'ZhipuAI/GLM-5.2', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'ZhipuAI/GLM-5.2', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'ZhipuAI/GLM-5.2', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'ZhipuAI/GLM-5.2'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='KAT-Coder',
        category='cn_official',
        website_url='https://console.streamlake.ai',
        api_key_url='https://console.streamlake.ai/console/api-key',
        env={'ANTHROPIC_BASE_URL': 'https://vanchin.streamlake.ai/api/gateway/v1/endpoints/${ENDPOINT_ID}/claude-code-proxy', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'KAT-Coder-Pro V1', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'KAT-Coder-Air V1', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'KAT-Coder-Pro V1', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'KAT-Coder-Pro V1'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        template_values={'ENDPOINT_ID': {'label': 'Vanchin Endpoint ID', 'placeholder': 'ep-xxx-xxx', 'default': ''}},
    ),
    ClaudePreset(
        name='Longcat',
        category='cn_official',
        website_url='https://longcat.chat/platform',
        api_key_url='https://longcat.chat/platform/api_keys',
        env={'ANTHROPIC_BASE_URL': 'https://api.longcat.chat/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'LongCat-2.0', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'LongCat-2.0', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'LongCat-2.0', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'LongCat-2.0', 'CLAUDE_CODE_MAX_OUTPUT_TOKENS': '131072', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='MiniMax',
        category='cn_official',
        website_url='https://platform.minimaxi.com',
        api_key_url='https://platform.minimaxi.com/subscribe/coding-plan',
        env={'ANTHROPIC_BASE_URL': 'https://api.minimaxi.com/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'API_TIMEOUT_MS': '3000000', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'ANTHROPIC_MODEL': 'MiniMax-M2.7', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'MiniMax-M2.7', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'MiniMax-M2.7', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'MiniMax-M2.7'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='MiniMax en',
        category='cn_official',
        website_url='https://platform.minimax.io',
        api_key_url='https://platform.minimax.io/subscribe/coding-plan',
        env={'ANTHROPIC_BASE_URL': 'https://api.minimax.io/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'API_TIMEOUT_MS': '3000000', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'ANTHROPIC_MODEL': 'MiniMax-M2.7', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'MiniMax-M2.7', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'MiniMax-M2.7', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'MiniMax-M2.7'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='BaiLing',
        category='cn_official',
        website_url='https://alipaytbox.yuque.com/sxs0ba/ling/get_started',
        env={'ANTHROPIC_BASE_URL': 'https://api.tbox.cn/api/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'Ling-2.5-1T', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'Ling-2.5-1T', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'Ling-2.5-1T', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'Ling-2.5-1T'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='AiHubMix',
        category='aggregator',
        website_url='https://aihubmix.com',
        api_key_url='https://aihubmix.com',
        env={'ANTHROPIC_BASE_URL': 'https://aihubmix.com', 'ANTHROPIC_API_KEY': ''},
        api_key_field='ANTHROPIC_API_KEY',
        api_format='anthropic',
        endpoint_candidates=['https://aihubmix.com', 'https://api.aihubmix.com'],
    ),
    ClaudePreset(
        name='CherryIN',
        category='aggregator',
        website_url='https://open.cherryin.ai',
        api_key_url='https://open.cherryin.ai/console/token',
        env={'ANTHROPIC_BASE_URL': 'https://open.cherryin.net', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'anthropic/claude-sonnet-5', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'anthropic/claude-haiku-4.5', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'anthropic/claude-sonnet-5', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'anthropic/claude-opus-5'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://open.cherryin.net'],
    ),
    ClaudePreset(
        name='RelaxyCode',
        category='third_party',
        website_url='https://www.relaxycode.com',
        api_key_url='https://www.relaxycode.com/register',
        env={'ANTHROPIC_BASE_URL': 'https://www.relaxycode.com', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='E-FlowCode',
        category='third_party',
        website_url='https://e-flowcode.cc',
        api_key_url='https://e-flowcode.cc',
        env={'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_BASE_URL': 'https://e-flowcode.cc'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://e-flowcode.cc'],
        notes='extra settings.json keys: {"effortLevel": "high", "enabledPlugins": {"superpowers@superpowers-marketplace": true}, "includeCoAuthoredBy": false, "ENABLE_TOOL_SEARCH": true, "skipWebFetchPreflight": true}',
    ),
    ClaudePreset(
        name='OpenRouter',
        category='aggregator',
        website_url='https://openrouter.ai',
        api_key_url='https://openrouter.ai/keys',
        env={'ANTHROPIC_BASE_URL': 'https://openrouter.ai/api', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'anthropic/claude-sonnet-5', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'anthropic/claude-haiku-4.5', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'anthropic/claude-sonnet-5', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'anthropic/claude-opus-5'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='TheRouter',
        category='aggregator',
        website_url='https://therouter.ai',
        api_key_url='https://dashboard.therouter.ai',
        env={'ANTHROPIC_BASE_URL': 'https://api.therouter.ai', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'anthropic/claude-sonnet-5', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'anthropic/claude-haiku-4.5', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'anthropic/claude-sonnet-5', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'anthropic/claude-opus-5'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.therouter.ai'],
    ),
    ClaudePreset(
        name='Novita AI',
        category='aggregator',
        website_url='https://novita.ai',
        api_key_url='https://novita.ai',
        env={'ANTHROPIC_BASE_URL': 'https://api.novita.ai/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'zai-org/glm-5.1', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'zai-org/glm-5.1', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'zai-org/glm-5.1', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'zai-org/glm-5.1'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.novita.ai/anthropic'],
    ),
    ClaudePreset(
        name='GitHub Copilot',
        category='third_party',
        website_url='https://github.com/features/copilot',
        env={'ANTHROPIC_BASE_URL': 'https://api.githubcopilot.com', 'ANTHROPIC_MODEL': 'claude-sonnet-5', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'claude-haiku-4.5', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'claude-sonnet-5', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'claude-sonnet-5'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='openai_chat',
        provider_type='github_copilot',
        requires_oauth=True,
    ),
    ClaudePreset(
        name='Codex',
        category='third_party',
        website_url='https://openai.com/chatgpt/pricing',
        env={'ANTHROPIC_BASE_URL': 'https://chatgpt.com/backend-api/codex', 'ANTHROPIC_MODEL': 'gpt-5.6-sol', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'gpt-5.6-luna', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'gpt-5.6-sol', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'gpt-5.6-sol', 'CLAUDE_CODE_MAX_CONTEXT_TOKENS': '372000', 'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '372000'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='openai_responses',
        provider_type='codex_oauth',
        requires_oauth=True,
    ),
    ClaudePreset(
        name='xAI (Grok)',
        category='third_party',
        website_url='https://x.ai/grok',
        env={'ANTHROPIC_BASE_URL': 'https://api.x.ai/v1', 'ANTHROPIC_MODEL': 'grok-4.5', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'grok-4.5', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'grok-4.5', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'grok-4.5'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='openai_responses',
        provider_type='xai_oauth',
        requires_oauth=True,
    ),
    ClaudePreset(
        name='Nvidia',
        category='aggregator',
        website_url='https://build.nvidia.com',
        api_key_url='https://build.nvidia.com/settings/api-keys',
        env={'ANTHROPIC_BASE_URL': 'https://integrate.api.nvidia.com', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'moonshotai/kimi-k2.5', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'moonshotai/kimi-k2.5', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'moonshotai/kimi-k2.5', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'moonshotai/kimi-k2.5'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='openai_chat',
    ),
    ClaudePreset(
        name='PIPELLM',
        category='aggregator',
        website_url='https://code.pipellm.ai',
        api_key_url='https://code.pipellm.ai/login',
        env={'ANTHROPIC_BASE_URL': 'https://cc-api.pipellm.ai', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'claude-opus-5', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'claude-haiku-4-5-20251001', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'claude-sonnet-5', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'claude-opus-5'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        notes='extra settings.json keys: {"includeCoAuthoredBy": false}',
    ),
    ClaudePreset(
        name='Xiaomi MiMo',
        category='cn_official',
        website_url='https://platform.xiaomimimo.com',
        api_key_url='https://platform.xiaomimimo.com/#/console/api-keys',
        env={'ANTHROPIC_BASE_URL': 'https://api.xiaomimimo.com/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'mimo-v2.5-pro', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'mimo-v2.5-pro', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'mimo-v2.5-pro', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'mimo-v2.5-pro'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='Xiaomi MiMo Token Plan (China)',
        category='cn_official',
        website_url='https://platform.xiaomimimo.com/#/token-plan',
        api_key_url='https://platform.xiaomimimo.com/#/console/plan-manage',
        env={'ANTHROPIC_BASE_URL': 'https://token-plan-cn.xiaomimimo.com/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'mimo-v2.5-pro', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'mimo-v2.5-pro', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'mimo-v2.5-pro', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'mimo-v2.5-pro'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
    ),
    ClaudePreset(
        name='AWS Bedrock (AKSK)',
        category='cloud_provider',
        website_url='https://aws.amazon.com/bedrock/',
        env={'ANTHROPIC_BASE_URL': 'https://bedrock-runtime.${AWS_REGION}.amazonaws.com', 'AWS_ACCESS_KEY_ID': '${AWS_ACCESS_KEY_ID}', 'AWS_SECRET_ACCESS_KEY': '${AWS_SECRET_ACCESS_KEY}', 'AWS_REGION': '${AWS_REGION}', 'ANTHROPIC_MODEL': 'global.anthropic.claude-opus-5', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'global.anthropic.claude-haiku-4-5-20251001-v1:0', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'global.anthropic.claude-sonnet-5', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'global.anthropic.claude-opus-5', 'CLAUDE_CODE_USE_BEDROCK': '1'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        template_values={'AWS_REGION': {'label': 'AWS Region', 'placeholder': 'us-west-2', 'default': 'us-west-2'}, 'AWS_ACCESS_KEY_ID': {'label': 'Access Key ID', 'placeholder': 'your-access-key-id', 'default': ''}, 'AWS_SECRET_ACCESS_KEY': {'label': 'Secret Access Key', 'placeholder': 'your-secret-key', 'default': ''}},
    ),
    ClaudePreset(
        name='AWS Bedrock (API Key)',
        category='cloud_provider',
        website_url='https://aws.amazon.com/bedrock/',
        env={'ANTHROPIC_BASE_URL': 'https://bedrock-runtime.${AWS_REGION}.amazonaws.com', 'AWS_REGION': '${AWS_REGION}', 'ANTHROPIC_MODEL': 'global.anthropic.claude-opus-5', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'global.anthropic.claude-haiku-4-5-20251001-v1:0', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'global.anthropic.claude-sonnet-5', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'global.anthropic.claude-opus-5', 'CLAUDE_CODE_USE_BEDROCK': '1'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        template_values={'AWS_REGION': {'label': 'AWS Region', 'placeholder': 'us-west-2', 'default': 'us-west-2'}},
        notes='extra settings.json keys: {"apiKey": ""}',
    ),
    ClaudePreset(
        name='JieKou AI',
        category='aggregator',
        website_url='https://jiekou.ai/#model-library',
        api_key_url='https://jiekou.ai/settings/key-management',
        env={'ANTHROPIC_BASE_URL': 'https://api.jiekou.ai/anthropic', 'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_MODEL': 'claude-fable-5', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'claude-fable-5', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'claude-fable-5', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'claude-fable-5'},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.jiekou.ai/anthropic'],
    ),
    ClaudePreset(
        name='AICodeWith',
        category='aggregator',
        website_url='https://aicodewith.ai',
        api_key_url='https://aicodewith.ai/login',
        env={'ANTHROPIC_BASE_URL': 'https://api.aicodewith.ai', 'ANTHROPIC_AUTH_TOKEN': ''},
        api_key_field='ANTHROPIC_AUTH_TOKEN',
        api_format='anthropic',
        endpoint_candidates=['https://api.aicodewith.ai'],
    ),
]
