import { useEffect, useState } from "react";
import { api } from "./api";
import { Button, Details } from "./ui";

const input = "w-full bg-gray-950 border border-gray-800 rounded px-2 py-1.5 text-sm text-gray-200";
const label = "text-[11px] text-gray-400 mt-2 mb-1";

export default function Settings({ projectId, onClose, author, setAuthor, cc, refetch }) {
  const [tab, setTab] = useState("models");
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-[#111318] border border-gray-800 rounded-lg w-[960px] h-[85vh] flex text-sm overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="w-44 border-r border-gray-800 p-3 space-y-1">
          <div className="text-gray-100 font-medium mb-3">设置</div>
          {[["general", "通用"], ["models", "模型"], ["cc", "Claude Code"]].map(([k, l]) => (
            <button key={k} onClick={() => setTab(k)} className={`w-full text-left px-2 py-1.5 rounded text-sm ${tab === k ? "bg-gray-800 text-white" : "text-gray-400 hover:text-gray-200"}`}>{l}</button>
          ))}
        </div>
        <div className="flex-1 overflow-auto p-5">
          <div className="flex justify-end"><Button kind="ghost" onClick={onClose}>关闭</Button></div>
          {tab === "general" && <General author={author} setAuthor={setAuthor} />}
          {tab === "models" && <Models />}
          {tab === "cc" && <ClaudeCode projectId={projectId} cc={cc} refetch={refetch} />}
        </div>
      </div>
    </div>
  );
}

function General({ author, setAuthor }) {
  const [s, setS] = useState(null);
  useEffect(() => { api.settings().then(setS).catch(() => {}); }, []);
  return (
    <div className="space-y-3 max-w-lg">
      <h2 className="text-gray-100">通用</h2>
      <div className={label}>你的名字（显示在消息和反馈上；同一浏览器记住）</div>
      <input className={input} value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="例如 nic" />
      {s && (
        <div className="text-[11px] text-gray-500 space-y-1 mt-4">
          <div>并发运行上限：{s.max_concurrent_runs}（服务器环境 WORKBENCH_MAX_CONCURRENT_RUNS）</div>
          <div>项目目录：{s.projects_dir}</div>
          <div>数据目录：{s.data_dir}</div>
          <div>访问令牌：{s.token_required ? "已启用（服务器 WORKBENCH_TOKEN）" : "未启用（仅本机模式）"}</div>
        </div>
      )}
    </div>
  );
}

