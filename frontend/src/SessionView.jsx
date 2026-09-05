import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import Preview, { AgentPreview, DiffText } from "./Preview";
import Composer from "./Composer";
import { Avatar, AvatarStack, Badge, Button, Dialog, Dot, I, STATUS_LABEL, Text, fmtDur, fmtRel, fmtTime, permissionLabel, toneOf } from "./ui";

const TERMINAL = ["succeeded", "failed", "cancelled", "interrupted", "exhausted"];
const EXPLORE = new Set(["Read", "Grep", "Glob", "ToolSearch", "LS", "WebFetch", "WebSearch"]);
const EDIT = new Set(["Edit", "MultiEdit", "Write", "NotebookEdit"]);

// ---- timeline model (AO's ChatTimelineItems, reduced to what CC emits) -----
function buildTimeline(messages, runs) {
  const results = new Map();
  for (const m of messages) for (const b of m.blocks || []) if (b.type === "tool_result") results.set(b.tool_use_id, b);
  const items = [];
  let lastRun = null;
  const push = (it) => items.push(it);
  for (const m of messages) {
    if (m.run_id && m.run_id !== lastRun) { const r = runs.find((x) => x.id === m.run_id); if (r) push({ kind: "run", id: `run-${r.id}`, run: r, n: runs.indexOf(r) + 1 }); lastRun = m.run_id; }
    if (m.role === "user" && m.author !== "tool") {
      const text = (m.blocks || []).filter((b) => b.type === "text").map((b) => b.text).join("\n");
      if (text) push({ kind: "user", id: m.id, author: m.author, userId: m.user_id, handle: m.handle, text, at: m.created_at, meta: m.meta });
      continue;
    }
    if (m.role !== "assistant") continue;
    for (const b of m.blocks || []) {
      if (b.type === "text" && b.text) {
        // a question asked with ask_human is rendered as a card with one-click answers
        const ask = m.meta?.needs_human ? { options: m.meta.options || [] } : null;
        push({ kind: ask ? "ask" : "text", id: m.id + ":" + items.length, text: b.text, at: m.created_at, error: m.error, ask, agent: m.author });
      }
      if (b.type === "tool_use") {
        const res = results.get(b.id);
        const last = items[items.length - 1];
        if (EXPLORE.has(b.name)) {
          if (last?.kind === "explore") { last.tools.push({ block: b, result: res }); continue; }
          push({ kind: "explore", id: b.id, tools: [{ block: b, result: res }] });
        } else push({ kind: "tool", id: b.id, block: b, result: res });
      }
    }
  }
  return items;
}
const resultText = (c) => typeof c === "string" ? c : Array.isArray(c) ? c.map((x) => (x.type === "text" ? x.text : JSON.stringify(x))).join("\n") : JSON.stringify(c ?? "", null, 1);
const shortPath = (p, root) => {
  if (!p) return "";
  if (root && p.startsWith(root + "/")) return p.slice(root.length + 1);
  return p.replace(/^.*?\/(?=[^/]+\/[^/]+$)/, "…/");
};

