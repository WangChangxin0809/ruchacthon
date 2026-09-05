// Same-origin by default (Vite proxies /api and /ws to the backend, see
// vite.config.js). VITE_API_BASE / VITE_WS_URL point at another backend.
const base = import.meta.env.BASE_URL.replace(/\/$/, "");
export const API_BASE = import.meta.env.VITE_API_BASE || base;
const WS_URL =
  import.meta.env.VITE_WS_URL ||
  `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}${base}/ws`;

// The session token from POST /api/auth/login|register. It also arrives as
// ?token= (an invite mail, or the bootstrap token a server prints once), and
// is then saved so the query string can be cleaned off the address bar.
const KEY = "wb.token";
let onUnauthorized = null;
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }
export function getToken() {
  try {
    const q = new URLSearchParams(window.location.search).get("token");
    if (q) { localStorage.setItem(KEY, q); return q; }
    return localStorage.getItem(KEY) || "";
  } catch { return ""; }
}
export function setToken(t) {
  try { if (t) localStorage.setItem(KEY, t); else localStorage.removeItem(KEY); } catch { /* private mode */ }
}

async function req(path, opts = {}) {
  const r = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}) },
    ...opts,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401) {
    // the token is gone or expired: back to the login screen, no prompt box
    setToken("");
    onUnauthorized?.();
    throw new Error("需要登录");
  }
  if (r.status === 204) return {};
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(typeof body.detail === "string" ? body.detail : `${r.status} ${r.statusText}`);
    err.status = r.status;
    throw err;
  }
  return body;
}
const get = (p) => req(p);
const post = (p, body) => req(p, { method: "POST", body });
const patch = (p, body) => req(p, { method: "PATCH", body });
const del = (p) => req(p, { method: "DELETE" });
const q = (o) => {
  const s = new URLSearchParams(Object.entries(o || {}).filter(([, v]) => v !== undefined && v !== null && v !== "")).toString();
  return s ? `?${s}` : "";
};

