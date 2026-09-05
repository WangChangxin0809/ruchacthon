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

4. Open TCP 8787 in the Aliyun security group for your IP, then browse to
   `http://8.140.221.53:8787/?token=…` with the WORKBENCH_TOKEN value from `/etc/workbench.env`.
   The token is remembered by the browser; every teammate needs it.

5. Register a project: the path must exist on the server (clone your repo under
   `/var/lib/workbench/projects/` first).

Ops: `journalctl -u workbench -f` for logs; data in `/var/lib/workbench`
(`workbench.db`, worktrees, artifacts). To rotate the token, edit the env file
and restart.
