# Put Agent Orchestrator next to CC Workbench for comparison

- **Covers**: how the AO viewing copy on the Aliyun box was built and wired,
  what is patched, and what does not work in that copy.
- **Does not cover**: running AO for real work. AO ships as a desktop app;
  this is a browser-served copy for looking at its UX side by side with ours.

## What runs where

| Piece | Where | Notes |
|---|---|---|
| AO Go daemon (`ao daemon`) | `systemd` unit `ao-compare`, loopback `:3001` | data in `/var/lib/ao-compare`, env in `/etc/ao-compare.env`, telemetry off |
| AO renderer (browser build) | `/opt/ao-compare/web`, served by nginx on `:8788` | basic auth; password in `/root/ao-compare-credentials.txt` |
| nginx | proxies `/api/` (REST + SSE, buffering off) and `/mux` (terminal WebSocket) to the daemon | config in `scripts/deploy/ao-compare/nginx.conf` |
| CC Workbench | unchanged on `:8787` | |

Open the security group for `8788/tcp` the same way `8787` was opened.

## Build (on a machine with Go ≥ 1.25.7 and Node 20)

```bash
git clone --depth 1 https://github.com/Untrivial-ai/agent-orchestrator.git ao
cd ao && git apply /path/to/ruchacthon/scripts/deploy/ao-compare/renderer-same-origin.patch
(cd backend && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o ../ao-linux-amd64 ./cmd/ao)
cd frontend
ELECTRON_SKIP_BINARY_DOWNLOAD=1 PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci --ignore-scripts
VITE_AO_API_BASE_URL=same-origin npx vite build --config vite.renderer.config.ts --outDir dist-web
cd /path/to/ruchacthon && AO_SRC=/path/to/ao ./scripts/deploy/ao-compare.sh
```

If a corporate HTTP proxy is in the environment and unreachable, prefix the
Go and npm steps with `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy`.

## The patch

AO's renderer normally lives inside Electron: a preload bridge tells it the
daemon's port and it talks to `http://127.0.0.1:<port>` directly. Three small
changes make the same bundle work from a browser behind a reverse proxy:

- `api-client.ts`: `VITE_AO_API_BASE_URL=same-origin` means "relative URLs".
- `bridge.ts`: without Electron, report the daemon as `ready` instead of
  "preload is not available" so the startup gate opens.
- `daemon-status.ts`: do not overwrite the base URL with `127.0.0.1:<port>`.

Nothing in the daemon is patched. The daemon keeps its loopback-only bind; the
browser's `Origin` is allowed through `AO_ALLOWED_ORIGINS`.

## What works / what does not in this copy

Works: the board and its columns, project add/remove, spawning sessions, the
session view (terminal over `/mux`, chat mode), file changes, settings.
Requires a Claude Code login on the server, which is deliberately absent, so
sessions can be created but the agent will not answer until that login exists.

Does not work: everything that needs the desktop shell — "choose directory"
dialogs (type the path instead), open-in-editor, native notifications, the
embedded browser preview, keyboard shortcuts bound in Electron. `gh` is not
installed, so PR columns stay empty.

## Remove it

```bash
systemctl disable --now ao-compare; rm -f /etc/systemd/system/ao-compare.service
rm -f /etc/nginx/sites-enabled/ao-compare /etc/nginx/sites-available/ao-compare; systemctl reload nginx
rm -rf /opt/ao-compare /var/lib/ao-compare /etc/ao-compare.env /etc/nginx/ao-compare.htpasswd /root/ao-compare-credentials.txt
```
