# deploy/single — the single-node, giftable profile

Central Command's core spine on **one machine**, via `podman kube play`. This is
the profile you hand to someone who has an LLM API key and nothing else.

It is **derived from `deploy/k3s/`, not a replacement for it.** the operator's two-node
k3s cluster stays what it is; everything homelab-specific — two-node
placement, Tailscale, the workstation llama fleet, the sandbox, the crawler,
the log console — is factored out here.

## What runs

`stack-llm.yaml` — litellm + its postgres + redis. Played **first**: setup is
LiteLLM-first (2026-08-25), so the proxy is up and its aliases registered
before anything is validated, and every probe then runs THROUGH those aliases.

`stack.yaml` — postgres (the spine, schema auto-loaded) · neo4j · graphiti
(MCP). Played **second**, and not before the embedding dimension has been
measured through `cc-embedding`: `CC_EMBED_DIM` is written into the Neo4j
vector index and is effectively permanent. It is the only variable of the two
files that the LLM half does not need, which is why `./render.sh llm` can run
before it exists.

`optional-n8n.yaml` — n8n + its postgres. Played **only** when an n8n-backed
integration (today: Gmail) is selected (`CC_ENABLE_N8N=1`). Skip it and the
install is two containers lighter.

`optional-crawler.yaml` — cc-crawler, the browser-rendering crawl service
(rung 2 of docs ingestion). Played when `CC_ENABLE_CRAWLER=1`, which is the
default. Its image is built locally and is large (Chromium); rung 1, a plain
HTTP fetch, keeps working without it.

The **sandbox** has no pod: sandbox containers are created on demand by the
runner, and on this profile the runner's backend is rootless podman rather
than the k3s profile's gVisor.

Everything is a plain `Pod`. `podman kube play` implements no Services, so the
**pod name is the DNS name** on the shared podman network — which is why
`CC_POD_PREFIX` travels into every cross-pod reference. Host access is
hostPort on `127.0.0.1`, at the same ports the app's `.env` already assumes
(postgres 5442, litellm 4000, graphiti 8000, bolt 7687).

## Prerequisites

- **Claude Code CLI** — a hard prerequisite: it conducts the install and the
  onboarding interview. There is no no-Claude-Code install path.
- podman ≥ 4.9 (validated on 4.9.3), with `envsubst` (gettext), `curl`,
  `openssl`, `git`, and `uv` (which supplies CPython 3.12). Node ≥ 22 is
  optional — without it the cockpit is not built and the API still runs.
  `./setup.sh preflight` checks all of these by name.
- Windows: **podman CLI** (a podman machine on WSL2; Podman Desktop is
  optional — a GUI over the same machine) plus **Git Bash** for the `.sh`
  scripts (ships `envsubst`, `openssl`, `curl`). Windows has no real
  `python3`; the scripts fall back to uv's (already required).
- An OpenAI-compatible endpoint: base URL, API key, a chat model id, an
  embedding model id, and the embedding model's **dimension**.
- ~3 GB RAM for the stack, plus image pulls.

## Install

**Answers in `.env`, then one command.** `setup.sh` is the whole install; the
agent's job is to elicit the answers, read the result, and diagnose a failure
— never to compose the commands (design record
`docs/superpowers/specs/2026-08-25-deterministic-setup.md`).

```bash
cd deploy/single
cp env.example .env    # then fill it in — see below
./setup.sh             # validate -> preflight -> llm -> stack -> app -> verify
```

What you must supply in `.env`: `CC_LLM_BASE_URL`, `CC_LLM_API_KEY`,
`CC_CHAT_MODEL`, `CC_EMBED_MODEL`, and the `CC_ENABLE_*` / `CC_AIRGAP` flags.
Everything else is generated or measured. To pick the two model ids, list what
your endpoint serves:

```bash
./discover-llm.sh models
```

That one read is DIRECT rather than through the proxy, because of a bootstrap
circularity: you cannot list an upstream's models through a proxy that has not
registered them yet. **`CC_EMBED_DIM` you do not fill in** — the `llm` phase
measures it through the `cc-embedding` alias and writes it back, and refuses to
overwrite a different value that is already there.

Embeddings served by a different server than chat? Also set
`CC_EMBED_BASE_URL` / `CC_EMBED_API_KEY`; unset means "same server" and only
the `cc-embedding` alias reads them.

### Reading the output

Every check is one line on stdout: `PASS|WARN|FAIL <check-name>: <message>`.
Everything else — subprocess output, progress, detail — is stderr. Exit codes
follow cloud-init: **0** clean, **1** hard failure, **2** finished with
warnings. The full run stops at the first phase that hard-fails and tells you
which phase to re-run.

### When something fails

```bash
./setup.sh diagnose        # writes setup-diagnostics.txt
```

Paste that file to Claude. It carries pod/container states, the last 100 log
lines per pod, `verify.sh`'s output, tool versions — and **key NAMES only,
never values**. Claude interprets it and tells you which phase to re-run; it
does not freehand replacement commands.

### Re-running one phase

Each phase is a subcommand of the same code path as the full run, and every
step inside it is idempotent, so **resume is just re-run**:

```bash
./setup.sh validate    # offline check of .env; no side effects
./setup.sh preflight   # podman/tooling/RAM/disk/linger checks; no side effects
./setup.sh llm         # secrets + LiteLLM up + probe its aliases + measure CC_EMBED_DIM
./setup.sh stack       # build local images, render, play postgres/neo4j/graphiti (+crawler, +n8n)
./setup.sh app         # venv, editable install, the app's .env, mint the spine's virtual key, cockpit
./setup.sh verify      # verify.sh, then live, then the capability manifest
./setup.sh status      # postconditions only, mutates nothing
```

The `kube play` calls use `--replace`, so re-applying after a config change
replaces the pods and updates the podman secrets in place.

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

### What this profile does NOT install

`./setup.sh verify` ends by printing the capability manifest; the one
deliberate omission is the **vlogs log console**. Fluent Bit's container input
tails CRI-format `/var/log/containers/*.log`, which podman does not produce, so
the collector would need a redesign rather than a port. Use `podman logs
<pod>-<container>` and `./setup.sh diagnose` instead.

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
lines on stdout, exit 0/1/2, resume is re-run.

It works by making the deployment tree a git repo with two branches:
`upstream` holds pristine imports (one commit per downloaded zip), `local` is
what actually runs — upstream plus your local modifications. A three-way merge
is what carries your changes across updates.

```bash
cd deploy/single
./update.sh init            # ONE-TIME: turn the unzipped tree into that repo
./update.sh import ~/central-command-master.zip   # each update: commit the new zip
./update.sh plan            # dry-run: diff, flags, predicted conflicts — mutates nothing
./update.sh apply           # merge, then: schema -> ./setup.sh app -> ./setup.sh verify
./update.sh rollback        # reset to the pre-update tag and re-deploy that tree
```

What `apply` does, in load-bearing order: re-run `schema.sql` against the live
spine **before** the code goes live (the schema is additive-only and fully
idempotent, so old code tolerates new columns — a failed migration stops the
update with the old code still running), then `./setup.sh app` (deps from
`requirements.lock`, honoring `CC_AIRGAP`; cockpit rebuild), then
`./setup.sh verify`. It always ends with `WARN restart-required` (exit 2): a
merged change is not live until you restart your uvicorn API (and the sandbox
runner, if you run one).

Rules that will save you:

- **Commit your local file tweaks to `local` as you make them.** An
  uncommitted edit is invisible to the merge; `plan` warns about it and
  `apply` refuses until it is committed. (`.env`, `secrets.yaml`, `.venv` are
  gitignored and never part of this — they survive untouched.)
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

Prefix-aware on purpose — with a changed `CC_POD_PREFIX`/`CC_NETWORK` the
hardcoded names would silently leave secrets and the network behind:

```bash
PFX="$(grep ^CC_POD_PREFIX= .env | cut -d= -f2)"
NET="$(grep ^CC_NETWORK= .env | cut -d= -f2)"
podman kube down --force optional-crawler.yaml
podman kube down --force optional-n8n.yaml   # --force also removes the volumes
podman kube down --force stack.yaml
podman kube down --force stack-llm.yaml      # the LLM half LAST — it is what
                                             # everything else talks to
podman secret rm "${PFX}litellm" "${PFX}neo4j" "${PFX}graphiti" "${PFX}n8n"
podman network rm "$NET"
```

Without `--force` the named volumes survive, which is what you want for a
restart and not what you want for a wipe.

## Things that will bite you

**`LITELLM_SALT_KEY` and `N8N_ENCRYPTION_KEY` can never be rotated.** The first
encrypts the stored LiteLLM virtual keys, the second decrypts the stored Gmail
OAuth credential. Starting the same database under a different value leaves
the rows present but undecryptable. `make-secrets.sh` generates them once and
never overwrites a value that is already set. **Back `.env` up somewhere
outside the install tree.**

**`CC_EMBED_DIM` is a discover-once decision, not a tunable.** It is written
into the graphiti config and thus into the Neo4j vector index. A mis-sized
vector does not error — it corrupts retrieval silently. Changing the embedder
later means dropping the index and re-embedding the whole graph.

**The `openai/chat_completions/` prefix on `graphiti-llm` is load-bearing.**
graphiti_core drives extraction through the Responses API; LiteLLM passes a
plain `openai/...` model straight through, and an endpoint that does not
implement `/v1/responses` silently drops the json_schema, so the model answers
prose and every extraction fails. The prefix forces LiteLLM's Responses→chat
bridge. It looks like a redundant duplicate of `cc-default`. It is not.

**Rootless podman needs lingering.** Without `loginctl enable-linger $USER`,
the pause process dies when your last login session ends and takes every
container with it. Hit during validation over SSH.

**Secrets live in `secrets.yaml` and nowhere else.** `podman kube play` takes
only YAML, so unlike the k3s profile (which pipes secrets in imperatively) a
file is unavoidable. It is chmod'd 0600 (honored on Linux/macOS; on
Windows/NTFS chmod is a silent no-op and the file relies on the user
account's ACLs instead — `make-secrets.sh` reports which case applied) and
gitignored, along with `.env` and all three rendered manifests. base64 is not
encryption — never commit it.

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
end-to-end with live inference.** The `.sh` scripts need a bash (Git Bash or
WSL); the manifests themselves are shell-independent.

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
