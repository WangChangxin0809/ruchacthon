import { useState } from "react";
import { api } from "./api";
import { Badge, Button, Details, Empty, fmtTime } from "./ui";

export default function Preview({ artifacts, tasks, selectedTaskId, onSelectTask, author }) {
  const list = selectedTaskId ? artifacts.filter((a) => a.task_id === selectedTaskId) : artifacts;
  const [openId, setOpenId] = useState(null);
  const taskTitle = (id) => tasks.find((t) => t.id === id)?.title || "主 agent";
  if (list.length === 0) return <Empty>{selectedTaskId ? "这个任务还没有提交成果。" : "agent 提交的成果会出现在这里（Markdown / 图片 / HTML / 文件 / diff / dev server）。"}</Empty>;
  return (
    <div className="space-y-2">
      {selectedTaskId && <button className="text-[11px] text-gray-500 hover:text-gray-300" onClick={() => onSelectTask(null)}>← 查看全部成果</button>}
      {list.map((a) => {
        const open = openId === a.id;
        return (
          <div key={a.id} className={`bg-gray-900 border rounded-lg overflow-hidden ${a.status === "current" ? "border-gray-800" : "border-gray-900 opacity-60"}`}>
            <button onClick={() => setOpenId(open ? null : a.id)} className="w-full text-left px-3 py-2 flex items-center gap-2 hover:bg-gray-800">
              <span className="text-[11px] text-gray-500 w-14 shrink-0">{a.kind}</span>
              <div className="flex-1 min-w-0">
                <div className="text-sm text-gray-100 truncate">{a.title} <span className="text-gray-500">v{a.version}</span></div>
                <div className="text-[11px] text-gray-500 truncate">{taskTitle(a.task_id)} · {fmtTime(a.created_at)}</div>
              </div>
              {a.status !== "current" ? <Badge status="cancelled">已被新版本替代</Badge>
                : a.kind === "devserver" ? <Badge status={a.available ? "running" : "failed"}>{a.available ? "可用" : "不可用"}</Badge>
                : <Badge status="in_review">最新</Badge>}
            </button>
            {open && <ArtifactBody a={a} author={author} />}
          </div>
        );
      })}
    </div>
  );
}

function ArtifactBody({ a, author }) {
  const [text, setText] = useState("");
  const [verdict, setVerdict] = useState("comment");
  const [msg, setMsg] = useState("");
  const [logs, setLogs] = useState(null);
  async function send() {
    setMsg("");
    try {
      const r = await api.feedback(a.id, text, verdict, author);
      setMsg(`已送达 worker（${r.delivery.how === "live" ? "追加到正在运行的会话" : "以新一次运行恢复其会话"}）`);
      setText("");
    } catch (e) { setMsg(`失败：${e.message}`); }
  }
  return (
    <div className="px-3 pb-3 space-y-2">
      {a.kind === "markdown" && <pre className="whitespace-pre-wrap text-sm text-gray-200 bg-gray-950 rounded p-3 max-h-96 overflow-auto">{a.content}</pre>}
      {a.kind === "diff" && <DiffText diff={a.content} />}
      {a.kind === "html" && <iframe title={a.title} srcDoc={a.content} sandbox="" className="w-full h-80 bg-white rounded border border-gray-700" />}
      {a.kind === "image" && <img alt={a.title} src={api.artifactFileUrl(a.id)} className="max-h-96 rounded border border-gray-800" />}
      {a.kind === "file" && <a className="text-sm text-indigo-300 underline" href={api.artifactFileUrl(a.id)} target="_blank" rel="noreferrer">下载 {a.meta?.source_path}</a>}
      {a.kind === "devserver" && (
        <div className="space-y-2">
          <div className="text-xs text-gray-400">
            命令 <code className="text-gray-300">{a.meta?.command}</code> · 端口 {a.devserver?.port} · 状态 {a.devserver?.status} / {a.devserver?.health || "-"}
          </div>
          {a.available ? (
            <>
              <a className="text-sm text-indigo-300 underline" href={a.url} target="_blank" rel="noreferrer">在新窗口打开 {a.url}</a>
              <iframe title={a.title} src={a.url} sandbox="allow-scripts allow-forms allow-same-origin" className="w-full h-96 bg-white rounded border border-gray-700" />
            </>
          ) : <div className="text-xs text-rose-300">服务已退出或成果已过期，不再显示为可用。</div>}
          <div className="flex gap-2">
            {a.devserver && a.devserver.status !== "stopped" && a.devserver.status !== "exited" && <Button kind="danger" onClick={() => api.stopDevserver(a.devserver.id)}>停止</Button>}
            {a.devserver && <Button onClick={async () => setLogs((await api.devserverLogs(a.devserver.id)).log)}>查看日志</Button>}
          </div>
          {logs !== null && <pre className="text-[11px] text-gray-400 bg-gray-950 rounded p-2 max-h-48 overflow-auto">{logs || "(空)"}</pre>}
        </div>
      )}
      <Details summary={`详情：artifact ${a.id} · run ${a.run_id} · workspace ${a.workspace_id}`}>
        <pre className="text-[11px] text-gray-500">{JSON.stringify(a.meta, null, 1)}</pre>
      </Details>
      {a.feedback?.length > 0 && (
        <div className="space-y-1">
          {a.feedback.map((f) => (
            <div key={f.id} className="text-xs text-gray-300 bg-gray-950 rounded px-2 py-1">
              <span className="text-amber-300">{f.author}</span> · {f.verdict} · v{f.version} · {f.delivery}: {f.text}
            </div>
          ))}
        </div>
      )}
      {a.status === "current" && (
        <div className="flex gap-1 items-start">
          <select className="bg-gray-950 border border-gray-800 rounded px-1 py-1 text-xs text-gray-300" value={verdict} onChange={(e) => setVerdict(e.target.value)}>
            <option value="comment">评论</option>
            <option value="request_changes">要求修改</option>
            <option value="approve">审阅通过</option>
          </select>
          <input className="flex-1 bg-gray-950 border border-gray-800 rounded px-2 py-1 text-xs text-gray-200" placeholder="反馈会送回对应 worker 的会话" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
          <Button kind="primary" onClick={send} disabled={!text.trim()}>发送</Button>
        </div>
      )}
      {msg && <div className="text-[11px] text-gray-400">{msg}</div>}
    </div>
  );
}

export function DiffText({ diff }) {
  if (!diff) return <div className="text-xs text-gray-500">（无改动）</div>;
  return (
    <pre className="text-[11px] font-mono bg-gray-950 rounded p-2 max-h-[70vh] overflow-auto">
      {diff.split("\n").map((l, i) => (
        <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "text-emerald-300" : l.startsWith("-") && !l.startsWith("---") ? "text-rose-300" : l.startsWith("@@") ? "text-sky-400" : "text-gray-400"}>{l || " "}</div>
      ))}
    </pre>
  );
}
