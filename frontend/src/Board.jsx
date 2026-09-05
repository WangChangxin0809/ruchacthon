import { Badge, Empty, fmtTime } from "./ui";

const COLUMNS = [
  { key: "todo", label: "待开始 / 排队", statuses: ["todo", "queued"] },
  { key: "running", label: "运行中", statuses: ["running"] },
  { key: "attention", label: "需要处理", statuses: ["needs_input", "failed", "cancelled", "interrupted", "exhausted", "changes_requested"] },
  { key: "review", label: "待审阅", statuses: ["in_review"] },
  { key: "merge", label: "可合并 / 已合并", statuses: ["ready_to_merge", "done"] },
];

export default function Board({ tasks, onOpen, selectedTaskId }) {
  if (tasks.length === 0) return <Empty>看板由真实运行生成：还没有任务。</Empty>;
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${COLUMNS.length}, minmax(150px, 1fr))` }}>
      {COLUMNS.map((col) => {
        const items = tasks.filter((t) => col.statuses.includes(t.status));
        return (
          <div key={col.key} className="min-w-0">
            <div className="text-[11px] text-gray-500 mb-2">{col.label} <span className="text-gray-700">{items.length}</span></div>
            {items.map((t) => (
              <button key={t.id} onClick={() => onOpen(t)}
                className={`w-full text-left bg-gray-900 border rounded-lg p-2.5 mb-2 hover:border-gray-600 ${selectedTaskId === t.id ? "border-indigo-600" : "border-gray-800"}`}>
                <div className="text-sm text-gray-100 truncate">{t.title}</div>
                <div className="mt-1 flex items-center gap-1 flex-wrap">
                  <Badge status={t.status} />
                  {t.review_status !== "unreviewed" && <Badge status="in_review">{t.review_status === "approved" ? "审阅通过" : "需修改"}</Badge>}
                  {t.merge_status === "merged" && <Badge status="done">已合并</Badge>}
                </div>
                <div className="text-[10px] text-gray-500 mt-1">
                  {t.runs_count} 次运行{t.latest_run?.ended_at ? ` · ${fmtTime(t.latest_run.ended_at)}` : ""}
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
