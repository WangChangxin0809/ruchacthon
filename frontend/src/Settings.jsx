import { useEffect, useState } from "react";
import { api } from "./api";
import { Details, I } from "./ui";

// AO's settings modal (left nav, rows with the control on the right) holding
// dsh's model configuration (provider cards, write-only keys).
export default function Settings({ projectId, onClose, author, setAuthor, cc, refetch }) {
  const [tab, setTab] = useState("models");
  useEffect(() => { const k = (e) => e.key === "Escape" && onClose(); window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [onClose]);
  const NAV = [["general", "通用", <I.gear className="w-4 h-4" />], ["models", "模型", <I.brain className="w-4 h-4" />], ["cc", "Claude Code", <I.terminal className="w-4 h-4" />], ["keys", "快捷键", <I.compass className="w-4 h-4" />]];
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white border border-[var(--border)] rounded-xl w-[880px] max-w-[95vw] h-[80vh] flex text-[13px] overflow-hidden shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="w-48 border-r border-[var(--border)] bg-[var(--bg)] p-3 space-y-0.5">
          <div className="font-semibold text-[14px] px-2 py-1.5 mb-2">设置</div>
          {NAV.map(([k, l, ic]) => (
            <button key={k} onClick={() => setTab(k)} className={`row w-full text-left px-2 py-1.5 rounded-md ${tab === k ? "bg-[var(--hover)] font-medium" : "text-[var(--muted)] hover:bg-[var(--subtle)]"}`}>{ic}{l}</button>
          ))}
        </div>
        <div className="flex-1 overflow-auto">
          <div className="row h-12 px-6 border-b border-[var(--border)] sticky top-0 bg-white"><span className="font-semibold text-[14px]">{NAV.find((n) => n[0] === tab)?.[1]}</span><div className="flex-1" /><button className="btn btn-ghost btn-sm" onClick={onClose}><I.x className="w-4 h-4" /></button></div>
          <div className="p-6">
            {tab === "general" && <General author={author} setAuthor={setAuthor} />}
            {tab === "models" && <Models />}
            {tab === "cc" && <ClaudeCode projectId={projectId} cc={cc} refetch={refetch} />}
            {tab === "keys" && <Keys />}
          </div>
        </div>
      </div>
    </div>
  );
}

function Row({ title, desc, children }) {
  return (
    <div className="row justify-between gap-6 py-3 border-b border-[var(--border)] last:border-b-0">
      <div className="min-w-0"><div>{title}</div>{desc && <div className="text-[12px] text-[var(--muted)]">{desc}</div>}</div>
      <div className="shrink-0 row">{children}</div>
    </div>
  );
}

function General({ author, setAuthor }) {
  const [s, setS] = useState(null);
  useEffect(() => { api.settings().then(setS).catch(() => {}); }, []);
  return (
    <div>
      <Row title="你的名字" desc="显示在消息和反馈上；同一浏览器记住"><input className="input w-48" value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="例如 nic" /></Row>
      {s && (
        <>
          <Row title="并发运行上限" desc="服务器环境 WORKBENCH_MAX_CONCURRENT_RUNS"><span className="mono">{s.max_concurrent_runs}</span></Row>
          <Row title="项目目录"><span className="mono text-[12px]">{s.projects_dir}</span></Row>
          <Row title="数据目录"><span className="mono text-[12px]">{s.data_dir}</span></Row>
          <Row title="访问令牌" desc="服务器 WORKBENCH_TOKEN"><span className="text-[12px]">{s.token_required ? "已启用" : "未启用（仅本机模式）"}</span></Row>
        </>
      )}
    </div>
  );
}

function Keys() {
  const K = ({ k }) => <kbd className="mono text-[11px] border border-[var(--border)] rounded px-1.5 py-0.5 bg-[var(--subtle)]">{k}</kbd>;
  return (
    <div>
      <Row title="搜索会话"><K k="Ctrl+K" /></Row>
      <Row title="发送消息"><K k="Enter" /></Row>
      <Row title="换行"><K k="Shift+Enter" /></Row>
      <Row title="新任务对话框里提交"><K k="Ctrl+Enter" /></Row>
      <Row title="关闭对话框"><K k="Esc" /></Row>
    </div>
  );
}

