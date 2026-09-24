# Air-gapped and mirrored installs

The work-site environment has enterprise mirrors for container images, PyPI,
npm and Debian. This document is the map of every external source Central
Command touches and the seam that redirects each one. It is not a mirroring
project.

## The model (2026-08-31)

**Discovery first, then mirrors; nothing falls back on its own.**
`deploy/discover.sh` maps what the environment can actually reach; the
single-node driver's `fetch` phase (`deploy/single/setup.sh fetch`, right
after `preflight`, before anything is deployed) acquires every dependency —
images by digest, the three locally-built images, the Python resolution, the
cockpit's npm tree — and STOPS (exit 3) on the first artifact it cannot get,
naming the `.env` seam that governs it. You fix the mirror seam and re-run
(acquired artifacts fast-forward). The decision is in `.env`, so a re-run and
an update make the same one.

Every mirror layer we sit on — podman's `registries.conf`, containerd's —
falls through to the public endpoint on a miss by DEFAULT; fail-loud is
opt-in (`pull-from-mirror`, k3s's `--disable-default-registry-endpoint`).
The `fetch` phase is where we make it loud.

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
too, and the report's "how to consume" section then points at them. Never
standardize on disabled TLS verification — `discover.sh` uses `-k` only as
a diagnostic to identify interception, and only when `CC_TLS_INSECURE=1` says
so explicitly, printing a WARN on every run that does; the fix it prescribes is
trusting the corporate CA.

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
| docker.io | postgres, neo4j, redis, n8n, the graphiti and sandbox base images | `CC_REGISTRY_DOCKERIO` | host prefix only; a mirror that re-namespaces paths needs the path edited in `images.txt` |
| ghcr.io | LiteLLM, the speech engine (`speaches`) | `CC_REGISTRY_GHCR` | |
| mcr.microsoft.com | the crawler base (Microsoft's Playwright image: browsers + OS libs baked in) | `CC_REGISTRY_MCR` | replaces Debian + Microsoft's browser CDN for that build |
| Debian archive | `apt-get` inside the graphiti and sandbox builds | `CC_APT_MIRROR`, `CC_APT_SECURITY_MIRROR` | build-args; the deb822 sources file is REWRITTEN from `/etc/os-release` (slim images ship no `sources.list`; security is a separate path on every mirror) |
| PyPI | the app's venv (uv), graphiti's `uv pip`, the crawler's `pip` | `CC_PYPI_INDEX_URL` | fanned out to `PIP_INDEX_URL` AND `UV_DEFAULT_INDEX` — uv reads no `PIP_*` and `UV_INDEX_URL` is deprecated |
| python-build-standalone | uv, only when the host has no CPython 3.12 | `CC_PYTHON_MIRROR` | `UV_PYTHON_INSTALL_MIRROR`; a `file://` directory works |
| registry.npmjs.org | the cockpit build (`npm ci`), `sandbox-runtime` inside the sandbox build | `CC_NPM_REGISTRY` | `NPM_CONFIG_REGISTRY`; the lockfile's `resolved` URLs point at npmjs and npm rewrites those to the configured registry (its `replace-registry-host` default) — a lock regenerated AGAINST a mirror would not be rewritten back |
| huggingface.co | the speech engine's models (Kokoro TTS + faster-whisper STT), fetched by `cc-speech` when setup's llm phase installs them (`POST /v1/models/<id>`) | `CC_HF_ENDPOINT` (single) / hand-edit `HF_ENDPOINT` in `deploy/k3s/90-speech.yaml`; or pre-place the hub snapshots in the `speech-models` volume | `HF_HUB_CACHE` is the volume; a present snapshot is not re-fetched. `CC_ENABLE_SPEECH=0` removes the source entirely (point `cc-tts`/`cc-stt` at your own engines) |
| huggingface.co | Whisper STT model for the cockpit's *local* engine (k3s Node server only) | `WHISPER_MODELS_BASE_URL` (web server env) or pre-place `ggml-*.bin` in `config.whisperModelDir` | `whisper-local.ts` checks the local file before downloading; unused once `cc-stt` is registered |
| a private/corporate CA (TLS interception, self-signed mirror) | every host-side acquisition: curl, uv/pip, npm, node | `CC_CA_BUNDLE` | fanned out to `CURL_CA_BUNDLE`, `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS`, `NPM_CONFIG_CAFILE`; NOT podman pulls or the in-build package fetches — see below |
| a mandatory egress proxy | every host-side acquisition | `CC_PROXY` | fanned out to `http(s)_proxy` both cases, `no_proxy` pinned to loopback; podman forwards proxy vars into builds on its own |
| — (diagnosis only) | `deploy/discover.sh`'s probes, and nothing else today | `CC_TLS_INSECURE=1` | `-k` on every probe, to tell you whether TLS interception is the cause. Every run that sees it prints a WARN naming the fact; it is never a PASS and never the fix. Fanning it out to the rest of the toolchain is a later phase (D4 of the 2026-09-23 record). |
| — (credentials) | `deploy/discover.sh`'s probes | `CC_NETRC=1` | `--netrc`, so credentials stay in `~/.netrc` and never in `.env` |

**Windows (Git Bash + a podman machine) — measured 2026-09-18.** Git for
Windows' curl is schannel-only: it IGNORES `CURL_CA_BUNDLE`, and it checks
revocation, which an intercepting proxy cannot answer
(`CRYPT_E_REVOCATION_OFFLINE`). So on Windows the CA must ALSO be in the
Windows Root store (usually there by policy; else, as Administrator,
`certutil -addstore Root <ca.cer>` — the preflight check `ca-windows-store`
names it), and `setup.sh` writes a setup-owned `.curlrc` in the state
directory (`ssl-revoke-best-effort`, via `CURL_HOME`) for every curl it spawns. uv, npm
and node take the bundle from the variables as on Linux. The podman MACHINE
is a second host with its own egress: podman passes the host's `HTTP(S)_PROXY`
into the VM when it STARTS, and `podman machine set --import-native-ca`
imports the host's trusted CAs at every boot — the preflight checks
`machine-egress` / `machine-ca` name both commands. Without them a pull goes
DIRECT (a dead host proxy still "passed" fetch) or fails naming the registry.

**What `CC_CA_BUNDLE` does not cover.** podman PULLS verify against the host
trust store (`update-ca-certificates` / `/etc/containers/certs.d/<registry>/ca.crt`),
not the exported variables. And the apt/pip/npm fetches INSIDE the three
local image builds run in containers that trust only the base image's CA set
— on a TLS-intercepted network, point their seams (`CC_APT_MIRROR`,
`CC_PYPI_INDEX_URL`, `CC_NPM_REGISTRY`) at internal mirrors whose certificates
chain to a publicly-trusted (or base-image-trusted) root. Plumbing the
corporate CA into the builds themselves is a deliberate follow-up, not a
variable that exists today. Never disable verification anywhere in this
table — that converts one broken fetch into an unverifiable supply chain.
`CC_TLS_INSECURE=1` is not an exception to that: it is honoured by
`deploy/discover.sh`'s probes ONLY, as the diagnostic that identifies
interception, and every run that sees it prints a WARN.

Registries can alternatively be mirrored in podman's own `registries.conf`
(`[[registry.mirror]]`, tried before the primary, with `pull-from-mirror =
"digest-only"` to keep tag pulls off it) — on macOS/Windows that file is the
podman MACHINE's, not the host's; `preflight` prints what `podman info`
sees so you can tell. Credentials for a build-time mirror go through
`podman build --secret`, never a build-arg (build-args are visible in
`podman history`).

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
blind pull of the locked tag, with a WARN. The three locally built images pin
their base tag in the Dockerfile, so a SUBSTITUTED base tag is not what the
build uses — mirror the tested tag for those. The Dockerfiles pin their
packages (`playwright==` must equal the crawler base's tag).
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