export const api = {
  // ---- who you are
  authState: () => get("/api/auth"),
  register: (body) => post("/api/auth/register", body),
  login: (handle, password) => post("/api/auth/login", { handle, password }),
  logout: () => post("/api/auth/logout", {}),
  me: () => get("/api/auth/me"),
  patchMe: (body) => patch("/api/auth/me", body),
  users: (params) => get(`/api/users${q(params)}`),

  // ---- teams and invites
  teams: () => get("/api/teams"),
  createTeam: (name) => post("/api/teams", { name }),
  team: (id) => get(`/api/teams/${id}`),
  patchTeam: (id, body) => patch(`/api/teams/${id}`, body),
  addTeamMember: (id, body) => post(`/api/teams/${id}/members`, body),
  setTeamRole: (id, userId, role) => patch(`/api/teams/${id}/members/${userId}`, { role }),
  removeTeamMember: (id, userId) => del(`/api/teams/${id}/members/${userId}`),
  invites: (id) => get(`/api/teams/${id}/invites`),
  createInvite: (id, role) => post(`/api/teams/${id}/invites`, { role }),
  revokeInvite: (teamId, inviteId) => del(`/api/teams/${teamId}/invites/${inviteId}`),
  invite: (token) => get(`/api/invites/${token}`),
  requestJoin: (sessionId, note = "") => post(`/api/sessions/${sessionId}/join-request`, { note }),
  acceptInvite: (token) => post(`/api/invites/${token}/accept`, {}),

  // ---- notifications
  notifications: (params) => get(`/api/notifications${q(params)}`),
  readNotifications: (body) => post("/api/notifications/read", body),

  // ---- conversations
  conversations: (params) => get(`/api/conversations${q(params)}`),
  createGroup: (body) => post("/api/conversations", { kind: "group", ...body }),
  openDm: (userId) => post("/api/conversations/dm", { user_id: userId }),
  conversation: (id) => get(`/api/conversations/${id}`),
  patchConversation: (id, body) => patch(`/api/conversations/${id}`, body),
  addConvMember: (id, body) => post(`/api/conversations/${id}/members`, body),
  removeConvMember: (id, userId) => del(`/api/conversations/${id}/members/${userId}`),
  readConversation: (id) => post(`/api/conversations/${id}/read`, {}),
  convMessages: (id) => get(`/api/conversations/${id}/messages`),
  convSend: (id, text) => post(`/api/conversations/${id}/messages`, { text }),

  // ---- agent definitions
  agents: (params) => get(`/api/agents${q(params)}`),
  createAgent: (body) => post("/api/agents", body),
  patchAgent: (id, body) => patch(`/api/agents/${id}`, body),
  deleteAgent: (id) => del(`/api/agents/${id}`),
  setAgentDefault: (id, teamId) => post(`/api/agents/${id}/default`, { team_id: teamId }),

  // ---- projects, sessions, tasks
  health: () => get("/api/health"),
  overview: () => get("/api/overview"),
  settings: () => get("/api/settings"),
  putSettings: (body) => req("/api/settings", { method: "PUT", body }),
  ccStatus: () => get("/api/cc/status"),
  ccLoginToken: (oauth_token) => post("/api/cc/login-token", { oauth_token }),
  ccLoginTokenDelete: () => del("/api/cc/login-token"),
  projects: () => get("/api/projects"),
  projectCandidates: () => get("/api/projects/candidates"),
  createProject: (body) => post("/api/projects", body),
  project: (id) => get(`/api/projects/${id}`),
  mainSession: (pid) => get(`/api/projects/${pid}/main-session`),
  sessions: (pid) => get(`/api/projects/${pid}/sessions`),
  session: (sid) => get(`/api/sessions/${sid}`),
  patchSession: (sid, body) => patch(`/api/sessions/${sid}`, body),
  messages: (sid) => get(`/api/sessions/${sid}/messages`),
  send: (sid, text, profile_id, model) => post(`/api/sessions/${sid}/messages`, { text, profile_id, model }),
  tasks: (pid) => get(`/api/projects/${pid}/tasks`),
  task: (id) => get(`/api/tasks/${id}`),
  createTask: (pid, body) => post(`/api/projects/${pid}/tasks`, body),
  retryTask: (id, prompt) => post(`/api/tasks/${id}/retry`, { prompt }),
  cancelTask: (id) => post(`/api/tasks/${id}/cancel`, {}),
  reviewTask: (id, verdict, note) => post(`/api/tasks/${id}/review`, { verdict, note }),
  mergeTask: (id) => post(`/api/tasks/${id}/merge`, {}),
  taskDiff: (id) => get(`/api/tasks/${id}/diff`),
  cancelRun: (id) => post(`/api/runs/${id}/cancel`, {}),

  // ---- artifacts, room
  artifacts: (pid) => get(`/api/projects/${pid}/artifacts`),
  artifact: (id) => get(`/api/artifacts/${id}`),
  artifactFileUrl: (id) => `${API_BASE}/api/artifacts/${id}/file${getToken() ? `?token=${encodeURIComponent(getToken())}` : ""}`,
  feedback: (id, text, verdict) => post(`/api/artifacts/${id}/feedback`, { text, verdict }),
  stopDevserver: (id) => post(`/api/devservers/${id}/stop`, {}),
  devserverLogs: (id) => get(`/api/devservers/${id}/logs`),
  room: (pid) => get(`/api/projects/${pid}/room`),
  decide: (id, decision, reason) => post(`/api/decisions/${id}/decide`, { decision, reason }),

  // ---- model providers
  profiles: () => get("/api/profiles"),
  presets: () => get("/api/presets"),
  createProfile: (body) => post("/api/profiles", body),
  patchProfile: (id, body) => patch(`/api/profiles/${id}`, body),
  deleteProfile: (id) => del(`/api/profiles/${id}`),
  deleteProfileSecret: (id) => del(`/api/profiles/${id}/secret`),
  checkProfile: (id) => post(`/api/profiles/${id}/check`, {}),
  deepCheckProfile: (id) => post(`/api/profiles/${id}/deep-check`, {}),
  profileModels: (id) => post(`/api/profiles/${id}/models`, {}),
  discovery: (pid) => get(`/api/cc/discovery${q({ project_id: pid })}`),
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
