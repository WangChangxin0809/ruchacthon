// The browser-side mirror of server state. Lists (tasks, artifacts, room)
// are refetched on the events that touch them -- cheap, and it means a
// missed event can never leave a stale list. Messages and stream deltas are
// applied incrementally because those are the ones a user is watching.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, connectEvents } from "./api";

const LIST_EVENTS = {
  task: "tasks", run_status: "tasks", run_waiting: "tasks", run_blocked: "tasks", run_unblocked: "tasks",
  artifact: "artifacts", artifact_feedback: "artifacts", devserver: "artifacts",
  claim: "room", claims_released: "room", claim_expired: "room", decision: "room", room_message: "room", handoff: "room",
  session: "sessions", project: "projects",
};

export function useWorkbench() {
  const [projects, setProjects] = useState([]);
  // ?p=<project id>&s=<session id> deep-links a board card or a shared session
  const params = new URLSearchParams(window.location.search);
  const [projectId, setProjectId] = useState(() => params.get("p") || safeGet("wb.project"));
  const [tasks, setTasks] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [artifacts, setArtifacts] = useState([]);
  const [room, setRoom] = useState({ claims: [], pending_decisions: [], messages: [], overlaps: [], members: [] });
  const [messages, setMessages] = useState([]);
  const [sessionId, setSessionId] = useState(() => params.get("s"));
  const [streams, setStreams] = useState({}); // run_id -> partial assistant text
  const [runStatus, setRunStatus] = useState({}); // run_id -> latest status payload
  const [wsStatus, setWsStatus] = useState("connecting");
  const [health, setHealth] = useState(null);
  const sessionRef = useRef(null);
  sessionRef.current = sessionId;
  const pending = useRef({});

  const refetch = useCallback(
    (what) => {
      if (!projectId && what !== "projects") return;
      clearTimeout(pending.current[what]);
      pending.current[what] = setTimeout(async () => {
        try {
          if (what === "projects") setProjects(await api.projects());
          if (what === "tasks") setTasks(await api.tasks(projectId));
          if (what === "artifacts") setArtifacts(await api.artifacts(projectId));
          if (what === "room") setRoom(await api.room(projectId));
          if (what === "sessions") setSessions(await api.sessions(projectId));
        } catch (e) {
          console.warn("refetch", what, e);
        }
      }, 150);
    },
    [projectId],
  );

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ ok: false }));
    refetch("projects");
  }, [refetch]);

  useEffect(() => {
    if (!projectId) return;
    safeSet("wb.project", projectId);
    ["tasks", "artifacts", "room", "sessions"].forEach(refetch);
    api.mainSession(projectId).then((s) => setSessionId((cur) => cur || s.id));
    const close = connectEvents(
      projectId,
      (ev) => {
        const list = LIST_EVENTS[ev.type];
        if (list) refetch(list);
        if (ev.type === "run_status") {
          setRunStatus((m) => ({ ...m, [ev.run_id]: ev.payload }));
          if (["succeeded", "failed", "cancelled", "interrupted", "exhausted"].includes(ev.payload.status)) {
            setStreams((s) => { const n = { ...s }; delete n[ev.run_id]; return n; });
          }
        }
        if (ev.session_id && ev.session_id === sessionRef.current) {
          if (ev.type === "message") {
            setMessages((ms) => (ms.some((m) => m.id === ev.payload.id) ? ms : [...ms, ev.payload]));
            if (ev.payload.role === "assistant") setStreams((s) => ({ ...s, [ev.run_id]: "" }));
          }
          if (ev.type === "stream_delta") setStreams((s) => ({ ...s, [ev.run_id]: (s[ev.run_id] || "") + ev.payload.text }));
        }
        if (ev.type === "server_started") ["tasks", "artifacts", "room", "sessions"].forEach(refetch);
      },
      setWsStatus,
    );
    return close;
  }, [projectId, refetch]);

  useEffect(() => {
    if (!sessionId) return;
    setMessages([]);
    setStreams({});
    api.messages(sessionId).then(setMessages).catch(() => setMessages([]));
  }, [sessionId]);

  return {
    projects, projectId, setProjectId, tasks, sessions, artifacts, room, messages, sessionId, setSessionId, streams, runStatus,
    wsStatus, health, refetch,
  };
}

function safeGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function safeSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }
