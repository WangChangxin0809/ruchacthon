import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Button, Empty, fmtTime } from "./ui";

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
    if (!author) { setErr("先在右上角填你的名字"); return; }
    setErr("");
    try { await api.chatPost(current.id, input, author); setInput(""); } catch (e) { setErr(e.message); }
  }
  async function createChannel() {
    if (!newName.trim()) return;
    try { const ch = await api.createChannel(newName, author || "human"); setNewName(""); refetch("channels"); setChannelId(ch.id); } catch (e) { setErr(e.message); }
  }

  return (
    <div className="flex h-full min-h-0">
      <div className="w-60 border-r border-gray-800 flex flex-col bg-[#0c0e12]">
        <div className="px-3 py-2 text-xs text-gray-400 border-b border-gray-800">群 · {channels.length}</div>
        <div className="flex-1 overflow-auto">
          {channels.map((c) => (
            <button key={c.id} onClick={() => setChannelId(c.id)} className={`w-full text-left px-3 py-2 text-sm ${current?.id === c.id ? "bg-gray-800 text-white" : "text-gray-300 hover:bg-gray-900"}`}>
              <div className="flex items-center justify-between"><span># {c.name}</span>{c.last && <span className="text-[10px] text-gray-600">{fmtTime(c.last.created_at)}</span>}</div>
              {c.last && <div className="text-[10px] text-gray-500 truncate">{c.last.author}: {c.last.text}</div>}
            </button>
          ))}
        </div>
        <div className="p-2 border-t border-gray-800 flex gap-1">
          <input className="flex-1 min-w-0 bg-gray-900 border border-gray-800 rounded px-2 py-1 text-xs text-gray-200" placeholder="拉个群…" value={newName} onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && createChannel()} />
          <Button onClick={createChannel} disabled={!newName.trim()}>＋</Button>
        </div>
      </div>
      <div className="flex-1 flex flex-col min-w-0">
        <div className="px-4 py-2 border-b border-gray-800 flex items-center gap-3">
          <span className="text-sm text-gray-200"># {current?.name || "—"}</span>
          <span className="text-[11px] text-gray-500">人和人聊。贴任务链接（?p=…&s=…）会变成可点的卡片；悬停消息可「派给 agent」</span>
        </div>
        <div className="flex-1 overflow-auto p-4 space-y-2">
          {messages.length === 0 && <Empty>还没有消息。</Empty>}
          {messages.map((m) => (
            <div key={m.id} className={`group flex ${m.author === author ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[75%] rounded-lg px-3 py-2 text-sm ${m.author === author ? "bg-indigo-900/70 text-indigo-100" : "bg-gray-800 text-gray-100"}`}>
                <div className="text-[10px] text-gray-400 mb-0.5 flex items-center gap-2">
                  <span>{m.author} · {fmtTime(m.created_at)}</span>
                  <button onClick={() => setAssign(m)} className="opacity-0 group-hover:opacity-100 text-indigo-300 hover:text-indigo-100">派给 agent</button>
                </div>
                <Rich text={m.text} onOpenTask={onOpenTask} />
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
        {err && <div className="mx-4 text-xs text-rose-300">{err}</div>}
        <div className="p-3 border-t border-gray-800 flex gap-2">
          <textarea rows={2} className="flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 resize-none" placeholder={`在 #${current?.name || ""} 说点什么…（Enter 发送，Shift+Enter 换行）`} value={input}
            onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
          <Button kind="primary" onClick={send} disabled={!input.trim()}>发送</Button>
        </div>
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
        return <button key={i} onClick={() => onOpenTask({ id: null, project_id: m[1], session_id: m[2] })} className="inline-block align-middle text-[11px] bg-gray-900 border border-indigo-800 rounded px-1.5 py-0.5 text-indigo-200 hover:border-indigo-500">↗ 打开会话 {m[2].slice(-6)}</button>;
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
  const input = "w-full bg-gray-950 border border-gray-800 rounded px-2 py-1.5 text-sm text-gray-200";
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-[#111318] border border-gray-800 rounded-lg w-[480px] p-5 text-sm space-y-3" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-gray-100">把这条消息派给 agent</h2>
        <div className="text-xs text-gray-400 bg-gray-900 rounded p-2 max-h-32 overflow-auto whitespace-pre-wrap">{message.text}</div>
        <select className={input} value={projectId} onChange={(e) => setProjectId(e.target.value)}>
          {projects.length === 0 && <option value="">（先新增项目）</option>}
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <input className={input} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="任务名" />
        <div className="text-[11px] text-gray-500">会启动一个 worker（独立 worktree），并在群里回一条带链接的消息。</div>
        {err && <div className="text-xs text-rose-300">{err}</div>}
        <div className="flex justify-end gap-2"><Button onClick={onClose}>取消</Button><Button kind="primary" disabled={busy || !projectId || !title.trim()} onClick={go}>{busy ? "启动中…" : "启动 worker"}</Button></div>
      </div>
    </div>
  );
}
