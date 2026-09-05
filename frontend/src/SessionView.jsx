import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Badge, Button, Details, fmtTime, STATUS_LABEL } from "./ui";

const TERMINAL = ["succeeded", "failed", "cancelled", "interrupted", "exhausted"];

function ToolUse({ block, result }) {
  const name = block.name.replace(/^mcp__(\w+)__/, "$1: ");
  const failed = result?.is_error;
  const summary = typeof block.input === "object" ? Object.entries(block.input).map(([k, v]) => `${k}=${JSON.stringify(v).slice(0, 60)}`).join(" ") : "";
  return (
    <div className={`text-xs font-mono px-2 py-1.5 rounded mb-1 border ${failed ? "bg-rose-950/60 border-rose-900 text-rose-200" : "bg-gray-900 border-gray-800 text-gray-400"}`}>
      <span className="text-gray-200">⚙ {name}</span> <span className="text-gray-500">{summary.slice(0, 160)}</span>
      {result === undefined && <span className="text-gray-600"> …</span>}
      {result && (
        <Details summary="结果" className="mt-1">
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

function toItems(messages) {
  const results = new Map();
  for (const m of messages) for (const b of m.blocks || []) if (b.type === "tool_result") results.set(b.tool_use_id, b);
  const items = [];
  for (const m of messages) {
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

export default function SessionView({ session, task, messages, streams, runStatus, profiles, onCancel, onRetry }) {
  const [input, setInput] = useState("");
  const [author, setAuthor] = useState(() => { try { return localStorage.getItem("wb.author") || "human"; } catch { return "human"; } });
  const [profileId, setProfileId] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef(null);
  const items = toItems(messages);
  const runs = session?.runs || [];
  const latest = task?.latest_run || runs[runs.length - 1];
  const live = latest && (runStatus[latest.id]?.status || latest.status);
  const streaming = latest ? streams[latest.id] : "";
  const active = live && !TERMINAL.includes(live);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages.length, streaming]);

  async function send() {
    if (!input.trim() || !session) return;
    setBusy(true); setError("");
    try {
      await api.send(session.id, input, author, profileId || undefined);
      setInput("");
    } catch (e) { setError(String(e.message || e)); } finally { setBusy(false); }
  }

  if (!session) return <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">选择左侧项目和会话</div>;

  return (
    <div className="flex flex-col h-full min-h-0">
      <header className="px-4 py-2 border-b border-gray-800 flex items-center gap-3 shrink-0">
        <div className="min-w-0 flex-1">
          <div className="text-sm text-gray-100 truncate">{task ? task.title : session.title}</div>
          <div className="text-[11px] text-gray-500 truncate">
            {session.kind === "main" ? "主 agent 会话" : "worker 会话"} · {runs.length} 次运行
            {task?.workspace?.branch && <> · 分支 {task.workspace.branch}</>}
          </div>
        </div>
        {live && <Badge status={live === "succeeded" ? "in_review" : live}>{live === "succeeded" ? "本次运行结束" : STATUS_LABEL[live] || live}</Badge>}
        {active && <Button kind="danger" onClick={() => onCancel(latest.id)}>取消运行</Button>}
        {task && !active && live && live !== "succeeded" && <Button onClick={() => onRetry(task.id)}>重试（新一次运行）</Button>}
      </header>

      <RunBanner run={latest} status={live} />

      <div className="flex-1 overflow-y-auto p-4 space-y-2 min-h-0">
        {items.length === 0 && !streaming && (
          <div className="text-sm text-gray-600 text-center mt-10">
            {session.kind === "main" ? "跟主 agent 说点什么：它可以直接干活，也可以派 worker 到独立 worktree 里后台干。" : "这个 worker 还没有输出。"}
          </div>
        )}
        {items.map((it) =>
          it.kind === "tool" ? <ToolUse key={it.id} block={it.block} result={it.result} /> : (
            <div key={it.id} className={`flex ${it.kind === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${it.kind === "user"
                ? (it.author === "human" || !it.author ? "bg-indigo-900/70 text-indigo-100" : "bg-amber-950/70 text-amber-100 border border-amber-900")
                : it.error ? "bg-rose-950 text-rose-200 border border-rose-900" : "bg-gray-800 text-gray-100"}`}>
                {it.kind === "user" && it.author && it.author !== "human" && <div className="text-[10px] text-amber-400 mb-1">来自 {it.author}</div>}
                {it.text}
                <div className="text-[10px] text-gray-500 mt-1 text-right">{fmtTime(it.at)}</div>
              </div>
            </div>
          ),
        )}
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

      <div className="p-3 border-t border-gray-800 shrink-0">
        <div className="flex gap-2 items-center mb-2 text-[11px] text-gray-500">
          <span>发言者</span>
          <input className="bg-gray-900 border border-gray-800 rounded px-1.5 py-0.5 w-28 text-gray-300" value={author}
            onChange={(e) => { setAuthor(e.target.value); try { localStorage.setItem("wb.author", e.target.value); } catch { /* ignore */ } }} />
          {session.kind === "main" && profiles?.length > 0 && (
            <>
              <span>Provider</span>
              <select className="bg-gray-900 border border-gray-800 rounded px-1.5 py-0.5 text-gray-300" value={profileId} onChange={(e) => setProfileId(e.target.value)}>
                <option value="">默认（Claude Code 登录）</option>
                {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </>
          )}
          {active && <span className="text-emerald-400">运行中 — 发送会作为追加消息进入当前会话</span>}
        </div>
        <div className="flex gap-2">
          <textarea rows={2} className="flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 resize-none"
            placeholder={session.kind === "main" ? "给主 agent 发消息…（Enter 发送，Shift+Enter 换行）" : "给这个 worker 发消息…"}
            value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} disabled={busy} />
          <Button kind="primary" onClick={send} disabled={busy || !input.trim()}>发送</Button>
        </div>
      </div>
    </div>
  );
}

function RunBanner({ run, status }) {
  if (!run || !status) return null;
  const msgs = {
    queued: ["排队中：等待并发额度或依赖任务完成", "border-sky-900 text-sky-300"],
    needs_input: ["等待人工决定：这个运行被 Room 冲突阻塞，去 Room 面板裁决", "border-amber-800 text-amber-300"],
    failed: [`失败：${run.error || "未知错误"}`, "border-rose-900 text-rose-300"],
    cancelled: ["已取消：运行被人为终止，未完成的工作不算完成", "border-gray-700 text-gray-400"],
    interrupted: [`已中断：${run.error || "服务重启时进程已不存在"}`, "border-rose-900 text-rose-300"],
    exhausted: [`步数/预算耗尽：${run.error || ""}`, "border-orange-900 text-orange-300"],
  }[status];
  if (!msgs) return null;
  return <div className={`mx-4 mt-2 text-xs border rounded px-3 py-1.5 shrink-0 ${msgs[1]}`}>{msgs[0]}</div>;
}
