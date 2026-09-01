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

It writes `deploy/discovery.out/discovery-report.md` — an exhaustive guide
to the environment for anyone (or anything) developing, deploying, or
operating in it: what is reachable, what each nuance is, and the concrete
config lines to consume each resource here — plus `discovery.env`,
machine-readable facts for tooling. Both are gitignored: they name internal
hosts.

The elicitation loop is config-and-rerun, same shape as `setup.sh`: each
finding's USERACTION names a key in `deploy/discovery.conf` (also
gitignored) — `DISCO_CA_BUNDLE` for the corporate root CA, `DISCO_PROXY`,
`DISCO_NETRC=1` for credentials, `DISCO_MIRROR_<KEY>` per-resource mirrors.
Fill in what your environment has and re-run; configured mirrors are probed
too, and the report's "how to consume" section then points at them. Never
standardize on disabled TLS verification — `discover.sh` uses `-k` only as
a diagnostic to identify interception; the fix it prescribes is trusting
the corporate CA.

The report's findings map directly onto the seam table below: a resource
the report marks mirror-only is the value you put in that seam's `.env`
variable.

## Every external source, and its seam

All seams live in `deploy/single/.env` (see `env.example`, "Where every
dependency comes from"); blank means the public source.

| Source | Used by | Seam | Notes |
|---|---|---|---|
| docker.io | postgres, neo4j, redis, n8n, the graphiti and sandbox base images | `CC_REGISTRY_DOCKERIO` | host prefix only; a mirror that re-namespaces paths needs `images.txt` + templates edited |
| ghcr.io | LiteLLM | `CC_REGISTRY_GHCR` | |
| mcr.microsoft.com | the crawler base (Microsoft's Playwright image: browsers + OS libs baked in) | `CC_REGISTRY_MCR` | replaces Debian + Microsoft's browser CDN for that build |
| Debian archive | `apt-get` inside the graphiti and sandbox builds | `CC_APT_MIRROR`, `CC_APT_SECURITY_MIRROR` | build-args; the deb822 sources file is REWRITTEN from `/etc/os-release` (slim images ship no `sources.list`; security is a separate path on every mirror) |
| PyPI | the app's venv (uv), graphiti's `uv pip`, the crawler's `pip` | `CC_PYPI_INDEX_URL` | fanned out to `PIP_INDEX_URL` AND `UV_DEFAULT_INDEX` — uv reads no `PIP_*` and `UV_INDEX_URL` is deprecated |
| python-build-standalone | uv, only when the host has no CPython 3.12 | `CC_PYTHON_MIRROR` | `UV_PYTHON_INSTALL_MIRROR`; a `file://` directory works |
| registry.npmjs.org | the cockpit build (`npm ci`), `sandbox-runtime` inside the sandbox build | `CC_NPM_REGISTRY` | `NPM_CONFIG_REGISTRY`; the lockfile's `resolved` URLs point at npmjs and npm rewrites those to the configured registry (its `replace-registry-host` default) — a lock regenerated AGAINST a mirror would not be rewritten back |
| huggingface.co | Whisper STT model, first voice-input use | `WHISPER_MODELS_BASE_URL` (web server env) or pre-place `ggml-*.bin` in `config.whisperModelDir` | `whisper-local.ts` checks the local file before downloading |

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
| huggingface.co | Pre-place `ggml-*.bin` in `config.whisperModelDir` (checked before any download), or set `WHISPER_MODELS_BASE_URL`. |

## Pins

`deploy/single/images.txt` pins every pulled image by digest; `fetch` pulls
`ref@digest` and tags it `ref`, so the templates' `name:tag` never triggers
a pull and the mirror cannot serve a drifted or poisoned tag. The Dockerfiles
pin their packages (`playwright==` must equal the crawler base's tag).
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