function Models() {
  const [data, setData] = useState({ profiles: [], kinds: {}, default_models: [] });
  const [adding, setAdding] = useState(null); // "builtin" | "custom"
  const load = () => api.profiles().then(setData).catch(() => {});
  useEffect(() => { load(); }, []);
  const builtin = Object.entries(data.kinds).filter(([, v]) => v.builtin);

  async function setDefault(profile_id, model) {
    await api.putSettings({ default_profile_id: profile_id || "", default_model: model || "" });
    load();
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-gray-100">模型</h2>
        <p className="text-[11px] text-gray-500 mt-1">填入各提供方的密钥即可使用其模型。密钥只写不读：保存后页面只知道「已设置」。按运行注入，不改服务器上 Claude Code 的全局配置。</p>
      </div>
      {data.profiles.map((p) => <ProviderCard key={p.id} p={p} data={data} onChange={load} onDefault={setDefault} />)}
      {data.profiles.length === 0 && <div className="text-xs text-gray-500 border border-dashed border-gray-800 rounded p-4">还没有提供方。没有提供方时，运行使用服务器上 Claude Code 自己的登录（见「Claude Code」页）。</div>}
      <div className="grid grid-cols-2 gap-3">
        <button onClick={() => setAdding("builtin")} className="border border-dashed border-gray-700 rounded-lg py-3 text-gray-300 hover:border-gray-500">＋ 添加提供方</button>
        <button onClick={() => setAdding("custom")} className="border border-dashed border-gray-700 rounded-lg py-3 text-gray-300 hover:border-gray-500">＋ 添加自定义提供方</button>
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
    <div className={`bg-gray-900 border rounded-lg p-4 ${isDefault ? "border-indigo-700" : "border-gray-800"}`}>
      <div className="flex items-center gap-2">
        <div className="text-gray-100">{p.display_name || p.name}</div>
        <div className="text-[11px] text-gray-500">{p.name} · {p.kind_label}</div>
        <div className="flex-1" />
        {isDefault ? <span className="text-[11px] text-indigo-300">默认</span> : <Button kind="ghost" onClick={() => onDefault(p.id, p.model)}>设为默认</Button>}
        <Button kind="danger" onClick={() => { if (window.confirm(`删除提供方 ${p.name}？其密钥一并删除。`)) api.deleteProfile(p.id).then(onChange); }}>删除</Button>
      </div>
      {p.compat?.checked_at && (
        <div className={`text-[11px] mt-1 ${p.compat.ok ? "text-emerald-300" : "text-rose-300"}`}>
          上次检查 {p.compat.ok ? "通过" : "失败"}：流式 {p.compat.stream_text ? "✓" : "✗"} · 工具 {p.compat.tool_call ? "✓" : "✗"}{p.compat.error ? ` · ${p.compat.error}` : ""}
        </div>
      )}
      <div className="grid grid-cols-2 gap-x-4">
        <div>
          <div className={label}>显示名称</div>
          <input className={input} value={display} onChange={(e) => setDisplay(e.target.value)} />
        </div>
        {(p.base_url || p.kind === "anthropic_compatible_gateway" || p.kind === "foundry") && (
          <div>
            <div className={label}>API 地址</div>
            <input className={input} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          </div>
        )}
        {p.accepts_secret && (
          <div>
            <div className={label}>API 密钥 / 令牌 {p.credential_set ? <span className="text-emerald-400">· 已设置{p.credential_source === "env" ? "（来自服务器环境变量）" : ""}</span> : <span className="text-amber-400">· 未设置</span>}</div>
            <div className="flex gap-1">
              <input type="password" className={input} placeholder={p.credential_set ? "输入新密钥以替换" : "输入密钥"} value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" />
              {p.credential_set && p.credential_source === "store" && <Button onClick={() => api.deleteProfileSecret(p.id).then(onChange)}>清除</Button>}
            </div>
          </div>
        )}
        <div>
          <div className={label}>模型列表（逗号分隔；第一个是默认）</div>
          <input className={input} value={models} onChange={(e) => setModels(e.target.value)} placeholder={data.default_models.join(", ")} />
        </div>
      </div>
      <div className="flex items-center gap-2 mt-3">
        <Button onClick={check} disabled={checking}>{checking ? "检查中…" : "兼容性检查"}</Button>
        <div className="flex-1 text-[11px] text-gray-400">{msg}</div>
        <Button kind="primary" onClick={save} disabled={!dirty}>保存</Button>
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
      await api.createProfile({ name, kind, display_name: display || null, base_url: baseUrl || null, secret: secret || null,
        models: models.split(",").map((s) => s.trim()).filter(Boolean) });
      onDone();
    } catch (e) { setErr(e.message); }
  }
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-4">
      <div className="text-gray-100 mb-2">{mode === "builtin" ? "添加提供方" : "添加自定义提供方"}</div>
      {mode === "builtin" && (
        <>
          <div className={label}>提供方</div>
          <select className={input} value={kind} onChange={(e) => setKind(e.target.value)}>{kinds.map(([id, v]) => <option key={id} value={id}>{v.label}</option>)}</select>
        </>
      )}
      <div className={label}>Provider ID <span className="text-gray-600">小写标识，用于凭据引用，创建后不可改</span></div>
      <input className={input} value={name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder="acme-gateway" />
      <div className={label}>显示名称</div>
      <input className={input} value={display} onChange={(e) => setDisplay(e.target.value)} />
      {(k?.needs_base_url) && (
        <>
          <div className={label}>API 地址</div>
          <input className={input} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://gateway.example" />
          {mode === "custom" && <div className="text-[11px] text-gray-500 mt-1">协议：Anthropic Messages（Claude Code 只支持这一种网关协议；OpenAI 风格接口无法接入）</div>}
        </>
      )}
      {k?.credential && (
        <>
          <div className={label}>API 密钥 / 令牌（只写）</div>
          <input type="password" className={input} value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" />
        </>
      )}
      <div className={label}>模型（逗号分隔；第一个是默认）</div>
      <input className={input} value={models} onChange={(e) => setModels(e.target.value)} />
      <div className="text-[11px] text-gray-500 mt-2">{k?.note}</div>
      {err && <div className="text-xs text-rose-300 mt-2">{err}</div>}
      <div className="flex justify-end gap-2 mt-3"><Button onClick={onCancel}>取消</Button><Button kind="primary" onClick={create} disabled={!name || !kind}>保存</Button></div>
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
    <div className="space-y-4 max-w-2xl">
      <h2 className="text-gray-100">Claude Code</h2>
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-2">
        <div className="text-gray-200">登录状态：{!cc ? "…" : eff?.logged_in ? <span className="text-emerald-300">已登录（{eff.method}{eff.email ? ` · ${eff.email}` : ""}{cc.saved_token?.logged_in ? " · 使用页面保存的令牌" : " · 服务器上 claude 自己的登录"}）</span> : <span className="text-rose-300">未登录</span>}</div>
        <div className="text-[11px] text-gray-500">两种登录方式：在服务器上以运行服务的用户执行一次 <code>claude</code> 交互登录；或在本机执行 <code>claude setup-token</code> 生成长期令牌，粘贴到下面（只写，保存在服务器 0600 文件里，按运行注入 CLAUDE_CODE_OAUTH_TOKEN）。</div>
        <div className="flex gap-1">
          <input type="password" className={input} placeholder="sk-ant-oat01-…" value={tok} onChange={(e) => setTok(e.target.value)} autoComplete="off" />
          <Button kind="primary" onClick={save} disabled={tok.length < 20}>保存令牌</Button>
          {cc?.saved_token_set && <Button onClick={() => api.ccLoginTokenDelete().then(() => refetch("cc"))}>清除令牌</Button>}
        </div>
        {msg && <div className="text-[11px] text-gray-400">{msg}</div>}
      </div>
      <h3 className="text-xs text-gray-400">发现结果</h3>
      {!disc && <div className="text-gray-500 text-xs">加载中…</div>}
      {disc && !disc.error && (
        <div className="text-xs text-gray-300 space-y-1">
          <div>二进制 <code>{disc.claude_binary || "未找到"}</code> · 版本 {disc.claude_version || "-"} · 平台 {disc.platform}</div>
          <div>用户 settings：env 变量 {disc.user.settings?.env_names?.join(", ") || "无"}；hooks {disc.user.settings?.hook_events?.join(", ") || "无"}；插件 {disc.user.settings?.plugins?.join(", ") || "无"}</div>
          <div>用户 skills：{disc.user.skills.join(", ") || "无"} · CLAUDE.md {disc.user.claude_md ? "有" : "无"}</div>
          {disc.project && <div>项目：CLAUDE.md {disc.project.claude_md.join(", ") || "无"} · .mcp.json {disc.project.mcp_servers.join(", ") || "无"} · skills {disc.project.skills.join(", ") || "无"}</div>}
          <Details summary="每类配置在运行中的支持情况（已验证 / 未验证）">
            <table className="text-[11px] w-full mt-1"><tbody>
              {Object.entries(disc.support).map(([k, v]) => <tr key={k} className="border-t border-gray-800"><td className="py-1 pr-2 text-gray-200 align-top">{k}</td><td className="py-1 pr-2 text-gray-400 align-top">{v.how}</td><td className="py-1 text-gray-500 align-top">{v.verified}</td></tr>)}
            </tbody></table>
          </Details>
        </div>
      )}
    </div>
  );
}
