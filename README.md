# Central Command

> A human-supervised agentic-team framework — a control plane over a team of
> Claude agents where the operator is team lead: tasking agents, approving their
> actions, answering their questions, and coaching their behavior over time.

_(Renamed from "Central Command" to "Central Command" on 2026-08-26 — prose and
product name only. Code identifiers — the `central_command/` package, `CC_*` env
vars, `cc-*` units, namespace, and database names — deliberately retain the
historical name.)_

## Getting started

The path from zero to a running, onboarded system is four steps. The
authoritative step-by-step reference for the deployment itself is
[`deploy/single/README.md`](deploy/single/README.md); this section is the map.

### 1. Get the code

```bash
git clone https://github.com/codyandrson/central-command.git
cd central-command
```

No git, or an air-gapped target? Download the source zip instead —
**Code → Download ZIP** on the repo page (or
`https://github.com/codyandrson/central-command/archive/refs/heads/master.zip`)
— carry it across, and unzip it where the deployment will live. A
zip-installed deployment updates cleanly later (see **Updating** below); for
mirror/no-egress installs read [`deploy/AIRGAP.md`](deploy/AIRGAP.md) first.

### 2. Prerequisites

- **[Claude Code](https://claude.com/claude-code)** — it conducts the install
  and the onboarding interview; there is no by-hand path.
- **podman ≥ 4.9**, plus `git`, `curl`, `openssl`, `envsubst` (gettext), and
  `uv`. Node ≥ 22 is optional (without it the cockpit UI is not built; the
  API still runs). `./setup.sh preflight` checks every one by name.
- **An OpenAI-compatible LLM endpoint**: base URL, API key, a chat model id,
  and an embedding model id. ~3 GB RAM for the stack.

### 3. Install

```bash
claude        # from the repo root, then type:  /setup
```

`/setup` elicits your answers into `deploy/single/.env` and runs the
deterministic driver `./setup.sh` — six idempotent phases
(`validate → preflight → llm → stack → app → verify`), each re-runnable on
its own, every check one `PASS|WARN|FAIL` line. A failure tells you which
phase to re-run; `./setup.sh diagnose` writes a support bundle to paste back
to Claude. Nothing is guessed: the agent reads the results and diagnoses —
the script does all the mutating.

### 4. Onboarding

`/setup` ends with the **onboarding interview**: Claude asks who you are and
how you want the team to operate (your name for canonical graph naming, graph
scope, approval posture), seeds the knowledge graph, and hands you a **watched
dry-run demo** — the executor starts in `dry_run`, so every write is logged,
gated, and performed against nothing until you deliberately flip
`CC_EXECUTOR_MODE=live`.

## Updating a deployment

Updates are built for the same worst case as the install: the only transport
in is a downloaded source zip. **The human path is one command** — download
the release zip, then:

```bash
cd deploy/single
./update.sh ~/Downloads/central-command-<version>.zip
```

That imports the zip, shows the version gate + plan, pauses for your explicit
yes, and applies. Under the hood `update.sh` turns the deployment into a
two-branch git repo (`upstream` = pristine imports, `local` = yours) so each
update is compare-then-merge, with your local modifications preserved by
three-way merge and a tagged rollback point. The named subcommands remain for
granular or agent-conducted flows:

```bash
./update.sh init            # one-time, on an existing deployment
./update.sh import <zip>    # commit the newly downloaded zip
./update.sh plan            # dry-run: what changes, what will run, conflicts
./update.sh apply           # merge -> schema -> deps/cockpit -> verify
./update.sh rollback        # if needed: back to the pre-update tag
```

Full semantics — conflict handling, what rollback does and does not undo —
in [`deploy/single/README.md`](deploy/single/README.md#updating-an-existing-deployment).

**Nothing changes the world without passing an approval gate.** Single-operator,
self-hosted on a Linux homelab (podman). The primary risk model is *error, not
malice* (hallucination / misreads), so the guards are reliability guards that
also happen to stop misuse.

## The core idea

An agent can only **propose** and **read** — never write. It emits a `Proposal`
(intent + actions + evidence), the run **durably pauses** (survives a full
process restart), the proposal lands in a **Decisions Inbox**, and only after the
operator approves does a credentialed **Executor** perform the real write (Jira,
the knowledge graph) and stamp provenance. The trust boundary is the import
graph: `runtime/` may never import the gateway/executor tier — and that rule is
enforced by a test, not a convention.

**Approval attaches to what work _does_, never to how it was triggered.** A
schedule, a delegation, or an orchestrated project can move work along, but every
world-changing action still gates individually — exactly as if the operator had
started it by hand.

## Architecture

A modular monolith in four tiers:

1. **Control Plane UI** — React cockpit (`web/`, a forked OpenClaw-Nerve frontend)
2. **Control Plane API & Services** — FastAPI (`central_command/api`, `central_command/gateway`)
3. **Agent Runtime** — Pydantic AI 2.x + Claude (`central_command/runtime`) — *propose + read only*
4. **Stores** — Postgres (spine), Graphiti/Neo4j (knowledge graph via MCP), n8n
   (email façade holding the Gmail OAuth)

Durable pause/resume is explicit persistence over Postgres (the paused run's
message history + pending tool call), so an agent can wait days for a human
approval and survive a restart.

## Where to read next

| Doc | What it is |
|-----|------------|
| [`CLAUDE.md`](CLAUDE.md) | Orientation — read this first: what the system is, how to run it, conventions and hard-won gotchas |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed, release by release (the operator's live status and development journal are private instance documents) |
| [`docs/DESIGN.md`](docs/DESIGN.md) | The full design compendium and decision record |

## Running it

**Fresh install** (you have an LLM API key and nothing else): run **`/setup`**
in Claude Code from the repo root — Claude Code is a hard prerequisite of
setup — and it takes you from zero to a verified, onboarded, dry-run system
(`.claude/skills/setup/`, deployment profile `deploy/single/`).

Dev checkout:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,runtime]"
cp .env.example .env                 # fill CC_LLM_BASE_URL + CC_LLM_API_KEY for live mode

# Demo mode — no API key, no live writes (safe):
(cd web && npm install && npm run build)
CC_DEMO_MODE=true CC_EXECUTOR_MODE=dry_run uvicorn central_command.api.app:app --port 8080

pytest                               # offline suite (contract + durable + orchestration)
```

Postgres for the spine: `docker compose up -d postgres` (podman works) — starts
`cc-postgres` on **127.0.0.1:5442** with the schema auto-loaded. For live UI dev
with hot-reload, run the API on :8080 and `cd web && npm run dev` (Vite proxies
`/api`). See `CLAUDE.md` for live-mode flags, ingestion, and audit-trail endpoints.

## Status at a glance

Phases 1–3 are built and human-verified; Phase 4 is underway. The full spine runs
end-to-end on real Claude — email ingestion at scale, a knowledge-graph write
path, governed charters, a graduated independent auditor, a coaching loop, native
Jira, capability packs, a governed heartbeat scheduler, and (newest) a **D7
orchestration runtime**: the orchestrator runs multi-agent projects as a durable
plan → assign → receive → decide loop, with the human gate exactly where it
belongs. See [`CHANGELOG.md`](CHANGELOG.md) for what has landed so far.

## Secrets

All credentials live only in `.env` / `web/.env` (both git-ignored) — never in
the repo or its history. The tracked `.env.example` files are empty placeholders.
