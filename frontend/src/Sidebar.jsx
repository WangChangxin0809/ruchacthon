import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Avatar, Badge, Button, Dialog, Dot, Field, I, Popover, TEAM_ROLE_LABEL, fmtRelShort } from "./ui";

// AO's sidebar in shape: the team you are acting as at the top, projects with
// their sessions nested underneath, and you plus your inbox pinned at the
// bottom. Everything a person is a member of, nothing they are not.
export default function Sidebar({ wb, view, onOpenProject, onOpenSession, onHome, onAddProject, onOpenChat, onOpenSettings, onOpenInbox }) {
  const { projects, projectId, tasks, sessions, sessionId, me, teamId, unread } = wb;
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
  const cc = wb.cc;

  return (
    <div className="h-full flex flex-col bg-[var(--bg)] border-r border-[var(--border)] w-60 shrink-0">
      <TeamSwitcher wb={wb} onHome={onHome} />

      <div className="px-3 pb-2">
        <div className="relative">
          <I.search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-[var(--faint)]" />
          <input ref={searchRef} className="input pl-8 pr-14 bg-[var(--subtle)] border-transparent" placeholder="搜索任务" value={q} onChange={(e) => setQ(e.target.value)} />
          <span className="absolute right-2 top-2 text-[10px] text-[var(--faint)] mono">Ctrl+K</span>
        </div>
      </div>

      <div className="row justify-between px-4 pt-2 pb-1">
        <span className="label">项目</span>
        <button onClick={onAddProject} className="text-[var(--muted)] hover:text-[var(--text)]" title="新增项目"><I.plus /></button>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {projects.length === 0 && <div className="text-[12px] text-[var(--faint)] px-2 py-3">还没有项目。点上面的 ＋。</div>}
        {projects.map((p) => {
          const open = p.id === projectId;
          return (
            <div key={p.id} className="mb-0.5">
              <div className={`row rounded-md px-2 h-8 group ${open ? "bg-[var(--hover)]" : "hover:bg-[var(--subtle)]"}`}>
                <button onClick={() => onOpenProject(p.id)} className="row flex-1 min-w-0 text-left h-full"><I.folder className="text-[var(--muted)]" /><span className="truncate text-[13px]">{p.name}</span></button>
                <button onClick={() => { onOpenProject(p.id); onOpenSession("main"); }} title="主 agent"
                  className="text-[var(--faint)] hover:text-[var(--text)] opacity-0 group-hover:opacity-100 transition-opacity duration-150"><I.sitemap /></button>
              </div>
              {open && (
                <div className="ml-4 border-l border-[var(--border)] pl-1 my-0.5">
                  {main && (
                    <button onClick={() => onOpenSession(main.id)} className={`nav-item h-7 ${sessionId === main.id && view === "session" ? "nav-item-active" : ""}`}>
                      <I.sitemap className="w-3.5 h-3.5 text-[var(--muted)]" /><span className="truncate flex-1">主 agent</span>
                    </button>
                  )}
                  {active.map((t) => (
                    <button key={t.id} onClick={() => onOpenSession(t.session_id, t)} disabled={t.member === false}
                      className={`nav-item h-7 ${sessionId && sessionId === t.session_id && view === "session" ? "nav-item-active" : ""} ${t.member === false ? "opacity-60" : ""}`}>
                      {t.member === false ? <I.lock className="w-3.5 h-3.5 text-[var(--faint)]" /> : <Dot status={t.status} />}
                      <span className="truncate flex-1">{t.title}</span>
                      <span className="text-[10px] text-[var(--faint)] mono shrink-0">{fmtRelShort(lastActivity(t))}</span>
                    </button>
                  ))}
                  {active.length === 0 && <div className="text-[11px] text-[var(--faint)] px-2 py-1">{q ? "没有匹配的任务" : "没有进行中的任务"}</div>}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="border-t border-[var(--border)] px-2 py-2 space-y-0.5">
        <button onClick={onOpenChat} className={`nav-item ${view === "chat" ? "nav-item-active" : ""}`}>
          <I.chat className="text-[var(--muted)]" /> 消息
          {wb.conversations.some((c) => c.unread > 0) && <span className="ml-auto dot" style={{ background: "var(--needs)" }} />}
        </button>
        <button onClick={onOpenInbox} className="nav-item">
          <I.inbox className="text-[var(--muted)]" /> 通知
          {unread > 0 && <span className="ml-auto min-w-[18px] h-[18px] px-1 rounded-full bg-[var(--needs)] text-white text-[11px] font-medium flex items-center justify-center">{unread > 99 ? "99+" : unread}</span>}
        </button>
        <button onClick={onOpenSettings} className="nav-item">
          <I.gear className="text-[var(--muted)]" /> 设置
          <span className="ml-auto row text-[11px] text-[var(--muted)]" title="Claude Code 登录状态">
            <Dot color={!cc ? "var(--idle)" : cc.effective?.logged_in ? "var(--ready)" : "var(--needs)"} />
          </span>
        </button>
        <Me wb={wb} onOpenSettings={onOpenSettings} />
      </div>
    </div>
  );
}

function TeamSwitcher({ wb, onHome }) {
  const { me, teamId, setTeamId } = wb;
  const teams = me?.teams || [];
  const current = teams.find((t) => t.id === teamId) || teams[0];
  const [creating, setCreating] = useState(false);
  return (
    <>
      <div className="row px-2 h-12 gap-1">
        <button onClick={onHome} className="btn btn-ghost btn-sm btn-icon" title="首页"><I.sitemap className="w-[18px] h-[18px]" /></button>
        <Popover className="flex-1 min-w-0" width={230}
          trigger={({ toggle, open }) => (
            <button onClick={toggle} className="row w-full min-w-0 rounded-md px-2 h-8 hover:bg-[var(--subtle)] text-left transition-colors duration-150">
              <span className="flex-1 min-w-0">
                <span className="block truncate text-[13px] font-semibold leading-tight">{current?.name || "还没有团队"}</span>
                <span className="block truncate text-[11px] text-[var(--muted)] leading-tight">{current ? `${current.member_count} 人 · ${TEAM_ROLE_LABEL[current.role] || current.role}` : "创建一个开始"}</span>
              </span>
              <I.chevupdown className={`w-3.5 h-3.5 text-[var(--faint)] transition-transform duration-150 ${open ? "opacity-100" : "opacity-60"}`} />
            </button>
          )}>
          {({ close }) => (
            <div className="p-1">
              <div className="label px-2 py-1">团队</div>
              {teams.map((t) => (
                <button key={t.id} className="menu-item" onClick={() => { setTeamId(t.id); close(); }}>
                  <span className="w-5 h-5 rounded bg-[var(--subtle)] text-[10px] font-semibold flex items-center justify-center">{t.name.slice(0, 1)}</span>
                  <span className="flex-1 truncate">{t.name}</span>
                  {t.id === current?.id && <I.check className="w-3.5 h-3.5" />}
                </button>
              ))}
              {teams.length === 0 && <div className="px-2 py-1.5 hint">你还不属于任何团队。</div>}
              <div className="divider my-1" />
              <button className="menu-item" onClick={() => { close(); setCreating(true); }}><I.plus className="text-[var(--muted)]" />创建团队</button>
            </div>
          )}
        </Popover>
      </div>
      {creating && <NewTeamDialog onClose={() => setCreating(false)} onCreated={async (t) => { setCreating(false); await wb.refetch("me"); setTeamId(t.id); }} />}
    </>
  );
}

function NewTeamDialog({ onClose, onCreated }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const create = async () => {
    setBusy(true); setError("");
    try { onCreated(await api.createTeam(name.trim())); } catch (e) { setError(e.message); setBusy(false); }
  };
  return (
    <Dialog title="创建团队" onClose={onClose} width={380}
      footer={<><Button onClick={onClose}>取消</Button><Button kind="primary" disabled={!name.trim() || busy} onClick={create}>创建</Button></>}>
      <Field label="团队名" hint="项目、模型配置和 agent 定义都属于团队；你会成为所有者。">
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && name.trim() && create()} placeholder="演示团队" />
      </Field>
      {error && <div className="mt-3 text-[12px] text-red-600">{error}</div>}
    </Dialog>
  );
}

function Me({ wb, onOpenSettings }) {
  const { me, signOut } = wb;
  if (!me) return null;
  return (
    <Popover width={200} align="left" side="top"
      trigger={({ toggle }) => (
        <button onClick={toggle} className="nav-item">
          <Avatar name={me.display_name} handle={me.handle} size={22} />
          <span className="flex-1 truncate">{me.display_name}</span>
          {me.is_admin && <Badge tone="outline" title="实例管理员">管理</Badge>}
        </button>
      )}>
      {({ close }) => (
        <div className="p-1">
          <div className="px-2 py-1.5">
            <div className="text-[13px] font-medium truncate">{me.display_name}</div>
            <div className="text-[11px] text-[var(--muted)]">@{me.handle}</div>
          </div>
          <div className="divider my-1" />
          <button className="menu-item" onClick={() => { close(); onOpenSettings("general"); }}><I.gear className="text-[var(--muted)]" />账号设置</button>
          <button className="menu-item text-red-600" onClick={() => { close(); signOut(); }}><I.logout />退出登录</button>
        </div>
      )}
    </Popover>
  );
}
