// Same-origin by default (Vite proxies /api and /ws to the backend, see
// vite.config.js). VITE_API_BASE / VITE_WS_URL point at another backend.
const base = import.meta.env.BASE_URL.replace(/\/$/, "");
export const API_BASE = import.meta.env.VITE_API_BASE || base;
const WS_URL =
  import.meta.env.VITE_WS_URL ||
  `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}${base}/ws`;

// A deployed workbench requires a token (WORKBENCH_TOKEN on the server);
// ?token= in the URL or a saved one. A 401 asks the user for it once.
export function getToken() { try { return new URLSearchParams(window.location.search).get("token") || localStorage.getItem("wb.token") || ""; } catch { return ""; } }
export function setToken(t) { try { localStorage.setItem("wb.token", t); } catch { /* private mode */ } }

async function req(path, opts = {}) {
  const r = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}) },
    ...opts,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401) {
    const t = window.prompt("这个工作台需要访问令牌（服务器上的 WORKBENCH_TOKEN）：");
    if (t) { setToken(t); return req(path, opts); }
    throw new Error("需要访问令牌");
  }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `${r.status} ${r.statusText}`);
  return body;
}
const get = (p) => req(p);
const post = (p, body) => req(p, { method: "POST", body });

export const api = {
  health: () => get("/api/health"),
  overview: () => get("/api/overview"),
  settings: () => get("/api/settings"),
  putSettings: (body) => req("/api/settings", { method: "PUT", body }),
  ccStatus: () => get("/api/cc/status"),
  ccLoginToken: (oauth_token) => post("/api/cc/login-token", { oauth_token }),
  ccLoginTokenDelete: () => req("/api/cc/login-token", { method: "DELETE" }),
  channels: () => get("/api/chat/channels"),
  createChannel: (name, author) => post("/api/chat/channels", { name, author }),
  chatMessages: (id) => get(`/api/chat/channels/${id}/messages`),
  chatPost: (id, text, author) => post(`/api/chat/channels/${id}/messages`, { text, author }),
  projects: () => get("/api/projects"),
  projectCandidates: () => get("/api/projects/candidates"),
  createProject: (body) => post("/api/projects", body),
  project: (id) => get(`/api/projects/${id}`),
  mainSession: (pid) => get(`/api/projects/${pid}/main-session`),
  sessions: (pid) => get(`/api/projects/${pid}/sessions`),
  session: (sid) => get(`/api/sessions/${sid}`),
  messages: (sid) => get(`/api/sessions/${sid}/messages`),
  send: (sid, text, author, profile_id, model) => post(`/api/sessions/${sid}/messages`, { text, author, profile_id, model }),
  tasks: (pid) => get(`/api/projects/${pid}/tasks`),
  task: (id) => get(`/api/tasks/${id}`),
  createTask: (pid, body) => post(`/api/projects/${pid}/tasks`, body),
  retryTask: (id, prompt) => post(`/api/tasks/${id}/retry`, { prompt }),
  cancelTask: (id) => post(`/api/tasks/${id}/cancel`, {}),
  reviewTask: (id, verdict, note, author) => post(`/api/tasks/${id}/review`, { verdict, note, author }),
  mergeTask: (id) => post(`/api/tasks/${id}/merge`, {}),
  taskDiff: (id) => get(`/api/tasks/${id}/diff`),
  cancelRun: (id) => post(`/api/runs/${id}/cancel`, {}),
  artifacts: (pid) => get(`/api/projects/${pid}/artifacts`),
  artifact: (id) => get(`/api/artifacts/${id}`),
  artifactFileUrl: (id) => `${API_BASE}/api/artifacts/${id}/file${getToken() ? `?token=${encodeURIComponent(getToken())}` : ""}`,
  feedback: (id, text, verdict, author) => post(`/api/artifacts/${id}/feedback`, { text, verdict, author }),
  stopDevserver: (id) => post(`/api/devservers/${id}/stop`, {}),
  devserverLogs: (id) => get(`/api/devservers/${id}/logs`),
  room: (pid) => get(`/api/projects/${pid}/room`),
  decide: (id, decision, reason, actor) => post(`/api/decisions/${id}/decide`, { decision, reason, actor }),
  profiles: () => get("/api/profiles"),
  createProfile: (body) => post("/api/profiles", body),
  patchProfile: (id, body) => req(`/api/profiles/${id}`, { method: "PATCH", body }),
  deleteProfile: (id) => req(`/api/profiles/${id}`, { method: "DELETE" }),
  deleteProfileSecret: (id) => req(`/api/profiles/${id}/secret`, { method: "DELETE" }),
  checkProfile: (id) => post(`/api/profiles/${id}/check`, {}),
  discovery: (pid) => get(`/api/cc/discovery${pid ? `?project_id=${pid}` : ""}`),
};

// One socket; reconnects with the last seq it saw so nothing is missed or
// repeated. `onEvent` gets every event (persisted ones carry `seq`).
export function connectEvents(projectId, onEvent, onStatus) {
  let ws;
  let closed = false;
  let lastSeq = -1;
  let timer;
  function open() {
    const url = `${WS_URL}?since=${lastSeq}${projectId ? `&project_id=${projectId}` : ""}${getToken() ? `&token=${encodeURIComponent(getToken())}` : ""}`;
    ws = new WebSocket(url);
    ws.onopen = () => onStatus("connected");
    ws.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      if (ev.seq) {
        if (ev.seq <= lastSeq) return; // already seen
        lastSeq = ev.seq;
      }
      onEvent(ev);
    };
    ws.onclose = () => {
      if (closed) return;
      onStatus("reconnecting");
      timer = setTimeout(open, 1500);
    };
    ws.onerror = () => ws.close();
  }
  open();
  return () => {
    closed = true;
    clearTimeout(timer);
    ws?.close();
  };
}
