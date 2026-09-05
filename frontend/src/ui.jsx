import { useState } from "react";

export const STATUS_LABEL = {
  todo: "待开始", queued: "排队中", running: "运行中", needs_input: "等待决定", in_review: "待审阅", changes_requested: "需修改",
  ready_to_merge: "可合并", done: "已合并", failed: "失败", cancelled: "已取消", interrupted: "已中断", exhausted: "步数耗尽", unknown: "未知",
};
export const STATUS_COLOR = {
  todo: "text-gray-400 border-gray-700", queued: "text-sky-300 border-sky-800", running: "text-emerald-300 border-emerald-800",
  needs_input: "text-amber-300 border-amber-700", in_review: "text-violet-300 border-violet-800", changes_requested: "text-orange-300 border-orange-800",
  ready_to_merge: "text-teal-300 border-teal-800", done: "text-gray-300 border-gray-600", failed: "text-rose-300 border-rose-800",
  cancelled: "text-gray-400 border-gray-700", interrupted: "text-rose-300 border-rose-900", exhausted: "text-orange-300 border-orange-900",
};

export function Badge({ status, children }) {
  return (
    <span className={`text-[11px] px-1.5 py-0.5 rounded border whitespace-nowrap ${STATUS_COLOR[status] || "text-gray-400 border-gray-700"}`}>
      {children ?? STATUS_LABEL[status] ?? status}
    </span>
  );
}

export function Button({ children, kind = "default", className = "", ...props }) {
  const styles = {
    default: "bg-gray-800 hover:bg-gray-700 text-gray-100 border border-gray-700",
    primary: "bg-indigo-700 hover:bg-indigo-600 text-white",
    danger: "bg-rose-900 hover:bg-rose-800 text-rose-100 border border-rose-800",
    ghost: "hover:bg-gray-800 text-gray-300",
  };
  return (
    <button className={`text-xs rounded px-2.5 py-1 disabled:opacity-40 disabled:cursor-not-allowed ${styles[kind]} ${className}`} {...props}>
      {children}
    </button>
  );
}

export function Empty({ children }) {
  return <div className="text-sm text-gray-600 border border-dashed border-gray-800 rounded-lg p-6 text-center">{children}</div>;
}

export function Details({ summary, children, className = "" }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={className}>
      <button onClick={() => setOpen(!open)} className="text-[11px] text-gray-500 hover:text-gray-300">{open ? "▾" : "▸"} {summary}</button>
      {open && <div className="mt-1">{children}</div>}
    </div>
  );
}

export function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function shortId(id) {
  return id ? id.slice(-6) : "";
}
