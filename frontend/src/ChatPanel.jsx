import { useEffect, useRef, useState } from "react";
import { fetchChatHistory, fetchHealth, sendChatMessage } from "./api";

// Turns the raw OpenAI/Anthropic-shape message log into a flat list of
// {kind: "text"|"tool", ...} items the UI can render in order. Rebuilt from
// `messages` on every update (not an ephemeral "last turn's events" list)
// so tool calls from earlier turns don't disappear once a new message sends.
function toDisplayItems(messages) {
  const items = [];
  const resultByCallId = new Map();
  for (const m of messages) {
    if (m.role === "tool" && m.tool_call_id) {
      resultByCallId.set(m.tool_call_id, safeParse(m.content));
    }
    if (m.role === "user" && Array.isArray(m.content)) {
      for (const block of m.content) {
        if (block.type === "tool_result") {
          resultByCallId.set(block.tool_use_id, safeParse(block.content));
        }
      }
    }
  }
  for (const m of messages) {
    if (m.role === "user") {
      const text = typeof m.content === "string" ? m.content : null;
      if (text) items.push({ kind: "text", role: "user", text });
      continue;
    }
    if (m.role !== "assistant") continue;

    if (typeof m.content === "string" && m.content) {
      items.push({ kind: "text", role: "assistant", text: m.content });
    } else if (Array.isArray(m.content)) {
      for (const block of m.content) {
        if (block.type === "text" && block.text) {
          items.push({ kind: "text", role: "assistant", text: block.text });
        } else if (block.type === "tool_use") {
          items.push({
            kind: "tool", name: block.name, arguments: block.input,
            result: resultByCallId.get(block.id),
          });
        }
      }
    }
    for (const call of m.tool_calls || []) {
      items.push({
        kind: "tool", name: call.function.name,
        arguments: safeParse(call.function.arguments),
        result: resultByCallId.get(call.id),
      });
    }
  }
  return items;
}

function safeParse(s) {
  if (typeof s !== "string") return s;
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
}

function ToolCallLine({ item }) {
  const failed = item.result?.ok === false;
  return (
    <div className={`text-xs font-mono px-2 py-1.5 rounded mb-1 ${failed ? "bg-rose-950 text-rose-300" : "bg-gray-800 text-gray-400"}`}>
      → {item.name}({JSON.stringify(item.arguments)})
      {item.result?.conflict && <span className="text-rose-400"> ⚠ conflict</span>}
      {item.result === undefined && <span className="text-gray-600"> …</span>}
    </div>
  );
}

export default function ChatPanel() {
  const [messages, setMessages] = useState([]); // raw history from the backend
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
  }, [messages]);

  async function send() {
    if (!input.trim() || busy) return;
    const text = input;
    setInput("");
    setBusy(true);
    setError("");
    // Optimistic: show the user's message immediately, replaced by the
    // authoritative history (with the assistant's reply) once it lands.
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    try {
      const res = await sendChatMessage(text);
      if (!res.ok) {
        setError(res.error);
        setMessages((prev) => prev.slice(0, -1)); // the send never actually landed
      } else {
        setMessages(await fetchChatHistory());
      }
    } catch (e) {
      setError(String(e));
      setMessages((prev) => prev.slice(0, -1));
    } finally {
      setBusy(false);
    }
  }

  const items = toDisplayItems(messages);

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
        {items.length === 0 && (
          <div className="text-sm text-gray-600 text-center mt-8">
            跟主 Agent 说点什么，它能自己改文件，也能 spawn_subagent 派后台子代理去做。
          </div>
        )}
        {items.map((item, i) =>
          item.kind === "tool" ? (
            <ToolCallLine key={i} item={item} />
          ) : (
            <div key={i} className={`flex ${item.role === "user" ? "justify-end" : "justify-start"}`}>
              <div
                className={`max-w-[80%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                  item.role === "user" ? "bg-indigo-900 text-indigo-100" : "bg-gray-800 text-gray-100"
                }`}
              >
                {item.text}
              </div>
            </div>
          ),
        )}
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
