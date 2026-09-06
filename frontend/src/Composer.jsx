import { useEffect, useMemo, useRef, useState } from "react";
import { Avatar, I, Spinner } from "./ui";

// One composer for chat threads and work sessions. Enter sends, Shift+Enter
// makes a line, and typing "@" opens a picker over the people and agents of
// this conversation -- the @ rule on the server decides who is actually
// woken, so getting the handle right matters.
export default function Composer({ onSend, people = [], placeholder = "说点什么", disabled, hint, extra, autoFocus }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pick, setPick] = useState(null);   // {query, start} while an @ is being typed
  const [cursor, setCursor] = useState(0);
  const ref = useRef(null);

  useEffect(() => { if (autoFocus) ref.current?.focus(); }, [autoFocus]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [text]);

  const matches = useMemo(() => {
    if (!pick) return [];
    const q = pick.query.toLowerCase();
    return people
      .map((p) => ({ id: p.member_id || p.id, name: p.display_name || p.name || "", handle: p.handle || p.agent_name || p.display_name || "", agent: p.member_kind === "agent" || p.agent }))
      .filter((p) => !q || (p.name + p.handle).toLowerCase().includes(q))
      .slice(0, 6);
  }, [pick, people]);

  useEffect(() => { setCursor(0); }, [pick?.query]);   // eslint-disable-line react-hooks/exhaustive-deps

  const onChange = (e) => {
    const v = e.target.value;
    setText(v);
    const upto = v.slice(0, e.target.selectionStart);
    const m = /(?:^|\s)@([^\s@]*)$/.exec(upto);
    setPick(m ? { query: m[1], start: upto.length - m[1].length - 1 } : null);
  };

  const insert = (p) => {
    const handle = p.agent ? p.handle : p.handle;
    const before = text.slice(0, pick.start);
    const after = text.slice(pick.start + 1 + pick.query.length);
    const next = `${before}@${handle} ${after.replace(/^\s+/, "")}`;
    setText(next);
    setPick(null);
    requestAnimationFrame(() => {
      const at = before.length + handle.length + 2;
      ref.current?.focus();
      ref.current?.setSelectionRange(at, at);
    });
  };

  const submit = async () => {
    const t = text.trim();
    if (!t || busy || disabled) return;
    setBusy(true); setError("");
    try {
      await onSend(t);
      setText("");
    } catch (e) {
      setError(e.message || "发送失败");
    } finally { setBusy(false); }
  };

  const onKeyDown = (e) => {
    if (pick && matches.length) {
      if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => (c + 1) % matches.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => (c - 1 + matches.length) % matches.length); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); insert(matches[cursor]); return; }
      if (e.key === "Escape") { setPick(null); return; }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
  };

  return (
    <div className="border-t border-[var(--border)] bg-white px-4 py-3 shrink-0">
      {extra}
      <div className="relative">
        {pick && matches.length > 0 && (
          <div className="popover bottom-full mb-2 left-0 w-[260px] p-1">
            {matches.map((p, i) => (
              <button key={p.id || p.handle} onMouseDown={(e) => { e.preventDefault(); insert(p); }} onMouseEnter={() => setCursor(i)}
                className={`menu-item ${i === cursor ? "bg-[var(--subtle)]" : ""}`}>
                <Avatar name={p.name} handle={p.handle} agent={p.agent} size={20} />
                <span className="flex-1 truncate">{p.name}</span>
                <span className="text-[11px] text-[var(--faint)]">@{p.handle}</span>
              </button>
            ))}
          </div>
        )}
        <div className="flex items-end gap-2 rounded-lg border border-[var(--border)] bg-white px-2.5 py-2 focus-within:border-[var(--border-strong)] focus-within:ring-2 focus-within:ring-[var(--ring)] transition-[border-color,box-shadow] duration-150">
          <textarea ref={ref} rows={1} value={text} disabled={disabled} placeholder={placeholder}
            className="flex-1 resize-none bg-transparent outline-none text-[13px] leading-relaxed placeholder:text-[var(--faint)] max-h-[180px]"
            onChange={onChange} onKeyDown={onKeyDown} />
          <button className="btn btn-primary btn-sm btn-icon shrink-0" onClick={submit} disabled={!text.trim() || busy || disabled} title="发送 (Enter)">
            {busy ? <Spinner /> : <I.send className="w-4 h-4" />}
          </button>
        </div>
      </div>
      <div className="row mt-1.5 gap-2 min-h-[16px]">
        {error ? <span className="text-[12px] text-red-600">{error}</span> : <span className="hint truncate">{hint}</span>}
        <div className="flex-1" />
        <span className="text-[11px] text-[var(--faint)] shrink-0"><span className="kbd">Enter</span> 发送 · <span className="kbd">Shift+Enter</span> 换行</span>
      </div>
    </div>
  );
}
