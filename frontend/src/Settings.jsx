import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import Members from "./Members";
import Agents from "./Agents";
import Models from "./Models";
import { Badge, Button, Details, Field, I, useToast } from "./ui";

// AO's settings modal (left nav, rows with the control on the right) holding
// dsh's model configuration: provider cards, write-only keys, a preset picker.
const NAV = [
  ["general", "通用", "gear"],
  ["team", "团队", "users"],
  ["models", "模型", "brain"],
  ["agents", "Agent 定义", "bot"],
  ["cc", "Claude Code", "terminal"],
  ["keys", "快捷键", "compass"],
];

export default function Settings({ wb, tab, setTab, onClose }) {
  const [toastNode, toast] = useToast();
  useEffect(() => { const k = (e) => e.key === "Escape" && onClose(); window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [onClose]);
  const nav = NAV.filter(([k]) => k !== "cc" || wb.me?.is_admin);
  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-[2px] flex items-center justify-center z-50 animate-fade" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="bg-white border border-[var(--border)] rounded-xl w-[900px] max-w-[95vw] h-[82vh] flex text-[13px] overflow-hidden shadow-[var(--shadow-lg)] animate-rise">
        <div className="w-48 border-r border-[var(--border)] bg-[var(--bg)] p-3 space-y-0.5 shrink-0">
          <div className="font-semibold text-[14px] px-2 py-1.5 mb-2">设置</div>
          {nav.map(([k, l, icon]) => {
            const Icon = I[icon];
            return (
              <button key={k} onClick={() => setTab(k)} className={`nav-item ${tab === k ? "nav-item-active" : "text-[var(--muted)]"}`}>
                <Icon className={tab === k ? "" : "text-[var(--muted)]"} />{l}
              </button>
            );
          })}
        </div>
        <div className="flex-1 min-w-0 flex flex-col">
          <div className="row h-12 px-6 border-b border-[var(--border)] shrink-0">
            <span className="font-semibold text-[14px]">{nav.find((n) => n[0] === tab)?.[1]}</span>
            <div className="flex-1" />
            <button className="btn btn-ghost btn-sm btn-icon" onClick={onClose} aria-label="关闭"><I.x /></button>
          </div>
          <div className="flex-1 overflow-y-auto p-6">
            {tab === "general" && <General wb={wb} toast={toast} />}
            {tab === "team" && <Members wb={wb} toast={toast} />}
            {tab === "models" && <Models wb={wb} toast={toast} />}
            {tab === "agents" && <Agents wb={wb} toast={toast} />}
            {tab === "cc" && <ClaudeCode wb={wb} />}
            {tab === "keys" && <Keys />}
          </div>
        </div>
      </div>
      {toastNode}
    </div>
  );
}

export function Row({ title, desc, children }) {
  return (
    <div className="flex justify-between items-start gap-6 py-3 border-b border-[var(--border)] last:border-b-0">
      <div className="min-w-0 pt-1"><div>{title}</div>{desc && <div className="text-[12px] text-[var(--muted)] mt-0.5">{desc}</div>}</div>
      <div className="shrink-0 row">{children}</div>
    </div>
  );
}

