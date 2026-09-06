# 赛道三设计稿：多人多 agent 协作

写于 2026-09-05。基于对现有代码、cc-switch、Agent Orchestrator（AO）、deepseek-harness（dsh）
和旧版 AgentRoom（git `fb898cd`）的通读。原则只有一条：能抄的抄，能复用的复用，
只在参考项目没有的地方自己写。每一节都标了「抄自哪里」。

- **覆盖**：数据模型、鉴权、对话统一、@ 规则、agent 间通信、Agent Definition、
  多厂商 key 与转换代理、通知与权限、前端改动、实施顺序、验证方式。
- **不覆盖**：飞书本身的对接（这里是「学飞书的模型」，不是接飞书）、OAuth 类厂商
  （GitHub Copilot / Codex OAuth / xAI OAuth）、手机推送。

## 0. 结论先行

| 需求 | 做法 | 抄自 |
|---|---|---|
| 1 多人多 agent | 用户 + 团队 + 成员，一个部署 = 一个组织 | 飞书/Slack 的模型，AO cloud contract 的 `owner/admin/member` 词汇 |
| 2.1 私聊 / 群聊 | `conversations(kind=dm/group/session)` + `conversation_members` 一张成员表管三种对话 | 自己写，数据放在现有 `chat_messages` 里 |
| 2.2 加好友 / 建团 / 邀请 | 砍掉「加好友」；保留 注册、登录、建团、邀请链接、团内互相可见 | 飞书的 Workspace 语义 |
| 3.1 agent 间 MCP 通信 + claim | 现有 `room` 工具保留，补 `message_agent`、`list_agents`、`ask_human`，主 agent 也挂 `room`；消息统一 `[from <name>]` 前缀 | AgentRoom 的五个动词 + AO `ao send` 的前缀规则 |
| 3.2 AO 的管理方式 | `spawn / send / list / get / kill / rename` 作为 MCP 工具；orchestrator / worker 系统提示词整段抄 | AO `prompt.go` + `using-ao` skill |
| 4.1 work 区对话 + summary + preview | 已有（本轮 UI 已照 AO 抄完） | AO |
| 4.2 会话 = 有成员的群 | `session` 也是 conversation：owner 管成员，非成员 403，多人时只回 @，消息带人名 | 自己写；`sessionguard` 的拒绝策略抄 AO |
| 5.1 Agent Definition | `agent_definitions` 表 = Agent SDK 选项集；内置 orchestrator / worker / reviewer 只读，复制即新建 | dsh agent-presets 的 roster UX + AO `AgentConfig` 字段 |
| 5.2 多厂商 key | 88 个 cc-switch 预置整表搬过来；key 按人归属、只写不读；非 Anthropic 协议走本地转换代理 | cc-switch 预置 + 代理，dsh 的设置页 UX |
| 补：通知 | `notifications` 表 + 按用户过滤的 WS 事件 | AO 的四种通知语义 |
| 补：权限 | 一张规则表，两个 helper | 自己写 |

## 1. 身份、团队、邀请

### 1.1 表

```sql
users(id, handle UNIQUE NOCASE, display_name, email, password_hash, is_admin, prefs JSON, created_at, last_seen_at)
auth_sessions(id, user_id, token_hash UNIQUE, user_agent, created_at, expires_at, last_used_at)
teams(id, slug UNIQUE, name, owner_id, default_profile_id, default_model, created_at)
team_members(team_id, user_id, role owner|admin|member, joined_at)
invites(id, team_id, token UNIQUE, created_by, role, max_uses, uses, expires_at, revoked_at, created_at)
```

- 密码：`hashlib.pbkdf2_hmac("sha256", pw, salt, 310_000)`，存 `pbkdf2_sha256$rounds$salt$hash`。不加依赖。
- 令牌：`wbs_` + `secrets.token_urlsafe(32)`，库里只存 sha256。30 天滑动过期。
  传输沿用现有 `Authorization: Bearer` 和 `?token=`（`/ws` 与 `<img src>` 需要）。
- `WORKBENCH_TOKEN` 降级为**引导令牌**：只能用于第一次注册和 `/api/admin/*`，不能发消息。
- `WORKBENCH_SINGLE_USER=1`：本机单人模式，自动创建 `local` 用户，README 的两终端快速开始不需要密码。
- 第一个注册的用户是管理员，自动建「默认团队」并认领所有历史数据（项目、会话、profile）。
  之后每个注册者若 `handle` 等于历史消息里的自由文本 `author`，把那些消息认领到自己名下。
  这样阿里云上的演示数据不用手改。

