import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import Board from "./Board";
import Preview, { DiffText } from "./Preview";
import RoomBar from "./RoomBar";
import SessionView from "./SessionView";
import Settings from "./Settings";
import Sidebar from "./Sidebar";
import { useWorkbench } from "./store";
import { Button, Empty } from "./ui";

export default function App() {
  const wb = useWorkbench();
  const [tab, setTab] = useState("board");
  const [rightWidth, setRightWidth] = useState(() => Number(safeGet("wb.right")) || 460);
  const [maximized, setMaximized] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState(null);
  const [session, setSession] = useState(null);
  const [diff, setDiff] = useState(null);
  const [profiles, setProfiles] = useState([]);
  const [showSettings, setShowSettings] = useState(false);
  const author = (() => { try { return localStorage.getItem("wb.author") || "human"; } catch { return "human"; } })();

  useEffect(() => { api.profiles().then((d) => setProfiles(d.profiles)).catch(() => {}); }, [showSettings]);
  useEffect(() => {
    if (!wb.sessionId) { setSession(null); return; }
    api.session(wb.sessionId).then(setSession).catch(() => setSession(null));
  }, [wb.sessionId, wb.tasks]);

  const task = wb.tasks.find((t) => t.session_id === wb.sessionId) || null;
  const taskFull = task ? { ...task, workspace: session?.workspace } : null;

  useEffect(() => {
    if (tab !== "diff" || !selectedTaskId) return;
    api.taskDiff(selectedTaskId).then(setDiff).catch((e) => setDiff({ available: false, reason: e.message }));
  }, [tab, selectedTaskId, wb.artifacts]);

  const openTask = useCallback((t) => {
    setSelectedTaskId(t.id);
    if (t.session_id) wb.setSessionId(t.session_id);
  }, [wb]);

  const selectedTask = wb.tasks.find((t) => t.id === selectedTaskId) || task;

  function startDrag(e) {
    const startX = e.clientX, start = rightWidth;
    const move = (ev) => setRightWidth(Math.max(320, Math.min(window.innerWidth - 500, start + (startX - ev.clientX))));
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); safeSet("wb.right", String(rightWidth)); };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  }

  return (
    <div className="h-screen bg-[#0f1115] text-gray-100 flex flex-col overflow-hidden">
      <header className="h-9 px-4 flex items-center justify-between border-b border-gray-800 shrink-0 text-xs">
        <div className="text-gray-300">CC Workbench <span className="text-gray-600">· Claude Code 多 agent 工作台</span></div>
        <div className="flex items-center gap-3">
          {wb.health && !wb.health.claude_binary && <span className="text-rose-300">未找到 claude 可执行文件</span>}
          <span className={wb.wsStatus === "connected" ? "text-emerald-400" : "text-amber-300"}>
            {wb.wsStatus === "connected" ? "● 实时" : wb.wsStatus === "reconnecting" ? "○ 断线，重连中（不会丢事件）" : "○ 连接中"}
          </span>
        </div>
      </header>

      <div className="flex-1 flex min-h-0">
        {!maximized && (
          <div className="w-64 shrink-0">
            <Sidebar {...wb} onOpenSettings={() => setShowSettings(true)} />
          </div>
        )}
        {!maximized && (
          <main className="flex-1 min-w-0 flex flex-col">
            {wb.projectId ? (
              <SessionView session={session} task={taskFull} messages={wb.messages} streams={wb.streams} runStatus={wb.runStatus} profiles={profiles}
                onCancel={(runId) => api.cancelRun(runId)} onRetry={(id) => api.retryTask(id)} />
            ) : (
              <div className="flex-1 flex items-center justify-center"><Empty>在左侧添加或选择一个项目开始。</Empty></div>
            )}
          </main>
        )}
        <div onMouseDown={startDrag} className="w-1 cursor-col-resize bg-gray-800 hover:bg-indigo-600 shrink-0" />
        <aside style={{ width: maximized ? "100%" : rightWidth }} className="shrink-0 flex flex-col min-h-0 bg-[#0c0e12]">
          <div className="flex items-center border-b border-gray-800 px-2 shrink-0">
            {[["board", "看板"], ["preview", "预览"], ["diff", "Diff"]].map(([k, l]) => (
              <button key={k} onClick={() => setTab(k)} className={`px-3 py-2 text-xs ${tab === k ? "text-white border-b-2 border-indigo-500" : "text-gray-500 hover:text-gray-300"}`}>{l}</button>
            ))}
            <div className="flex-1" />
            <Button kind="ghost" onClick={() => setMaximized(!maximized)}>{maximized ? "还原" : "放大"}</Button>
          </div>
          <div className="flex-1 overflow-auto p-3 min-h-0">
            {!wb.projectId && <Empty>先选择项目。</Empty>}
            {wb.projectId && tab === "board" && <Board tasks={wb.tasks} onOpen={openTask} selectedTaskId={selectedTask?.id} />}
            {wb.projectId && tab === "preview" && <Preview artifacts={wb.artifacts} tasks={wb.tasks} selectedTaskId={selectedTaskId} onSelectTask={setSelectedTaskId} author={author} />}
            {wb.projectId && tab === "diff" && (
              selectedTask ? <TaskActions task={selectedTask} diff={diff} refetch={wb.refetch} author={author} /> : <Empty>在看板里点一个任务查看它的 Diff。</Empty>
            )}
          </div>
        </aside>
      </div>
      {wb.projectId && <RoomBar room={wb.room} tasks={wb.tasks} author={author} />}
      {showSettings && <Settings projectId={wb.projectId} onClose={() => setShowSettings(false)} />}
    </div>
  );
}

function TaskActions({ task, diff, refetch, author }) {
  const [msg, setMsg] = useState("");
  const [note, setNote] = useState("");
  async function run(fn) {
    setMsg("");
    try { const r = await fn(); setMsg(r?.error ? `失败：${r.error}` : "完成"); refetch("tasks"); } catch (e) { setMsg(`失败：${e.message}`); }
  }
  return (
    <div className="space-y-2">
      <div className="text-sm text-gray-100">{task.title}</div>
      <div className="text-[11px] text-gray-500">执行 {task.status} · 审阅 {task.review_status} · 合并 {task.merge_status}</div>
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
