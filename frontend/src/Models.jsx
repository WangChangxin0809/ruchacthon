import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { Badge, Button, Dialog, EmptyState, Field, I, Spinner, Tabs } from "./ui";

// dsh's model settings: your providers as cards, keys write-only, and a
// vendor picker built from the preset catalogue the server serves.
export default function Models({ wb, toast }) {
  const [data, setData] = useState({ profiles: [], kinds: {}, default_models: [] });
  const [presets, setPresets] = useState(null);
  const [adding, setAdding] = useState(false);
  const [scope, setScope] = useState("mine");
  const load = () => api.profiles().then(setData).catch(() => {});
  useEffect(() => { load(); api.presets().then(setPresets).catch(() => {}); }, []);

  const mine = data.profiles.filter((p) => p.mine);
  const shared = data.profiles.filter((p) => !p.mine);
  const shown = scope === "mine" ? mine : shared;

  const setDefault = async (p) => {
    await api.patchMe({ prefs: { default_profile_id: p.id, default_model: p.model || null } });
    await wb.refetch("me"); load(); toast(`默认改成 ${p.display_name || p.name}`);
  };

  return (
    <div className="space-y-4">
      <p className="text-[12px] text-[var(--muted)]">
        填入你自己的密钥即可使用对应服务商。密钥只写不读：保存后页面只知道「已设置」，也不会写进日志或数据库。
        说 OpenAI 协议的服务商会自动经过本机转换代理。
      </p>

      <div className="row">
        <Tabs size="sm" value={scope} onChange={setScope} tabs={[{ value: "mine", label: `我的 · ${mine.length}` }, { value: "shared", label: `团队共享 · ${shared.length}` }]} />
        <div className="flex-1" />
        <Button kind="primary" size="sm" onClick={() => setAdding(true)}><I.plus />添加服务商</Button>
      </div>

      {shown.length === 0 && (
        <EmptyState icon={<I.key className="w-5 h-5" />} title={scope === "mine" ? "还没有你自己的服务商" : "团队里没人共享服务商"}
          action={scope === "mine" ? <Button kind="primary" size="sm" onClick={() => setAdding(true)}><I.plus />添加</Button> : null}>
          {scope === "mine"
            ? "没有服务商时，运行使用这台服务器上 Claude Code 自己的登录。"
            : "别人把服务商设成「共享给团队」后会出现在这里，你可以用，但看不到密钥。"}
        </EmptyState>
      )}
      {shown.map((p) => (
        <ProviderCard key={p.id} p={p} data={data} me={wb.me} onChange={load} onDefault={() => setDefault(p)} toast={toast} />
      ))}

      {adding && <AddProvider presets={presets} kinds={data.kinds} teamId={wb.teamId} defaultModels={data.default_models}
        onDone={() => { setAdding(false); load(); toast("已添加"); }} onCancel={() => setAdding(false)} />}
    </div>
  );
}

