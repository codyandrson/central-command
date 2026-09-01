---
name: setup
description: Install Central Command from scratch on this machine — the guided, nothing-skipped setup for a user who has an LLM API key and nothing else. On the podman substrate, the agent's job is elicitation and diagnosis only: it writes answers into deploy/single/.env and runs the deterministic `./setup.sh` (ten phases: validate/preflight/fetch/llm/stack/app/verify/test/boot/demo, PASS/WARN/FAIL/USERACTION output, exit 0/1/2/3 — 3 means the run stopped for the operator), reading `./setup.sh diagnose`'s bundle on failure rather than freehanding fixes, and ENDING ITS TURN on any non-zero exit after surfacing the outcome. Detects an existing installation and routes updates through `./update.sh` (import/plan/apply, with version gate, automatic DB backup and stop/restart gates) instead of re-installing. The single-node profile now includes the sandbox (rootless-podman backend) and crawler alongside postgres/LiteLLM/Neo4j/Graphiti/optional n8n. The multi-node k3s substrate has its own sibling driver, `./deploy/k3s/setup.sh`, with the same output protocol and exit taxonomy but six phases — no test/boot/demo; the agent conducts those steps there. Either way it ends with the onboarding interview and a working dry-run demo. Use when the user says "/setup", "install Central Command", "set this up", or "get me up and running".
---

# Central Command setup — zero to functioning

**The rule (2026-08-25 redesign, `docs/superpowers/specs/2026-08-25-deterministic-setup.md`):
if you are composing a command that mutates anything, you are off the rails —
the script does that.** On the podman substrate, `deploy/single/setup.sh` is
the whole install. Your job is: (a) elicit answers and write them into
`deploy/single/.env`, (b) run `./setup.sh` (or have the user run it) and read
its PASS/WARN/FAIL lines and exit code, (c) on failure, run `./setup.sh
diagnose` and reason over `setup-diagnostics.txt`, then name the fix — never
freehand a replacement command, (d) conduct the onboarding interview
(Phase 2), (e) verify integrations and walk the go-live checklist
(Phase 3). Delegate read-only investigation
("where is X defined", multi-file config reads) to an Explore sub-agent;
never end a turn mid-phase with no text.

Rules that hold for the whole run:

- **The exception-handler contract (2026-08-27,
  `docs/superpowers/specs/2026-08-27-setup-update-contract.md`): the script
  is the spine, you are the exception handler.** On ANY non-zero exit from
  `setup.sh` or `update.sh` — 1 (failure), 2 (warnings), or 3 (user action)
  — you STOP AND END YOUR TURN after surfacing exactly what happened: quote
  the FAIL/WARN/USERACTION lines, say what they mean, and (for failures) what
  you propose. You may run the read-only rungs first (`./setup.sh diagnose`,
  read `setup-log.txt`, read the bundle) — but the decision about what
  happens next is the operator's, made in conversation, before anything is
  re-run or changed. Autonomous continuation is for exit 0 only.
- **Exit 3 is a gate, not an error.** The last USERACTION line names the
  operator's move (add/fix models in the LiteLLM UI, stop the API before an
  update, restart it after). Relay the script's own instructions — for the
  llm gate that includes the UI URL, where the credential lives (by NAME:
  LITELLM_MASTER_KEY in deploy/single/.env — never print the value, or
  UI_USERNAME/UI_PASSWORD if the operator set them), and the
  expected alias list. When the operator says they've acted, re-run the same
  phase; it is idempotent and verifies.
- **Discovery evidence, if it exists, is part of your briefing.** Before
  Phase 0, check for `deploy/discovery.out/discovery-report.md` — /discover's
  converged environment map (gitignored; it names internal hosts, so its
  contents stay in this conversation, never in tracked files). If present,
  read it (and `discovery.out/discovery.env`, the machine-readable
  classifications) BEFORE eliciting: a discovery-verified mirror pre-fills
  the matching seam question (the operator confirms instead of recalling),
  and `DISCO_TLS_INTERCEPT=1` tells you the CA story must be settled before
  `fetch` hits it. Discovery output is EVIDENCE informing what you propose
  into `.env` — never source it or copy it wholesale, and a
  `DISCO_INSECURE=1` diagnostic never translates into disabling TLS
  verification in any deploy config: the fix it indicates is trusting the
  corporate root CA.
