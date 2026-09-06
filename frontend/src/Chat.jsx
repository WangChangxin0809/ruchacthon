import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import Composer from "./Composer";
import { Avatar, AvatarStack, Badge, Button, Dialog, EmptyState, Field, I, Popover, TEAM_ROLE_LABEL, fmtDay, fmtRelShort, fmtTime } from "./ui";

// Direct messages and group chats. One conversation model on the server, so
// the only difference here is the header and who may add members.
const REPLY_MODES = [
  ["auto", "自动", "一个人时 agent 有问必答；多个人时只回 @ 它的消息"],
  ["mention", "只回 @", "任何时候都只回 @ 它的消息"],
  ["always", "总是回", "每条消息都交给 agent"],
  ["never", "不回", "agent 只看，不说话"],
];

export default function Chat({ wb, convId, onConv, onOpenSession }) {
  const { conversations, me, teamId } = wb;
  // App owns which conversation is open, so a notification can point at one
  // and the address bar keeps it -- without reloading the page.
  const [activeId, setActiveId] = useState(convId || null);
  const pick = useCallback((id) => { setActiveId(id); onConv?.(id); }, [onConv]);
  const [messages, setMessages] = useState([]);
  const [detail, setDetail] = useState(null);
  const [newGroup, setNewGroup] = useState(false);
  const [newDm, setNewDm] = useState(false);
  const [loading, setLoading] = useState(false);

  const dms = conversations.filter((c) => c.kind === "dm");
  const groups = conversations.filter((c) => c.kind === "group");
  const active = conversations.find((c) => c.id === activeId) || null;

  useEffect(() => { if (convId && convId !== activeId) setActiveId(convId); }, [convId]);  // eslint-disable-line
  useEffect(() => {
    if (!activeId && conversations.length) pick(conversations[0].id);
  }, [conversations, activeId, pick]);

  const load = useCallback(async (id) => {
    if (!id) { setMessages([]); setDetail(null); return; }
    setLoading(true);
    try {
      const [msgs, d] = await Promise.all([api.convMessages(id), api.conversation(id)]);
      setMessages(msgs); setDetail(d);
      api.readConversation(id).then(() => wb.refetch("conversations")).catch(() => {});
    } finally { setLoading(false); }
  }, [wb]);

  useEffect(() => { load(activeId); }, [activeId, load]);

  useEffect(() => {
    const ev = wb.lastEvent;
    if (ev?.type === "message" && ev.payload?.conversation_id === activeId) {
      setMessages((ms) => (ms.some((m) => m.id === ev.payload.id) ? ms : [...ms, ev.payload]));
      if (ev.payload.user_id !== me?.id) api.readConversation(activeId).catch(() => {});
    }
    if (ev?.type === "conversation_member" && ev.payload?.conversation_id === activeId) load(activeId);
  }, [wb.lastEvent, activeId, me, load]);

  const send = async (text) => {
    const sent = await api.convSend(activeId, text);
    if (sent?.id) setMessages((ms) => (ms.some((m) => m.id === sent.id) ? ms : [...ms, sent]));
    wb.refetch("conversations");
  };

  return (
    <div className="flex-1 min-h-0 flex">
      <aside className="w-[264px] shrink-0 border-r border-[var(--border)] bg-white flex flex-col min-h-0">
        <div className="row h-12 px-3 border-b border-[var(--border)] shrink-0">
          <span className="font-semibold text-[14px]">消息</span>
          <div className="flex-1" />
          <Popover align="right" width={160} trigger={({ toggle }) => <button className="btn btn-ghost btn-sm btn-icon" onClick={toggle} title="新建"><I.plus /></button>}>
            {({ close }) => (
              <div className="p-1">
                <button className="menu-item" onClick={() => { close(); setNewDm(true); }}><I.user className="text-[var(--muted)]" />新私聊</button>
                <button className="menu-item" onClick={() => { close(); setNewGroup(true); }}><I.users className="text-[var(--muted)]" />新群聊</button>
              </div>
            )}
          </Popover>
        </div>
        <div className="flex-1 overflow-y-auto p-2 space-y-4">
          <Section title="私聊" items={dms} activeId={activeId} onPick={pick} me={me} empty="还没有私聊" />
          <Section title="群聊" items={groups} activeId={activeId} onPick={pick} me={me} empty="还没有群聊" />
        </div>
      </aside>

      <section className="flex-1 min-w-0 flex flex-col min-h-0">
        {!active && <EmptyState icon={<I.message className="w-5 h-5" />} title="选一个会话" action={<Button onClick={() => setNewGroup(true)}><I.plus />新群聊</Button>}>
          私聊只有你们两个人；群聊可以拉人，也可以把一个 agent 拉进来一起讨论。
        </EmptyState>}
        {active && (
          <>
            <Header conv={detail || active} me={me} onOpenSession={onOpenSession} onChanged={() => { load(activeId); wb.refetch("conversations"); }} teamId={teamId} />
            <Thread messages={messages} me={me} loading={loading} />
            <Composer
              onSend={send}
              people={(detail?.members || []).filter((m) => m.member_id !== me?.id)}
              placeholder={active.kind === "dm" ? `发给 ${title(active, me)}` : "发到群里，@ 可以叫人或 agent"}
            />
          </>
        )}
      </section>

      {newGroup && <NewGroupDialog teamId={teamId} me={me} onClose={() => setNewGroup(false)} onCreated={(c) => { setNewGroup(false); wb.refetch("conversations"); pick(c.id); }} />}
      {newDm && <NewDmDialog teamId={teamId} me={me} onClose={() => setNewDm(false)} onCreated={(c) => { setNewDm(false); wb.refetch("conversations"); pick(c.id); }} />}
    </div>
  );
}

