---
name: setup
description: Install Central Command from scratch on this machine — the guided, nothing-skipped setup for a user who has an LLM API key and nothing else. On the podman substrate, the agent's job is elicitation and diagnosis only: it writes answers into deploy/single/.env and runs the deterministic `./setup.sh` (validate/preflight/llm/stack/app/verify, PASS/WARN/FAIL output, exit 0/1/2), reading `./setup.sh diagnose`'s bundle on failure rather than freehanding fixes. The single-node profile now includes the sandbox (rootless-podman backend) and crawler alongside postgres/LiteLLM/Neo4j/Graphiti/optional n8n. On the multi-node k3s substrate it still conducts deploy/k3s/README.md's clean-install runbook. Either way it ends with the onboarding interview and a working dry-run demo. Use when the user says "/setup", "install Central Command", "set this up", or "get me up and running".
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
(Phase 5), (e) conduct the demo (Phase 6). Delegate read-only investigation
("where is X defined", multi-file config reads) to an Explore sub-agent;
never end a turn mid-phase with no text.

Rules that hold for the whole run:

- **Phased with gates — now enforced by `setup.sh` itself.** The full run
  stops at the first phase that hard-fails; don't skip ahead of it, and don't
  re-derive what a failed phase already told you to fix.
- **Resumable = re-run.** Every phase is idempotent (`./setup.sh <phase>`
  re-runs just that one); there is no state file and none is needed.
- **Never print secret values.** `.env` and `secrets.yaml` contents stay out
  of your output, and so does `setup-diagnostics.txt`'s content beyond what
  it already redacts (it records key NAMES only) — refer to keys by name.
- **pytest gates Phase 5, but `setup.sh` does not run it.** After the `app`
  phase reports clean, run `pytest -q` yourself (sequential — xdist's
  per-worker DBs are broken in practice; ~11 minutes). All green before the
  interview; a red suite here is a real defect, not something to wave through.
- **Do not start uvicorn before the interview.** First boot hires the roster;
  the interview's answers must land in `.env` first.
- **Capability manifest and vlogs disclosure are mandatory.** `./setup.sh
  verify` prints what this profile installed vs. the k3s deployment, naming
  vlogs (the log console) as the one deliberate omission — podman doesn't
  produce the CRI-format logs Fluent Bit tails. Don't paraphrase this away;
  read it out to the user.

## Substrate — ask this once, up front

> Will Central Command run on **this machine alone** (podman — the default, and
> the giftable profile), or across the operator's **multi-node k3s cluster**
> (`deploy/k3s/`)?

Record the answer. Everything below "Podman substrate" is that path. The
**k3s substrate is untouched by this redesign** — conduct
`deploy/k3s/README.md`'s clean-install runbook exactly as before (see the
bottom of this file for the reference, unchanged from the prior version of
this skill).

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

- **`CC_LLM_BASE_URL`, `CC_LLM_API_KEY`** — any OpenAI-compatible endpoint,
  URL including `/v1`.
- **Split embedding endpoint?** Ask separately where embeddings are served —
  self-hosted setups often run a dedicated embedder on another port. If so,
  set `CC_EMBED_BASE_URL` / `CC_EMBED_API_KEY` (a key the server ignores can
  be a placeholder like `none`, but must be non-empty — LiteLLM needs a value
  to send). Leaving both empty means "same server as chat," and only the
  `cc-embedding` alias reads them.
- **`CC_CHAT_MODEL`, `CC_EMBED_MODEL`** — model ids as the endpoint names
  them. List them with the one DIRECT read that's safe before the proxy
  exists: `./discover-llm.sh models` (this is the only command in the happy
  path that talks to the upstream directly rather than through LiteLLM).
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
  can't discover them. Record `CC_REGISTRY_DOCKERIO` / `CC_REGISTRY_GHCR` (the
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
    Phase 6's real read is the verification. No Jira: record that; jira-pack
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
    with a raw message; `fixtures/emails/` has samples for Phase 6).
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

