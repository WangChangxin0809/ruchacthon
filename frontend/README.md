# CC Workbench frontend

React + Vite + Tailwind. Left: projects and tasks. Centre: the selected
session (main agent or a worker) with streaming output and tool calls.
Right: board / preview / diff, resizable and maximisable. Bottom: the Room,
collapsed by default.

```bash
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

`/api` and `/ws` are proxied to `127.0.0.1:8787` (see `vite.config.js`;
`BACKEND_HOST` overrides the host). Deep links: `?p=<project id>&s=<session id>`.

The WebSocket client keeps the last event `seq` and reconnects with it, so a
dropped connection replays what was missed and never duplicates a message.

`package.json` pins Vite 5 (rolldown-vite's native binding was broken in the
original sandbox); swap when convenient.
