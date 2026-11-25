# AGENTS.md — Central Command agent instructions

> Covers only what applies to ANY Central Command deployment. Keep facts about
> YOUR deployment (hostnames, live state, operational history) out of this
> tree — put them in a private instance repo or a gitignored `CLAUDE.local.md`.
> Subtree-specific bite marks live in `.claude/rules/` and load by path.

<!-- bmad:context -->
<!-- Verified 2026-09-19 against ba173438. Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## Central Command

A human-supervised agentic-team framework: a control plane where the operator tasks Claude agents and approves, by default, every action that changes the world. Python 3.12 (FastAPI, Pydantic AI) with a React/Hono cockpit in `web/`, Postgres as the spine, Graphiti/Neo4j, LiteLLM, n8n. Single-operator and self-hosted, with an air-gapped deployment target; the risk model is error, not malice. Why and what: `docs/PRD.md`; how, as built: `docs/ARCHITECTURE.md`; why behind each rule: `docs/decisions/`; what changed: `CHANGELOG.md`; design records: `docs/superpowers/`; the founding design, frozen: `docs/archive/DESIGN.md`.

## Policy

- Nothing changes the world without passing an approval gate, BY DEFAULT; the tier rules are under Architecture below. The default has a closed, named exemption list: the operator's own graph curation and the deterministic wiki annotation (`wiki_annotation_mode="auto"`) — the two writes with no agent in the path. Adding to the list is the operator's call, never an agent's. Those two write with no proposal at all (never one auto-approved as `system`); every other write is a proposal, including one an override decides. An MCP tool's gate (`ungated` or `human approval`, the operator's per-agent `agent_mcp_gate_override`) is a separate form: it governs whether the agent may call the tool DIRECTLY, and stays as it is.
- **A gate override is the operator's standing decision, not an exemption.** Every capability has a default gate; the operator may relax it to none for one agent and one capability (scoped, narrowable by argument, target or schedule), only in the cockpit, and revoking is a timestamp. A write an override decides is still a proposal, decided by the gateway with approver `override:<id>` (several, sorted, when several cover it) and run by the same Executor, which re-checks the scope; it carries `trust=override:<id>`, runs a shadow period (`gate_override_shadow_runs`, default 5) of the operator's own approvals first, and is NEVER operator agreement for graduation. The auditor never holds one; an irreversible or LIVE class takes an explicit acknowledgement and a non-zero shadow, and the reversibility mark informs an override, never forbids one. An irreversible class never graduates to the auditor: that bar reads the same server-held mark, never the agent's word. Anything a reviewer would have seen flagged, every redraft (after the world moved, a failed execution or a Reject) and a proposal carrying the operator's reopen note still park. Agents are never told overrides exist. Detail: `docs/ARCHITECTURE.md` "Who may approve what"; why: DL-187.
- Secrets live only in `.env` (gitignored). Never commit or print them, and keep anything sensitive or personal out of tracked files: no real operator identity, no real mail/issue content, no deployment facts, nothing that lowers the cost of attacking an instance. Prose says "the operator"; examples use the Doe/Rivers cast and `example.com`; instance facts go in a private repo or gitignored `CLAUDE.local.md`. A bundled skill's instance facts go to the operator's overlay (`CC_SKILL_OVERLAY_DIR`, outside the checkout): the public tree carries only an `<!-- overlay: <field> -->` marker naming a field of the public index, never the text, and `tests/test_no_instance_facts.py` fails any tracked file holding the shape of an instance fact (a tailnet address, a `/home/<name>` path, an ssh-target default, a literal node name; names are checked against the operator's private list by `scripts/prepush-scan.sh --tree`).
- Never weaken `scripts/prepush-scan.sh` (the pre-push guard) to get a push through; move the content to a private repo instead.
- `master` is the only branch — no `main`. A feature branch is a deliberate choice to raise with the operator, not a default.
- A release is three things: a CHANGELOG entry, a `VERSION` bump, a tag. The updaters read `VERSION` as the installed version — not git, not the tag; `tests/test_version_file.py` pins it to the newest CHANGELOG heading.
- Never change `LITELLM_SALT_KEY` or `N8N_ENCRYPTION_KEY` (the single-node profile's `.env` spells the first `CC_LITELLM_SALT_KEY`); they encrypt the stored LiteLLM virtual keys and the Gmail OAuth credential, and a restore under a different key leaves the rows present but undecryptable.
- Never edit `docs/vendor/` (refetch via `scripts/vendor_docs_fetch.sh`, which regenerates the `docs/vendor/MANIFEST` fingerprint `update.sh import` reads to skip those 47k files — `scripts/vendor_manifest.sh`, guarded by `tests/test_vendor_manifest.py`) or hand-create anything in `servers/` (it arrives only through an approved `mcp.sync_source`; the hand-committed `servers/echo-demo/` demo is the one stated exception, and nothing else is added by hand). Regenerating `requirements.lock` is a deliberate act.

## Where things are

- `web/` is our own code, forked from openclaw-nerve and no longer tracking it; written here are `web/server/routes/cc-*.ts`, the `/ws` relay `web/server/lib/ws-proxy.ts` and the Central Command screens under `web/src/features/`. Dead inherited code is removed, not quarantined; what of upstream is still present, and the file browser's guard: `web/VENDORED.md`.
- Touching `deploy/`? `deploy/k3s/README.md` is the multi-node runbook, `deploy/single/` the Compose profile, and read `deploy/AIRGAP.md` FIRST for any mirrored or no-egress install.
- Queue operations (enrolling the fixture backlog, stepping the dispatcher, reading the audit trail): `docs/QUEUE.md`.
- Subtree bite marks load from `.claude/rules/` when a matching file is read: `graph`, `deploy-k3s`, `deploy-single`, `models`, `cockpit`, `integrations`, `runtime-resilience`, `tests`. Working in one of those areas without having opened its files? Read the rule file first.

## Running and verifying

- Python `>=3.12`, Node `>=22` (`web/.nvmrc`). Install with `pip install -e ".[dev,runtime,exchange,google,check]"` (the two provider extras carry the mail/calendar clients; the suite skips what is absent; `check` carries the linter and type checker, which a deployment does not install); build the cockpit with `(cd web && npm install && npm run build)`; serve with `uvicorn central_command.api.app:app --port 8080`.
- There is no CI — run the suite yourself before pushing. `pytest -q`, sequential, is the gate (about 11 min for ~6,500 tests on a Pi 5, so give it a timeout to match): it is the run that catches a test leaking state into its neighbour, which xdist workers hide. `-n 4` (about half the wall time when last measured, one database per worker) is only the quick pass. Cockpit tests are separate: `(cd web && npm test)`.
- `scripts/check` is the lint, format and type gate (`ruff check`, `ruff format --check`, `mypy` over `central_command/`; version and rules pinned in `pyproject.toml`; run `ruff format` on what you touch); run it before pushing — the pre-push hook runs it too and refuses the push when it fails.
- Dev database: `docker compose up -d postgres` → `cc-postgres` on 127.0.0.1:5442 with the schema auto-loaded. 5442, never 5432 — the port is baked into `.env`, `.env.example`, the compose files, the k3s manifests and the `config.py` default.
- A fresh git worktree has no `.env`; copy it in before running the suite.
- `python scripts/m5_acceptance.py` is the live acceptance run: it needs a key, a Postgres and Jira credentials, spends money, and performs a real Jira write on its throwaway issue.

<!-- /bmad:context -->

## Architecture — runtime is Pydantic AI, models through LiteLLM

Modular monolith, 4 tiers:
1. **Control Plane UI** — React (`web/`)
2. **Control Plane API & Services** — FastAPI (`central_command/api`, `central_command/gateway`)
3. **Agent Runtime** — Pydantic AI, any model the LiteLLM proxy serves (`central_command/runtime`) — holds **no credential**: every tool that changes an outside service is a `propose_*`. Its other tools are reads; triggers that touch only Central Command's own state (`create_task`, `consult_agent`, and the deferred `ask_operator`, `assign_work`, `submit_plan`); work inside the sandbox; and MCP tools the operator has marked ungated
4. **Stores** — Postgres (spine), Graphiti/Neo4j (knowledge graph — graphiti-core runs in
   the API process, no server), n8n
   (mail and calendar façades holding the Google OAuth; Jira and Exchange are
   native clients in `integrations/` — n8n only where it already solved a hard
   integration problem)

**The trust boundary is the import graph:** `runtime/` must never import
`gateway`/`executor`. Agents hold only `propose_*` tools (plus `create_task`,
a trigger through the one task-create seam and its backstops, not a write);
the credentialed Executor performs writes *after* approval. Keep it that way.
The ban is on importing those tiers; a guard test walks the imports. The
runtime does reach `central_command.api` lazily (the task-create seam,
resumes), and `api/` reaches the gateway: that is the shape as built, not a
licence to route a new write through it.

## Conventions & gotchas (bite marks)

Every rule states its reason. A session never drops, weakens or works around
a rule on its own. A rule whose reason is missing or no longer holds is raised
with the operator, who decides.

- **The test model is test-only, and there is no executor mode.** The
  deterministic model is `settings.test_model`, set only in-process by the test
  suite — no env key or env file can set it — and the API refuses to start when
  `CC_TEST_MODEL`, `CC_DEMO_MODE` or `CC_EXECUTOR_MODE` is present in its
  environment, whatever the value. Every approval performs its write; tests
  that need an approval to execute use `tests/fake_world.py`. The one model
  seam is `runtime/models.py:resolve_model()`.
- **Proposal arg shape:** real Claude *flattens* the `Proposal` fields at the
  top level of tool-call args (Pydantic AI hoists a single-model tool param);
  the test model nests them under `proposal`. Always parse via
  `durable.proposal_from_call()` — it tolerates both.
- **A proposal's `agent_id` is the DRAFTER, never the subject.** The coach
  drafts charter edits for other agents, so the two differ: the proposal row
  (session, Inbox attribution, throttle count) belongs to the agent that
  generated it; the target lives in the action arguments where the Executor
  reads it. `executor.execute()` takes `proposer` from the gateway for
  exactly this reason — an actor an agent can write into its own args is an
  agent-authored claim about provenance.
- **A fresh run must load the charter; `build_agent_for` does not.** It is
  the RESUME factory and passes no charter (the persisted history carries the
  original prompt); `run_task` and `run_coach` load the real one. Every
  fresh-run path loads it (the steward, the wiki repair, reflection,
  conversations, consults and orchestration all do); a new one must too.
- **`system_prompt=` is load-bearing — never "modernise" it to
  `instructions=`.** A system prompt is PERSISTED into the message history,
  so a session paused at a deferral resumes under the charter it was proposed
  under; `instructions=` is re-applied from the live agent at every run — a
  resume would silently execute under whatever the charter says NOW.
  Rewriting the record of what an agent was told when it proposed is a
  governance change wearing an idiom cleanup's clothes. This rule is about the
  CHARTER. Skill guidance is a different channel: it rides
  `Capability(instructions=…)` in `runtime/skills.py` and is read live on
  every run, so a resumed session sees the skill text as it is now.
- **Never swap the approval gate for pydantic-ai's `approval_required()`.**
  Its semantics are "pause, collect a yes, then run the tool IN THIS PROCESS"
  — which puts the write back in `runtime/`, the tier that may never hold
  credentials. `CallDeferred` + `propose_*` + the credentialed Executor is
  the shape on purpose: the runtime cannot perform the write even if it is
  approved by mistake.
- **A fresh database must reproduce the roster.** `schema.sql`'s
  founding-roster UPDATEs are guarded by `where … and role = ''`, so the
  INSERT above them must NOT seed `role`. If you add a seeded column, ask
  what a brand-new database does, not what the dev box does.
- **`schema.sql` is applied on a FRESH database and by the updater at release
  — never at process start.** A fresh database auto-loads it (initdb); a
  running one gets it from the updater's `schema` phase (`cc-update.sh`,
  `update.sh apply`), additive-only and idempotent, on every update. Nothing in
  the app process applies it: the updater does so after its backup and
  rollback checkpoint, and a start-up apply would change a live database on
  any restart with no rollback point. The test DB re-executes it every run, so a new
  column passes the whole suite and then breaks a dev or hand-run database
  with an undefined-column error that reads like a code bug — apply additive
  columns to any database the updater does not own in the same change that
  adds them to the file. Keep every statement `add column if not exists`
  style: the updater re-runs the whole file against the live database.
- **The agent sandbox is CONTAINMENT, not restriction.** It exists so an
  agent can work freely without risking the host — NOT to limit what it can
  reach. Egress is open (link-local addresses excepted); the gate is the CAPABILITY, never reachability —
  every tool that CHANGES a service is a `propose_*`, gated by default (Policy lists the exemptions). What constrains a
  sandbox is that it holds NO credentials; provisioning one converts reach
  into ungated writes, so that is an explicit operator decision.
- **graphiti-core is imported lazily, and only by the two `integrations/`
  modules that need it: `graphiti_client.py` and the read module
  `graphiti.py`.** Importing it reads `SEMAPHORE_LIMIT` and arms telemetry,
  so the client sets both first (`graphiti.py` imports `graphiti_core.search*`
  function-locally, after `client._prepare_environment()`) and nothing loaded
  at application start may import the package. **Never call `Graphiti.search()`**
  — it mutates a module-level recipe that `add_episode` also reads, so one
  search changes extraction for the life of the process; search through
  `search_()` with a deep-copied recipe. `runtime/` may import
  `integrations/graphiti` (reads) and nothing that writes: not the client, not
  `graphiti_ingest`, not `neo4j_writer`.
- **The sandbox's only exit is `mcp.sync_source`, and content is captured at
  PROPOSE time.** The tool embeds the files in the proposal; the Executor
  writes exactly those bytes and never re-reads the sandbox — an agent that
  could swap content after approval would make review theater. ("Exit" means
  files leaving the sandbox for the repository; network egress is the bullet
  above.)
  `_mcp_servers_root()` must resolve to the SAME path in `runtime/tools.py`
  and `gateway/executor.py` (a guard test pins it).
- **A proposal has THREE verdicts.** Approve executes; Reject means "fix it
  and try again" and RESUMES the drafter, which redrafts; **Dismiss** means
  no action needed — WITHDRAWN, folds released, session closed, and the agent
  is NEVER resumed (a test makes the agent factory raise if any path tries).
  Rejecting to make something go away is the trap Dismiss exists to end.
  Dismissing an APPROVED proposal that is waiting on an outage is the second
  form: it withdraws the execution and resolves the task FAILED, never DONE
  (`.claude/rules/runtime-resilience.md`).
  Nothing feeds coaching automatically: the operator foregrounds the signals
  that matter case by case, and the coach reads everything — a selection is
  foregrounded context, not a fence.
- **A proposal's argument SHAPE is checked in BOTH tiers from ONE list —
  `contract.ARG_SPECS`.** The runtime hands a malformed draft back to the
  model (`tools._validate_proposal`, ModelRetry with every problem at once);
  `executor.execute()` runs the same `validate_action_args` over every action
  before the first runs. Adding a capability whose handler subscripts
  `args[...]` means adding its spec. Only SHAPE goes in the spec;
  world-state checks stay Executor-only because the world can change between
  propose and approve. Every `propose_*` tool carries `max_retries=10` —
  pydantic-ai's default of 1 fails the session on the second bad draft.
- **A "never guess X" docstring needs a tool that can READ X.** Agents will
  invent plausible values for any required argument nothing in the system can
  answer (hence `jira_list_projects`). Before coaching an agent for guessing
  — or for following bad documentation — check whether anything in the system
  could actually answer the question. And don't build a propose-time guard
  for a failure that is already LOUD (400 → FAILED → redraft); guards exist
  for failures that return 200 and bind nothing. The `ARG_SPECS` shape check
  above is not a second guard of that kind: it returns every problem of a
  draft to the model at once, where the Executor alone would surface them one
  failed execution at a time.
- **The event log is append-only, so write order is read order forever.**
  Emit an event *before* the thing it authorises — `proposal.decided` is
  written inside `gateway.approve_and_execute` before `executor.execute()`,
  never from an API route.
- **…but a record that authorises nothing needn't be written at all.** A
  heartbeat action may declare `ActionSpec.material` only if it is
  *non-authorising* AND *self-recording* — an explicit, parity-tested
  allowlist. Errors are always material; cadence liveness lives in
  `last_fired_at`, not in history. Don't quiet an action that authorises
  work.
- **A seeded heartbeat schedule that contacts the operator or the world is
  born disabled; an internal sweep may seed enabled, and each is listed
  here.** The first enable of every other seed (the EA contacts, `mail-poll`,
  `drain-window`, the graph verification sweep, the Jira hygiene task and the
  rest: `schema.sql` is the list) is an operator click on the event log; the table default is `enabled false`.
  The only internal sweep seeded enabled is `retry-sweep` (outage
  convergence — a strict no-op when nothing is parked and no dependency is
  recorded down, it re-drives only what was already authorised, and its only
  other writes are the probe results it records on a state change); `schema.sql` records the deviation where it
  is seeded. Seeding another enabled schedule means adding it to this list
  in the same change.
- **Task creation is a trigger, not a write and not a gate exemption.** Any
  active agent except the auditor may create a task and assign it to any agent,
  the orchestrator included, with `create_task` and no approval — no proposal,
  no inbox item (`await_result` waits on the outcome). Every created task is
  bounded at the one create seam (`api/backstops.py`): depth 3 and fan-out 25 by default (`task_max_depth`,
  `task_fanout_limit`), no self or cycle, an assignee required; a refusal comes back to the agent as
  text and is loud on the record (`task.refused`), never an inbox item. The run
  a task starts gates its own world changes like any other, an agent-created
  project parks its plan for the operator before round one, and the
  orchestrator's project runs still assign through `assign_work`. Orchestration
  is not star-shaped any more; never reintroduce a guard that keeps the
  orchestrator out of agent-created work. (The heartbeat action kind named
  `task.create` is a different thing: a schedule's lever, not an agent tool.)
- **The recall index is derived, and the tool's own echo is never in it.**
  `session_recall`/`session_recall_mark` are a rebuildable projection of
  `session.run_state` (FR-52, DL-214): never read them as the record, never
  write a path that depends on a row being there (the next `search_my_sessions`
  re-indexes lazily from the watermark). `extract` skips the tool's own calls
  and results, or the next search would find the last search's quoted text
  instead of the record. The text-search expression (`simple`, with `- _ / . @ :`
  turned to spaces) lives in `schema.sql`, `repo.RECALL_SEP_SQL` and
  `session_recall.SEP_RE`, pinned equal by a test; a generated column is not
  altered by `create table if not exists`, so changing it means dropping both
  tables. The agent comes from `ctx.deps`, never an argument, and every query
  filters on it in SQL. Indexing is chunked and bounded per tool call (a big
  session stays pending between calls) and a session that raises is parked
  until it changes: never make a path wait on "fully indexed".
- **A grant seed that widens a pack to the roster runs ONCE per database.**
  `schema.sql` re-runs on every release, so an unguarded `insert ... select`
  hands the pack, at the next update, to an agent the operator hired later with
  an explicit pack list that leaves it out. Gate it on a marker row in
  `app_setting` (`seed:session-recall`, `seed:skill-propose-all`) written by the
  next statement. A test that runs the seed block must delete the marker first.
- **Agents take `deps`** (`runtime/deps.py`). Every `agent.run()` needs
  `deps=TriageDeps(...)`; resume paths pass a fresh one on purpose. The tools
  read it for the agent id, the session and the consult chain, so a run
  without it fails inside its first tool call. The agents that hold no tools
  (the two auditors, the context summariser) take none.
- **Every proposal takes one path too:**
  `runtime/proposals.park_proposal()` — it pauses the run at its deferred
  call, persists the proposal, AND emits `proposal.created`.
  `tests/test_proposal_created.py` walks the source: a
  `repo.save_proposal(...)` anywhere but the seam fails the suite.
- **`events.emit()` is the only way to publish.** It writes to Postgres then
  fans out; the SSE stream is a projection of the table, never a parallel
  channel. **The emit-before rule is about *authorisation*, not
  bookkeeping:** a proposal authorises nothing — its *decision* does — so
  `proposal.created` lands after its save and that is not a violation.
- **No agent claim is trusted unverified — on every path that makes one.**
  `contract.claim_supported()` re-checks a quoted claim against the recorded
  source: a redraft's operator quote against the rejection feedback, a coach
  citation against the signal or event it points at, a steward quote against
  the stored document, and an email quote against the stored text of each
  mail the session was fed — at the triage park for the first draft and
  through the session→work-item link for every redraft and follow-on
  (`proposals.verified_email_evidence`,
  `gateway._recheck_against_session_sources`). It lives in `contract/`, not
  `gateway/`, because BOTH tiers need it and `runtime/` may never import
  `gateway/`. On the coach path the agent supplies the POINTER as well as the
  quote, so citing a signal the session was never fed is flagged exactly like
  a misquote. **`claim_matches_source:
  None` means NOT CHECKED** — never "checked and inconclusive". Don't fork
  the checker, and don't backfill the flag onto decided proposals — that
  would falsify the record.
- **Roster membership = a non-empty `role` column** (set only by schema seeds
  and `repo.hire_agent`). Plain `upsert_agent` registrations never join the
  roster — don't "fix" an agent's absence by upserting; hire it.
- **Grant revocation is a timestamp, never a delete.** `agent_grant` rows
  keep history so the idempotent schema seeds can't resurrect a revoked
  pack. An agent's charter capability list is GENERATED from grants at run
  start — never write capability lists into charter text.
- **Sessions are never destroyed.** Closing a conversation is a status flip
  (`converse.end_conversation`, guarded) with the transcript kept forever.
  The `agent:<id>:main` alias means "the CURRENT lane": the newest
  conversation IF it is open, and NONE when that newest lane is terminal —
  it deliberately does NOT fall back to an older still-open session
  (`nerve_gateway._latest_conversation(open_only=True)` is the one seam).
  Multiple concurrent sessions per agent is the TARGET architecture; never
  add code assuming one session per agent.
- **A note is written only through the gate, and a note is never a gate.**
  The note store (`agent_note`, DL-217) is written by exactly two Executor
  handlers (`memory.note_add`, `memory.note_retire`) after approval; there is
  no direct write path, not even for the operator, and no note is ever edited
  or deleted — retirement is a timestamp. The owner is the gateway's
  `proposer`. A note informs; it never changes what needs approval, and no
  code may read a note to decide a gate, a grant or an override.
- **Operator-scope authorship is proven from the record and fails closed.**
  An operator-scope note's quote must be found in words the RECORD attributes
  to the operator in that session (`repo.operator_words_for_session`:
  operator-actor events only, and a chat event only when its words are also in
  that session's transcript), never in a message's role: a triage prompt is the
  email, a chat part can carry attachment text, a nudge is a user prompt too.
  Never in a task's instructions either: `task.created_by` records who CALLED
  the create, not who wrote the text (remediation writes mail-derived text,
  the Task Board appends attachment text). The same proof is required to
  retire an operator-scope note, and a note's quote must clear the note path's
  own floor (four words, twenty characters) because `claim_supported` alone
  accepts "the". A new place the operator
  speaks qualifies only once it writes such a record and is added there;
  until then it does not.
- **The notes block is rendered by every fresh-run path through the one
  seam.** A fresh run's `team_section` comes from `notes.standing_sections`
  (the roster, then the notes), never from `roster.team_section` directly; a
  new fresh-run path that builds a prompt without it fails the source walk in
  `tests/test_agent_notes.py`. Resumes keep the prompt they were started under.
