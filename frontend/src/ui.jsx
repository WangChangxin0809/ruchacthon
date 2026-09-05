import { useState } from "react";

// Task status → AO-style delivery lane + tone. Display is derived, never stored.
export const STATUS_LABEL = {
  todo: "待开始", queued: "排队中", running: "进行中", needs_input: "等待你决定", in_review: "待审阅", changes_requested: "已要求修改",
  ready_to_merge: "可合并", done: "已合并", failed: "失败", cancelled: "已取消", interrupted: "已中断", exhausted: "步数耗尽", unknown: "未知",
  succeeded: "已完成", pending: "等待中",
};
export const LANES = [
  { key: "building", label: "进行中", color: "var(--working)", statuses: ["running", "queued", "todo"] },
  { key: "needs_you", label: "需要你", color: "var(--needs)", statuses: ["needs_input", "failed", "interrupted", "exhausted", "cancelled", "changes_requested"] },
  { key: "review", label: "审阅中", color: "var(--review)", statuses: ["in_review"] },
  { key: "ready", label: "可合并", color: "var(--ready)", statuses: ["ready_to_merge"] },
];
export const ARCHIVE_STATUSES = ["done"];
export function laneOf(status) { return LANES.find((l) => l.statuses.includes(status)) || null; }
export function toneOf(status) {
  if (status === "done") return "var(--merged)";
  if (status === "queued" || status === "todo") return "var(--idle)";
  return laneOf(status)?.color || "var(--idle)";
}

