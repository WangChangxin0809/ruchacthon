# Use your own API key

Every run needs a model endpoint. The workbench can use the server's own
Claude Code login, or a key you supply yourself; a key you supply is yours,
and nobody else sees it unless you share the provider with your team.

## Add a provider

1. 设置 → 模型 → 新增。
2. Pick a vendor from the list. It is the same catalogue cc-switch ships, so
   most Chinese and international Claude-compatible vendors are already there
   with their address filled in. Paste your key and the picker will usually
   guess which vendor it belongs to from its prefix.
3. 测试连接 sends one tiny request and shows the round-trip time. It does not
   start Claude Code, so it is free to retry.
4. 拉取模型列表 asks the vendor which models the key can use, so you can pick
   instead of typing a model id.
5. 设为默认 makes it what your runs use. 共享给团队 lets your teammates pick
   it too — they still never see the key itself.

## What happens to the key

The key is stored in a `0600` file next to the database, referenced by a name
(`profile.<your handle>.<provider id>`). It is never returned by the API,
never written to an event, a message or the database, and never shown in a
log. The UI only ever learns whether a key is set.

A provider can instead point at an environment variable on the server. That
is for administrators: an ordinary account may only use its own stored key,
because store names are guessable and a chosen reference would otherwise be a
way to read somebody else's.

## Vendors that do not speak Anthropic's protocol

Claude Code speaks the Anthropic Messages protocol. Many vendors (NVIDIA NIM,
most aggregators) speak OpenAI's chat protocol instead. For those the
workbench starts a converter on loopback and points that run's Claude Code at
it:

```
ANTHROPIC_BASE_URL=http://127.0.0.1:8787/proxy/<route token>
ANTHROPIC_AUTH_TOKEN=<route token>
```

The route token is issued when the run starts and destroyed when it ends. It
is not your key: your key stays in the server process and is attached to each
forwarded request there. `/proxy` answers loopback clients only, so the route
is unreachable from the network even though the workbench itself is not.

Model names are translated on the way through. `ANTHROPIC_DEFAULT_*_MODEL`
carry alias names (`haiku`, `sonnet`, `opus`) that the converter maps to the
vendor's own model ids using the provider's model map, so a subagent asking
for Haiku gets whatever you mapped Haiku to.

Two settings matter if you moved things around:

| Variable | Meaning |
|---|---|
| `WORKBENCH_PORT` | the port the converter's URL points at, when it is not 8787 |
| `WORKBENCH_PROXY_BASE` | the whole base URL, for when the child process reaches the server by another name |

A run using such a provider does not load `~/.claude/settings.json`. That
file has an `env` block Claude Code assigns over the launch environment, and
it would put the machine's own login back in front of the key you chose.

Vendors whose protocol is neither of these (OpenAI Responses, Gemini native)
are listed but cannot be selected yet; the card says 暂不支持.

## Verified

NVIDIA NIM, 2026-09-06: connection check 1.4 s, 81 models discovered, and a
real Claude Code turn answered through the converter. See
`docs/reference/acceptance-2026-09-06.md`.