### 1.2 路由

```
GET  /api/auth                      公开：{required, mode, registration_open, single_user, ok, me}
POST /api/auth/register             {handle, display_name, password, invite?}
POST /api/auth/login                {handle, password} → {user, token, expires_at}
POST /api/auth/logout
GET  /api/auth/me                   用户 + teams + prefs + unread_notifications
PATCH /api/auth/me                  {display_name?, prefs?, password?:{old,new}}
GET  /api/users?team_id=&q=         只返回同团队的人（@ 选择器、新私聊用）
GET/POST /api/teams                 我的团队；建团 = 我是 owner，同时建「全员」群
GET/PATCH/DELETE /api/teams/{id}
POST/PATCH/DELETE /api/teams/{id}/members
POST/GET/DELETE /api/teams/{id}/invites      → {invite, url: <origin>/?invite=<token>}
GET  /api/invites/{token}           公开：{team.name, invited_by, valid}
POST /api/invites/{token}/accept
GET/POST/PATCH /api/admin/users     管理员
```

作者一律服务器推导：`MessageIn / ChatIn / ChannelIn / ReviewIn / FeedbackIn / DecisionIn`
里的 `author / actor` 字段删除，改从 `request.state.user` 取；表里保留 `author` 字符串做显示快照，
新增 `user_id / actor_id` 做过滤。ARCHITECTURE 增加不变量 7：「作者由鉴权主体推导，请求体不携带作者」。

## 2. 对话统一：私聊、群聊、工作会话是一种东西

```sql
conversations(id, kind dm|group|session, team_id, project_id, session_id UNIQUE, title, owner_id,
              dm_key UNIQUE, is_default, agent_reply auto|always|mention|never, created_at, updated_at)
conversation_members(conversation_id, member_kind user|agent, member_id, role owner|member|agent,
                     added_by, joined_at, last_read_at, muted)
notifications(id, user_id, kind, title, body, link, conversation_id, project_id, actor_id, read_at, created_at)
```

- `dm/group` 的消息继续存 `chat_messages`（`channel_id` = conversation id，旧 `ch_*` 频道原地变成 group）；
  `session` 的消息仍是 `messages`。一张成员表、一套可见性、一套未读，两张消息表不动。
- 每个 `sessions` 行自动有一个 `kind=session` 的 conversation，成员至少是 `{创建者: owner, agent: 该 session}`。
- 主 agent 的会话是项目的「公共房间」：团队成员打开即自动加入。worker 会话严格：创建者 + agent，
  owner 手动拉人。看板上非成员看到的卡片带 🔒，点开提示「向 owner 申请加入」。
- 可见性 helper：`is_member(user, conv)`、`visible_sessions(user)`、`require_conv(user, conv, owner=False)`。
  `GET /api/sessions/{id}`、`/messages`、WS 的 `message / stream_delta`、`since=` 回放全部按成员过滤。
- 路由：
  ```
  GET  /api/conversations?team_id=&kind=      我的：成员、last、unread
  POST /api/conversations                     {kind:"group", team_id, title, member_ids}
  POST /api/conversations/dm                  {user_id} get-or-create
  GET/PATCH /api/conversations/{id}           owner 可改 title / agent_reply
  POST/DELETE /api/conversations/{id}/members {user_id}   group: 任何成员可拉人；session: 仅 owner
  POST /api/conversations/{id}/read
  GET/POST /api/conversations/{id}/messages   dm/group 走 chat_messages；session 转发到会话发送
  ```
  旧的 `/api/chat/channels*` 删除，`Chat.jsx` 改用 `/api/conversations`。

### 2.1 @ 规则（4.2）

在唯一的入口 `post_message → runs.deliver / create_run` 实现：

```
humans = 该会话的 user 成员数
should_run = mode != never and (mode == always or (mode == auto and humans <= 1) or 文本 @ 到了这个 agent)
```

- 人的消息先落库并广播（其他成员实时看到），不 `should_run` 就到此为止，模型看不到。
- `should_run` 时，先把上次以来被跳过的消息（`delivered_to_agent=0`）作为
  `[Earlier in this conversation, not yet shown to you]` 一段补给模型（最多 30 条），再发当前消息。
- **所有**发给模型的人类消息统一前缀 `[显示名 (@handle)] `，1 对 1 也加。这是 AO `ao send` 的
  `[from <id>]` 规则，只是把 id 换成人名。
