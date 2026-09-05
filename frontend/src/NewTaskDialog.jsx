import { useState } from "react";
import { api } from "./api";
import { Button, Dialog, I, ROLE_LABEL } from "./ui";

// AO's "Create a new task": one brief, then the three choices that actually
// change the run on a single row — which agent definition, which model, and
// whether it gets its own worktree.
export default function NewTaskDialog({ projectId, tasks, profileData, agents, onClose, onCreated }) {
  const [brief, setBrief] = useState("");
  const [choice, setChoice] = useState("");
  const [isolation, setIsolation] = useState("worktree");
  const [dep, setDep] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const defs = (agents?.items || []).filter((d) => d.role === "worker" || d.role === "any");
  const [agentId, setAgentId] = useState(agents?.defaults?.worker || "worker");
  const def = defs.find((d) => d.id === agentId);

  const options = [{ value: "", label: "默认模型" }];
  for (const p of profileData?.profiles || []) {
    for (const m of p.models?.length ? p.models : [null]) options.push({ value: `${p.id}|${m || ""}`, label: `${p.display_name || p.name} ▸ ${m || "默认"}` });
  }
  const [profileId, model] = choice.split("|");
  const title = brief.trim().split("\n")[0].slice(0, 60);

  async function create() {
    setBusy(true); setErr("");
    try {
      const r = await api.createTask(projectId, {
        title, instructions: brief, isolation, edit_mode: "exclusive", depends_on: dep ? [dep] : [],
        profile_id: profileId || null, model: model || null, agent_definition_id: agentId || null,
      });
      onCreated(r.task || r);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <Dialog title="新建任务" width={580} onClose={onClose} bodyClass="p-5 pt-4"
      footer={<>
        <span className="text-[11px] text-[var(--faint)] mr-auto">Ctrl+Enter 开始</span>
        <Button onClick={onClose}>取消</Button>
        <Button kind="primary" disabled={busy || !brief.trim()} onClick={create}>{busy ? "启动中…" : "开始任务"}</Button>
      </>}>
      <textarea autoFocus rows={4} value={brief} onChange={(e) => setBrief(e.target.value)}
        className="w-full resize-none text-[14px] leading-relaxed focus:outline-none placeholder:text-[var(--faint)] bg-transparent"
        placeholder="描述要做的改动、约束和验收方式。第一行会成为任务名。"
        onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && brief.trim()) create(); }} />

      <div className="row mt-3 gap-2 flex-wrap">
        <div className="row gap-1.5 pl-1 pr-2 h-8 rounded-md border border-[var(--border)] bg-white">
          <I.bot className="w-3.5 h-3.5 text-[var(--agent)] ml-1" />
          <select className="bg-transparent text-[12px] focus:outline-none max-w-[190px]" value={agentId} onChange={(e) => setAgentId(e.target.value)}>
            {defs.map((d) => <option key={d.id} value={d.id}>{d.name}{agents?.defaults?.worker === d.id ? "（默认）" : ""}</option>)}
          </select>
        </div>
        <select className="input py-1 text-[12px] w-auto max-w-[200px]" value={choice} onChange={(e) => setChoice(e.target.value)}>
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <select className="input py-1 text-[12px] w-auto" value={isolation} onChange={(e) => setIsolation(e.target.value)}>
          <option value="worktree">独立 worktree</option><option value="main">直接在项目目录</option>
        </select>
        {tasks.length > 0 && (
          <select className="input py-1 text-[12px] w-auto max-w-[150px]" value={dep} onChange={(e) => setDep(e.target.value)}>
            <option value="">不依赖</option>{tasks.map((t) => <option key={t.id} value={t.id}>等 {t.title}</option>)}
          </select>
        )}
      </div>

      {def && (
        <div className="text-[11px] text-[var(--muted)] mt-2 leading-relaxed">
          <span className="text-[var(--text)]">{ROLE_LABEL[def.role]} · {def.permission_label} · 最多 {def.max_turns} 轮</span>
          {def.description ? ` — ${def.description}` : ""}
        </div>
      )}
      {err && <div className="text-[12px] text-red-600 mt-2">{err}</div>}
      <div className="text-[11px] text-[var(--faint)] mt-2">
        {isolation === "worktree" ? "worker 在自己的分支上干活，完成后进入「审阅中」。" : "直接改项目目录：和别的任务抢同一个文件时会停下来等你裁决。"}
      </div>
    </Dialog>
  );
}
