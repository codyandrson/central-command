# Roadmap — unfinished work found in the records

> Seeded 2026-09-20 from a consistency audit of every design record against
> the code. This is an inventory with a disposition per item, **not** a
> schedule and not a spec. Each design record's own header says what was built
> ([index](superpowers/README.md)); this file collects what was *not*.
>
> Dispositions: **spec next** — ready for a short spec before any build ·
> **blocked** — cannot proceed, with the blocker named · **operator task** —
> needs the operator's hands or judgment, not code · **deferred** — deliberately
> not being built; the revisit condition is stated.

## Spec next

| Item | What is missing | Source record |
|---|---|---|
| Coaching stages 5b / 5c / 5d | Stage 5a (the coach, governed charter edits) is built; each later stage has its open question recorded in the spec | [coaching experiences](superpowers/specs/2026-07-27-coaching-experiences-design.md) |
| Cross-session reviewer (self-directed learning §4) | Session-close reflection is built; the scheduled reviewer that reads *across* sessions has no code | [self-directed learning](superpowers/specs/2026-08-01-self-directed-learning-design.md) |
| Update pipeline, full shape (deploy-refactor D3) | `deploy/single/update.sh` has import/stage/plan/apply/rollback; the specced separate pre-test phase and plan-time constraint resolution against the mirror are not built | [deploy refactor](superpowers/specs/2026-09-03-deploy-refactor-design.md) |
| k3s Helm chart (deploy-refactor D4) | Not started; `deploy/k3s/` still runs the bespoke manifest + `cc-update.sh` choreography. Pairs naturally with splitting `deploy/pi/` into live shared assets and a retired remainder | [deploy refactor](superpowers/specs/2026-09-03-deploy-refactor-design.md), [`deploy/pi/README.md`](../deploy/pi/README.md) |
| Supersede/annotation writes as gated Confluence actions; steward bulk-approval graduation | Knowledge-layer item 5 — the steward measures agreement (`gateway/steward_agreement.py`) but no class has graduated | [knowledge-layer routing](superpowers/specs/2026-07-29-knowledge-layer-routing-design.md) |
| D7 auto-assign graduation; post-graduation spot-checks | The orchestrator recommends, the operator assigns. The ladder names the evidence-starvation blind spot after a class graduates; no spot-check mechanism exists for the dismissal classes | [trust-tier graduation](superpowers/specs/2026-07-30-trust-tier-graduation-design.md) |
| Spot-check before `graph.verify_invalidation` graduates | Decided in the graph-auditor spec as a precondition; verify what exists before flipping that class | [graph verification auditor](superpowers/specs/2026-08-19-graph-verification-auditor-design.md) |
| EA follow-ups (EA-widening Slice B) | Slice A (calendar writes) is built; Slice B is not | [EA widening](superpowers/research/2026-08-06-ea-widening-design.md) |
| Episode-walk cluster walk | The Graph panel's Walk toggle is navigation-only; walking a cluster is the agreed next increment | [graph inspection](superpowers/specs/2026-08-09-graph-inspection-design.md) |
| Comprehensive onboarding flow | The in-product first-run prompt and EA tour exist; an end-to-end guided onboarding does not | [setup & onboarding](superpowers/specs/2026-08-21-setup-onboarding-design.md), [team tour](superpowers/specs/2026-08-23-ea-hosted-team-tour-design.md) |
| Guard tests for unguarded invariants | Several trust and data-loss invariants are enforced by code structure only — see the `Enforced:` line of each entry in the [decision log](decisions/README.md). Includes: runtime's use of credentialed `integrations/` clients is read-only by inspection, not by test | [decision log](decisions/README.md) |
| Cockpit RPC pressure: `sessions.list` storm, approve-resume still inline | Approve and reject return when recorded; the session-list polling load remains | [`CHANGELOG.md`](../CHANGELOG.md) v2.27.2, v2.29.3 |

## Blocked

