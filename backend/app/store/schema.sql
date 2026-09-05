-- Single source of truth for AgentRoom state (D2). No jsonl, no in-memory
-- state that isn't a cache of these tables.
--
-- Room (实况文档 §3.1) is a coordination domain equal to a project, not a
-- table of its own -- claims/escalations/decisions below carry project_id
-- instead.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL,
    vcs TEXT NOT NULL CHECK (vcs IN ('git', 'none')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL CHECK (kind IN ('worktree', 'dir')),
    path TEXT NOT NULL,
    branch TEXT,
    base_commit TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    review TEXT NOT NULL DEFAULT 'unreviewed'
        CHECK (review IN ('unreviewed', 'changes_requested', 'approved')),
    merge TEXT NOT NULL DEFAULT 'not_merged'
        CHECK (merge IN ('not_merged', 'merged', 'conflicted', 'not_applicable')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_deps (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id),
    PRIMARY KEY (task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    cc_session_id TEXT,
    title TEXT,
    created_at TEXT NOT NULL
);

-- One row per execution attempt. Retry = new row (attempt+1), never an
-- UPDATE over a prior attempt's status/terminal_reason (D3).
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    session_id TEXT NOT NULL REFERENCES sessions(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    profile_id TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'starting', 'running', 'cancelling',
        'succeeded', 'failed', 'cancelled', 'budget_exhausted', 'interrupted'
    )),
    attempt INTEGER NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    cc_session_id TEXT,
    pid INTEGER,
    terminal_reason TEXT,
    config_snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    kind TEXT NOT NULL,
    version INTEGER NOT NULL,
    ref TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    project_id TEXT NOT NULL,
    path TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('worktree', 'person', 'team')),
    claimed_at TEXT NOT NULL,
    PRIMARY KEY (project_id, path)
);

CREATE TABLE IF NOT EXISTS escalations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    path TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    description TEXT NOT NULL,
    risk_summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected'))
);

-- The AgentRoom MCP layer's own append-only activity log (claim/release/
-- broadcast notes) -- distinct from the unified `events` table above,
-- which is project-scoped and drives the dashboard's run/task stream.
-- Room has no project concept yet (that's the D4 identity-binding round),
-- so this stays a flat log, just moved off room_log.jsonl onto SQLite (D2).
CREATE TABLE IF NOT EXISTS room_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    scope TEXT NOT NULL,
    path TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    note TEXT NOT NULL,
    ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    escalation_id TEXT NOT NULL REFERENCES escalations(id),
    path TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    actor TEXT NOT NULL,
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS room_agents (
    project_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    status TEXT NOT NULL,
    current_task TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, actor_id)
);

CREATE TABLE IF NOT EXISTS previews (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    html TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    endpoint TEXT,
    model TEXT,
    credential_ref TEXT,
    compat_result TEXT,
    created_at TEXT NOT NULL
);

-- Event bus: every event in the system lands here. `seq` is monotonic
-- *within a project* (enforced by store/events.py, not by this schema --
-- SQLite has no native per-partition autoincrement), so a WS/HTTP client
-- can always ask "give me everything after seq N" and get a gapless,
-- non-duplicated answer (§3.3).
CREATE TABLE IF NOT EXISTS events (
    rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    seq INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    task_id TEXT,
    run_id TEXT,
    type TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE (project_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_events_project_seq ON events(project_id, seq);
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