- **Existing install?** Before Phase 0, check: if the repo has an `upstream`
  branch (update.sh-initialized) — this is a deployment, not a fresh target.
  Read the tail of `deploy/single/setup-log.txt` for where it last stopped.
  A new release zip goes through `./update.sh import <zip>` → `plan` (show
  the operator the plan output and version gate) → on their explicit
  approval → `apply` — never through a fresh install, and never by
  extracting a zip over the tree. `setup.sh` itself refuses (exit 3) to run
  a full pass over a tree with an unapplied import.
- **Phased with gates — now enforced by `setup.sh` itself.** The full run
  stops at the first phase that hard-fails; don't skip ahead of it, and don't
  re-derive what a failed phase already told you to fix.
- **Resumable = re-run.** Every phase is idempotent (`./setup.sh <phase>`
  re-runs just that one); there is no state file and none is needed.
- **Never print secret values.** `.env` and `secrets.yaml` contents stay out
  of your output, and so does `setup-diagnostics.txt`'s content beyond what
  it already redacts (it records key NAMES only) — refer to keys by name.
- **`setup.sh` now runs the whole ride (2026-08-28): ten phases, ending in
  `test` (the pytest gate, via the venv), `boot` (elicits the operator name
  on a terminal, starts the API detached — `./setup.sh stop` is its
  counterpart) and `demo` (fixture email → the operator approves in the
  cockpit → dry-run provenance verified).** Do not hand-conduct those steps
  on the podman substrate any more — run `./setup.sh` and interpret. The
  late phases skip by probing reality (healthy API skips test+boot; a
  decided proposal skips demo), so re-runs converge. A red `test` phase is
  a real defect — diagnose it, don't wave it through.
- **The interview's `.env` answer is only `CC_OPERATOR_NAME`, and `boot`
  elicits it interactively** — the fuller onboarding interview (operator
  episodes into the graph) remains yours to conduct, any time after boot.
- **Capability manifest and vlogs disclosure are mandatory.** `./setup.sh
  verify` prints what this profile installed vs. the k3s deployment, naming
  vlogs (the log console) as the one deliberate omission — podman doesn't
  produce the CRI-format logs Fluent Bit tails. Don't paraphrase this away;
  read it out to the user.

## Substrate — ask this once, up front

> Will Central Command run on **this machine alone** (podman — the default, and
> the giftable profile), or across the operator's **multi-node k3s cluster**
> (`deploy/k3s/`)?

Record the answer. Everything below "Podman substrate" is that path; the
**k3s substrate** (bottom of this file) has its own driver,
`./deploy/k3s/setup.sh`, under the same output/exit contract (six phases —
no `test`/`boot`/`demo` there) — the conductor rules above apply verbatim
on both.

---

## Podman substrate

### Phase 0 — elicit into `deploy/single/.env`

```bash
cd deploy/single
cp env.example .env && chmod 600 .env    # skip the copy if .env already exists; still chmod
```

Elicit each of these and write it into `.env` (never run the install steps
yourself — `./setup.sh` owns everything mechanical, including generating the
credentials further down the file):

- **The LLM provider is NOT elicited into `.env` (2026-08-30).** Tell the
  operator up front what is coming: the `llm` phase brings LiteLLM up,
  creates the four required aliases (`cc-default`, `graphiti-llm`,
  `cc-embedding`, `gpt-4.1-nano`) as skeletons in the proxy's database, and
  **pauses (exit 3)** for THEM to enter the provider in the LiteLLM UI —
  model ids, `api_base`, the key (stored encrypted in the proxy). Re-running
  `./setup.sh llm` validates every alias with a real request and continues.
  Have them ready: endpoint URL (the one the CONTAINER dials —
  `host.containers.internal`, never `127.0.0.1`, for a server on this
  machine), the key (`none` if the server ignores it), and the model ids
  (`CC_LLM_BASE_URL=… CC_LLM_API_KEY=… ./discover-llm.sh models` lists them
  directly). Self-hosted embedders on another server are just a different
  `api_base` on the `cc-embedding` row.
