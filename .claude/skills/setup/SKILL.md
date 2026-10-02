---
name: setup
description: Install Central Command from scratch on this machine, or carry an existing deployment through an update — the guided setup for an operator who has an LLM API key and nothing else. On the podman (single-node) substrate the agent conducts one loop and never mutates anything itself. It runs ./setup.sh, reads the ledger and the PASS/WARN/FAIL/USERACTION lines it prints (exit 0/1/2/3, where 3 means the run stopped for the operator), and then either changes the .env key a FAIL line names and runs ./setup.sh again, or reports the row as a repository defect with ./setup.sh report. It may change .env and run ./setup.sh, ./setup.sh status and ./setup.sh report, and nothing else; the operator answers ./setup.sh configure in their own terminal, acts on the checklist's bold rows (the LiteLLM catalog, the demo approval) and runs ./update.sh for updates. The procedure is deploy/single/CHECKLIST.md, generated from deploy/single/steps.tsv. The multi-node k3s substrate has its own driver, ./deploy/k3s/setup.sh, with the same output protocol and exit taxonomy. Either way the install ends with a working demo and hands off to the cockpit, which asks the operator's name on first run and lets the EA-hosted team tour ask the rest. Use when the user says "/setup", "install Central Command", "set this up", or "get me up and running".
---

# Central Command setup — zero to functioning

**The rule (2026-08-25 redesign, `docs/superpowers/specs/2026-08-25-deterministic-setup.md`):
if you are composing a command that mutates anything, you are off the rails —
the script does that.** You conduct; the driver installs. Delegate read-only
investigation ("where is X defined", multi-file config reads) to an Explore
sub-agent, and never end a turn with no text.

## Substrate — ask this once, up front

> Will Central Command run on **this machine alone** (podman — the default, and
> the giftable profile), or across the operator's **multi-node k3s cluster**
> (`deploy/k3s/`)?

Record the answer. The **podman substrate** below is the default path; the
**k3s substrate** (bottom of this file) has its own driver,
`./deploy/k3s/setup.sh`, under the same output protocol and exit taxonomy.

## Rules for both substrates

- **The exception-handler contract (2026-08-27,
  `docs/superpowers/specs/2026-08-27-setup-update-contract.md`): the script
  is the spine, you are the exception handler.** On ANY non-zero exit from
  the driver — 1 (failure), 2 (warnings), or 3 (user action) — you STOP AND
  END YOUR TURN after surfacing exactly what happened: quote the
  FAIL/WARN/USERACTION lines, say what they mean, and (for a failure) what
  you propose. You may read first — the report file, `<state>/setup-log.txt`,
  the discovery evidence — but the decision about what happens next is the
  operator's, made in conversation, before anything is re-run or changed.
  Autonomous continuation is for exit 0 only.
- **Exit 3 is a gate, not an error.** The last USERACTION line names the
  operator's move. Relay the script's own instructions — for the LiteLLM
  catalog that includes the UI URL, where the credential lives (by NAME:
  `CC_LLM_PROXY_ADMIN_KEY` in the repo-root `.env`, or `UI_USERNAME` /
  `UI_PASSWORD` if the operator set them), and the expected alias list. When
  the operator says they have acted, run the driver again; it re-verifies.
- **Never print secret values.** `.env` contents stay out of your output; refer
  to keys by name. The report file is redacted as it is collected and records
  key NAMES only — do not undo that by quoting values from anywhere else.
- **Discovery evidence, if it exists, is part of your briefing.** Look for
  `<state>/discovery/discovery-report.md` — /discover's converged environment
  map, in the STATE DIRECTORY and never in the checkout (`./setup.sh report`
  prints the state dir; the map names internal hosts, so its contents stay in
  this conversation, never in tracked files). Read it, and
  `discovery/discovery.env` (the machine-readable classifications), before
  explaining a network-shaped finding: a discovery-verified mirror is a value
  for the operator to confirm instead of recall, and `DISCO_TLS_INTERCEPT=1`
  means the CA story must be settled before the images are fetched. It is
  EVIDENCE informing what you propose for `.env` — never source it or copy it
  wholesale, and a `CC_TLS_INSECURE=1` diagnostic never becomes disabled
  verification: the fix it indicates is trusting the corporate root CA. No
  discovery and a failure that smells like a restricted network: the proposal
  to surface is "run /discover".