This runs `validate → preflight → llm → stack → app → verify` in order,
stopping at the first hard failure. Each line on stdout is
`PASS|WARN|FAIL <check-name>: <message>`; subprocess detail goes to stderr.
Exit code **0** = clean, **1** = hard failure, **2** = completed with
warnings. Read every line — a WARN is not nothing (e.g. `node-version` WARN
means the cockpit won't be built).

If the user is running it themselves instead of you, have them paste the
output; interpret it the same way.

**On any FAIL:**

```bash
./setup.sh diagnose
```

This writes `deploy/single/setup-diagnostics.txt` — pod/container states,
last 100 log lines per pod, `verify.sh`'s output, tool versions, and `.env`
key NAMES only (never values). Read it, reason about the actual cause, and
say which phase to re-run and why. Do not compose a replacement command for
whatever `setup.sh` was doing — if the fix is a real command (e.g. `loginctl
enable-linger`), it's one `setup.sh preflight` already named in its WARN/FAIL
line; otherwise the fix is almost always "re-run `./setup.sh <phase>`" after
correcting `.env`.

One exception worth knowing about, not composing: if `probe-chat` or
`probe-embed` fails inside the `llm` phase, the note on stderr already tells
you to run `./discover-llm.sh chat <upstream-model-id>` or `./discover-llm.sh
embed <upstream-model-id>` DIRECT — that's the troubleshooting rung baked
into the script's own output, not a freehand invention. A direct success with
a proxied failure means the upstream and key are fine and the fault is
registration; a direct failure means the upstream URL or key is wrong.

Gate: `./setup.sh` exits 0 (or 2 with WARNs you've read and accepted).

### Phase 2 — pytest gate

```bash
pytest -q
```

Sequential, ~11 minutes. All green before Phase 3. A red suite here is a real
defect in the install or the repo — diagnose it, don't proceed past it.

### Phase 3 — CC_OPERATOR_NAME and the interview (unchanged)

`CC_OPERATOR_NAME` is the one root-`.env` value `setup.sh`'s `app` phase
deliberately leaves unset (everything else — the virtual key,
`CC_NEO4J_PASSWORD`, `CC_EMBED_DIM`/`CC_EMBED_ALIAS`, the LiteLLM db url and
salt, `CC_EXECUTOR_MODE=dry_run` — it already wrote). It's the interview's
first answer, below. Also write in the Jira/Confluence/network-trust values
you elicited in Phase 0 if `setup.sh`'s `app` phase didn't already carry them
forward — check the root `.env` before re-typing anything.

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

1. **Identity.** What should the agents call them? →
   `CC_OPERATOR_NAME=<name>` in the root `.env`. This also becomes the
   provenance actor on their decisions (`human:<name-slug>`).
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

### Phase 4 — first boot + the demo

**Do not start uvicorn before Phase 3 lands `CC_OPERATOR_NAME` and the
episodes** — first boot hires the roster from what's already in `.env`/graph.

```bash
uvicorn central_command.api.app:app --port 8080
```

First boot self-hires the roster. Be honest about what renders where: the
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
message: feed one fixture email from `fixtures/emails/` via
`POST /api/emails`, watch the proposal land in the Decisions Inbox, have the
user approve it, and confirm the dry-run execution + provenance in the event
log. Two shapes, so you don't guess them:

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
default and never skipped silently (the operator, 2026-08-27: "off until onboarding
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

## k3s substrate (unchanged — out of scope for the 2026-08-25 redesign)

The k3s branch does not restate commands: it **conducts
`deploy/k3s/README.md`**, which is the clean-install runbook, phase by
phase. The gate for the whole substrate is `./deploy/k3s/verify.sh
--clean-install` with **zero failures** (in `--clean-install` mode there is
no expected red).

> **Before you wipe or deploy anything:** if the operator's Claude Code CLI
> routes through this stack's LiteLLM `/anthropic` passthrough, re-point it
> to direct Anthropic FIRST — otherwise the installer loses its own model
> mid-run, at the exact moment the proxy goes down.

**Phase 0 deltas.** Confirm the runbook's own prerequisites
(`deploy/k3s/README.md` §1): k3s v1.34+ with both nodes `Ready`; Tailscale up
on both; passwordless `ssh` from the Pi to the chromebox (every build script
shells over it); Docker CE on the Pi (arm64 image builder) and podman on the
chromebox (amd64 one) — both, not either; `./deploy/k3s/install-gvisor.sh`
(idempotent) on the chromebox, without which sandbox Jobs never start and the
failure only shows at first use; uv-managed CPython 3.12 and NodeSource 22 on
the Pi.

**Phase 1 — LLM endpoint.** Same elicitation, different target: the "LLM
endpoint" is the operator's own upstream (local llama-swap / OpenAI-compatible
server). The tracked proxy config under `deploy/pi/litellm/` is repo config
and re-applies as-is. The direct listing still runs first, against the
environment rather than a `.env` file (the k3s substrate never reads
`deploy/single/.env`):

```bash
CC_LLM_BASE_URL=<upstream>/v1 CC_LLM_API_KEY=<key> \
  ./deploy/single/discover-llm.sh models
```

**Phase 2 — LiteLLM first, then the rest, through three tracked scripts —
run them in this order, don't hand-compose any of it:**

- **`./deploy/k3s/init-env.sh`** bootstraps `deploy/pi/.env`: copies the
  template, `chmod 600`, generates every GENERATED variable that's empty, and
  writes `PENDING` into the three Graphiti virtual-key slots. It prints the
  ELICITED variables you must supply.
- After that, the runbook's `./deploy/k3s/make-secrets.sh` (accepts
  `PENDING`, refuses only EMPTY), then apply the trio
  (`00-namespace`, `30-litellm`, `31-litellm-rbac`), roll it out, register
  models via `deploy/pi/litellm/register-models.py` (`--dry-run` first),
  `policy.py --apply`, restart `cc-litellm`, `policy.py --check`.
- **`./deploy/k3s/mint-keys.sh`** mints the four virtual keys against the
  now-running proxy, writes them back, re-runs `make-secrets.sh`, restarts
  `cc-graphiti`. It requires the repo-root `.env` to already exist — run `cp
  .env.example .env && chmod 600 .env` first if it doesn't.
- Then build local images (`build-graphiti-image.sh`,
  `build-sandbox-image.sh`, `build-crawler-image.sh`) and
  `sudo k3s kubectl apply -f deploy/k3s/` (re-applies the trio as a no-op).

Through-alias probes: `CC_LLM_BASE_URL=http://127.0.0.1:4000/v1
CC_LLM_API_KEY=<master> ./deploy/single/discover-llm.sh chat cc-default` and
`... embed qwen3-embedding-local` — the embed alias on THIS substrate is
`qwen3-embedding-local`, not `cc-embedding`; check `GET /v1/models` on the
live proxy rather than trusting either literal.

Gate: `./deploy/k3s/verify.sh --clean-install` — cannot reach zero failures
yet (the control-plane API and cockpit checks probe processes Phase 3/4
haven't started). Confirm every OTHER check passes.

**Phase 3 — integrations.** Identical to the podman substrate's Phase 0
integration elicitation above (Jira/Confluence/email/network-trust);
`CC_*` values land in the root `.env` in Phase 4 there, not written yet here.

**Phase 4 — install the app.**

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev,runtime]"
cp .env.example .env && chmod 600 .env
(cd web && npm install && npm run build)
```

Air-gapped: `uv pip install -r requirements.lock && uv pip install -e .
--no-deps` instead. Root `.env` needs the same full list as the podman
substrate's app phase writes automatically — here you write it by hand:
`CC_LLM_API_KEY` (mint via `POST http://127.0.0.1:4000/key/generate`,
`Authorization: Bearer <LITELLM_MASTER_KEY from deploy/pi/.env>`, body
`{"models": ["cc-default"], "metadata": {"cc": "spine"}}`, no `tags` field —
403s on non-Enterprise), `CC_LLM_PROXY_ADMIN_KEY` (the master key),
`CC_EXECUTOR_MODE=dry_run` (`.env.example` ships `live`), `CC_EMBED_ALIAS=
qwen3-embedding-local` (NOT `cc-embedding` — that's the podman profile's own
name), `CC_EMBED_DIM=<discovered>`, `CC_NEO4J_PASSWORD` (= `NEO4J_PASSWORD`
from `deploy/pi/.env`), `CC_LITELLM_DB_URL`/`CC_LITELLM_SALT_KEY`, the
Jira/Confluence/network-trust values from Phase 3. `CC_OPERATOR_NAME` is
Phase 5's.

