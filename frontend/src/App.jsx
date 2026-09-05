import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import Board from "./Board";
import Chat from "./Chat";
import Home from "./Home";
import NewTaskDialog from "./NewTaskDialog";
import ProjectDialog from "./ProjectDialog";
import SessionView, { DecisionCard } from "./SessionView";
import Settings from "./Settings";
import Sidebar from "./Sidebar";
import { useWorkbench } from "./store";
import { I, fmtTime } from "./ui";

const TERMINAL = ["succeeded", "failed", "cancelled", "interrupted", "exhausted"];

// AO's shell: a sidebar and one centre view (home / board / session / chat),
// dialogs on top. State the URL can carry (?p= ?s= ?area=) keeps working so
// links pasted into chat still land on the right thing.
export default function App() {
  const wb = useWorkbench();
  const [view, setView] = useState(() => {
    const q = new URLSearchParams(location.search);
    const area = q.get("area");
    if (q.get("s")) return "session";
    if (area === "chat") return "chat";
    if (q.get("p") || area === "work") return "board";
    return "home";
  });
  const [showSettings, setShowSettings] = useState(() => new URLSearchParams(location.search).get("area") === "settings");
  const [projectDialog, setProjectDialog] = useState(null); // null | clone | new | existing
  const [showNewTask, setShowNewTask] = useState(false);
  const [showRoom, setShowRoom] = useState(false);
  const [session, setSession] = useState(null);
  const [profileData, setProfileData] = useState({ profiles: [], default_profile_id: null, default_model: null });
  const [unread, setUnread] = useState(0);
  const author = wb.author || "";

  useEffect(() => { api.profiles().then(setProfileData).catch(() => {}); }, [showSettings, showNewTask]);
  useEffect(() => {
    if (!wb.sessionId) { setSession(null); return; }
    api.session(wb.sessionId).then(setSession).catch(() => setSession(null));
  }, [wb.sessionId, wb.tasks]);
  useEffect(() => {
    if (wb.lastEvent?.type === "chat_message" && view !== "chat" && wb.lastEvent.payload.author !== author) setUnread((n) => n + 1);
  }, [wb.lastEvent]);
  useEffect(() => { if (view === "chat") setUnread(0); }, [view]);
  useEffect(() => {
    // keep the address bar shareable: ?p=&s= for a session, ?p= for a board
    const q = new URLSearchParams();
    if (view === "chat") q.set("area", "chat");
    if ((view === "board" || view === "session") && wb.projectId) q.set("p", wb.projectId);
    if (view === "session" && wb.sessionId) q.set("s", wb.sessionId);
    const s = q.toString();
    history.replaceState(null, "", location.pathname + (s ? `?${s}` : ""));
  }, [view, wb.projectId, wb.sessionId]);

  const mainSession = wb.sessions.find((s) => s.kind === "main");
  const task = wb.tasks.find((t) => t.session_id === wb.sessionId) || null;
  const taskFull = task ? { ...task, workspace: session?.workspace } : null;
  const mainLive = wb.sessions.some((s) => s.kind === "main" && s.last_run_status && !TERMINAL.includes(s.last_run_status)) || wb.tasks.some((t) => t.status === "running");

  const openProject = useCallback((pid) => {
    if (pid !== wb.projectId) { wb.setSessionId(null); wb.setProjectId(pid); }
    setView("board");
  }, [wb]);
  const openSession = useCallback((sid) => {
    if (sid === "main") { if (mainSession) wb.setSessionId(mainSession.id); else api.mainSession(wb.projectId).then((s) => wb.setSessionId(s.id)).catch(() => {}); }
    else wb.setSessionId(sid);
    setView("session");
  }, [wb, mainSession]);
  const openTask = useCallback((t) => {
    if (t.project_id && t.project_id !== wb.projectId) { wb.setSessionId(null); wb.setProjectId(t.project_id); }
    if (t.session_id) { wb.setSessionId(t.session_id); setView("session"); } else setView("board");
  }, [wb]);

  const project = wb.projects.find((p) => p.id === wb.projectId);
  const pending = wb.room?.pending_decisions || [];

  return (
    <div className="h-screen flex overflow-hidden bg-[var(--bg)] text-[var(--text)]">
      <Sidebar projects={wb.projects} projectId={wb.projectId} tasks={wb.tasks} sessions={wb.sessions} sessionId={view === "session" ? wb.sessionId : null}
        onOpenProject={openProject} onOpenSession={openSession} onHome={() => setView("home")} onAddProject={() => setProjectDialog("clone")}
        onOpenChat={() => setView("chat")} onOpenSettings={() => setShowSettings(true)} unread={unread} cc={wb.cc} author={author} setAuthor={wb.setAuthor} />

      <main className="flex-1 min-w-0 flex flex-col min-h-0 relative">
        {wb.health && !wb.health.claude_binary && <div className="bg-red-50 text-red-700 text-[12px] px-4 py-1.5 border-b border-red-200">服务器上找不到 claude 可执行文件，运行会失败。</div>}
        {view === "home" && <Home projects={wb.projects} lastEvent={wb.lastEvent} onOpenProject={openProject} onOpenTask={openTask} onAddProject={setProjectDialog} onOpenChat={() => setView("chat")} />}
        {view === "chat" && <Chat channels={wb.channels} author={author} lastEvent={wb.lastEvent} refetch={wb.refetch} projects={wb.projects} onOpenTask={openTask} />}
        {(view === "board" || view === "session") && !wb.projectId && <Home projects={wb.projects} lastEvent={wb.lastEvent} onOpenProject={openProject} onOpenTask={openTask} onAddProject={setProjectDialog} onOpenChat={() => setView("chat")} />}
        {view === "board" && wb.projectId && (
          <Board tasks={wb.tasks} room={wb.room} onOpen={openTask} onNewTask={() => setShowNewTask(true)} onOpenMain={() => openSession("main")} mainLive={mainLive} onBell={() => setShowRoom(true)} project={project} />
        )}
        {view === "session" && wb.projectId && (
          <SessionView session={session} task={taskFull} messages={wb.messages} streams={wb.streams} runStatus={wb.runStatus} profileData={profileData}
            author={author} room={wb.room} artifacts={wb.artifacts} refetch={wb.refetch}
            onCancel={(runId) => api.cancelRun(runId)} onRetry={(id) => api.retryTask(id)} onOpenSettings={() => setShowSettings(true)}
            onNewTask={() => setShowNewTask(true)} onOpenMain={() => openSession("main")} onBack={() => setView("board")} />
        )}
        {wb.wsStatus !== "connected" && <div className="absolute bottom-3 right-3 text-[11px] bg-amber-50 text-amber-700 border border-amber-200 rounded-full px-3 py-1 shadow-sm">事件流断开，重连中…（不会丢事件）</div>}
      </main>

      {showRoom && <RoomDrawer room={wb.room} tasks={wb.tasks} author={author || "human"} onClose={() => setShowRoom(false)} />}
      {showSettings && <Settings projectId={wb.projectId} onClose={() => setShowSettings(false)} author={author} setAuthor={wb.setAuthor} cc={wb.cc} refetch={wb.refetch} />}
      {projectDialog && <ProjectDialog initialMode={projectDialog} onClose={() => setProjectDialog(null)} onCreated={(p) => { setProjectDialog(null); wb.refetch("projects"); openProject(p.id); }} />}
      {showNewTask && wb.projectId && (
        <NewTaskDialog projectId={wb.projectId} tasks={wb.tasks} profileData={profileData} onClose={() => setShowNewTask(false)}
          onCreated={(r) => { setShowNewTask(false); wb.refetch("tasks"); wb.refetch("sessions"); if (r?.session?.id) { wb.setSessionId(r.session.id); setView("session"); } }} />
      )}
    </div>
  );
}

