import { useState } from "react";
import { api } from "./api";
import { Button, Empty, fmtTime, STATUS_LABEL } from "./ui";

// AO groups the board by "what you need to do"; the same grouping here so
// the sidebar and the board never disagree about what needs attention.
export const GROUPS = [
  { key: "attention", label: "需要你", statuses: ["needs_input", "failed", "interrupted", "exhausted", "changes_requested", "cancelled"], color: "bg-amber-400" },
  { key: "working", label: "进行中", statuses: ["running", "queued"], color: "bg-emerald-400" },
  { key: "review", label: "待审阅", statuses: ["in_review"], color: "bg-violet-400" },
  { key: "merge", label: "可合并", statuses: ["ready_to_merge"], color: "bg-teal-400" },
  { key: "done", label: "已合并 / 待开始", statuses: ["done", "todo"], color: "bg-gray-600" },
];
export const dotFor = (status) => GROUPS.find((g) => g.statuses.includes(status))?.color || "bg-gray-600";

export default function Sidebar({ projects, projectId, setProjectId, tasks, sessions, sessionId, setSessionId, refetch, onAddProject, selectedTaskId, onSelectTask }) {
  const [showNew, setShowNew] = useState(false);
  const [q, setQ] = useState("");
  const main = sessions.find((s) => s.kind === "main");
  const visible = q ? tasks.filter((t) => (t.title + " " + (t.branch || "")).toLowerCase().includes(q.toLowerCase())) : tasks;

  return (
    <div className="h-full flex flex-col bg-[#0c0e12] border-r border-gray-800">
      <div className="p-3 border-b border-gray-800 flex gap-1">
        <select className="flex-1 min-w-0 bg-gray-900 border border-gray-800 rounded px-2 py-1 text-sm text-gray-200" value={projectId || ""}
          onChange={(e) => { setSessionId(null); setProjectId(e.target.value || null); }}>
          <option value="">— 选择项目 —</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}{p.is_git ? "" : "（非 git）"}</option>)}
        </select>
        <Button onClick={onAddProject} title="新增项目">＋</Button>
      </div>

      {projectId && (
        <>
          <button onClick={() => main && setSessionId(main.id)}
            className={`text-left px-3 py-2 border-b border-gray-800 text-sm ${sessionId === main?.id ? "bg-gray-800 text-white" : "text-gray-300 hover:bg-gray-900"}`}>
            <div>🧭 主 agent</div>
            <div className="text-[10px] text-gray-500">规划、直接干活、派 worker</div>
          </button>
          <div className="px-3 py-2 flex items-center gap-2">
            <input className="flex-1 min-w-0 bg-gray-900 border border-gray-800 rounded px-2 py-0.5 text-xs text-gray-200" placeholder={`搜索 ${tasks.length} 个任务…`} value={q} onChange={(e) => setQ(e.target.value)} />
            <Button kind="ghost" onClick={() => setShowNew(!showNew)}>＋ 任务</Button>
          </div>
          {showNew && <NewTask projectId={projectId} onDone={() => { setShowNew(false); refetch("tasks"); }} tasks={tasks} />}
          <div className="flex-1 overflow-y-auto px-2 pb-2">
            {tasks.length === 0 && <Empty>还没有任务。在主 agent 会话里派 worker，或点「＋ 任务」。</Empty>}
            {GROUPS.map((g) => {
              const items = visible.filter((t) => g.statuses.includes(t.status));
              if (!items.length) return null;
              return (
                <div key={g.key} className="mb-2">
                  <div className="text-[10px] text-gray-500 px-2 py-1 uppercase tracking-wide">{g.label} · {items.length}</div>
                  {items.map((t) => (
                    <button key={t.id} onClick={() => onSelectTask(t)}
                      className={`w-full text-left rounded px-2 py-1.5 text-sm ${(sessionId && sessionId === t.session_id) || selectedTaskId === t.id ? "bg-gray-800 text-white" : "text-gray-300 hover:bg-gray-900"}`}>
                      <div className="flex items-center gap-2">
                        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotFor(t.status)} ${t.status === "running" ? "animate-pulse" : ""}`} />
                        <span className="truncate flex-1">{t.title}</span>
                      </div>
                      <div className="text-[10px] text-gray-500 pl-3.5 truncate">
                        {STATUS_LABEL[t.status] || t.status}{t.branch ? ` · ${t.branch}` : t.isolation === "main" ? " · 主目录" : ""}{t.latest_run?.ended_at ? ` · ${fmtTime(t.latest_run.ended_at)}` : ""}
                      </div>
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

function NewTask({ projectId, onDone, tasks }) {
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [isolation, setIsolation] = useState("worktree");
  const [editMode, setEditMode] = useState("exclusive");
  const [dep, setDep] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function create() {
    setBusy(true); setErr("");
    try {
      await api.createTask(projectId, { title, instructions, isolation, edit_mode: editMode, depends_on: dep ? [dep] : [] });
      onDone();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <div className="mx-2 mb-2 p-2 rounded border border-gray-800 bg-gray-900 space-y-1.5">
      <input className="w-full bg-gray-950 border border-gray-800 rounded px-2 py-1 text-xs text-gray-200" placeholder="任务名" value={title} onChange={(e) => setTitle(e.target.value)} />
      <textarea rows={3} className="w-full bg-gray-950 border border-gray-800 rounded px-2 py-1 text-xs text-gray-200" placeholder="给 worker 的指令" value={instructions} onChange={(e) => setInstructions(e.target.value)} />
      <div className="flex gap-1 text-[11px]">
        <select className="bg-gray-950 border border-gray-800 rounded px-1 py-0.5 text-gray-300" value={isolation} onChange={(e) => setIsolation(e.target.value)}>
          <option value="worktree">独立 worktree（默认）</option>
          <option value="main">直接在项目目录</option>
        </select>
        <select className="bg-gray-950 border border-gray-800 rounded px-1 py-0.5 text-gray-300" value={editMode} onChange={(e) => setEditMode(e.target.value)}>
          <option value="exclusive">独占编辑</option>
          <option value="shared">共享编辑（实验性）</option>
        </select>
      </div>
      <select className="w-full bg-gray-950 border border-gray-800 rounded px-1 py-0.5 text-[11px] text-gray-300" value={dep} onChange={(e) => setDep(e.target.value)}>
        <option value="">不依赖其他任务</option>
        {tasks.map((t) => <option key={t.id} value={t.id}>依赖：{t.title}</option>)}
      </select>
      {err && <div className="text-[11px] text-rose-300">{err}</div>}
      <Button kind="primary" onClick={create} disabled={busy || !title.trim() || !instructions.trim()} className="w-full">启动 worker</Button>
    </div>
  );
}
