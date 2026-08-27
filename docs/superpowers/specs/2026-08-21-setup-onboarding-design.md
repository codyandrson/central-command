# Setup & onboarding — zero to functioning from an API key and Claude Code

_Design record written 2026-08-21 from a design discussion with the operator. Goal in
his words: "I can hand this to either myself or a teammate that has literally
nothing other than an API key and Claude Code CLI, and they can get up and
running." Nothing may be skipped — LiteLLM, email, graph, every selected
integration is set up or explicitly declared already-present, and the install
ends inside a working, verified system._

## The shape in one paragraph

Setup is **two phases with a hard gate between them**. Phase 1 is a
deterministic, incremental installer: stand up the stores, LiteLLM,
Graphiti/Neo4j and the selected integrations in dependency order, each step
ending in its own online-check, finishing with an acceptance run the user
watches succeed. Phase 2 is agent onboarding: an interview conducted BY the
system THROUGH the user's own LLM, whose answers become knowledge-graph
episodes (who the user is, their environment, their team) and curated signals
fed through the existing coaching loop, taking every installed agent from its
shipped v0 charter template to a personalized v1. Tools are installed and
tested BEFORE any agent is coached on them, because charter capability lists
are generated from grants and guidance must never reference a tool that
doesn't exist.

## Decisions

1. **Claude Code is the setup harness; Central Command scripts are the setup.**
   The repo ships a `/setup` skill: Claude Code does elicitation, adaptation
   and failure diagnosis; deterministic idempotent scripts do everything that
   must be reproducible (write `.env`, apply schema, render manifests, run
   verification). Claude Code fills script inputs and runs scripts — it never
   freehand-edits config. **Claude Code is a hard prerequisite of setup**
   (the operator's decision, 2026-08-21, revising this spec's original fallback
   line): the target user has it, it works at his work against their
   models, and a no-Claude-Code install path would be maintained for nobody
   — do not build or document one. The scripts stay because they are the
   REPRODUCIBILITY layer Claude Code drives, not a fallback.
2. **The LLM endpoint comes first.** Prompt for base URL + API key, query
   `/v1/models`, let the user pick a chat model, run a real completion to
   prove it. From that moment the installer has an LLM available for
   anything that goes sideways — and phase 2 has its interviewer.
3. **Embedding model + dimension are DISCOVERED at setup, and the dimension
   is a discover-once decision.** Probe the chosen embedding model for its
   output dimension, write it to config, create the Neo4j vector index to
   match. The 1024 constant in the writer/config becomes configuration
   validated at install. Swapping embedders later means dropping the index
   and re-embedding the whole graph — the installer records the choice as
   effectively permanent, not a tunable.
