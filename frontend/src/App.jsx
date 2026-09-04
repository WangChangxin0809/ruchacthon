import { useEffect, useState } from "react";
import { fetchAgents, fetchEscalations, fetchPreviews, decide, connectWebSocket } from "./api";
import ChatPanel from "./ChatPanel";
import PreviewPanel from "./PreviewPanel";

const STATUS_COLUMNS = [
  { key: "working", label: "工作中" },
  { key: "needs_input", label: "需要输入" },
  { key: "in_review", label: "待审查" },
  { key: "ready_to_merge", label: "准备合并" },
];

const SCOPE_STYLE = {
  worktree: "bg-emerald-900 text-emerald-200 border-emerald-700",
  person: "bg-amber-900 text-amber-200 border-amber-700",
  team: "bg-rose-900 text-rose-200 border-rose-700",
};

function ScopeBadge({ scope }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded border ${SCOPE_STYLE[scope] || "bg-gray-800 text-gray-300 border-gray-700"}`}>
      {scope}
    </span>
  );
}

function AgentCard({ agent }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 mb-2 text-sm">
      <div className="font-mono text-gray-100">{agent.agent_id}</div>
      <div className="text-gray-400 text-xs mt-1">owner: {agent.owner_id} · worktree: {agent.worktree_id}</div>
      {agent.current_task && <div className="text-gray-300 text-xs mt-1">{agent.current_task}</div>}
    </div>
  );
}

function EscalationCard({ esc, onDecide }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  async function act(decision) {
    setBusy(true);
    try {
      await decide(esc.id, decision, reason, "human");
      onDecide(esc.id);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bg-gray-900 border border-rose-800 rounded-lg p-4 mb-3">
      <div className="flex items-center justify-between mb-2">
        <ScopeBadge scope={esc.scope} />
        <span className="text-xs text-gray-500">{esc.created_at}</span>
      </div>
      <div className="font-mono text-sm text-gray-100 mb-1">{esc.path}</div>
      <div className="text-sm text-gray-300 mb-1">{esc.description}</div>
      <div className="text-xs text-rose-300 mb-3">{esc.risk_summary}</div>
      <input
        className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm mb-2 text-gray-100"
        placeholder="裁决理由（可选）"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <div className="flex gap-2">
        <button
          disabled={busy}
          onClick={() => act("approve")}
          className="flex-1 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white text-sm rounded px-3 py-1.5"
        >
          通过
        </button>
        <button
          disabled={busy}
          onClick={() => act("reject")}
          className="flex-1 bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white text-sm rounded px-3 py-1.5"
        >
          退回
        </button>
      </div>
    </div>
  );
}

export default function App() {
  const [agents, setAgents] = useState([]);
  const [escalations, setEscalations] = useState([]);
  const [previews, setPreviews] = useState([]);
  const [connected, setConnected] = useState(false);

  async function refresh() {
    const [a, e, p] = await Promise.all([fetchAgents(), fetchEscalations(), fetchPreviews()]);
    setAgents(a);
    setEscalations(e);
    setPreviews(p);
  }

  useEffect(() => {
    refresh();
    const close = connectWebSocket((msg) => {
      setConnected(true);
      if (msg.type === "snapshot") {
        setAgents(msg.data.agents || []);
        setEscalations(msg.data.escalations || []);
      } else if (msg.type === "agent_status") {
        setAgents((prev) => {
          const rest = prev.filter((a) => a.agent_id !== msg.data.agent_id);
          return [...rest, msg.data];
        });
      } else if (msg.type === "escalation") {
        setEscalations((prev) => [...prev, msg.data]);
      } else if (msg.type === "decision") {
        setEscalations((prev) => prev.filter((e) => e.id !== msg.data.escalation_id));
      } else if (msg.type === "preview") {
        setPreviews((prev) => [...prev, msg.data]);
      }
    });
    return close;
  }, []);

  function removeEscalation(id) {
    setEscalations((prev) => prev.filter((e) => e.id !== id));
  }

  return (
    <div className="h-screen bg-[#0f1115] text-gray-100 p-6 flex flex-col overflow-hidden">
      <header className="flex items-center justify-between mb-4 shrink-0">
        <div>
          <h1 className="text-xl font-semibold">AgentRoom</h1>
          <p className="text-sm text-gray-400">Multi-Agent 协作中的人工参与与治理 · 赛道三 Demo</p>
        </div>
        <span className={`text-xs px-2 py-1 rounded border ${connected ? "border-emerald-700 text-emerald-300" : "border-gray-700 text-gray-500"}`}>
          {connected ? "● 已连接实时更新" : "○ 未连接（轮询中）"}
        </span>
      </header>

      <div className="flex-1 grid grid-cols-1 lg:grid-cols-5 gap-6 min-h-0">
        <section className="lg:col-span-3 min-h-0">
          <ChatPanel />
        </section>

        <section className="lg:col-span-2 overflow-y-auto pr-1 space-y-6">
          <div>
            <h2 className="text-sm font-medium text-gray-400 mb-3">Agent 状态</h2>
            <div className="grid grid-cols-2 gap-3">
              {STATUS_COLUMNS.map((col) => (
                <div key={col.key}>
                  <div className="text-xs text-gray-500 mb-2">{col.label}</div>
                  {agents.filter((a) => a.status === col.key).map((a) => (
                    <AgentCard key={a.agent_id} agent={a} />
                  ))}
                </div>
              ))}
            </div>
          </div>

          <div>
            <h2 className="text-sm font-medium text-gray-400 mb-3">
              待人工裁决队列 {escalations.length > 0 && <span className="text-rose-400">({escalations.length})</span>}
            </h2>
            {escalations.length === 0 && (
              <div className="text-sm text-gray-600 border border-dashed border-gray-800 rounded-lg p-6 text-center">
                暂无待裁决事项
              </div>
            )}
            {escalations.map((esc) => (
              <EscalationCard key={esc.id} esc={esc} onDecide={removeEscalation} />
            ))}
          </div>

          <div>
            <h2 className="text-sm font-medium text-gray-400 mb-3">成果预览</h2>
            <PreviewPanel previews={previews} />
          </div>
        </section>
      </div>
    </div>
  );
}
