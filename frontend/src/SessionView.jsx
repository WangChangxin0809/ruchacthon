import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { Badge, Button, Details, fmtTime, STATUS_LABEL } from "./ui";

const TERMINAL = ["succeeded", "failed", "cancelled", "interrupted", "exhausted"];
const TOOL_ICON = { Read: "📖", Edit: "✏️", MultiEdit: "✏️", Write: "📝", Bash: "$", Grep: "🔍", Glob: "🔍", WebFetch: "🌐", WebSearch: "🌐", TodoWrite: "☑" };

function toolSummary(name, input) {
  if (!input || typeof input !== "object") return "";
  if (name === "Bash") return input.description || input.command || "";
  if (input.file_path) return input.file_path;
  if (input.pattern) return input.pattern;
  if (input.title) return input.title;
  return Object.entries(input).map(([k, v]) => `${k}=${JSON.stringify(v).slice(0, 50)}`).join(" ");
}

function ToolUse({ block, result }) {
  const name = block.name.replace(/^mcp__(\w+)__/, "$1: ");
  const failed = result?.is_error;
  const icon = TOOL_ICON[block.name] || (block.name.startsWith("mcp__") ? "🔌" : "⚙");
  return (
    <div className={`text-xs px-2 py-1 rounded mb-1 border ${failed ? "bg-rose-950/60 border-rose-900 text-rose-200" : "bg-gray-900/70 border-gray-800 text-gray-400"}`}>
      <span className="text-gray-300 font-mono">{icon} {name}</span> <span className="text-gray-500 font-mono">{toolSummary(block.name, block.input).slice(0, 140)}</span>
      {result === undefined && <span className="text-gray-600 animate-pulse"> …</span>}
      {result && (
        <Details summary={failed ? "错误详情" : "结果"} className="mt-0.5">
          <pre className="whitespace-pre-wrap text-[11px] max-h-48 overflow-auto text-gray-400">{renderResult(result.content)}</pre>
        </Details>
      )}
    </div>
  );
}

function renderResult(c) {
  if (typeof c === "string") return c.slice(0, 4000);
  if (Array.isArray(c)) return c.map((x) => (x.type === "text" ? x.text : JSON.stringify(x))).join("\n").slice(0, 4000);
  return JSON.stringify(c, null, 1)?.slice(0, 4000);
}

function toItems(messages, runs) {
  const results = new Map();
  for (const m of messages) for (const b of m.blocks || []) if (b.type === "tool_result") results.set(b.tool_use_id, b);
  const items = [];
  let lastRun = null;
  for (const m of messages) {
    if (m.run_id && m.run_id !== lastRun) {
      const r = runs.find((x) => x.id === m.run_id);
      items.push({ kind: "run", id: `run-${m.run_id}`, run: r, n: r ? runs.indexOf(r) + 1 : null });
      lastRun = m.run_id;
    }
    if (m.role === "user" && m.author !== "tool") {
      const text = (m.blocks || []).filter((b) => b.type === "text").map((b) => b.text).join("\n");
      if (text) items.push({ kind: "user", author: m.author, text, at: m.created_at, id: m.id });
      continue;
    }
    if (m.role !== "assistant") continue;
    for (const b of m.blocks || []) {
      if (b.type === "text" && b.text) items.push({ kind: "assistant", text: b.text, at: m.created_at, id: m.id + b.text.length, error: m.error });
      if (b.type === "tool_use") items.push({ kind: "tool", block: b, result: results.get(b.id), id: b.id });
    }
  }
  return items;
}

