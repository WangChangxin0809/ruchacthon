import { useEffect, useState } from "react";
import { api } from "./api";
import { Button } from "./ui";

export default function ProjectDialog({ onClose, onCreated }) {
  const [mode, setMode] = useState("clone");
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
      const p = await api.createProject(body);
      onCreated(p);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  const tab = (k, l) => <button onClick={() => setMode(k)} className={`px-3 py-1.5 text-xs rounded ${mode === k ? "bg-gray-700 text-white" : "text-gray-400 hover:text-gray-200"}`}>{l}</button>;
  const input = "w-full bg-gray-950 border border-gray-800 rounded px-2 py-1.5 text-sm text-gray-200";

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-[#111318] border border-gray-800 rounded-lg w-[560px] p-5 text-sm space-y-3" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between"><h2 className="text-gray-100">新增项目</h2><Button kind="ghost" onClick={onClose}>关闭</Button></div>
        <div className="flex gap-1">{tab("clone", "克隆 git 仓库")}{tab("new", "新建空项目")}{tab("existing", "服务器上已有目录")}</div>
        {mode === "clone" && (
          <>
            <input className={input} placeholder="https://github.com/org/repo.git 或 git@…" value={gitUrl} onChange={(e) => setGitUrl(e.target.value)} />
            <input className={input} placeholder="项目名（可选，默认用仓库名）" value={name} onChange={(e) => setName(e.target.value)} />
            <div className="text-[11px] text-gray-500">私有仓库请把访问令牌放进 URL（https://TOKEN@github.com/…），或先在服务器上配好 SSH key。克隆到 {cands.projects_dir}。</div>
          </>
        )}
        {mode === "new" && (
          <>
            <input className={input} placeholder="项目名" value={name} onChange={(e) => setName(e.target.value)} />
            <div className="text-[11px] text-gray-500">在 {cands.projects_dir} 下新建目录并 git init。</div>
          </>
        )}
        {mode === "existing" && (
          <>
            {cands.candidates.length > 0 && (
              <select className={input} value={path} onChange={(e) => setPath(e.target.value)}>
                <option value="">— 选择 {cands.projects_dir} 下的目录 —</option>
                {cands.candidates.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            )}
            <input className={input} placeholder="或输入服务器上的绝对路径" value={path} onChange={(e) => setPath(e.target.value)} />
            <input className={input} placeholder="项目名（可选）" value={name} onChange={(e) => setName(e.target.value)} />
          </>
        )}
        {err && <div className="text-xs text-rose-300">{err}</div>}
        <Button kind="primary" className="w-full" disabled={busy || (mode === "clone" ? !gitUrl : mode === "new" ? !name : !path)} onClick={create}>{busy ? "处理中…" : "创建"}</Button>
      </div>
    </div>
  );
}
