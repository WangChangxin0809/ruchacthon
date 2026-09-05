# CC Workbench

A local multi-agent development workbench where **Claude Code is the only
execution engine**. You talk to a main agent; it (or you) spawns background
workers, each a separate Claude Code process in its own git worktree. Workers
hand back previews you can review and comment on, feedback goes straight back
into the right worker's session, and workers coordinate through a Room whose
real conflicts are decided by you, not by a merge heuristic.

- **Covers**: what this is, how to run it, and where to go next.
- **Does not cover**: how to work *on* it (CONTRIBUTING.md), how the pieces
  fit (ARCHITECTURE.md), the decisions behind it (docs/decisions/).

## 前提

- 本机已安装并登录 **Claude Code**（`claude --version` 能跑；`claude -p "hi"` 能回答）。
  工作台不读取、不复制你的登录凭证，只是启动 `claude` 子进程。
- Python 3.11+、Node 20+、git。Windows / macOS / Linux 都可以，不需要 tmux。

## Quick start（5 分钟跑起来）

两个终端，都一直开着。

**终端 1 — 后端**

```bash
python3 -m pip install --user --break-system-packages -r backend/requirements.txt
python3 -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8787
```

Windows 用 `py -m pip install -r backend/requirements.txt` 和
`py -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8787`。

**终端 2 — 前端**

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

打开 `http://localhost:5173`：

1. 右上角填你的名字（多人共用时 agent 要知道是谁在说话）。「总览」点「新增项目」：克隆一个 git 仓库、新建空项目，或选服务器上已有的目录。
2. 「工作」区的「主 agent」里说话，比如「把 README 里的安装步骤整理一下，另外派一个 worker 给 utils 加单元测试」。
   主 agent 可以自己干，也会用 `spawn_worker` 派 worker；worker 出现在左侧任务列表和右侧看板。
3. 右侧「预览」看 worker 提交的成果（Markdown / 图片 / HTML / 文件 / diff / dev server），
   直接在成果下面写反馈——它会送回那个 worker 的会话；「审阅通过」≠ 合并，合并在「Diff」页单独点。
4. 底部「Room」默认折叠：成员、认领、路径重叠、待裁决。两个 worker 在同一工作区抢同一个文件时，
   这里会出现一张裁决卡，被挡住的 worker 会等你点。
5. ⚙ 设置 → **模型**：像 dsh 一样按提供方加卡片，密钥只写不读，按运行注入，不改全局 Claude Code 配置；
   输入框下方的模型选择器列出「提供方 ▸ 模型」。设置 → **Claude Code** 看登录状态、粘贴 `claude setup-token` 的令牌。
6. 「聊天」区是人和人聊的地方（拉群、贴任务链接）；悬停一条消息可「派给 agent」，它会变成一个 worker 任务。

默认所有 worker 用你 Claude Code 登录的模型；想省钱，后端启动前 `export WORKBENCH_MODEL=claude-sonnet-5`。
并发上限 `WORKBENCH_MAX_CONCURRENT_RUNS`（默认 3）。数据在 `backend/data/`（可用 `WORKBENCH_DATA_DIR` 改）。

## 验收与证据

`./ci.sh --unit` 跑不依赖 Claude Code 的机制测试（`backend/tests/test_workbench.py`）。
真实 Claude Code 的验收记录（改文件、并行 worker、取消、重启恢复、断线重连、反馈回流、Room 裁决、Profile 检查）
在 [docs/reference/acceptance-2026-09-05.md](docs/reference/acceptance-2026-09-05.md)，
连同尚未验证的场景清单。

## 常见问题

- **对话回复「Failed to authenticate」**：`claude` 本身没登录或登录过期，先在终端跑一次 `claude` 登录。
- **端口被占**：`lsof -ti:8787 | xargs kill`（Windows：`netstat -ano | findstr 8787` 后 `taskkill /PID <pid> /F`）。
- **worker 状态是「已中断」**：后端重启时它的进程已不在，这是真实状态；点「重试」会在同一会话开一次新运行，
  旧运行的记录保留。
- **Docker**：`docker-compose.yml` 沿用旧版本，尚未针对「引擎是本机 Claude Code」验证，暂不推荐。

## Documentation

- Bird's eye view and invariants: [ARCHITECTURE.md](ARCHITECTURE.md)
- Everything else, routed: [docs/index.md](docs/index.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues go to
[SECURITY.md](SECURITY.md), not the issue tracker.

## License

Unlicensed for now — a hackathon submission under Fresh Build rules; see
[SECURITY.md](SECURITY.md) for the placeholder note on this. Upstream
projects are referenced for ideas only; no code was copied
(docs/decisions/0003).
