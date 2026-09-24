# deploy/single — the single-node, giftable profile

Central Command's core spine on **one machine**, via **Compose** (`podman
compose`; `docker compose` on a dev box). This is the profile you hand to
someone who has an LLM API key and nothing else.

It is **derived from `deploy/k3s/`, not a replacement for it.** the operator's two-node
k3s cluster stays what it is; everything homelab-specific — two-node
placement, Tailscale, the workstation llama fleet, the sandbox, the crawler,
the log console — is factored out here.

## What runs

One file: **`compose.yaml`**. It replaced `render.sh` + five envsubst
templates + `podman kube play` on 2026-09-03 (design record
`docs/superpowers/specs/2026-09-03-deploy-refactor-design.md`).

**Always up:** `postgres` (the spine, schema auto-loaded from the repo's
`schema.sql` on a FRESH database) · `litellm` + its own `litellm-db` and
`litellm-redis` · `neo4j` · `graphiti` (MCP).

**Profiles**, selected from the `CC_ENABLE_*` flags by `setup.sh`:

- `n8n` — n8n + its postgres. Only needed by an n8n-backed integration
  (today: Gmail). Off by default; skip it and the install is two containers
  lighter. With it on: create a Gmail OAuth2 credential named exactly
  `Gmail account` in the n8n UI, then `./deploy/n8n/apply-workflows.sh
  --podman` installs the shipped façade workflows (`deploy/n8n/README.md`);
  `update.sh apply` re-applies them on every release that changes them.
- `crawler` — cc-crawler, the browser-rendering crawl service (rung 2 of docs
  ingestion). On by default. Its image is built locally and is large
  (Chromium); rung 1, a plain HTTP fetch, keeps working without it.
- `speech` — cc-speech, the self-hosted speech engine (Speaches: faster-whisper
  STT + Kokoro TTS) behind the `cc-tts` / `cc-stt` aliases the cockpit's voice
  input and read-aloud use. On by default, and started in the **llm** phase
  rather than `stack`, so the catalog re-run can probe both aliases with a real
  synthesis-then-transcription round trip. Its models are HF-hub snapshots the
  container pulls on first boot (`CC_HF_ENDPOINT` for a mirror; no egress at
  all means pre-placing them — `deploy/AIRGAP.md`). `CC_ENABLE_SPEECH=0` means
  you point BOTH aliases at engines of your own (hosted Whisper-convention
  models for `cc-stt` is the expected case); the aliases are required either
  way.

The **sandbox** has no service: sandbox containers are created on demand by the
runner, and on this profile the runner's backend is rootless podman rather than
the k3s profile's gVisor.

**Order lives in the file, not in the driver.** `healthcheck` +
`depends_on: condition: service_healthy` express what used to be a sequence of
plays and polls: litellm waits for its database and redis, graphiti waits for
neo4j and litellm. `setup.sh` brings services up with `--wait`. Two host-side
polls survive on purpose — LiteLLM's first-boot Prisma migration (5 minutes),
and the speech engine, which carries **no** healthcheck because its first boot
spends up to half an hour downloading ~1GB of models.

Services find each other by **compose service name** (`neo4j`, `litellm`,
`speech`) on the project's own network — the old pod-name/`CC_POD_PREFIX` DNS
convention is gone. `CC_POD_PREFIX` still prefixes `container_name`, which is
how `verify.sh`, `./setup.sh diagnose` and `update.sh` address containers. Host
access is loopback-published ports at the same numbers the app's `.env` already
assumes (postgres 5442, litellm 4000, graphiti 8000, bolt 7687).

The proxy's model catalog is **DB-stored** (`store_model_in_db`, as on k3s) and
**yours to fill in**: the `llm` phase creates the required aliases
(`models.json`, via `deploy/pi/litellm/register-models.py` — create-only) as
skeletons and **pauses** for you to enter the provider, model ids and key in
the LiteLLM UI; re-running `./setup.sh llm` validates each alias with a real
request and continues. Nothing you enter or later change in the UI is ever
overwritten by setup.

`CC_EMBED_DIM` is measured, not declared, and the `stack` phase refuses to run
without it: it is written into the Neo4j vector index and is effectively
permanent.

## Prerequisites

- **Claude Code CLI** — a hard prerequisite: it conducts the install. There is
  no no-Claude-Code install path. Onboarding is not part of it (2026-09-18):
  the cockpit asks your name and the EA tour asks the rest, after the install
  ends.
- podman ≥ 4.9 (validated on 4.9.3) **with compose support** (`podman compose`
  must answer; `docker compose` is accepted as a dev-box fallback), plus
  `curl`, `openssl`, `git`, and `uv` (which supplies CPython 3.12). Node ≥ 22
  is optional — without it the cockpit is not built and the API still runs.
  `./setup.sh preflight` checks all of these by name.
- Windows: **podman CLI** (a podman machine on WSL2; Podman Desktop is
  optional — a GUI over the same machine) plus **Git Bash** for the `.sh`
  scripts (ships `openssl`, `curl`). Windows has no real
  `python3`; the scripts fall back to uv's (already required).
- An OpenAI-compatible endpoint: base URL, API key, and a model id per alias
  (chat, graph extraction, embedding, reranker, and the speech pair if you keep
  the bundled engine). `./setup.sh configure` asks for exactly these, and they
  are the ONLY answers it treats as required.
- ~3 GB RAM for the stack, plus image pulls.
- **A terminal, for `configure`.** It is the one interactive step; with no TTY it
  refuses to guess and tells you which keys to fill in instead (see the loop).

## Install

**Answers in ONE `.env`, then one command.** `setup.sh` is the whole install;
the agent's job is to elicit the answers, read the result, and diagnose a
failure — never to compose the commands (design record
`docs/superpowers/specs/2026-08-25-deterministic-setup.md`).

The answer file is the **repo-root `.env`** — the app's configuration and this
profile's, in one file, since v2.42.0 (`docs/superpowers/specs/2026-09-23-airgap-check-configure-setup-design.md`,
D1). There is no `deploy/single/.env` and no `deploy/single/env.example` any
more; an existing install's is merged into the root file and moved aside
automatically on the next `setup.sh` or `update.sh` run, which prints a
`PASS env-migrate` line naming what moved. Compose is therefore always invoked
with `--env-file <repo>/.env`: there is no `.env` beside `compose.yaml` for it
to find, so a hand-run `podman compose` needs that flag too.

```bash
cd deploy/single
./setup.sh configure   # asks what .env does not answer yet; creates it if absent
./setup.sh check       # everything dry, one table — the loop below
./setup.sh             # check -> machine -> fetch -> llm -> stack -> app -> verify -> test -> boot -> demo
```

You do not copy `.env.example` by hand any more: `configure` is the ONE command
that creates the answer file, and `chmod 600` is its job too.

### The loop: `configure` → `check` → triage (edit `.env`) → `check` → `./setup.sh`

**`./setup.sh configure`** (v2.45.0, design record D6) asks every question in
`deploy/single/questions.tsv` that `.env` does not answer yet — plain prompts,
no TUI, grouped as `identity`, `features`, `network`, `mirrors`, `llm`, `paths`
(`--all` adds the ports and re-asks everything with the current value as the
default). One schema, read twice: `configure` ASKS from it and `check`
VALIDATES from it, so a new seam is a new row rather than a new prompt in one
place and a new check in another.

It prints the diff before it writes (`set    KEY=<value>`, `keep   KEY`, and
secrets as `(secret, not printed)`), writes **only** `.env`, then runs
`make-secrets.sh` so the generated credentials exist before `check` runs, and
ends by pointing at `check`. Blank is a valid answer wherever a key is optional
— for a mirror it means "the public source".

**It fails closed.** With no terminal, or with `--non-interactive`, it prompts
for nothing: it lists every unanswered REQUIRED key as a `USERACTION` and exits
3. An installer that cannot ask does not guess (rustup's rule). That is also
what makes `.env` a preseed file — fill it in on a connected machine, carry it
across with the zip, and `configure` reports `keep` for every row and asks
nothing. Only the upstream LLM keys are required; `CC_OPERATOR_NAME` is not (the
cockpit asks on first run).

**`./setup.sh check`** (v2.44.0, design record D5) runs every check that can be made
**without changing anything** and prints one table. It is the gate: the full run
starts with it and refuses to go past a `FAIL` or a `USERACTION`; a WARN-only
check continues with `--accept-warnings` or an interactive `y` (no terminal and
no flag → it stops and names the flag, rather than deciding for you). So the
install is: `configure`, run `check`, triage what it names (with Claude, if you
like — it reads the same lines), run `check` again, and only then `./setup.sh`.
Nothing but `.env` changes in that loop, and `check` itself writes only two keys
there: `CC_STATE_DIR` and `CC_EMBED_DIM` (measured, see below). An
`answers-<KEY>` USERACTION means a key nobody has answered — that is
`configure`'s question, and the line names it.

Eight sections, in order — `./setup.sh check --list` prints this table:

| section | what it checks |
|---|---|
| `answers` | .env present and sourceable; every questions.tsv key required by these flags set, and every set key valid; ports valid, unique and free |
| `host` | podman, the compose provider, the host tools, RAM/disk, the Windows CA store |
| `machine` | the podman machine's CA, registries and proxy — current state and the diff the machine phase would apply |
| `images` | every images.txt row resolves against its registry, including the three build bases and operator pins |
| `indexes` | PyPI, npm, the Python resolution, the apt archive, the CPython download mirror |
| `llm` | the upstream endpoint FROM THIS HOST — the model list, one chat, one structured, one embedding — ONLY when .env declares it; with the catalog left to the LiteLLM UI this section says so and probes nothing |
| `compose` | compose.yaml renders with this .env, with no variable it requires left unset |
| `models` | the speech models' source (Hugging Face or a pre-placed volume) and the cockpit's whisper model |

It ends with `CHECK: <n> pass, <n> warn, <n> fail, <n> action`, the state-dir
path, and one honest ceiling: **check proves inputs, not builds.** A local image
build can still fail inside the build (the apt/pip/npm work happens there), and
the LiteLLM *alias* probes belong to the `llm` phase — what check proves is that
every input those steps need is real. `validate` and `preflight` remain
callable on their own; check composes them rather than copying their probes.

Run `configure` first and there is nothing to accept: it generates the
credentials itself. Skip it and two things are WARNs by design on a brand-new
`.env` — the credentials `make-secrets.sh` owns are still blank (check never
generates a secret; `configure` and the `llm` phase do), and compose therefore
reports them unset. `--accept-warnings` is then how you say "yes, generate
them".

Everything this install GENERATES — `setup-log.txt`, `setup-diagnostics.txt`,
`installed.manifest`, the API and cockpit logs and pid files, the updater's
working directory, discovery's report — lives in the **state directory**,
outside the checkout (`CC_STATE_DIR`; default
`${XDG_STATE_HOME:-~/.local/state}/central-command/<dir>-<hash>`, resolved and
written back into `.env` on the first run). `./setup.sh diagnose` prints the
path first and `./setup.sh status` shows it. `git status` is clean after every
command.

What you must supply for the deployment — all of it asked by `configure`, all
of it a row in `questions.tsv`: the `CC_ENABLE_*` / `CC_AIRGAP` flags, the
upstream LLM (below), and, behind a mirror or with no network, the seams under
"Where every dependency comes from" (see below). Everything else is generated or
measured.

**The LLM provider: declare it in `.env`, or fill it in the LiteLLM UI**
(v2.44.0, design record D3). Set `CC_LLM_UPSTREAM_BASE_URL`,
`CC_LLM_UPSTREAM_API_KEY` and one `CC_LLM_UPSTREAM_MODEL_<ALIAS>` per required
alias (the table is in `.env.example`; `cc_required_aliases` in
`deploy/env-lib.sh` is the code) and setup registers LiteLLM's rows itself —
and `check` probes that endpoint from the host, before any container exists,
which is what turns "the LLM is wrong" from a mid-install surprise into a line
in a table. Leave them blank and the old path is unchanged: the `llm` phase
stops (exit 3) once the proxy is up, with the aliases created as skeletons and
the URL/login printed;
fill in model ids, `api_base` (what the container dials —
`host.containers.internal`, never `127.0.0.1`, for a server on this machine)
and the key, then re-run `./setup.sh llm`. A `127.0.0.1` upstream in `.env` is
rewritten to `host.containers.internal` for the row, with a WARN saying so. To see what a server names its
models before you fill the rows in:

```bash
CC_LLM_BASE_URL=https://host/v1 CC_LLM_API_KEY=... ./discover-llm.sh models
```

**`CC_EMBED_DIM` you do not fill in** — the `llm` phase measures it through
the `cc-embedding` alias and writes it back, and refuses to overwrite a
different value that is already there. An embedder on a different server is
just a different `api_base` on the `cc-embedding` row.

### Mirrors and air gaps

`fetch` is the only phase that needs the network, and it runs BEFORE
anything is deployed: it RESOLVES every image against your registry (see
below), pulls the resolved refs, builds
the three local images (graphiti, sandbox, crawler) with your mirrors passed
in as build-args, resolves the Python dependencies, and runs the cockpit's
`npm ci`. Each artifact it cannot get is a `FAIL` naming the `.env` seam that
governs it (`CC_REGISTRY_*`, `CC_APT_MIRROR`, `CC_PYPI_INDEX_URL`,
`CC_NPM_REGISTRY`, …), and the phase ends with `USERACTION` / exit 3. Fix the
mirror and re-run `./setup.sh fetch` — acquired artifacts fast-forward.
Nothing falls back on its own; the choice lives in `.env` so an update makes
the same one.

**Image versions are a constraint, a lock, and a resolution** (2026-09-03),
because rigid `@sha256` pinning broke real installs when an enterprise mirror
lacked the exact artifact. `images.txt` carries, per image, the tag SERIES this
release supports (`16`, `5.26`, `7`) alongside the tested tag and digest; a
substitute must keep the locked tag's FLAVOUR (`7-alpine` admits `7.4-alpine`,
never `7.4` or `7.4-alpine3.22`; `5.26.2` never admits `5.26.4-enterprise`). `resolve-images.sh` asks the registry what it has:

- the locked tag → `PASS`, and its digest is verified against `images.txt`
  (a mismatch is a `FAIL` — that tag has drifted or been poisoned). A tag that
  is a series or a channel (`16`, `7-alpine`, `main-stable`) is declared
  ROLLING with a `-` in the digest column: it moves on every upstream rebuild,
  so the tag is the pin and the observed digest is recorded as provenance;
- otherwise the newest tag satisfying the constraint → `WARN` naming the
  substitution, no digest check (a substituted tag is trusted from the
  enterprise mirror deliberately: the digest pin defended against
  public-registry tag poisoning, a threat the mirrored air gap does not carry);
- nothing satisfying it → `FAIL` naming the constraint and what the mirror has.

The resolved refs are written to `.env` as `CC_IMG_*` (compose.yaml reads
them) and recorded in `$CC_STATE_DIR/installed.manifest`. **A `WARN`-level substitution plus
a green `./setup.sh verify` is a supported install** — capability is proven by
probes, not by version strings. Since v2.43.0 the three locally BUILT images resolve their base
through the same manifest: the resolver writes
`CC_IMG_ZEPAI_KNOWLEDGE_GRAPH_MCP`, `CC_IMG_PYTHON` and
`CC_IMG_PLAYWRIGHT_PYTHON`, and each `build-*-image.sh` passes its one as a
`--build-arg`, so a substituted or re-namespaced base reaches the build instead
of failing it. The Dockerfile `ARG` default is the locked ref, which is what a
bare `podman build` (and the k3s build scripts) use — a test fails the suite if
the default and `images.txt` ever drift.

**An operator pin wins.** Set a `CC_IMG_<NAME>` in `.env` yourself and the
resolver verifies that exact ref exists (a manifest HEAD by tag, or by digest
for a `…@sha256:…` ref, parsed from YOUR ref — so a mirror that re-namespaces
the PATH works), `WARN`s that it honoured a pin, records it as `pinned`, and
never rewrites it. A pin the registry does not have is a `FAIL` naming the key.
Unset it to resolve against `images.txt` again. It tells your pin from its own
last write by comparing against what `installed.manifest` records.

On a restricted network, run `deploy/discover.sh` first: it probes every
external source this profile touches, diagnoses each failure mode, and its
report names the mirror value to write into each `.env` seam. The full map
of sources and seams is `deploy/AIRGAP.md`.

### Reading the output

Every check is one line on stdout:
`PASS|WARN|FAIL|USERACTION <check-name>: <message>`. Everything else —
subprocess output, progress, detail — is stderr. Exit codes follow
cloud-init, plus a gate code: **0** clean, **1** hard failure, **2** finished
with warnings, **3** stopped for the operator's move (the last USERACTION
line names it). The full run stops at the first phase that hard-fails and
tells you which phase to re-run.

### When something fails

```bash
./setup.sh diagnose        # writes <state>/setup-diagnostics.txt (and prints the state dir first)
```

Paste that file to Claude. It carries pod/container states, the last 100 log
lines per container, `verify.sh`'s output, tool versions — and **key NAMES only,
never values**. Claude interprets it and tells you which phase to re-run; it
does not freehand replacement commands.

### Re-running one phase

Each phase is a subcommand of the same code path as the full run, and every
step inside it is idempotent, so **resume is just re-run**:

```bash
./setup.sh configure   # ASK what .env does not answer yet (the one command that creates it);
                       #   --all also asks the ports, --non-interactive never prompts
