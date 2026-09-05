# Deploy the workbench to the Aliyun server

One process, one port: the backend serves the built UI and runs Claude Code
workers on the server. Server access: [connect-to-aliyun-server.md](connect-to-aliyun-server.md).

1. Prepare the box once (2 GB swap, node 20, `claude`, a venv):

       ssh -i ~/.ssh/aliyun_8140 root@8.140.221.53 'bash -s' < scripts/deploy/prep-aliyun.sh   # docs-runnable: ignore

   Criterion: `claude --version` and `node --version` print on the server.

2. Deploy (builds the UI locally, rsyncs, installs deps, writes the systemd unit):

       scripts/deploy/aliyun.sh   # docs-runnable: ignore

   Criterion: the script ends with `active` and `health 200`.

3. Give the service a Claude Code login — the one thing the script will not do
   for you. On the server, edit `/etc/workbench.env` and set
   `CLAUDE_CODE_OAUTH_TOKEN=` (copy the value from your own
   `~/.claude/settings.json` `env` block), or run `claude` interactively as
   root once. Then `systemctl restart workbench`.

   Criterion: in the UI, ⚙ → a `claude_code_default` profile → 兼容性检查 passes.

3b. Before deploying a build that changes the schema (any bump of
   `SCHEMA_VERSION` in `backend/app/db.py`): checkpoint, back up, and rehearse
   the migration on a copy — the migration is idempotent, the backup is what
   makes a bad build reversible.

       ssh -i ~/.ssh/aliyun_8140 root@8.140.221.53 'sqlite3 /var/lib/workbench/workbench.db "PRAGMA wal_checkpoint(TRUNCATE)" && cp /var/lib/workbench/workbench.db /var/lib/workbench/workbench.db.bak-$(date +%F)'   # docs-runnable: ignore
       scp -i ~/.ssh/aliyun_8140 root@8.140.221.53:/var/lib/workbench/workbench.db /tmp/wb-rehearsal/workbench.db   # docs-runnable: ignore
       WORKBENCH_DATA_DIR=/tmp/wb-rehearsal python3 -c 'import sys; sys.path.insert(0, "backend"); from app.db import Database; d = Database(); print(d.setting("schema_version"), d.one("SELECT COUNT(*) AS n FROM conversations"))'   # docs-runnable: ignore

   Criterion: the last command prints the current schema version and a
   conversation count without a traceback; `workbench.db.bak-<date>` exists on
   the server.

4. Open TCP 8787 in the Aliyun security group for your IP, then browse to
   `http://8.140.221.53:8787/?token=<WORKBENCH_TOKEN>` — the value is in
   `/etc/workbench.env` on the server. **The first account needs that token.**
   A box is reachable from the moment it restarts, so without the gate the
   first stranger to find the port would become its administrator; the login
   screen says so and shows the URL to use. On a laptop, where no
   `WORKBENCH_TOKEN` is set, the first registration stays open.

   That first account becomes the administrator and owns `默认团队`, and all
   data from before the upgrade is claimed by it (if the data dir was last
   used with `WORKBENCH_SINGLE_USER=1`, that person takes over the `local`
   account instead). Everyone else joins through an invite link from
   ⚙ → 团队.

   Criterion: `curl -s http://<host>:8787/api/auth` on a fresh instance shows
   `"needs_deploy_token": true`, and a registration without the token is 403.

   `WORKBENCH_TOKEN` in `/etc/workbench.env` is now only a *bootstrap* token:
   it can register the first user and call `/api/admin/*` (for example
   `POST /api/admin/users/<id>/reset-password` when the admin forgets their
   password); it cannot read or post as a person, nor create accounts once
   one exists. Password reset makes it root-equivalent all the same: keep it
   in `/etc/workbench.env` only, never in a browser or a chat.

5. Register a project: the path must exist on the server (clone your repo under
   `/var/lib/workbench/projects/` first).

Ops: `journalctl -u workbench -f` for logs; data in `/var/lib/workbench`
(`workbench.db`, worktrees, artifacts). To rotate the token, edit the env file
and restart.