The app runs as **systemd units**, not a foreground process: runbook §6
installs `cc-uvicorn`, `cc-nerve`, `cc-sandbox-runner`, `cc-graph-bolt`,
`cc-backup.timer`. Before starting `cc-nerve`, create `web/.env` with
`GATEWAY_URL=http://127.0.0.1:8080` (§6 has the exact lines) — its code
default points at the vendored gateway port, not Central Command's real API. Do
NOT start `cc-uvicorn` yet — first boot is Phase 6, after the interview.

Gate: `pytest -q` all green — same rule as the podman substrate.

**Phase 5 — the onboarding interview.** Identical to the podman substrate's
Phase 3 above.

**Phase 6 — first boot.** `sudo systemctl start cc-uvicorn` (`ExecStartPre`
waits on 127.0.0.1:5442). Gate on runbook §9's smoke checks (four `curl`
health probes, all pods `Running 1/1` on §4's placement,
`./deploy/k3s/verify.sh --clean-install` at zero failures) plus `GET
/api/agents`. The cockpit is `cc-nerve`'s tailnet URL, not `GET /` on :8080.
The demo (fixture email → approval → provenance, Jira/Confluence first-read
verification, macro harvest offer, team tour) is unchanged from the podman
substrate's Phase 4 above.

Then read `deploy/k3s/README.md` §8 with the operator and decide each
instance-data item deliberately — on a fully-clean deployment the answer is
"restore nothing" (§7).

Then the GO-LIVE checklist — identical to the podman substrate's, above:
executor live, feed, dispatch, schedules, each its own prompted question,
choices recorded as an operator episode. Do not end setup with the system
silently in dry_run.

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