function ToolRow({ block, result, root }) {
  const name = block.name; const inp = block.input || {}; const failed = result?.is_error;
  const [open, setOpen] = useState(false);
  let icon = <I.plug className="w-3.5 h-3.5" />, head;
  if (EDIT.has(name)) { icon = <I.pencil className="w-3.5 h-3.5" />; head = <><span className="text-[var(--muted)]">编辑</span> <span className="mono">{shortPath(inp.file_path, root)}</span></>; }
  else if (name === "Bash") { icon = <I.terminal className="w-3.5 h-3.5" />; head = <span className="mono">$ {(inp.command || "").replace(/^cd "[^"]*" && /, "").slice(0, 120)}</span>; }
  else if (name.startsWith("mcp__")) { head = <><span className="text-[var(--muted)]">{name.replace(/^mcp__(\w+)__/, "$1 · ")}</span> <span className="mono text-[var(--muted)]">{Object.entries(inp).map(([k, v]) => `${k}=${JSON.stringify(v).slice(0, 40)}`).join(" ").slice(0, 120)}</span></>; }
  else head = <><span>{name}</span> <span className="mono text-[var(--muted)]">{JSON.stringify(inp).slice(0, 100)}</span></>;
  return (
    <div className="text-[12px]">
      <button onClick={() => setOpen(!open)} className={`row w-full text-left py-0.5 hover:bg-[var(--subtle)] rounded px-1 ${failed ? "text-red-600" : ""}`}>
        <span className="text-[var(--muted)]">{icon}</span><span className="truncate flex-1">{head}</span>{result === undefined ? <span className="text-[var(--faint)] animate-breathe">运行中</span> : <I.chev className={`w-3 h-3 text-[var(--faint)] ${open ? "rotate-90" : ""}`} />}
      </button>
      {open && <pre className="whitespace-pre-wrap text-[11px] bg-[var(--subtle)] rounded p-2 mt-1 max-h-56 overflow-auto text-[var(--muted)]">{name === "Bash" ? `$ ${inp.command}\n\n` : ""}{EDIT.has(name) && inp.new_string ? `- ${(inp.old_string || "").slice(0, 400)}\n+ ${inp.new_string.slice(0, 400)}\n\n` : ""}{EDIT.has(name) && inp.content ? inp.content.slice(0, 800) + "\n\n" : ""}{result ? resultText(result.content).slice(0, 3000) : ""}</pre>}
    </div>
  );
}
function ExploreGroup({ tools, root }) {
  const [open, setOpen] = useState(false);
  const files = new Set(tools.map((t) => t.block.input?.file_path || t.block.input?.pattern || t.block.input?.query || t.block.input?.url).filter(Boolean));
  return (
    <div className="text-[12px]">
      <button onClick={() => setOpen(!open)} className="row text-[var(--muted)] hover:text-[var(--text)] px-1 py-0.5">浏览了 {files.size || tools.length} 处 <I.chev className={`w-3 h-3 ${open ? "rotate-90" : ""}`} /></button>
      {open && <div className="pl-2 border-l border-[var(--border)] ml-2">{tools.map((t) => <ToolRow key={t.block.id} block={t.block} result={t.result} root={root} />)}</div>}
    </div>
  );
}
function diffStats(diff) {
  const files = []; let cur = null;
  for (const l of (diff || "").split("\n")) {
    const m = l.match(/^diff --git a\/(.+?) b\/(.+)$/);
    if (m) { cur = { path: m[2], add: 0, del: 0 }; files.push(cur); continue; }
    if (!cur) continue;
    if (l.startsWith("+") && !l.startsWith("+++")) cur.add++;
    else if (l.startsWith("-") && !l.startsWith("---")) cur.del++;
  }
  return files;
}

// ---- the view ----------------------------------------------------------------
export default function SessionView({ session, task, messages, streams, runStatus, profileData, me, agents, room, artifacts, onCancel, onRetry, onOpenSettings, onNewTask, onOpenMain, refetch, onBack }) {
  const [choice, setChoice] = useState(() => { try { return localStorage.getItem("wb.model") || ""; } catch { return ""; } });
  const [note, setNote] = useState("");
  const [stuck, setStuck] = useState(false);
  const [members, setMembers] = useState(false);
  const [inspector, setInspector] = useState(() => { try { return localStorage.getItem("wb.inspector") || "summary"; } catch { return "summary"; } });
  const [diff, setDiff] = useState(null);
  const bottomRef = useRef(null); const listRef = useRef(null);
  const runs = session?.runs || [];
  const items = useMemo(() => buildTimeline(messages, runs), [messages, runs]);
  const latest = task?.latest_run || runs[runs.length - 1];
  const live = latest && (runStatus[latest.id]?.status || latest.status);
  const streaming = latest ? streams[latest.id] : "";
  const active = live && !TERMINAL.includes(live);
  const options = useMemo(() => {
    const out = [{ value: "", label: defaultLabel(profileData) }];
    for (const p of profileData.profiles || []) for (const m of p.models?.length ? p.models : [null]) out.push({ value: `${p.id}|${m || ""}`, label: `${p.display_name || p.name} ▸ ${m || "默认"}` });
    return out;
  }, [profileData]);
  const [profileId, model] = choice.split("|");
  // one session, one person deciding what it runs on: everyone else talks to
  // the agent, and the server refuses their overrides anyway
  const isOwner = !session?.owner_id || session.owner_id === me?.id || !!me?.is_admin;

  useEffect(() => { if (!stuck) bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages.length, streaming, stuck]);
  useEffect(() => { if (task?.id) api.taskDiff(task.id).then(setDiff).catch(() => setDiff(null)); else setDiff(null); }, [task?.id, task?.latest_run?.status, artifacts?.length]);
  useEffect(() => { try { localStorage.setItem("wb.inspector", inspector); } catch { /* ignore */ } }, [inspector]);
  const onScroll = () => { const el = listRef.current; if (el) setStuck(el.scrollHeight - el.scrollTop - el.clientHeight > 120); };

  // The server decides whether the agent is actually woken (the @ rule), and
  // says so; a message it kept for the people only must not look delivered.
  async function send(text) {
    const r = await api.send(session.id, text, isOwner ? profileId || undefined : undefined, isOwner ? model || undefined : undefined);
    setNote(r?.delivered === false ? r.note || "已发给会话里的人，agent 没有被叫到。" : "");
    return r;
  }
  if (!session) return <div className="flex-1 flex items-center justify-center text-[var(--muted)]">选择左侧「主 agent」或一个任务</div>;
  const files = diffStats(diff?.diff);
  const root = session.workspace?.path || session.workspace?.root_path || null;
  const insTab = (k, icon, title) => <button onClick={() => setInspector(inspector === k ? null : k)} title={title} className={`btn btn-ghost btn-sm ${inspector === k ? "bg-[var(--hover)]" : ""}`}>{icon}</button>;

  return (
    <div className="flex h-full min-h-0">
      <div className="flex-1 min-w-0 flex flex-col bg-white">
        {/* AO's tab strip: session tab, +, toolbar, inspector toggles */}
        <div className="row h-12 border-b border-[var(--border)] shrink-0 pr-2">
          <button onClick={onBack} className="btn btn-ghost btn-sm ml-1" title="回看板"><I.arrowleft className="w-4 h-4" /></button>
          <div className="row h-full px-3 border-r border-[var(--border)] max-w-[260px]">
            {session.kind === "main" ? <I.sitemap className="w-4 h-4 text-[var(--muted)]" /> : <I.bot className="w-4 h-4 text-orange-500" />}
            <span className="text-[13px] font-medium truncate">{task ? task.title : "主 agent"}</span>
            {live && <Dot status={live === "succeeded" ? "in_review" : live} />}
          </div>
          <button onClick={onNewTask} className="btn btn-ghost btn-sm" title="新任务"><I.plus className="w-4 h-4" /></button>
          <span className="text-[11px] text-[var(--muted)] mono truncate">{task?.branch ? task.branch : session.kind === "main" ? "项目目录" : task?.isolation === "main" ? "项目目录" : ""}</span>
          <div className="flex-1" />
          <AgentPicker session={session} agents={agents} live={active} canEdit={isOwner} onChanged={() => refetch("sessions")} />
          <button className="btn btn-ghost btn-sm" onClick={() => setMembers(true)} title="会话成员">
            <AvatarStack people={(session.members || []).map((m) => ({ id: m.member_id, name: m.name || m.display_name, handle: m.handle, agent: m.member_kind === "agent" }))} size={18} max={3} />
            <span className="text-[12px] text-[var(--muted)]">{session.member_count || 0}</span>
          </button>
          {active && <button onClick={() => onCancel(latest.id)} className="btn btn-sm text-red-600" title="停止这次运行">停止</button>}
          {task && !active && live && live !== "succeeded" && <button onClick={() => onRetry(task.id)} className="btn btn-sm">重试</button>}
          {session.kind !== "main" && <button onClick={onOpenMain} className="btn btn-primary btn-sm" title="主 agent"><I.sitemap className="w-4 h-4" /></button>}
          <span className="w-px h-5 bg-[var(--border)] mx-1" />
          {insTab("summary", <I.list className="w-4 h-4" />, "摘要")}
          {insTab("preview", <I.globe className="w-4 h-4" />, "预览（agent 提交的成果）")}
          {insTab("files", <I.files className="w-4 h-4" />, "变更文件")}
        </div>

        <RunBanner run={latest} status={live} />

        <div ref={listRef} onScroll={onScroll} className="flex-1 overflow-y-auto min-h-0">
          <div className="max-w-[760px] mx-auto px-4 py-4 space-y-2">
            {items.length === 0 && !streaming && (
              <div className="text-center text-[var(--muted)] mt-16 max-w-md mx-auto">{session.kind === "main" ? "跟主 agent 说点什么。它会直接改代码，或把独立的活派给 worker（各自在自己的分支上后台跑），你在看板里跟进。" : "这个 worker 还没有输出。"}</div>
            )}
            {items.map((it) => {
              if (it.kind === "run") return <RunDivider key={it.id} run={it.run} n={it.n} />;
              if (it.kind === "user") {
                const mine = it.userId && it.userId === me?.id;
                return (
                  <div key={it.id} className={`flex gap-2 pt-2 ${mine ? "flex-row-reverse" : ""}`}>
                    <Avatar name={mine ? me?.display_name || it.author : it.author} handle={mine ? me?.handle : it.handle} size={26} agent={!mine && !it.userId} />
                    <div className={`max-w-[85%] rounded-xl px-4 py-3 text-[13.5px] whitespace-pre-wrap ${mine ? "bg-[var(--accent)] text-white" : "bg-[var(--subtle)]"}`}>
                      <div className={`text-[11px] mb-1 ${mine ? "text-white/70 text-right" : "text-[var(--muted)]"}`}>{mine ? me?.display_name || it.author : it.author}</div>{it.text}
                      <div className={`text-[10px] mt-1 text-right ${mine ? "text-white/60" : "text-[var(--faint)]"}`}>{fmtTime(it.at)}</div>
                    </div>
                  </div>
                );
              }
              if (it.kind === "ask") return <AskCard key={it.id} item={it} session={session} onAnswer={send} />;
              if (it.kind === "explore") return <ExploreGroup key={it.id} tools={it.tools} root={root} />;
              if (it.kind === "tool") return <ToolRow key={it.id} block={it.block} result={it.result} root={root} />;
              return <div key={it.id} className={`py-1 ${it.error ? "text-red-700" : ""}`}><Text text={it.text} /></div>;
            })}
            {streaming && <div className="py-1"><Text text={streaming} /><span className="animate-breathe">▍</span></div>}
            {active && !streaming && <div className="row text-[12px] text-[var(--muted)]"><Dot color="var(--working)" breathe /> Claude Code 正在工作…</div>}
            {task && files.length > 0 && (
              <div className="card mt-3">
                <div className="row px-3 py-2 text-[12px]"><span className="text-[var(--muted)]">{files.length} 个文件已更改</span><div className="flex-1" /><button onClick={() => setInspector("files")} className="text-[12px] hover:underline">审阅</button></div>
                {files.map((f) => <div key={f.path} className="row px-3 py-1.5 text-[12px] border-t border-[var(--border)]"><I.filediff className="w-3.5 h-3.5 text-[var(--muted)]" /><span className="mono truncate flex-1">{f.path}</span><span className="text-green-600 mono">+{f.add}</span><span className="text-red-600 mono">−{f.del}</span></div>)}
              </div>
            )}
            {latest?.error && <div className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">{latest.error}</div>}
            <div ref={bottomRef} />
          </div>
        </div>
        {stuck && <button onClick={() => { setStuck(false); bottomRef.current?.scrollIntoView(); }} className="self-center -mt-8 mb-1 z-10 btn btn-sm rounded-full shadow">↓ 回到底部</button>}

        <Composer
          onSend={send}
          people={session.members || []}
          autoFocus
          placeholder={session.kind === "main" ? "给主 agent 发消息…" : active ? "给这个 worker 发消息（会追加进当前运行）…" : "给这个 worker 发消息…"}
          hint={note || (session.human_count > 1
            ? `这个会话有 ${session.human_count} 个人：只有 @${session.agent_name || "主 agent"} 的消息才会交给 agent`
            : `以 ${me?.display_name || "你"} 的身份发送`)}
          extra={(
            <div className="row gap-2 pb-2">
              {isOwner ? (
                <>
                  <button className="btn btn-ghost btn-xs" onClick={onOpenSettings} title="配置模型"><I.wallet className="w-3.5 h-3.5" />模型</button>
                  <select className="text-[12px] bg-transparent hover:bg-[var(--subtle)] rounded px-1.5 h-6 max-w-[260px]" value={choice}
                    onChange={(e) => { setChoice(e.target.value); try { localStorage.setItem("wb.model", e.target.value); } catch { /* ignore */ } }} title="模型">
                    {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </>
              ) : (
                <span className="row gap-1 text-[11px] text-[var(--faint)]" title="模型和 agent 定义由会话 owner 决定">
                  <I.lock className="w-3.5 h-3.5" />模型由会话 owner 决定
                </span>
              )}
              <div className="flex-1" />
              <Badge tone="outline" title="这个 agent 定义允许的操作范围">{permissionLabel(session.agent_definition?.permission_mode)}</Badge>
            </div>
          )}
        />
      </div>

      {members && <SessionMembers session={session} me={me} onClose={() => setMembers(false)} onChanged={() => refetch("sessions")} />}

      {inspector && (
        <aside className="w-[340px] shrink-0 border-l border-[var(--border)] bg-white overflow-y-auto">
          {inspector === "summary" && <Summary session={session} task={task} runs={runs} room={room} refetch={refetch} live={live} artifacts={artifacts} />}
          {inspector === "preview" && (
            <div className="p-3 space-y-4">
              <div>
                <div className="label mb-2">agent 打开的</div>
                <AgentPreview session={session} onCleared={() => refetch("sessions")} />
              </div>
              <div>
                <div className="label mb-2">提交的成果</div>
                <Preview artifacts={artifacts} tasks={task ? [task] : []} selectedTaskId={task?.id || null} onSelectTask={() => {}} />
              </div>
            </div>
          )}
          {inspector === "files" && (
            <div className="p-3">
              <div className="label mb-2">变更文件{task?.branch ? ` · ${task.branch}` : ""}</div>
              {!task ? <div className="text-[12px] text-[var(--muted)]">主 agent 直接在项目目录里改；看 git 状态请在项目里执行 git diff。</div>
                : diff === null ? <div className="text-[12px] text-[var(--muted)]">加载中…</div>
                : diff.available ? <DiffText diff={diff.diff} /> : <div className="text-[12px] text-amber-600">无法生成 diff：{diff.reason}</div>}
            </div>
          )}
        </aside>
      )}
    </div>
  );
}

function Summary({ session, task, runs, room, refetch, live, artifacts }) {
  const [note, setNote] = useState(""); const [msg, setMsg] = useState("");
  async function run(fn) { setMsg(""); try { const r = await fn(); setMsg(r?.error ? `失败：${r.error}` : "完成"); refetch("tasks"); } catch (e) { setMsg(`失败：${e.message}`); } }
  const claims = (room?.claims || []).filter((c) => runs.some((r) => r.id === c.run_id));
  const pending = (room?.pending_decisions || []).filter((d) => runs.some((r) => r.id === d.blocked_run_id) || task);
  const cost = runs.reduce((s, r) => s + (r.cost_usd || 0), 0);
  const events = [];
  for (const r of runs) {
    events.push({ at: r.created_at, label: `运行 #${runs.indexOf(r) + 1} 创建${r.attempt_no > 1 ? `（第 ${r.attempt_no} 次尝试）` : ""}` });
    if (r.started_at) events.push({ at: r.started_at, label: `开始 · ${r.profile_snapshot?.model || "默认模型"}` });
    if (r.ended_at) events.push({ at: r.ended_at, label: `${STATUS_LABEL[r.status] || r.status}${r.cost_usd ? ` · $${r.cost_usd.toFixed(3)}` : ""}${r.num_turns ? ` · ${r.num_turns} 轮` : ""}`, tone: toneOf(r.status === "succeeded" ? "in_review" : r.status), error: r.error });
  }
  for (const a of artifacts || []) if (task && a.task_id === task.id) for (const f of a.feedback || []) events.push({ at: f.created_at, label: `${f.author} 反馈 ${a.title} v${f.version}：${f.verdict}`, tone: "var(--review)" });
  events.sort((a, b) => (a.at < b.at ? 1 : -1));
  return (
    <div className="p-4 space-y-5 text-[12px]">
      <section>
        <div className="label mb-2">合并</div>
        {!task ? <div className="text-[var(--muted)]">主 agent 直接在项目目录里工作，没有独立分支要合并。</div> : (
          <>
            <div className="mb-2">{task.merge_status === "merged" ? <span style={{ color: "var(--merged)" }}>已合并到主分支</span> : task.review_status === "approved" ? <span style={{ color: "var(--ready)" }}>审阅通过，可以合并</span> : task.review_status === "changes_requested" ? <span style={{ color: "var(--needs)" }}>已要求修改，等 worker 回应</span> : <span className="text-[var(--muted)]">还没有审阅。审阅通过 ≠ 合并：合并是单独的一步。</span>}</div>
            <input className="input mb-2" placeholder="审阅备注（要求修改时会送回 worker）" value={note} onChange={(e) => setNote(e.target.value)} />
            <div className="row flex-wrap gap-1">
              <button className="btn btn-sm" onClick={() => run(() => api.reviewTask(task.id, "approve", note))}>审阅通过</button>
              <button className="btn btn-sm" onClick={() => run(() => api.reviewTask(task.id, "request_changes", note))}>要求修改</button>
              <button className="btn btn-primary btn-sm" disabled={task.review_status !== "approved" || task.merge_status === "merged"} onClick={() => run(() => api.mergeTask(task.id))}><I.merge className="w-3.5 h-3.5" /> 合并到主分支</button>
            </div>
            {msg && <div className="text-[var(--muted)] mt-1">{msg}</div>}
          </>
        )}
      </section>
      <section>
        <div className="label mb-2">会话</div>
        <div className="space-y-1 text-[var(--muted)]">
          <div>{session.kind === "main" ? "主 agent" : "worker"} · {runs.length} 次运行{cost ? ` · 共 $${cost.toFixed(3)}` : ""}</div>
          {task && <div>隔离：{task.isolation === "worktree" ? `独立 worktree（${task.branch}）` : "项目目录"} · 编辑模式 {task.edit_mode === "shared" ? "共享（实验）" : "独占"}</div>}
          {task?.depends_on?.length > 0 && <div>依赖 {task.depends_on.length} 个任务</div>}
        </div>
      </section>
      <section>
        <div className="label mb-2">Room</div>
        {claims.length === 0 && pending.length === 0 && <div className="text-[var(--muted)]">没有认领的文件。worker 通过 room_claim 认领；同一工作区抢同一路径会停下来等你。</div>}
        {claims.map((c) => <div key={c.id} className="row"><span>🔒</span><span className="mono truncate">{c.path}</span>{c.note && <span className="text-[var(--muted)] truncate">· {c.note}</span>}</div>)}
        {pending.map((d) => <DecisionCard key={d.id} d={d} />)}
      </section>
      <section>
        <div className="label mb-2">活动</div>
        <div className="relative pl-4">
          <div className="absolute left-[3px] top-1 bottom-1 w-px bg-[var(--border)]" />
          {events.slice(0, 12).map((e, i) => (
            <div key={i} className="relative mb-3">
              <span className="absolute -left-4 top-1 dot" style={{ background: e.tone || "var(--idle)" }} />
              <div className="font-medium" style={{ color: e.tone }}>{e.label}</div>
              {e.error && <div className="text-red-600 text-[11px]">{e.error}</div>}
              <div className="text-[11px] text-[var(--faint)] mono">{fmtRel(e.at)}</div>
            </div>
          ))}
          {events.length === 0 && <div className="text-[var(--muted)]">还没有活动。</div>}
        </div>
      </section>
    </div>
  );
}

export function DecisionCard({ d }) {
  const [reason, setReason] = useState(""); const [busy, setBusy] = useState(false);
  const s = d.subject;
  async function act(decision) { setBusy(true); try { await api.decide(d.id, decision, reason); } finally { setBusy(false); } }
  return (
    <div className="card border-red-200 p-2 mt-2">
      <div className="mono">{s.path}</div>
      <div className="text-[var(--muted)]">「{s.requester?.task_title}」想要，「{s.holder?.task_title}」持有</div>
      <input className="input my-1" placeholder="理由（会送到两个 worker）" value={reason} onChange={(e) => setReason(e.target.value)} />
      <div className="row gap-1"><button className="btn btn-primary btn-sm" disabled={busy} onClick={() => act("approve")}>转给请求方</button><button className="btn btn-sm" disabled={busy} onClick={() => act("reject")}>维持持有方</button></div>
    </div>
  );
}

// A work session is a group chat: its owner decides who can see it, and a
// non-member sees neither its messages nor its task description.
function SessionMembers({ session, me, onClose, onChanged }) {
  const [users, setUsers] = useState([]);
  const [busy, setBusy] = useState("");
  const isOwner = session.owner_id === me?.id || me?.is_admin;
  const has = new Set((session.members || []).map((m) => m.member_id));
  useEffect(() => { api.users({}).then((u) => setUsers(u.filter((x) => !has.has(x.id)))).catch(() => {}); }, [session.id]);   // eslint-disable-line
  const act = async (fn, id) => { setBusy(id); try { await fn(); onChanged(); onClose(); } finally { setBusy(""); } };
  return (
    <Dialog title="会话成员" onClose={onClose} width={400}>
      <div className="hint mb-3">只有这里的人能看到这个会话的消息和成果。</div>
      {(session.members || []).map((m) => (
        <div key={m.member_id} className="row py-1.5">
          <Avatar name={m.name || m.display_name} handle={m.handle} agent={m.member_kind === "agent"} size={28} />
          <span className="flex-1 min-w-0">
            <span className="block truncate">{m.name || m.display_name}{m.member_id === me?.id && <span className="text-[var(--muted)]"> （你）</span>}</span>
            <span className="block text-[11px] text-[var(--muted)]">{m.member_kind === "agent" ? "这个会话的 agent" : `@${m.handle}`}</span>
          </span>
          {isOwner && m.member_kind === "user" && m.member_id !== session.owner_id && (
            <Button size="xs" kind="danger" disabled={busy === m.member_id}
              onClick={() => act(() => api.removeConvMember(session.conversation_id, m.member_id), m.member_id)}>移出</Button>
          )}
        </div>
      ))}
      {isOwner && (
        <>
          <div className="label mt-4 mb-1.5">加人</div>
          {users.length === 0 && <div className="hint">没有别人可以加了。</div>}
          <div className="max-h-[200px] overflow-y-auto">
            {users.map((u) => (
              <div key={u.id} className="row py-1">
                <Avatar name={u.display_name} handle={u.handle} size={24} />
                <span className="flex-1 truncate">{u.display_name} <span className="text-[var(--muted)]">@{u.handle}</span></span>
                <Button size="xs" disabled={busy === u.id} onClick={() => act(() => api.addConvMember(session.conversation_id, { user_id: u.id }), u.id)}><I.plus />加入</Button>
              </div>
            ))}
          </div>
        </>
      )}
    </Dialog>
  );
}

// Which agent definition this session runs as. Rebinding takes effect on the
// next run, so the server refuses while one is live and so do we.
function AgentPicker({ session, agents, live, canEdit, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const bound = session.agent_definition || {};
  const fit = (agents?.items || []).filter((a) => (session.kind === "main" ? ["orchestrator", "any"] : ["worker", "any"]).includes(a.role));
  const change = async (id) => {
    setBusy(true); setErr("");
    try { await api.patchSession(session.id, { agent_definition_id: id }); onChanged(); }
    catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };
  if (!canEdit) {
    return <span className="row gap-1 text-[12px] text-[var(--muted)]" title="agent 定义由会话 owner 决定"><I.bot className="w-3.5 h-3.5" />{bound.name || "内置"}</span>;
  }
  return (
    <span className="row gap-1" title={live ? "会话正在运行，等它结束再换" : bound.missing ? "原来的定义已被删除，现在用的是内置的" : "这个会话用哪种 agent"}>
      {bound.missing && <I.info className="w-3.5 h-3.5 text-amber-500" />}
      <select className="text-[12px] bg-transparent hover:bg-[var(--subtle)] rounded px-1.5 h-7 max-w-[150px] disabled:opacity-50"
        value={bound.id || ""} disabled={live || busy || fit.length === 0} onChange={(e) => change(e.target.value)}>
        {fit.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
      </select>
      {err && <span className="text-[11px] text-red-600 max-w-[160px] truncate">{err}</span>}
    </span>
  );
}

// ask_human: the agent stopped and is waiting. One click answers it.
function AskCard({ item, session, onAnswer }) {
  const [busy, setBusy] = useState(false);
  const at = `@${session.agent_name || "主 agent"} `;
  const answer = async (opt) => { setBusy(true); try { await onAnswer(at + opt); } finally { setBusy(false); } };
  return (
    <div className="card border-[var(--agent)]/40 bg-orange-50/40 p-3 my-2">
      <div className="row text-[11px] font-semibold mb-1.5" style={{ color: "var(--agent)" }}><I.help className="w-3.5 h-3.5" />需要你回答</div>
      <div className="text-[13.5px] leading-relaxed whitespace-pre-wrap">{item.text}</div>
      {item.ask.options.length > 0 && (
        <div className="row flex-wrap gap-1.5 mt-2.5">
          {item.ask.options.map((o) => <Button key={o} size="sm" disabled={busy} onClick={() => answer(o)}>{o}</Button>)}
        </div>
      )}
      {item.ask.options.length === 0 && <div className="hint mt-2">在下面直接回复它。</div>}
    </div>
  );
}

function defaultLabel(pd) {
  const p = (pd.profiles || []).find((x) => x.id === pd.default_profile_id);
  if (p) return `${p.display_name || p.name} ▸ ${pd.default_model || p.model || "默认"}`;
  return pd.default_model ? `Claude Code ▸ ${pd.default_model}` : "Claude Code · 默认";
}
function RunDivider({ run, n }) {
  const bits = [`运行 #${n}`, run.attempt_no > 1 ? `第 ${run.attempt_no} 次尝试` : null, STATUS_LABEL[run.status] || run.status, run.profile_snapshot?.model || null, run.cost_usd ? `$${run.cost_usd.toFixed(3)}` : null, run.started_at && run.ended_at ? fmtDur(run.started_at, run.ended_at) : null].filter(Boolean);
  return <div className="row text-[10px] text-[var(--faint)] my-2"><div className="flex-1 border-t border-[var(--border)]" />{bits.join(" · ")}<div className="flex-1 border-t border-[var(--border)]" /></div>;
}
function RunBanner({ run, status }) {
  if (!run || !status) return null;
  const m = {
    queued: ["排队中：等待并发额度或依赖任务完成", "text-sky-700 bg-sky-50 border-sky-200"],
    needs_input: ["等待你裁决：这个运行被 Room 冲突挡住了，在右侧「摘要 → Room」里决定", "text-amber-700 bg-amber-50 border-amber-200"],
    failed: [`失败：${run.error || "未知错误"}`, "text-red-700 bg-red-50 border-red-200"],
    cancelled: ["已取消：运行被人为终止，未完成的工作不算完成", "text-gray-600 bg-gray-50 border-gray-200"],
    interrupted: [`已中断：${run.error || "服务重启时进程已不存在"}`, "text-red-700 bg-red-50 border-red-200"],
    exhausted: [`步数/预算耗尽：${run.error || ""}`, "text-orange-700 bg-orange-50 border-orange-200"],
  }[status];
  if (!m) return null;
  return <div className={`mx-4 mt-3 text-[12px] border rounded-md px-3 py-1.5 shrink-0 ${m[1]}`}>{m[0]}</div>;
}