export default function SessionView({ session, task, messages, streams, runStatus, profileData, author, onCancel, onRetry, onOpenSettings }) {
  const [input, setInput] = useState("");
  const [choice, setChoice] = useState(() => { try { return localStorage.getItem("wb.model") || ""; } catch { return ""; } });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [stuck, setStuck] = useState(false);
  const bottomRef = useRef(null);
  const listRef = useRef(null);
  const runs = session?.runs || [];
  const items = useMemo(() => toItems(messages, runs), [messages, runs]);
  const latest = task?.latest_run || runs[runs.length - 1];
  const live = latest && (runStatus[latest.id]?.status || latest.status);
  const streaming = latest ? streams[latest.id] : "";
  const active = live && !TERMINAL.includes(live);

  // dsh: the picker lists provider ▸ model; picking one also becomes the default for the next message.
  const options = useMemo(() => {
    const out = [{ value: "", label: `默认（${defaultLabel(profileData)}）` }];
    for (const p of profileData.profiles || []) for (const m of p.models?.length ? p.models : [null]) out.push({ value: `${p.id}|${m || ""}`, label: `${p.display_name || p.name} ▸ ${m || "默认模型"}` });
    return out;
  }, [profileData]);
  const [profileId, model] = choice.split("|");

  useEffect(() => { if (!stuck) bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages.length, streaming, stuck]);
  function onScroll() { const el = listRef.current; if (el) setStuck(el.scrollHeight - el.scrollTop - el.clientHeight > 120); }

  async function send() {
    if (!input.trim() || !session) return;
    if (!author) { setError("先在右上角填你的名字：多人共用一个会话时，agent 需要知道是谁在说话。"); return; }
    setBusy(true); setError("");
    try {
      await api.send(session.id, input, author, profileId || undefined, model || undefined);
      setInput("");
    } catch (e) { setError(String(e.message || e)); } finally { setBusy(false); }
  }

  if (!session) return <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">选择左侧「主 agent」或一个任务</div>;
  const cost = runs.reduce((s, r) => s + (r.cost_usd || 0), 0);

  return (
    <div className="flex flex-col h-full min-h-0">
      <header className="px-4 py-2 border-b border-gray-800 flex items-center gap-3 shrink-0">
        <div className="min-w-0 flex-1">
          <div className="text-sm text-gray-100 truncate">{task ? task.title : session.title}</div>
          <div className="text-[11px] text-gray-500 truncate">
            {session.kind === "main" ? "主 agent · 在项目目录里直接工作" : `worker${task?.branch ? ` · 分支 ${task.branch}` : task?.isolation === "main" ? " · 项目目录" : ""}`}
            {" · "}{runs.length} 次运行{cost ? ` · $${cost.toFixed(3)}` : ""}
          </div>
        </div>
        {live && <Badge status={live === "succeeded" ? "in_review" : live}>{live === "succeeded" ? "本次运行结束" : STATUS_LABEL[live] || live}</Badge>}
        {active && <Button kind="danger" onClick={() => onCancel(latest.id)}>取消运行</Button>}
        {task && !active && live && live !== "succeeded" && <Button onClick={() => onRetry(task.id)}>重试（新一次运行）</Button>}
      </header>

      <RunBanner run={latest} status={live} />

      <div ref={listRef} onScroll={onScroll} className="flex-1 overflow-y-auto p-4 space-y-2 min-h-0 relative">
        {items.length === 0 && !streaming && (
          <div className="text-sm text-gray-600 text-center mt-10 max-w-md mx-auto">
            {session.kind === "main"
              ? "跟主 agent 说点什么。它会直接改代码，或者把独立的活派给 worker（各自在自己的 worktree 里后台跑），你在右侧看板和预览里跟进。"
              : "这个 worker 还没有输出。"}
          </div>
        )}
        {items.map((it) => {
          if (it.kind === "run") return <RunDivider key={it.id} run={it.run} n={it.n} />;
          if (it.kind === "tool") return <ToolUse key={it.id} block={it.block} result={it.result} />;
          const mine = it.kind === "user";
          return (
            <div key={it.id} className={`flex ${mine ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${mine
                ? (it.author === author || !it.author ? "bg-indigo-900/70 text-indigo-100" : "bg-amber-950/70 text-amber-100 border border-amber-900")
                : it.error ? "bg-rose-950 text-rose-200 border border-rose-900" : "bg-gray-800 text-gray-100"}`}>
                {mine && it.author && it.author !== author && <div className="text-[10px] text-amber-400 mb-1">{it.author}</div>}
                {it.text}
                <div className="text-[10px] text-gray-500 mt-1 text-right">{fmtTime(it.at)}</div>
              </div>
            </div>
          );
        })}
        {streaming && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap bg-gray-800 text-gray-100 border border-emerald-900">
              {streaming}<span className="animate-pulse">▍</span>
            </div>
          </div>
        )}
        {active && !streaming && <div className="text-xs text-gray-500 animate-pulse">Claude Code 正在工作…</div>}
        {error && <div className="text-xs text-rose-300 bg-rose-950 border border-rose-800 rounded px-3 py-2">{error}</div>}
        <div ref={bottomRef} />
      </div>
      {stuck && <button onClick={() => { setStuck(false); bottomRef.current?.scrollIntoView(); }} className="self-center -mt-8 mb-1 z-10 text-[11px] bg-gray-800 border border-gray-700 rounded-full px-3 py-0.5 text-gray-300">↓ 回到底部</button>}

      <div className="p-3 border-t border-gray-800 shrink-0">
        <div className="flex gap-2">
          <textarea rows={2} className="flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 resize-none"
            placeholder={session.kind === "main" ? "给主 agent 发消息…（Enter 发送，Shift+Enter 换行）" : "给这个 worker 发消息…（运行中会作为追加消息进入当前轮）"}
            value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} disabled={busy} />
          <Button kind="primary" onClick={send} disabled={busy || !input.trim()}>发送</Button>
        </div>
        <div className="flex gap-2 items-center mt-1.5 text-[11px] text-gray-500">
          <select className="bg-gray-900 border border-gray-800 rounded px-1.5 py-0.5 text-gray-300 max-w-[260px]" value={choice}
            onChange={(e) => { setChoice(e.target.value); try { localStorage.setItem("wb.model", e.target.value); } catch { /* ignore */ } }} title="模型（在设置 → 模型里添加提供方）">
            {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <button onClick={onOpenSettings} className="hover:text-gray-300">配置模型…</button>
          <div className="flex-1" />
          <span>以 <b className="text-gray-300">{author || "（未填名字）"}</b> 发送</span>
          {active && <span className="text-emerald-400">运行中</span>}
        </div>
      </div>
    </div>
  );
}

