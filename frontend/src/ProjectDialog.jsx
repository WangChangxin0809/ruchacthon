import { useEffect, useState } from "react";
import { api } from "./api";

export default function ProjectDialog({ initialMode = "clone", onClose, onCreated }) {
  const [mode, setMode] = useState(initialMode);
  const [gitUrl, setGitUrl] = useState("");
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [cands, setCands] = useState({ projects_dir: "", candidates: [] });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { api.projectCandidates().then(setCands).catch(() => {}); }, []);
  async function create() {
    setBusy(true); setErr("");
    try {
      const body = mode === "clone" ? { git_url: gitUrl, name: name || null } : mode === "new" ? { name } : { root_path: path, name: name || null };
      onCreated(await api.createProject(body));
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  const tab = (k, l) => <button onClick={() => setMode(k)} className={`px-3 py-1.5 text-[12px] rounded-md ${mode === k ? "bg-[var(--accent)] text-white" : "text-[var(--muted)] hover:bg-[var(--subtle)]"}`}>{l}</button>;
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50" onClick={onClose}>
      <div className="card w-[560px] p-5 shadow-xl space-y-3" onClick={(e) => e.stopPropagation()}>
        <div className="text-[15px] font-semibold">新增项目</div>
        <div className="row gap-1">{tab("clone", "克隆 git 仓库")}{tab("new", "新建空项目")}{tab("existing", "服务器上已有目录")}</div>
        {mode === "clone" && (<>
          <input autoFocus className="input" placeholder="https://github.com/org/repo.git 或 git@…" value={gitUrl} onChange={(e) => setGitUrl(e.target.value)} />
          <input className="input" placeholder="项目名（可选，默认用仓库名）" value={name} onChange={(e) => setName(e.target.value)} />
          <div className="text-[11px] text-[var(--muted)]">私有仓库把访问令牌放进 URL（https://TOKEN@github.com/…），或先在服务器配好 SSH key。克隆到 {cands.projects_dir}。</div>
        </>)}
        {mode === "new" && (<>
          <input autoFocus className="input" placeholder="项目名" value={name} onChange={(e) => setName(e.target.value)} />
          <div className="text-[11px] text-[var(--muted)]">在 {cands.projects_dir} 下新建目录并 git init。</div>
        </>)}
        {mode === "existing" && (<>
          {cands.candidates.length > 0 && <select className="input" value={path} onChange={(e) => setPath(e.target.value)}><option value="">— 选择 {cands.projects_dir} 下的目录 —</option>{cands.candidates.map((c) => <option key={c} value={c}>{c}</option>)}</select>}
          <input className="input" placeholder="或输入服务器上的绝对路径" value={path} onChange={(e) => setPath(e.target.value)} />
          <input className="input" placeholder="项目名（可选）" value={name} onChange={(e) => setName(e.target.value)} />
        </>)}
        {err && <div className="text-[12px] text-red-600">{err}</div>}
        <div className="row justify-end gap-2"><button className="btn" onClick={onClose}>取消</button><button className="btn btn-primary" disabled={busy || (mode === "clone" ? !gitUrl : mode === "new" ? !name : !path)} onClick={create}>{busy ? "处理中…" : "创建"}</button></div>
      </div>
    </div>
  );
}
