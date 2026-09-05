import { useEffect, useState } from "react";
import { api } from "./api";
import { Button, Details } from "./ui";

export default function Settings({ projectId, onClose }) {
  const [data, setData] = useState({ profiles: [], kinds: {} });
  const [disc, setDisc] = useState(null);
  const [form, setForm] = useState({ name: "", kind: "claude_code_default", base_url: "", model: "", credential_env: "" });
  const [checking, setChecking] = useState(null);
  const [err, setErr] = useState("");
  const load = () => api.profiles().then(setData);
  useEffect(() => { load(); api.discovery(projectId).then(setDisc).catch(() => setDisc({ error: true })); }, [projectId]);

  async function create() {
    setErr("");
    try {
      await api.createProfile({ ...form, base_url: form.base_url || null, model: form.model || null, credential_env: form.credential_env || null });
      setForm({ name: "", kind: "claude_code_default", base_url: "", model: "", credential_env: "" });
      load();
    } catch (e) { setErr(e.message); }
  }
  async function check(id) {
    setChecking(id);
    try { await api.checkProfile(id); load(); } finally { setChecking(null); }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-[#111318] border border-gray-800 rounded-lg w-[820px] max-h-[85vh] overflow-auto p-5 text-sm" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-gray-100 font-medium">Provider Profile 与 Claude Code 配置</h2>
          <Button kind="ghost" onClick={onClose}>关闭</Button>
        </div>

        <h3 className="text-xs text-gray-400 mb-2">Provider Profiles（按运行注入，不改你的全局 Claude Code 配置；密钥只以环境变量名引用）</h3>
        <div className="space-y-2 mb-3">
          {data.profiles.map((p) => (
            <div key={p.id} className="bg-gray-900 border border-gray-800 rounded p-2 flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <div className="text-gray-100">{p.name} <span className="text-gray-500 text-xs">{p.kind_label}</span></div>
                <div className="text-[11px] text-gray-500 truncate">
                  {p.base_url && <>endpoint {p.base_url} · </>}模型 {p.model || "默认"} · 凭证 {p.credential_env ? `${p.credential_env}（${p.credential_set ? "已在服务端设置" : "未设置"}）` : "无需"}
                </div>
                {p.compat?.checked_at && (
                  <div className={`text-[11px] ${p.compat.ok ? "text-emerald-300" : "text-rose-300"}`}>
                    兼容性 {p.compat.ok ? "通过" : "失败"}：流式文本 {p.compat.stream_text ? "✓" : "✗"} · 工具调用 {p.compat.tool_call ? "✓" : "✗"}{p.compat.error && ` · ${p.compat.error}`}
                  </div>
                )}
              </div>
              <Button onClick={() => check(p.id)} disabled={checking === p.id}>{checking === p.id ? "检查中…" : "兼容性检查"}</Button>
              <Button kind="danger" onClick={() => api.deleteProfile(p.id).then(load)}>删除</Button>
            </div>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2 bg-gray-900 border border-gray-800 rounded p-2 mb-4">
          <input className="bg-gray-950 border border-gray-800 rounded px-2 py-1 text-xs text-gray-200" placeholder="名称" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <select className="bg-gray-950 border border-gray-800 rounded px-2 py-1 text-xs text-gray-200" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            {Object.entries(data.kinds).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
          <input className="bg-gray-950 border border-gray-800 rounded px-2 py-1 text-xs text-gray-200" placeholder="base_url（gateway / foundry）" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
          <input className="bg-gray-950 border border-gray-800 rounded px-2 py-1 text-xs text-gray-200" placeholder="模型，如 claude-sonnet-5" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} />
          <input className="bg-gray-950 border border-gray-800 rounded px-2 py-1 text-xs text-gray-200" placeholder="凭证环境变量名（服务端需已设置）" value={form.credential_env} onChange={(e) => setForm({ ...form, credential_env: e.target.value })} />
          <Button kind="primary" onClick={create} disabled={!form.name}>新建 Profile</Button>
          <div className="col-span-2 text-[11px] text-gray-500">{data.kinds[form.kind]?.note}</div>
          {err && <div className="col-span-2 text-[11px] text-rose-300">{err}</div>}
        </div>

        <h3 className="text-xs text-gray-400 mb-2">Claude Code 发现结果</h3>
        {!disc && <div className="text-gray-500 text-xs">加载中…</div>}
        {disc && !disc.error && (
          <div className="text-xs text-gray-300 space-y-1">
            <div>二进制 <code>{disc.claude_binary || "未找到"}</code> · 版本 {disc.claude_version || "-"} · 平台 {disc.platform}</div>
            <div>用户 settings：env 变量 {disc.user.settings?.env_names?.join(", ") || "无"}；hooks {disc.user.settings?.hook_events?.join(", ") || "无"}；插件 {disc.user.settings?.plugins?.join(", ") || "无"}</div>
            <div>用户 skills：{disc.user.skills.join(", ") || "无"} · CLAUDE.md {disc.user.claude_md ? "有" : "无"} · 登录账号 {disc.user.claude_json?.has_oauth_account ? "有" : "无/未知"}</div>
            {disc.project && (
              <div>项目：CLAUDE.md {disc.project.claude_md.join(", ") || "无"} · .mcp.json 服务 {disc.project.mcp_servers.join(", ") || "无"} · skills {disc.project.skills.join(", ") || "无"}</div>
            )}
            <Details summary="每类配置在运行中的支持情况（已验证 / 未验证）">
              <table className="text-[11px] w-full mt-1">
                <tbody>
                  {Object.entries(disc.support).map(([k, v]) => (
                    <tr key={k} className="border-t border-gray-800"><td className="py-1 pr-2 text-gray-200 align-top">{k}</td><td className="py-1 pr-2 text-gray-400 align-top">{v.how}</td><td className="py-1 text-gray-500 align-top">{v.verified}</td></tr>
                  ))}
                </tbody>
              </table>
            </Details>
          </div>
        )}
      </div>
    </div>
  );
}
