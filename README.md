# Central Command

> A human-supervised agentic-team framework — a control plane over a team of
> Claude agents where the operator is team lead: tasking agents, approving their
> actions, answering their questions, and coaching their behavior over time.

_(Renamed from "Central Command" to "Central Command" on 2026-08-26 — prose and
product name only. The GitHub repo is named **CentralCommand**, and code
identifiers — the `central_command/` package, `CC_*` env vars, `cc-*` units,
namespace, and database names — deliberately retain the historical name.)_

## Quickstart

```bash
git clone https://github.com/codyandrson/CentralCommand.git central_command
cd central_command
claude        # launch Claude Code, then type:  /setup
```

`/setup` conducts the whole install interactively — gated, resumable, ending
inside a watched dry-run demo. Prerequisites:
[Claude Code](https://claude.com/claude-code) (it runs the install and the
onboarding interview — there is no by-hand path), podman ≥ 4.9, and an
OpenAI-compatible LLM endpoint (base URL + API key). Details and the
step-by-step reference: [`deploy/single/README.md`](deploy/single/README.md).

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
