import { useEffect, useRef, useState } from "react";
import { fetchChatHistory, fetchHealth, sendChatMessage } from "./api";

function renderContent(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (block.type === "text") return block.text;
        if (block.type === "tool_use") return `[calling ${block.name}]`;
        if (block.type === "tool_result") return `[tool result]`;
        return "";
      })
      .join("\n");
  }
  return "";
}

function ToolCallLine({ event }) {
  const ok = event.result?.ok !== false;
  return (
    <div className={`text-xs font-mono px-2 py-1 rounded mb-1 ${ok ? "bg-gray-800 text-gray-400" : "bg-rose-950 text-rose-300"}`}>
      → {event.name}({JSON.stringify(event.arguments)}) {event.result?.conflict ? "⚠ conflict" : ""}
    </div>
  );
}

export default function ChatPanel() {
  const [messages, setMessages] = useState([]); // {role, content} from history
  const [pendingEvents, setPendingEvents] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [llmConfigured, setLlmConfigured] = useState(true);
  const [error, setError] = useState("");
  const bottomRef = useRef(null);

  useEffect(() => {
    fetchChatHistory().then(setMessages);
    fetchHealth().then((h) => setLlmConfigured(h.llm_configured));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingEvents]);

  async function send() {
    if (!input.trim() || busy) return;
    const text = input;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setBusy(true);
    setError("");
    setPendingEvents([]);
    try {
      const res = await sendChatMessage(text);
      if (!res.ok) {
        setError(res.error);
      } else {
        setPendingEvents(res.events);
        const history = await fetchChatHistory();
        setMessages(history);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col h-full bg-gray-950 border border-gray-800 rounded-lg">
      <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
        <h2 className="text-sm font-medium text-gray-300">对话</h2>
        {!llmConfigured && (
          <span className="text-xs text-amber-400 border border-amber-800 rounded px-2 py-0.5">
            未配置 LLM_API_KEY — 对话/子代理暂不可用
          </span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <div className="text-sm text-gray-600 text-center mt-8">
            跟主 Agent 说点什么，它能自己改文件，也能 spawn_subagent 派后台子代理去做。
          </div>
        )}
        {messages
          .filter((m) => m.role === "user" || m.role === "assistant")
          .map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div
                className={`max-w-[80%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                  m.role === "user" ? "bg-indigo-900 text-indigo-100" : "bg-gray-800 text-gray-100"
                }`}
              >
                {renderContent(m.content)}
              </div>
            </div>
          ))}
        {pendingEvents.filter((e) => e.type === "tool_call").map((e, i) => (
          <ToolCallLine key={i} event={e} />
        ))}
        {error && (
          <div className="text-xs text-rose-300 bg-rose-950 border border-rose-800 rounded px-3 py-2">
            {error}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="p-3 border-t border-gray-800 flex gap-2">
        <input
          className="flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100"
          placeholder="给主 Agent 发消息…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          disabled={busy}
        />
        <button
          onClick={send}
          disabled={busy || !input.trim()}
          className="bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50 text-white text-sm rounded px-4 py-2"
        >
          发送
        </button>
      </div>
    </div>
  );
}
