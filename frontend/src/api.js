// Same-origin by default (proxied by Vite's dev server, see vite.config.js)
// so this works both from `localhost` and from behind the cheese preview
// tunnel's subpath. Set VITE_API_BASE/VITE_WS_URL to point at a different
// backend entirely.
const base = import.meta.env.BASE_URL.replace(/\/$/, "");
const API_BASE = import.meta.env.VITE_API_BASE || base;
const WS_URL =
  import.meta.env.VITE_WS_URL ||
  `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}${base}/ws`;

export async function fetchAgents() {
  const r = await fetch(`${API_BASE}/api/agents`);
  return r.json();
}

export async function fetchEscalations() {
  const r = await fetch(`${API_BASE}/api/escalations`);
  return r.json();
}

export async function decide(escalationId, decision, reason, actor) {
  const r = await fetch(`${API_BASE}/api/escalations/${escalationId}/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, reason, actor }),
  });
  return r.json();
}

export function connectWebSocket(onMessage) {
  let ws;
  let closedByUs = false;

  function open() {
    ws = new WebSocket(WS_URL);
    ws.onmessage = (event) => onMessage(JSON.parse(event.data));
    ws.onclose = () => {
      if (!closedByUs) setTimeout(open, 2000); // backend restarts a lot during a hackathon
    };
  }
  open();

  return () => {
    closedByUs = true;
    ws?.close();
  };
}

export { API_BASE, WS_URL };
