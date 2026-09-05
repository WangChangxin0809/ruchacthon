import { useState } from "react";
import { api } from "./api";
import { I } from "./ui";

// AO's "Create a new task": one brief, agent + model on the bottom row, go.
export default function NewTaskDialog({ projectId, tasks, profileData, onClose, onCreated }) {
  const [brief, setBrief] = useState("");
  const [choice, setChoice] = useState("");
  const [isolation, setIsolation] = useState("worktree");
  const [dep, setDep] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const options = [{ value: "", label: "默认模型" }];
  for (const p of profileData?.profiles || []) for (const m of p.models?.length ? p.models : [null]) options.push({ value: `${p.id}|${m || ""}`, label: `${p.display_name || p.name} ▸ ${m || "默认"}` });
  const [profileId, model] = choice.split("|");
  const title = brief.trim().split("\n")[0].slice(0, 60);

  async function create() {
    setBusy(true); setErr("");
    try {
      const r = await api.createTask(projectId, { title, instructions: brief, isolation, edit_mode: "exclusive", depends_on: dep ? [dep] : [], profile_id: profileId || null, model: model || null });
      onCreated(r.task || r);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={onClose}>
      <div className="card w-[560px] p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="text-[15px] font-semibold mb-3">新建任务</div>
        <textarea autoFocus rows={4} className="w-full resize-none text-[14px] focus:outline-none placeholder:text-[var(--faint)]" placeholder="描述要做的改动、约束和验收方式。第一行会成为任务名。" value={brief} onChange={(e) => setBrief(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) create(); }} />
        <div className="row mt-3 gap-2">
          <span className="btn btn-sm pointer-events-none"><I.bot className="w-3.5 h-3.5 text-orange-500" /> Claude Code</span>
          <select className="input py-1 text-[12px] w-auto max-w-[200px]" value={choice} onChange={(e) => setChoice(e.target.value)}>{options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
          <select className="input py-1 text-[12px] w-auto" value={isolation} onChange={(e) => setIsolation(e.target.value)}><option value="worktree">独立 worktree</option><option value="main">直接在项目目录</option></select>
          {tasks.length > 0 && <select className="input py-1 text-[12px] w-auto max-w-[160px]" value={dep} onChange={(e) => setDep(e.target.value)}><option value="">不依赖</option>{tasks.map((t) => <option key={t.id} value={t.id}>等 {t.title}</option>)}</select>}
          <div className="flex-1" />
          <button className="btn btn-primary" disabled={busy || !brief.trim()} onClick={create}>{busy ? "启动中…" : "开始任务"}</button>
        </div>
        {err && <div className="text-[12px] text-red-600 mt-2">{err}</div>}
        <div className="text-[11px] text-[var(--faint)] mt-2">Ctrl+Enter 开始。worker 在自己的分支上干活，完成后进入「审阅中」。</div>
      </div>
    </div>
  );
}