function ProviderCard({ p, data, me, onChange, onDefault, toast }) {
  const [secret, setSecret] = useState("");
  const [models, setModels] = useState((p.models || []).join(", "));
  const [baseUrl, setBaseUrl] = useState(p.base_url || "");
  const [display, setDisplay] = useState(p.display_name || p.name);
  const [shared, setShared] = useState(!!p.shared);
  const [check, setCheck] = useState(null);
  const [busy, setBusy] = useState("");
  const [found, setFound] = useState(null);
  const isDefault = data.default_profile_id === p.id;
  const dirty = secret || models !== (p.models || []).join(", ") || baseUrl !== (p.base_url || "") || display !== (p.display_name || p.name) || shared !== !!p.shared;

  const save = async () => {
    setBusy("save");
    try {
      const list = models.split(",").map((s) => s.trim()).filter(Boolean);
      await api.patchProfile(p.id, { secret: secret || null, models: list, model: list[0] || null, base_url: baseUrl || null, display_name: display, shared });
      setSecret(""); onChange(); toast("已保存");
    } catch (e) { toast(e.message, "red"); } finally { setBusy(""); }
  };
  const test = async () => {
    setBusy("check"); setCheck(null);
    try { setCheck(await api.checkProfile(p.id)); onChange(); }
    catch (e) { setCheck({ ok: false, error: e.message }); } finally { setBusy(""); }
  };
  const fetchModels = async () => {
    setBusy("models");
    try {
      const r = await api.profileModels(p.id);
      setFound(r.models);
      toast(`找到 ${r.models.length} 个模型`);
    } catch (e) { toast(e.message, "red"); } finally { setBusy(""); }
  };

  return (
    <div className={`card p-4 ${isDefault ? "border-[var(--accent)]" : ""}`}>
      <div className="row">
        <div className="font-medium truncate">{p.display_name || p.name}</div>
        <Badge tone="outline">{p.kind_label}</Badge>
        {p.needs_routing && <Badge tone="violet" title="Claude Code 说 Anthropic 协议，这家说 OpenAI 协议，本机代理负责转换">经代理</Badge>}
        {!p.runnable && <Badge tone="amber" title={`${p.api_format} 协议还没接`}>暂不支持</Badge>}
        {isDefault && <Badge tone="accent">默认</Badge>}
        {!p.mine && <Badge tone="blue" title={`由 ${p.owner_name || "团队成员"} 共享`}>共享</Badge>}
        <div className="flex-1" />
        {!isDefault && p.runnable && <Button size="xs" onClick={onDefault}>设为默认</Button>}
        {p.website_url && <a className="btn btn-xs btn-ghost" href={p.website_url} target="_blank" rel="noreferrer" title="服务商官网"><I.external className="w-3.5 h-3.5" /></a>}
        {p.editable && (
          <Button size="xs" kind="ghost" className="text-red-600" title="删除这个服务商" onClick={() => { if (window.confirm(`删除 ${p.display_name || p.name}？密钥一并删除。`)) api.deleteProfile(p.id).then(onChange); }}>
            <I.trash className="w-3.5 h-3.5" />
          </Button>
        )}
      </div>

      {check && (
        <div className={`text-[12px] mt-2 rounded-md px-2.5 py-1.5 border ${check.ok ? "bg-green-50 border-green-200 text-green-700" : "bg-red-50 border-red-200 text-red-700"}`}>
          {check.ok ? `连接正常 · ${check.latency_ms} ms · ${check.model}` : `连接失败：${check.error}`}
        </div>
      )}

      {!p.editable && <div className="hint mt-2">这是 {p.owner_name || "别人"} 共享的，你可以用但不能改。</div>}

      {p.editable && (
        <>
          <div className="grid grid-cols-2 gap-x-4 gap-y-0">
            <Field label="显示名称" className="mt-3"><input className="input" value={display} onChange={(e) => setDisplay(e.target.value)} /></Field>
            <Field label="API 地址" className="mt-3" hint={p.preset_base_url ? `预置：${p.preset_base_url}` : undefined}>
              <input className="input" value={baseUrl} placeholder={p.preset_base_url || "https://…"} onChange={(e) => setBaseUrl(e.target.value)} />
            </Field>
            {p.accepts_secret && (
              <Field className="mt-3"
                label={<>API 密钥 {p.credential_set ? <span className="text-green-700 font-normal">· 已设置{p.credential_source === "env" ? "（服务器环境变量）" : ""}</span> : <span className="text-amber-600 font-normal">· 未设置</span>}</>}>
                <div className="row">
                  <input type="password" className="input" placeholder={p.credential_set ? "输入新密钥以替换" : "输入密钥"} value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" />
                  {p.credential_set && p.credential_source === "store" && <Button size="sm" onClick={() => api.deleteProfileSecret(p.id).then(onChange)}>清除</Button>}
                </div>
              </Field>
            )}
            <Field label="模型（逗号分隔，第一个是默认）" className="mt-3">
              <div className="row">
                <input className="input" value={models} list={`models-${p.id}`} onChange={(e) => setModels(e.target.value)} placeholder={(data.default_models || []).join(", ")} />
                <Button size="sm" disabled={busy === "models" || !p.credential_set} onClick={fetchModels} title="问服务商它有哪些模型">
                  {busy === "models" ? <Spinner /> : <I.refresh className="w-3.5 h-3.5" />}
                </Button>
              </div>
              {found && <datalist id={`models-${p.id}`}>{found.map((m) => <option key={m.id} value={m.id} />)}</datalist>}
            </Field>
          </div>
          {found && (
            <div className="mt-2 max-h-24 overflow-y-auto flex flex-wrap gap-1">
              {found.slice(0, 40).map((m) => (
                <button key={m.id} className="chip hover:bg-[var(--subtle)]" onClick={() => setModels(models ? `${models}, ${m.id}` : m.id)}>{m.id}</button>
              ))}
            </div>
          )}
          <div className="row mt-3">
            <Button size="sm" onClick={test} disabled={busy === "check" || !p.credential_set}>{busy === "check" ? <Spinner /> : <I.zap className="w-3.5 h-3.5" />}测试连接</Button>
            <label className="row text-[12px] text-[var(--muted)] cursor-pointer ml-2">
              <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />共享给团队
            </label>
            <div className="flex-1" />
            <Button kind="primary" size="sm" onClick={save} disabled={!dirty || busy === "save"}>保存</Button>
          </div>
        </>
      )}
    </div>
  );
}

