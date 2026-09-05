# 0005 — Users, teams and one conversation model for DMs, groups and sessions

Date: 2026-09-05
Status: accepted (batch 1 of [0004, the track-3 design](0004-track3-multi-user-multi-agent.md))

## Context

The workbench was single-user: one shared `WORKBENCH_TOKEN`, and every request
body carried an `author` string the server trusted. Several people sharing one
deployment (the Aliyun demo) could impersonate each other, saw every session,
and had no way to talk privately. A session shared by two people also had no
rule for when the agent should answer, so the model saw everything and replied
to everyone.

## Decision

- **Identity**: `users` with stdlib pbkdf2 passwords, `auth_sessions` holding
  only a sha256 of a `wbs_` bearer (30-day sliding). `WORKBENCH_TOKEN` is
  demoted to a *bootstrap* principal: the first registration and `/api/admin/*`
  only -- never a person, never a later account -- but `/api/admin/*` includes
  password reset, so it stays root-equivalent and lives in the server env
  only. `WORKBENCH_SINGLE_USER=1` keeps the laptop quick start password-free by
  making every request the admin user `local`; the first real registration on
  such a database takes that account over. Handles the machinery uses as
  `author` or as a principal (`local`, `tool`, `room`, `agent`, ...) are
  reserved. `?token=` is accepted only on `/ws` and the artifact-file URL, and
  the access log redacts it.
- **Membership follows the team**: leaving or being removed from a team ends
  every group and work-session membership under it (one `conversation_member
  {removed}` event per row, so open sockets refresh), and a session gate also
  re-checks the project's team. Anything about a locked worker session --
  task instructions, run prompts, artifacts, events -- is for its members;
  a non-member sees only the card (title, status, owner, branch).
- **Authorship is server-derived** (ARCHITECTURE invariant 7): no request body
  carries `author`/`actor`; rows keep a display-name snapshot plus a `user_id`.
- **One deployment = one organisation**: `teams`, `team_members
  (owner|admin|member)`, `invites` as share links. The first registered user is
  admin, owns `默认团队`, and claims every row that predates users; later
  registrants claim legacy rows whose free-text author equals their handle.
- **One conversation model**: `conversations(kind=dm|group|session)` with one
  `conversation_members` table. DM/group messages stay in `chat_messages`
  (legacy `ch_*` channels become groups in place); session messages stay in
  `messages`. Visibility, unread and the WebSocket filter are one rule.
- **The @ rule** lives in the single send path (`RunManager.send_human`): one
  human -> the agent gets everything; several -> only messages that mention it,
  with the skipped backlog replayed under `[Earlier in this conversation, not
  yet shown to you]`; every human line the model sees is prefixed
  `[name (@handle)]` (AO's `[from <id>]` rule with a person instead of an id).
- **Notifications** are one table plus private events (`events.user_id`),
  produced at the spot the underlying event is already emitted.
- **Keys are owned**: `provider_profiles.owner_id/team_id/shared`; a non-admin's
  `credential_ref` is always `profile.<handle>.<name>` (the store is keyed by
  predictable names, so a client-chosen ref would read anybody's), the
  environment-variable fallback is admin-only, and a default profile (own
  prefs, team, global) that is not visible to the person running is skipped.
- **A session has one agent at a time**: feedback, review notes and Room
  notices are delivered to the session's active run, not to the run that
  happened to produce the artifact, so a session never holds two queued runs.

## Consequences

- Every `/api` route needs a login; the deploy health check (`GET /api/auth`)
  stays public. The frontend must move from `author` params and
  `/api/chat/channels*` to the principal and `/api/conversations` (batch 2).
- A worker session is strict (creator + agent; the owner adds people); the
  main session is the project's shared room that team members auto-join.
- A same-workspace claim conflict still escalates to a human, now any member
  of the project's team — hard rule 1 unchanged, its "human" at the team
  boundary.
- The data migration is idempotent and keyed by `settings.schema_version`;
  deploys back up the database and rehearse it on a copy first
  ([deploy-aliyun](../how-to/deploy-aliyun.md) step 3b).

## Rejected

- **Friend requests / contact lists.** A team already says who can see whom;
  a second social graph would be one more thing to keep consistent.
- **An external `/mcp` endpoint** so a colleague's own Claude Code can enter
  the Room. Needs per-run bearers and its own threat model; deferred to an
  ADR of its own.
- **Renaming `chat_messages.channel_id`.** Reusing the legacy `ch_*` id as the
  conversation id avoids rewriting rows and keeps the migration to inserts.