- 系统提示词加一句：多人共用此会话，消息带发送者名字，请称呼对方；多人会话里只会收到 @ 你的消息。
- 群聊里也允许 agent 成员：`POST /api/conversations/{id}/messages` 对每个 agent 成员套同一规则，
  `@主 agent` 即送进它的会话。这就是「agent 进群」，不需要新传输通道。
- 安全策略抄 AO `sessionguard`：人发的消息在 `blocked`（等权限）时拒收；自动催促在 `needs_input`
  时拒发；未知状态一律拒绝；同一 key 的催促去重、最多 3 次。

## 3. agent 间通信与管理（3.1 / 3.2）

### 3.1 保留现有 `room`，补三个动词，主 agent 也挂上

现有 `room_claim / room_release / room_state / room_broadcast / room_inbox / room_handoff` 不动
（租约、路径归一、同工作区冲突升级为人工裁决——这是硬规则 1）。新增：

| 工具 | 参数 | 语义 |
|---|---|---|
| `message_agent` | `{target: task_id \| "main", text}` | 定向消息，落到目标会话的 `messages`（author = `agent:<name>`），并 push 进活着的运行 |
| `list_agents` | `{}` | 本项目所有 agent：name、kind、task、status、activity、branch |
| `ask_human` | `{question, path?}` | 生成 `needs_input` 状态 + 通知会话成员；不阻塞 claim 之外的事 |
| `room_inbox` | 加 `since` 游标 | 旧 AgentRoom 的 `room_read(since_id)` |

- `Room.identity(run)` 扩成 `{run_id, task_id, task_title, workspace_id, kind, session_id, conversation_id, agent_name, created_by}`，
  身份仍是 Run 上的闭包，模型不能冒充别人（不变量 4 不变）。
- 冲突判定加「owner」维度：同一人自己的两个 agent 重叠只标黄（旧 AgentRoom 的 `person` 档），
  不同人的 agent 才升级裁决（`team` 档）。裁决卡恢复旧版的 `risk_summary` 一句话。
- 旧版 `room_edit_text`（CRDT 工具）不恢复，`shared_edit.py` 的 Write 钩子已经覆盖。
- 对外 HTTP `/mcp` 端点（让同事自己终端里的 Claude Code 进 Room）本里程碑不做；要做时按 `fb898cd` 的
  `app.mount("/mcp", mcp.streamable_http_app())` 模式加 per-run 令牌，另写 ADR。

### 3.2 AO 的管理动词做成 MCP 工具

AO 里 orchestrator 靠 shell 跑 `ao spawn / send / session ls / kill / rename`；我们是 SDK 进程内 MCP，
把它的参数表翻成 JSON schema 放进 `workbench` 服务器：

```
spawn_worker{title<=20字, instructions, agent_definition_id?, model?, isolation?, depends_on?, edit_mode?}
send_message{target, text}            = message_agent 的别名
list_sessions{include_terminated?}    = list_agents
get_session{id}  kill_session{id}  rename_session{id, title}
worker_transcript{task_id, tail?}     现有
```

- 三段提示词整段抄 AO `backend/internal/session_manager/prompt.go`：`orchestratorSystemPrompt`
  （规划、派活、绝不自己写实现、标题 20 字、先 inspect 再 spawn、不要用内置 subagent 工具）、
  `workerSystemPrompt`（自己的分支、汇报给 orchestrator、git 规则）、`systemPromptGuard`。把 `ao ...`
  换成上面的工具名。放进 `backend/app/prompts.py`。
- AO 的 `DeriveStatus / DeriveKanbanPresentation` 已经在本轮 UI 抄过（`ui.jsx` LANES），不再动。
- 是否允许 worker 再 spawn：由 Agent Definition 的 `can_spawn` 决定，默认只有 orchestrator 可以。

## 4. Agent Definition（5.1）

在只有 Claude Code 一个引擎的前提下，一个 definition 就是一组 Agent SDK 选项：

```sql
agent_definitions(id, team_id, owner_id, name, description, trust system|user, is_default,
  role orchestrator|worker|any, system_prompt, allowed_tools JSON, disallowed_tools JSON,
  mcp_servers JSON, model, permission_mode, max_turns, max_budget_usd, can_spawn, effort,
  created_at, updated_at)
```

