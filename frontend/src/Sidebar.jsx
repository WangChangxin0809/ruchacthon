import { useEffect, useRef, useState } from "react";
import { Dot, I, fmtRel } from "./ui";

// AO's sidebar, verbatim in shape: search, projects with their sessions
// nested underneath, utilities pinned at the bottom.
export default function Sidebar({ projects, projectId, tasks, sessions, sessionId, onOpenProject, onOpenSession, onHome, onAddProject, onOpenChat, onOpenSettings, unread, cc, author, setAuthor, onSearchOpen }) {
  const [q, setQ] = useState("");
  const searchRef = useRef(null);
  useEffect(() => {
    const onKey = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); searchRef.current?.focus(); } };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, []);
  const main = sessions.find((s) => s.kind === "main");
  const filter = (t) => !q || t.title.toLowerCase().includes(q.toLowerCase());
  const active = tasks.filter((t) => t.status !== "done").filter(filter);
  const lastActivity = (t) => t.latest_run?.ended_at || t.latest_run?.started_at || t.updated_at;

  return (
    <div className="h-full flex flex-col bg-[var(--bg)] border-r border-[var(--border)] w-60 shrink-0">
      <button onClick={onHome} className="row px-4 h-12 text-[14px] font-semibold hover:opacity-80"><I.bot className="w-5 h-5" /> CC Workbench</button>
      <div className="px-3 pb-2">
        <div className="relative">
          <I.search className="w-3.5 h-3.5 absolute left-2.5 top-2 text-[var(--faint)]" />
          <input ref={searchRef} className="input pl-8 pr-12 py-1.5 bg-[var(--subtle)] border-transparent" placeholder="搜索" value={q} onChange={(e) => setQ(e.target.value)} />
          <span className="absolute right-2 top-1.5 text-[10px] text-[var(--faint)] mono">Ctrl+K</span>
        </div>
      </div>
      <div className="row justify-between px-4 pt-3 pb-1"><span className="text-[12px] text-[var(--muted)] font-medium">项目</span><button onClick={onAddProject} className="text-[var(--muted)] hover:text-[var(--text)]" title="新增项目"><I.plus className="w-4 h-4" /></button></div>
      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {projects.length === 0 && <div className="text-[12px] text-[var(--faint)] px-2 py-3">还没有项目。点右上角 ＋。</div>}
        {projects.map((p) => {
          const open = p.id === projectId;
          return (
            <div key={p.id} className="mb-0.5">
              <div className={`row rounded-md px-2 py-1.5 group ${open ? "bg-[var(--hover)]" : "hover:bg-[var(--subtle)]"}`}>
                <button onClick={() => onOpenProject(p.id)} className="row flex-1 min-w-0 text-left"><I.folder className="w-4 h-4 text-[var(--muted)]" /><span className="truncate text-[13px]">{p.name}</span></button>
                <button onClick={() => { onOpenProject(p.id); main && open ? onOpenSession(main.id) : onOpenSession("main"); }} title="主 agent" className="text-[var(--faint)] hover:text-[var(--text)] opacity-0 group-hover:opacity-100"><I.sitemap className="w-4 h-4" /></button>
              </div>
              {open && (
                <div className="ml-4 border-l border-[var(--border)] pl-1 my-0.5">
                  {main && (
                    <button onClick={() => onOpenSession(main.id)} className={`row w-full rounded-md px-2 py-1 text-left ${sessionId === main.id ? "bg-[var(--hover)]" : "hover:bg-[var(--subtle)]"}`}>
                      <I.sitemap className="w-3.5 h-3.5 text-[var(--muted)]" /><span className="text-[13px] truncate flex-1">主 agent</span>
                    </button>
                  )}
                  {active.map((t) => (
                    <button key={t.id} onClick={() => onOpenSession(t.session_id, t)} className={`row w-full rounded-md px-2 py-1 text-left ${sessionId && sessionId === t.session_id ? "bg-[var(--hover)]" : "hover:bg-[var(--subtle)]"}`}>
                      <Dot status={t.status} /><span className="text-[13px] truncate flex-1">{t.title}</span><span className="text-[10px] text-[var(--faint)] mono">{fmtRel(lastActivity(t)).replace(" 分钟前", "m").replace(" 小时前", "h").replace(" 天前", "d").replace("刚刚", "now")}</span>
                    </button>
                  ))}
                  {active.length === 0 && !q && <div className="text-[11px] text-[var(--faint)] px-2 py-1">没有进行中的任务</div>}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="border-t border-[var(--border)] px-2 py-2 space-y-0.5">
        <button onClick={onOpenChat} className="row w-full rounded-md px-2 py-1.5 hover:bg-[var(--subtle)] text-[13px]"><I.chat className="w-4 h-4 text-[var(--muted)]" /> 聊天{unread ? <span className="ml-auto bg-red-600 text-white rounded-full px-1.5 text-[10px]">{unread}</span> : null}</button>
        <button onClick={onOpenSettings} className="row w-full rounded-md px-2 py-1.5 hover:bg-[var(--subtle)] text-[13px]"><I.gear className="w-4 h-4 text-[var(--muted)]" /> 设置
          <span className="ml-auto row text-[11px] text-[var(--muted)]" title="Claude Code 登录状态"><Dot color={!cc ? "var(--idle)" : cc.effective?.logged_in ? "var(--ready)" : "var(--needs)"} /> {!cc ? "…" : cc.effective?.logged_in ? "CC 已登录" : "CC 未登录"}</span>
        </button>
        <Identity author={author} setAuthor={setAuthor} />
      </div>
    </div>
  );
}

function Identity({ author, setAuthor }) {
  const [editing, setEditing] = useState(!author);
  const [v, setV] = useState(author);
  if (editing) return (
    <form onSubmit={(e) => { e.preventDefault(); if (v.trim()) { setAuthor(v.trim()); setEditing(false); } }} className="row px-2 py-1">
      <I.user className="w-4 h-4 text-[var(--muted)]" /><input autoFocus className="input py-1" placeholder="你的名字" value={v} onChange={(e) => setV(e.target.value)} /><button className="btn btn-primary btn-sm" disabled={!v.trim()}>好</button>
    </form>
  );
  return <button onClick={() => { setV(author); setEditing(true); }} className="row w-full rounded-md px-2 py-1.5 hover:bg-[var(--subtle)] text-[13px]" title="点击改名"><I.user className="w-4 h-4 text-[var(--muted)]" /> {author}</button>;
}
