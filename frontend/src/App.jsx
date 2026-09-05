import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import Board from "./Board";
import Chat from "./Chat";
import Overview from "./Overview";
import Preview, { DiffText } from "./Preview";
import ProjectDialog from "./ProjectDialog";
import RoomBar from "./RoomBar";
import SessionView from "./SessionView";
import Settings from "./Settings";
import Sidebar from "./Sidebar";
import { useWorkbench } from "./store";
import { Button, Empty } from "./ui";

// Three areas, one socket: 总览 (every project at a glance, AO-style board),
// 工作 (dsh-style session + AO-style right rail), 聊天 (people only).
export default function App() {
  const wb = useWorkbench();
  const [area, setArea] = useState(() => { const q = new URLSearchParams(location.search); return q.get("area") || (q.get("p") ? "work" : safeGet("wb.area") || "overview"); });
  const [showSettings, setShowSettings] = useState(() => new URLSearchParams(location.search).get("area") === "settings");
  const [tab, setTab] = useState("board");
  const [rightWidth, setRightWidth] = useState(() => Number(safeGet("wb.right")) || 460);
  const [maximized, setMaximized] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState(null);
  const [session, setSession] = useState(null);
  const [diff, setDiff] = useState(null);
  const [profileData, setProfileData] = useState({ profiles: [], default_profile_id: null, default_model: null });
  const [showProject, setShowProject] = useState(false);
  const [unread, setUnread] = useState(0);
  const author = wb.author || "";

  useEffect(() => { safeSet("wb.area", area); }, [area]);
  useEffect(() => { api.profiles().then(setProfileData).catch(() => {}); }, [showSettings]);
  useEffect(() => {
    if (!wb.sessionId) { setSession(null); return; }
    api.session(wb.sessionId).then(setSession).catch(() => setSession(null));
  }, [wb.sessionId, wb.tasks]);
  useEffect(() => {
    if (wb.lastEvent?.type === "chat_message" && area !== "chat" && wb.lastEvent.payload.author !== author) setUnread((n) => n + 1);
  }, [wb.lastEvent]);
  useEffect(() => { if (area === "chat") setUnread(0); }, [area]);

  const task = wb.tasks.find((t) => t.session_id === wb.sessionId) || null;
  const taskFull = task ? { ...task, workspace: session?.workspace } : null;

  useEffect(() => {
    if (tab !== "diff" || !selectedTaskId) return;
    api.taskDiff(selectedTaskId).then(setDiff).catch((e) => setDiff({ available: false, reason: e.message }));
  }, [tab, selectedTaskId, wb.artifacts]);

  const openTask = useCallback((t) => {
    if (t.project_id && t.project_id !== wb.projectId) { wb.setSessionId(null); wb.setProjectId(t.project_id); }
    setSelectedTaskId(t.id);
    if (t.session_id) wb.setSessionId(t.session_id);
    setArea("work");
  }, [wb]);
  const openProject = useCallback((pid) => { if (pid !== wb.projectId) { wb.setSessionId(null); wb.setProjectId(pid); } setArea("work"); }, [wb]);

  const selectedTask = wb.tasks.find((t) => t.id === selectedTaskId) || task;
  const project = wb.projects.find((p) => p.id === wb.projectId);
  const pending = wb.room?.pending_decisions?.length || 0;

  function startDrag(e) {
    const startX = e.clientX, start = rightWidth;
    const move = (ev) => setRightWidth(Math.max(320, Math.min(window.innerWidth - 500, start + (startX - ev.clientX))));
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); safeSet("wb.right", String(rightWidth)); };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  }

  const cc = wb.cc?.effective;
  const nav = (k, l, badge) => (
    <button onClick={() => setArea(k)} className={`px-3 h-9 text-xs relative ${area === k ? "text-white border-b-2 border-indigo-500" : "text-gray-400 hover:text-gray-200"}`}>
      {l}{badge ? <span className="ml-1 bg-rose-600 text-white rounded-full px-1.5 text-[10px]">{badge}</span> : null}
    </button>
  );

  return (
    <div className="h-screen bg-[#0f1115] text-gray-100 flex flex-col overflow-hidden">
      <header className="h-9 px-3 flex items-center border-b border-gray-800 shrink-0 text-xs gap-1">
        <div className="text-gray-200 mr-3 font-medium">CC Workbench</div>
        {nav("overview", "总览", pending)}
        {nav("work", project ? `工作 · ${project.name}` : "工作")}
        {nav("chat", "聊天", unread)}
        <div className="flex-1" />
        {wb.health && !wb.health.claude_binary && <span className="text-rose-300 mr-3">未找到 claude 可执行文件</span>}
        <button onClick={() => setShowSettings(true)} className="flex items-center gap-1.5 px-2 py-1 rounded hover:bg-gray-800" title="Claude Code 登录状态（点击进入设置）">
          <span className={`w-2 h-2 rounded-full ${!wb.cc ? "bg-gray-600" : cc?.logged_in ? "bg-emerald-400" : "bg-rose-500"}`} />
          <span className="text-gray-300">{!wb.cc ? "CC …" : cc?.logged_in ? `CC 已登录${cc.email ? ` · ${cc.email}` : ""}` : "CC 未登录"}</span>
        </button>
        <Identity author={author} setAuthor={wb.setAuthor} />
        <span className={`ml-2 ${wb.wsStatus === "connected" ? "text-emerald-400" : "text-amber-300"}`} title="事件流（断线重连不丢事件）">
          {wb.wsStatus === "connected" ? "●" : "○"}
        </span>
        <Button kind="ghost" onClick={() => setShowSettings(true)} title="设置">⚙</Button>
      </header>

      {area === "overview" && (
        <div className="flex-1 min-h-0">
          <Overview lastEvent={wb.lastEvent} onOpenProject={openProject} onOpenTask={openTask} onAddProject={() => setShowProject(true)} author={author} />
        </div>
      )}

      {(area === "chat" || area === "settings") && (
        <div className="flex-1 min-h-0">
          <Chat channels={wb.channels} author={author} lastEvent={wb.lastEvent} refetch={wb.refetch} projects={wb.projects} onOpenTask={openTask} />
        </div>
      )}

      {area === "work" && (
        <>
          <div className="flex-1 flex min-h-0">
            {!maximized && (
              <div className="w-64 shrink-0">
                <Sidebar {...wb} onAddProject={() => setShowProject(true)} selectedTaskId={selectedTask?.id} onSelectTask={(t) => { setSelectedTaskId(t.id); if (t.session_id) wb.setSessionId(t.session_id); }} />
              </div>
            )}
            {!maximized && (
              <main className="flex-1 min-w-0 flex flex-col">
                {wb.projectId ? (
                  <SessionView session={session} task={taskFull} messages={wb.messages} streams={wb.streams} runStatus={wb.runStatus}
                    profileData={profileData} author={author} setAuthor={wb.setAuthor}
                    onCancel={(runId) => api.cancelRun(runId)} onRetry={(id) => api.retryTask(id)} onOpenSettings={() => setShowSettings(true)} />
                ) : (
                  <div className="flex-1 flex items-center justify-center">
                    <div className="text-center space-y-3">
                      <Empty>还没有选择项目。</Empty>
                      <Button kind="primary" onClick={() => setShowProject(true)}>＋ 新增项目</Button>
                    </div>
                  </div>
                )}
              </main>
            )}
            <div onMouseDown={startDrag} className="w-1 cursor-col-resize bg-gray-800 hover:bg-indigo-600 shrink-0" />
            <aside style={{ width: maximized ? "100%" : rightWidth }} className="shrink-0 flex flex-col min-h-0 bg-[#0c0e12]">
              <div className="flex items-center border-b border-gray-800 px-2 shrink-0">
                {[["board", "看板"], ["preview", `预览${wb.artifacts.length ? ` ${wb.artifacts.length}` : ""}`], ["diff", "变更 / 审阅"]].map(([k, l]) => (
                  <button key={k} onClick={() => setTab(k)} className={`px-3 py-2 text-xs ${tab === k ? "text-white border-b-2 border-indigo-500" : "text-gray-500 hover:text-gray-300"}`}>{l}</button>
                ))}
                <div className="flex-1" />
                <Button kind="ghost" onClick={() => setMaximized(!maximized)}>{maximized ? "还原" : "放大"}</Button>
              </div>
              <div className="flex-1 overflow-auto p-3 min-h-0">
                {!wb.projectId && <Empty>先选择项目。</Empty>}
                {wb.projectId && tab === "board" && <Board tasks={wb.tasks} onOpen={openTask} selectedTaskId={selectedTask?.id} room={wb.room} />}
                {wb.projectId && tab === "preview" && <Preview artifacts={wb.artifacts} tasks={wb.tasks} selectedTaskId={selectedTaskId} onSelectTask={setSelectedTaskId} author={author || "human"} />}
                {wb.projectId && tab === "diff" && (
                  selectedTask ? <TaskActions task={selectedTask} diff={diff} refetch={wb.refetch} author={author || "human"} /> : <Empty>在看板里点一个任务查看它的变更。</Empty>
                )}
              </div>
            </aside>
          </div>
          {wb.projectId && <RoomBar room={wb.room} tasks={wb.tasks} author={author || "human"} />}
        </>
      )}

      {showSettings && <Settings projectId={wb.projectId} onClose={() => setShowSettings(false)} author={author} setAuthor={wb.setAuthor} cc={wb.cc} refetch={wb.refetch} />}
      {showProject && <ProjectDialog onClose={() => setShowProject(false)} onCreated={(p) => { setShowProject(false); wb.refetch("projects"); openProject(p.id); }} />}
    </div>
  );
}

