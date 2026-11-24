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
inherited client speaks). The cockpit server opens no other connection to the
API's `/ws` (upstream's `server/lib/gateway-rpc.ts` client was removed in
v2.131.0).

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

## Removed in the release after v2.116.0 (the operator's rulings of 2026-11-21)

| removed | why |
|---|---|
| the top bar's Log and Events dropdowns: `routes/agent-log.ts` (`GET/POST /api/agentlog` and the `agent-log.json` it wrote; `config.agentLogPath`, `agentLogMax`, `limits.agentLog`; `readJSON`/`writeJSON` in `server/lib/files.ts`), `src/features/activity/AgentLog.tsx` and `EventLog.tsx`, the agent-log and raw-event state in `SessionContext.tsx`, the Appearance toggles "Show events" and "Show activity log" (`nerve:showEvents`, `nerve:showLog`) and their command-palette entries | the Activity screen covers both. `agent-log.json` stays in `.gitignore` and in the file browser's exclusions, so a file an older install left behind stays out of both |
| the connect dialog (`src/features/connect/`, `routes/connect-defaults.ts`, `src/lib/gatewayUrls.ts`, `DEFAULT_GATEWAY_WS`) and the editable URL and token that `useConnectionManager` carried for it | the gateway is fixed at install (`GATEWAY_URL`). The client never connected with the defaults it fetched: it connects with an empty target, which the `/ws` relay resolves server-side and injects the token for. Settings > Connection keeps its status, its Reconnect button and the restart card |
| the slash-command autocomplete in `src/features/chat/InputBar.tsx` (the empty `SLASH_COMMANDS` list, its helpers, menu and keyboard handling) | no slash command is wired server-side; a leading `/` is ordinary chat text |
| the `<relevant-memories>` block (`src/features/chat/MemoriesSection.tsx`, its parsing in `MessageBubble.tsx` and the collapse props `ChatPanel.tsx` passed it) | nothing in the application emits that tag |
| the `identity` key of `routes/workspace.ts` (`GET /api/workspace/identity`, which read and created `IDENTITY.md` in `~/.openclaw/workspace-<agent>/`), the client's lookup of it in `SessionContext.tsx` and `extractIdentityName` | an agent's display name is its roster name, which `sessions.list` already sends as the `label` of the agent's root row (`nerve_gateway._sessions_list`); a root row without one shows the agent id. `routes/workspace.ts` stays for `chatPathLinks` |

## Removed in v2.126.0

| removed | why |
|---|---|
| device identity and pairing: `server/lib/device-identity.ts` (the Ed25519 key in `~/.nerve/device-identity.json`, the signed `device` block in the connect frame), the relay's wait for the challenge nonce, its retry without identity on a pairing rejection, its injection of `GATEWAY_TOKEN` into the connect frame with `server/lib/trust-utils.ts`, the `device` block `server/lib/gateway-rpc.ts` sent, and the `operator.pairing` scope the client asked for | the API's `/ws` reads no connect parameters (`nerve_gateway._dispatch` answers `connect` with the protocol and server name whatever the frame holds), so the signature, the nonce and the token were never checked. The relay now forwards every frame unchanged. A `~/.nerve/device-identity.json` an older install wrote is no longer read and can be deleted |

## Removed in v2.131.0

| removed | why |
|---|---|
| the relay's interception of `sessions.patch`, `.delete`, `.reset` and `.compact` (`RESTRICTED_METHODS`, `CONTROL_UI_CLIENT_ID` and the `client.id` check in `server/lib/ws-proxy.ts`), which sent those requests through `gateway-rpc.ts` unless the connect frame's `client.id` was `openclaw-control-ui` | the cockpit's only `/ws` client (`src/hooks/useWebSocket.ts`) always sends that id, so the interception never fired. Every frame now goes to the API's `/ws` as sent; the API answers `sessions.patch`, `.delete` and `.reset` itself |
| `server/lib/gateway-rpc.ts` and its test, the remote-workspace fallbacks in `routes/workspace.ts` and `routes/file-browser.ts` that called it (`agents.files.list`, `.get`, `.set`), `NERVE_WORKSPACE_REMOTE` (`config.workspaceRemote`) and `NERVE_PUBLIC_ORIGIN` (`config.publicOrigin`) | the API serves no `agents.files.*` method (`nerve_gateway._dispatch` answers it as not wired), so every fallback ended in its failure answer. A workspace directory not on this host now gets that answer directly: the workspace and file-browser reads list it empty and answer 404, and the workspace write answers 500. `NERVE_WORKSPACE_REMOTE` only forced that path; with it gone a file-browser write (when `FILE_BROWSER_WRITABLE=1`) is always to the local directory, which it may create, and the mutations still answer 501 for a directory that is not there. `GATEWAY_TOKEN` stays (the login fallback) |
| the hand-written side-by-side diff on the Skills page's flag banner (`src/features/skills/skillReview.ts` and its test) | the banner uses the chat's `src/features/chat/DiffView.tsx`, which gained optional panel labels |
| the hand-copied font files and `@font-face` CSS in `public/fonts/` | the same families, weights and subsets come from the `@fontsource/instrument-sans`, `@fontsource/space-grotesk` and `@fontsource/jetbrains-mono` packages, imported per weight by `src/lib/fonts.ts` and bundled by Vite (still same-origin) |
| `msw` in `package.json`'s `allowScripts` | it is in no dependency block; it was only an optional peer of `@vitest/mocker`, never installed |

