import { Badge, Empty, fmtTime } from "./ui";
import { GROUPS } from "./Sidebar";

// AO-style card: task, agent, branch, activity, review/merge, status — together.
export default function Board({ tasks, onOpen, selectedTaskId, room }) {
  if (tasks.length === 0) return <Empty>看板由真实运行生成：还没有任务。</Empty>;
  const claimsFor = (t) => (room?.claims || []).filter((c) => c.run_id === t.latest_run?.id).length;
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${GROUPS.length}, minmax(150px, 1fr))` }}>
      {GROUPS.map((col) => {
        const items = tasks.filter((t) => col.statuses.includes(t.status));
        return (
          <div key={col.key} className="min-w-0">
            <div className="text-[11px] text-gray-500 mb-2 flex items-center gap-1.5"><span className={`w-1.5 h-1.5 rounded-full ${col.color}`} />{col.label} <span className="text-gray-700">{items.length}</span></div>
            {items.map((t) => (
              <button key={t.id} onClick={() => onOpen(t)}
                className={`w-full text-left bg-gray-900 border rounded-lg p-2.5 mb-2 hover:border-gray-600 ${selectedTaskId === t.id ? "border-indigo-600" : "border-gray-800"}`}>
                <div className="text-sm text-gray-100 truncate">{t.title}</div>
                <div className="text-[10px] text-gray-500 truncate mt-0.5">
                  {t.kind === "worker" ? "worker" : "主 agent"}{t.branch ? ` · ${t.branch}` : t.isolation === "main" ? " · 主目录" : ""}
                </div>
                <div className="mt-1 flex items-center gap-1 flex-wrap">
                  <Badge status={t.status} />
                  {t.review_status !== "unreviewed" && <Badge status="in_review">{t.review_status === "approved" ? "审阅通过" : "需修改"}</Badge>}
                  {t.merge_status === "merged" && <Badge status="done">已合并</Badge>}
                  {claimsFor(t) > 0 && <span className="text-[10px] text-gray-500">🔒 {claimsFor(t)}</span>}
                </div>
                <div className="text-[10px] text-gray-500 mt-1">
                  {t.runs_count} 次运行{t.latest_run?.ended_at ? ` · ${fmtTime(t.latest_run.ended_at)}` : t.latest_run?.started_at ? ` · 始于 ${fmtTime(t.latest_run.started_at)}` : ""}
                  {t.latest_run?.error && <div className="text-rose-400 truncate">{t.latest_run.error}</div>}
                </div>
              </button>
            ))}
          </div>
        );
      })}
    </div>
  );
}