// AO puts the orchestrator's attention items behind the bell; ours are the
// Room's pending decisions, claims and overlaps.
function RoomDrawer({ room, tasks, author, onClose }) {
  const title = (runId) => tasks.find((x) => x.latest_run?.id === runId)?.title || room.members?.find((m) => m.run_id === runId)?.task_title || runId?.slice(-6);
  const pending = room.pending_decisions || [];
  return (
    <div className="fixed inset-0 z-40" onClick={onClose}>
      <div className="absolute right-0 top-0 h-full w-[380px] bg-white border-l border-[var(--border)] shadow-xl overflow-y-auto p-4 space-y-5 text-[12px]" onClick={(e) => e.stopPropagation()}>
        <div className="row"><span className="font-semibold text-[14px]">Room</span><div className="flex-1" /><button className="btn btn-ghost btn-sm" onClick={onClose}><I.x className="w-4 h-4" /></button></div>
        <section>
          <div className="label mb-2" style={{ color: pending.length ? "var(--needs)" : undefined }}>待你裁决 · {pending.length}</div>
          {pending.length === 0 && <div className="text-[var(--muted)]">没有。只有两个 worker 在同一工作区抢同一路径时才会出现，被挡住的 worker 会一直等你。</div>}
          {pending.map((d) => <DecisionCard key={d.id} d={d} author={author} />)}
        </section>
        <section>
          <div className="label mb-2">认领 · {room.claims?.length || 0}</div>
          {(room.claims || []).map((c) => <div key={c.id} className="row truncate"><span className="text-[var(--muted)] shrink-0">{title(c.run_id)}</span><span>→</span><span className="mono truncate">{c.path}</span></div>)}
          {(room.overlaps || []).map((o, i) => <div key={i} className={o.same_workspace ? "text-red-600" : "text-amber-600"}><span className="mono">{o.a.path}</span> ↔ <span className="mono">{o.b.path}</span> {o.same_workspace ? "同一工作区（冲突）" : "各自 worktree（合并时会冲突）"}</div>)}
        </section>
        <section>
          <div className="label mb-2">广播 / 交接</div>
          {(room.messages || []).slice(0, 20).map((m) => <div key={m.id} className="truncate"><span className="text-[var(--faint)] mono">{fmtTime(m.created_at)}</span> <span className="text-[var(--muted)]">[{m.kind}]</span> {m.payload.text || m.payload.note || m.payload.path || ""}</div>)}
          {(room.messages || []).length === 0 && <div className="text-[var(--muted)]">还没有。</div>}
        </section>
      </div>
    </div>
  );
}