function Models() {
  const [data, setData] = useState({ profiles: [], kinds: {}, default_models: [] });
  const [adding, setAdding] = useState(null); // "builtin" | "custom"
  const load = () => api.profiles().then(setData).catch(() => {});
  useEffect(() => { load(); }, []);
  const builtin = Object.entries(data.kinds).filter(([, v]) => v.builtin);
  async function setDefault(profile_id, model) { await api.putSettings({ default_profile_id: profile_id || "", default_model: model || "" }); load(); }
  return (
    <div className="space-y-4">
      <p className="text-[12px] text-[var(--muted)]">填入各提供方的密钥即可使用其模型。密钥只写不读：保存后页面只知道「已设置」。按运行注入，不改服务器上 Claude Code 的全局配置。</p>
      {data.profiles.map((p) => <ProviderCard key={p.id} p={p} data={data} onChange={load} onDefault={setDefault} />)}
      {data.profiles.length === 0 && <div className="text-[12px] text-[var(--muted)] border border-dashed border-[var(--border)] rounded-lg p-4">还没有提供方。没有提供方时，运行使用服务器上 Claude Code 自己的登录（见「Claude Code」页）。</div>}
      <div className="grid grid-cols-2 gap-3">
        <button onClick={() => setAdding("builtin")} className="border border-dashed border-[var(--border)] rounded-lg py-3 hover:border-gray-400 hover:bg-[var(--subtle)]">＋ 添加提供方</button>
        <button onClick={() => setAdding("custom")} className="border border-dashed border-[var(--border)] rounded-lg py-3 hover:border-gray-400 hover:bg-[var(--subtle)]">＋ 添加自定义提供方</button>
      </div>
      {adding && <AddProvider mode={adding} kinds={adding === "builtin" ? builtin : Object.entries(data.kinds).filter(([, v]) => !v.builtin)} defaultModels={data.default_models} onDone={() => { setAdding(null); load(); }} onCancel={() => setAdding(null)} />}
    </div>
  );
}

