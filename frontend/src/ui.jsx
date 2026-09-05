import { useEffect, useLayoutEffect, useRef, useState } from "react";

// Task status → AO-style delivery lane + tone. Display is derived, never stored.
export const STATUS_LABEL = {
  todo: "待开始", queued: "排队中", running: "进行中", needs_input: "等待你决定", in_review: "待审阅", changes_requested: "已要求修改",
  ready_to_merge: "可合并", done: "已合并", failed: "失败", cancelled: "已取消", interrupted: "已中断", exhausted: "步数耗尽", unknown: "未知",
  succeeded: "已完成", pending: "等待中",
};
export const LANES = [
  { key: "building", label: "进行中", color: "var(--working)", statuses: ["running", "queued", "todo"], empty: "没有 worker 在跑" },
  { key: "needs_you", label: "需要你", color: "var(--needs)", statuses: ["needs_input", "failed", "interrupted", "exhausted", "cancelled", "changes_requested"], empty: "没有等你的事" },
  { key: "review", label: "审阅中", color: "var(--review)", statuses: ["in_review"], empty: "没有待审阅的改动" },
  { key: "ready", label: "可合并", color: "var(--ready)", statuses: ["ready_to_merge"], empty: "没有审阅通过待合并的" },
];
export const ARCHIVE_STATUSES = ["done"];
export function laneOf(status) { return LANES.find((l) => l.statuses.includes(status)) || null; }
export function toneOf(status) {
  if (status === "done") return "var(--merged)";
  if (status === "queued" || status === "todo") return "var(--idle)";
  return laneOf(status)?.color || "var(--idle)";
}
// SDK permission modes → the label a person understands (contract A).
export const PERMISSION_LABEL = { plan: "仅可查看", acceptEdits: "工作区内修改", bypassPermissions: "完全权限" };
export const permissionLabel = (m) => PERMISSION_LABEL[m] || m || "—";
export const ROLE_LABEL = { orchestrator: "主控", worker: "工作者", any: "通用" };
export const TEAM_ROLE_LABEL = { owner: "所有者", admin: "管理员", member: "成员" };

