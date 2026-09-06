import { useEffect, useState } from "react";
import { api } from "./api";
import { Badge, Button, Dialog, Field, I, ROLE_LABEL, Toggle } from "./ui";

// An agent definition is one row that becomes one set of Claude Code options:
// which prompt, which tools, which permission mode, how many turns. The four
// built-ins are read-only; 复制一份 gives you an editable copy.
const ALL_TOOLS = ["Read", "Grep", "Glob", "Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "mcp__room", "mcp__workbench"];
const TOOL_NOTE = { mcp__room: "Room：认领路径、广播、交接、请人裁决", mcp__workbench: "开子任务、派工作者（只有主控用得上）" };
const EFFORTS = ["", "low", "medium", "high", "xhigh", "max"];

export default function Agents({ wb, toast }) {
  const [data, setData] = useState({ items: [], defaults: {}, permission_modes: [], roles: [] });
  const [editing, setEditing] = useState(null);
  const teamId = wb.teamId;
  const mine = (wb.me?.teams || []).find((t) => t.id === teamId);
  const canDefault = !!teamId && (mine?.role === "owner" || mine?.role === "admin" || wb.me?.is_admin);

  const load = () => api.agents({ team_id: teamId }).then(setData).catch(() => {});
  useEffect(() => { load(); }, [teamId]);

  const builtin = data.items.filter((d) => d.trust === "system");
  const custom = data.items.filter((d) => d.trust !== "system");

  const save = async (body) => {
    if (editing.id) await api.patchAgent(editing.id, body);
    else await api.createAgent({ ...body, copy_from: editing.copy_from, team_id: teamId });
    setEditing(null); load(); wb.refetch("agents"); toast("已保存");
  };
  const setDefault = async (d) => {
    try { await api.setAgentDefault(d.id, teamId); load(); wb.refetch("agents"); toast(`${ROLE_LABEL[d.role]}默认改成「${d.name}」`); }
    catch (e) { toast(e.message, "red"); }
  };
  const remove = async (d) => {
    if (!window.confirm(`删除「${d.name}」？正在用它的会话会回到内置定义。`)) return;
    try { await api.deleteAgent(d.id); load(); wb.refetch("agents"); toast("已删除"); }
    catch (e) { toast(e.message, "red"); }
  };

  const Card = ({ d }) => (
    <div className={`card p-3 ${data.defaults?.[d.role] === d.id ? "border-[var(--accent)]" : ""}`}>
      <div className="row">
        <span className="w-6 h-6 rounded-md bg-[var(--subtle)] grid place-items-center text-[var(--agent)] shrink-0"><I.bot className="w-3.5 h-3.5" /></span>
        <span className="font-medium truncate">{d.name}</span>
        <Badge tone="outline">{ROLE_LABEL[d.role] || d.role}</Badge>
        <Badge tone={d.permission_mode === "plan" ? "blue" : d.permission_mode === "bypassPermissions" ? "red" : "neutral"}>{d.permission_label}</Badge>
        {d.can_spawn && <Badge tone="violet" title="可以创建子任务、派工作者">可派活</Badge>}
        {d.trust === "system" && <Badge tone="neutral">内置</Badge>}
        {data.defaults?.[d.role] === d.id && <Badge tone="accent">默认</Badge>}
        <div className="flex-1" />
        {canDefault && data.defaults?.[d.role] !== d.id && <Button size="xs" onClick={() => setDefault(d)}>设为默认</Button>}
        <Button size="xs" onClick={() => setEditing(d.editable ? d : { copy_from: d.id, ...d, id: null, name: `${d.name} 副本` })}>
          {d.editable ? <><I.pencil className="w-3.5 h-3.5" />编辑</> : <><I.copy className="w-3.5 h-3.5" />复制一份</>}
        </Button>
        {d.editable && <Button size="xs" kind="ghost" className="text-red-600" title="删除这个定义" onClick={() => remove(d)}><I.trash className="w-3.5 h-3.5" /></Button>}
      </div>
      <div className="text-[12px] text-[var(--muted)] mt-1.5 line-clamp-2">{d.description || "没有说明。"}</div>
      <div className="row mt-2 text-[11px] text-[var(--faint)] flex-wrap gap-x-3">
        <span>模型 {d.model || "跟随服务商默认"}</span>
        <span>最多 {d.max_turns} 轮</span>
        {d.max_budget_usd ? <span>预算 ${d.max_budget_usd}</span> : null}
        {d.effort ? <span>思考 {d.effort}</span> : null}
        {d.owner && <span>来自 @{d.owner.handle}</span>}
      </div>
    </div>
  );

  return (
    <div className="space-y-5">
      <p className="text-[12px] text-[var(--muted)]">
        一个定义就是一套 Claude Code 参数：用哪段系统提示、能用哪些工具、权限到哪、最多跑几轮。
        新建任务和会话时选一个；团队默认由管理员定。
      </p>

      <div>
        <div className="label mb-2">内置 · 不可修改</div>
        <div className="space-y-2">{builtin.map((d) => <Card key={d.id} d={d} />)}</div>
      </div>

      <div>
        <div className="row mb-2">
          <span className="label">{teamId ? "团队与我的" : "我的"} · {custom.length}</span>
          <div className="flex-1" />
          <Button size="sm" kind="primary" onClick={() => setEditing({ copy_from: "worker", ...builtin.find((b) => b.id === "worker"), id: null, name: "" })}>
            <I.plus />新建定义
          </Button>
        </div>
        {custom.length === 0
          ? <div className="text-[12px] text-[var(--faint)] border border-dashed border-[var(--border)] rounded-lg py-5 text-center">
              还没有自定义定义。从内置的「复制一份」开始最省事。
            </div>
          : <div className="space-y-2">{custom.map((d) => <Card key={d.id} d={d} />)}</div>}
      </div>

      {editing && <EditAgent d={editing} modes={data.permission_modes} onSave={save} onCancel={() => setEditing(null)} />}
    </div>
  );
}

function EditAgent({ d, modes, onSave, onCancel }) {
  const [f, setF] = useState({
    name: d.name || "", description: d.description || "", role: d.role || "worker",
    system_prompt: d.system_prompt || "", model: d.model || "", permission_mode: d.permission_mode || "acceptEdits",
    max_turns: d.max_turns || 60, max_budget_usd: d.max_budget_usd || "", can_spawn: !!d.can_spawn, effort: d.effort || "",
    allowed_tools: d.allowed_tools || [], disallowed_tools: d.disallowed_tools || [],
  });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setF((p) => ({ ...p, [k]: v }));
  const toggleTool = (t) => set("allowed_tools", f.allowed_tools.includes(t) ? f.allowed_tools.filter((x) => x !== t) : [...f.allowed_tools, t]);

  const submit = async () => {
    setErr(""); setBusy(true);
    try {
      await onSave({
        ...f, model: f.model || null, effort: f.effort || null,
        max_turns: Number(f.max_turns), max_budget_usd: f.max_budget_usd === "" ? null : Number(f.max_budget_usd),
      });
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  return (
    <Dialog title={d.id ? `编辑「${d.name}」` : "新建 agent 定义"} onClose={onCancel} width={640}
      footer={<><Button onClick={onCancel}>取消</Button><Button kind="primary" disabled={!f.name.trim() || busy} onClick={submit}>保存</Button></>}>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="名称"><input className="input" value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="例如 严格审查者" autoFocus /></Field>
          <Field label="用在哪" hint="主控只能用在主会话，工作者只能用在任务上。">
            <select className="input" value={f.role} onChange={(e) => set("role", e.target.value)}>
              {Object.entries(ROLE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </Field>
        </div>
        <Field label="说明" hint="选它的时候别人看到的一句话"><input className="input" value={f.description} onChange={(e) => set("description", e.target.value)} /></Field>

        <Field label="追加的系统提示" hint="接在内置提示后面，不会替换它。留空就用内置的。">
          <textarea className="input min-h-[76px] resize-y" value={f.system_prompt} onChange={(e) => set("system_prompt", e.target.value)}
            placeholder="例如：改动前先写测试；不要碰 migrations/ 目录。" />
        </Field>

        <Field label="可用工具" hint="没勾的工具在运行里直接不存在。Read / Grep / Glob 这类只读工具总是可用。">
          <div className="flex flex-wrap gap-1">
            {ALL_TOOLS.map((t) => (
              <button key={t} onClick={() => toggleTool(t)} title={TOOL_NOTE[t]}
                className={`chip ${f.allowed_tools.includes(t) ? "bg-[var(--accent)] text-white border-transparent" : "hover:bg-[var(--subtle)]"}`}>{t}</button>
            ))}
          </div>
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="权限" hint="仅可查看＝一行都不改。">
            <select className="input" value={f.permission_mode} onChange={(e) => set("permission_mode", e.target.value)}>
              {(modes.length ? modes : ["plan", "acceptEdits", "bypassPermissions"]).map((m) => (
                <option key={m} value={m}>{{ plan: "仅可查看", acceptEdits: "工作区内修改", bypassPermissions: "完全权限", default: "默认" }[m] || m}</option>
              ))}
            </select>
          </Field>
          <Field label="模型" hint="留空跟随服务商的默认模型"><input className="input" value={f.model} onChange={(e) => set("model", e.target.value)} placeholder="claude-sonnet-4-6" /></Field>
          <Field label="最多轮数"><input className="input" type="number" min="1" value={f.max_turns} onChange={(e) => set("max_turns", e.target.value)} /></Field>
          <Field label="单次预算（美元）" hint="留空不限"><input className="input" type="number" step="0.5" min="0" value={f.max_budget_usd} onChange={(e) => set("max_budget_usd", e.target.value)} placeholder="不限" /></Field>
          <Field label="思考强度" hint="留空用服务器默认">
            <select className="input" value={f.effort} onChange={(e) => set("effort", e.target.value)}>
              {EFFORTS.map((v) => <option key={v} value={v}>{v || "默认"}</option>)}
            </select>
          </Field>
          <Field label="可以派活" hint="打开后它能建子任务、派工作者。一般只有主控需要。">
            <div className="row h-8"><Toggle on={f.can_spawn} onChange={(v) => set("can_spawn", v)} /><span className="text-[12px] text-[var(--muted)]">{f.can_spawn ? "可以" : "不可以"}</span></div>
          </Field>
        </div>
        {err && <div className="text-[12px] text-red-600">{err}</div>}
      </div>
    </Dialog>
  );
}