function title(c, me) {
  if (c.title) return c.title;
  const other = (c.members || []).find((m) => m.member_id !== me?.id);
  return other?.name || other?.display_name || "私聊";
}

function Section({ title: label, items, activeId, onPick, me, empty }) {
  return (
    <div>
      <div className="label px-2 mb-1">{label} · {items.length}</div>
      {items.length === 0 && <div className="px-2 py-1.5 text-[12px] text-[var(--faint)]">{empty}</div>}
      {items.map((c) => {
        const name = title(c, me);
        const isAgent = c.kind === "session";
        return (
          <button key={c.id} onClick={() => onPick(c.id)}
            className={`nav-item h-auto py-1.5 items-start gap-2 ${activeId === c.id ? "nav-item-active" : ""}`}>
            {c.kind === "group"
              ? <span className="w-6 h-6 rounded-full bg-[var(--subtle)] text-[var(--muted)] flex items-center justify-center shrink-0"><I.users className="w-3.5 h-3.5" /></span>
              : <Avatar name={name} handle={c.peer_handle} agent={isAgent} size={24} />}
            <span className="flex-1 min-w-0">
              <span className="flex items-baseline gap-1.5">
                <span className="truncate font-medium">{name}</span>
                <span className="flex-1" />
                {c.last?.created_at && <span className="text-[11px] text-[var(--faint)] shrink-0">{fmtRelShort(c.last.created_at)}</span>}
              </span>
              <span className="block truncate text-[12px] text-[var(--muted)] leading-snug">{c.last ? `${c.last.author}: ${c.last.text}` : "还没有消息"}</span>
            </span>
            {c.unread > 0 && <span className="min-w-[18px] h-[18px] px-1 rounded-full bg-[var(--needs)] text-white text-[11px] font-medium flex items-center justify-center shrink-0">{c.unread > 99 ? "99+" : c.unread}</span>}
          </button>
        );
      })}
    </div>
  );
}

function Header({ conv, me, onChanged, onOpenSession, teamId }) {
  const [manage, setManage] = useState(false);
  const isOwner = conv.owner_id === me?.id || me?.is_admin;
  const agent = (conv.members || []).find((m) => m.member_kind === "agent");
  return (
    <div className="row h-12 px-4 border-b border-[var(--border)] bg-white shrink-0 gap-3">
      <span className="font-semibold text-[14px] truncate">{title(conv, me)}</span>
      {conv.kind === "group" && <Badge tone="outline">{(conv.members || []).length} 人</Badge>}
      {agent && <button className="chip hover:bg-[var(--subtle)]" onClick={() => onOpenSession?.(agent.member_id)}><I.bot className="w-3.5 h-3.5 text-[var(--agent)]" />{agent.name || "agent"}</button>}
      <div className="flex-1" />
      {conv.kind !== "dm" && (
        <>
          <AvatarStack people={(conv.members || []).map((m) => ({ id: m.member_id, name: m.name || m.display_name, handle: m.handle, agent: m.member_kind === "agent" }))} />
          <Button size="sm" kind="ghost" onClick={() => setManage(true)}><I.users />成员</Button>
        </>
      )}
      {manage && <MembersDialog conv={conv} me={me} isOwner={isOwner} teamId={teamId} onClose={() => setManage(false)} onChanged={onChanged} />}
    </div>
  );
}

