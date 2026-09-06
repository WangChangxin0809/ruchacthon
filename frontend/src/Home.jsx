import { useEffect, useState } from "react";
import { api } from "./api";
import { Dot, I, STATUS_LABEL, fmtRel, laneOf } from "./ui";

// AO's home: "jump back right in" actions and recent projects; plus the one
// thing AO keeps elsewhere and we surface here: everything waiting on a human.
export default function Home({ projects, lastEvent, onOpenProject, onOpenTask, onAddProject, onOpenChat }) {
  const [ov, setOv] = useState(null);
  useEffect(() => { api.overview().then(setOv).catch(() => {}); }, [lastEvent?.seq]);
  const tasks = ov?.tasks || [];
  const pname = (id) => projects.find((p) => p.id === id)?.name || id;
  const attention = tasks.filter((t) => laneOf(t.status)?.key === "needs_you");
  const lastFor = (pid) => tasks.filter((t) => t.project_id === pid).map((t) => t.latest_run?.ended_at || t.latest_run?.started_at || t.updated_at).sort().pop();

  const Action = ({ icon, label, onClick }) => (
    <button onClick={onClick} className="row gap-3 px-4 py-3 rounded-lg hover:bg-[var(--subtle)] text-left text-[14px]"><span className="text-[var(--muted)]">{icon}</span>{label}</button>
  );
  return (
    <div className="h-full overflow-auto">
      <div className="max-w-[640px] mx-auto pt-24 pb-16 px-6">
        <div className="row justify-between mb-2"><h1 className="text-[17px] font-semibold">从这里开始</h1></div>
        <div className="grid grid-cols-2">
          <Action icon={<I.branch />} label="克隆 git 仓库" onClick={() => onAddProject("clone")} />
          <Action icon={<I.folder />} label="导入服务器上已有目录" onClick={() => onAddProject("existing")} />
          <Action icon={<I.plus />} label="新建空项目" onClick={() => onAddProject("new")} />
          <Action icon={<I.chat />} label="和人聊天" onClick={onOpenChat} />
        </div>

        {(attention.length > 0 || ov?.pending_decisions?.length > 0) && (
          <>
            <h2 className="text-[15px] font-semibold mt-10 mb-2" style={{ color: "var(--needs)" }}>需要你</h2>
            {ov.pending_decisions.map((d) => (
              <button key={d.id} onClick={() => onOpenProject(d.project_id)} className="row w-full gap-3 px-4 py-2.5 rounded-lg hover:bg-[var(--subtle)] text-left">
                <Dot color="var(--needs)" /><div className="flex-1 min-w-0"><div className="text-[13px]">同一工作区抢 <code className="text-[12px]">{d.subject?.path}</code>：「{d.subject?.requester?.task_title}」想要，「{d.subject?.holder?.task_title}」持有</div><div className="text-[11px] text-[var(--muted)]">{pname(d.project_id)} · 裁决前被挡住的 worker 一直等着</div></div>
              </button>
            ))}
            {attention.map((t) => (
              <button key={t.id} onClick={() => onOpenTask(t)} className="row w-full gap-3 px-4 py-2.5 rounded-lg hover:bg-[var(--subtle)] text-left">
                <Dot status={t.status} /><div className="flex-1 min-w-0"><div className="text-[13px] truncate">{t.title}</div><div className="text-[11px] text-[var(--muted)]">{pname(t.project_id)} · {STATUS_LABEL[t.status]}{t.latest_run?.error ? ` · ${t.latest_run.error}` : ""}</div></div>
                <span className="text-[11px] text-[var(--faint)]">{fmtRel(t.latest_run?.ended_at)}</span>
              </button>
            ))}
          </>
        )}

        <h2 className="text-[15px] font-semibold mt-10 mb-2">最近项目</h2>
        {projects.length === 0 && <div className="text-[13px] text-[var(--muted)] px-4">还没有项目。上面三种方式任选一种。</div>}
        {projects.map((p) => {
          const ts = tasks.filter((t) => t.project_id === p.id);
          const live = ts.filter((t) => t.status === "running").length;
          return (
            <button key={p.id} onClick={() => onOpenProject(p.id)} className="row w-full gap-3 px-4 py-2.5 rounded-lg hover:bg-[var(--subtle)] text-left">
              <I.folder className="text-[var(--muted)]" />
              <div className="flex-1 min-w-0"><div className="text-[13px] font-medium">{p.name}</div><div className="text-[11px] text-[var(--muted)] truncate">{p.root_path}</div></div>
              <div className="text-[11px] text-[var(--faint)] text-right">{live ? <span style={{ color: "var(--working)" }}>{live} 个进行中 · </span> : null}{lastFor(p.id) ? fmtRel(lastFor(p.id)) : "从未"}</div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
