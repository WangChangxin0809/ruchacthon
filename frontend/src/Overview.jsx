import { useEffect, useState } from "react";
import { api } from "./api";
import { Badge, Button, Empty, fmtTime } from "./ui";
import { GROUPS } from "./Sidebar";


export default function Overview({ lastEvent, onOpenProject, onOpenTask, onAddProject, author }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.overview().then(setData).catch((e) => setErr(e.message)); }, [lastEvent?.seq]);
  if (err) return <Empty>总览加载失败：{err}</Empty>;
  if (!data) return <div className="text-sm text-gray-500 p-6">加载中…</div>;
  const pname = (id) => data.projects.find((p) => p.id === id)?.name || id;

  return (
    <div className="p-5 space-y-6 overflow-auto h-full">
      <div className="grid grid-cols-4 gap-3">
        {[["项目", data.counts.projects], ["任务", data.counts.tasks], ["运行中", data.counts.live_runs], ["待裁决", data.counts.pending_decisions]].map(([l, n]) => (
          <div key={l} className="bg-gray-900 border border-gray-800 rounded-lg p-3"><div className="text-[11px] text-gray-500">{l}</div><div className="text-2xl text-gray-100">{n}</div></div>
        ))}
      </div>

      <section>
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm text-gray-300">项目</h2>
          <Button kind="primary" onClick={onAddProject}>＋ 新增项目</Button>
        </div>
        {data.projects.length === 0 && <Empty>还没有项目。点「新增项目」：克隆一个 git 仓库、新建空项目，或选服务器上已有的目录。</Empty>}
        <div className="grid grid-cols-3 gap-3">
          {data.projects.map((p) => {
            const ts = data.tasks.filter((t) => t.project_id === p.id);
            const live = ts.filter((t) => ["running", "queued"].includes(t.status)).length;
            const attention = ts.filter((t) => GROUPS[0].statuses.includes(t.status)).length;
            return (
              <button key={p.id} onClick={() => onOpenProject(p.id)} className="text-left bg-gray-900 border border-gray-800 rounded-lg p-3 hover:border-indigo-600">
                <div className="text-gray-100 text-sm">{p.name} {!p.is_git && <span className="text-[10px] text-amber-400">非 git</span>}</div>
                <div className="text-[11px] text-gray-500 truncate">{p.root_path}</div>
                <div className="text-[11px] text-gray-400 mt-1">{ts.length} 个任务 · {live} 运行中{attention ? <span className="text-amber-300"> · {attention} 需处理</span> : null}</div>
              </button>
            );
          })}
        </div>
      </section>

      {data.pending_decisions.length > 0 && (
        <section>
          <h2 className="text-sm text-rose-300 mb-2">待你裁决（{data.pending_decisions.length}）</h2>
          {data.pending_decisions.map((d) => (
            <button key={d.id} onClick={() => onOpenProject(d.project_id)} className="block w-full text-left bg-gray-900 border border-rose-900 rounded p-2 mb-1 text-xs text-gray-200">
              {pname(d.project_id)} · <code>{d.subject?.path}</code>：「{d.subject?.requester?.task_title}」想要，「{d.subject?.holder?.task_title}」持有 → 打开项目的 Room 面板裁决
            </button>
          ))}
        </section>
      )}

      <section>
        <h2 className="text-sm text-gray-300 mb-2">所有任务</h2>
        {data.tasks.length === 0 && <Empty>还没有任务。打开一个项目，在主 agent 会话里派活，或点「新任务」。</Empty>}
        <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${GROUPS.length}, minmax(160px, 1fr))` }}>
          {GROUPS.map((g) => {
            const items = data.tasks.filter((t) => g.statuses.includes(t.status));
            return (
              <div key={g.label}>
                <div className="text-[11px] text-gray-500 mb-2 flex items-center gap-1.5"><span className={`w-1.5 h-1.5 rounded-full ${g.color}`} />{g.label} <span className="text-gray-700">{items.length}</span></div>
                {items.map((t) => (
                  <button key={t.id} onClick={() => onOpenTask(t)} className="w-full text-left bg-gray-900 border border-gray-800 rounded-lg p-2.5 mb-2 hover:border-gray-600">
                    <div className="text-sm text-gray-100 truncate">{t.title}</div>
                    <div className="text-[10px] text-gray-500 truncate">{pname(t.project_id)}</div>
                    <div className="mt-1 flex gap-1 flex-wrap"><Badge status={t.status} />{t.latest_run?.ended_at && <span className="text-[10px] text-gray-600">{fmtTime(t.latest_run.ended_at)}</span>}</div>
                  </button>
                ))}
              </div>
            );
          })}
        </div>
      </section>

      {data.recent_artifacts.length > 0 && (
        <section>
          <h2 className="text-sm text-gray-300 mb-2">最近成果</h2>
          {data.recent_artifacts.slice(0, 10).map((a) => (
            <button key={a.id} onClick={() => onOpenTask({ id: a.task_id, project_id: a.project_id })} className="block w-full text-left text-xs text-gray-300 bg-gray-900 border border-gray-800 rounded px-2 py-1 mb-1">
              <span className="text-gray-500">{a.kind}</span> {a.title} v{a.version} · {pname(a.project_id)} · {fmtTime(a.created_at)}
            </button>
          ))}
        </section>
      )}
      
    </div>
  );
}