export function Dot({ status, color, breathe }) {
  return <span className={`dot ${breathe || status === "running" ? "animate-breathe" : ""}`} style={{ background: color || toneOf(status) }} />;
}
export function StatusText({ status, children }) {
  return <span className="text-[12px] font-medium" style={{ color: toneOf(status) }}>{children ?? STATUS_LABEL[status] ?? status}</span>;
}
export function StatusBadge({ status, children }) {
  return <span className="badge border" style={{ color: toneOf(status), borderColor: "var(--border)" }}>{children ?? STATUS_LABEL[status] ?? status}</span>;
}
// Generic badge: tone = neutral | accent | blue | green | amber | red | violet
const TONES = {
  neutral: "bg-[var(--subtle)] text-[var(--muted)]", accent: "bg-[var(--accent)] text-white", blue: "bg-blue-50 text-blue-700",
  green: "bg-green-50 text-green-700", amber: "bg-amber-50 text-amber-700", red: "bg-red-50 text-red-700", violet: "bg-violet-50 text-violet-700",
  outline: "border border-[var(--border)] text-[var(--muted)] bg-white",
};
export function Badge({ tone = "neutral", children, className = "", title }) {
  return <span title={title} className={`badge ${TONES[tone] || TONES.neutral} ${className}`}>{children}</span>;
}
export function Button({ children, kind = "default", size, className = "", ...props }) {
  const k = { default: "", primary: "btn-primary", danger: "btn-danger", ghost: "btn-ghost" }[kind] || "";
  return <button className={`btn ${k} ${size === "sm" ? "btn-sm" : size === "xs" ? "btn-xs" : ""} ${className}`} {...props}>{children}</button>;
}
export function Empty({ children, title }) {
  return (
    <div className="text-center py-10">
      {title && <div className="text-[14px] font-semibold mb-1">{title}</div>}
      <div className="text-[13px] text-[var(--muted)] max-w-md mx-auto">{children}</div>
    </div>
  );
}
// AO's empty state: icon in a soft tile, one sentence, one primary action.
export function EmptyState({ icon, title, children, action, className = "" }) {
  return (
    <div className={`flex flex-col items-center justify-center text-center py-12 px-6 ${className}`}>
      {icon && <div className="w-11 h-11 rounded-xl bg-[var(--subtle)] text-[var(--muted)] flex items-center justify-center mb-3">{icon}</div>}
      {title && <div className="text-[14px] font-semibold mb-1">{title}</div>}
      {children && <div className="text-[12px] text-[var(--muted)] max-w-sm">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
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
export function Toggle({ on, onChange, disabled, title }) {
  return <button type="button" role="switch" aria-checked={!!on} title={title} disabled={disabled} onClick={() => onChange?.(!on)} className={`w-8 h-[18px] rounded-full relative transition-colors duration-150 ${on ? "bg-[var(--accent)]" : "bg-gray-300"} disabled:opacity-40`}><span className={`absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow-sm transition-[left] duration-150 ${on ? "left-[16px]" : "left-[2px]"}`} /></button>;
}
export function Skeleton({ className = "" }) { return <div className={`skeleton ${className}`} />; }
export function Spinner({ className = "" }) { return <span className={`inline-block w-3.5 h-3.5 border-2 border-current border-r-transparent rounded-full animate-spin-slow ${className}`} />; }

// ---- people -----------------------------------------------------------------
// One deterministic hue per handle so the same person looks the same everywhere.
export function hueOf(s) { let h = 0; for (const c of String(s || "")) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h % 360; }
export function initialsOf(name) {
  const n = String(name || "?").trim();
  if (!n) return "?";
  if (/^[A-Za-z]/.test(n)) { const w = n.split(/[\s_.-]+/).filter(Boolean); return (w.length > 1 ? w[0][0] + w[1][0] : n.slice(0, 2)).toUpperCase(); }
  return n.slice(0, 1);
}
export function Avatar({ name, handle, size = 24, agent, className = "", title, ring }) {
  const key = handle || name || "?";
  const hue = hueOf(key);
  const style = agent
    ? { width: size, height: size, background: "var(--agent)", color: "#fff" }
    : { width: size, height: size, background: `hsl(${hue} 60% 92%)`, color: `hsl(${hue} 45% 32%)` };
  return (
    <span title={title ?? name} className={`inline-flex items-center justify-center rounded-full shrink-0 font-semibold select-none ${ring ? "ring-2 ring-white" : ""} ${className}`} style={{ ...style, fontSize: Math.max(9, Math.round(size * 0.42)) }}>
      {agent ? <I.bot className="" style={{ width: size * 0.6, height: size * 0.6 }} /> : initialsOf(name)}
    </span>
  );
}
export function AvatarStack({ people = [], size = 20, max = 4 }) {
  const shown = people.slice(0, max);
  const extra = people.length - shown.length;
  return (
    <span className="inline-flex items-center">
      {shown.map((p, i) => <Avatar key={p.id || p.member_id || i} name={p.name || p.display_name} handle={p.handle} agent={p.member_kind === "agent" || p.agent} size={size} ring className={i ? "-ml-1.5" : ""} />)}
      {extra > 0 && <span className="-ml-1.5 rounded-full bg-[var(--subtle)] ring-2 ring-white text-[10px] text-[var(--muted)] font-medium flex items-center justify-center" style={{ width: size, height: size }}>+{extra}</span>}
    </span>
  );
}

// ---- overlays ---------------------------------------------------------------
// Popover anchored to a trigger; closes on outside click and Esc.
export function Popover({ trigger, children, align = "left", side = "bottom", width, className = "", open: ctl, onOpenChange }) {
  const [open, setOpenState] = useState(false);
  const isOpen = ctl ?? open;
  const setOpen = (v) => { setOpenState(v); onOpenChange?.(v); };
  const ref = useRef(null);
  useEffect(() => {
    if (!isOpen) return;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } };
    document.addEventListener("mousedown", onDoc); document.addEventListener("keydown", onKey, true);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey, true); };
  }, [isOpen]);
  const pos = `${side === "top" ? "bottom-full mb-1" : "top-full mt-1"} ${align === "right" ? "right-0" : "left-0"}`;
  return (
    <div ref={ref} className={`relative ${className}`}>
      {typeof trigger === "function" ? trigger({ open: isOpen, toggle: () => setOpen(!isOpen) }) : <span onClick={() => setOpen(!isOpen)}>{trigger}</span>}
      {isOpen && <div className={`popover ${pos}`} style={{ width }}>{typeof children === "function" ? children({ close: () => setOpen(false) }) : children}</div>}
    </div>
  );
}
// Modal with blurred backdrop; Esc closes; the first input is focused.
export function Dialog({ title, onClose, children, width = 560, footer, className = "", bodyClass = "p-5" }) {
  const box = useRef(null);
  useEffect(() => {
    const k = (e) => { if (e.key === "Escape") { e.stopPropagation(); onClose?.(); } };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  useLayoutEffect(() => { box.current?.querySelector("input:not([type=hidden]),textarea,select")?.focus(); }, []);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-[2px] animate-fade" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose?.(); }}>
      <div ref={box} role="dialog" aria-modal="true" className={`bg-white border border-[var(--border)] rounded-xl shadow-[var(--shadow-lg)] max-w-[95vw] max-h-[90vh] flex flex-col animate-rise ${className}`} style={{ width }}>
        {title && <div className="row h-12 px-5 border-b border-[var(--border)] shrink-0"><span className="font-semibold text-[14px] truncate">{title}</span><div className="flex-1" /><button className="btn btn-ghost btn-sm btn-icon" onClick={onClose} aria-label="关闭"><I.x className="w-4 h-4" /></button></div>}
        <div className={`flex-1 min-h-0 overflow-y-auto ${bodyClass}`}>{children}</div>
        {footer && <div className="row justify-end gap-2 px-5 py-3 border-t border-[var(--border)] shrink-0 bg-[var(--bg)] rounded-b-xl">{footer}</div>}
      </div>
    </div>
  );
}
export function Tabs({ tabs, value, onChange, size = "md", className = "" }) {
  return (
    <div className={`inline-flex bg-[var(--subtle)] rounded-md p-0.5 ${className}`} role="tablist">
      {tabs.map((t) => (
        <button key={t.value} role="tab" aria-selected={value === t.value} onClick={() => onChange(t.value)}
          className={`rounded-[5px] font-medium transition-[background-color,color,box-shadow] duration-150 ${size === "sm" ? "px-2.5 h-6 text-[12px]" : "px-3 h-7 text-[13px]"} ${value === t.value ? "bg-white shadow-sm text-[var(--text)]" : "text-[var(--muted)] hover:text-[var(--text)]"}`}>{t.label}</button>
      ))}
    </div>
  );
}
export function Field({ label, hint, error, children, className = "" }) {
  return (
    <label className={`block ${className}`}>
      {label && <span className="field-label">{label}</span>}
      {children}
      {error ? <span className="block text-[12px] text-red-600 mt-1">{error}</span> : hint ? <span className="block hint mt-1">{hint}</span> : null}
    </label>
  );
}
export function Callout({ tone = "neutral", children, className = "" }) {
  const t = { neutral: "bg-[var(--subtle)] text-[var(--muted)] border-[var(--border)]", amber: "bg-amber-50 text-amber-800 border-amber-200", red: "bg-red-50 text-red-700 border-red-200", green: "bg-green-50 text-green-700 border-green-200", blue: "bg-sky-50 text-sky-800 border-sky-200" }[tone];
  return <div className={`text-[12px] border rounded-md px-3 py-2 ${t} ${className}`}>{children}</div>;
}
// Light markdown, shared by the transcript and the preview pane: fenced code,
// inline code, headings and bullets. Not a markdown library on purpose --
// agent text and workspace documents are read here, not authored.
export function Text({ text }) {
  const parts = String(text ?? "").split(/(```[\s\S]*?```)/g);
  return (
    <div className="text-[13.5px] leading-relaxed whitespace-pre-wrap">
      {parts.map((p, i) => p.startsWith("```")
        ? <pre key={i} className="bg-[var(--subtle)] rounded p-2 my-1 text-[12px] overflow-auto">{p.replace(/^```\w*\n?/, "").replace(/```$/, "")}</pre>
        : p.split("\n").map((line, j) => {
            const h = line.match(/^(#{1,4})\s+(.*)$/);
            const li = line.match(/^\s*[-*]\s+(.*)$/);
            const body = inline(h ? h[2] : li ? li[1] : line, `${i}-${j}`);
            if (h) return <div key={`${i}-${j}`} className={`font-semibold mt-2 ${h[1].length <= 2 ? "text-[15px]" : "text-[13.5px]"}`}>{body}</div>;
            if (li) return <div key={`${i}-${j}`} className="pl-4 -indent-2">· {body}</div>;
            return <div key={`${i}-${j}`}>{body}</div>;
          }))}
    </div>
  );
}

function inline(s, key) {
  return s.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((x, k) => {
    if (x.startsWith("`")) return <code key={`${key}-${k}`} className="bg-[var(--subtle)] rounded px-1 text-[12px] text-[var(--working)]">{x.slice(1, -1)}</code>;
    if (x.startsWith("**")) return <strong key={`${key}-${k}`}>{x.slice(2, -2)}</strong>;
    return <span key={`${key}-${k}`}>{x}</span>;
  });
}

export function useToast() {
  const [toast, setToast] = useState(null);
  const show = (text, tone = "neutral") => { setToast({ text, tone }); setTimeout(() => setToast(null), 2200); };
  const node = toast ? <div className={`fixed bottom-4 left-1/2 -translate-x-1/2 z-[60] px-3 py-1.5 rounded-full text-[12px] shadow-[var(--shadow-md)] animate-rise ${toast.tone === "red" ? "bg-red-600 text-white" : "bg-[var(--accent)] text-white"}`}>{toast.text}</div> : null;
  return [node, show];
}

// ---- time -------------------------------------------------------------------
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
export function fmtRelShort(iso) { return fmtRel(iso).replace(" 分钟前", "m").replace(" 小时前", "h").replace(" 天前", "d").replace("刚刚", "now"); }
export function fmtDur(a, b) {
  if (!a || !b) return "";
  const s = Math.round((new Date(b) - new Date(a)) / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}
export function fmtDay(iso) {
  const d = new Date(iso); const t = new Date(); const y = new Date(); y.setDate(t.getDate() - 1);
  if (d.toDateString() === t.toDateString()) return "今天";
  if (d.toDateString() === y.toDateString()) return "昨天";
  return d.toLocaleDateString([], { month: "long", day: "numeric" });
}
export function fmtDateTime(iso) { return iso ? new Date(iso).toLocaleString([], { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""; }
export function isToday(iso) { return iso && new Date(iso).toDateString() === new Date().toDateString(); }
export function shortId(id) { return id ? id.slice(-6) : ""; }
// navigator.clipboard exists only in a secure context, and a self-hosted box
// is usually plain http on an IP -- so the old textarea + execCommand path is
// the one that actually runs there, not a legacy fallback.
export async function copyText(t) {
  try {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(t); return true; }
  } catch { /* denied or insecure: fall through */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = t;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, t.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch { return false; }
}

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
  chevupdown: svg(<><path d="m7 15 5 5 5-5" /><path d="m7 9 5-5 5 5" /></>),
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
  checks: svg(<><path d="m2 12 5 5L18 6" /><path d="m13 17 9-11" /></>),
  x: svg(<><path d="M18 6 6 18" /><path d="m6 6 12 12" /></>),
  user: svg(<><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>),
  users: svg(<><circle cx="9" cy="8" r="3.5" /><path d="M2 20a7 7 0 0 1 14 0" /><path d="M16 4.5a3.5 3.5 0 0 1 0 7" /><path d="M18 13.5a6.5 6.5 0 0 1 4 6.5" /></>),
  at: svg(<><circle cx="12" cy="12" r="4" /><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8" /></>),
  inbox: svg(<><path d="M22 12h-6l-2 3h-4l-2-3H2" /><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.7 4H7.3a2 2 0 0 0-1.8 1.1" /></>),
  key: svg(<><circle cx="7.5" cy="15.5" r="4.5" /><path d="m21 2-9.6 9.6" /><path d="m15.5 7.5 3 3L22 7l-3-3" /></>),
  logout: svg(<><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="m16 17 5-5-5-5" /><path d="M21 12H9" /></>),
  shield: svg(<><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10" /><path d="m9 12 2 2 4-4" /></>),
  link: svg(<><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" /><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" /></>),
  lock: svg(<><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>),
  arrowleft: svg(<><path d="m12 19-7-7 7-7" /><path d="M19 12H5" /></>),
  clock: svg(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  merge: svg(<><circle cx="18" cy="18" r="2.5" /><circle cx="6" cy="6" r="2.5" /><circle cx="6" cy="18" r="2.5" /><path d="M6 8.5v7" /><path d="M6 8.5c0 4 3 6 9.5 7" /></>),
  external: svg(<><path d="M15 3h6v6" /><path d="M10 14 21 3" /><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /></>),
  help: svg(<><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7" /><path d="M12 17h.01" /></>),
  sparkle: svg(<><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /><path d="M19 17l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z" /></>),
  more: svg(<><circle cx="5" cy="12" r="1.2" /><circle cx="12" cy="12" r="1.2" /><circle cx="19" cy="12" r="1.2" /></>),
  info: svg(<><circle cx="12" cy="12" r="9" /><path d="M12 8h.01" /><path d="M11 12h1v4h1" /></>),
  star: svg(<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9z" />),
  refresh: svg(<><path d="M21 12a9 9 0 1 1-2.6-6.4" /><path d="M21 3v6h-6" /></>),
  building: svg(<><rect x="4" y="3" width="16" height="18" rx="1.5" /><path d="M9 7h2" /><path d="M13 7h2" /><path d="M9 11h2" /><path d="M13 11h2" /><path d="M9 15h2" /><path d="M13 15h2" /><path d="M10 21v-3h4v3" /></>),
  hash: svg(<><path d="M4 9h16" /><path d="M4 15h16" /><path d="M10 3 8 21" /><path d="m16 3-2 21" /></>),
  message: svg(<><path d="M21 12a8 8 0 0 1-8 8H5l-2 2V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8" /></>),
  zap: svg(<path d="M13 2 4 14h7l-1 8 9-12h-7z" />),
  wallet: svg(<><rect x="3" y="6" width="18" height="14" rx="2" /><path d="M3 10h18" /><path d="M16 15h.01" /></>),
};