function Identity({ author, setAuthor }) {
  const [editing, setEditing] = useState(!author);
  const [v, setV] = useState(author);
  if (editing) {
    return (
      <form onSubmit={(e) => { e.preventDefault(); if (v.trim()) { setAuthor(v.trim()); setEditing(false); } }} className="flex items-center gap-1">
        <input autoFocus className="bg-gray-900 border border-gray-700 rounded px-1.5 py-0.5 w-28 text-gray-200" placeholder="你的名字" value={v} onChange={(e) => setV(e.target.value)} />
        <Button kind="primary" type="submit" disabled={!v.trim()}>确定</Button>
      </form>
    );
  }
  return <button onClick={() => { setV(author); setEditing(true); }} className="px-2 py-1 rounded hover:bg-gray-800 text-gray-300" title="点击改名">👤 {author}</button>;
}

function TaskActions({ task, diff, refetch, author }) {
  const [msg, setMsg] = useState("");
  const [note, setNote] = useState("");
  async function run(fn) {
    setMsg("");
    try { const r = await fn(); setMsg(r?.error ? `失败：${r.error}` : "完成"); refetch("tasks"); } catch (e) { setMsg(`失败：${e.message}`); }
  }
  const stage = task.merge_status === "merged" ? "已合并到主分支" : task.review_status === "approved" ? "审阅通过，可合并" : task.review_status === "changes_requested" ? "已要求修改，等 worker 回应" : "等待审阅";
  return (
    <div className="space-y-2">
      <div className="text-sm text-gray-100">{task.title}</div>
      <div className="text-[11px] text-gray-500">执行 {task.status} · 审阅 {task.review_status} · 合并 {task.merge_status}{task.branch ? ` · 分支 ${task.branch}` : ""} — {stage}</div>
      <div className="flex gap-1 flex-wrap items-center">
        <input className="bg-gray-950 border border-gray-800 rounded px-2 py-1 text-xs text-gray-200 w-56" placeholder="审阅备注（要求修改时会送回 worker）" value={note} onChange={(e) => setNote(e.target.value)} />
        <Button onClick={() => run(() => api.reviewTask(task.id, "approve", note, author))}>审阅通过</Button>
        <Button onClick={() => run(() => api.reviewTask(task.id, "request_changes", note, author))}>要求修改</Button>
        <Button kind="primary" disabled={task.review_status !== "approved" || task.merge_status === "merged"} onClick={() => run(() => api.mergeTask(task.id))}>合并到主分支</Button>
      </div>
      {msg && <div className="text-[11px] text-gray-400">{msg}</div>}
      {diff === null ? <div className="text-xs text-gray-500">加载中…</div> : diff.available ? <DiffText diff={diff.diff} /> : <div className="text-xs text-amber-300">无法生成 diff：{diff.reason}</div>}
    </div>
  );
}

function safeGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function safeSet(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } }
