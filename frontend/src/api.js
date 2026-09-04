const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8787";
const WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8787/ws";

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
