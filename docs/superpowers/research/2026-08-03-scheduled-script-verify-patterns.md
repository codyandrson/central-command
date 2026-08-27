# Scheduled script + agent-verifies — pattern survey and Central Command mapping

> Research task, 2026-08-03. Answers the open question in `docs/STATUS.md`
> ("Agent workspace / sandbox — design decision owed" /
> cron→script→agent-verifies scenario) and composes with
> `docs/superpowers/research/2026-08-03-mcp-sandbox-creation-design.md` (the
> sandbox mechanism: k8s Job-per-exec behind a sandbox-runner service, gVisor,
> copy-in, one gated sync-back — not yet built).

## 0. The scenario, restated precisely

An agent has already built a script (it lives in the repo, having crossed
`mcp.sync_source` or an equivalent gated sync once already). On a schedule:
the script runs and does bulk mechanical work; **then** an agent evaluates the
run — reads output/logs, verifies success, and on failure investigates, fixes
(new sandbox round + gated sync-back), re-runs, then does gated follow-ups.

This is two problems wearing one name: (1) run a script on a timer and hand
its result somewhere durable, (2) hand that result to an agent with enough
structure that "did it work" is a cheap read, not an investigation. Central Command
already has a complete, working answer to a *different* pairing —
schedule → `task.create` → agent runs freely (`central_command/heartbeat/actions.py`
`_task_create`, `_ea_contact`) — which is the **cron→task** leg the STATUS.md
note says already works. What's missing is the leg *before* that: schedule →
**script execution**, with its result captured in a machine-checkable shape
the agent (or the heartbeat itself) can consume without running the whole
thing over again in an LLM.

## 1. Proven patterns surveyed

### 1a. Plain k8s CronJob + Job status (the boring native answer)

A `CronJob` creates a `Job` on schedule; the `Job` creates a `Pod`; the Job
object records `status.succeeded`/`status.failed`, `startTime`,
`completionTime`. Standard knobs: `successfulJobsHistoryLimit` /
`failedJobsHistoryLimit` (keep N finished Jobs around so logs are inspectable
after the fact — the Pod's logs die with the Pod by default unless something
reads them first), `concurrencyPolicy` (Forbid/Replace/Allow — Forbid is right
for anything that shouldn't overlap itself), `activeDeadlineSeconds` (the
built-in hang-timeout — a script that never exits is exactly the failure mode
a schedule must not let accumulate Pods forever).

**The native answer has no built-in "and then run this on completion" hook.**
Kubernetes does not ship a Job-completion webhook or trigger; "watch for Job
done, then act" is universally solved by something *external* polling or
watching the API:
- **A controller/informer** watching Job events (what Argo Events' Kubernetes
  Resource EventSource does, generically).
- **A poll loop** — literally `kubectl get jobs -w` or an equivalent client
  watch, or a periodic `list jobs where status changed since last check`.
- **kube-events / Kubernetes Event objects** — Jobs emit `Event` objects
  (`BackoffLimitExceeded`, `Completed`, etc.) that anything watching the
  Events API can react to, but consuming that stream reliably (dedup, replay
  on restart) is exactly the kind of infrastructure a homelab shouldn't build
  from scratch when a tick loop already exists (see §3).

Verdict: CronJob is the right unit for "run this on a timer," and gives free
history/logs (`kubectl logs job/<x>`, retained per the history limits) — but
"noticed it finished, now do something" is BYO in every deployment of this
pattern, native or otherwise. That's the actual gap this research is about.

### 1b. Argo Workflows / Argo Events

Confirmed via `argoproj.github.io/argo-events/concepts/architecture` fetch:
Argo Events' components are **EventSource + Sensor + EventBus (NATS
JetStream) + Trigger** — a Kubernetes-resource EventSource can watch Job
completion and a Sensor's Trigger can then create another K8s object (e.g. the
task-creation call) or hit an HTTP endpoint. This is a real, proven answer to
exactly "Job finished → fire a follow-up" — and it is also a second
scheduler, a message bus, two new CRD controllers, and a namespace's worth of
new moving parts, for a homelab that has one tick loop already reading a
Postgres table on a 10-30s cadence. arm64 support is fine (Argo ships
multi-arch images); the objection isn't portability, it's mass: adopting Argo
Events to solve "notice a Job finished" is buying a general-purpose
event-routing platform to answer a question Central Command's existing heartbeat
tick already answers for six other action kinds. Not disqualified if the
sandbox-runner's own trigger fan-out ever needs to be generic across many
consumers — but nothing in this scenario needs that yet.

### 1c. CI systems' scheduled-pipeline pattern

The instructive shape, not the tooling: GitHub Actions `schedule:` /
GitLab CI scheduled pipelines both do exactly "cron fires → a job container
runs the script → the platform records exit code + captures stdout/stderr as
the job log + optionally artifacts are uploaded from a declared path → a
downstream job or a status-check API reads the *result*, not the raw
transcript, to decide pass/fail." The contract a step exposes to the next step
is **exit code (pass/fail) + a small number of named files** (JUnit XML test
reports, coverage.json, a build manifest) — never "re-read the full log and
infer." This is the shape to borrow, not a system to adopt: Central Command has no
CI server and doesn't need one to borrow the contract.

