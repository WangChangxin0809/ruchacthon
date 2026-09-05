# Third-party notices

Code in this repository was copied or adapted from the projects below (see docs/decisions/0004-track3-multi-user-multi-agent.md §10 for what and where). Their licence texts follow.

## cc-switch (https://github.com/farion1231/cc-switch) — MIT

Used for: backend/app/proxy/ (Anthropic ↔ OpenAI conversion, SSE re-encoding, model mapping), backend/app/provider_presets.py.

```
MIT License

Copyright (c) 2025 Jason Young

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.```

## Agent Orchestrator (https://github.com/Untrivial-ai/agent-orchestrator) — Apache-2.0

Used for: backend/app/prompts.py (orchestrator / worker system prompts), the `[from <sender>]` message prefix, notification semantics, the board presentation rules in frontend/src/ui.jsx.

Licensed under the Apache License, Version 2.0. A copy is at https://www.apache.org/licenses/LICENSE-2.0. Copyright Untrivial AI and the Agent Orchestrator contributors. The upstream LICENSE file carries no per-author copyright line; this notice preserves the attribution the licence requires.

## deepseek-harness (https://github.com/deepseek-ai/dsh) — MIT

Used for: settings-page copy, API-key validation rules, model discovery, agent-preset roster interaction in frontend/src/Settings.jsx.

```
MIT License

Copyright (c) 2026 DeepSeek

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
