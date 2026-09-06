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

"${SSH[@]}" bash "$DEST/scripts/deploy/remote-install.sh" "$DEST"
echo "deployed to $HOST:$DEST — token is in /etc/workbench.env on the server (never printed here)"