// The picker: cc-switch's catalogue grouped by category, with a key-prefix
// hint so pasting a key usually finds the vendor for you.
function AddProvider({ presets, kinds, teamId, defaultModels, onDone, onCancel }) {
  const [cat, setCat] = useState("cn_official");
  const [qs, setQs] = useState("");
  const [chosen, setChosen] = useState(null);
  const [custom, setCustom] = useState(false);
  const [form, setForm] = useState({ name: "", display: "", secret: "", models: "", baseUrl: "", kind: "anthropic_compatible_gateway" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const items = presets?.items || [];
  const shown = useMemo(() => {
    const q = qs.trim().toLowerCase();
    return items.filter((i) => (q ? i.name.toLowerCase().includes(q) : i.category === cat));
  }, [items, cat, qs]);

  // typing a key suggests the vendor it belongs to (cc-switch's prefix table)
  const suggestion = useMemo(() => {
    const k = form.secret.trim();
    if (!k || chosen) return null;
    const hit = (presets?.key_prefixes || []).find((p) => k.startsWith(p.prefix));
    return hit ? items.find((i) => i.name.toLowerCase() === hit.preset.toLowerCase()) : null;
  }, [form.secret, presets, items, chosen]);

  const pick = (i) => {
    setChosen(i);
    setForm((f) => ({
      ...f,
      name: f.name || i.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40),
      display: f.display || i.name,
      models: f.models || Object.values(i.models || {})[0] || "",
    }));
  };

  const create = async () => {
    setErr(""); setBusy(true);
    try {
      const list = form.models.split(",").map((s) => s.trim()).filter(Boolean);
      await api.createProfile({
        name: form.name, kind: custom ? form.kind : "preset", preset: custom ? undefined : chosen.name,
        display_name: form.display || null, base_url: form.baseUrl || null, secret: form.secret || null,
        models: list, model: list[0] || null, team_id: teamId,
        model_map: !custom && chosen?.needs_routing && list[0] ? { "*": list[0] } : undefined,
      });
      onDone();
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  const step2 = chosen || custom;
  return (
    <Dialog title={step2 ? `添加 ${custom ? "自定义服务商" : chosen.name}` : "选一个服务商"} onClose={onCancel} width={620}
      footer={step2
        ? <><Button onClick={() => { setChosen(null); setCustom(false); }}>返回</Button><Button kind="primary" disabled={!form.name || busy} onClick={create}>添加</Button></>
        : <Button onClick={onCancel}>取消</Button>}>
      {!step2 && (
        <>
          <div className="row mb-3">
            <div className="relative flex-1">
              <I.search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-[var(--faint)]" />
              <input className="input pl-8" placeholder="搜服务商名字" value={qs} onChange={(e) => setQs(e.target.value)} />
            </div>
            <Button size="sm" onClick={() => setCustom(true)}>自定义</Button>
          </div>
          {!qs && (
            <div className="flex flex-wrap gap-1 mb-3">
              {(presets?.categories || []).map((c) => (
                <button key={c.id} onClick={() => setCat(c.id)}
                  className={`chip ${cat === c.id ? "bg-[var(--accent)] text-white border-transparent" : "hover:bg-[var(--subtle)]"}`}>{c.label} {c.count}</button>
              ))}
            </div>
          )}
          {!presets && <div className="text-[12px] text-[var(--muted)]">加载中…</div>}
          <div className="grid grid-cols-2 gap-1.5 max-h-[340px] overflow-y-auto">
            {shown.map((i) => (
              <button key={i.name} onClick={() => i.runnable && pick(i)} disabled={!i.runnable}
                className={`card card-hover p-2.5 text-left ${i.runnable ? "" : "opacity-50 cursor-not-allowed"}`}>
                <div className="row">
                  <span className="font-medium truncate flex-1">{i.name}</span>
                  {i.needs_routing && <Badge tone="violet">经代理</Badge>}
                  {!i.runnable && <Badge tone="amber">暂不支持</Badge>}
                </div>
                <div className="text-[11px] text-[var(--muted)] truncate mt-0.5">{i.base_url || i.notes || "—"}</div>
              </button>
            ))}
          </div>
        </>
      )}

      {step2 && (
        <div className="space-y-3">
          {chosen?.needs_routing && (
            <div className="text-[12px] rounded-md border border-violet-200 bg-violet-50 text-violet-800 px-3 py-2">
              这家说的是 OpenAI 协议。运行时本机会起一个只监听回环地址的转换代理，你的密钥不会交给 Claude Code。
            </div>
          )}
          {custom && <Field label="协议" hint="Claude Code 只能直连 Anthropic Messages 协议的网关。"><select className="input" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            {Object.entries(kinds || {}).filter(([, v]) => !v.builtin).map(([id, v]) => <option key={id} value={id}>{v.label}</option>)}
          </select></Field>}
          <div className="grid grid-cols-2 gap-3">
            <Field label="标识" hint="小写，创建后不可改"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value.toLowerCase() })} placeholder="my-vendor" /></Field>
            <Field label="显示名称"><input className="input" value={form.display} onChange={(e) => setForm({ ...form, display: e.target.value })} /></Field>
          </div>
          {(custom || chosen?.base_url) && (
            <Field label="API 地址" hint={chosen?.base_url ? `留空则用预置的 ${chosen.base_url}` : undefined}>
              <input className="input" value={form.baseUrl} onChange={(e) => setForm({ ...form, baseUrl: e.target.value })} placeholder={chosen?.base_url || "https://gateway.example"} />
            </Field>
          )}
          <Field label="API 密钥" hint={chosen?.api_key_url ? <>在 <a href={chosen.api_key_url} target="_blank" rel="noreferrer">服务商后台</a> 生成。只写不读。</> : "只写不读。"}>
            <input type="password" className="input" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} autoComplete="off" />
          </Field>
          {suggestion && <div className="hint">这个前缀看起来是 {suggestion.name} 的密钥。</div>}
          <Field label="模型（逗号分隔，第一个是默认）" hint={chosen?.needs_routing ? "代理会把 Claude Code 要的 haiku/sonnet/opus 映射到这里的第一个模型。" : undefined}>
            <input className="input" value={form.models} onChange={(e) => setForm({ ...form, models: e.target.value })} placeholder={(defaultModels || []).join(", ")} />
          </Field>
          {err && <div className="text-[12px] text-red-600">{err}</div>}
        </div>
      )}
    </Dialog>
  );
}