./setup.sh check       # ALL of the below that changes nothing, in one table (--list names the sections)
./setup.sh validate    # offline check of .env; no side effects
./setup.sh preflight   # podman/tooling/RAM/disk/linger checks; no side effects
./setup.sh machine     # write the podman MACHINE from .env: the CA into its trust
                       #   store, the registries mirror/insecure drop-in, the proxy
                       #   drop-in. A no-op on bare Linux; idempotent; prints the
                       #   diff before each write. `machine --dry-run` reports only
                       #   (which is what preflight calls). deploy/AIRGAP.md
./setup.sh fetch       # acquire every external artifact up front (the one network phase)
./setup.sh llm         # secrets + LiteLLM (+speech) up + probe its aliases + measure CC_EMBED_DIM
./setup.sh stack       # assert the local images, then `compose up -d --wait` (+crawler, +n8n)
./setup.sh app         # venv, editable install, the derived .env values, mint the spine's virtual key, cockpit
./setup.sh verify      # verify.sh, then live, then the capability manifest
./setup.sh test        # the pytest gate, via the venv (~10 min, sequential)
./setup.sh boot        # asks your name (once), starts the API detached, checks the roster
./setup.sh demo        # fixture email -> triage -> YOUR approval -> a real graph write, provenance stamped
./setup.sh status      # postconditions only, mutates nothing
./setup.sh stop        # stops the API that `boot` started
```

### Trust: two knobs (v2.43.0)

`CC_CA_BUNDLE` (a PEM the whole deployment trusts) and `CC_TLS_INSECURE=0|1`
(verification off). Two keys, not twelve — per-tool knobs are what drift. One
function fans the CA out to every host-side tool
(`deploy/env-lib.sh`'s `cc_export_tls_env`), the builds take it as a
`podman build --secret` (never a build-arg — those show in `podman history`),
`./setup.sh machine` installs it in the podman machine, and LiteLLM and the
speech engine get it mounted read-only at `/etc/cc/ca.pem`. Prefer the CA; the
insecure knob is supported for a site that relies on isolation instead, and
every command that sees it prints one `WARN tls-insecure:` line naming what it
covers — never a PASS. The full table, including the one consumer with no
insecure option (Hugging Face), is in `deploy/AIRGAP.md`.

`compose up -d` converges: a service whose definition is unchanged is left
alone, a changed one is recreated. Re-running a phase after a config change is
the supported way to apply it.

### First boot and the demo (the last three phases, 2026-08-28)

A bare `./setup.sh` runs all eleven phases — **zero to a working, human-approved
demo in one command.** The late phases skip by probing reality, never a state
file: a healthy API skips `test` and `boot`, a decided proposal in the event
log skips `demo`. Only two moments are yours, and on a terminal the script
waits in place for both:

1. **Your name** (`boot`) — becomes `CC_OPERATOR_NAME` and the provenance
   actor on your decisions. A headless run does not ask: the cockpit does, on
   first run.
2. **The demo approval** (`demo`) — the script feeds
   `fixtures/emails/007-ownership-change.eml`, steps the dispatcher (a real
   inference against your endpoint — commonly a few minutes), and then waits
   while you open the cockpit at http://127.0.0.1:3080, read the proposal in
   the **Decisions Inbox**, and decide. That gate is the product; the script
   never decides for you. It then verifies the decision and the execution
   landed on the event log — and fails honestly on a `work.failed` event
   rather than reporting a failed execution as a rejection. The fixture is
   knowledge-only on purpose: triage proposes a graph episode, which the
   Executor performs **for real** against your local graph, so the demo needs
   no Jira and proves the whole spine.

The API runs detached afterward (log: `$CC_STATE_DIR/uvicorn.log`), and so
does the **cockpit server** — `web/server-dist`, the same Node process the k3s
profile runs as `cc-nerve`, on `CC_COCKPIT_PORT` (3080). `web/.env` is retired
on this profile (v2.42.0): the boot phase EXPORTS `PORT`, `GATEWAY_URL` and
`CC_UPDATE_BACKEND=api` into the node process instead, which is exactly
equivalent — the server's `dotenv/config` never overrides a variable already in
its environment — and leaves the checkout clean. (The k3s profile still writes
`web/.env`; there the file is `cc-nerve`'s.) The SPA
uvicorn serves on 8080 is NOT the cockpit: every panel is a route or a
WebSocket proxy the Node server owns, so 8080 alone sits at CONNECTING with
404s (2026-09-17 Windows run). `./setup.sh stop` stops both and PROVES the
ports are free — under Git Bash `kill` reports success against a native
Windows process it never signalled. **Deliberately still OFF after the demo, each one an
explicit flip when you decide:** the mail feed and dispatch drain
(`CC_FEED_ENABLED` / `CC_DISPATCH_ENABLED` + their schedules in the cockpit's
Crons tab), every recurring schedule (seeded disabled), and the sandbox runner
(below). The executor is NOT one of them — it runs `live` from the first
approval (2026-09-18); the gate is the safety, and `dry_run` no-ops every
capability including the internal ones. Onboarding waits for you in the
cockpit: a first-run prompt bar asks your name, and the EA's team tour asks
the rest and records it in the knowledge graph through the normal gate. Two demo traps worth knowing: re-POSTing the same email is a silent
no-op (repeat Message-IDs are terminal by design — recover a stuck item with
`POST /api/work/<id>/requeue`), and the model pickers need the API restarted
at least once after an update that adds routes.

### Starting the sandbox runner

The sandbox runner is a **host process**, not a pod — it creates sandbox
containers on demand. Start it from the repo venv alongside the API:

```bash
CC_SANDBOX_BACKEND=podman uvicorn central_command.sandbox.runner:app \
  --host 127.0.0.1 --port 8090