- **The embedding-permanence warning** — say it in one sentence: the
  embedding choice is effectively permanent, because `CC_EMBED_DIM` gets
  written into the Neo4j vector index. Changing the embedder later means
  dropping the index and re-embedding the whole graph. **Leave
  `CC_EMBED_DIM` blank** — the `llm` phase of `setup.sh` measures it through
  the `cc-embedding` alias and writes it back, and refuses to overwrite a
  different value that's already there.
- **Enable flags** — `CC_ENABLE_N8N` (default 0; only needed by an
  n8n-backed integration, today Gmail), `CC_ENABLE_CRAWLER` (default 1; the
  Chromium crawl service — rung 2 of docs ingestion, large local image; rung
  1 plain HTTP fetch still works without it), `CC_ENABLE_SANDBOX` (default 1;
  this profile's sandbox backend is rootless podman, not the k3s profile's
  gVisor — weaker isolation, say so plainly), `CC_AIRGAP` (default 0; set to
  1 when no public package index is reachable — the `app` phase then
  installs from `requirements.lock` instead of resolving).
- **Air-gap mirrors, if `CC_AIRGAP=1`.** This is still elicitation — mirrors,
  CA bundles, and internal URLs are facts only the user knows, and `setup.sh`
  can't discover them. If `deploy/discovery.out/` exists (see the briefing
  rule above), its report already verified these answers — offer each as a
  pre-fill for the operator to confirm; if it doesn't and the network is
  restricted, propose running /discover first rather than eliciting blind.
  Record `CC_REGISTRY_DOCKERIO` / `CC_REGISTRY_GHCR` (the
  container registry mirror) and anything `deploy/AIRGAP.md` calls for (read
  it now if air-gapped). Reachability itself is `setup.sh preflight`'s job —
  its `package-indexes` check reports PASS/WARN/FAIL; you don't probe by hand.