function defaultLabel(pd) {
  const p = (pd.profiles || []).find((x) => x.id === pd.default_profile_id);
  if (p) return `${p.display_name || p.name} ▸ ${pd.default_model || p.model || "默认模型"}`;
  return pd.default_model ? `Claude Code 登录 ▸ ${pd.default_model}` : "Claude Code 登录";
}

function RunDivider({ run, n }) {
  if (!run) return null;
  const snap = run.profile_snapshot || {};
  const bits = [`运行 #${n}`, run.attempt_no > 1 ? `第 ${run.attempt_no} 次尝试` : null, STATUS_LABEL[run.status] || run.status,
    snap.model || null, run.cost_usd ? `$${run.cost_usd.toFixed(3)}` : null, run.num_turns ? `${run.num_turns} 轮` : null, fmtTime(run.started_at || run.created_at)].filter(Boolean);
  return <div className="flex items-center gap-2 text-[10px] text-gray-600 my-2"><div className="flex-1 border-t border-gray-800" />{bits.join(" · ")}<div className="flex-1 border-t border-gray-800" /></div>;
}

function RunBanner({ run, status }) {
  if (!run || !status) return null;
  const msgs = {
    queued: ["排队中：等待并发额度或依赖任务完成", "border-sky-900 text-sky-300"],
    needs_input: ["等待人工决定：这个运行被 Room 冲突阻塞，去底部 Room 面板裁决", "border-amber-800 text-amber-300"],
    failed: [`失败：${run.error || "未知错误"}`, "border-rose-900 text-rose-300"],
    cancelled: ["已取消：运行被人为终止，未完成的工作不算完成", "border-gray-700 text-gray-400"],
    interrupted: [`已中断：${run.error || "服务重启时进程已不存在"}`, "border-rose-900 text-rose-300"],
    exhausted: [`步数/预算耗尽：${run.error || ""}`, "border-orange-900 text-orange-300"],
  }[status];
  if (!msgs) return null;
  return <div className={`mx-4 mt-2 text-xs border rounded px-3 py-1.5 shrink-0 ${msgs[1]}`}>{msgs[0]}</div>;
}