```

**Containment trade, stated plainly: on this profile the sandbox is rootless
podman, not gVisor — weaker isolation than the k3s deployment.** The
credential-free posture is unchanged (the runner holds no keys and the only
exit is a reviewed `mcp.sync_source`), but the kernel boundary is the host's.

### Verifying Atlassian connectivity (Jira / Confluence)

Jira and Confluence are configured in the ROOT `.env` (`CC_JIRA_*` /
`CC_CONFLUENCE_*`, documented in `.env.example`) — which since v2.42.0 is the
same file as everything else here. Both products ship in two flavors and the flavor is a
real fork, not a login difference: **Cloud** serves Jira REST v3 and
Confluence v2, **Server/Data Center** serves Jira REST v2 and Confluence v1
only — different rich-text, search and listing shapes, and on Jira DC no
filter-search, dashboard or gadget endpoints at all. Set
`CC_JIRA_API_FLAVOR=server` / `CC_CONFLUENCE_API_FLAVOR=server` for Data
Center; the auth mode follows the flavor unless you name one.

Prove it against your instance before trusting it:

```bash
python scripts/atlassian_probe.py
```

Read-only. It walks every endpoint each configured flavor uses and prints one
`PASS|FAIL|SKIP <product> <METHOD> <path> — <status> <body>` line per check,
plus what the instance reports for `serverInfo.deploymentType`, the real shape
of an issue's `description`, and whether epics surface as `parent` or as an
"Epic Link" custom field. It never prints a token or an email, and it exits
non-zero if any non-SKIP check failed. If the flavor is wrong, the client's
own auth check says so by name (`CC_JIRA_API_FLAVOR`) rather than reporting a
bare 404.

### What this profile does NOT install

`./setup.sh verify` ends by printing the capability manifest; the one
deliberate omission is the **vlogs log console**. Fluent Bit's container input
tails CRI-format `/var/log/containers/*.log`, which podman does not produce, so
the collector would need a redesign rather than a port. Use `podman compose -f
deploy/single/compose.yaml logs <service>` and `./setup.sh diagnose` instead.

**When a `--proxy` probe fails, re-run it direct** (`./discover-llm.sh chat
<upstream-model-id>`): a bad upstream URL/key and a broken
deployment/registration are different failures with different fixes, and the
direct probe is what tells them apart. Direct is the troubleshooting rung, not
the validation path — what production uses is the alias.

`verify.sh` proves the stack is **deployed and configured**. It does not spend
a token by default, because a bad upstream key and a broken deployment are
different failures with different fixes. `CC_VERIFY_LIVE=1 ./verify.sh` adds a
real completion and checks that the embedding endpoint actually returns
`CC_EMBED_DIM` values.

## Updating an existing deployment

`update.sh` is the update driver, built for the air-gapped case where the only
transport in is a **source zip downloaded from the public repo** (Code →
Download ZIP, or `…/archive/refs/heads/master.zip`). Same output protocol and
philosophy as `setup.sh`: every subcommand is idempotent, `PASS|WARN|FAIL`
lines on stdout, exit 0/1/2/3 (3 = paused for your explicit go-ahead), resume
is re-run.

It works by making the deployment tree a git repo with two branches:
`upstream` holds pristine imports (one commit per downloaded zip), `local` is
what actually runs — upstream plus your local modifications. A three-way merge
is what carries your changes across updates.

**The human path is one command** (2026-08-28 — terraform's own lesson:
separate plan/apply is for automation; the human command shows the plan and
asks): download the release zip, then

```bash
cd deploy/single
./update.sh ~/Downloads/central-command-1.0.2.zip
```

That inits the repo on first use, imports the zip, shows the version gate +
plan, pauses for your explicit yes, and applies — offering to stop a
`./setup.sh boot`-started API first.

**The cockpit path (2026-09-03)** drives the same machinery without a
terminal: Settings › Updates has **Update from file** — pick the downloaded
zip and the API stages it (`./update.sh stage` = init/import/plan; safe under
the live API), shows what it brings, and applies on your explicit click.
**Check for updates / Apply** works there too when the box can reach the
release source. The apply is performed by `update-run.sh`, a detached runner
the API spawns: it runs `./setup.sh stop`, `./update.sh apply` (all the same
gates: version, DB backup, merge), `./setup.sh boot`, health-checks, and
rolls back automatically on failure. Its log is
`$CC_STATE_DIR/update/apply.log`; the dialog polls
`$CC_STATE_DIR/update/status.json` straight through the restart (the API hands
the runner that path as `CC_UPDATE_DIR`, so both sides always agree). A pause
that needs you (a fetch seam, the LiteLLM catalog) restarts the cockpit and
tells you the exact command to finish with. The named subcommands remain for
granular or agent-conducted flows:

```bash
./update.sh init            # ONE-TIME: turn the unzipped tree into that repo
./update.sh import <zip>    # each update: commit the new zip
./update.sh plan            # dry-run: diff, flags, predicted conflicts — mutates nothing
./update.sh apply           # merge, then: schema -> ./setup.sh app -> ./setup.sh verify
./update.sh rollback        # reset to the pre-update tag and re-deploy that tree
```

What `apply` does, in load-bearing order: re-run `schema.sql` against the live
spine **before** the code goes live (the schema is additive-only and fully
idempotent, so old code tolerates new columns — a failed migration stops the
update with the old code still running), then `./setup.sh app` (deps from
`requirements.lock`, honoring `CC_AIRGAP`; cockpit rebuild), then
`./setup.sh verify`. It always ends with a `USERACTION restart` (exit 3): a
merged change is not live until you restart your uvicorn API (and the sandbox
runner, if you run one). The one exception is the cockpit's detached runner
(`CC_UPDATE_DRIVEN=1`), which owns the restart itself.

Rules that will save you:

- **Commit your local file tweaks to `local` as you make them.** An
  uncommitted edit is invisible to the merge; `plan` warns about it and
  `apply` refuses until it is committed. (`.env` is gitignored and `.venv`
  too; everything generated is outside the tree entirely — none of it is ever
  part of a merge.)
- **Conflicts are a stop, not a failure.** If your local change and the update
  touch the same lines, `apply` stops with git's normal conflict markers:
  resolve, `git add`, `git commit`, and **re-run `./update.sh apply`** — every
  later step is idempotent and picks up where it stopped. To back out instead:
  `git merge --abort`.
- **Rollback restores code, not the database.** Schema statements already
  applied stay applied; the additive-only discipline is what makes the
  restored code run fine against them. `rollback` refuses over uncommitted
  changes unless `CC_UPDATE_FORCE=1`.

## Teardown

One command, and the profiles must be named or their containers are left
behind:

```bash
podman compose -f compose.yaml --profile n8n --profile crawler --profile speech down
```

Add `-v` to remove the named volumes too (the databases, the graph, the speech
model cache). Without it the volumes survive, which is what you want for a
restart and not what you want for a wipe. Nothing else is left over: there are
no podman secrets and no hand-created network any more.

## Things that will bite you

**`CC_LITELLM_SALT_KEY` and `N8N_ENCRYPTION_KEY` can never be rotated.** The first
encrypts the stored LiteLLM virtual keys, the second decrypts the stored Gmail
OAuth credential. Starting the same database under a different value leaves
the rows present but undecryptable. `make-secrets.sh` generates them once and
never overwrites a value that is already set. **Back `.env` up somewhere
outside the install tree.**

**`CC_EMBED_DIM` is a discover-once decision, not a tunable.** It is written
into the graphiti config and thus into the Neo4j vector index. A mis-sized
vector does not error — it corrupts retrieval silently. Changing the embedder
later means dropping the index and re-embedding the whole graph.

**`graphiti-llm` must be a PLAIN `openai/<model>` — the old `chat_completions/`
bridge prefix now 404s.** (2026-09-21) Graphiti's MCP server uses upstream's
stock chat-completions client, which POSTs `/v1/chat/completions` with a
`response_format` json_schema; LiteLLM forwards that to the upstream server
unchanged, so a plain registration — exactly like `cc-default` — carries it
through correctly. The old `openai/chat_completions/` prefix forced LiteLLM's
Responses→chat bridge, which now sends `chat_completions/<model>` upstream and
the server 404s. It looks like a redundant duplicate of `cc-default`. It is
not — it carries its own `chat_template_kwargs` to keep thinking off — but the
model prefix itself should match `cc-default`'s pattern now.

**Rootless podman needs lingering.** Without `loginctl enable-linger $USER`,
the pause process dies when your last login session ends and takes every
container with it. Hit during validation over SSH.

**Secrets live in the repo-root `.env` and nowhere else.** Compose reads that
file natively (`--env-file`), so the old rendered `secrets.yaml` is gone — and
since v2.42.0 there is no second `.env` either. Where a credential is also the
app's, it has ONE key, the `CC_` one: `CC_LLM_PROXY_ADMIN_KEY` (the proxy's
`LITELLM_MASTER_KEY`), `CC_LITELLM_SALT_KEY`, `CC_NEO4J_PASSWORD`. Compose
hands each to its container under the name that container wants. It is chmod'd 0600 (honored on
Linux/macOS; on Windows/NTFS chmod is a silent no-op and the file relies on the
user account's ACLs instead — `make-secrets.sh` reports which case applied) and
gitignored. Never commit it, and never print its values.

## Reranking

`GRAPHITI_RERANK_MODEL` is deliberately unset, so Graphiti falls back to
upstream's logprob-classifier reranker, which addresses a model literally
named `gpt-4.1-nano` through LiteLLM. This profile registers that alias
mapped to the user's chat model (the live k3s deployment does the same), so
reranked searches work out of the box. Two caveats: an endpoint that does not
return logprobs degrades rerank *quality*, not availability; and if the
user's endpoint offers a real `/rerank`-capable cross-encoder, set
`GRAPHITI_RERANK_MODEL` to it instead — that is the better path when it
exists.

## Windows

Designed for a single node, no hostPath, no GPU, loopback ports only.
**Validated on Windows 11 + podman 5.8.6 (WSL2) + Git Bash, 2026-08-21,
end-to-end with live inference** — on the kube-play substrate; the Compose
refactor (2026-09-03) is pending its own Windows validation. The `.sh` scripts
need a bash (Git Bash or WSL); `compose.yaml` itself is shell-independent.

Two things measured on that run, both already reflected in the scripts/docs
above:

- **127.0.0.1, never localhost (W1).** Windows resolves `localhost` to `::1`
  first and the podman machine publishes IPv4-only — the difference between a
  9-minute and a 5-hour test suite. `.env.example` and this profile's configs
  use `127.0.0.1` throughout for exactly this reason.
- **Ignore `podman machine inspect`'s memory number.** It reports 2048MB and
  `podman machine set --memory` errors on WSL2 — WSL allocates dynamically,
  and 7.6Gi was actually available during validation. Don't chase the 2048
  figure; it does not reflect what the machine can use.