- **Integration choices** (these still feed the ROOT `.env` in the `app`
  phase, not `deploy/single/.env` — collect now, write later):
  - **Network trust** — ask ONCE, before Jira/Confluence, if either is
    self-hosted: does reaching it need a client cert (mTLS) or a private CA
    bundle, or does plain HTTPS work? One shared seam
    (`integrations/http.py`). If yes, elicit `CC_CA_BUNDLE` and
    `CC_CLIENT_CERT`/`CC_CLIENT_KEY`.
  - **Jira** — Cloud or Server/Data Center (a real fork: Cloud uses
    email+API token, DC uses a bearer PAT and ignores email —
    `central_command/config.py`'s `jira_auth_mode`). Elicit `CC_JIRA_BASE_URL`
    either way, then `CC_JIRA_EMAIL`+`CC_JIRA_API_TOKEN` (Cloud) or a PAT
    into `CC_JIRA_API_TOKEN` with `CC_JIRA_AUTH_MODE=bearer` (DC). Say
    plainly that DC endpoint shapes are coded to docs, not yet proven live —
    Phase 3's real read is the verification. No Jira: record that; jira-pack
    agents fail loudly on reads until it's configured.
  - **Confluence** — same Cloud/DC shape into `CC_CONFLUENCE_BASE_URL` /
    `CC_CONFLUENCE_EMAIL` / `CC_CONFLUENCE_API_TOKEN` /
    `CC_CONFLUENCE_API_FLAVOR` / `CC_CONFLUENCE_AUTH_MODE`. Ask which macro
    profile matches their instance
    (`skills/confluence/references/instance-profiles.md`: `cloud-free` or
    `work-server-9.2`) — if neither matches, record `CC_CONFLUENCE_PROFILE`
    empty rather than guessing a macro that might not exist on their
    instance. No Confluence: record that.
  - **Email** — Gmail (needs `CC_ENABLE_N8N=1`, wired via the n8n UI on
    `http://127.0.0.1:5678` once `setup.sh` brings it up; or, migrating from
    a prior instance, offer decrypt-under-old-key/re-import as the
    operator-assisted alternative to redoing OAuth), Exchange (no native
    adapter yet — say so before asking anything else; elicit specifics about
    their existing PowerShell tools per
    `docs/reference/work-environment-compatibility.md` §2.3/§7 as design
    input, don't invent config), or manual-feed/none (`POST /api/emails`
    with a raw message; `fixtures/emails/` has samples the `demo` phase
    uses).
  - Further integrations ship as capability packs — list what
    `central_command/runtime/packs.py` actually defines rather than promising
    from memory.

Gate: every ELICITED variable above is in `deploy/single/.env` (or explicitly
declined and recorded), and the integration choices are noted for the `app`
phase.

### Phase 1 — run `./setup.sh`

```bash
./setup.sh
```

This runs all ten phases — `validate → preflight → fetch → llm → stack →
app → verify → test → boot → demo` — in order, stopping at the first hard
failure. Each line on stdout is
`PASS|WARN|FAIL|USERACTION <check-name>: <message>`; subprocess detail goes
to stderr. Exit code **0** = clean, **1** = hard failure, **2** = completed
with warnings, **3** = stopped for the operator's move. Read every line — a
WARN is not nothing (e.g. `node-version` WARN means the cockpit won't be
built).

The late phases are interactive where they must be: `test` is the pytest
gate (sequential, ~11 minutes — a red suite is a real defect in the install
or the repo; diagnose it, don't wave it through), `boot` elicits the
operator's name on the terminal and starts the API detached (`./setup.sh
stop` is its counterpart), and `demo` feeds a fixture email, waits for the
operator's approval in the cockpit, then verifies the dry-run provenance.
If the demo wedges, Phase 3's recovery shapes apply — never re-POST the
same fixture.

If the user is running it themselves instead of you, have them paste the
output; interpret it the same way.

**On any non-zero exit — stop. This is the contract, not advice:**

1. Optionally run the read-only rungs: `./setup.sh diagnose` (writes
   `setup-diagnostics.txt` — pod/container states, last 100 log lines per
   pod, `verify.sh` output, tool versions, `.env` key NAMES only) and read
   the tail of `setup-log.txt`. For a network-shaped failure (fetch
   timeouts, TLS/cert errors, 407s, registry pull failures), also read
   `deploy/discovery.out/` if it exists — the report for the prescription,
   `raw/<key>.*` for the evidence — before proposing a cause; discovery
   already classified the failure mode you are looking at. If it does not
   exist and the failure smells like a restricted network, the proposal to
   surface is "run /discover", not another iteration against `setup.sh`.
2. Surface the outcome to the operator: the exact FAIL/WARN/USERACTION
   lines, what they mean, and — for a failure — your proposed cause and fix.
3. **End your turn.** The operator decides what happens next. Only after
   they respond do you correct `.env` and/or re-run `./setup.sh <phase>`.

Never compose a replacement command for whatever `setup.sh` was doing — if
the fix is a real command (e.g. `loginctl enable-linger`), it's one the
script already named in its WARN/FAIL line; otherwise the fix is almost
always "re-run `./setup.sh <phase>`" after correcting `.env`.

**Exit 3 in the `llm` phase (the model gate) is EXPECTED on every fresh
install** — it is the pause where the operator enters the provider into
the LiteLLM UI, and it also fires if a PLACEHOLDER was left in, an
invariant was broken (the `openai/chat_completions/` prefix on
`graphiti-llm`), or a probe failed after filling in. The script's stderr
already printed the full hand-off: the UI URL (`http://127.0.0.1:4000/ui`),
the credential's location by name, the alias table with what each needs,
and the direct-vs-proxy discrimination command (direct success + proxy
failure = the alias row is wrong; direct failure = wrong URL/key). Relay
it, let the OPERATOR do it, then re-run `./setup.sh llm`. You never
register, edit, or delete a model — not via curl, not via the UI, not at
all.

Gate: `./setup.sh` exits 0 (or 2 with WARNs the operator has read and
accepted — a WARN is a stop-and-discuss point, not a drive-past).

### Phase 2 — the onboarding interview

`boot` already elicited `CC_OPERATOR_NAME` into the root `.env` (everything
else — the virtual key, `CC_NEO4J_PASSWORD`, `CC_EMBED_DIM`/`CC_EMBED_ALIAS`,
the LiteLLM db url and salt, `CC_EXECUTOR_MODE=dry_run` — the `app` phase
already wrote). The fuller interview below is yours to conduct, any time
after boot. Also write in the Jira/Confluence/network-trust values you
elicited in Phase 0 if the root `.env` doesn't already carry them — check
before re-typing anything.

You are the interviewer. The answers here are OPERATOR INPUT — trusted
evidence, not agent claims — so nothing in this phase rides the approval
gate: config values go to `.env`, facts go to the graph as operator-direct
episodes, and confirming each section back to the user in this conversation
IS the review.

Work through the sections in order. For each: elicit, summarize back what
you will record, get an explicit yes, then write it. Skip nothing; if the
user declines a section, record that they declined. **One `AskUserQuestion`
per section, then follow-ups until it has real substance** — a team member
needs role, expertise and reporting line, not just a name — never bundle
sections into one question.

1. **Identity.** Confirm the name `boot` recorded (`CC_OPERATOR_NAME` in
   the root `.env`) is what the agents should call them. It also becomes
   the provenance actor on their decisions (`human:<name-slug>`).
2. **Work environment.** What they do, where they work, the systems that
   matter (their Jira, their repos, their wiki), what a normal week looks
   like. One episode per topic, each 1–5 sentences of DISTILLED claims
   (never pasted raw answers). When the user says when a fact became true,
   state it in the claim — a dateless claim is recorded as becoming true
   today.
   ```bash
   python scripts/onboard_episode.py "Work environment: <topic>" "<distilled claims>"
   ```
3. **The team.** For each person: canonical full name, role, who they report
   to, what systems/areas they are expert in, anything known about current
   load. One episode per person (`"Team: <full name>"`). These seed the
   staffing doctrine's competency map — an empty graph can't route work.
4. **Working preferences.** Approval appetite (what they want to review vs
   would delegate as trust builds), communication style, quiet hours, pet
   peeves. One episode (`"Operator preferences"`) — these are facts agents
   will read, not charter text.
5. **Summary episode.** One final episode (`"Onboarding interview
   2026-XX-XX"`) recording that onboarding ran, what was covered, and what
   was declined — so the interview itself is on the record inside the
   system, not only in this chat's transcript.

Gate: the user confirms the summary; every episode command returned an
acknowledgement.

### Phase 3 — integration proof + go-live

`./setup.sh boot` and `demo` already did the mechanical part on this
substrate: first boot self-hired the roster, and the operator watched the
demo loop (fixture email → their approval in the cockpit → dry-run
provenance). Be honest about what renders where: the
founding agents are seeded by `schema.sql` at database init and don't carry
template markers yet, so THOSE charters read "the operator" — a recorded
future rebuild. What does use the interview's values today: any agent hired
from the menu (`POST /api/agents/hire`) renders `<<operator_name>>`, the
provenance actor on every decision is `human:<name-slug>`, and every agent
reads the interview's episodes from the graph.

Gate: the roster lists its agents (`GET /api/agents`) and, if the cockpit was
built, `GET /` serves it — the cockpit is served BY uvicorn on this same port
in this profile; if `/` 404s, the web build was skipped (check the
`cockpit`/`node-version` line from `./setup.sh app`).

The install ends inside a working demo the user watches, not a success
message — `./setup.sh demo` runs that loop here; on the k3s substrate you
conduct it by hand: feed one fixture email from `fixtures/emails/` via
`POST /api/emails`, watch the proposal land in the Decisions Inbox, have the
user approve it, and confirm the dry-run execution + provenance in the event
log. Either way, these are the shapes for conducting or recovering it, so
you don't guess them:

- `POST /api/emails` takes exactly one field —
  `{"text": "<the raw RFC-822 message>"}` (`EmailIn` in
  `central_command/api/routes.py`).
- **There is no `GET /api/decisions`.** The Decisions Inbox list is the
  `decisions.list` RPC over the cockpit WebSocket (`nerve_gateway.py`); over
  HTTP you have `GET /api/proposals/{id}` and the
  `POST /api/proposals/{id}/{approve,reject,dismiss}` verbs.
- **If the fixture email doesn't process cleanly** (a stuck/failed ledger row
  from a cancelled run, a slow model, etc.), do NOT just re-`POST` the same
  fixture — `enroll_email` treats a repeat Message-ID as terminal
  ("duplicate": true) regardless of the row's actual state, so re-posting is
  a silent no-op. Recover with `POST /api/dispatch/step` (claims and runs the
  next eligible item directly) or, for a row that reached `FAILED`,
  `POST /api/work/{item_id}/requeue`.

If Jira and/or Confluence were configured in Phase 0, run one real read
against each now and show the result — this is the FIRST proof either
integration actually works, not a formality, especially on Server/DC where
the endpoint shapes were coded to docs and never proven live before this
moment. A failure here is a real finding (wrong base URL, wrong auth mode,
missing cert trust) — diagnose it against the error, don't wave it through.

**Confluence macro harvest (offer it now, while the operator is engaged).**
If Confluence verified above, offer the sampler-page harvest —
`skills/confluence/references/macro-discovery.md`'s "sampler-page harvest"
section is the procedure; follow it, don't paraphrase it. Short form: you
author the BUILT-IN macros onto a scratch page from `storage-format.md`'s
verified recipes and check each via `body.view` round-trip; the OPERATOR
inserts the marketplace macros (Table Filter, Forms, Scroll, Epic Sum Up,
test-report family — the exact no-recipe list is in `macro-discovery.md`)
onto a "Central Command Macro Sampler" page via the macro browser, which emits
correct-by-construction XML; then you harvest that page's `body.storage` and
record every macro block as an instance-verified recipe stamped with date +
instance version. Optional and skippable — record the choice either way; the
per-use discovery doctrine still covers whatever wasn't harvested.

Only after they have seen the demo loop (and the Jira/Confluence reads, if
configured) does GO-LIVE happen — and it is a PROMPTED CHECKLIST, never a
default and never skipped silently (The operator, 2026-08-27: "off until onboarding
finishes" is right, but off-by-default with no prompted turn-on is a cliff —
the first instance sat in dry_run for a day of approvals nobody knew were
simulated). Walk each item as its own question, one line on what it does,
apply what they choose, and record the choices as one operator episode
("Go-live <date>"):

1. **Executor**: flip `CC_EXECUTOR_MODE` to `live` + restart. Until this,
   every EXECUTED proposal was a `[dry-run]` simulation — say that plainly.
   Flipping it is the USER'S choice, never yours. WHERE it flips is
   substrate-specific: podman profile → root `.env`. k3s substrate → a
   systemd drop-in (`/etc/systemd/system/cc-uvicorn.service.d/*.conf` with
   `[Service]` `Environment=CC_EXECUTOR_MODE=live`, then daemon-reload +
   restart) — the tracked unit pins `Environment=CC_EXECUTOR_MODE=dry_run`
   as the clean-install safe default, and a systemd `Environment=` BEATS
   pydantic's env_file, so editing `.env` silently does nothing there
   (bit round 4, 2026-08-27: go-live "took", the honest banner said
   otherwise). Verify with the `status` RPC's `executorMode`, not the file.
2. **Email feed**: `CC_FEED_ENABLED` + the `mail-poll` schedule. New-mail-only
   is the default posture (`feed_query` is `newer_than`-scoped); populating
   the BACKLOG is a separate deliberate step needing `CC_BACKLOG_CUTOFF_DATE`
   — offer it, don't assume it.
3. **Dispatch drain**: `CC_DISPATCH_ENABLED` + the `drain-window` schedule
   (the approval-limit valve keeps it human-paced).
4. **Recurring schedules**: list what is actually seeded
   (`GET /api/heartbeat/schedules`) and let them pick — EA contacts
   (morning-report/checkin/digest/week-ahead), jira-hygiene, maintenance
   sweeps, litellm-discovery. Enable via
   `POST /api/heartbeat/schedules/<id>/toggle` `{"enabled": true}` (the
   PATCH route does NOT take `enabled`).

Anything declined stays off and is recorded as declined — the Crons tab
flips any of them later.

**The tour comes AFTER go-live, never before.** The tour produces real
knowledge (the operator's contact doctrine, tour-state episodes) through
gated proposals — run in dry_run those approvals simulate and the knowledge
evaporates, which is exactly what happened on 2026-08-27: a full tour's
worth of episodes read EXECUTED and wrote nothing.

Your LAST act is to hand them to their team and get out of the way. Create
the EA-hosted team tour (a product contact kind, not a setup script, so it's
re-runnable long after this session ends), DISABLED (the table default), then
fire it once:

```bash
curl -sX POST localhost:8080/api/heartbeat/schedules -H 'content-type: application/json' \
  -d '{"id":"team-tour","name":"Meet the team","schedule_kind":"at",
       "schedule":{"at":"2000-01-01T00:00:00+00:00"},
       "action_kind":"ea.contact","action_params":{"kind":"onboarding_tour"}}'
curl -sX POST localhost:8080/api/heartbeat/schedules/team-tour/run
```

(The past `at` means `compute_next_due` returns None — it can never fire on
its own, and the run-now button works regardless.) Then tell them their EA is
waiting in the cockpit to introduce them to the team, and stop: the tour is
the EA's conversation, not yours.

### Uninstalling (podman)

`setup.sh` has no teardown subcommand — this remains a deterministic but
manual sequence, prefix-aware (never hardcode names — a changed
`CC_POD_PREFIX`/`CC_NETWORK` would otherwise leak secrets and the network):

```bash
cd deploy/single
PFX="$(grep ^CC_POD_PREFIX= .env | cut -d= -f2)"
NET="$(grep ^CC_NETWORK= .env | cut -d= -f2)"
podman kube down --force optional-crawler.yaml 2>/dev/null || true
podman kube down --force optional-n8n.yaml 2>/dev/null || true
podman kube down --force stack.yaml
podman kube down --force stack-llm.yaml   # LLM half LAST — everything else talks to it
podman secret rm "${PFX}litellm" "${PFX}neo4j" "${PFX}graphiti" "${PFX}n8n" 2>/dev/null
podman network rm "$NET"
```

`--force` removes the volumes (a wipe); omit it to keep data for a restart.
Remind the user their `.env` backup is the only copy of the never-rotate keys
(`LITELLM_SALT_KEY`, `N8N_ENCRYPTION_KEY`) — deleting the install tree
without it orphans any kept volumes.

---

## k3s substrate — same contract, driven by `deploy/k3s/setup.sh` (2026-08-27)

The k3s branch now has its own deterministic driver, the sibling of the
podman one: **`./deploy/k3s/setup.sh`** runs `validate → preflight → llm →
stack → app → verify` with the same output protocol, exit taxonomy
(0/1/2/3), `setup-log.txt`, and `diagnose` bundle. All the conductor rules
at the top of this file apply UNCHANGED: stop and end your turn on any
non-zero exit; exit 3 gates are the operator's move; never compose a
mutating command — every mechanical step in `deploy/k3s/README.md` §1–§6
and §9 is a phase of the driver now. The README remains the reference for
the *why*, the placement table, §8's instance-data decisions and rollback.

> **Before you wipe or deploy anything:** if the operator's Claude Code CLI
> routes through this stack's LiteLLM `/anthropic` passthrough, re-point it
> to direct Anthropic FIRST — otherwise the installer loses its own model
> mid-run, at the exact moment the proxy goes down.

**Elicitation (before the driver).** Two answer files:

- `deploy/pi/.env` — run `./deploy/k3s/init-env.sh` (idempotent); it
  generates every GENERATED value and prints the ELICITED ones to collect.
  Topology: placement is the role labels `cc-role/anchor` /
  `cc-role/compute` (preflight names the label commands if missing), and
  `CC_COMPUTE_SSH` is the compute node's ssh target (defaults to the
  homelab chromebox). `CC_COCKPIT_ORIGIN` is the cockpit's tailnet URL —
  without it, tailnet browsers 403 on the WS upgrade (README §6).
  On a RESTORE, the two never-rotate keys (`LITELLM_SALT_KEY`,
  `N8N_ENCRYPTION_KEY`) must carry their existing values BEFORE init-env
  runs — a restore under a different key leaves rows undecryptable.
- Integration choices (Jira/Confluence/email/network-trust) — identical
  elicitation to the podman substrate's Phase 0; the values go into the
  root `.env` (the driver creates it with `CC_EXECUTOR_MODE=dry_run`).

**Run the driver.**

```bash
./deploy/k3s/setup.sh                # all six phases, stops at first FAIL/gate
```

What its phases own (so you can interpret, not so you re-derive): `llm`
brings up ONLY the LiteLLM trio, creates every alias `model-preferences.yaml`
declares as a SKELETON (`register-models.py`, create-only) and **pauses
(exit 3) on a fresh catalog** for the operator to enter providers, model
ids and keys/credentials in the LiteLLM UI; the re-run checks the
invariants (the `openai/chat_completions/` bridge prefix; rerank's
`/v1/rerank` api_base), applies the routing policy (`policy.py --apply` →
restart → `--check`), mints the virtual keys, then probes `cc-default`,
`graphiti-llm` (a structured Responses round trip) and
`cc-embedding` through the proxy — a probe failure is the same
exit-3 gate.
`stack` builds the per-arch images only if missing and rolls out every
manifest. `app` installs the venv/cockpit and the systemd units, holding
`cc-uvicorn` enabled-but-stopped, and ends at the **first-boot exit-3
gate** — that gate is where the onboarding interview happens.

**At the first-boot gate:** conduct the onboarding interview (identical to
the podman substrate's Phase 2 above, except `CC_OPERATOR_NAME` is yours to
elicit here — land it and the episodes first, BEFORE starting uvicorn:
first boot hires the roster from what's already in `.env`/graph), write in
the Phase-0 integration values if the root `.env`
does not carry them yet, then have the operator:

```bash
sudo systemctl start cc-uvicorn
./deploy/k3s/setup.sh verify --clean-install   # zero failures is the gate
```

Then read `deploy/k3s/README.md` §8 with the operator and decide each
instance-data item deliberately — on a fully-clean deployment the answer is
"restore nothing" (§7). The demo (fixture email → approval → provenance,
Jira/Confluence first-read verification, macro harvest offer, team tour)
and the GO-LIVE checklist are identical to the podman substrate's Phase 3
above (there the demo is hand-conducted, per its shapes) — with the
k3s-specific executor flip: a systemd drop-in, not the
`.env` (the tracked unit pins dry_run and systemd `Environment=` beats
pydantic's env_file; verify via the `status` RPC's `executorMode`).

**Uninstalling (k3s).** Per-manifest: `sudo k3s kubectl delete -f
deploy/k3s/<file>.yaml` for each of `20-postgres`, `40-graph`, `50-n8n`,
`60-sandbox`, `61-mcp`, `70-crawler`, `80-vlogs` (add `30-litellm` +
`31-litellm-rbac` only for a FULL wipe — deleting a manifest's PVCs deletes
the data), plus `sudo systemctl disable --now cc-uvicorn cc-nerve
cc-graph-bolt cc-sandbox-runner cc-backup.timer`. Same `.env`-backup
reminder, for `deploy/pi/.env`. For a FULLY-clean slate, also purge the
gitignored leftovers a manifest wipe misses — the minted
`~/.cc-*.kubeconfig` files (stale tokens outlive their namespaces) and the
podman profile's litter in a shared checkout (`deploy/single/.env` — back it
up first — plus its rendered yamls and `setup-diagnostics.txt`); the full
list is `deploy/k3s/README.md` §8's purge block.
