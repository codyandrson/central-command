# Where `web/` came from, and what of upstream is still in it

`web/` is Central Command's own code. It originated as a fork of
**openclaw-nerve v1.5.3** (MIT; the copyright notice stays in `LICENSE`, as the
licence requires), taken wholesale on 2026-07-22, and has diverged since: about
170 upstream files modified, plus Central Command's own routes and screens.
Upstream is not tracked and nothing is merged from it. The operator ruled on
2026-11-21 that the cockpit is owned code: dead inherited code is removed, and
an inherited feature stays only where the operator chose to keep or adapt it
(DL-224; PRD FR-44).

`server/app.ts` is the authority on what is live: a route module it does not
import does not run. The seam to the FastAPI control plane is two channels:
every `cc-*` route is a thin HTTP proxy to `config.gatewayUrl + '/api'`, and
chat, sessions and live push ride the cockpit server's `/ws`
(`server/lib/ws-proxy.ts`), which relays to FastAPI's `/ws`
(`central_command/api/nerve_gateway.py`, which answers the wire protocol the
inherited client speaks). `server/lib/gateway-rpc.ts` is not that seam: it is
upstream's WebSocket client for remote workspace-file access.

## Removed on 2026-11-21 (the release after v2.114.0)

| removed | why |
|---|---|
| `vendor-unused/` (upstream kanban, crons, memories and tokens routes and stores, about 9,900 lines) | quarantined since 2026-07-29; nothing imported them, and each had a `cc-*` replacement |
| `docs/` (upstream's manual, about 6,900 lines of text plus three images) | it documented openclaw-nerve's install, API and deployment, which contradict Central Command's; the chat-marker reference agents rely on is now `docs/CHAT_MARKERS.md` at the repository root |
| `install.sh`, `scripts/setup.ts` and `scripts/lib/`, `bin/nerve-update.ts`, `server/lib/updater/`, the `setup` and `update` package scripts, `config/tsconfig.bin.json`, `config/tsconfig.scripts.json` and the `@inquirer/prompts` dependency | upstream's installer and self-updater; a self-update would have fetched upstream's releases over this code. The cockpit is installed and updated by Central Command's own setup and updater. `server/lib/release-source.ts` stays: the version badge (`routes/version-check.ts`) and `routes/cc-update.ts` read it |
| `.github/`, `CHANGELOG.md`, `CONTRIBUTING.md`, and upstream's `README.md` (replaced) | upstream's project files; GitHub reads `.github/` only at a repository root, so its CI never ran |
| `server/lib/usage-tracker.ts`, `server/lib/token-usage.ts` (and the `USAGE_FILE` setting), the server and client kanban `parseMarkers` | nothing reachable imported them |
| `POST /api/gateway/restart` and `server/lib/openclaw-bin.ts` | it shelled out to an `openclaw` CLI no deployment has; the restart card in Settings > Connection uses `routes/cc-update.ts` |
| `POST /api/sessions/spawn-subagent` and `server/lib/subagent-spawn.ts` | nothing in the cockpit called it |

## Removed in the release after v2.115.0 (the operator's rulings of 2026-11-21)

The server-backed inert features, each with its route, its libraries, the
client code that called or rendered it, its settings and its tests:

| removed | why |
|---|---|
| beads: `routes/beads.ts`, `server/lib/beads.ts` and `server/lib/plans.ts` (its only importer), `src/features/beads/`, the `bead:` link handling in the markdown renderer and chat, and the bead viewer tab | it shelled out to a `bd` command no deployment has |
| the Codex and Claude Code usage-limit blocks in the Usage panel: `routes/codex-limits.ts`, `routes/claude-code-limits.ts`, `server/services/claude-usage.ts`, `server/lib/cached-fetch.ts`, `src/features/dashboard/useLimits.ts`, and the `node-pty` dependency | neither CLI runs here, so both always reported "unavailable". The rest of the Usage panel (spend from LiteLLM through `cc-tokens`) stays |
| `routes/channels.ts` and the schedule dialog's "deliver to a channel" section (`DELIVERY_ENABLED`, the channel and recipient fields, the appended send instruction) | it read channel names from `~/.openclaw/openclaw.json`; no deployment has a messaging channel. A schedule no longer sends a `delivery` field, which the API never read |
| the Replicate, Microsoft Edge and Xiaomi text-to-speech providers (`server/services/{replicate,edge,xiaomi}-tts.ts`, `server/lib/language.ts`), the local Whisper engine (`server/services/whisper-local.ts`, its model download and picker, `PUT /api/transcribe/config`, `GET /api/language/support`, the `@fugood/whisper.node` dependency, `STT_PROVIDER`, `WHISPER_MODEL`, `WHISPER_MODEL_DIR` with its `~/.nerve/models` default, and the installer's `WHISPER_MODELS_BASE_URL` question), and the API-key form with `routes/api-keys.ts` (`REPLICATE_API_TOKEN`, `MIMO_API_KEY`, `EDGE_VOICE_GENDER`) | read-aloud and voice input have one engine: the deployment's speech service behind `cc-tts` / `cc-stt`, reached through `OPENAI_BASE_URL` with the key setup mints. `OPENAI_API_KEY` stays (that key); the form that could overwrite it from the browser is gone |
| the OpenClaw session-file routes `GET /api/sessions/hidden`, `/api/sessions/media`, `/api/sessions/runtime` and `/api/sessions/:id/model` (all of `routes/sessions.ts`, and `SESSIONS_DIR`), with their callers in `SessionContext.tsx`, `loadHistory.ts`, `useModelEffort.ts` and `SessionInfoPanel.tsx` | they read `~/.openclaw/agents/*/sessions`, which no deployment has, so each answered empty or "missing". Sessions, models and thinking levels come over the `/ws` relay and `GET /api/gateway/models` |
| the file watcher (`server/lib/file-watcher.ts`, `NERVE_WATCH_WORKSPACE_RECURSIVE`), `routes/events.ts` (`GET /api/events` SSE, `POST /api/events/test`, `broadcast()`) and the client's `src/hooks/useServerEvents.ts` | they pushed `file.changed` / `memory.changed` for upstream's workspace files. The file tree (kept, UI off) has no live refresh: it loads when opened and has a Refresh button. Everything live in Central Command rides `/ws` or its own polling |

## Still present from upstream, to be removed or adapted in a later release

Inert here, or only partly used, and kept for now by decision:

- the connect dialog (`src/features/connect/`, `routes/connect-defaults.ts`);
- the Log and Events dropdowns;
- the slash-command code (`src/features/chat/InputBar.tsx`; none is wired
  server-side);
- the relevant-memories block;
- device identity and pairing (`server/lib/device-identity.ts`);
- `server/lib/gateway-rpc.ts`, and `server/lib/ws-proxy.ts`, which is live as
  the `/ws` relay and carries upstream's gateway handshake.

The product branding (the `openclaw-nerve` package name, `NERVE_*` settings)
is unchanged.

## The file browser — kept, UI off, read-only unless the operator says otherwise

**`server/routes/file-browser.ts`** is mounted in `server/app.ts` and works
(tree, read, write, rename, move, trash, restore, traversal-guarded); the
operator kept it and the editor. The UI that drives it sits behind
`src/App.tsx`'s `FILE_BROWSER_ENABLED = false` (decided 2026-07-23, re-affirmed
2026-08-05: every governed artifact already has its own surface). Facts
operators and sessions still need before anyone turns it on:

- **`server/routes/cc-file-browser-guard.ts`** is middleware on
  `/api/files/*`: every non-read request (write, rename, move, trash, restore,
  and the remote `gatewayFilesSet` fallback inside write, agent-scoped
  workspaces included) answers `403 { ok: false, error: 'the file browser is
  read-only' }` unless `FILE_BROWSER_WRITABLE=1` (`config.fileBrowserWritable`
  in `server/lib/config.ts`). The GETs (tree, read, raw, resolve) stay open.
  It is registered inside `file-browser.ts` itself (an import plus
  `app.use('/api/files/*', fileBrowserGuard);` right after
  `const app = new Hono();`), so the suites that build their app from that
  file alone run under the guard, and any route added to the file is guarded by
  the prefix. Tests: `cc-file-browser-guard.test.ts`; `file-browser.test.ts`
  sets the flag in its config mock.
- **A custom `FILE_BROWSER_ROOT` that holds secrets is refused at config
  load.** `server/lib/file-utils.ts` returns EMPTY exclusion lists (no `.env`,
  no `.git`) on a custom root, so `validateConfig()` discards a root whose top
  level holds `.env*`, `.git` or `certs` (error logged, default workspace
  used). It checks the top level at boot only: a secret created later or nested
  deeper is not caught, so keep a custom root scoped to a directory that never
  holds secrets.
- **Agent sandbox workspaces are unreachable** from it: they live in pods, and
  the remote fallback (`agents.files.list/get/set` gateway RPC) has no handler
  in `nerve_gateway.py`. The default root (`~/.openclaw/workspace`) is an
  upstream leftover with nothing in it.

When you need to know how something in the cockpit behaves, read
`server/app.ts`, the `cc-*` routes and `nerve_gateway.py`.
