# AgentRoom dashboard (frontend)

React + Vite + Tailwind. Shows live agent status and the pending human
escalation queue, backed by `../backend`'s REST + WebSocket API.

## Run

```bash
npm install    # first time only
npm run dev -- --host 0.0.0.0 --port 5173
```

Defaults to `http://localhost:8787` / `ws://localhost:8787/ws` for the
backend (see `src/api.js`). Override with `VITE_API_BASE` / `VITE_WS_URL`
env vars (see `.env.example`) if the backend runs elsewhere.

## Note on the bundler

`npm create vite@latest` currently scaffolds onto Vite 8's new rolldown
bundler, which shipped without a working `linux-x64-gnu` native binding in
this sandbox (`Cannot find native binding`, a known npm optional-dependency
bug). `package.json` is pinned to classic Vite 5 + `@vitejs/plugin-react` 4
instead -- swap back once rolldown-vite is stable in your environment.
