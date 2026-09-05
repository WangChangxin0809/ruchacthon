#!/usr/bin/env bash
# Runs ON the server after rsync (called by aliyun.sh): deps, env file
# (created once, never overwritten), systemd unit, restart, health.
set -euo pipefail
DEST="${1:-/opt/workbench}"
/opt/workbench-venv/bin/pip install -q -r "$DEST/backend/requirements.txt"
mkdir -p /var/lib/workbench/projects
if [ ! -f /etc/workbench.env ]; then
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  {
    echo "WORKBENCH_TOKEN=$TOKEN"
    echo "WORKBENCH_DATA_DIR=/var/lib/workbench"
    echo "WORKBENCH_MODEL=claude-sonnet-5"
    echo "WORKBENCH_MAX_CONCURRENT_RUNS=2"
    echo "# Claude Code login for the service: run 'claude' once as root on this box,"
    echo "# or set the same CLAUDE_CODE_OAUTH_TOKEN your local ~/.claude/settings.json env uses."
    echo "#CLAUDE_CODE_OAUTH_TOKEN="
  } > /etc/workbench.env
  chmod 600 /etc/workbench.env
fi
cat > /etc/systemd/system/workbench.service <<UNIT
[Unit]
Description=CC Workbench
After=network.target

[Service]
EnvironmentFile=/etc/workbench.env
Environment=HOME=/root
WorkingDirectory=$DEST
ExecStart=/opt/workbench-venv/bin/python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8787
Restart=on-failure
KillMode=mixed

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable workbench >/dev/null 2>&1 || true
systemctl restart workbench
sleep 4
echo "service: $(systemctl is-active workbench)"
curl -s -o /dev/null -w "health %{http_code}\n" http://127.0.0.1:8787/api/auth
