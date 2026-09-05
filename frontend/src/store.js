// The browser-side mirror of server state. Lists (tasks, artifacts, room,
// conversations) are refetched on the events that touch them -- cheap, and it
// means a missed event can never leave a stale list. Messages and stream
// deltas are applied incrementally because those are the ones a user watches.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, connectEvents, setToken, setUnauthorizedHandler } from "./api";

const LIST_EVENTS = {
  task: "tasks", run_status: "tasks", run_waiting: "tasks", run_blocked: "tasks", run_unblocked: "tasks",
  artifact: "artifacts", artifact_feedback: "artifacts", devserver: "artifacts",
  claim: "room", claims_released: "room", claim_expired: "room", decision: "room", room_message: "room", handoff: "room",
  session: "sessions", project: "projects", cc_status: "cc",
  conversation: "conversations", conversation_member: "conversations",
  notification: "notifications", team: "me", team_member: "me", invite: "me",
  agent_definition: "agents",
};
// Only these need no project selected.
const GLOBAL = ["projects", "cc", "me", "conversations", "notifications", "agents"];

export function useWorkbench() {
  const params = new URLSearchParams(window.location.search);
  const [authState, setAuthState] = useState("loading");   // loading | anon | ok
  const [me, setMe] = useState(null);
  const [teamId, setTeamIdState] = useState(() => safeGet("wb.team"));
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(() => params.get("p") || safeGet("wb.project"));
  const [tasks, setTasks] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [artifacts, setArtifacts] = useState([]);
  const [room, setRoom] = useState({ claims: [], pending_decisions: [], messages: [], overlaps: [], members: [] });
  const [messages, setMessages] = useState([]);
  const [sessionId, setSessionId] = useState(() => params.get("s"));
  const [streams, setStreams] = useState({});      // run_id -> partial assistant text
  const [runStatus, setRunStatus] = useState({});  // run_id -> latest status payload
  const [wsStatus, setWsStatus] = useState("connecting");
  const [health, setHealth] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [agents, setAgents] = useState({ items: [], defaults: {} });
  const [cc, setCc] = useState(null);
  const [lastEvent, setLastEvent] = useState(null);
  const sessionRef = useRef(null);
  sessionRef.current = sessionId;
  const pending = useRef({});
  const teamRef = useRef(teamId);
  teamRef.current = teamId;

  const setTeamId = useCallback((id) => { setTeamIdState(id); safeSet("wb.team", id || ""); }, []);

  const refetch = useCallback((what) => {
    if (!projectId && !GLOBAL.includes(what)) return;
    clearTimeout(pending.current[what]);
    pending.current[what] = setTimeout(async () => {
      try {
        if (what === "projects") setProjects(await api.projects());
        if (what === "cc") setCc(await api.ccStatus());
        if (what === "tasks") setTasks(await api.tasks(projectId));
        if (what === "artifacts") setArtifacts(await api.artifacts(projectId));
        if (what === "room") setRoom(await api.room(projectId));
        if (what === "sessions") setSessions(await api.sessions(projectId));
        if (what === "conversations") setConversations(await api.conversations({ team_id: teamRef.current }));
        if (what === "notifications") setNotifications((await api.notifications({ limit: 60 })).items || []);
        if (what === "agents") setAgents(await api.agents({ team_id: teamRef.current }));
        if (what === "me") {
          const m = await api.me();
          setMe(m);
          if (!teamRef.current && m.teams?.length) setTeamId(m.teams[0].id);
        }
      } catch (e) {
        console.warn("refetch", what, e);
      }
    }, 150);
  }, [projectId, setTeamId]);

  // ---- sign in / out ------------------------------------------------------
  const signOut = useCallback(async () => {
    try { await api.logout(); } catch { /* the token may already be dead */ }
    setToken("");
    setMe(null); setAuthState("anon"); setTeamId(null);
    setProjects([]); setConversations([]); setNotifications([]);
  }, [setTeamId]);

  const signIn = useCallback(async (token) => {
    if (token) setToken(token);          // omitted when the stored token is already good
    // ?token= and ?invite= have done their job; a reload should not redo them
    const url = new URL(window.location.href);
    if (url.searchParams.has("token") || url.searchParams.has("invite")) {
      url.searchParams.delete("token"); url.searchParams.delete("invite");
      window.history.replaceState(null, "", url.pathname + url.search);
    }
    const m = await api.me();
    setMe(m);
    if (m.teams?.length && !teamRef.current) setTeamId(m.teams[0].id);
    setAuthState("ok");
  }, [setTeamId]);

  useEffect(() => {
    setUnauthorizedHandler(() => { setMe(null); setAuthState("anon"); });
    (async () => {
      try {
        const st = await api.authState();
        // the deploy token is a principal but not a person: it may create the
        // first account and nothing else, so it stays on the login screen
        if (st.me && !st.me.bootstrap) await signIn();
        else setAuthState("anon");
      } catch {
        setAuthState("anon");
      }
    })();
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  // ---- data ---------------------------------------------------------------
  useEffect(() => {
    if (authState !== "ok") return;
    api.health().then(setHealth).catch(() => setHealth({ ok: false }));
    GLOBAL.forEach(refetch);
  }, [authState, refetch]);

  useEffect(() => { if (authState === "ok") ["conversations", "agents"].forEach(refetch); }, [teamId, authState, refetch]);

  useEffect(() => {
    if (authState !== "ok") return undefined;
    if (projectId) {
      safeSet("wb.project", projectId);
      ["tasks", "artifacts", "room", "sessions"].forEach(refetch);
    }
    const close = connectEvents(projectId, (ev) => {
      setLastEvent(ev);
      const list = LIST_EVENTS[ev.type];
      if (list) refetch(list);
      if (ev.type === "message" && ev.payload?.conversation_id) refetch("conversations");
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
    }, setWsStatus);
    return close;
  }, [projectId, refetch, authState]);

  useEffect(() => {
    if (!sessionId || authState !== "ok") return;
    setMessages([]);
    setStreams({});
    api.messages(sessionId).then(setMessages).catch(() => setMessages([]));
  }, [sessionId, authState]);

  const unread = notifications.filter((n) => !n.read).length;

  return {
    authState, me, signIn, signOut, teamId, setTeamId,
    projects, projectId, setProjectId, tasks, sessions, artifacts, room, messages, sessionId, setSessionId, streams, runStatus,
    wsStatus, health, refetch, conversations, notifications, unread, agents, cc, lastEvent,
  };
}

function safeGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function safeSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }
