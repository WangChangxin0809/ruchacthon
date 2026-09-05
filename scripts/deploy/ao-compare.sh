#!/usr/bin/env bash
# Deploy Agent Orchestrator (AO) next to CC Workbench for a side-by-side look.
#
# Expects an AO checkout that has already been built:
#   $AO_SRC/ao-linux-amd64          go build of backend/cmd/ao (CGO_ENABLED=0 GOOS=linux)
#   $AO_SRC/frontend/dist-web/      vite build of the renderer with
#                                   VITE_AO_API_BASE_URL=same-origin and the
#                                   renderer-same-origin.patch applied
# See docs/how-to/compare-with-ao.md for the build steps.
set -euo pipefail
AO_SRC="${AO_SRC:?path to the agent-orchestrator checkout}"
HOST="${WB_HOST:-root@8.140.221.53}"
KEY="${WB_KEY:-$HOME/.ssh/aliyun_8140}"
DEST="${AO_DEST:-/opt/ao-compare}"
PUBLIC_ORIGIN="${AO_PUBLIC_ORIGIN:-http://8.140.221.53:8788}"
SSH=(ssh -i "$KEY" -o BatchMode=yes "$HOST")
HERE="$(cd "$(dirname "$0")" && pwd)"

[ -x "$AO_SRC/ao-linux-amd64" ] || { echo "missing $AO_SRC/ao-linux-amd64"; exit 1; }
[ -f "$AO_SRC/frontend/dist-web/index.html" ] || { echo "missing $AO_SRC/frontend/dist-web/index.html"; exit 1; }

"${SSH[@]}" mkdir -p "$DEST/web" "$DEST/deploy"
rsync -az -e "ssh -i $KEY -o BatchMode=yes" "$AO_SRC/ao-linux-amd64" "$HOST:$DEST/ao"
rsync -az --delete -e "ssh -i $KEY -o BatchMode=yes" "$AO_SRC/frontend/dist-web/" "$HOST:$DEST/web/"
rsync -az -e "ssh -i $KEY -o BatchMode=yes" "$HERE/ao-compare/" "$HOST:$DEST/deploy/"
"${SSH[@]}" bash "$DEST/deploy/remote-install.sh" "$DEST" "$PUBLIC_ORIGIN"
echo "AO is at $PUBLIC_ORIGIN — credentials in /root/ao-compare-credentials.txt on the server"
