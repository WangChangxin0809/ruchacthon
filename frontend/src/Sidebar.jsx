import { useState } from "react";
import { api } from "./api";
import { Badge, Button, Empty } from "./ui";

export default function Sidebar({ projects, projectId, setProjectId, tasks, sessions, sessionId, setSessionId, refetch, onOpenSettings }) {
  const [newPath, setNewPath] = useState("");
  const [err, setErr] = useState("");
  const [showNew, setShowNew] = useState(false);
  const main = sessions.find((s) => s.kind === "main");

  async function addProject() {
    setErr("");
    try {
      const p = await api.createProject(newPath);
      setNewPath("");
      refetch("projects");
      setProjectId(p.id);
    } catch (e) { setErr(e.message); }
  }

  return (
    <div className="h-full flex flex-col bg-[#0c0e12] border-r border-gray-800">
      <div className="p-3 border-b border-gray-800">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-medium text-gray-400">项目</span>
          <Button kind="ghost" onClick={onOpenSettings} title="Provider / Claude Code 配置">⚙</Button>
        </div>
        <select className="w-full bg-gray-900 border border-gray-800 rounded px-2 py-1 text-sm text-gray-200" value={projectId || ""}
          onChange={(e) => { setSessionId(null); setProjectId(e.target.value || null); }}>
          <option value="">— 选择项目 —</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}{p.is_git ? "" : "（非 git）"}</option>)}
        </select>
        <div className="flex gap-1 mt-2">
          <input className="flex-1 bg-gray-900 border border-gray-800 rounded px-2 py-1 text-xs text-gray-200" placeholder="添加：本机项目目录的绝对路径"
            value={newPath} onChange={(e) => setNewPath(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addProject()} />
          <Button onClick={addProject} disabled={!newPath.trim()}>＋</Button>
        </div>
        {err && <div className="text-[11px] text-rose-300 mt-1">{err}</div>}
      </div>

      {projectId && (
        <>
          <button onClick={() => main && setSessionId(main.id)}
            className={`text-left px-3 py-2 border-b border-gray-800 text-sm ${sessionId === main?.id ? "bg-gray-800 text-white" : "text-gray-300 hover:bg-gray-900"}`}>
            💬 主 agent 会话
          </button>
          <div className="px-3 py-2 flex items-center justify-between">
            <span className="text-xs font-medium text-gray-400">任务（{tasks.length}）</span>
            <Button kind="ghost" onClick={() => setShowNew(!showNew)}>＋ 新任务</Button>
          </div>
          {showNew && <NewTask projectId={projectId} onDone={() => { setShowNew(false); refetch("tasks"); }} tasks={tasks} />}
          <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-1">
            {tasks.length === 0 && <Empty>还没有任务。在主会话里让 agent 派 worker，或点「新任务」。</Empty>}
            {[...tasks].reverse().map((t) => (
              <button key={t.id} onClick={() => t.session_id && setSessionId(t.session_id)}
                className={`w-full text-left rounded px-2 py-1.5 text-sm ${sessionId && sessionId === t.session_id ? "bg-gray-800 text-white" : "text-gray-300 hover:bg-gray-900"}`}>
                <div className="flex items-center gap-2">
                  <span className="truncate flex-1">{t.title}</span>
                  <Badge status={t.status} />
                </div>
              </button>
            ))}
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
