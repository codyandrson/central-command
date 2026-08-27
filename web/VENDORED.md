# What in `web/` is Central Command and what is upstream Nerve

`web/` is a fork of **openclaw-nerve v1.5.3** (MIT), vendored wholesale on
2026-07-22 (commit `5b1a7d7`). That was deliberate — the fork brought a working
cockpit, and the plan was always to keep its capabilities rather than rebuild
them. The cost is that a lot of this tree describes, tests, and documents a
*different product*. This file says which is which, so nobody has to guess.

## Central Command's own code

- **`server/routes/cc-*.ts`** — the routes Central Command actually serves:
  `cc-kanban`, `cc-crons`, `cc-memories`, `cc-tokens` (each replacing an
  upstream route of the same name minus the prefix), plus the Central Command-only
  `cc-activity`, `cc-charter`, `cc-graph`, `cc-loe` and `cc-skills` (proxies
  for the three Skills-page import routes; the upstream `skills.ts` was
  deleted outright rather than quarantined — it had no `cc-` twin to point
  at). **`server/app.ts` is the authority** on what is live: if a route module
  is not imported there, it does not run.
- **`server/lib/gateway-*.ts`** — the RPC seam to the FastAPI control plane
  (`central_command/api/nerve_gateway.py`). This is the ~700-line boundary the
  pivot was designed around.
- **`src/features/decisions`, `agents`, `workspace`, `skills`, `attention`** —
  the Central Command screens.

## `vendor-unused/` — upstream code that never runs

Quarantined 2026-07-29. ~9,900 lines of upstream route and store modules that
**`server/app.ts` never imports**, each superseded by a `cc-*` twin:

| quarantined | replaced by |
|---|---|
| `server/routes/kanban.ts` (+ test) | `server/routes/cc-kanban.ts` |
| `server/lib/kanban-store.ts`, `kanban-assignee.ts`, `kanban-subagent-fallback.ts` (+ tests) | — reachable only from the above |
| `server/routes/crons.ts` (+ test) | `server/routes/cc-crons.ts` |
| `server/routes/memories.ts` (+ test) | `server/routes/cc-memories.ts` |
| `server/routes/tokens.ts` | `server/routes/cc-tokens.ts` |

None had been modified since the vendoring commit. They are **quarantined, not
deleted**, on purpose: a `cc-*` route may yet want to borrow logic from its
upstream twin, and the standing rule is that vendored Nerve capabilities are not
discarded without a decision.

Why they had to leave `server/`: their tests **passed**, and ran on every
`vitest` invocation. A green test over code the server never loads is worse than
no test — it reads as coverage. They are now excluded from `vitest.config.ts`
and from `config/tsconfig.server.json`.

**To restore one:** `git mv` it back to its original path under `server/`, then
register it in `server/app.ts`. The directory layout inside `vendor-unused/`
mirrors `server/` so relative imports *within* the quarantine still resolve;
imports reaching into live `server/lib` (e.g. `../lib/config.js`) will not
resolve until the file is moved back. Nothing typechecks the quarantine, so that
breakage is invisible until you restore — which is the intended trade.

## Live but permanently inert — mounted routes with no Central Command backing

These are registered in `server/app.ts` and do run, but Central Command has
nothing behind them and never will: no OpenClaw channel config, no Codex/CC
CLI installed on the Pi. Both were checked 2026-08-03 and confirmed to
degrade honestly (empty list / "unavailable") rather than error, so they are
kept as-is rather than quarantined:

- **`server/routes/channels.ts`** — the OpenClaw channels route. Returns an
  empty channel list; nothing in Central Command's UI depends on it having data.
- **`server/routes/codex-limits.ts`, `claude-code-limits.ts`** — CLI usage
  panels for Codex / Claude Code CLI. Report "unavailable" since neither CLI
  runs here (Central Command talks to Claude via the LiteLLM proxy, not a local
  CLI).

## The file browser — live-mounted upstream code, UI off by decision

**`server/routes/file-browser.ts`** is registered in `server/app.ts` and fully
functional (tree/read/write/rename/move/**trash**/restore, traversal-guarded),
but the UI that drives it sits behind `src/App.tsx` `FILE_BROWSER_ENABLED =
false` (2026-07-23 decision: every governed artifact already has a first-class
surface; re-affirmed 2026-08-05 — leave off). Two facts recorded here so nobody
"just turns it on" (investigated 2026-08-05):

- **The `.env`/`.git` exclusion list silently goes EMPTY whenever
  `FILE_BROWSER_ROOT` is customized** (`server/lib/file-utils.ts` —
  `getExcludedNames()`/`getExcludedPatterns()` return empty sets on a custom
  root). Pointing the root at the repo to browse `servers/` would serve `.env`
  — and its secrets — over HTTP. If it is ever enabled, scope the root to a
  directory that contains no secrets (e.g. `servers/` itself) and add a
  read-only guard first: the mutation endpoints have no off switch today.
- **Agent sandbox workspaces are unreachable regardless**: they live in k8s
  pods, and the remote fallback this route would use (`agents.files.list/get/set`
  gateway RPC) has no handler in `nerve_gateway.py`. The default root
  (`~/.openclaw/workspace`) is an upstream leftover with nothing in it.

## `docs/`, `README.md`, `CHANGELOG.md`, `install.sh`, `.github/` — upstream

**All frozen at the fork date and describing openclaw-nerve, not Central Command.**
Treat every one as third-party documentation:

- **`docs/DEPLOYMENT-A.md`, `-B`, `-C`, `INSTALL.md`, `INSTALLER-STEPS.md`,
  `UPDATING.md`, `TAILSCALE.md`** — upstream's install and deploy paths. They
  **contradict** how Central Command is actually deployed. The real deployment is
  `deploy/k3s/` at the repo root (the two-node k3s cluster, 2026-07-31);
  `deploy/pi/` is the SUPERSEDED compose stack kept as the rollback path — see
  the root `CLAUDE.md`'s "Where it runs". Nothing here is used, including
  `install.sh` (49 KB).
- **`docs/API.md` (2,125 lines), `ARCHITECTURE.md`, `CONFIGURATION.md`** —
  accurate for upstream's API surface, partially true here. The Central Command
  contract is `central_command/api/nerve_gateway.py` plus `docs/DESIGN.md`.
- **`.github/workflows/ci.yml` + issue templates** — inert. GitHub only reads
  `.github/` at the repository root, and there isn't one; this CI has never run.
- **`CHANGELOG.md`** — upstream's release history. Central Command's history is
  `docs/JOURNAL.md`.
- **`bin/nerve-update.ts`** (+ its gitignored `bin-dist/` build output, wired
  as the `npm run update` script and `server/lib/release-source.ts`) —
  upstream's SELF-UPDATE tooling, fetching releases from upstream's GitHub.
  Never run it here: Central Command's cockpit updates by `git pull` + `npm run
  build`, and a self-update would overwrite the fork with upstream.
- **`skills/nerve-kanban/`** — an upstream skill bundle, not referenced by
  `server/app.ts` or any cc-* route; inert upstream leftover, same class as
  `vendor-unused/`.

When you need to know how something in the cockpit behaves *here*, read
`server/app.ts`, the `cc-*` routes, and `nerve_gateway.py`. Not these.
