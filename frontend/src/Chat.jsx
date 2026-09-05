import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Empty, I, fmtTime } from "./ui";

// People talking to people. Nothing here reaches a model unless someone
// deliberately hands it to the work area with 「派给 agent」.
export default function Chat({ channels, author, lastEvent, refetch, projects, onOpenTask }) {
  const [channelId, setChannelId] = useState(() => { try { return localStorage.getItem("wb.channel"); } catch { return null; } });
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [newName, setNewName] = useState("");
  const [err, setErr] = useState("");
  const [assign, setAssign] = useState(null); // message being turned into a task
  const bottomRef = useRef(null);
  const current = channels.find((c) => c.id === channelId) || channels[0];

  useEffect(() => { if (current && current.id !== channelId) setChannelId(current.id); }, [channels, current?.id]);
  useEffect(() => {
    if (!current) return;
    try { localStorage.setItem("wb.channel", current.id); } catch { /* ignore */ }
    api.chatMessages(current.id).then(setMessages).catch(() => setMessages([]));
  }, [current?.id]);
  useEffect(() => {
    if (lastEvent?.type === "chat_message" && lastEvent.payload.channel_id === current?.id) {
      setMessages((ms) => (ms.some((m) => m.id === lastEvent.payload.id) ? ms : [...ms, lastEvent.payload]));
    }
  }, [lastEvent, current?.id]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages.length]);

  async function send() {
    if (!input.trim() || !current) return;
    if (!author) { setErr("先在左下角填你的名字"); return; }
    setErr("");
    try { await api.chatPost(current.id, input, author); setInput(""); } catch (e) { setErr(e.message); }
  }
  async function createChannel() {
    if (!newName.trim()) return;
    try { const ch = await api.createChannel(newName, author || "human"); setNewName(""); refetch("channels"); setChannelId(ch.id); } catch (e) { setErr(e.message); }
  }

  return (
    <div className="flex h-full min-h-0">
      <div className="w-60 border-r border-[var(--border)] flex flex-col bg-[var(--bg)]">
        <div className="row h-12 px-4 border-b border-[var(--border)] font-semibold text-[14px]"><I.chat className="w-4 h-4" /> 聊天 <span className="text-[12px] text-[var(--muted)] font-normal">{channels.length} 个群</span></div>
        <div className="flex-1 overflow-auto">
          {channels.map((c) => (
            <button key={c.id} onClick={() => setChannelId(c.id)} className={`w-full text-left px-3 py-2 text-[13px] rounded-md mx-1 ${current?.id === c.id ? "bg-[var(--hover)]" : "hover:bg-[var(--subtle)]"}`} style={{ width: "calc(100% - 8px)" }}>
              <div className="flex items-center justify-between"><span># {c.name}</span>{c.last && <span className="text-[10px] text-[var(--faint)]">{fmtTime(c.last.created_at)}</span>}</div>
              {c.last && <div className="text-[11px] text-[var(--muted)] truncate">{c.last.author}: {c.last.text}</div>}
            </button>
          ))}
        </div>
        <div className="p-2 border-t border-[var(--border)] flex gap-1">
          <input className="input flex-1 min-w-0 py-1" placeholder="拉个群…" value={newName} onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && createChannel()} />
          <button className="btn btn-sm" onClick={createChannel} disabled={!newName.trim()}><I.plus className="w-3.5 h-3.5" /></button>
        </div>
      </div>
      <div className="flex-1 flex flex-col min-w-0 bg-white">
        <div className="row h-12 px-4 border-b border-[var(--border)]">
          <span className="text-[14px] font-semibold"># {current?.name || "—"}</span>
          <span className="text-[11px] text-[var(--muted)]">人和人聊。贴任务链接（?p=…&s=…）会变成可点的卡片；悬停消息可「派给 agent」</span>
        </div>
        <div className="flex-1 overflow-auto p-4 space-y-2"><div className="max-w-[760px] mx-auto space-y-2">
          {messages.length === 0 && <Empty>还没有消息。</Empty>}
          {messages.map((m) => (
            <div key={m.id} className={`group flex ${m.author === author ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[75%] rounded-xl px-4 py-2.5 text-[13.5px] ${m.author === author ? "bg-[var(--accent)] text-white" : "bg-[var(--subtle)]"}`}>
                <div className={`text-[10px] mb-0.5 flex items-center gap-2 ${m.author === author ? "text-white/60" : "text-[var(--muted)]"}`}>
                  <span>{m.author} · {fmtTime(m.created_at)}</span>
                  <button onClick={() => setAssign(m)} className="opacity-0 group-hover:opacity-100 underline">派给 agent</button>
                </div>
                <Rich text={m.text} onOpenTask={onOpenTask} />
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div></div>
        {err && <div className="mx-4 text-[12px] text-red-600">{err}</div>}
        <div className="px-4 pb-4 pt-2"><div className="max-w-[760px] mx-auto card rounded-2xl shadow-sm focus-within:border-gray-400 flex items-end gap-2 p-2">
          <textarea rows={2} className="flex-1 bg-transparent px-2 py-1.5 text-[14px] resize-none focus:outline-none placeholder:text-[var(--faint)]" placeholder={`在 #${current?.name || ""} 说点什么…（Enter 发送，Shift+Enter 换行）`} value={input}
            onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
          <button onClick={send} disabled={!input.trim()} className="w-7 h-7 mb-1 rounded-full bg-[var(--accent)] text-white flex items-center justify-center disabled:opacity-30"><I.send className="w-4 h-4" /></button>
        </div></div>
      </div>
      {assign && <AssignDialog message={assign} projects={projects} author={author} onClose={() => setAssign(null)} onDone={(t) => { setAssign(null); onOpenTask(t); }} channel={current} />}
    </div>
  );
}

// Deep links into the work area (?p=<project>&s=<session>) render as chips.
function Rich({ text, onOpenTask }) {
  const parts = text.split(/(\S*\?p=[\w-]+&s=[\w-]+\S*)/g);
  return (
    <div className="whitespace-pre-wrap">
      {parts.map((part, i) => {
        const m = part.match(/\?p=([\w-]+)&s=([\w-]+)/);
        if (!m) return <span key={i}>{part}</span>;
        return <button key={i} onClick={() => onOpenTask({ id: null, project_id: m[1], session_id: m[2] })} className="inline-block align-middle text-[11px] bg-white text-[var(--text)] border border-[var(--border)] rounded px-1.5 py-0.5 hover:border-gray-400">↗ 打开会话 {m[2].slice(-6)}</button>;
      })}
    </div>
  );
}

function AssignDialog({ message, projects, author, channel, onClose, onDone }) {
  const [projectId, setProjectId] = useState(projects[0]?.id || "");
  const [title, setTitle] = useState(message.text.split("\n")[0].slice(0, 60));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function go() {
    setBusy(true); setErr("");
    try {
      const instructions = `${message.text}\n\n（来自聊天 #${channel?.name}，${message.author} 说的；由 ${author || "human"} 派发）`;
      const r = await api.createTask(projectId, { title, instructions, isolation: "worktree", edit_mode: "exclusive", depends_on: [] });
      const t = r.task || r;
      await api.chatPost(channel.id, `已派给 agent：「${title}」→ ${location.origin}${location.pathname}?p=${projectId}&s=${t.session_id}`, author || "human");
      onDone(t);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white border border-[var(--border)] rounded-xl shadow-xl w-[480px] p-5 text-[13px] space-y-3" onClick={(e) => e.stopPropagation()}>
        <h2 className="font-semibold text-[14px]">把这条消息派给 agent</h2>
        <div className="text-[12px] text-[var(--muted)] bg-[var(--subtle)] rounded p-2 max-h-32 overflow-auto whitespace-pre-wrap">{message.text}</div>
        <select className="input" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
          {projects.length === 0 && <option value="">（先新增项目）</option>}
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="任务名" />
        <div className="text-[11px] text-[var(--muted)]">会启动一个 worker（独立 worktree），并在群里回一条带链接的消息。</div>
        {err && <div className="text-[12px] text-red-600">{err}</div>}
        <div className="row justify-end"><button className="btn btn-sm" onClick={onClose}>取消</button><button className="btn btn-primary btn-sm" disabled={busy || !projectId || !title.trim()} onClick={go}>{busy ? "启动中…" : "启动 worker"}</button></div>
      </div>
    </div>
  );
}