| Item | Blocked on | Source record |
|---|---|---|
| Exchange adapter over an internal PowerShell automation toolset | Being on-site in the target work environment; endpoint verification cannot be done remotely | [work-transition compatibility](superpowers/specs/2026-08-21-work-transition-compatibility-design.md) |
| Sources catalog slice 8: work adapters (SharePoint, mapped drives, Atlassian Data Center) | The same work migration; slices 1–7 prove the pattern first | [sources catalog](superpowers/specs/2026-08-23-sources-catalog-design.md) |
| Human-team staffing agent | Real team traffic to staff against; doctrine is decided | [human-team staffing](superpowers/specs/2026-08-21-human-team-staffing-design.md) |
| Graphistry push from the Graph panel | A GPU host to run it; the three-state seam is already in the cockpit | [graph inspection](superpowers/specs/2026-08-09-graph-inspection-design.md) |

## Operator task

| Item | What it takes | Source record |
|---|---|---|
| Editor rollout ("Steer" slices 2–3) | The editor is hired and built. Getting drafters to consult it is *coaching* through the existing charter loop, not a build | [editor quality evaluator](superpowers/research/2026-08-06-editor-quality-evaluator-design.md) |
| Discussion-stall escalation | Built; its `discussion-sweep` schedule is seeded disabled. Enable it in the crons tab, or decide it stays dormant | [discussion-stall escalation](superpowers/specs/2026-07-27-discussion-stall-escalation-design.md) |
| Graduating any auditor class | Every class defaults to shadow. Flipping one is an operator decision made on its agreement record — see "Who may approve what" in [`ARCHITECTURE.md`](ARCHITECTURE.md) | [trust-tier graduation](superpowers/specs/2026-07-30-trust-tier-graduation-design.md) |

## Deferred — deliberately not being built

| Item | Revisit when | Source record |
|---|---|---|
| Skills library Stage 4: per-agent gate model | Skill grants prove too coarse in practice | [skills library](superpowers/specs/2026-07-26-skills-library-design.md) |
| Activity full-text search and saved views | The Activity view's filters stop being enough | [activity coverage](superpowers/specs/2026-08-11-activity-coverage-design.md) |
| Standing auto-dismiss rules | Bulk dismissal (one decision per known set) stops being enough; a standing rule is a graduation nobody has decided | [bulk dismissal](superpowers/specs/2026-08-17-bulk-dismissal-design.md) |
| A2A-style threaded consults | Transcript evidence shows single-shot consults losing information | [expert-team scaling](superpowers/specs/2026-08-22-expert-team-scaling-design.md) |
| Full-roster team tour | The core-five tour plus "offer the rest" proves insufficient | [team tour](superpowers/specs/2026-08-23-ea-hosted-team-tour-design.md) |
| Agent-version lifecycle and replay (founding D18) | Charter versioning + revert stops being enough | [`DESIGN.md`](DESIGN.md) §4 |
| Adaptive routing feedback loop; local→cloud fallback chains | Withdrawn on evidence; fallbacks are declared but deliberately not configured | [quality-router migration](superpowers/specs/2026-07-26-quality-router-migration-design.md), [decision log](decisions/models.md) |
| `ARG_SPECS` full coverage | Never pre-emptively — a spec is added when a capability family produces a multi-round rejection in practice | [decision log](decisions/trust-gate.md) |

## Small cleanups (no spec needed)

- `ProposalStatus.proposed` / `.approved` and `SessionStatus.awaiting_input` /
  `.aborted` (`central_command/contract/enums.py`) are declared and never
  assigned; remove them with the `status in (...)` reads that tolerate them, or
  document why they stay.
- A legacy-identifier rename across the cockpit's proxy helpers and remaining
  comments, as its own release.
- A Windows service wrapper for the API and cockpit on the single-node profile.
- `deploy/k3s/` build scripts and runbook carry example node names and
  addresses from the reference deployment as defaults; parameterise them.