### 1d. Temporal / DBOS-style durable execution

Both give you a step function that survives process death and resumes
mid-workflow — genuinely the right tool for "long multi-step workflow with
retries and human-in-the-loop waits," which is arguably a description of the
*whole* propose→approve→execute pipeline Central Command already implements by
hand with Postgres rows and `CallDeferred`. DBOS is already a noted-deferred
item in this repo (per memory: "DBOS is already a deferred item"), and nothing
in this specific scenario (schedule → one Job → one evaluation task) has the
multi-week-durability or complex-compensating-transaction shape that would
justify introducing a durable-execution engine now. The heartbeat's advisory
lock + `next_due` cache + skip-missed-not-replay policy is already a (much
smaller) purpose-built version of the same idea, scoped to what Central Command
actually schedules.

### 1e. How agent products already do "scheduled agent run"

Fetched the `schedule` skill shipped with this Claude Code setup
(`~/.claude/remote/plugins/.../skills/schedule/SKILL.md`): the pattern is
**cron fires → a fresh agent session starts, with a fully self-contained
prompt, and the agent does everything itself** (`create_scheduled_task` with a
`cronExpression`, no separate "script" stage — the agent IS the scheduled
unit). That is precisely Central Command's existing `_task_create` /`_ea_contact`
heartbeat actions today: schedule → `task.create` → agent runs freely. It
validates that Central Command's already-built leg is the standard shape for
"scheduled agent work" — and it also shows why this scenario is different: the
whole point of splitting off a script is that an LLM turn is the *wrong* tool
for "do the same bulk mechanical thing every night," and no agent product
surveyed splits a scheduled run into a cheap deterministic stage plus an agent
evaluation stage as a first-class pattern. Central Command has to compose that
split itself from primitives it already has, not import it.

## 2. The evaluation half: script/evaluator contract

Universal shape across CI systems, Unix conventions, and durable-execution
result passing:
- **Exit code** is the coarse pass/fail signal — cheap, always present, never
  sufficient alone (a script can exit 0 having silently done nothing, or
  written garbage).
- **A structured result file** (commonly `result.json` or similar) the script
  writes on its way out, containing at minimum: outcome, counts/metrics
  relevant to what it did, and paths to anything else it produced. This is
  the one artifact an evaluator (agent or code) reads first — cheap,
  structured, no log-parsing.
- **Log tail, not full log, in context.** Agents (and CI status checks) read
  the last N lines of stdout/stderr for the *why* when the structured result
  says failure — the full log stays available as an artifact, but is not
  pushed wholesale into an LLM's context by default.
- **A declared artifacts directory**, not "whatever files happen to exist" —
  the script and its evaluator agree in advance on where outputs land, so the
  evaluator's read is a `list_files(known_dir)` rather than a guess.

