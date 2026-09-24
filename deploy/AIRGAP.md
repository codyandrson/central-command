# Air-gapped and mirrored installs

The work-site environment has enterprise mirrors for container images, PyPI,
npm and Debian. This document is the map of every external source Central
Command touches and the seam that redirects each one. It is not a mirroring
project.

## The model (2026-08-31)

**Discovery first, then mirrors; nothing falls back on its own.**
`deploy/discover.sh` maps what the environment can actually reach; the
single-node driver's `fetch` phase (`deploy/single/setup.sh fetch`, after the
dry `check` and `machine`, before anything is deployed) acquires every dependency —
images by digest, the three locally-built images, the Python resolution, the
cockpit's npm tree — and STOPS (exit 3) on the first artifact it cannot get,
naming the `.env` seam that governs it. You fix the mirror seam and re-run
(acquired artifacts fast-forward). The decision is in `.env`, so a re-run and
an update make the same one.

Every mirror layer we sit on — podman's `registries.conf`, containerd's —
falls through to the public endpoint on a miss by DEFAULT; fail-loud is
opt-in (`pull-from-mirror`, k3s's `--disable-default-registry-endpoint`).
The `fetch` phase is where we make it loud.

## The loop (v2.44.0) — `check` → triage → `check` → `./setup.sh`

`./deploy/single/setup.sh check` runs every check that can be made **without
changing anything** — the answer file, this host, the podman machine's current
state, every image ref against its registry, the package indexes, the UPSTREAM
LLM probed from this host, the compose render, the speech models' source — and
prints one table, ending in
`CHECK: <n> pass, <n> warn, <n> fail, <n> action` (design record
`2026-09-23-airgap-check-configure-setup-design.md`, D5).
`./setup.sh check --list` names the eight sections.

```
edit .env  ->  ./setup.sh check  ->  triage what it names  ->  ./setup.sh check
                                                                   ...until green
                                              ->  ./setup.sh      (the install)
```

Two properties make the loop worth running:

* **it changes nothing but `.env`** — and inside `.env`, only `CC_STATE_DIR` and
  `CC_EMBED_DIM` (measured from the upstream embedder, written only when unset).
  Triage means editing `.env`, never editing a script, a Dockerfile or
  `images.txt`: every seam this document lists is a key in that file.
* **it is the GATE.** The full run starts with it and refuses to continue past a
  `FAIL` or a `USERACTION`. A WARN-only check continues with
  `--accept-warnings`, or with an interactive `y`; with no terminal and no flag
  it stops and names the flag rather than deciding for you. So a configuration
  problem is a line in a table before anything is pulled, built or deployed.

**Its ceiling is printed with the summary, and it is honest:** *check proves
inputs, not builds.* A local image build can still fail INSIDE the build (the
apt/pip/npm work happens there), and the LiteLLM *alias* probes belong to the
`llm` phase. What check proves is that every input those steps need is real —
"no CONFIGURATION failure at setup", not "no failure of any kind".

`deploy/discover.sh` still comes first on a network nobody has mapped: it
classifies what is reachable and by what failure mode, and its report names the
`.env` seams. `check` then proves THIS deployment's own inputs against the
answers discovery produced.

## The LLM: declared in `.env`, with the LiteLLM UI as the fallback

Until v2.44.0 the model catalog was outside `.env` entirely: setup created
`PLACEHOLDER` rows in LiteLLM's database and PAUSED (exit 3) while the operator
typed provider, model id and key into the proxy's web UI. On an air-gapped
install that was the worst possible place to discover that the endpoint, the key
or a model id was wrong — half-deployed, with the UI as the only instrument.

Declare it instead (design record D3), in the repo-root `.env`:

| key | meaning |
|---|---|
| `CC_LLM_UPSTREAM_BASE_URL` | the `/v1` base THIS HOST can reach. A `127.0.0.1` value is rewritten to `host.containers.internal` for the LiteLLM row — the row is dialled by a CONTAINER — with a WARN saying so |
| `CC_LLM_UPSTREAM_API_KEY` | its key. Never printed, never logged, never compared (LiteLLM masks it) |
| `CC_LLM_UPSTREAM_MODEL_<ALIAS>` | the UPSTREAM model id for each alias: `_CC_DEFAULT`, `_GRAPHITI_LLM`, `_CC_EMBEDDING`, `_GPT_4_1_NANO`, and with `CC_ENABLE_SPEECH=1` `_CC_TTS` and `_CC_STT` (the alias upper-cased, every non-alphanumeric `_`) |

Then `check`'s `llm` section probes that endpoint FROM THE HOST with curl before
any container exists — the model list (a 404 there is a WARN: some gateways do
not implement it, and then membership is simply unchecked), one chat completion
per distinct chat model id, one `json_schema` round trip for the `graphiti-llm`
model, and one embedding call whose length IS `CC_EMBED_DIM` — and the `llm`
phase registers real rows and skips its pause entirely.
`register-models.py` stays CREATE-ONLY: with the keys unset it creates today's
`PLACEHOLDER` skeletons (which is what the k3s profile relies on), a skeleton is
UPDATED once `.env` declares the upstream, and a row the operator filled in is
never written to — it WINS over `.env`, and the script says so.

Which aliases are REQUIRED follows the feature flags, and
`cc_required_aliases` in `deploy/env-lib.sh` is the one place that decides:
the four core aliases always, the speech pair only with `CC_ENABLE_SPEECH=1`
(with the engine off you point `cc-tts`/`cc-stt` at engines of your own in the
UI, which is not an upstream `.env` can name).

**Open item, unchanged:** the CA and the insecure knob reach the LiteLLM
container (`SSL_CERT_FILE` / `SSL_VERIFY`, via compose), but `CC_PROXY` does
NOT — `containers.conf`'s `[engine] env` covers pulls and builds, not
containers. An upstream reachable only through an egress proxy needs
`HTTP(S)_PROXY` added to the `litellm` service by hand; the `llm` phase's
failure text says so too.

## Step 0 — discover the environment

Before touching any seam, run `deploy/discover.sh`. It probes every commonly
needed external resource — package indexes, container registries, forges,
OS archives, AI hosts, CDNs — and classifies each one by failure MODE (DNS
vs refused vs timeout vs TLS interception vs auth), because the mode is the
diagnosis: a timeout is a default-deny firewall, a certificate failure that
clears with `-k` is a TLS-intercepting proxy, a 407 is proxy credentials.

It writes `$CC_STATE_DIR/discovery/discovery-report.md` — an exhaustive guide
to the environment for anyone (or anything) developing, deploying, or
operating in it: what is reachable, what each nuance is, and the concrete
config lines to consume each resource here — plus `discovery.env`,
machine-readable facts for tooling. Both live OUTSIDE the checkout (v2.42.0,
design record `2026-09-23-airgap-check-configure-setup-design.md` D7): they
name internal hosts, and nothing a command generates belongs in the tree.
`./deploy/single/setup.sh diagnose` prints the state dir's path.

The elicitation loop is answer-and-rerun, same shape as `setup.sh`, and it
writes **only the repo-root `.env`**: each finding's USERACTION names a key
there — `CC_CA_BUNDLE` for the corporate root CA, `CC_PROXY`, `CC_NETRC=1` for
credentials, and the mirror seams in the table below (plus
`CC_DISCO_MIRROR_<KEY>` for a resource with no seam of its own).
`deploy/discovery.conf` is retired: its keys were one-to-one with those seams,
and an existing one is merged into `.env` and moved aside on the next run.
Fill in what your environment has and re-run; configured mirrors are probed
too, and the report's "how to consume" section then points at them. TLS
interception has two supported answers and the report names both: trust the
corporate CA through `CC_CA_BUNDLE`, or turn verification off with
`CC_TLS_INSECURE=1`. See "Trust" below.

The report's findings map directly onto the seam table below: a resource
the report marks mirror-only is the value you put in that seam's `.env`
variable.

## Every external source, and its seam

All seams live in the **repo-root `.env`** — one answer file for the app and
the deployment since v2.42.0 (see `.env.example`'s "Deployment — single-node
profile" section, "Where every dependency comes from"); blank means the public
source. `deploy/single/env.example` and `deploy/single/.env` are gone.

| Source | Used by | Seam | Notes |
|---|---|---|---|
| docker.io | postgres, neo4j, redis, n8n, the graphiti and sandbox base images | `CC_REGISTRY_DOCKERIO` | host prefix only; a mirror that re-namespaces PATHS is `CC_IMG_<NAME>` — an operator pin, verified and never rewritten (see "Pins") |
| ghcr.io | LiteLLM, the speech engine (`speaches`) | `CC_REGISTRY_GHCR` | |
| mcr.microsoft.com | the crawler base (Microsoft's Playwright image: browsers + OS libs baked in) | `CC_REGISTRY_MCR` | replaces Debian + Microsoft's browser CDN for that build |
| Debian archive | `apt-get` inside the graphiti and sandbox builds | `CC_APT_MIRROR`, `CC_APT_SECURITY_MIRROR` | build-args; the deb822 sources file is REWRITTEN from `/etc/os-release` (slim images ship no `sources.list`; security is a separate path on every mirror) |
| PyPI | the app's venv (uv), graphiti's `uv pip`, the crawler's `pip` | `CC_PYPI_INDEX_URL` | fanned out to `PIP_INDEX_URL` AND `UV_DEFAULT_INDEX` — uv reads no `PIP_*` and `UV_INDEX_URL` is deprecated |
| python-build-standalone | uv, only when the host has no CPython 3.12 | `CC_PYTHON_MIRROR` | `UV_PYTHON_INSTALL_MIRROR`; a `file://` directory works |
| registry.npmjs.org | the cockpit build (`npm ci`), `sandbox-runtime` inside the sandbox build | `CC_NPM_REGISTRY` | `NPM_CONFIG_REGISTRY`; the lockfile's `resolved` URLs point at npmjs and npm rewrites those to the configured registry (its `replace-registry-host` default) — a lock regenerated AGAINST a mirror would not be rewritten back |
| huggingface.co | the speech engine's models (Kokoro TTS + faster-whisper STT), fetched by `cc-speech` when setup's llm phase installs them (`POST /v1/models/<id>`) | `CC_HF_ENDPOINT` (single) / hand-edit `HF_ENDPOINT` in `deploy/k3s/90-speech.yaml`; or pre-place the hub snapshots in the `speech-models` volume | `HF_HUB_CACHE` is the volume; a present snapshot is not re-fetched. `CC_ENABLE_SPEECH=0` removes the source entirely (point `cc-tts`/`cc-stt` at your own engines) |
| huggingface.co | Whisper STT model for the cockpit's *local* engine (k3s Node server only) | `WHISPER_MODELS_BASE_URL` (web server env) or pre-place `ggml-*.bin` in `config.whisperModelDir` | `whisper-local.ts` checks the local file before downloading; unused once `cc-stt` is registered |
| a private/corporate CA (TLS interception, self-signed mirror) | everything: host-side acquisition, the three image builds, podman pulls, LiteLLM, the speech engine | `CC_CA_BUNDLE` | one key, fanned out everywhere — see "Trust" below for the table |
| a mandatory egress proxy | every host-side acquisition, and (inside a podman machine) pulls and builds | `CC_PROXY` | fanned out to `http(s)_proxy` both cases, `no_proxy` pinned to loopback; `./setup.sh machine` writes the machine's `containers.conf` `[engine] env` drop-in. The machine's OWN environment still comes from `podman machine start` |
| — (verification off) | everything the CA row covers, except the speech engine | `CC_TLS_INSECURE=1` | the other supported answer to interception. One switch, not six; every run that sees it prints one WARN naming what it covers, and it is never a PASS. See "Trust" |
| — (credentials) | `deploy/discover.sh`'s probes | `CC_NETRC=1` | `--netrc`, so credentials stay in `~/.netrc` and never in `.env` |
| the upstream LLM endpoint | LiteLLM (and `check`, from the host) | `CC_LLM_UPSTREAM_BASE_URL`, `CC_LLM_UPSTREAM_API_KEY`, `CC_LLM_UPSTREAM_MODEL_<ALIAS>` | v2.44.0; blank means the LiteLLM-UI pause instead. See "The LLM" above |

**Windows (Git Bash + a podman machine) — measured 2026-09-18.** Git for
Windows' curl is schannel-only: it IGNORES `CURL_CA_BUNDLE`, and it checks
revocation, which an intercepting proxy cannot answer
(`CRYPT_E_REVOCATION_OFFLINE`). So on Windows the CA must ALSO be in the
Windows Root store (usually there by policy; else, as Administrator,
`certutil -addstore Root <ca.cer>` — the preflight check `ca-windows-store`
names it), and `setup.sh` writes a setup-owned `.curlrc` in the state
directory (`ssl-revoke-best-effort`, via `CURL_HOME`) for every curl it spawns. uv, npm
and node take the bundle from the variables as on Linux. The podman MACHINE
is a second host with its own egress — `./setup.sh machine` is what configures
it now (see below); what remains the operator's is the machine's own process
environment, which podman takes from the HOST when the machine STARTS. Without
that, a pull goes DIRECT (a dead host proxy still "passed" fetch) or fails
naming the registry, which is why `preflight`'s `machine-egress` check still
issues a USERACTION for it.

## Trust: two knobs, fanned out everywhere (v2.43.0)

There are exactly **two** trust keys, and both live in the repo-root `.env`:

* **`CC_CA_BUNDLE`** — a PEM the whole deployment should trust.
* **`CC_TLS_INSECURE=1`** — TLS verification off, everywhere below.

Two, not twelve, because per-tool knobs are what drift. Prefer the CA: it keeps
the supply chain verifiable. `CC_TLS_INSECURE=1` is a supported answer for a
site that relies on isolation instead — it trades a verifiable chain for a
working install, which is a decision the operator is entitled to make and this
document is not going to relitigate. It is never silent: every command that
sees it prints one `WARN tls-insecure:` line naming the consumers that command
drives, and it is never a PASS.

| Consumer | `CC_CA_BUNDLE` | `CC_TLS_INSECURE=1` |
|---|---|---|
| curl (host) | `CURL_CA_BUNDLE` | `insecure` in the generated `<state>/curl/.curlrc` (`CURL_HOME` points there) |
| uv | `SSL_CERT_FILE` (`UV_NATIVE_TLS` is deprecated → `UV_SYSTEM_CERTS`) | `UV_INSECURE_HOST=<index hosts>` |
| pip | `PIP_CERT` | `PIP_TRUSTED_HOST=<index hosts>` |
| npm | `NPM_CONFIG_CAFILE` | `NPM_CONFIG_STRICT_SSL=false` |
| node | `NODE_EXTRA_CA_CERTS` | `NODE_TLS_REJECT_UNAUTHORIZED=0` |
| git | `GIT_SSL_CAINFO` | `GIT_SSL_NO_VERIFY=1` |
| podman pulls | installed into the podman machine's trust store by `./setup.sh machine` (plus `podman machine set --import-native-ca` where podman ≥ 6.0 supports it) | `insecure = true` per `[[registry]]` in the machine's `registries.conf` drop-in, also written by `./setup.sh machine` |
| the three image builds | `podman build --secret id=cc_ca,src=$CC_CA_BUNDLE`; the Dockerfiles install it into the image trust store with `update-ca-certificates` | `--tls-verify=false` + `--build-arg CC_TLS_INSECURE=1` |
| apt inside a build | the same build secret | `Acquire::https::Verify-Peer "false"` in `/etc/apt/apt.conf.d/99cc-insecure` |
| pip / npm inside a build | `PIP_CERT` / `NPM_CONFIG_CAFILE`, set by the build's trust step | `PIP_TRUSTED_HOST` (derived from `PIP_INDEX_URL`'s host), `NPM_CONFIG_STRICT_SSL=false` |
| LiteLLM (the LLM endpoint) | `SSL_CERT_FILE`, from the CA mounted read-only at `/etc/cc/ca.pem` | `SSL_VERIFY=False` |
| the speech engine (Hugging Face) | `REQUESTS_CA_BUNDLE`, same mount | **none exists** — `huggingface_hub` has no insecure switch. Use the CA, an HF mirror (`CC_HF_ENDPOINT`), pre-placed snapshots, or `CC_ENABLE_SPEECH=0` |

One fan-out function does the host side —
`deploy/env-lib.sh`'s `cc_export_tls_env`, called by every command in the
profile — so a consumer cannot lose its variable in one script and keep it in
another (`tests/test_single_airgap_seams.py` walks the table). The CA reaches a
BUILD as a `--secret`, never a build-arg (build-args are visible in
`podman history`) and never in the build context; the mount is
`required=false`, so the k3s build scripts, which pass no secret, still build.
The two derived values compose reads — `CC_LITELLM_SSL_VERIFY`,
`CC_CA_BUNDLE_IN_CONTAINER` — are EXPORTED by `setup.sh`, never written into
`.env`: they are composed from the two keys above, and one fact has one key.

Registries can alternatively be mirrored in podman's own `registries.conf`
(`[[registry.mirror]]`, tried before the primary, with `pull-from-mirror =
"digest-only"` to keep tag pulls off it) — on macOS/Windows that file is the
podman MACHINE's, not the host's, and `./setup.sh machine` is what writes it.

## `setup.sh machine` — the phase that writes the podman machine

On Windows and macOS podman runs inside a VM, and **that VM is a second host**:
your `CC_CA_BUNDLE`, `CC_REGISTRY_*` and `CC_PROXY` do not reach a pull or a
build unless the machine itself carries them, and the Windows-side
`registries.conf` is parsed but **not honoured** for a machine-backed
connection (podman#16532). The `machine` phase — between `preflight` and
`fetch` — applies, over `podman machine ssh`, idempotently, printing the diff
before each write:

* the CA into the machine's trust store
  (`/etc/pki/ca-trust/source/anchors/cc-ca.pem` + `update-ca-trust`; the
  machine image is Fedora CoreOS. A Debian/Ubuntu machine image falls back to
  `/usr/local/share/ca-certificates/` + `update-ca-certificates`), plus
  `podman machine set --import-native-ca=true` where this podman has that flag
  (podman ≥ 6.0) — it imports the **Windows** trust store, which is usually
  where an enterprise CA already is;
* `/etc/containers/registries.conf.d/cc-central-command.conf` — a **drop-in**;
  the main file is never touched — with a `[[registry]]`/`[[registry.mirror]]`
  pair per configured `CC_REGISTRY_*`, and `insecure = true` (including for the
  host of every `CC_IMG_*` pin) when `CC_TLS_INSECURE=1`;
* `/etc/containers/containers.conf.d/cc-proxy.conf` — `[engine] env`, the
  engine's own environment, which is what pulls and builds travel through. The
  proxy VALUE is never printed; only the key name.

Then it VERIFIES the CA with a live probe from inside the machine (a `curl` at
the registry; curl exit 60 is a certificate failure, which is a FAIL naming the
code) rather than reading a settings file — Podman Desktop's CA propagation is a
known rough edge (podman-desktop#3821).

`./setup.sh machine --dry-run` reports current state and the diff and writes
nothing; that is what `preflight` calls, so a preflight run tells you what the
phase will do without doing it. On bare Linux the whole phase is a no-op.

**What it does NOT do:** the machine's own process environment for pulls is set
at `podman machine start` from the host environment, so a proxy there stays a
USERACTION (`podman machine stop && HTTPS_PROXY=… podman machine start`) —
writing systemd drop-ins inside the machine is not doc-verified and is an open
item in the design record.

## Partial availability — the minimal move per missing source

The sources are independent: fix ONLY the seam your environment cannot serve
and leave every other seam alone. `deploy/discover.sh`'s report says which
sources are reachable and which mirrors can stand in. For each source, the
smallest move when it alone is unavailable (single-node profile):

| Missing source | Minimal move |
|---|---|
| Debian apt | apt is consumed ONLY inside the three local image builds — point `CC_APT_MIRROR`/`CC_APT_SECURITY_MIRROR` at the mirror discovery found. Nothing else in the profile touches apt. |
| NodeSource (apt-based Node) | Node ≥ 22 is a HOST prerequisite (it *runs* the cockpit). Pre-stage the official self-contained tarball — `node-v22.x-linux-<arch>.tar.xz` from nodejs.org (or its mirror), untarred onto PATH — no apt involved. |
| registry.npmjs.org | `CC_NPM_REGISTRY` at the npm mirror (the lockfile's `resolved` URLs are rewritten automatically). |
| PyPI | `CC_PYPI_INDEX_URL` at the PyPI mirror; the pip installs inside the graphiti/crawler builds ride the same seam as build-args. |
| A container registry | `CC_REGISTRY_DOCKERIO`/`_GHCR`/`_MCR` at the registry mirror, or a `registries.conf` mirror podman sees. |
| python-build-standalone | Install CPython 3.12 on the host, or point `CC_PYTHON_MIRROR` at a `file://` directory holding the archive. |
| huggingface.co | Speech: `CC_HF_ENDPOINT` at an HF mirror, or pre-place the two hub snapshots in the `speech-models` volume, or `CC_ENABLE_SPEECH=0` and register `cc-tts`/`cc-stt` at engines you already have. Cockpit-local Whisper (k3s only): pre-place `ggml-*.bin` in `config.whisperModelDir`, or set `WHISPER_MODELS_BASE_URL`. |

## Pins

`deploy/single/images.txt` carries THREE tiers per image (2026-09-03):
a **constraint** (the supported tag series), the tested **locked tag** and its
**digest**. `fetch` runs `resolve-images.sh`, which asks the mirror's
`/v2/<path>/tags/list` what it actually has: the locked tag is used and its
digest verified (a mismatch FAILS — drifted or poisoned; a rolling series or
channel tag carries `-` instead of a digest and is pinned by the tag alone);
otherwise the newest
tag satisfying the constraint is substituted with a WARN and no digest check
(deliberate — the digest pin defended against public-registry tag poisoning, a
threat a mirrored air gap does not carry); otherwise it FAILS naming the
constraint and what the mirror has. Resolved refs are written to `.env` as
`CC_IMG_*` (which `compose.yaml` reads) and recorded in
`$CC_STATE_DIR/installed.manifest`.
A mirror serving no tags-list API still works: resolution falls back to a
blind pull of the locked tag, with a WARN.

**The three locally built images resolve their BASE through the same manifest**
(v2.43.0). `images.txt`'s `graphiti-base` / `sandbox-base` / `crawler-base`
rows used to be read by nothing — each Dockerfile carried its own base tag —
so a mirror lacking a base tag meant editing a Dockerfile. Now the resolver
writes `CC_IMG_ZEPAI_KNOWLEDGE_GRAPH_MCP`, `CC_IMG_PYTHON` and
`CC_IMG_PLAYWRIGHT_PYTHON`, the build scripts pass each as a `--build-arg`, and
the Dockerfile's `ARG` default (which a bare `podman build` and the k3s build
scripts use) must equal the row — a test fails the suite if they drift. The
Dockerfiles still pin their packages (`playwright==` must equal the crawler
base's tag).

**An operator pin wins.** Set a `CC_IMG_<NAME>` in `.env` yourself and the
resolver treats it as authoritative: it verifies that exact ref exists (a
manifest HEAD by tag, or by digest for a `…@sha256:…` ref — parsed from YOUR
ref, so a mirror that re-namespaces the PATH works), prints
`WARN <name>: operator pin honoured`, records it as `pinned`, and never
rewrites it. A pin the registry does not have is a FAIL naming the key: a pin
is checked, not trusted. Unset the key to resolve against `images.txt` again.
This is the seam for a path-renaming mirror — the case no host variable can
express — and for "use this tag, I checked". How the resolver tells your pin
from its own last write: `installed.manifest` records what it wrote, and a
value that differs from that record is yours.
`requirements.lock` is the frozen Python resolution; `web/package-lock.json`
the cockpit's. A mirror that "gets updated regularly" changes nothing until
a release bumps a pin — that is the point.

## The k3s profile

The two-node deployment keeps its own seams: `deploy/k3s/registries.yaml.example`
(containerd mirrors; add `--disable-default-registry-endpoint` so a miss
fails loud), `deploy/airgap.env.example` (pip/uv), and `web/.npmrc.example`
(npm). It also still needs the k3s install script vendored and the
NodeSource apt repo mirrored. The Dockerfiles' build-args apply to its
builds too (pass them to `podman build` by hand; the k3s build scripts
pass nothing and get the public defaults).