function ProviderCard({ p, data, onChange, onDefault }) {
  const [secret, setSecret] = useState("");
  const [models, setModels] = useState(p.models.join(", "));
  const [baseUrl, setBaseUrl] = useState(p.base_url || "");
  const [display, setDisplay] = useState(p.display_name || p.name);
  const [msg, setMsg] = useState("");
  const [checking, setChecking] = useState(false);
  const isDefault = data.default_profile_id === p.id;
  const dirty = secret || models !== p.models.join(", ") || baseUrl !== (p.base_url || "") || display !== (p.display_name || p.name);
  async function save() {
    setMsg("");
    try {
      const list = models.split(",").map((s) => s.trim()).filter(Boolean);
      await api.patchProfile(p.id, { secret: secret || null, models: list, model: list[0] || null, base_url: baseUrl || null, display_name: display });
      setSecret(""); setMsg("已保存"); onChange();
    } catch (e) { setMsg(`失败：${e.message}`); }
  }
  async function check() {
    setChecking(true); setMsg("");
    try { const r = await api.checkProfile(p.id); setMsg(r.ok ? `兼容性通过（${r.model}）` : `兼容性失败：${r.error || "未知"}`); onChange(); } finally { setChecking(false); }
  }
  return (
    <div className={`card p-4 ${isDefault ? "border-gray-900" : ""}`}>
      <div className="row">
        <div className="font-medium">{p.display_name || p.name}</div>
        <div className="text-[11px] text-[var(--muted)]">{p.name} · {p.kind_label}</div>
        <div className="flex-1" />
        {isDefault ? <span className="text-[11px] font-medium">默认</span> : <button className="btn btn-ghost btn-sm" onClick={() => onDefault(p.id, p.model)}>设为默认</button>}
        <button className="btn btn-ghost btn-sm text-red-600" onClick={() => { if (window.confirm(`删除提供方 ${p.name}？其密钥一并删除。`)) api.deleteProfile(p.id).then(onChange); }}><I.trash className="w-3.5 h-3.5" /></button>
      </div>
      {p.compat?.checked_at && <div className={`text-[11px] mt-1 ${p.compat.ok ? "text-green-700" : "text-red-600"}`}>上次检查 {p.compat.ok ? "通过" : "失败"}：流式 {p.compat.stream_text ? "✓" : "✗"} · 工具 {p.compat.tool_call ? "✓" : "✗"}{p.compat.error ? ` · ${p.compat.error}` : ""}</div>}
      <div className="grid grid-cols-2 gap-x-4">
        <div><div className="label mt-3 mb-1">显示名称</div><input className="input" value={display} onChange={(e) => setDisplay(e.target.value)} /></div>
        {(p.base_url || p.kind === "anthropic_compatible_gateway" || p.kind === "foundry") && <div><div className="label mt-3 mb-1">API 地址</div><input className="input" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /></div>}
        {p.accepts_secret && (
          <div>
            <div className="label mt-3 mb-1">API 密钥 / 令牌 {p.credential_set ? <span className="text-green-700">· 已设置{p.credential_source === "env" ? "（来自服务器环境变量）" : ""}</span> : <span className="text-amber-600">· 未设置</span>}</div>
            <div className="row"><input type="password" className="input" placeholder={p.credential_set ? "输入新密钥以替换" : "输入密钥"} value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" />
              {p.credential_set && p.credential_source === "store" && <button className="btn btn-sm" onClick={() => api.deleteProfileSecret(p.id).then(onChange)}>清除</button>}</div>
          </div>
        )}
        <div><div className="label mt-3 mb-1">模型列表（逗号分隔；第一个是默认）</div><input className="input" value={models} onChange={(e) => setModels(e.target.value)} placeholder={data.default_models.join(", ")} /></div>
      </div>
      <div className="row mt-3">
        <button className="btn btn-sm" onClick={check} disabled={checking}>{checking ? "检查中…" : "兼容性检查"}</button>
        <div className="flex-1 text-[11px] text-[var(--muted)]">{msg}</div>
        <button className="btn btn-primary btn-sm" onClick={save} disabled={!dirty}>保存</button>
      </div>
    </div>
  );
}

