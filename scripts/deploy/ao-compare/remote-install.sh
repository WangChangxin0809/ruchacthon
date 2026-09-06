#!/usr/bin/env bash
# Runs ON the server. Wires the rsynced AO binary + web build into nginx and
# systemd. Idempotent: re-running only restarts what changed.
set -euo pipefail
DEST="${1:-/opt/ao-compare}"
PUBLIC_ORIGIN="${2:?public origin, e.g. http://1.2.3.4:8788}"

ln -sf "$DEST/ao" /usr/local/bin/ao
chmod +x "$DEST/ao"
mkdir -p /var/lib/ao-compare /var/lib/ao-compare/projects

if [ ! -f /etc/ao-compare.env ]; then
  cat > /etc/ao-compare.env <<ENV
AO_PORT=3001
AO_DATA_DIR=/var/lib/ao-compare
AO_ALLOWED_ORIGINS=$PUBLIC_ORIGIN
AO_TELEMETRY_EVENTS=0
AO_TELEMETRY_METRICS=0
ENV
  chmod 600 /etc/ao-compare.env
fi

# One basic-auth user; the password is generated once and kept root-only.
if [ ! -f /etc/nginx/ao-compare.htpasswd ]; then
  # cut, not head: head closing the pipe early makes tr die of SIGPIPE under pipefail
  PASS="$(head -c 48 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | cut -c1-14)"
  htpasswd -cb /etc/nginx/ao-compare.htpasswd ao "$PASS" >/dev/null
  printf 'user: ao\npassword: %s\n' "$PASS" > /root/ao-compare-credentials.txt
  chmod 600 /root/ao-compare-credentials.txt
fi

install -m 644 "$DEST/deploy/nginx.conf" /etc/nginx/sites-available/ao-compare
ln -sf /etc/nginx/sites-available/ao-compare /etc/nginx/sites-enabled/ao-compare
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

install -m 644 "$DEST/deploy/ao-compare.service" /etc/systemd/system/ao-compare.service
systemctl daemon-reload
systemctl enable --now ao-compare
systemctl restart ao-compare
for i in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:3001/healthz >/dev/null 2>&1; then break; fi
  sleep 1
done
echo "ao-compare: $(systemctl is-active ao-compare)"
echo "daemon health: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3001/healthz)"
echo "web: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8788/)  (401 = auth is on)"
