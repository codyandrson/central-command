# The editor — a content-quality evaluator (2026-08-06, DRAFT for operator review)

Operator direction (2026-08-06): build the one composition gap the team review
found — a **quality** evaluator distinct from the **safety** auditor. This doc
is the detailed proposal for review/discussion/approval.

## What it is, and is not

The auditor answers "should this action happen at all" (pre-gate,
independence-guarded, zero tools, active on `dismissal.confirm`). Nothing
answers "is this content *good*" — is the Jira update well-written and
complete, is the digest actually useful, is this charter edit minimal. The
field (evaluator-optimizer pattern) treats these as distinct roles; the
critical property is that the critic must NOT share the drafter's charter or
context, or it inherits the drafter's blind spots.

**It is not**: an automatic pass on every proposal (that taxes every action
with latency and tokens and re-creates review load the gate already carries),
a second auditor, or a rewrite service.

## Shape: a hired agent, zero new mechanism

Everything it needs already exists — this is a hire, not a build.

- **`editor`** (name open — "reviewer" reads too close to auditor), hired via
  a singleton template (steward/EA/mcp-manager precedent, excluded from the
  operator hire menu), `ensure_registered` at startup.
- **Consultable: yes.** The delivery vehicle is consult slice 1/2, proven live
  2026-08-05: a drafter consults the editor with its draft; the editor answers
  with a critique (read-only consult — str out, no session row). Cheap, on
  demand, no new plumbing.
- **Taskable: yes.** "Review the last week's digests" is a legitimate operator
  task.
- **Packs:** `jira-read`, `graph-read`, `record-read`, `ask-operator`,
  `consult`? (recommend NO consult-holding in v1 — a critic that consults the
  drafter it's critiquing muddies independence; revisit). Deliberately **no
  propose packs**: the editor never drafts what it critiques — the drafter
  revises and owns the proposal. Its read surface exists to CHECK claims in
  the content (does TASKS-19 really have those labels, does the graph really
  say that), which is what separates a useful critique from vibes.
- **Skill:** `central-command-conventions` (it judges against team doctrine).
- **Charter core (v1, operator-authored):** rubric — factually grounded
  (verify ids/claims with read tools), complete for its purpose, appropriately
  scoped (flag padding as hard as gaps), follows the target system's
  conventions, actionable. Output = numbered, specific, severity-ranked
  fixes; quote the sentence being criticized; "no issues found" is a valid and
  expected answer, not a failure to perform. Never rewrite wholesale; never
  propose; independence sentence: if asked to review content it authored
  (impossible by construction today), refuse.

## How it enters the flow

Coaching, not wiring. Nothing hard-codes an editor pass. The operator steers WHEN
drafters consult it the same way consultability itself was widened
"enable-ahead-of-coaching": start by coaching one or two drafters whose
content quality has real review cost — candidates, in order of observed
operator burden:

1. `inbox-triage` — highest volume; its rejected support draft is the on-record
   example an editor pass might have caught.
2. `ea` — digest usefulness is an explicitly unvalidated live-Claude claim; an
   editor critique per digest (or sampled) builds the quality record.
3. `jira-expert` — its update conventions already live in its `jira` skill;
   lower urgency.

## Cost & failure modes, honestly

- A consult is a full sub-run (REQUEST_LIMIT 100 now); self-hosted, so the
  budget is loop-risk, not dollars. The consult-scoping lesson from
  2026-08-05 applies verbatim: critiques must be scoped to one artifact.
- Risk: rubber-stamp drift (critic that always says "fine") or nitpick drift
  (critic that always finds something). Both are visible in the record because
  every consult is on it; both are coachable; neither is automated away in v1.
- The editor's own quality gets the same treatment as everyone's: coaching from
  operator-curated signals.

## Slices

1. **Hire** — template + charter + grants + `ensure_registered` + guard tests
   (uniform-management invariant; roster walk). Validation: one live consult
   (`ea` → `editor` on a digest) lands a real critique on the record.
2. **Steer** — coach inbox-triage (and/or ea) to consult the editor before
   substantial drafts. Pure coaching, no code.
3. **Later, evidence-gated** — if the critique record shows consistent value,
   consider making an editor pass part of specific heartbeat surfaces
   (e.g. digest pre-delivery). Not before the record says so.

## Open questions for the operator

1. Name: `editor`?
2. Which drafter gets steered first (recommend inbox-triage)?
3. Should the editor hold `web-read` (checking external links in reviewed
   content)? Recommend not in v1.