export function Dot({ status, color, breathe }) {
  return <span className={`dot ${breathe || status === "running" ? "animate-breathe" : ""}`} style={{ background: color || toneOf(status) }} />;
}
export function StatusText({ status, children }) {
  return <span className="text-[12px] font-medium" style={{ color: toneOf(status) }}>{children ?? STATUS_LABEL[status] ?? status}</span>;
}
export function Badge({ status, children }) {
  return <span className="text-[11px] px-1.5 py-0.5 rounded border whitespace-nowrap" style={{ color: toneOf(status), borderColor: "var(--border)" }}>{children ?? STATUS_LABEL[status] ?? status}</span>;
}
export function Button({ children, kind = "default", size, className = "", ...props }) {
  const k = { default: "", primary: "btn-primary", danger: "btn-danger", ghost: "btn-ghost" }[kind] || "";
  return <button className={`btn ${k} ${size === "sm" ? "btn-sm" : ""} ${className}`} {...props}>{children}</button>;
}
export function Empty({ children, title }) {
  return (
    <div className="text-center py-10">
      {title && <div className="text-[14px] font-semibold mb-1">{title}</div>}
      <div className="text-[13px] text-[var(--muted)] max-w-md mx-auto">{children}</div>
    </div>
  );
}
export function Details({ summary, children, className = "", open: init = false }) {
  const [open, setOpen] = useState(init);
  return (
    <div className={className}>
      <button onClick={() => setOpen(!open)} className="text-[12px] text-[var(--muted)] hover:text-[var(--text)] inline-flex items-center gap-1">{summary} <I.chev className={`w-3 h-3 transition ${open ? "rotate-90" : ""}`} /></button>
      {open && <div className="mt-1">{children}</div>}
    </div>
  );
}
export function Toggle({ on, onChange, disabled }) {
  return <button disabled={disabled} onClick={() => onChange?.(!on)} className={`w-8 h-4.5 h-[18px] rounded-full relative transition ${on ? "bg-[var(--accent)]" : "bg-gray-300"} disabled:opacity-40`}><span className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white transition ${on ? "left-[16px]" : "left-[2px]"}`} /></button>;
}

export function fmtTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
export function fmtRel(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 45) return "刚刚";
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`;
  return `${Math.round(s / 86400)} 天前`;
}
export function fmtDur(a, b) {
  if (!a || !b) return "";
  const s = Math.round((new Date(b) - new Date(a)) / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}
export function shortId(id) { return id ? id.slice(-6) : ""; }

// Minimal line icons (lucide geometry, hand-trimmed).
// a className without an explicit width keeps the 16px default, so callers can pass only a colour
const svg = (d, extra = {}) => ({ className = "", ...p }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={`shrink-0 ${/\bw-/.test(className) ? "" : "w-4 h-4 "}${className}`} {...extra} {...p}>{d}</svg>
);
export const I = {
  folder: svg(<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z" />),
  search: svg(<><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></>),
  plus: svg(<><path d="M5 12h14" /><path d="M12 5v14" /></>),
  branch: svg(<><circle cx="6" cy="18" r="2.5" /><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="9" r="2.5" /><path d="M6 8.5v7" /><path d="M18 11.5c0 3-3 4-6 4.5-2 .3-3.5 1-4 2.5" /></>),
  list: svg(<><path d="M8 6h13" /><path d="M8 12h13" /><path d="M8 18h13" /><path d="M3 6h.01" /><path d="M3 12h.01" /><path d="M3 18h.01" /></>),
  globe: svg(<><circle cx="12" cy="12" r="9" /><path d="M3 12h18" /><path d="M12 3a14 14 0 0 1 0 18" /><path d="M12 3a14 14 0 0 0 0 18" /></>),
  files: svg(<><path d="M15 2H9a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V7z" /><path d="M3 7v13a2 2 0 0 0 2 2h11" /></>),
  trash: svg(<><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /></>),
  sitemap: svg(<><rect x="9" y="3" width="6" height="5" rx="1" /><rect x="3" y="16" width="6" height="5" rx="1" /><rect x="15" y="16" width="6" height="5" rx="1" /><path d="M12 8v4" /><path d="M6 16v-2a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2" /></>),
  bell: svg(<><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10 21h4" /></>),
  terminal: svg(<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m7 9 3 3-3 3" /><path d="M13 15h4" /></>),
  pencil: svg(<><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" /></>),
  filediff: svg(<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M12 12v6" /><path d="M9 15h6" /></>),
  chev: svg(<path d="m9 6 6 6-6 6" />),
  chevdown: svg(<path d="m6 9 6 6 6-6" />),
  send: svg(<><path d="M12 19V5" /><path d="m5 12 7-7 7 7" /></>),
  gear: svg(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" /></>),
  chat: svg(<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />),
  copy: svg(<><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>),
  panel: svg(<><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M15 3v18" /></>),
  bot: svg(<><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M12 8V4" /><path d="M9 13h.01" /><path d="M15 13h.01" /><path d="M8 4h8" /></>),
  compass: svg(<><circle cx="12" cy="12" r="9" /><path d="m16 8-2.5 6L8 16l2.5-6z" /></>),
  eye: svg(<><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12" /><circle cx="12" cy="12" r="3" /></>),
  plug: svg(<><path d="M12 22v-5" /><path d="M9 8V2" /><path d="M15 8V2" /><path d="M18 8v5a6 6 0 0 1-12 0V8z" /></>),
  brain: svg(<><path d="M12 5a3 3 0 1 0-5.9 1A4 4 0 0 0 5 14a4 4 0 0 0 1 5.5A3 3 0 0 0 12 19z" /><path d="M12 5a3 3 0 1 1 5.9 1A4 4 0 0 1 19 14a4 4 0 0 1-1 5.5A3 3 0 0 1 12 19z" /></>),
  check: svg(<path d="m5 12 5 5L20 7" />),
  x: svg(<><path d="M18 6 6 18" /><path d="m6 6 12 12" /></>),
  user: svg(<><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>),
  arrowleft: svg(<><path d="m12 19-7-7 7-7" /><path d="M19 12H5" /></>),
  clock: svg(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  merge: svg(<><circle cx="18" cy="18" r="2.5" /><circle cx="6" cy="6" r="2.5" /><circle cx="6" cy="18" r="2.5" /><path d="M6 8.5v7" /><path d="M6 8.5c0 4 3 6 9.5 7" /></>),
  external: svg(<><path d="M15 3h6v6" /><path d="M10 14 21 3" /><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /></>),
};
