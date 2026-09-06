import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import Board from "./Board";
import Chat from "./Chat";
import Home from "./Home";
import Inbox from "./Inbox";
import Login from "./Login";
import NewTaskDialog from "./NewTaskDialog";
import ProjectDialog from "./ProjectDialog";
import SessionView, { DecisionCard } from "./SessionView";
import Settings from "./Settings";
import Sidebar from "./Sidebar";
import { useWorkbench } from "./store";
import { Avatar, Button, EmptyState, I, Spinner, fmtTime, useToast } from "./ui";

const TERMINAL = ["succeeded", "failed", "cancelled", "interrupted", "exhausted"];

// AO's shell: a sidebar and one centre view (home / board / session / chat),
// dialogs and drawers on top. State the URL can carry (?p= ?s= ?c= ?area=)
// keeps working so links pasted into a chat still land on the right thing.
export default function App() {
  const wb = useWorkbench();
  const [authInfo, setAuthInfo] = useState(null);
  const [view, setView] = useState(() => {
    const q = new URLSearchParams(location.search);
    const area = q.get("area");
    if (q.get("s")) return "session";
    if (area === "chat" || q.get("c")) return "chat";
    if (q.get("p") || area === "work") return "board";
    return "home";
  });
  const [chatId, setChatId] = useState(() => new URLSearchParams(location.search).get("c"));
  const [settingsTab, setSettingsTab] = useState(null);
  const [projectDialog, setProjectDialog] = useState(null);
  const [showNewTask, setShowNewTask] = useState(false);
  const [showRoom, setShowRoom] = useState(false);
  const [showInbox, setShowInbox] = useState(false);
  const [session, setSession] = useState(null);
  const [toastNode, toast] = useToast();
  const [profileData, setProfileData] = useState({ profiles: [], default_profile_id: null, default_model: null });

  useEffect(() => {
    if (wb.authState === "anon") api.authState().then(setAuthInfo).catch(() => setAuthInfo({}));
  }, [wb.authState]);
  useEffect(() => {
    const area = new URLSearchParams(location.search).get("area");
    if (area === "settings") setSettingsTab("general");
  }, []);
  useEffect(() => { if (wb.authState === "ok") api.profiles().then(setProfileData).catch(() => {}); }, [settingsTab, showNewTask, wb.authState]);
  useEffect(() => {
    if (!wb.sessionId) { setSession(null); return; }
    api.session(wb.sessionId).then(setSession).catch(() => setSession(null));
  }, [wb.sessionId, wb.tasks, wb.sessions]);
  useEffect(() => {
    const q = new URLSearchParams();
    if (view === "chat") { q.set("area", "chat"); if (chatId) q.set("c", chatId); }
    if ((view === "board" || view === "session") && wb.projectId) q.set("p", wb.projectId);
    if (view === "session" && wb.sessionId) q.set("s", wb.sessionId);
    const s = q.toString();
    history.replaceState(null, "", location.pathname + (s ? `?${s}` : ""));
  }, [view, wb.projectId, wb.sessionId, chatId]);

  const mainSession = wb.sessions.find((s) => s.kind === "main");
  const task = wb.tasks.find((t) => t.session_id === wb.sessionId) || null;
  const taskFull = task ? { ...task, workspace: session?.workspace } : null;
  const mainLive = wb.sessions.some((s) => s.kind === "main" && s.last_run_status && !TERMINAL.includes(s.last_run_status)) || wb.tasks.some((t) => t.status === "running");

  const openProject = useCallback((pid) => {
    if (pid !== wb.projectId) { wb.setSessionId(null); wb.setProjectId(pid); }
    setView("board");
  }, [wb]);
  const openSession = useCallback((sid) => {
    if (sid === "main") {
      if (mainSession) wb.setSessionId(mainSession.id);
      else api.mainSession(wb.projectId).then((s) => { wb.setSessionId(s.id); wb.refetch("sessions"); }).catch(() => {});
    } else wb.setSessionId(sid);
    setView("session");
  }, [wb, mainSession]);
  // the one thing a locked card can do: ask the owner in (ADR 0004 §2)
  const requestJoin = useCallback(async (t) => {
    try {
      const r = await api.requestJoin(t.session_id);
      toast(r.already_member ? "你已经在这个会话里了" : r.already_sent ? "已经申请过了，等 owner 处理" : "已经告诉 owner 了");
      if (r.already_member) wb.refetch("tasks");
    } catch (e) { toast(e.message, "red"); }
  }, [wb, toast]);
  const openTask = useCallback((t) => {
    if (t.member === false) return;
    if (t.project_id && t.project_id !== wb.projectId) { wb.setSessionId(null); wb.setProjectId(t.project_id); }
    if (t.session_id) { wb.setSessionId(t.session_id); setView("session"); } else setView("board");
  }, [wb]);
  // where a notification points
  const navigate = useCallback((link) => {
    if (link.session_id) { if (link.project_id) wb.setProjectId(link.project_id); wb.setSessionId(link.session_id); setView("session"); return; }
    if (link.conversation_id) { setChatId(link.conversation_id); setView("chat"); return; }
    if (link.project_id) openProject(link.project_id);
  }, [wb, openProject]);

  if (wb.authState === "loading") {
    return <div className="h-screen flex items-center justify-center text-[var(--muted)]"><Spinner /></div>;
  }
  if (wb.authState === "anon") {
    return <Login authState={authInfo} onSignedIn={wb.signIn} />;
  }

  const project = wb.projects.find((p) => p.id === wb.projectId);
  const noTeam = (wb.me?.teams || []).length === 0;

  return (
    <div className="h-screen flex overflow-hidden bg-[var(--bg)] text-[var(--text)]">
      <Sidebar wb={wb} view={view} onOpenProject={openProject} onOpenSession={openSession} onHome={() => setView("home")}
        onAddProject={() => setProjectDialog("clone")} onOpenChat={() => setView("chat")} onOpenInbox={() => setShowInbox(true)}
        onOpenSettings={(tab) => setSettingsTab(typeof tab === "string" ? tab : "general")} />

      <main className="flex-1 min-w-0 flex flex-col min-h-0 relative">
        {wb.health && !wb.health.claude_binary && <div className="bg-red-50 text-red-700 text-[12px] px-4 py-1.5 border-b border-red-200">服务器上找不到 claude 可执行文件，运行会失败。</div>}

        {view === "home" && (noTeam
          ? <EmptyState className="h-full" icon={<I.users className="w-5 h-5" />} title="先建一个团队"
              action={<Button kind="primary" onClick={() => setSettingsTab("team")}><I.plus />去创建</Button>}>
              项目、模型配置和 agent 定义都属于团队。建一个，然后把人邀请进来。
            </EmptyState>
          : <Home projects={wb.projects} lastEvent={wb.lastEvent} onOpenProject={openProject} onOpenTask={openTask} onAddProject={setProjectDialog} onOpenChat={() => setView("chat")} />)}

        {view === "chat" && <Chat wb={wb} convId={chatId} onConv={setChatId} onOpenSession={openSession} />}

        {(view === "board" || view === "session") && !wb.projectId && (
          <Home projects={wb.projects} lastEvent={wb.lastEvent} onOpenProject={openProject} onOpenTask={openTask} onAddProject={setProjectDialog} onOpenChat={() => setView("chat")} />
        )}
        {view === "board" && wb.projectId && (
          <Board tasks={wb.tasks} room={wb.room} onOpen={openTask} onNewTask={() => setShowNewTask(true)} onOpenMain={() => openSession("main")}
            mainLive={mainLive} onBell={() => setShowRoom(true)} project={project} onRequestJoin={requestJoin} />
        )}
        {view === "session" && wb.projectId && (
          <SessionView session={session} task={taskFull} messages={wb.messages} streams={wb.streams} runStatus={wb.runStatus}
            profileData={profileData} me={wb.me} agents={wb.agents} room={wb.room} artifacts={wb.artifacts} refetch={wb.refetch}
            onCancel={(runId) => api.cancelRun(runId)} onRetry={(id) => api.retryTask(id)} onOpenSettings={() => setSettingsTab("models")}
            onNewTask={() => setShowNewTask(true)} onOpenMain={() => openSession("main")} onBack={() => setView("board")} />
        )}
        {wb.wsStatus !== "connected" && <div className="absolute bottom-3 right-3 text-[11px] bg-amber-50 text-amber-700 border border-amber-200 rounded-full px-3 py-1 shadow-sm">事件流断开，重连中…（不会丢事件）</div>}
      </main>

      {showInbox && <Inbox wb={wb} onClose={() => setShowInbox(false)} onNavigate={navigate} />}
      {showRoom && <RoomDrawer room={wb.room} tasks={wb.tasks} onClose={() => setShowRoom(false)} />}
      {settingsTab && <Settings wb={wb} tab={settingsTab} setTab={setSettingsTab} onClose={() => setSettingsTab(null)} />}
      {projectDialog && <ProjectDialog initialMode={projectDialog} teamId={wb.teamId} onClose={() => setProjectDialog(null)}
        onCreated={(p) => { setProjectDialog(null); wb.refetch("projects"); openProject(p.id); }} />}
      {showNewTask && wb.projectId && (
        <NewTaskDialog projectId={wb.projectId} tasks={wb.tasks} profileData={profileData} agents={wb.agents} onClose={() => setShowNewTask(false)}
          onCreated={(r) => { setShowNewTask(false); wb.refetch("tasks"); wb.refetch("sessions"); if (r?.session?.id) { wb.setSessionId(r.session.id); setView("session"); } }} />
      )}
      {toastNode}
    </div>
  );
}