This is exactly the shape Central Command's own `ActionSpec.run` returns already —
every heartbeat action returns a small `dict` (`{"opened": ..., "reason": ...}`,
`{"checked": ..., "stalled": [...], "nudged": [...]}`) that `fire()` treats as
the structured result, decides material/quiet from, and embeds in
`heartbeat.completed`/`heartbeat.error`. **The contract to adopt for a script
run is the same shape**: exit code → ok/error, plus a `result.json` (or the
sandbox-runner's own summary payload) → the dict that fills the same role
`ActionSpec.run`'s return already fills for every other action kind.

## 3. Two options mapped onto Central Command

### Option A — heartbeat-native (recommended)

A new `ActionSpec` (e.g. `sandbox.run_script`) whose `run` calls the
sandbox-runner (the same Job-per-exec machinery the MCP-sandbox design already
specifies) with the script's already-synced repo path as the copy-in source,
waits for the Job to finish (bounded by `activeDeadlineSeconds`), and returns
a dict built from: exit code, stdout/stderr tail, and the contents of a
declared `result.json` if the script wrote one. `fire()` already logs this as
`heartbeat.completed` (success) or `heartbeat.error` (failure/timeout) with
zero new code in `engine.py`. The action then calls the *existing*
`_task_create` body (or its own thin wrapper around
`create_and_run_task`) to hand the owning agent a task whose instructions
embed the run record — literally the same call `_ea_contact` already makes,
with a different brief.

**Failure vs success, which creates a task:** make it a heartbeat-level
choice per schedule, following the `_dispatch_window`/`retry.sweep`
material-predicate precedent rather than inventing new machinery — the
action's own params can carry `on_success: none|task` and always create a
task `on_failure` (mirrors `ea.contact`'s stance of "loud on purpose, this
authorises an agent run": a routine clean pass costs no tokens by default,
same as the quiet-eligible actions; a failure always needs the agent, same as
`heartbeat.error` always being material). This is a parameter on the action,
not a new concept.

**What's built:** one `ActionSpec` (~as much code as `_dispatch_window`), one
small "call sandbox-runner and wait" helper, reuse of `_task_create`'s body.
**What's reused as-is:** the tick loop, the lease, the event log, the
Decisions/Tasks UI, the sandbox-runner Job mechanism (once slice 1 of the MCP
sandbox design ships), the material/quiet convention, `create_and_run_task`.

**Governance fit:** schedules stay governed *data* (`heartbeat_schedule`
rows) exactly like every other cron Central Command runs — no new manifest kind
for the operator to reason about, no drift between "schedules I can see in
the crons tab" and "schedules that actually exist." **Audit trail:** for
free — `heartbeat.fired`/`completed`/`error` already carries the result dict,
and the follow-up task's own events chain from there, same path as
`ea.contact`. **Failure modes:** a missed tick is a missed run (already the
documented skip-not-replay policy, acceptable for a homelab); a hung script is
caught by `activeDeadlineSeconds` on the Job plus the engine's own tick
timeout; the engine dying mid-run loses that one firing, same as every other
action today (no worse than the status quo). **Operational surface:** zero
new services — the sandbox-runner is being built anyway for the MCP arc, and
this reuses it as a client, not a second consumer.

### Option B — k8s CronJob-native

The script IS a `CronJob` manifest, deployed via the (also being designed)
gated deploy pipeline. Something has to watch Job completions and turn a
finished/failed Job into a task — candidates: a heartbeat action that lists
Jobs by label on every tick (a K8s API poll, functionally a hand-rolled,
narrower Argo Events EventSource), or an actual informer/controller
(effectively re-deriving Argo Events' Kubernetes Resource EventSource, or
adopting it outright).

**Governance fit:** worse — the schedule now lives as a k8s manifest (reviewed
once at deploy, via the gated deploy pipeline) *and* has to be cross-referenced
against the crons tab for the operator to get one picture of "what fires when"
— two systems of record for the same concept, the exact "two copies of the
same numbers is how they drift" failure this repo's own CLAUDE.md warns about
for docs, now in schedules. **Audit trail:** the Job's own status is visible
via `kubectl`, but nothing lands in Central Command's event log unless the watcher
writes it there — meaning the watcher becomes exactly as complex as
Option A's action, just triggered by a k8s poll instead of the tick loop it
already has. **Failure modes:** a CronJob whose controller-manager is briefly
down can miss a fire silently (k8s's own missed-schedule handling, a second
skip policy to reason about on top of the heartbeat's); nothing native surfaces
a hung Job to the operator without the same watcher Option A needed anyway.
**What's built:** a manifest-generation/deploy path for scripts-as-CronJobs
*and* a Job-completion watcher — strictly more new parts than Option A, for a
governance story that's strictly worse (two schedule systems instead of one).

### Comparison

| | A: heartbeat-native | B: CronJob-native |
|---|---|---|
| New parts | 1 ActionSpec + wait helper | CronJob-manifest deploy path + a Job watcher |
| Schedule lives as | governed Postgres row (crons tab) | k8s manifest (git + deploy pipeline) |
| Audit trail | free (heartbeat event chain) | must be built (watcher writes it) |
| Missed-fire policy | one (heartbeat's, already documented) | two, unreconciled (k8s's + heartbeat's if any) |
| Reuses sandbox-runner | yes, as intended client | no — competes with it as a second Job-launcher |

Option A wins on every axis and is smaller. Option B isn't wrong, it's just
solving a problem Central Command already has a working answer to, with a second,
worse-integrated answer.

## 4. Recommendation

**Option A.** Add one heartbeat `ActionSpec` (`sandbox.run_script` or similar)
that invokes the sandbox-runner Job for a script whose source already crossed
the one gated sync (same precondition the MCP sandbox design already
establishes for anything durable), captures exit code + log tail +
`result.json`, and on failure (always) / success (per-schedule param) calls
the existing `create_and_run_task` path with the run record in the brief —
same shape as `_ea_contact`'s "build snapshot → create task with it embedded."
No new scheduler, no new watcher, no new manifest kind. This is squarely
"compose what exists": tick loop, lease, event log, Decisions/Tasks UI,
sandbox-runner Job mechanism, `create_and_run_task`.

**Smallest slice:** ship this *after* MCP-sandbox slice 1 (sandbox mechanism,
no exit) lands, since it needs the sandbox-runner as a client but not slice 2+
(no gated exec result needs to cross `mcp.sync_source` — it's a read of a
Job's output, not a write to `servers/`). Concretely: one `ActionSpec`, its
`run` function (~40-60 lines, comparable to `_dispatch_window`), a
`test_heartbeat.py` case exercising success/failure/timeout, and the
`on_success`/`on_failure` param wired through `validate_action`'s existing
`choices` mechanism (no new validation concept needed).

**Deliberately NOT built:** no Argo Workflows/Events (buys a
general-purpose event-routing platform for a problem the tick loop already
answers six times over); no Temporal/DBOS durable-execution engine (this
scenario has no multi-week-durability or compensating-transaction need — the
already-deferred DBOS item stays deferred); no CronJob-as-schedule-of-record
(would fork "what fires when" into two systems); no new Job-completion
watcher/informer (the heartbeat tick already polls on a cadence — reusing it
IS the watcher).
