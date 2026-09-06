import { api } from "./api";
import { Button, EmptyState, I, fmtDay, fmtRel } from "./ui";

// Everything addressed to you, newest first. Clicking one takes you where it
// happened and marks it read; the server decides what you are told about, so
// there is nothing to filter here.
const KIND = {
  mention: { icon: "at", label: "有人 @ 你", tone: "text-[var(--link)]" },
  dm: { icon: "message", label: "私聊", tone: "text-[var(--link)]" },
  question: { icon: "help", label: "agent 提问", tone: "text-[var(--agent)]" },
  decision_pending: { icon: "shield", label: "等你裁决", tone: "text-[var(--needs)]" },
  artifact: { icon: "files", label: "新成果", tone: "text-[var(--review)]" },
  run_finished: { icon: "check", label: "任务结束", tone: "text-[var(--muted)]" },
  feedback: { icon: "pencil", label: "反馈", tone: "text-[var(--muted)]" },
  member_added: { icon: "users", label: "加入", tone: "text-[var(--muted)]" },
  invite_accepted: { icon: "users", label: "加入", tone: "text-[var(--muted)]" },
};

export default function Inbox({ wb, onClose, onNavigate }) {
  const items = wb.notifications;
  const unread = items.filter((n) => !n.read);

  const open = async (n) => {
    if (!n.read) await api.readNotifications({ ids: [n.id] }).catch(() => {});
    wb.refetch("notifications");
    onNavigate(n.link || {});
    onClose();
  };
  const readAll = async () => { await api.readNotifications({ all: true }).catch(() => {}); wb.refetch("notifications"); };

  let lastDay = "";
  return (
    <div className="fixed inset-0 z-40 bg-black/20 animate-fade" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="absolute right-0 top-0 h-full w-[400px] max-w-[92vw] bg-white border-l border-[var(--border)] shadow-[var(--shadow-lg)] flex flex-col animate-rise">
        <div className="row h-12 px-4 border-b border-[var(--border)] shrink-0">
          <span className="font-semibold text-[14px]">通知</span>
          {unread.length > 0 && <span className="badge bg-[var(--needs)] text-white">{unread.length}</span>}
          <div className="flex-1" />
          {unread.length > 0 && <Button size="sm" kind="ghost" onClick={readAll}><I.checks />全部已读</Button>}
          <button className="btn btn-ghost btn-sm btn-icon" onClick={onClose} aria-label="关闭"><I.x /></button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto">
          {items.length === 0 && (
            <EmptyState icon={<I.inbox className="w-5 h-5" />} title="没有通知">
              有人 @ 你、agent 问你问题、或者有事等你裁决时，会出现在这里。
            </EmptyState>
          )}
          {items.map((n) => {
            const k = KIND[n.kind] || { icon: "info", label: n.kind, tone: "text-[var(--muted)]" };
            const Icon = I[k.icon] || I.info;
            const day = fmtDay(n.created_at);
            const showDay = day !== lastDay;
            lastDay = day;
            return (
              <div key={n.id}>
                {showDay && <div className="label px-4 pt-4 pb-1">{day}</div>}
                <button onClick={() => open(n)} className={`w-full text-left px-4 py-2.5 flex gap-2.5 hover:bg-[var(--subtle)] transition-colors duration-150 ${n.read ? "" : "bg-[var(--subtle)]/60"}`}>
                  <span className={`mt-0.5 ${k.tone}`}><Icon className="w-4 h-4" /></span>
                  <span className="flex-1 min-w-0">
                    <span className="row gap-1.5">
                      <span className="text-[13px] font-medium truncate flex-1">{n.title}</span>
                      <span className="text-[11px] text-[var(--faint)] shrink-0">{fmtRel(n.created_at)}</span>
                    </span>
                    {n.body && <span className="block text-[12px] text-[var(--muted)] line-clamp-2 mt-0.5">{n.body}</span>}
                  </span>
                  {!n.read && <span className="dot mt-1.5" style={{ background: "var(--link)" }} />}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
