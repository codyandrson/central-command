# web/ — the Central Command cockpit

The operator's web UI: a React client (`src/`) and a Node/Hono server
(`server/`) that proxies to the FastAPI control plane. It began as a fork of
openclaw-nerve v1.5.3 (MIT, notice kept in `LICENSE`) and is now Central
Command's own code; `VENDORED.md` records where it came from and what of
upstream is still present.

Build with `npm install --no-package-lock && npm run build` (npm 11), test with
`npm test`. How the cockpit fits the system: `docs/ARCHITECTURE.md` at the
repository root; the deployment runbooks are under `deploy/`.