## Changed in v2.132.0

| changed | why |
|---|---|
| `server/lib/ws-proxy.ts` no longer relays frame by frame on the `ws` package (its `WebSocketServer`, the buffer for frames sent before the upstream opened, the 30-second pings of both sides, the 100-frame / 1 MB buffer cap and the 1008 / 1001 close codes it chose). It runs the Origin check, the session-cookie check and the `?target=` check (`WS_ALLOWED_HOSTS` for a client-supplied target, `GATEWAY_URL` otherwise) on the upgrade request, and only then hands it to the `http-proxy-3` package, which forwards the handshake to the API's `/ws` and pipes the two sockets together. `ws` moved to `devDependencies` (only tests import it) | the WebSocket session is now end to end between the browser and uvicorn, so what the relay re-implemented is the API's own: uvicorn pings every 20 s (its default) and the browser answers, its `--ws-max-size` is the size limit, its close codes reach the browser unchanged, and the browser cannot send before the API's 101 arrives, so nothing needs buffering. A refused upgrade (Origin, session, target) gets an HTTP 401 / 403 / 400 / 502 and opens no upstream connection, where a bad target used to be accepted and then closed 1008; an API that refuses the connection fails the browser's handshake at once, and one that does not answer after a 10 s connect timeout. The cockpit's session cookie is not forwarded to the API. On shutdown the relay drops its sockets instead of sending close 1001; the browser reconnects on any close code (`src/hooks/useWebSocket.ts` reads none). The per-connection `[ws-proxy:<id>]` log lines are gone; upstream errors still log |

## Still present from upstream, to be removed or adapted in a later release

Inert here, or only partly used, and kept for now by decision:

- the connect frame's upstream fields (`client`, `role`, `scopes`, `auth`),
  which the browser still sends and the API ignores, and the API's
  `connect.challenge` event, which the browser waits for before it connects.

The visible product name is Central Command (the page title, the wordmark, the
login page and the logo's label; changed in the release after v2.121.0). The
internal names stay so that stored settings keep working: the `openclaw-nerve`
package name, `NERVE_*` settings, `nerve:*` storage keys and event names, file
and component names (`NerveLogo`), and the server's log lines.

## The file browser — kept, UI off, read-only unless the operator says otherwise

**`server/routes/file-browser.ts`** is mounted in `server/app.ts` and works
(tree, read, write, rename, move, trash, restore, traversal-guarded); the
operator kept it and the editor. The UI that drives it sits behind
`src/App.tsx`'s `FILE_BROWSER_ENABLED = false` (decided 2026-07-23, re-affirmed
2026-08-05: every governed artifact already has its own surface). Facts
operators and sessions still need before anyone turns it on:

- **`server/routes/cc-file-browser-guard.ts`** is middleware on
  `/api/files/*`: every non-read request (write, rename, move, trash, restore,
  agent-scoped workspaces included) answers `403 { ok: false, error: 'the file browser is
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
  the cockpit has no remote-workspace path (the inherited `agents.files.*`
  fallback, which the API never served, was removed in v2.131.0). The default root (`~/.openclaw/workspace`) is an
  upstream leftover with nothing in it.

When you need to know how something in the cockpit behaves, read
`server/app.ts`, the `cc-*` routes and `nerve_gateway.py`.