- 内置四个只读（`trust=system`）：`orchestrator`（AO 提示词 + `workbench` + `room` 工具）、
  `worker`（AO worker 提示词 + `room`）、`reviewer`（只读工具：Read/Grep/Glob/Bash 只读命令，不能 claim）、
  `chat`（无文件工具，纯问答）。
- UX 抄 dsh agent-presets：列表行 `{id, trust, isDefault, name, description, broken?}`，「复制」是唯一的新建路径
  （弹窗填 id + 名字），「设为默认」，会话创建时固定，坏了显示 `broken` 徽章和原因。
- 权限档抄 dsh 三个标签：仅可查看 / 工作区内修改 / 完全权限，选「完全权限」要确认一次。
  映射到 SDK：`default | acceptEdits | bypassPermissions`；AO 的 `auto` 保留为第四档。
- `mcp_servers` 只存 `{name, type stdio|http|sse, command, args, url, headers, env 变量名}`，
  值仍由环境变量名引用（硬规则 3）。
- 生效点：`RunManager._spec(run)` 把 definition 合并进 `RunSpec`：`system_prompt_append = definition.system_prompt + 角色段`，
  `allowed_tools / disallowed_tools / permission_mode / model / max_turns`，`mcp_servers` 与 run 绑定的
  SDK 服务器并列传入。`runs.agent_definition_id` 记录快照。
- 选择点：新任务对话框的 `Claude Code` 静态标签换成 definition 下拉；主会话输入框旁也加一个；
  `spawn_worker` 工具带 `agent_definition_id`。

## 5. 多厂商 key 与转换代理（5.2）

### 5.1 预置：整表搬 cc-switch

- `backend/app/provider_presets.py`：cc-switch `src/config/claudeProviderPresets.ts` 的 88 个条目逐条转成
  `ClaudePreset(name, category, website_url, api_key_url, env, api_key_field, api_format, endpoint_candidates,
  template_values, models_url, is_official, is_partner)`。名字和 id 保持原样，去掉推广参数。
- 88 个里 83 个是 Anthropic 协议（`api_format=anthropic`），不需要代理，直接 per-run 注入
  `ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN 或 ANTHROPIC_API_KEY / ANTHROPIC_MODEL / ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS,FABLE}_MODEL`。
  这和现有 `providers.env_for` 是一回事，只是 KINDS 从 7 个手写档变成预置驱动。
- `api_format=openai_chat` 的（NVIDIA NIM、OpenRouter 兼容模式、各种 NewAPI 网关）走 §5.3 的代理。
  `openai_responses / gemini_native` 本里程碑不做。
- 「识别厂商」：dsh 和 cc-switch 都**不做**硬识别。我们做成前端建议：key 前缀表
  （`sk-ant-` Anthropic、`nvapi-` NVIDIA、`sk-or-` OpenRouter、`AIza` Google、`xai-` xAI、`gsk_` Groq）和
  base URL 的 host 表自动选中预置，用户可改。不做服务器端硬校验，dsh 的注释说得对：
  硬校验会把网关用户锁在外面。
- 「测试连接」= dsh 的模型发现：`GET {base}/models`（bearer）或 `{root}/v1/models`（x-api-key）；
  能列出模型就是绿点，401/403 提示「检查 key」。cc-switch 的 `build_models_url_candidates` 补候选路径。
- 现有 `/api/profiles/{id}/check`（流式 + 工具调用的兼容性检查）保留，作为验收。

### 5.2 key 的归属

- `provider_profiles` 加 `owner_id / team_id / shared`；`credential_ref = profile.<handle>.<name>`；
  密钥仍在 0600 的 `secrets.json`，只写不读；`SecretStore.get()` 的环境变量回退只对管理员的 profile 开放
  （否则任何人可以把 profile 指向服务器环境里的 `CLAUDE_CODE_OAUTH_TOKEN`）。
- **谁的 key 跑谁的 run**：worker 用 `tasks.created_by` 的；主会话用触发这次运行的人的；
  `spawn_worker` 传下 `created_by`。解析链：显式 `profile_id` → 用户默认 → 团队默认 → 全局默认 → 服务器登录。
  `runs.created_by` + 现有的无密快照 `profile_snapshot` 是审计线索。
- `env_for` 对没选中的凭据变量一律置空（`ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_BASE_URL`），
  避免运维的 `~/.claude` 登录顺着 `setting_sources=user` 漏进别人的 run。dsh 的子进程做法。

### 5.3 转换代理：抄 cc-switch 的 `proxy/providers/transform.rs + streaming.rs`