// AO puts the orchestrator's attention items behind the bell; ours are the
// Room's pending decisions, claims and overlaps.
function RoomDrawer({ room, tasks, onClose }) {
  const title = (runId) => tasks.find((x) => x.latest_run?.id === runId)?.title || room.members?.find((m) => m.run_id === runId)?.task_title || runId?.slice(-6);
  const pending = room.pending_decisions || [];
  return (
    <div className="fixed inset-0 z-40 bg-black/20 animate-fade" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="absolute right-0 top-0 h-full w-[400px] max-w-[92vw] bg-white border-l border-[var(--border)] shadow-[var(--shadow-lg)] overflow-y-auto p-4 space-y-5 text-[12px] animate-rise">
        <div className="row"><span className="font-semibold text-[14px]">Room</span><div className="flex-1" /><button className="btn btn-ghost btn-sm btn-icon" onClick={onClose} aria-label="关闭"><I.x /></button></div>
        <section>
          <div className="label mb-2" style={{ color: pending.length ? "var(--needs)" : undefined }}>待你裁决 · {pending.length}</div>
          {pending.length === 0 && <div className="text-[var(--muted)]">没有。只有两个 worker 在同一工作区抢同一路径时才会出现，被挡住的 worker 会一直等你。</div>}
          {pending.map((d) => <DecisionCard key={d.id} d={d} />)}
        </section>
        <section>
          <div className="label mb-2">认领 · {room.claims?.length || 0}</div>
          {(room.claims || []).map((c) => <div key={c.id} className="row truncate"><span className="text-[var(--muted)] shrink-0">{title(c.run_id)}</span><span>→</span><span className="mono truncate">{c.path}</span></div>)}
          {(room.claims || []).length === 0 && <div className="text-[var(--muted)]">没有 worker 认领着文件。</div>}
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