4. **LiteLLM is mandatory: detect-or-provision.** The Graphiti
   Responses→chat bridge (`openai/chat_completions/` prefix) and the carried
   graphiti patches assume the proxy. The installer either registers an
   existing LiteLLM (the operator's work case) or stands one up configured against
   the user's key (teammate case). `LITELLM_SALT_KEY` and
   `N8N_ENCRYPTION_KEY` are generated into the install directory's `.env`
   (the operator's call — no separate key escrow), with the never-rotate warning;
   the backup path for a portable install is configured at setup and must
   live outside the install tree.
5. **The portable deploy target is a SINGLE-NODE `podman kube play` profile**
   derived from the k3s manifests — not a Docker Compose stack. Both target
   environments run podman (work: podman + kube play; teammate: Windows with
   Podman Desktop), and `podman kube play` consumes Kubernetes YAML, so the
   existing manifests are the source of truth with the homelab specifics
   (two-node placement, Tailscale, the workstation llama fleet, hostPath
   layout) factored out. The two-node k3s deployment stays what it is —
   the operator's instance — and is NOT the giftable profile.
6. **Phased, gated, resumable.** Order: stores → LiteLLM → graph stack →
   selected integrations (n8n only if an n8n-backed integration is chosen) →
   acceptance run → agent onboarding. Each phase ends with an online-check;
   a step ledger records completion so a re-run after a mid-install death
   re-verifies rather than re-installs. Idempotency lives in the scripts,
   not in the harness's judgment.
7. **The catalog is enumerated, never hardcoded.** Integration and optional
   agent/skill menus are read from the pack registry and skills library
   (already data, D25), so a new integration appears in the installer with
   zero installer changes and the menu can never advertise something that
   isn't installable. Email provider is an explicit named choice: Gmail
   (implemented, via the n8n façade), Exchange/Outlook (listed, not yet
   available — see Deferred), or manual-feed/none.
8. **v0 charters become TEMPLATES, rendered ONCE at hire.** Placeholders
   (`<operator_name>`, tone/policy constants) plus optional sections keyed
   to PACK GRANTS — the same source the generated capability list reads —
   so a charter can never carry guidance for a tool the agent doesn't hold
   (the "guidance needs a mechanism" failure, closed structurally). Render
   at instantiation and record the result as the charter version; from then
   on it is plain text owned by the coaching loop. No runtime rendering —
   that would put a second mutable artifact under a session's feet, the
   `system_prompt=`/`instructions=` trap in new clothes. Facts (team
   structure, who's expert on what, environment) go in the GRAPH, never
   into charter text: charters hold operating rules, stores hold data.
   Mechanics: simple marker substitution + begin/end section markers, one
   render function, plus a test that every marker in a shipped template is
   a known placeholder or a real pack id. No template engine.
9. **Phase 2 rides existing mechanisms.** Interview answers land as graph
   episodes through the normal episode path and as curated signals through
   the existing coaching loop, producing v1 charter versions the same way
   coaching produces every other version. This keeps the system-vs-instance
   line exact: the repo ships templates and v0s (product); the installer
   manufactures the instance (`.env`, graph seed, rendered + coached
   charters) on the user's machine.

   **REVISED same day (the operator's call, and it simplified the design): Claude
   Code conducts the interview, and on a fresh install the approval burst
   dissolves entirely.** Three facts made the original shape redundant
   there: (a) onboarding answers are OPERATOR INPUT — trusted evidence, not
   the agent claims the gate exists to check; a dedicated onboarding agent
   would restate the operator's words as proposals for the operator to
   approve, review theater by construction. (b) At setup time the agents do
   not exist yet, so the interview runs BEFORE first boot: values land in
   `.env`, and the first-boot hires render every charter template against
   them — "v1" is the informed render, no coach proposals needed; the
   coaching loop takes over from day one of real usage. (c) Team and
   environment facts are operator-direct episodes
   (`scripts/onboard_episode.py`, `trust=operator-direct`, the curation-path
   convention) — and per-episode approval never protected against extraction
   defects anyway; that is the graph-verify sweep's job. The gates are
   untouched for everything agents do after onboarding. Costs accepted:
   Claude Code is a hard prerequisite of setup (accepted outright —
   see decision 1's revision; no by-hand path exists or is planned), and the
   interview transcript lives outside the system record — mitigated by a
   final summary episode written into the graph. The original
   coach-mediated shape remains correct for the one case where agents DO
   pre-date the interview: re-onboarding an existing instance (the
   transition clean-up).
10. **The install ends inside a working demo, then the user flips to live.**
    The final phase-1 step is an acceptance run, not a success message:
    per-tool verification for every selected integration (the
    `m5_acceptance` pattern, extended), then one end-to-end pass — fixture
    email in, proposal parked, approved in the cockpit, executed in
    `dry_run`, provenance stamped. Switching `CC_EXECUTOR_MODE=live` is the
    user's explicit last act, never the installer's default.

## Deferred, with reasons

- **Exchange/Outlook email — after the work transition, deliberately.** The
  ledger seam is already provider-agnostic (everything downstream of
  `parse_email()` doesn't care), and the Gmail integration's n8n-façade
  shape is right for Exchange too (n8n ships Microsoft Graph/Outlook
  nodes) — so the work is mostly an n8n workflow behind the same façade
  contract. The blocker is real: the Azure app registration / Graph OAuth
  (or on-prem EWS) can only be created and tested IN the work environment.
  Building it blind now is the mcp-1.x-FastMCP failure again. The installer
  names the choice so the seam is a decision point, not an assumption.
- **Catalog growth before sharing** — the operator's priority list for the work
  environment: Confluence, git hosting (GitHub/GitLab/Gitea), calendar/
  scheduling, and Cisco Jabber chat ingestion (enterprise-hosted,
  certificate-based login — a research item, not a known quantity).
- **Windows/Podman Desktop validation** — the profile is designed for it
  (single node, no hostPath assumptions beyond the install dir, no GPU) but
  first-hand testing happens when there's a Windows box in the loop; until
  then it is documented as designed-for/untested.

## Build order

Compose… rather, **profile first**: (1) single-node podman-kube profile +
its verify script; (2) installer phase 1 (`/setup` skill + scripts:
endpoint/model/dimension discovery, step ledger, per-phase checks,
acceptance run); (3) catalog growth; (4) charter templates + render-at-hire;
(5) phase-2 interview/coaching; (6) Exchange, at work. Nothing in the chain
blocks on Exchange; Exchange blocks on being at work.

_Items 1, 2, 4 and 5 SHIPPED 2026-08-21 (commits `a0b558e`, `2ab4db0`,
`da8cfbe`, and the interview slice): the `/setup` skill now runs six phases
with the interview BEFORE first boot, per the decision-9 revision.
Remaining: catalog growth, Windows/Podman-Desktop validation, Exchange at
work, and the v0 charter conversion to templated form (part of the
transition clean-up's v0 rebuild — the render seam is live, the shipped
charters simply don't carry markers yet)._

## Addendum 2026-08-25 — the flow is LiteLLM-first

_Cody's decision. Decision 2 above ("the LLM endpoint comes first") stands; what
changes is what "first" is proven AGAINST._

Everything Central Command runs at runtime addresses this stack's own LiteLLM by
alias, so setup now stands the proxy up EARLY, registers the model aliases
IMMEDIATELY, and runs every validation probe THROUGH them — a chat completion
via `cc-default` and the embedding-dimension measurement via `cc-embedding`.
Four reasons: what gets proven is what production uses; per-alias `api_base`
makes a split chat/embedding upstream a proxy-config fact rather than a
special case downstream; both substrates now read the same way (the k3s branch
already had this shape — trio → `register-models.py` → `mint-keys.sh`); and
those registrations are the install's initial **permanent catalog**, not
temporary scaffolding to be redone later.

The bootstrap circularity is resolved, not ignored: you cannot list an
upstream's models through a proxy that has not registered them, so phase 1
keeps exactly ONE direct read — `discover-llm.sh models` — and nothing else
direct. Direct chat/embed probes DEMOTE to a troubleshooting rung: when a
through-proxy probe fails, re-running it direct separates "bad upstream/key"
from "broken deployment/registration", which are different failures with
different fixes.

Mechanically: `deploy/single/stack.yaml.tmpl` splits into `stack-llm.yaml.tmpl`
(the trio) and the core file (postgres/neo4j/graphiti) — a reorganization, not
a change; the union renders document-for-document identical. Only the core file
carries `CC_EMBED_DIM`, so `render.sh llm` runs before the dimension exists and
the index-permanence rule is enforced by construction: the graph cannot come up
before the measurement. `discover-llm.sh --proxy` is the through-alias sugar.