- **Capability manifest and vlogs disclosure are mandatory.** The `verify`
  phase prints what this profile installed against the k3s deployment, naming
  vlogs (the log console) as the one deliberate omission — podman does not
  produce the CRI-format logs Fluent Bit tails. Read it out; do not paraphrase
  it away.
- **You do not interview anybody (2026-09-18).** The cockpit asks the
  operator's name on first run (a non-dismissible prompt bar, stored as the
  `operator_name` app setting); on a terminal the podman install asks it once
  (`CC_OPERATOR_NAME`, the env fallback). The work environment, the team and
  the working preferences are the EA-hosted team tour's step **1b. THEIR
  WORLD**, recorded as gated `graph.add_episode` proposals.

---

## Podman substrate

### The contract

The procedure is [`deploy/single/CHECKLIST.md`](../../../deploy/single/CHECKLIST.md),
generated from `deploy/single/steps.tsv` — read it before the first run. Your
part of it is three sentences (design record
`docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md`, D3):
run `./setup.sh` (from `deploy/single/`); read the ledger it prints; either
change a `.env` key the FAIL line names and run `./setup.sh` again, or report
the ledger row as a repository defect.

You may change `.env`. You may run `./setup.sh`, `./setup.sh status` and `./setup.sh report`. Everything else is a finding.

On a repository defect: run `./setup.sh report`, hand the operator the path it
prints, and end the turn.

