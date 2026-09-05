import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The cheese platform's preview tunnel reverse-proxies this app under
// $CHEESE_APP_BASE (e.g. /api/topics/<id>/app/) instead of "/" -- reading it
// here means `npm run dev` alone is correct both under that tunnel and at
// plain localhost (where it's unset, so `base` stays "/").
const appBase = process.env.CHEESE_APP_BASE || '/'

// Plain localhost for `npm run dev` on a laptop; `backend` (the
// docker-compose service name) inside a container, where the backend isn't
// reachable at 127.0.0.1 -- each container has its own loopback.
const backendHost = process.env.BACKEND_HOST || '127.0.0.1'

// Proxied so the browser only ever talks to this one origin -- the tunnel
// exposes exactly one port, and a bare `http://localhost:8787` from the
// frontend would resolve to the *viewer's* machine, not this sandbox.
export default defineConfig({
  base: appBase,
  plugins: [react()],
  server: {
    // Vite 5's DNS-rebinding guard rejects any request whose Host header
    // isn't localhost/127.0.0.1/a configured name -- which is exactly what
    // the platform's reverse-proxy tunnel sends (its own public hostname).
    // Confirmed by hand: `curl -H "Host: okcheese.com" ...` returned
    // "Blocked request. This host is not allowed." before this line existed.
    allowedHosts: true,
    proxy: {
      [`${appBase.replace(/\/$/, '')}/api`]: {
        target: `http://${backendHost}:8787`,
        changeOrigin: true,
        rewrite: (path) => path.replace(appBase.replace(/\/$/, ''), ''),
      },
      [`${appBase.replace(/\/$/, '')}/ws`]: {
        target: `ws://${backendHost}:8787`,
        ws: true,
        rewrite: (path) => path.replace(appBase.replace(/\/$/, ''), ''),
      },
    },
  },
})
