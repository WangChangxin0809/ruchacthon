import { useEffect, useState } from "react";
import { api } from "./api";
import { Badge, Button, Details, Empty, I, Text, fmtTime } from "./ui";

// What the agent pointed the pane at (the `preview` tool, AO's `ao preview`).
// The revision is why this is not just a URL: preview called again on the same
// file means "look at it now", so the iframe has to be remounted.
export function AgentPreview({ session, onCleared }) {
  const pv = session?.preview || null;
  const rev = session?.preview_revision || 0;
  const [text, setText] = useState(null);
  const src = pv ? (pv.kind === "url" ? pv.url : api.previewFileUrl(session.id, pv.path)) : null;
  const kind = pv?.file_kind || (pv?.kind === "url" ? "url" : "file");

  useEffect(() => {
    setText(null);
    if (!pv || !["markdown", "text"].includes(kind)) return;
    let live = true;
    fetch(src).then((r) => r.text()).then((t) => live && setText(t)).catch(() => live && setText("（读不到这个文件）"));
    return () => { live = false; };
  }, [src, kind, rev]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!pv) {
    return (
      <div className="text-[12px] text-[var(--faint)] border border-dashed border-[var(--border)] rounded-lg py-6 px-3 text-center">
        agent 还没有选要给你看的东西。它做出网页、文档、图片或跑起一个服务时会自己调 preview 打开这里。
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <div className="row text-[11px] text-[var(--muted)]">
        <I.globe className="w-3.5 h-3.5" />
        <span className="mono truncate flex-1" title={pv.url || pv.path}>{pv.title || pv.url || pv.path}</span>
        <a className="text-[var(--working)] hover:underline" href={src} target="_blank" rel="noreferrer">新标签打开</a>
        <button className="text-[var(--faint)] hover:text-[var(--text)]" title="关掉预览" onClick={async () => { await api.clearPreview(session.id); onCleared?.(); }}>关掉</button>
      </div>
      {(kind === "html" || kind === "url" || kind === "pdf") && (
        <iframe key={`${src}#${rev}`} title={pv.title || "预览"} src={src}
          sandbox={kind === "url" ? "allow-scripts allow-forms allow-same-origin" : "allow-scripts allow-forms"}
          className="w-full h-[420px] bg-white rounded border border-[var(--border)]" />
      )}
      {kind === "image" && <img alt={pv.title || pv.path} src={`${src}#${rev}`} className="max-h-[420px] rounded border border-[var(--border)]" />}
      {kind === "markdown" && (
        <div className="bg-white rounded border border-[var(--border)] p-3 max-h-[420px] overflow-auto">
          {text === null ? <span className="text-[12px] text-[var(--muted)]">加载中…</span> : <Text text={text} />}
        </div>
      )}
      {kind === "text" && (
        <pre className="whitespace-pre-wrap text-[12px] bg-[var(--subtle)] rounded p-3 max-h-[420px] overflow-auto">{text ?? "加载中…"}</pre>
      )}
      {kind === "file" && <a className="text-[13px] text-[var(--working)] underline" href={src} target="_blank" rel="noreferrer">下载 {pv.path}</a>}
    </div>
  );
}

export default function Preview({ artifacts, tasks, selectedTaskId, onSelectTask }) {
  const list = selectedTaskId ? artifacts.filter((a) => a.task_id === selectedTaskId) : artifacts;
  const [openId, setOpenId] = useState(null);
  const taskTitle = (id) => tasks.find((t) => t.id === id)?.title || "主 agent";
  if (list.length === 0) return <Empty>{selectedTaskId ? "这个任务还没有提交成果。" : "agent 提交的成果会出现在这里（Markdown / 图片 / HTML / 文件 / diff / dev server）。"}</Empty>;
  return (
    <div className="space-y-2">
      {selectedTaskId && <button className="text-[11px] text-[var(--muted)] hover:text-[var(--text)]" onClick={() => onSelectTask(null)}>← 查看全部成果</button>}
      {list.map((a) => {
        const open = openId === a.id;
        return (
          <div key={a.id} className={`card overflow-hidden ${a.status === "current" ? "" : "opacity-60"}`}>
            <button onClick={() => setOpenId(open ? null : a.id)} className="w-full text-left px-3 py-2 flex items-center gap-2 hover:bg-[var(--subtle)]">
              <span className="text-[11px] text-[var(--muted)] w-14 shrink-0">{a.kind}</span>
              <div className="flex-1 min-w-0">
                <div className="text-[13px] truncate">{a.title} <span className="text-[var(--muted)]">v{a.version}</span></div>
                <div className="text-[11px] text-[var(--muted)] truncate">{taskTitle(a.task_id)} · {fmtTime(a.created_at)}</div>
              </div>
              {a.status !== "current" ? <Badge status="cancelled">已被新版本替代</Badge>
                : a.kind === "devserver" ? <Badge status={a.available ? "running" : "failed"}>{a.available ? "可用" : "不可用"}</Badge>
                : <Badge status="in_review">最新</Badge>}
            </button>
            {open && <ArtifactBody a={a} />}
          </div>
        );
      })}
    </div>
  );
}

function ArtifactBody({ a }) {
  const [text, setText] = useState("");
  const [verdict, setVerdict] = useState("comment");
  const [msg, setMsg] = useState("");
  const [logs, setLogs] = useState(null);
  async function send() {
    setMsg("");
    try {
      const r = await api.feedback(a.id, text, verdict);
      setMsg(`已送达 worker（${r.delivery.how === "live" ? "追加到正在运行的会话" : "以新一次运行恢复其会话"}）`);
      setText("");
    } catch (e) { setMsg(`失败：${e.message}`); }
  }
  return (
    <div className="px-3 pb-3 space-y-2">
      {a.kind === "markdown" && <pre className="whitespace-pre-wrap text-[13px] bg-[var(--subtle)] rounded p-3 max-h-96 overflow-auto">{a.content}</pre>}
      {a.kind === "diff" && <DiffText diff={a.content} />}
      {a.kind === "html" && <iframe title={a.title} srcDoc={a.content} sandbox="" className="w-full h-80 bg-white rounded border border-[var(--border)]" />}
      {a.kind === "image" && <img alt={a.title} src={api.artifactFileUrl(a.id)} className="max-h-96 rounded border border-[var(--border)]" />}
      {a.kind === "file" && <a className="text-[13px] text-[var(--working)] underline" href={api.artifactFileUrl(a.id)} target="_blank" rel="noreferrer">下载 {a.meta?.source_path}</a>}
      {a.kind === "devserver" && (
        <div className="space-y-2">
          <div className="text-[12px] text-[var(--muted)]">
            命令 <code className="text-[var(--text)]">{a.meta?.command}</code> · 端口 {a.devserver?.port} · 状态 {a.devserver?.status} / {a.devserver?.health || "-"}
          </div>
          {a.available ? (
            <>
              <a className="text-[13px] text-[var(--working)] underline" href={a.url} target="_blank" rel="noreferrer">在新窗口打开 {a.url}</a>
              <iframe title={a.title} src={a.url} sandbox="allow-scripts allow-forms allow-same-origin" className="w-full h-96 bg-white rounded border border-[var(--border)]" />
            </>
          ) : <div className="text-[12px] text-red-600">服务已退出或成果已过期，不再显示为可用。</div>}
          <div className="flex gap-2">
            {a.devserver && a.devserver.status !== "stopped" && a.devserver.status !== "exited" && <Button kind="danger" onClick={() => api.stopDevserver(a.devserver.id)}>停止</Button>}
            {a.devserver && <Button onClick={async () => setLogs((await api.devserverLogs(a.devserver.id)).log)}>查看日志</Button>}
          </div>
          {logs !== null && <pre className="text-[11px] text-[var(--muted)] bg-[var(--subtle)] rounded p-2 max-h-48 overflow-auto">{logs || "(空)"}</pre>}
        </div>
      )}
      <Details summary={`详情：artifact ${a.id} · run ${a.run_id} · workspace ${a.workspace_id}`}>
        <pre className="text-[11px] text-[var(--muted)]">{JSON.stringify(a.meta, null, 1)}</pre>
      </Details>
      {a.feedback?.length > 0 && (
        <div className="space-y-1">
          {a.feedback.map((f) => (
            <div key={f.id} className="text-[12px] bg-[var(--subtle)] rounded px-2 py-1">
              <span className="text-amber-600">{f.author}</span> · {f.verdict} · v{f.version} · {f.delivery}: {f.text}
            </div>
          ))}
        </div>
      )}
      {a.status === "current" && (
        <div className="flex gap-1 items-start">
          <select className="input py-1 w-auto text-[12px]" value={verdict} onChange={(e) => setVerdict(e.target.value)}>
            <option value="comment">评论</option>
            <option value="request_changes">要求修改</option>
            <option value="approve">审阅通过</option>
          </select>
          <input className="input flex-1 py-1 text-[12px]" placeholder="反馈会送回对应 worker 的会话" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
          <Button kind="primary" onClick={send} disabled={!text.trim()}>发送</Button>
        </div>
      )}
      {msg && <div className="text-[11px] text-[var(--muted)]">{msg}</div>}
    </div>
  );
}

export function DiffText({ diff }) {
  if (!diff) return <div className="text-[12px] text-[var(--muted)]">（无改动）</div>;
  return (
    <pre className="text-[11px] bg-[var(--subtle)] rounded p-2 max-h-[70vh] overflow-auto">
      {diff.split("\n").map((l, i) => (
        <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "text-green-700 bg-green-50" : l.startsWith("-") && !l.startsWith("---") ? "text-red-700 bg-red-50" : l.startsWith("@@") ? "text-sky-700" : "text-[var(--muted)]"}>{l || " "}</div>
      ))}
    </pre>
  );
}