function AddProvider({ mode, kinds, defaultModels, onDone, onCancel }) {
  const [kind, setKind] = useState(kinds[0]?.[0] || "");
  const [name, setName] = useState("");
  const [display, setDisplay] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [models, setModels] = useState(defaultModels.join(", "));
  const [err, setErr] = useState("");
  const k = kinds.find(([id]) => id === kind)?.[1];
  useEffect(() => { if (!name && kind) setName(kind.replace(/_/g, "-")); }, [kind]);
  async function create() {
    setErr("");
    try {
      await api.createProfile({ name, kind, display_name: display || null, base_url: baseUrl || null, secret: secret || null, models: models.split(",").map((s) => s.trim()).filter(Boolean) });
      onDone();
    } catch (e) { setErr(e.message); }
  }
  const L = ({ children }) => <div className="label mt-3 mb-1">{children}</div>;
  return (
    <div className="card p-4">
      <div className="font-medium">{mode === "builtin" ? "添加提供方" : "添加自定义提供方"}</div>
      {mode === "builtin" && <><L>提供方</L><select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>{kinds.map(([id, v]) => <option key={id} value={id}>{v.label}</option>)}</select></>}
      <L>Provider ID <span className="text-[var(--faint)] normal-case tracking-normal">小写标识，用于凭据引用，创建后不可改</span></L>
      <input className="input" value={name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder="acme-gateway" />
      <L>显示名称</L><input className="input" value={display} onChange={(e) => setDisplay(e.target.value)} />
      {k?.needs_base_url && (
        <><L>API 地址</L><input className="input" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://gateway.example" />
          {mode === "custom" && <div className="text-[11px] text-[var(--muted)] mt-1">协议：Anthropic Messages（Claude Code 只支持这一种网关协议；OpenAI 风格接口无法接入）</div>}</>
      )}
      {k?.credential && <><L>API 密钥 / 令牌（只写）</L><input type="password" className="input" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" /></>}
      <L>模型（逗号分隔；第一个是默认）</L><input className="input" value={models} onChange={(e) => setModels(e.target.value)} />
      <div className="text-[11px] text-[var(--muted)] mt-2">{k?.note}</div>
      {err && <div className="text-[12px] text-red-600 mt-2">{err}</div>}
      <div className="row justify-end mt-3"><button className="btn btn-sm" onClick={onCancel}>取消</button><button className="btn btn-primary btn-sm" onClick={create} disabled={!name || !kind}>保存</button></div>
    </div>
  );
}

function ClaudeCode({ projectId, cc, refetch }) {
  const [disc, setDisc] = useState(null);
  const [tok, setTok] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => { api.discovery(projectId).then(setDisc).catch(() => setDisc({ error: true })); }, [projectId]);
  const eff = cc?.effective;
  async function save() {
    setMsg("");
    try { const r = await api.ccLoginToken(tok); setTok(""); setMsg(r.status.logged_in ? "令牌有效，已保存" : `已保存，但 claude 说未登录：${r.status.error || r.status.method}`); refetch("cc"); } catch (e) { setMsg(`失败：${e.message}`); }
  }
  return (
    <div className="space-y-5">
      <div>
        <Row title="登录状态">{!cc ? "…" : eff?.logged_in ? <span className="text-green-700">已登录（{eff.method}{eff.email ? ` · ${eff.email}` : ""}{cc.saved_token?.logged_in ? " · 页面保存的令牌" : " · 服务器上 claude 自己的登录"}）</span> : <span className="text-red-600">未登录</span>}</Row>
        <Row title="长期令牌" desc={<>本机执行 <code>claude setup-token</code>，把令牌粘贴到这里。只写，保存在服务器 0600 文件里，按运行注入 CLAUDE_CODE_OAUTH_TOKEN。</>}>
          <input type="password" className="input w-64" placeholder="sk-ant-oat01-…" value={tok} onChange={(e) => setTok(e.target.value)} autoComplete="off" />
          <button className="btn btn-primary btn-sm" onClick={save} disabled={tok.length < 20}>保存</button>
          {cc?.saved_token_set && <button className="btn btn-sm" onClick={() => api.ccLoginTokenDelete().then(() => refetch("cc"))}>清除</button>}
        </Row>
        {msg && <div className="text-[12px] text-[var(--muted)] py-2">{msg}</div>}
        <div className="text-[12px] text-[var(--muted)] py-2">另一种方式：在服务器上以运行服务的用户执行一次 <code>claude</code> 交互登录。</div>
      </div>
      <div className="label">发现结果</div>
      {!disc && <div className="text-[var(--muted)] text-[12px]">加载中…</div>}
      {disc && !disc.error && (
        <div className="text-[12px] space-y-1">
          <div>二进制 <code>{disc.claude_binary || "未找到"}</code> · 版本 {disc.claude_version || "-"} · 平台 {disc.platform}</div>
          <div>用户 settings：env 变量 {disc.user.settings?.env_names?.join(", ") || "无"}；hooks {disc.user.settings?.hook_events?.join(", ") || "无"}；插件 {disc.user.settings?.plugins?.join(", ") || "无"}</div>
          <div>用户 skills：{disc.user.skills.join(", ") || "无"} · CLAUDE.md {disc.user.claude_md ? "有" : "无"}</div>
          {disc.project && <div>项目：CLAUDE.md {disc.project.claude_md.join(", ") || "无"} · .mcp.json {disc.project.mcp_servers.join(", ") || "无"} · skills {disc.project.skills.join(", ") || "无"}</div>}
          <Details summary="每类配置在运行中的支持情况（已验证 / 未验证）">
            <table className="text-[11px] w-full mt-1"><tbody>
              {Object.entries(disc.support).map(([k, v]) => <tr key={k} className="border-t border-[var(--border)]"><td className="py-1 pr-2 align-top">{k}</td><td className="py-1 pr-2 text-[var(--muted)] align-top">{v.how}</td><td className="py-1 text-[var(--faint)] align-top">{v.verified}</td></tr>)}
            </tbody></table>
          </Details>
        </div>
      )}
    </div>
  );
}
