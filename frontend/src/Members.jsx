import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Row } from "./Settings";
import { Avatar, Badge, Button, EmptyState, I, TEAM_ROLE_LABEL, copyText, fmtRel } from "./ui";

// The team tab: who is in, what they may do, and the invite links that let
// somebody else register at all (registration is closed without one).
export default function Members({ wb, toast }) {
  const teamId = wb.teamId;
  const [team, setTeam] = useState(null);
  const [invites, setInvites] = useState([]);
  const [busy, setBusy] = useState("");
  const mine = (wb.me?.teams || []).find((t) => t.id === teamId);
  const canManage = mine?.role === "owner" || mine?.role === "admin" || wb.me?.is_admin;

  const load = useCallback(async () => {
    if (!teamId) return;
    const [t, inv] = await Promise.all([api.team(teamId), canManage ? api.invites(teamId).catch(() => []) : []]);
    setTeam(t); setInvites(inv);
  }, [teamId, canManage]);
  useEffect(() => { load().catch(() => {}); }, [load]);

  if (!teamId) {
    return <EmptyState icon={<I.users className="w-5 h-5" />} title="你还没有团队">在左上角的团队菜单里创建一个，然后邀请别人。</EmptyState>;
  }
  if (!team) return <div className="text-[var(--muted)] text-[12px]">加载中…</div>;

  const act = async (key, fn, ok) => {
    setBusy(key);
    try { await fn(); await load(); await wb.refetch("me"); toast(ok); }
    catch (e) { toast(e.message, "red"); }
    finally { setBusy(""); }
  };
  // a revoked link is not a link any more, so it leaves the list; expired or
  // used-up ones stay, greyed, until somebody clears them
  const live = invites.filter((iv) => !iv.revoked);
  const copy = async (url) => {
    const ok = await copyText(url);
    toast(ok ? "已复制" : "复制不了，点一下链接再 Ctrl+C", ok ? undefined : "red");
  };
  const newInvite = async (role) => {
    setBusy("invite");
    try {
      const r = await api.createInvite(teamId, role);
      await load();
      if (await copyText(r.url)) toast("邀请链接已复制"); else toast("已生成邀请链接，点一下它再 Ctrl+C");
    } catch (e) { toast(e.message, "red"); } finally { setBusy(""); }
  };

  return (
    <div className="space-y-6">
      <div>
        <Row title="团队名" desc={`${team.members?.length || 0} 人 · 你是${TEAM_ROLE_LABEL[mine?.role] || "成员"}`}>
          <TeamName team={team} canManage={canManage} onSaved={async (name) => act("name", () => api.patchTeam(teamId, { name }), "已改名")} busy={busy === "name"} />
        </Row>
      </div>

      <div>
        <div className="label mb-2">成员</div>
        <div className="space-y-0.5">
          {(team.members || []).map((m) => (
            <div key={m.user_id} className="row py-2 px-2 rounded-md hover:bg-[var(--subtle)]">
              <Avatar name={m.display_name} handle={m.handle} size={30} />
              <div className="flex-1 min-w-0">
                <div className="truncate">{m.display_name}{m.user_id === wb.me?.id && <span className="text-[var(--muted)]"> （你）</span>}</div>
                <div className="text-[11px] text-[var(--muted)]">@{m.handle} · 加入于 {fmtRel(m.joined_at)}</div>
              </div>
              {canManage && m.user_id !== wb.me?.id ? (
                <select className="input w-24 h-7 text-[12px]" value={m.role} disabled={busy === m.user_id}
                  onChange={(e) => act(m.user_id, () => api.setTeamRole(teamId, m.user_id, e.target.value), "身份已改")}>
                  {Object.entries(TEAM_ROLE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              ) : <Badge tone={m.role === "owner" ? "accent" : "outline"}>{TEAM_ROLE_LABEL[m.role] || m.role}</Badge>}
              {canManage && m.user_id !== wb.me?.id && m.role !== "owner" && (
                <Button size="xs" kind="danger" disabled={busy === m.user_id}
                  onClick={() => act(m.user_id, () => api.removeTeamMember(teamId, m.user_id), "已移出团队")}>移出</Button>
              )}
            </div>
          ))}
        </div>
      </div>

      {canManage && (
        <div>
          <div className="row mb-2">
            <span className="label">邀请链接</span>
            <div className="flex-1" />
            <Button size="sm" disabled={busy === "invite"} onClick={() => newInvite("member")}><I.plus />新建成员邀请</Button>
            <Button size="sm" disabled={busy === "invite"} onClick={() => newInvite("admin")}>管理员邀请</Button>
          </div>
          <div className="hint mb-2">没有邀请链接的人无法注册。链接可以重复使用，直到你撤销它。对方也可以把整条链接粘到注册页的「邀请码」里。</div>
          {live.length === 0 && <div className="text-[12px] text-[var(--faint)] border border-dashed border-[var(--border)] rounded-lg py-4 text-center">还没有邀请链接。</div>}
          {live.map((iv) => (
            <div key={iv.id} className={`row py-2 border-b border-[var(--border)] last:border-b-0 ${iv.valid ? "" : "opacity-60"}`}>
              <I.link className="text-[var(--muted)]" />
              <div className="flex-1 min-w-0">
                <input className="mono text-[12px] w-full bg-transparent truncate focus:outline-none" readOnly value={iv.url}
                  onFocus={(e) => e.target.select()} onClick={(e) => e.target.select()} />
                <div className="text-[11px] text-[var(--muted)]">
                  {TEAM_ROLE_LABEL[iv.role] || iv.role} · 建于 {fmtRel(iv.created_at)}{iv.uses ? ` · 已用 ${iv.uses} 次` : " · 还没人用过"}
                </div>
              </div>
              {!iv.valid && <Badge tone="neutral">{iv.reason || "不能用了"}</Badge>}
              {iv.valid && <Button size="xs" onClick={() => copy(iv.url)}><I.copy />复制</Button>}
              <Button size="xs" kind="danger" disabled={busy === iv.id}
                onClick={() => act(iv.id, () => api.revokeInvite(teamId, iv.id), "已撤销")}>{iv.valid ? "撤销" : "删掉"}</Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TeamName({ team, canManage, onSaved, busy }) {
  const [v, setV] = useState(team.name);
  useEffect(() => setV(team.name), [team.name]);
  if (!canManage) return <span>{team.name}</span>;
  return (
    <>
      <input className="input w-52" value={v} onChange={(e) => setV(e.target.value)} />
      <Button size="sm" kind="primary" disabled={busy || !v.trim() || v === team.name} onClick={() => onSaved(v.trim())}>保存</Button>
    </>
  );
}
