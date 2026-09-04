import { useState } from "react";

export default function PreviewPanel({ previews }) {
  const [openId, setOpenId] = useState(null);

  if (previews.length === 0) {
    return (
      <div className="text-sm text-gray-600 border border-dashed border-gray-800 rounded-lg p-6 text-center">
        Agent 完成工作后会在这里展示成果
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {previews.map((p) => {
        const open = openId === p.id;
        return (
          <div key={p.id} className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
            <button
              onClick={() => setOpenId(open ? null : p.id)}
              className="w-full text-left px-3 py-2 flex items-center justify-between hover:bg-gray-850"
            >
              <div>
                <div className="text-sm text-gray-100">{p.title}</div>
                <div className="text-xs text-gray-500">
                  {p.actor_id} · {p.owner_id} · {p.created_at}
                </div>
              </div>
              <span className="text-gray-500 text-xs">{open ? "收起" : "展开"}</span>
            </button>
            {open && (
              <div className="px-3 pb-3">
                <p className="text-sm text-gray-300 mb-2">{p.summary}</p>
                {p.html && (
                  <iframe
                    title={p.title}
                    srcDoc={p.html}
                    sandbox=""
                    className="w-full h-48 bg-white rounded border border-gray-700"
                  />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
