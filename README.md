# AgentRoom

When several Claude Code agents edit the same repository at once, the collision
that matters is not the character-level kind (CRDTs already solve that) -- it
is two *teammates'* agents both deciding to own the same file, which nobody
should let a machine settle unilaterally. AgentRoom auto-merges the first kind
and escalates the second to a human, then blocks the merge until that human
has actually decided.

- **Covers**: what this is, how to run it, and where to go next.
- **Does not cover**: how to work *on* it (CONTRIBUTING.md), how the pieces fit
  (ARCHITECTURE.md), how to perform a task (docs/how-to/).

## 用 Docker 跑（最省事，装了 Docker 就一条命令）

```bash
docker compose up --build
```

打开 `http://localhost:5173` 就行，前后端都在容器里，不用自己装 Python/Node 依赖。
想接真实模型，起之前先 `export LLM_PROVIDER=... LLM_API_KEY=...`（跟下面「接真实模型」
那节一样的变量，`docker compose up` 会自动带进容器）。停止用 `Ctrl+C`，再
`docker compose down` 清理。Agent 用 `write_file` 改的文件会直接出现在你本地这份代码
里（`git status` 能看到），不是关起来改了看不见。

没有 Docker，或者想更细地控制两个服务，往下看手动跑的方式。

## 5 分钟跑起来（不用 Docker，手动跑，照抄命令就行）

需要两个终端窗口——一个跑后端，一个跑前端，两个都要一直开着。

**终端 1 —— 后端**：

```bash
python3 -m pip install --user --break-system-packages -r backend/requirements.txt
python3 -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8787
```

看到 `Uvicorn running on http://0.0.0.0:8787` 就是起来了，这个终端别关，一直挂着。

**终端 2 —— 前端**：

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

看到 `Local: http://localhost:5173/` 就是起来了，同样别关。

**然后打开浏览器**：访问 `http://localhost:5173` ，就能看到看板了。页面上有三块：

- **左边「对话」**：可以给主 Agent 发消息，它能自己改文件、也能派子代理去干活。**没配模型 key 之前这块用不了**，会有黄色提示条，看下面「接真实模型」怎么配。
- **右上「Agent 状态」**：看板，四栏——工作中 / 需要输入 / 待审查 / 准备合并。
- **右下「待人工裁决队列」+「成果预览」**：Agent 之间抢同一个文件时，冲突会出现在这里，需要你手动点"通过"或"退回"；Agent 做完事想给你看的东西会出现在"成果预览"里。

**想看一次"两个 Agent 抢文件"的效果**（不需要模型 key，跑的是脚本模拟）：

```bash
python3 scripts/demo/run_two_agents.py
```

跑完刷新一下看板，"待人工裁决队列"里会多一条冲突，点"通过"之后跑一下：

```bash
python3 scripts/gates/check_escalation_decisions.py
```

裁决前这条命令会失败（exit 1），裁决后会成功（exit 0）——这就是"人不点头，代码合并不进去"这条规则本身。

### 接真实模型（让左边的对话框真的能用）

拿到任意一家的 API key（DeepSeek、Claude、GPT 都行），在**终端 1**（后端那个）里，**先按 `Ctrl+C` 停掉后端，再**这样重新起：

```bash
export LLM_PROVIDER=openai          # DeepSeek/GPT 用 openai；Claude 用 anthropic
export LLM_API_KEY=你的key
export LLM_MODEL=deepseek-chat      # 可选，不填有默认值
python3 -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8787
```

如果你机器上已经给别的工具设过 `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`，不用重复设 `LLM_API_KEY`，会自动用那个。**这里读不到 Claude Code 自己登录时用的那个凭证**——那是 Claude Code 专用的，没法也不该拿来给别的程序用，必须是一个独立的 API key。

配好重启后端、刷新页面，左边对话框顶部的黄色提示条会消失，就能正常聊天了。

### 常见问题

- **8787 或 5173 端口被占用**：说明上次启动的进程还在跑，找到它关掉再重开：`lsof -ti:8787 | xargs kill`（或把 8787 换成 5173）。
- **`pip install` 报错、装不上依赖**：如果是因为连不上 apt/Debian 的源，见 [backend/README.md](backend/README.md) 里绕开 apt 直接装的办法。
- **前端 `npm install` 卡住或报错**：需要 Node 20+；如果 `npm run build` 时报 rolldown 相关的原生模块找不到，见 [frontend/README.md](frontend/README.md)。
- **看板打不开、显示空白**：先确认终端 1 和终端 2 都还在跑（没报错退出），再检查浏览器地址是不是 `http://localhost:5173`（不是 8787，8787 是后端接口，不是给人看的页面）。
- **`docker compose up` 报端口冲突**：说明本机已经用手动方式起过一份（还在跑的 `uvicorn`/`npm run dev`），两种方式二选一，先停掉其中一个。

## Quick start (English, condensed)

```bash
# backend
python3 -m pip install --user --break-system-packages -r backend/requirements.txt
python3 -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8787 &

# frontend
cd frontend && npm install && npm run dev -- --host 0.0.0.0 --port 5173 &

# demo: two agents racing to claim the same file
python3 scripts/demo/run_two_agents.py
```

If it worked: the dashboard at `http://localhost:5173` shows both agents,
one pending escalation for the file they both claimed, and
`python3 scripts/gates/check_escalation_decisions.py` exits 1 until you
click "通过" in the dashboard, after which it exits 0.

## Requirements

- Python 3.11+, Node 20+

## Documentation

- Bird's eye view and invariants: [ARCHITECTURE.md](ARCHITECTURE.md)
- Everything else, routed: [docs/index.md](docs/index.md)
- Backend details (incl. chat/subagents/file tools/LLM key setup): [backend/README.md](backend/README.md)
- Frontend details: [frontend/README.md](frontend/README.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues go to
[SECURITY.md](SECURITY.md), not the issue tracker.

## License

Unlicensed for now — a hackathon submission under Fresh Build rules; see
[SECURITY.md](SECURITY.md) for the placeholder note on this.
