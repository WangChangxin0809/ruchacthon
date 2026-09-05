#!/usr/bin/env bash
# Deploy CC Workbench to the Aliyun box as one systemd service on :8787.
#   scripts/deploy/aliyun.sh            # build UI locally, rsync, install, restart
# Needs: ssh key from docs/how-to/connect-to-aliyun-server.md; server prepared
# per docs/how-to/deploy-aliyun.md (node, claude, venv). Never copies secrets:
# /etc/workbench.env on the server is created once with a generated token and
# edited there by a human.
set -euo pipefail
HOST="${WB_HOST:-root@8.140.221.53}"
KEY="${WB_KEY:-$HOME/.ssh/aliyun_8140}"
DEST="${WB_DEST:-/opt/workbench}"
SSH=(ssh -i "$KEY" -o BatchMode=yes "$HOST")
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

(cd "$ROOT/frontend" && npm run build >/dev/null)
rsync -az --delete -e "ssh -i $KEY -o BatchMode=yes" \
  --exclude .git --exclude node_modules --exclude 'backend/data' --exclude '__pycache__' --exclude '.venv' \
  "$ROOT/" "$HOST:$DEST/"

"${SSH[@]}" bash -s <<EOS
set -e
/opt/workbench-venv/bin/pip install -q -r $DEST/backend/requirements.txt
mkdir -p /var/lib/workbench /var/lib/workbench/projects
if [ ! -f /etc/workbench.env ]; then
  cat > /etc/workbench.env <<ENV
WORKBENCH_TOKEN=\$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
WORKBENCH_DATA_DIR=/var/lib/workbench
WORKBENCH_MODEL=claude-sonnet-5
WORKBENCH_MAX_CONCURRENT_RUNS=2
# Claude Code login for the service user. Either run \`claude\` once as root on
# this box, or put the same CLAUDE_CODE_OAUTH_TOKEN here that your local
# ~/.claude/settings.json env block uses. Proxy vars go here too if needed.
#CLAUDE_CODE_OAUTH_TOKEN=
ENV
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
systemctl enable --now workbench >/dev/null 2>&1 || true
systemctl restart workbench
sleep 3
systemctl is-active workbench
curl -s -o /dev/null -w "health %{http_code}\n" http://127.0.0.1:8787/api/auth
EOS
echo "deployed to $HOST:$DEST — token is in /etc/workbench.env on the server (never printed here)"