function General({ wb, toast }) {
  const me = wb.me;
  const [name, setName] = useState(me?.display_name || "");
  const [pw, setPw] = useState({ old: "", next: "" });
  const [s, setS] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.settings().then(setS).catch(() => {}); }, []);

  const saveName = async () => {
    setBusy(true);
    try { await api.patchMe({ display_name: name.trim() }); await wb.refetch("me"); toast("名字已改"); }
    catch (e) { toast(e.message, "red"); } finally { setBusy(false); }
  };
  const savePw = async () => {
    setBusy(true);
    try { await api.patchMe({ password: { old: pw.old, new: pw.next } }); setPw({ old: "", next: "" }); toast("密码已改，其他设备需要重新登录"); }
    catch (e) { toast(e.message, "red"); } finally { setBusy(false); }
  };

  return (
    <div>
      <Row title="显示名" desc="别人在消息里看到的名字">
        <input className="input w-52" value={name} onChange={(e) => setName(e.target.value)} />
        <Button kind="primary" size="sm" disabled={busy || !name.trim() || name === me?.display_name} onClick={saveName}>保存</Button>
      </Row>
      <Row title="用户名" desc="别人 @ 你时用的名字，不能改"><span className="mono text-[var(--muted)]">@{me?.handle}</span></Row>
      <Row title="修改密码" desc="改完之后其他设备上的登录会失效">
        <div className="space-y-1.5">
          <input type="password" className="input w-52" placeholder="当前密码" value={pw.old} onChange={(e) => setPw({ ...pw, old: e.target.value })} autoComplete="current-password" />
          <div className="row">
            <input type="password" className="input w-52" placeholder="新密码（至少 8 位）" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} autoComplete="new-password" />
            <Button size="sm" disabled={busy || pw.next.length < 8 || !pw.old} onClick={savePw}>修改</Button>
          </div>
        </div>
      </Row>
      <Row title="退出登录"><Button size="sm" kind="danger" onClick={wb.signOut}><I.logout />退出</Button></Row>
      {s && (
        <>
          <div className="label mt-6 mb-1">这台服务器</div>
          <Row title="并发运行上限" desc="环境变量 WORKBENCH_MAX_CONCURRENT_RUNS"><span className="mono">{s.max_concurrent_runs}</span></Row>
          <Row title="项目目录"><span className="mono text-[12px]">{s.projects_dir}</span></Row>
          <Row title="数据目录"><span className="mono text-[12px]">{s.data_dir}</span></Row>
        </>
      )}
    </div>
  );
}

function Keys() {
  const K = ({ k }) => <span className="kbd">{k}</span>;
  return (
    <div>
      <Row title="搜索任务"><K k="Ctrl+K" /></Row>
      <Row title="发送消息"><K k="Enter" /></Row>
      <Row title="换行"><K k="Shift+Enter" /></Row>
      <Row title="补全 @ 提到的人"><K k="↑ ↓" /> <K k="Enter" /></Row>
      <Row title="新任务对话框里提交"><K k="Ctrl+Enter" /></Row>
      <Row title="关闭对话框"><K k="Esc" /></Row>
    </div>
  );
}

function ClaudeCode({ wb }) {
  const { cc, projectId, refetch } = wb;
  const [disc, setDisc] = useState(null);
  const [tok, setTok] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => { api.discovery(projectId).then(setDisc).catch(() => setDisc({ error: true })); }, [projectId]);
  const eff = cc?.effective;
  async function save() {
    setMsg("");
    try {
      const r = await api.ccLoginToken(tok);
      setTok("");
      setMsg(r.status.logged_in ? "令牌有效，已保存" : `已保存，但 claude 说未登录：${r.status.error || r.status.method}`);
      refetch("cc");
    } catch (e) { setMsg(`失败：${e.message}`); }
  }
  return (
    <div className="space-y-5">
      <div>
        <Row title="登录状态">
          {!cc ? "…" : eff?.logged_in
            ? <span className="text-green-700">已登录（{eff.method}{eff.email ? ` · ${eff.email}` : ""}{cc.saved_token?.logged_in ? " · 页面保存的令牌" : " · 服务器上 claude 自己的登录"}）</span>
            : <span className="text-red-600">未登录</span>}
        </Row>
        <Row title="长期令牌" desc={<>本机执行 <code>claude setup-token</code>，把令牌粘贴到这里。只写，保存在服务器 0600 文件里，按运行注入。</>}>
          <input type="password" className="input w-64" placeholder="sk-ant-oat01-…" value={tok} onChange={(e) => setTok(e.target.value)} autoComplete="off" />
          <Button kind="primary" size="sm" onClick={save} disabled={tok.length < 20}>保存</Button>
          {cc?.saved_token_set && <Button size="sm" onClick={() => api.ccLoginTokenDelete().then(() => refetch("cc"))}>清除</Button>}
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
              {Object.entries(disc.support).map(([k, v]) => (
                <tr key={k} className="border-t border-[var(--border)]"><td className="py-1 pr-2 align-top">{k}</td><td className="py-1 pr-2 text-[var(--muted)] align-top">{v.how}</td><td className="py-1 text-[var(--faint)] align-top">{v.verified}</td></tr>
              ))}
            </tbody></table>
          </Details>
        </div>
      )}
    </div>
  );
}
