import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { DiffText } from "./Preview";
import Preview from "./Preview";
import { Details, Dot, I, STATUS_LABEL, fmtDur, fmtRel, fmtTime, toneOf } from "./ui";

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
      if (text) push({ kind: "user", id: m.id, author: m.author, text, at: m.created_at });
      continue;
    }
    if (m.role !== "assistant") continue;
    for (const b of m.blocks || []) {
      if (b.type === "text" && b.text) push({ kind: "text", id: m.id + ":" + items.length, text: b.text, at: m.created_at, error: m.error });
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
function Text({ text }) {
  // light markdown: fenced code, inline code, paragraphs
  const parts = text.split(/(```[\s\S]*?```)/g);
  return (
    <div className="text-[13.5px] leading-relaxed whitespace-pre-wrap">
      {parts.map((p, i) => p.startsWith("```") ? <pre key={i} className="bg-[var(--subtle)] rounded p-2 my-1 text-[12px] overflow-auto">{p.replace(/^```\w*\n?/, "").replace(/```$/, "")}</pre>
        : p.split(/(`[^`]+`)/g).map((s, j) => s.startsWith("`") ? <code key={`${i}-${j}`} className="bg-[var(--subtle)] rounded px-1 text-[12px] text-[var(--working)]">{s.slice(1, -1)}</code> : <span key={`${i}-${j}`}>{s}</span>))}
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
export default function SessionView({ session, task, messages, streams, runStatus, profileData, author, room, artifacts, onCancel, onRetry, onOpenSettings, onNewTask, onOpenMain, refetch, onBack }) {
  const [input, setInput] = useState("");
  const [choice, setChoice] = useState(() => { try { return localStorage.getItem("wb.model") || ""; } catch { return ""; } });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [stuck, setStuck] = useState(false);
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

  useEffect(() => { if (!stuck) bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages.length, streaming, stuck]);
  useEffect(() => { if (task?.id) api.taskDiff(task.id).then(setDiff).catch(() => setDiff(null)); else setDiff(null); }, [task?.id, task?.latest_run?.status, artifacts?.length]);
  useEffect(() => { try { localStorage.setItem("wb.inspector", inspector); } catch { /* ignore */ } }, [inspector]);
  const onScroll = () => { const el = listRef.current; if (el) setStuck(el.scrollHeight - el.scrollTop - el.clientHeight > 120); };

  async function send() {
    if (!input.trim() || !session) return;
    if (!author) { setError("先在左下角填你的名字：多人共用一个会话时，agent 需要知道是谁在说话。"); return; }
    setBusy(true); setError("");
    try { await api.send(session.id, input, author, profileId || undefined, model || undefined); setInput(""); } catch (e) { setError(String(e.message || e)); } finally { setBusy(false); }
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
          {active && <button onClick={() => onCancel(latest.id)} className="btn btn-ghost btn-sm text-red-600" title="取消运行"><I.trash className="w-4 h-4" /></button>}
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
              if (it.kind === "user") return (
                <div key={it.id} className="flex justify-end pt-2">
                  <div className="max-w-[85%] bg-[var(--subtle)] rounded-xl px-4 py-3 text-[13.5px] whitespace-pre-wrap">
                    {it.author && it.author !== author && <div className="text-[11px] text-[var(--muted)] mb-1">{it.author}</div>}{it.text}
                    <div className="text-[10px] text-[var(--faint)] text-right mt-1">{fmtTime(it.at)}</div>
                  </div>
                </div>
              );
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
            {error && <div className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</div>}
            <div ref={bottomRef} />
          </div>
        </div>
        {stuck && <button onClick={() => { setStuck(false); bottomRef.current?.scrollIntoView(); }} className="self-center -mt-8 mb-1 z-10 btn btn-sm rounded-full shadow">↓ 回到底部</button>}

        {/* AO's composer: one rounded box, model + permission on the bottom row */}
        <div className="px-4 pb-4 pt-2 shrink-0">
          <div className="max-w-[760px] mx-auto card rounded-2xl shadow-sm focus-within:border-gray-400">
            <textarea rows={2} className="w-full resize-none px-4 pt-3 pb-1 text-[14px] focus:outline-none placeholder:text-[var(--faint)] bg-transparent"
              placeholder={session.kind === "main" ? "给主 agent 发消息…" : active ? "给这个 worker 发消息（会追加进当前运行）…" : "给这个 worker 发消息…"} value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} disabled={busy} />
            <div className="row px-3 pb-2.5 gap-2">
              <button className="btn btn-ghost btn-sm" onClick={onOpenSettings} title="配置模型"><I.plus className="w-4 h-4" /></button>
              <select className="text-[12px] bg-transparent hover:bg-[var(--subtle)] rounded px-1.5 py-1 max-w-[240px]" value={choice} onChange={(e) => { setChoice(e.target.value); try { localStorage.setItem("wb.model", e.target.value); } catch { /* ignore */ } }} title="模型">
                {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
              <div className="flex-1" />
              <span className="text-[12px] text-[var(--muted)]" title="worker 在自己的分支里跑，权限已放开；合并前有人审阅">Bypass Permissions</span>
              <button onClick={send} disabled={busy || !input.trim()} className="w-7 h-7 rounded-full bg-[var(--accent)] text-white flex items-center justify-center disabled:opacity-30"><I.send className="w-4 h-4" /></button>
            </div>
          </div>
          <div className="text-[10px] text-[var(--faint)] text-center mt-1">以 {author || "（未填名字）"} 发送 · Enter 发送，Shift+Enter 换行</div>
        </div>
      </div>

      {inspector && (
        <aside className="w-[340px] shrink-0 border-l border-[var(--border)] bg-white overflow-y-auto">
          {inspector === "summary" && <Summary session={session} task={task} runs={runs} room={room} author={author} refetch={refetch} live={live} artifacts={artifacts} />}
          {inspector === "preview" && <div className="p-3"><div className="label mb-2">成果预览</div><Preview artifacts={artifacts} tasks={task ? [task] : []} selectedTaskId={task?.id || null} onSelectTask={() => {}} author={author || "human"} /></div>}
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

function Summary({ session, task, runs, room, author, refetch, live, artifacts }) {
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
              <button className="btn btn-sm" onClick={() => run(() => api.reviewTask(task.id, "approve", note, author || "human"))}>审阅通过</button>
              <button className="btn btn-sm" onClick={() => run(() => api.reviewTask(task.id, "request_changes", note, author || "human"))}>要求修改</button>
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
        {pending.map((d) => <DecisionCard key={d.id} d={d} author={author || "human"} />)}
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

export function DecisionCard({ d, author }) {
  const [reason, setReason] = useState(""); const [busy, setBusy] = useState(false);
  const s = d.subject;
  async function act(decision) { setBusy(true); try { await api.decide(d.id, decision, reason, author); } finally { setBusy(false); } }
  return (
    <div className="card border-red-200 p-2 mt-2">
      <div className="mono">{s.path}</div>
      <div className="text-[var(--muted)]">「{s.requester?.task_title}」想要，「{s.holder?.task_title}」持有</div>
      <input className="input my-1" placeholder="理由（会送到两个 worker）" value={reason} onChange={(e) => setReason(e.target.value)} />
      <div className="row gap-1"><button className="btn btn-primary btn-sm" disabled={busy} onClick={() => act("approve")}>转给请求方</button><button className="btn btn-sm" disabled={busy} onClick={() => act("reject")}>维持持有方</button></div>
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