function Thread({ messages, me, loading }) {
  const box = useRef(null);
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight }); }, [messages.length]);
  let lastDay = "";
  let lastAuthor = null;
  let lastAt = 0;
  return (
    <div ref={box} className="flex-1 min-h-0 overflow-y-auto px-4 py-4 flex flex-col">
      <div className="flex-1" />
      {loading && messages.length === 0 && <div className="text-center text-[12px] text-[var(--muted)] py-6">加载中…</div>}
      {!loading && messages.length === 0 && <div className="text-center text-[12px] text-[var(--muted)] py-6">还没有消息，说点什么。</div>}
      {messages.map((m) => {
        const mine = m.user_id && m.user_id === me?.id;
        const day = fmtDay(m.created_at);
        const showDay = day !== lastDay;
        lastDay = day;
        const at = new Date(m.created_at).getTime();
        const who = m.user_id || m.author;
        const grouped = who === lastAuthor && at - lastAt < 5 * 60 * 1000 && !showDay;
        lastAuthor = who; lastAt = at;
        // a chat row carries plain text; a session row carries Claude Code blocks
        const text = m.text ?? (m.blocks || []).filter((b) => b.type === "text").map((b) => b.text).join("");
        return (
          <div key={m.id}>
            {showDay && <div className="text-center my-4"><span className="text-[11px] text-[var(--faint)] bg-[var(--bg)] px-2 py-0.5 rounded-full">{day}</span></div>}
            <div className={`flex gap-2 ${mine ? "flex-row-reverse" : ""} ${grouped ? "mt-0.5" : "mt-3"}`}>
              <span className="w-7 shrink-0">{!grouped && <Avatar name={m.author || m.display_name} handle={m.handle} agent={m.role === "assistant" || !m.user_id} size={28} />}</span>
              <div className={`max-w-[min(560px,72%)] ${mine ? "items-end" : "items-start"} flex flex-col`}>
                {!grouped && <span className={`text-[11px] text-[var(--muted)] mb-0.5 px-1 ${mine ? "text-right" : ""}`}>{m.author || "?"} · {fmtTime(m.created_at)}</span>}
                <div className={`bubble ${mine ? "bubble-mine" : "bubble-theirs"}`}>{highlight(text)}</div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// @name reads as an address, so it should look like one.
export function highlight(text) {
  const parts = String(text || "").split(/(@[\w.一-龥-]+)/g);
  return parts.map((p, i) => (p.startsWith("@")
    ? <span key={i} className="text-[var(--link)] font-medium">{p}</span>
    : <span key={i}>{p}</span>));
}

function MembersDialog({ conv, me, isOwner, teamId, onClose, onChanged }) {
  const [candidates, setCandidates] = useState([]);
  const [busy, setBusy] = useState("");
  const members = conv.members || [];
  const has = new Set(members.map((m) => m.member_id));

  useEffect(() => { api.users({ team_id: teamId }).then((u) => setCandidates(u.filter((x) => !has.has(x.id)))).catch(() => {}); }, [teamId, conv.id]);  // eslint-disable-line

  const add = async (uid) => {
    setBusy(uid);
    try { await api.addConvMember(conv.id, { user_id: uid }); onChanged(); } finally { setBusy(""); }
  };
  const remove = async (uid) => {
    setBusy(uid);
    try { await api.removeConvMember(conv.id, uid); onChanged(); } finally { setBusy(""); }
  };
  const setReply = async (mode) => { await api.patchConversation(conv.id, { agent_reply: mode }); onChanged(); };

  return (
    <Dialog title={`「${conv.title || "会话"}」的成员`} onClose={onClose} width={440}>
      <div className="space-y-1">
        {members.map((m) => (
          <div key={m.member_id} className="row py-1.5">
            <Avatar name={m.name || m.display_name} handle={m.handle} agent={m.member_kind === "agent"} size={28} />
            <span className="flex-1 min-w-0">
              <span className="block truncate">{m.name || m.display_name}{m.member_id === me?.id && <span className="text-[var(--muted)]"> （你）</span>}</span>
              <span className="block text-[11px] text-[var(--muted)]">{m.member_kind === "agent" ? "agent" : `@${m.handle}`}{conv.owner_id === m.member_id ? " · 群主" : ""}</span>
            </span>
            {isOwner && m.member_kind === "user" && m.member_id !== conv.owner_id && (
              <Button size="xs" kind="danger" disabled={busy === m.member_id} onClick={() => remove(m.member_id)}>移出</Button>
            )}
          </div>
        ))}
      </div>

      {members.some((m) => m.member_kind === "agent") && (
        <div className="mt-5">
          <div className="label mb-1.5">agent 什么时候说话</div>
          <div className="space-y-1">
            {REPLY_MODES.map(([v, label, why]) => (
              <label key={v} className={`row items-start gap-2 rounded-md px-2 py-1.5 cursor-pointer ${conv.agent_reply === v ? "bg-[var(--subtle)]" : "hover:bg-[var(--subtle)]"}`}>
                <input type="radio" className="mt-1" checked={conv.agent_reply === v} onChange={() => setReply(v)} disabled={!isOwner} />
                <span><span className="font-medium">{label}</span><span className="block hint">{why}</span></span>
              </label>
            ))}
          </div>
        </div>
      )}

      {isOwner && (
        <div className="mt-5">
          <div className="label mb-1.5">加人</div>
          {candidates.length === 0 && <div className="hint">团队里的人都在群里了。</div>}
          <div className="max-h-[180px] overflow-y-auto">
            {candidates.map((u) => (
              <div key={u.id} className="row py-1">
                <Avatar name={u.display_name} handle={u.handle} size={24} />
                <span className="flex-1 truncate">{u.display_name} <span className="text-[var(--muted)]">@{u.handle}</span></span>
                <Button size="xs" disabled={busy === u.id} onClick={() => add(u.id)}><I.plus />加入</Button>
              </div>
            ))}
          </div>
        </div>
      )}
    </Dialog>
  );
}

function NewGroupDialog({ teamId, me, onClose, onCreated }) {
  const [name, setName] = useState("");
  const [picked, setPicked] = useState([]);
  const [users, setUsers] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { api.users({ team_id: teamId }).then((u) => setUsers(u.filter((x) => x.id !== me?.id))).catch(() => {}); }, [teamId, me]);

  const create = async () => {
    setBusy(true); setError("");
    try {
      onCreated(await api.createGroup({ title: name.trim(), team_id: teamId, member_ids: picked }));
    } catch (e) { setError(e.message); setBusy(false); }
  };
  return (
    <Dialog title="新群聊" onClose={onClose} width={420}
      footer={<><Button onClick={onClose}>取消</Button><Button kind="primary" disabled={!name.trim() || busy} onClick={create}>建群</Button></>}>
      <Field label="群名"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="前端小组" /></Field>
      <div className="label mt-4 mb-1.5">成员 · 已选 {picked.length}</div>
      {users.length === 0 && <div className="hint">团队里还没有别人，先在设置里邀请。</div>}
      <div className="max-h-[220px] overflow-y-auto -mx-1 px-1">
        {users.map((u) => (
          <label key={u.id} className="row py-1.5 px-1 rounded-md hover:bg-[var(--subtle)] cursor-pointer">
            <input type="checkbox" checked={picked.includes(u.id)}
              onChange={(e) => setPicked((p) => (e.target.checked ? [...p, u.id] : p.filter((x) => x !== u.id)))} />
            <Avatar name={u.display_name} handle={u.handle} size={24} />
            <span className="flex-1 truncate">{u.display_name} <span className="text-[var(--muted)]">@{u.handle}</span></span>
          </label>
        ))}
      </div>
      {error && <div className="mt-3 text-[12px] text-red-600">{error}</div>}
    </Dialog>
  );
}

function NewDmDialog({ teamId, me, onClose, onCreated }) {
  const [users, setUsers] = useState([]);
  const [qs, setQs] = useState("");
  const [busy, setBusy] = useState("");
  useEffect(() => { api.users({ team_id: teamId }).then((u) => setUsers(u.filter((x) => x.id !== me?.id))).catch(() => {}); }, [teamId, me]);
  const shown = useMemo(() => users.filter((u) => !qs || (u.display_name + u.handle).toLowerCase().includes(qs.toLowerCase())), [users, qs]);
  const open = async (uid) => { setBusy(uid); try { onCreated(await api.openDm(uid)); } finally { setBusy(""); } };
  return (
    <Dialog title="新私聊" onClose={onClose} width={380}>
      <input className="input mb-3" placeholder="搜名字或用户名" value={qs} onChange={(e) => setQs(e.target.value)} />
      {shown.length === 0 && <div className="hint">没有找到人。团队里的成员才会出现在这里。</div>}
      <div className="max-h-[300px] overflow-y-auto">
        {shown.map((u) => (
          <button key={u.id} className="row w-full py-2 px-1 rounded-md hover:bg-[var(--subtle)] text-left" disabled={busy === u.id} onClick={() => open(u.id)}>
            <Avatar name={u.display_name} handle={u.handle} size={28} />
            <span className="flex-1 min-w-0">
              <span className="block truncate">{u.display_name}</span>
              <span className="block text-[11px] text-[var(--muted)]">@{u.handle}</span>
            </span>
            {u.role && <Badge tone="outline">{TEAM_ROLE_LABEL[u.role] || u.role}</Badge>}
          </button>
        ))}
      </div>
    </Dialog>
  );
}
