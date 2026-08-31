# Air-gapped and mirrored installs

The work-site environment has enterprise mirrors for container images, PyPI,
npm and Debian. This document is the map of every external source Central
Command touches, the seam that redirects each one, and the bundle that
replaces a source the mirror cannot serve. It is not a mirroring project.

## The model (2026-08-30)

**Mirrors are the primary path; the bundle is an explicit, per-artifact
fallback; nothing falls back on its own.** The single-node driver's `fetch`
phase (`deploy/single/setup.sh fetch`, right after `preflight`, before
anything is deployed) acquires every dependency — images by digest, the
three locally-built images, the Python resolution, the cockpit's npm tree —
and STOPS (exit 3) on the first artifact it cannot get, naming the `.env`
seam that governs it. You fix the mirror and re-run (acquired artifacts
fast-forward), or you write `CC_SOURCE_<X>=bundle` for that one artifact.
The decision is in `.env`, so a re-run and an update make the same one.

This is the shape mature air-gap tooling takes: the artifact set is fixed in
a manifest, acquired on a connected side, verified by checksum on the
disconnected side (Zarf's `zarf.yaml` + `package verify`), and every mirror
layer we sit on — podman's `registries.conf`, containerd's — falls through
to the public endpoint on a miss by DEFAULT; fail-loud is opt-in
(`pull-from-mirror`, k3s's `--disable-default-registry-endpoint`). The
`fetch` phase is where we make it loud.

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

The sources are independent: bundle ONLY what your mirrors cannot serve and
leave every other seam on its mirror. For each source, the smallest move
when it alone is unavailable (single-node profile):

| Missing source | Minimal move |
|---|---|
| Debian apt | Don't build: apt is consumed ONLY inside the three local image builds. Connected side: `bundle.sh export <dir> graphiti sandbox crawler`; air-gapped side: `CC_SOURCE_GRAPHITI=bundle`, `CC_SOURCE_SANDBOX=bundle`, `CC_SOURCE_CRAWLER=bundle`. Nothing else in the profile touches apt. |
| NodeSource (apt-based Node) | Node ≥ 22 is a HOST prerequisite (it *runs* the cockpit; `CC_SOURCE_COCKPIT=bundle` removes npm, not the runtime). Pre-stage the official self-contained tarball — `node-v22.x-linux-<arch>.tar.xz` from nodejs.org, untarred onto PATH — no apt involved. |
| registry.npmjs.org | `CC_SOURCE_COCKPIT=bundle` (pre-built cockpit, no `npm ci`); the sandbox build's `sandbox-runtime` install rides `CC_SOURCE_SANDBOX=bundle`. |
| PyPI | `CC_SOURCE_PYTHON=bundle` (wheelhouse, installed `--no-index --find-links`); the pip installs inside the graphiti/crawler builds ride those images' bundles. |
| A container registry | `CC_SOURCE_IMAGES=bundle` for the pulled set (`bundle.sh export <dir> images` saves every `images.txt` ref under its public name). |
| python-build-standalone | Install CPython 3.12 on the host, or point `CC_PYTHON_MIRROR` at a `file://` directory holding the archive. |
| huggingface.co | Pre-place `ggml-*.bin` in `config.whisperModelDir` (checked before any download), or set `WHISPER_MODELS_BASE_URL`. |

Remember the wheelhouse and the saved images are OS/arch-specific: export on
a connected machine matching the air-gapped target.

## Pins

`deploy/single/images.txt` pins every pulled image by digest; `fetch` pulls
`ref@digest` and tags it `ref`, so the templates' `name:tag` never triggers
a pull and the mirror cannot serve a drifted or poisoned tag. The Dockerfiles
pin their packages (`playwright==` must equal the crawler base's tag).
`requirements.lock` is the frozen Python resolution; `web/package-lock.json`
the cockpit's. A mirror that "gets updated regularly" changes nothing until
a release bumps a pin — that is the point.

## The bundle

`deploy/single/bundle.sh export <dir>` on a connected machine of the SAME
OS/arch (the wheelhouse is platform-specific) after a successful
`setup.sh fetch`: saves every image in `images.txt` under its public ref,
the three local images under `localhost/cc-*`, a `pip download` wheelhouse
of `requirements.lock`, and the built cockpit (`dist`, `server-dist`,
`bin-dist`, `node_modules`) — plus `MANIFEST` (sha256 of every file) and
`RELEASE`. `import` refuses a bundle from another release and verifies the
checksums of the files it is about to load before loading anything.
`setup.sh fetch` calls it per artifact for every `CC_SOURCE_<X>=bundle`.

Digests are verified on the connected side (pull-by-digest); the bundle's
integrity on the disconnected side is the tarball sha256 — a docker-archive
round-trip does not preserve the manifest-list digest, which is also why the
digests live in `images.txt` and not in the templates.

## The k3s profile

The two-node deployment keeps its own seams: `deploy/k3s/registries.yaml.example`
(containerd mirrors; add `--disable-default-registry-endpoint` so a miss
fails loud), `deploy/airgap.env.example` (pip/uv), `web/.npmrc.example`
(npm), and `deploy/airgap-image-tarballs.sh` for the locally-built images
(`ctr images import`). It also still needs the k3s install script vendored
and the NodeSource apt repo mirrored. The Dockerfiles' build-args apply to
its builds too (pass them to `podman build` by hand; the k3s build scripts
pass nothing and get the public defaults).
