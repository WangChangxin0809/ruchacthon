import { useState } from "react";
import { ARCHIVE_STATUSES, AvatarStack, Dot, I, LANES, STATUS_LABEL, fmtRel, toneOf } from "./ui";

// AO's board: four delivery lanes, cards that show task + agent + branch +
// status + activity together, archive off to the side. Positions are derived
// from the task view the server computes; nothing here is stored.
export default function Board({ tasks, room, onOpen, onNewTask, onOpenMain, mainLive, onBell, project }) {
  const [archive, setArchive] = useState(false);
  const pending = room?.pending_decisions?.length || 0;
  const archived = tasks.filter((t) => ARCHIVE_STATUSES.includes(t.status));
  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="row h-12 px-4 border-b border-[var(--border)] bg-white shrink-0">
        <I.list className="w-4 h-4" /><span className="font-semibold text-[14px]">看板</span>{project && <span className="text-[12px] text-[var(--muted)] ml-1 truncate">{project.name}</span>}
        <div className="flex-1" />
        <button onClick={onNewTask} className="btn"><I.plus className="w-3.5 h-3.5" /> 任务</button>
        <button onClick={onOpenMain} className="btn btn-primary"><I.sitemap className="w-3.5 h-3.5" /> 主 agent</button>
        <button className="btn btn-ghost relative" title="Room：认领 / 待你裁决" onClick={onBell}><I.bell className="w-4 h-4" />{pending > 0 && <span className="absolute -top-0.5 -right-0.5 bg-red-600 text-white rounded-full px-1 text-[9px]">{pending}</span>}</button>
      </div>
      {!mainLive && (
        <div className="mx-4 mt-3 row gap-2 px-3 py-2 rounded-md border border-[var(--border)] bg-white text-[12px] text-[var(--muted)]"><span className="text-amber-500">▲</span> 这个项目还没有主 agent 在跑。主 agent 负责规划、直接改代码、把活派给 worker。</div>
      )}
      {tasks.length === 0 ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <div className="text-[15px] font-semibold mb-1">还没有 worker 会话</div>
            <div className="text-[13px] text-[var(--muted)] max-w-sm mx-auto mb-4">描述一个任务，主 agent 规划它、派出 worker 会话，并在这里跟踪进展。</div>
            <div className="row justify-center"><button onClick={onOpenMain} className="btn btn-primary"><I.sitemap className="w-3.5 h-3.5" /> 启动主 agent</button><button onClick={onNewTask} className="btn"><I.plus className="w-3.5 h-3.5" /> 新任务</button></div>
          </div>
        </div>
      ) : (
        <div className="flex-1 min-h-0 flex overflow-x-auto mt-3 border-t border-[var(--border)] bg-white">
          {LANES.map((lane) => {
            const items = tasks.filter((t) => lane.statuses.includes(t.status));
            return (
              <div key={lane.key} className="flex-1 min-w-[240px] border-r border-[var(--border)] last:border-r-0 flex flex-col min-h-0">
                <div className="row px-4 h-11 border-b border-[var(--border)] shrink-0"><Dot color={lane.color} /><span className="text-[13px] font-medium" style={{ color: lane.color }}>{lane.label}</span><span className="ml-auto text-[12px] text-[var(--muted)]">{items.length}</span></div>
                <div className="flex-1 overflow-y-auto p-3 space-y-3 bg-[var(--bg)]">
                  {items.length === 0 && <div className="text-[11px] text-[var(--faint)] text-center pt-6">{lane.empty || "空"}</div>}
                  {items.map((t) => <Card key={t.id} t={t} room={room} onOpen={onOpen} />)}
                </div>
              </div>
            );
          })}
          {archive && (
            <div className="flex-1 min-w-[240px] flex flex-col min-h-0">
              <div className="row px-4 h-11 border-b border-[var(--border)] shrink-0"><Dot color="var(--merged)" /><span className="text-[13px] font-medium" style={{ color: "var(--merged)" }}>已合并</span><span className="ml-auto text-[12px] text-[var(--muted)]">{archived.length}</span></div>
              <div className="flex-1 overflow-y-auto p-3 space-y-3 bg-[var(--bg)]">{archived.map((t) => <Card key={t.id} t={t} room={room} onOpen={onOpen} />)}</div>
            </div>
          )}
        </div>
      )}
      {tasks.length > 0 && <button onClick={() => setArchive(!archive)} className="text-[11px] text-[var(--muted)] hover:text-[var(--text)] px-4 py-1.5 border-t border-[var(--border)] bg-white text-left">{archive ? "隐藏" : "显示"}已合并（{archived.length}）</button>}
    </div>
  );
}

function Card({ t, room, onOpen }) {
  const claims = (room?.claims || []).filter((c) => c.run_id === t.latest_run?.id).length;
  const when = t.latest_run?.ended_at || t.latest_run?.started_at || t.updated_at;
  const cost = t.latest_run?.cost_usd;
  const locked = t.member === false;
  const footer = t.review_status === "changes_requested" ? "已要求修改" : t.review_status === "approved" && t.merge_status !== "merged" ? "审阅通过，可合并" : t.merge_status === "merged" ? "已合并到主分支" : t.status === "in_review" ? "等待审阅" : STATUS_LABEL[t.status] || t.status;

  // A task whose conversation you are not in: you see that it exists and who
  // is on it, never a word of what was said.
  if (locked) {
    return (
      <div className="card w-full text-left opacity-70 cursor-not-allowed" title="你不在这个任务的会话里，看不到内容">
        <div className="px-3 pt-3 pb-2">
          <div className="row"><I.lock className="w-3.5 h-3.5 text-[var(--faint)]" /><span className="text-[13px] font-semibold truncate text-[var(--muted)]">{t.title}</span></div>
          <div className="row mt-1.5 text-[11px] text-[var(--faint)]">你不在这个会话里</div>
        </div>
        <div className="row px-3 py-2 border-t border-[var(--border)] text-[11px]">
          <span className="font-medium" style={{ color: toneOf(t.status) }}>{footer}</span>
          {t.members?.length > 0 && <AvatarStack people={t.members} size={17} max={3} />}
          <span className="ml-auto text-[var(--faint)]">{fmtRel(when)}</span>
        </div>
      </div>
    );
  }
  return (
    <button onClick={() => onOpen(t)} className="card card-hover w-full text-left">
      <div className="px-3 pt-3 pb-2">
        <div className="row"><I.bot className="w-4 h-4 text-[var(--agent)]" /><span className="text-[13px] font-semibold truncate">{t.title}</span></div>
        <div className="row mt-1.5 text-[11px] text-[var(--muted)] mono"><I.branch className="w-3.5 h-3.5" /><span className="truncate">{t.branch || (t.isolation === "main" ? "项目目录（不隔离）" : "—")}</span></div>
      </div>
      <div className="row px-3 py-2 border-t border-[var(--border)] text-[11px]">
        <span className="font-medium" style={{ color: toneOf(t.status) }}>{footer}</span>
        {claims > 0 && <span className="text-[var(--faint)]" title={`认领了 ${claims} 个路径`}>🔒{claims}</span>}
        {t.members?.length > 0 && <AvatarStack people={t.members} size={17} max={3} />}
        <span className="ml-auto text-[var(--faint)]">{cost ? `$${cost.toFixed(2)} · ` : ""}{fmtRel(when)}</span>
      </div>
      {t.latest_run?.error && <div className="px-3 pb-2 text-[11px] text-red-600 truncate">{t.latest_run.error}</div>}
    </button>
  );
}