"Everything else" is meant literally: a script, a Dockerfile, `images.txt`, a
lock file, a file inside the podman machine, a model row in LiteLLM, a
hand-run `podman`/`curl`/`git`/`npm` command, a phase run by name. The driver
resumes from the ledger, so the next move is never a phase you pick — the
run decides what runs, and a step is never run by hand. On a deployment tree
(its state dir holds `ledger.tsv`) the repository's
`.claude/hooks/guard-install-tree.sh` holds you to this: it denies an edit to
any tracked file and a shell command that writes into the tree, and its
denial names `./setup.sh report`. When it fires, that IS the answer — report,
hand over the path, end the turn. Never look for a way around it. (The hook
runs once the operator has accepted Claude Code's workspace trust dialog for
the folder, which is a step of the checklist's **Before you start**.)

### What the operator does, not you

- **`./setup.sh configure`, in the operator's own terminal.** It asks every
  question in `deploy/single/questions.tsv` that `.env` does not answer yet, is
  the ONE command that creates `.env` (from `.env.example`), writes only that
  file, and generates the credentials. It asks; you cannot answer its prompts,
  and with no TTY it asks nothing rather than guessing — so you do not run it.
  Tell the operator to, and what it will ask about (**Elicitation help**
  below). A run with no `.env` stops with `USERACTION answer-file` naming it.
  If the operator would rather answer in conversation once `.env` exists, write
  each answer into `.env` yourself as `KEY=value`, quoting any value that
  contains a space (the file is SOURCED: `CC_OPERATOR_NAME=Jane Doe` unquoted
  runs `Doe` as a command).
- **The one-time preparation** in the checklist's **Before you start**: the
  host prerequisites, `./update.sh init` on a zip download (`check/tree-pristine`
  stops the run with a USERACTION naming it), and the workspace trust dialog.
- **The bold rows of the checklist** — the LiteLLM catalog, the name, the demo
  approval, and any other the manifest marks `human` or `gate`. Each says
  where the operator acts.
- **Updates**, through `./update.sh` (below), and `./setup.sh stop`.

### Before the first run

- **Is this already a deployment?** Run `./setup.sh status`: it prints the
  ledger and changes nothing (its self-check spends two small model requests).
  A tree with an imported but unapplied update stops `./setup.sh` with
  `USERACTION existing-install` naming `./update.sh plan` — that is the
  operator's update, never a fresh install, and never a zip extracted over the
  tree.
- **Walk the operator through the checklist's Before you start** — the
  prerequisites `check` will verify, the one-time `init` and `configure`, and
  the trust dialog — and read the discovery evidence if there is any.

### Reading what a run prints

- **`PLAN <phase>: WILL RUN | WILL SKIP — <why>`**, one line per phase before
  anything executes: a prediction from the same rule the run skips by. It
  tells you, before you wait, which phases a changed `.env` key will re-run.
- **Protocol lines** on stdout, `PASS|WARN|FAIL|USERACTION <check-name>:
  <message>`; detail on stderr; everything appended to `<state>/setup-log.txt`.
  Read every line — a WARN is not nothing (`node-version` means the cockpit is
  not built, and the demo is approved in the cockpit).
- **The ledger table**, printed at the end and at every stop: one row per
  step, `done`, `failed` (with the reason), `pending`, `gate` (waiting on the
  operator) or `started` — a run began that phase and never finished (it was
  interrupted); `./setup.sh` runs it again. Anything but `done` blocks what
  requires it, and the run refuses to run ahead of it.
- **`FAIL run-lock:`** — another run holds `<state>/run.lock`; the line names
  its pid, command and start time. Wait for it; do not start a second.
  **`WARN run-lock:`** — the next run reclaimed a lock whose process was gone
  and named the dead run; nothing to do.
- **Exit codes:** **0** clean, **1** hard failure, **2** completed with
  warnings, **3** stopped for the operator (the last USERACTION line names the
  move); precedence FAIL > USERACTION > WARN.

### The loop

- **Exit 1.** The FAIL line names its seam. Match it to `deploy/AIRGAP.md`'s
  seam table and propose the `.env` key that governs it; if you cannot name
  the key, you do not yet understand the finding — read the section of the run
  it came from (and `./setup.sh report`'s file) before proposing anything.
  End the turn; once the operator agrees, change `.env` and run `./setup.sh`
  again — it re-runs exactly the steps whose inputs changed. If no `.env` key
  explains it — the line names a file of the release, it fails again with the
  key right, or the `test` suite is red (a real defect in the install or the
  repository, never something to wave through) — it is a repository defect:
  run `./setup.sh report`, hand over the path, end the turn.
- **Exit 2.** If the run stopped at the `check` gate, nothing was changed.
  Surface every WARN and let the operator accept them explicitly; only then
  run `./setup.sh --accept-warnings`. Never pass that flag to get past a WARN
  they have not read. An exit 2 at the end of a full run is a finished install
  with warnings — surface them the same way.
- **Exit 3.** Relay the USERACTION and end the turn. When the operator says
  they have acted, run `./setup.sh`.

### The expected stops

- **The LiteLLM catalog (`llm/catalog-filled`) — expected on every fresh
  install** unless `.env` declares the upstream. It is the pause where the
  operator enters the providers in the LiteLLM UI; it also fires when a
  PLACEHOLDER was left in, an invariant was broken (`graphiti-llm` must be a
  PLAIN `openai/<model>` — the old `chat_completions/` bridge prefix now 404s),
  or a probe failed after filling in. The run's stderr already printed the
  hand-off: the UI URL, the credential's location by name, the alias table
  with what each row needs, and the direct-vs-proxy discrimination (direct
  succeeds and proxy fails = the alias row is wrong; direct fails = wrong URL
  or key). Relay it, let the OPERATOR do it, then run `./setup.sh` — even
  where the line's own re-run hint names the phase, the resume command is
  `./setup.sh`. You never register, edit or delete a model.
- **The name (`boot/operator-name`)** — on a terminal the run asks once; a
  headless run leaves it to the cockpit and is not stopped by it.
- **The demo approval (`demo/demo-approve`).** The run feeds a fixture email,
  lets the dispatcher run a real triage, and stops (exit 3 when headless) for
  the operator to decide in the cockpit's Decisions Inbox. Then `./setup.sh`
  records the decision and verifies the execution and its provenance; a
  `work.failed` event fails the step honestly rather than reading as a
  rejection. If the demo wedges, the FAIL line names the recovery
  (`POST /api/work/<id>/requeue`, and never a re-POST of the same fixture);
  that is the operator's to run, and a second wedge is a finding to report.
- **The update adoption (`ledger-adopt`)** and **the restart after an update**
  — see **Updates**.

### Updates

An existing deployment updates through `./update.sh`, run by the OPERATOR in
their terminal — it asks for an explicit yes, and it is not one of your three
commands. The human path is `./update.sh <downloaded-zip>` from
`deploy/single/`: init on first use, import, the version gate and the plan, a
yes, then apply. The apply **acquires before it merges**: the new release's
fetch and a probe of the running catalog against its aliases run from a
staged copy, and a stop there (exit 1 or 3) leaves the tree, the branch, the
database and the containers untouched. It refuses a tree that differs from
its release, and refuses to run under a live API. After the fast-forward the
order is schema → `fetch` → `llm` → `stack` (which, with `CC_ENABLE_N8N=1`,
also applies the n8n façade workflows) → `app` → `verify`; a stop there is fixed in `.env` (or in the LiteLLM UI) and
continued with `./update.sh apply`. Two USERACTIONs are yours to answer:

- **`restart`** — a successful apply ends at exit 3: run `./setup.sh` (it
  resumes at `boot`), then `./setup.sh status`.
- **`ledger-adopt`** — a deployment installed before the ledger (v2.55.0):
  run `./setup.sh` once; every phase is idempotent, so it walks the running
  deployment and records it.

The checklist's **Updating** section is the operator's version of this.

### Elicitation help

Everything `configure` asks is a row in `deploy/single/questions.tsv`, with
its prompt text. What follows is the judgement around those questions — what
they mean, what is permanent — for when the operator asks you, or would rather
answer in conversation (then you write the answer into `.env`).

- **The LLM provider — entered in the LiteLLM UI by default; the `.env` keys are
  an OPTIONAL shortcut** (v2.44.0 design record D3, made optional in v2.45.1).
  **The catalog lives in LiteLLM's database, not in `.env`**, and the catalog
  pause is a DELIBERATE exception to "a full run does not stop" — the
  operator's decision, because LiteLLM expresses provider nuance (credentials,
  per-provider parameters, routing, fallbacks) a flat answer file cannot, and
  it is one method across both profiles. So do NOT press for these values: ask
  once whether the upstream is a single OpenAI-compatible endpoint the
  operator would rather declare, and take "I'll do it in the UI" as the normal
  answer. Declaring it buys ONE thing — the `check` phase can then prove the
  endpoint from the host BEFORE anything is deployed, which matters most on an
  air-gapped install: `CC_LLM_UPSTREAM_BASE_URL` (the `/v1` base THIS HOST
  reaches — a `127.0.0.1` value is rewritten to `host.containers.internal` for
  the container's row, with a WARN), `CC_LLM_UPSTREAM_API_KEY` (`none` if the
  server ignores it, but never empty), and one `CC_LLM_UPSTREAM_MODEL_<ALIAS>`
  per required alias — `_CC_DEFAULT`, `_GRAPHITI_LLM`, `_CC_EMBEDDING`,
  `_GPT_4_1_NANO`, plus `_CC_TTS` / `_CC_STT` when `CC_ENABLE_SPEECH=1`. The
  values are the UPSTREAM model ids (`deploy/single/discover-llm.sh models`
  lists what a server names them — the operator's command). Either way the
  operator owns the values; a row they fill in the UI always wins over `.env`.
- **The embedding-permanence warning** — say it in one sentence: the
  embedding choice is effectively permanent, because `CC_EMBED_DIM` gets
  written into the Neo4j vector index; changing the embedder later means
  dropping the index and re-embedding the whole graph. **Leave `CC_EMBED_DIM`
  blank** — the run measures it, and refuses to overwrite a different value
  that is already there.
- **Enable flags** — `CC_ENABLE_N8N` (default 0; only needed by an
  n8n-backed integration, today Gmail), `CC_ENABLE_CRAWLER` (default 1; the
  Chromium crawl service — rung 2 of docs ingestion, a large local image; rung
  1, a plain HTTP fetch, works without it), `CC_ENABLE_SANDBOX` (default 1;
  this profile's sandbox backend is rootless podman, not the k3s profile's
  gVisor — weaker isolation, say so plainly), `CC_AIRGAP` (default 0; 1 when
  no public package index is reachable — the install then uses
  `requirements.lock` instead of resolving).
- **Mirrors, CA and proxy on a restricted network.** Mirrors, CA bundles and
  internal URLs are facts only the operator knows. If discovery ran, its
  report already verified these answers — offer each as a pre-fill to
  confirm; if it did not and the network is restricted, propose /discover
  first rather than eliciting blind. The seams (`CC_REGISTRY_DOCKERIO` /
  `CC_REGISTRY_GHCR` / …, `CC_CA_BUNDLE`, `CC_TLS_INSECURE`, `CC_PROXY`) are
  `deploy/AIRGAP.md`'s table. Reachability itself is the `check` phase's job:
  its `indexes`, `images` and `llm` sections name the seam per source.
- **Integration choices** (the same `.env` as everything above):
  - **Network trust** — ask ONCE, before Jira/Confluence, if either is
    self-hosted: does reaching it need a client cert (mTLS) or a private CA
    bundle, or does plain HTTPS work? One shared seam
    (`integrations/http.py`). If yes, elicit `CC_CA_BUNDLE` and
    `CC_CLIENT_CERT`/`CC_CLIENT_KEY`.
  - **Jira and Confluence are ASKED BY `configure` since v2.53.0** —
    `CC_JIRA_BASE_URL` first, its credentials only when it is answered, the
    two `*_API_FLAVOR` rows under `--all` — and the `check` phase's
    `integrations` section and the self-check run
    `scripts/atlassian_probe.py` against them. Blank is a valid answer meaning
    no Jira / no Confluence, and `check` states the consequence (agents holding
    those grants fail at execution). The help for those questions:
  - **Jira** — Cloud or Server/Data Center. A real fork, and NOT just a
    login: DC serves REST v2 only, takes wiki markup where Cloud takes ADF,
    pages by offset, and has no filter-search/dashboard/gadget endpoints at
    all. `CC_JIRA_API_FLAVOR=server` for Data Center (`cloud` is the default).
    The auth mode FOLLOWS the flavor — bearer (a PAT in `CC_JIRA_API_TOKEN`,
    email ignored) under `server`, basic (`CC_JIRA_EMAIL`+`CC_JIRA_API_TOKEN`)
    under `cloud` — so leave `CC_JIRA_AUTH_MODE` unset unless they want Basic
    on DC. Say plainly that DC endpoint shapes are coded to Atlassian's
    published reference, not yet proven live, and that the probe is how they
    get proven. Under `server` the Cloud-only tools
    (`jira_list_filters`/`_dashboards`/`_gadgets`) and the
    `jira.create_dashboard` capability are withheld from agents by design.
  - **Confluence** — same Cloud/DC shape into `CC_CONFLUENCE_BASE_URL` /
    `CC_CONFLUENCE_EMAIL` / `CC_CONFLUENCE_API_TOKEN` /
    `CC_CONFLUENCE_API_FLAVOR` / `CC_CONFLUENCE_AUTH_MODE`. Ask which macro
    profile matches their instance
    (`skills/confluence/references/instance-profiles.md`: `cloud-free` or
    `work-server-9.2`) — if neither matches, record `CC_CONFLUENCE_PROFILE`
    empty rather than guessing a macro that might not exist on their
    instance.
  - **Email** — Gmail (needs `CC_ENABLE_N8N=1`, wired in the n8n UI on
    `http://127.0.0.1:5678` once the stack is up — a credential named exactly
    `Gmail account`; or, migrating from a prior instance, decrypt-under-old-key
    and re-import as the operator-assisted alternative to redoing OAuth),
    Exchange (no native adapter yet — say so before asking anything else;
    elicit specifics about their existing PowerShell tools per
    `docs/reference/work-environment-compatibility.md` §2.3/§7 as design
    input, don't invent config), or manual-feed/none (`POST /api/emails` with
    a raw message; `fixtures/emails/` has samples).
  - Further integrations ship as capability packs — list what
    `central_command/runtime/packs.py` actually defines rather than promising
    from memory.

### Uninstalling (podman)

There is no teardown command in the driver. The teardown is the operator's
one `podman compose … down`, in `deploy/single/README.md`'s **Teardown**
section (every profile named, `-v` only for a wipe). Remind them that their
`.env` backup is the only copy of the never-rotate keys
(`CC_LITELLM_SALT_KEY`, `N8N_ENCRYPTION_KEY`) — deleting the install tree
without it orphans any kept volumes.

---

## After the install — both substrates

### Hand-off and go-live

Onboarding is the product's job (2026-09-18); nothing here is an interview.

- **The name.** If no terminal asked it, the cockpit asks on first run, and the
  agents say "the operator" until it is answered. Say this out loud: it is the
  one thing the operator must do before anything reads their name. Be honest
  about what renders it: the founding agents seeded by `schema.sql` and the
  five template hires made at startup (EA, editor, steward, MCP manager, graph
  curator) bake the name in ONCE, so naming yourself after first boot leaves
  them saying "the operator" until coached; an agent hired from the menu, every
  `_v0` founder (rendered on READ) and the provenance actor on every decision
  (`human:<name-slug>`) use it.
- **Integration proof.** If Jira and/or Confluence are configured, the first
  real read is the FIRST proof either works — especially on Server/DC, whose
  endpoint shapes were coded to docs. On podman that is `./setup.sh status`
  (the self-check's integrations row); on k3s, `python
  scripts/atlassian_probe.py`. A failure is a real finding (wrong base URL,
  wrong auth mode, missing cert trust) — diagnose it against the error, and
  the fix is an `.env` key.
- **GO-LIVE is a PROMPTED CHECKLIST**, never a default and never skipped
  silently (the operator, 2026-08-27: off-by-default with no prompted turn-on is
  a cliff). The Executor is not on it (2026-09-18): a fresh install runs
  `live`. What remains off is the work INTAKE. Walk each item as its own
  question, one line on what it does:
  1. **Email feed** — `CC_FEED_ENABLED` plus the `mail-poll` schedule.
     New-mail-only is the default posture (`feed_query` is `newer_than`-scoped);
     populating the BACKLOG is a separate deliberate step needing
     `CC_BACKLOG_CUTOFF_DATE` — offer it, don't assume it.
  2. **Dispatch drain** — `CC_DISPATCH_ENABLED` plus the `drain-window`
     schedule (the approval-limit valve keeps it human-paced).
  3. **Recurring schedules** — the seeded ones (EA contacts, jira-hygiene,
     maintenance sweeps, litellm-discovery), each turned on by the operator in
     the cockpit's Crons tab.
  The two `.env` flags are read when the API starts: on podman you may set
  them in `.env`, and they take effect after the operator's `./setup.sh stop`
  and your next `./setup.sh`. Anything declined stays off — the Crons tab
  flips any of them later.
- **The team tour, last.** It needs nothing flipped (2026-09-18). It is the
  seeded `team-tour` schedule — disabled, dated in the past so it never fires
  on its own — and the operator starts it with **Run now** in the cockpit's
  Crons tab. Tell them their EA is waiting there to introduce the team, and
  stop: the tour is the EA's conversation, not yours.
- **The Confluence macro harvest** is a separate, optional session after the
  install, not part of it — `skills/confluence/references/macro-discovery.md`'s
  "sampler-page harvest" section is the procedure. Offer it; record the choice.

### Demo shapes

On podman the `demo` rows conduct the demo. On k3s you conduct it by hand:
feed `fixtures/emails/007-ownership-change.eml` via `POST /api/emails`
(knowledge-only, so the approved episode is a REAL graph write and needs no
Jira), watch the proposal land in the Decisions Inbox, have the operator
approve it, and confirm the execution and its provenance in the event log — a
`work.failed` event is a failure to diagnose, not a rejection. The shapes, so
you don't guess them:

- `POST /api/emails` takes exactly one field —
  `{"text": "<the raw RFC-822 message>"}` (`EmailIn` in
  `central_command/api/routes.py`).
- **There is no `GET /api/decisions`.** The Decisions Inbox list is the
  `decisions.list` RPC over the cockpit WebSocket (`nerve_gateway.py`); over
  HTTP you have `GET /api/proposals/{id}` and the
  `POST /api/proposals/{id}/{approve,reject,dismiss}` verbs.
- **If the fixture email doesn't process cleanly** (a stuck/failed row from a
  cancelled run, a slow model), do NOT re-`POST` the same fixture —
  `enroll_email` treats a repeat Message-ID as terminal ("duplicate": true)
  regardless of the row's actual state, so re-posting is a silent no-op.
  Recover with `POST /api/dispatch/step` (claims and runs the next eligible
  item directly) or, for a row that reached `FAILED`,
  `POST /api/work/{item_id}/requeue`.

---

## k3s substrate — same contract, driven by `deploy/k3s/setup.sh` (2026-08-27)

The k3s branch now has its own deterministic driver, the sibling of the
podman one: **`./deploy/k3s/setup.sh`** runs `validate → preflight → llm →
stack → app → verify` with the same output protocol, exit taxonomy
(0/1/2/3), `setup-log.txt`, and `diagnose` bundle. All the rules for both
substrates at the top of this file apply UNCHANGED: stop and end your turn on any
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
  elicitation to the podman substrate's (see **Elicitation help**); the values go into the
  root `.env` (the driver creates it at the shipped `CC_EXECUTOR_MODE=live`).

**Run the driver.**

```bash
./deploy/k3s/setup.sh                # all six phases, stops at first FAIL/gate
```

What its phases own (so you can interpret, not so you re-derive): `llm`
brings up ONLY the LiteLLM trio, creates every alias `model-preferences.yaml`
declares as a SKELETON (`register-models.py`, create-only) and **pauses
(exit 3) on a fresh catalog** for the operator to enter providers, model
ids and keys/credentials in the LiteLLM UI; the re-run checks the
invariants (`graphiti-llm` must be a PLAIN `openai/<model>`, same prefix as
`cc-default` — the old `chat_completions/` bridge prefix 404s; rerank's
`/v1/rerank` api_base), applies the routing policy (`policy.py --apply` →
restart → `--check`), mints the virtual keys, then probes `cc-default`,
`graphiti-llm` (a structured chat/completions round trip) and
`cc-embedding` through the proxy — a probe failure is the same
exit-3 gate.
`stack` builds the per-arch images only if missing and rolls out every
manifest. `app` installs the venv/cockpit and the systemd units and **enables
and starts `cc-uvicorn` with the rest of them** (2026-09-18) — there is no
first-boot gate any more, because there is no interview to land before the API
reads its env. First boot hires the roster; the cockpit's first-run prompt bar
asks the operator's name.

**After `app`:** write in the Phase-0 integration values if the root `.env`
does not carry them yet, then:

```bash
./deploy/k3s/setup.sh verify --clean-install   # zero failures is the gate
```

`--clean-install` asserts the running API is LIVE — a stale `dry_run` systemd
drop-in or `.env` would simulate every approval, and that drop-in is the trap
now that the unit pins nothing.

Then read `deploy/k3s/README.md` §8 with the operator and decide each
instance-data item deliberately — on a fully-clean deployment the answer is
"restore nothing" (§7). The demo (fixture email → approval → provenance,
Jira/Confluence first-read verification, macro harvest offer, team tour)
and the GO-LIVE checklist are the shared sections above — **After the
install** and **Demo shapes** (here the demo is hand-conducted, per its shapes). No executor flip on
either substrate any more — if the operator ever wants `dry_run` here, note
that a systemd `Environment=` in a drop-in BEATS pydantic's env_file, so
editing `.env` alone does nothing; verify via the `status` RPC's
`executorMode`, never the file.

**Uninstalling (k3s).** Per-manifest: `sudo k3s kubectl delete -f
deploy/k3s/<file>.yaml` for each of `20-postgres`, `40-graph`, `50-n8n`,
`60-sandbox`, `61-mcp`, `70-crawler`, `80-vlogs` (add `30-litellm` +
`31-litellm-rbac` only for a FULL wipe — deleting a manifest's PVCs deletes
the data), plus `sudo systemctl disable --now cc-uvicorn cc-nerve
cc-graph-bolt cc-sandbox-runner cc-backup.timer`. Same `.env`-backup
reminder, for `deploy/pi/.env`. For a FULLY-clean slate, also purge the
gitignored leftovers a manifest wipe misses — the minted
`~/.cc-*.kubeconfig` files (stale tokens outlive their namespaces) and the
podman profile's litter in a shared checkout — since v2.42.0 only what a
PRE-v2.42.0 install left behind (`deploy/single/.env` — back it up first —
plus its rendered yamls and `setup-diagnostics.txt`); the full
list is `deploy/k3s/README.md` §8's purge block.