- 位置：`backend/app/proxy/`（`convert.py / streaming.py / router.py`），挂在同一个 FastAPI 进程上，
  前缀 `/proxy/{route_token}/v1/messages`、`/v1/messages/count_tokens`（cc-switch 没有，估算返回）。
  不起第二个进程，生命周期跟主进程走。
- 每个 run 一枚 `route_token`（`_spec` 时签发，run 结束吊销），映射到 `{base_url, api_key, model_map, extra_headers}`。
  Claude Code 只看到 `ANTHROPIC_BASE_URL=http://127.0.0.1:8787/proxy/<token>` 和
  `ANTHROPIC_AUTH_TOKEN=<token>`；真 key 只在代理进程内解析。这就是 cc-switch 的 `PROXY_MANAGED` 占位。
- 模型别名：注入 `ANTHROPIC_DEFAULT_HAIKU/SONNET/OPUS_MODEL`，代理按 `model_mapper.rs` 的规则把
  `claude-*` 映射到厂商模型（`{haiku, sonnet, opus, default}` 四档，`*` 兜底）。
- 转换规则按 transform.rs 逐条搬：system 数组拍平、`x-anthropic-billing-header` 前缀剥离、
  `cache_control / metadata / betas` 丢弃、`tool_use ↔ tool_calls`、`tool_result`（含数组内容、`is_error`）、
  图片、`tool_choice`、o 系列 `max_completion_tokens`、`stop_sequences → stop`、`stream_options.include_usage`、
  usage 里扣掉 cache 计数。流式按 streaming.rs 的状态机：`message_start → content_block_* → message_delta → message_stop`，
  first-finish_reason-wins，`message_delta` 延后到 `[DONE]`。
- 复杂上游 schema 的清洗（cc-switch 的 `clean_schema` / Moonshot 的 `$ref` 处理）一并带上；NIM 实测需要。
- 不搬：Responses API、Gemini、三个 OAuth 流、熔断器（先做单上游），托盘热切换副作用。
- 验收：Claude Code CLI 指向代理，`--model sonnet` 跑 `PONG` 和「创建文件」两个用例；单元测试
  用录制的 OpenAI 分片断言事件序列。测试模型 `nvidia/nemotron-3-super-120b-a12b`（工具调用 2 秒内）。

## 6. 通知与权限

- 通知种类（抄 AO 四种再加我们的）：`mention / dm / member_added / invite_accepted / decision_pending /
  artifact / run_finished / feedback`。写入点就是现有发事件的地方，每处一行 `notifications.insert + bus.emit("notification", user_id=)`。
- `EventBus.emit` 加 `user_id`；`/ws` 按连接持有 `{user, visible_sessions, my_convs}` 过滤，
  `conversation_member` 事件触发刷新；`since=` 回放同样过滤。
- 权限表（两个 helper `require_team(user, team, min_role)`、`require_conv(user, conv, owner)`）：

| 动作 | 谁 |
|---|---|
| 建项目、派任务 | 团队成员 |
| 删项目 / worktree | 团队 admin |
| 取消 / 重试 / 审阅 / 合并 / 反馈 | 该任务会话的成员 |
| Room 裁决 | 项目所属团队的成员（硬规则 1 的「人」在团队边界） |
| 改会话标题 / agent_reply / 会话成员 | 会话 owner |
| 群成员 | 任何群成员 |
| profile 改删 | owner 或 admin |
| 全局默认、CC 登录令牌、admin 路由 | 管理员 |

## 7. 前端改动（保持 AO 壳子不动）

新增三个文件：`Login.jsx`（登录/注册/邀请落地，`?invite=`）、`Members.jsx`（成员选择与列表，
群、会话、团队三处复用）、`Inbox.jsx`（右侧抽屉，克隆 `RoomDrawer`）。其余是改：

- `api.js`：401 不再 `window.prompt`，改回调到登录页；所有带 `author` 的调用去掉该参数；新增 auth / teams /
  conversations / notifications / agents / presets 调用。
- `store.js`：`me、authState、teamId、conversations、notifications、agents`；`LIST_EVENTS` 加
  `conversation / conversation_member / chat_message → conversations`，`notification → notifications`，`agent_definition → agents`。
- `Sidebar.jsx`：标题变团队切换；「通知」行带未读；底部身份 = 头像首字 + 名字 + 退出。
- `Chat.jsx`：会话列表分 私聊 / 群；「＋ 私聊」选人；「拉个群」选成员；头部成员 chip；`@` 自动补全。
- `SessionView.jsx`：成员 chip（owner 可加人）；多人时输入框下提示「多人会话里只有 @主 agent 时它才回复」；
  气泡按作者左右分；输入框旁 Agent Definition 下拉；`Bypass Permissions` 标签改成所选 definition 的权限档。
