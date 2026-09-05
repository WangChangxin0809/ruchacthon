import { useEffect, useState } from "react";
import { api } from "./api";
import { Button, Field, I, Spinner } from "./ui";

// The only screen an anonymous visitor sees. Three shapes, same card:
// first user (becomes the administrator), invited user (?invite=<token>),
// and everyone else, who needs an invite link to register.
export default function Login({ authState, onSignedIn }) {
  const inviteToken = new URLSearchParams(window.location.search).get("invite") || "";
  const [invite, setInvite] = useState(null);
  const [tab, setTab] = useState(authState?.registration_open || authState?.needs_deploy_token || inviteToken ? "register" : "login");
  const [handle, setHandle] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const first = !!authState?.registration_open && !inviteToken;
  const needsDeployToken = !!authState?.needs_deploy_token && !inviteToken;

  // authState arrives one tick after mount, so the tab follows it rather than
  // being decided before the server has said which of the three shapes this is
  useEffect(() => {
    if (!authState) return;
    setTab(authState.registration_open || authState.needs_deploy_token || inviteToken ? "register" : "login");
  }, [authState]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!inviteToken) return;
    api.invite(inviteToken).then(setInvite).catch((e) => setError(e.message));
  }, [inviteToken]);

  const submit = async (e) => {
    e.preventDefault();
    setError(""); setBusy(true);
    try {
      const body = tab === "register"
        ? await api.register({ handle: handle.trim(), display_name: displayName.trim() || handle.trim(), password, invite: inviteToken || undefined })
        : await api.login(handle.trim(), password);
      if (inviteToken && tab === "login") {
        // an existing account following an invite link joins on the way in
        localStorage.setItem("wb.token", body.token);
        await api.acceptInvite(inviteToken).catch(() => {});
      }
      await onSignedIn(body.token);
    } catch (err) {
      setError(err.message || "出错了");
      setBusy(false);
    }
  };

  const canSubmit = handle.trim().length >= 2 && password.length >= 8 && !busy;

  return (
    <div className="h-screen flex items-center justify-center bg-[var(--bg)] p-6">
      <div className="w-full max-w-[380px]">
        <div className="flex items-center gap-2.5 mb-6">
          <span className="w-9 h-9 rounded-xl bg-[var(--accent)] text-white flex items-center justify-center"><I.sitemap className="w-5 h-5" /></span>
          <div>
            <div className="text-[16px] font-semibold leading-tight">CC Workbench</div>
            <div className="text-[12px] text-[var(--muted)]">多人 · 多 agent 的本地工作台</div>
          </div>
        </div>

        <form onSubmit={submit} className="card p-5 shadow-[var(--shadow-md)]">
          {invite && (
            <div className="mb-4 rounded-md border border-[var(--border)] bg-[var(--subtle)] px-3 py-2.5">
              <div className="text-[13px] font-medium">{invite.inviter_name || "有人"} 邀请你加入「{invite.team_name}」</div>
              <div className="hint mt-0.5">注册或登录后自动加入，身份是{invite.role === "admin" ? "管理员" : "成员"}。</div>
            </div>
          )}
          {first && (
            <div className="mb-4 rounded-md border border-[var(--border)] bg-[var(--subtle)] px-3 py-2.5">
              <div className="text-[13px] font-medium">你是第一个用户</div>
              <div className="hint mt-0.5">这个账号会成为管理员，之后别人需要你的邀请链接才能注册。</div>
            </div>
          )}
          {needsDeployToken && (
            <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5">
              <div className="text-[13px] font-medium text-amber-900">这台服务器还没有第一个账号</div>
              <div className="text-[12px] text-amber-800 mt-0.5 leading-relaxed">
                建管理员账号需要部署令牌，否则先到的人就成了管理员。带上令牌再打开这一页：
                <code className="block mt-1 break-all">…:8787/?token=&lt;WORKBENCH_TOKEN&gt;</code>
                令牌在服务器的 <code>/etc/workbench.env</code> 里。
              </div>
            </div>
          )}

          {!first && (
            <div className="flex gap-1 mb-4 border-b border-[var(--border)] -mx-5 px-5">
              {[["login", "登录"], ["register", "注册"]].map(([v, label]) => (
                <button key={v} type="button" onClick={() => { setTab(v); setError(""); }}
                  className={`px-2 pb-2 -mb-px text-[13px] font-medium border-b-2 transition-colors duration-150 ${tab === v ? "border-[var(--accent)] text-[var(--text)]" : "border-transparent text-[var(--muted)] hover:text-[var(--text)]"}`}>
                  {label}
                </button>
              ))}
            </div>
          )}

          <div className="space-y-3">
            <Field label="用户名">
              <input className="input" value={handle} autoComplete="username" placeholder="小写字母、数字、_ . -"
                onChange={(e) => setHandle(e.target.value)} />
            </Field>
            {tab === "register" && (
              <Field label="显示名" hint="别人在聊天里看到的名字">
                <input className="input" value={displayName} placeholder={handle || "小明"} onChange={(e) => setDisplayName(e.target.value)} />
              </Field>
            )}
            <Field label="密码" hint={tab === "register" ? "至少 8 位" : undefined}>
              <input className="input" type="password" value={password} autoComplete={tab === "register" ? "new-password" : "current-password"}
                onChange={(e) => setPassword(e.target.value)} />
            </Field>
          </div>

          {error && <div className="mt-3 text-[12px] text-red-600 bg-red-50 border border-red-200 rounded-md px-2.5 py-2">{error}</div>}
          {tab === "register" && !first && !needsDeployToken && !inviteToken && !authState?.registration_open && (
            <div className="mt-3 hint">注册需要邀请链接，找团队里的人要一个。</div>
          )}

          <Button kind="primary" className="w-full mt-4 h-9" type="submit" disabled={!canSubmit}>
            {busy ? <Spinner /> : tab === "register" ? (first ? "创建管理员账号" : "注册并进入") : "登录"}
          </Button>
        </form>

        <div className="text-center hint mt-4">
          本地工作台。你的密钥、会话和项目都留在这台服务器上。
        </div>
      </div>
    </div>
  );
}
