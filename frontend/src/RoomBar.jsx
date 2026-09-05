import { useState } from "react";
import { api } from "./api";
import { Button, fmtTime } from "./ui";

export default function RoomBar({ room, tasks, author }) {
  const [open, setOpen] = useState(false);
  const title = (runId) => {
    const t = tasks.find((x) => x.latest_run?.id === runId || x.session_id === runId);
    return t ? t.title : (room.members.find((m) => m.run_id === runId)?.task_title || runId);
  };
  const pending = room.pending_decisions || [];
  return (
    <div className="border-t border-gray-800 bg-[#0c0e12] shrink-0">
      <button onClick={() => setOpen(!open)} className="w-full px-4 py-1.5 flex items-center gap-4 text-xs text-gray-400 hover:bg-gray-900">
        <span>{open ? "▾" : "▸"} Room</span>
        <span>成员 <b className="text-gray-200">{room.members?.length || 0}</b></span>
        <span>认领 <b className="text-gray-200">{room.claims?.length || 0}</b></span>
        <span>重叠 <b className={room.overlaps?.length ? "text-amber-300" : "text-gray-200"}>{room.overlaps?.length || 0}</b></span>
        <span>待裁决 <b className={pending.length ? "text-rose-300" : "text-gray-200"}>{pending.length}</b></span>
      </button>
      {open && (
        <div className="px-4 pb-3 grid grid-cols-3 gap-4 max-h-64 overflow-auto text-xs">
          <div>
            <div className="text-gray-500 mb-1">当前认领（租约到期自动释放）</div>
            {room.claims.length === 0 && <div className="text-gray-700">无</div>}
            {room.claims.map((c) => (
              <div key={c.id} className="text-gray-300 truncate"><span className="text-gray-500">{title(c.run_id)}</span> → <code>{c.path}</code> {c.note && <span className="text-gray-500">({c.note})</span>}</div>
            ))}
            {room.overlaps.length > 0 && <div className="text-gray-500 mt-2 mb-1">路径重叠</div>}
            {room.overlaps.map((o, i) => (
              <div key={i} className={o.same_workspace ? "text-rose-300" : "text-amber-300"}>
                <code>{o.a.path}</code> ↔ <code>{o.b.path}</code> {o.same_workspace ? "同一工作区（冲突）" : "各自 worktree（合并时会冲突）"}
              </div>
            ))}
          </div>
          <div>
            <div className="text-gray-500 mb-1">待人工裁决</div>
            {pending.length === 0 && <div className="text-gray-700">无</div>}
            {pending.map((d) => <DecisionCard key={d.id} d={d} author={author} />)}
          </div>
          <div>
            <div className="text-gray-500 mb-1">广播 / 交接 / 通知</div>
            {room.messages.slice(0, 15).map((m) => (
              <div key={m.id} className="text-gray-300 truncate">
                <span className="text-gray-600">{fmtTime(m.created_at)}</span> <span className="text-gray-500">[{m.kind}]</span> {m.payload.text || m.payload.note || m.payload.path || ""}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DecisionCard({ d, author }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const s = d.subject;
  async function act(decision) {
    setBusy(true);
    try { await api.decide(d.id, decision, reason, author); } finally { setBusy(false); }
  }
  return (
    <div className="bg-gray-900 border border-rose-900 rounded p-2 mb-2">
      <div className="text-gray-200"><code>{s.path}</code></div>
      <div className="text-gray-400">「{s.requester?.task_title}」想要，「{s.holder?.task_title}」持有</div>
      <input className="w-full bg-gray-950 border border-gray-800 rounded px-1.5 py-0.5 my-1 text-gray-200" placeholder="理由（会送到两个 worker）" value={reason} onChange={(e) => setReason(e.target.value)} />
      <div className="flex gap-1">
        <Button kind="primary" disabled={busy} onClick={() => act("approve")}>转给请求方</Button>
        <Button disabled={busy} onClick={() => act("reject")}>维持持有方</Button>
      </div>
    </div>
  );
}