- `NewTaskDialog.jsx`：definition 下拉替换静态 chip。
- `Settings.jsx`：通用（改名、改密码）、团队（成员表、邀请链接、撤销）、模型（我的 / 团队共享，预置选择器按类别分组，
  key 前缀建议，「拉取模型列表」）、Agent 定义（dsh roster）、Claude Code（管理员）。
- `Board.jsx`：非成员卡片 🔒。`Home.jsx`：显示当前团队。

## 8. 实施顺序（每批可单独部署）

1. **身份 + 团队 + 对话表**（后端 + 单元测试 + 迁移演练）。没有这个后面都是空中楼阁。
2. **前端登录、团队、聊天、成员、@**。
3. **Agent Definition + agent 间工具 + AO 提示词**。
4. **预置 + per-user key + 代理接入 + 设置页**（代理本体已在并行移植）。
5. **通知 + 权限收口 + 文档 + 部署**。

## 9. 验证

- `backend/tests/test_workbench.py` 增：密码哈希与令牌、邀请接受、私聊 get-or-create、@ 规则（1 人 vs 2 人）、
  非成员 403、旧库迁移（先用旧 DDL 灌数据再打开）、definition 合并进 RunSpec、profile 解析链。
- `backend/tests/test_proxy.py`：转换与流式的离线断言 + TestClient 假上游。
- 真机：两个浏览器两个账号，一个建团一个用邀请链接进；私聊互发；把对方拉进 worker 会话；
  两人都不 @ 时 agent 不动，@ 后回复且能叫出名字；用 NVIDIA key 建 profile，兼容性检查通过，
  用它跑一个 worker；Room 冲突升级到两人都能看见的裁决卡。
- 部署前：`PRAGMA wal_checkpoint(TRUNCATE)` 后备份 `/var/lib/workbench/workbench.db`，先在 scp 下来的副本上跑迁移。

## 10. 抄代码的政策与出处

0003 说「只借想法不抄代码」。这个里程碑的负责人改了主意：参考项目已经解决的问题，直接搬代码，
不重造。0003 已加注说明。搬来的东西和许可证：

| 搬了什么 | 来源 | 落点 | 许可证 |
|---|---|---|---|
| Anthropic ↔ OpenAI Chat 转换、SSE 重编码、模型别名映射 | cc-switch `src-tauri/src/proxy/providers/transform.rs, streaming.rs, model_mapper.rs`（其本身注明源自 anthropic-proxy-rs） | `backend/app/proxy/` | MIT（cc-switch, © 2025 Jason Young） |
| 88 个 Claude Code 厂商预置 | cc-switch `src/config/claudeProviderPresets.ts` | `backend/app/provider_presets.py` | MIT（cc-switch） |
| orchestrator / worker 系统提示词、`[from <sender>]` 前缀、通知语义 | Agent Orchestrator `backend/internal/session_manager/prompt.go`, `cli/send.go` | `backend/app/prompts.py`、投递路径 | Apache-2.0（Agent Orchestrator） |
| 设置页文案、key 校验、模型发现、agent preset roster 交互 | deepseek-harness `packages/client/ui-settings-models`, `packages/llm/llm-pi-ai/src/discovery.ts` | 前端设置页 | MIT（deepseek-harness, © 2026 DeepSeek） |
| Room 的三档作用域语义与礼仪提示 | 本仓库历史 `fb898cd`（AgentRoom MVP） | `room.py`、`prompts.py` | 本仓库 |

两条附加约束（来自纠错代理）：`/proxy/*` 在 0.0.0.0 上对外可达，除了每个 run 一枚随机 route token 之外，
再限制只接受回环地址的客户端；Claude Code 会把 `settings.json` 的 `env` 覆盖到启动环境之上，
所以 per-user key 生效靠的是二进制里 `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` 的优先级，
非默认 profile 应改用 `setting_sources=['project','local']`，第四批做真机验证。

## 11. 明确不做

- 加好友、组织架构、飞书对接、手机推送、LAN 监听。
- OAuth 类厂商、Responses / Gemini 协议、多上游熔断。
- 对外 `/mcp` 端点、`room_edit_text`。
- 第二个模型运行时（硬规则 2）。
