# Air-gapped mirrors plan — 2026-08-03

Re-derived after the prior write-up was lost to a `/tmp` scratchpad wipe.
Tracked in `docs/STATUS.md`: "Air-gapped mirrors gap: no containerd
`registries.yaml`, no pip/npm index overrides anywhere in deploy/. Needed
before the work deployment." This is a design-and-checklist item, not a
code-review item — re-verification below is grep confirmation of what
exists today, so the checklist starts from ground truth.

## What has to cross the air gap

### 1. Container images

Grepped from `deploy/k3s/*.yaml`:

| Image | Registry | Pin quality |
|---|---|---|
| `docker.io/library/postgres:16` (×3: postgres, litellm-db, n8n-db) | Docker Hub | major.minor only |
| `docker.io/library/redis:7-alpine` | Docker Hub | major only |
| `docker.io/library/neo4j:5.26.2` | Docker Hub | exact |
| `docker.io/n8nio/n8n:2.29.0` | Docker Hub | exact |
| `ghcr.io/berriai/litellm-database:main-stable` | GHCR | **moving tag** — flag regardless of air-gap; a `main-stable` re-push changes behavior under everyone with no local signal |
| `cc-graphiti:1.0.2-anthropic` | locally built | based on `docker.io/zepai/knowledge-graph-mcp:1.0.2-standalone` per `deploy/k3s/build-graphiti-image.sh:9` |

`build-graphiti-image.sh` already proves the tarball-import path works (it
builds locally per-arch and imports into containerd — arm64 via docker on
the Pi, amd64 natively via podman on the chromebox, no QEMU). That mechanism
generalizes to every image above: pull once outside the air gap, `docker
save` / `ctr images export` to a tarball, `ctr images import` on each node.

### 2. k3s itself

No mirror mechanism exists in the repo today — confirmed by grep: zero hits
for `get.k3s.io`, `k3s-airgap-images`, or any install script under `deploy/`.
k3s's own air-gap path needs, per node architecture:
- the `install.sh` (normally fetched from `get.k3s.io`) — must be vendored or
  mirrored, since the air-gapped site can't reach it live.
- the matching `k3s-airgap-images-<arch>.tar.zst` tarball, placed in
  `/var/lib/rancher/k3s/agent/images/` before `k3s.service` starts, so k3s's
  own bundled images (containerd, CNI, etc.) don't try to pull from the
  internet.

Nothing in `deploy/k3s/` addresses this today.

### 3. pip / uv

`pyproject.toml` deps are unpinned (`>=` throughout — `pydantic>=2.7`,
`fastapi>=0.111`, `pydantic-ai>=2.18`, etc., confirmed lines 7–29). An
air-gapped install needs either a vendored wheelhouse or a local PyPI mirror
reachable via `PIP_INDEX_URL` / `UV_INDEX_URL` — confirmed zero references to
either anywhere in `deploy/` today.

`deploy/pi/graphiti/Dockerfile` additionally pins
`graphiti-core[anthropic]==0.28.2` and `anthropic==0.116.0` exactly (line 27)
— these exact versions need to be present in whatever mirror/wheelhouse is
built, not just "some compatible version."

Toolchain, separate mirror problems from the app's own deps:
- **uv** itself is normally installed via `curl | sh` from astral's installer
  — needs a vendored copy or an internal mirror.
- **uv's Python management** downloads CPython from the
  `python-build-standalone` project on first use unless
  `UV_PYTHON_INSTALL_MIRROR` points somewhere internal — confirmed zero
  references in the repo. CLAUDE.md notes the toolchain is "uv-managed
  CPython 3.12 (Debian 12 ships 3.11)", so this download is NOT optional —
  Debian's system Python doesn't satisfy `requires-python = ">=3.12"`.
- **NodeSource** apt repo for Node 22 (Debian ships 18, per CLAUDE.md) — needs
  an internal apt mirror or a vendored `.deb`/tarball; zero references found
  in `deploy/`.

### 4. npm

`web/package.json`: 40 runtime dependencies, 24 dev dependencies (prior
report's "~34 runtime + ~20 dev" was a rough undercount — corrected here).
No `.npmrc` exists anywhere in the repo (checked repo root and `web/`), so
there is no registry override in place today — a fresh `npm install` on an
air-gapped host would fail outright with no local signal about why.

Two packages worth flagging for out-of-band native binary downloads (both
confirmed present in `package.json`, lines ~56 and ~71):
- `@fugood/whisper.node` (`^1.0.16`) — whisper.cpp bindings; native builds
  frequently fetch prebuilt binaries or trigger a native compile at install
  time.
- `node-pty` (`^1.1.0`) — native PTY bindings; same category of risk.

Both need verification (not yet done) of whether their npm postinstall
fetches anything beyond the tarball itself — that's an open question for the
work site, not something resolvable by grep alone.

## Mechanisms available (verified, not yet wired)

- **`/etc/rancher/k3s/registries.yaml`** on every node — supports `mirrors:`
  (per-registry endpoint override) and `configs:` (auth/CA per endpoint).
  `--disable-default-registry-endpoint` makes a misconfigured mirror fail
  loud instead of silently falling through to the real registry (which would
  quietly re-enable the air-gap leak the mirror exists to close).
- **`PIP_INDEX_URL` / `UV_INDEX_URL`** — environment-level override for pip
  and uv installs; **`UV_PYTHON_INSTALL_MIRROR`** — separately overrides
  where uv fetches CPython interpreters from.
- **`.npmrc`** `registry=` override — standard npm mechanism, currently
  entirely absent from this repo.

None of these are referenced anywhere in `deploy/` today (confirmed by grep
across `deploy/k3s/*.sh`, `deploy/pi/*.sh`, and the repo root) — this is a
green-field gap, not a partially-done feature.

## Proposed deliverables

No code changes — this is infrastructure config + a checklist doc, matching
the "small, safe, unscheduled" framing in `docs/STATUS.md`. ~4 files, ~150
lines:

1. `deploy/k3s/registries.yaml.example` — templated on a `MIRROR_HOST`
   placeholder, covering `docker.io`, `ghcr.io`, and the containerd config
   path, mirroring the shape `make-secrets.sh` uses today (imperative
   generation from a template, never a committed real value).
2. A pip/uv env snippet (could live in `deploy/AIRGAP.md` directly, or as a
   small `.env.airgap.example`) setting `PIP_INDEX_URL`, `UV_INDEX_URL`, and
   `UV_PYTHON_INSTALL_MIRROR`.
3. `web/.npmrc.example` — `registry=` pointed at the internal mirror
   placeholder.
4. `deploy/AIRGAP.md` — the real deliverable: a checklist walking through
   every category above (images, k3s binaries+tarball, pip/uv, toolchain,
   npm) with what to do on the work site, referencing the three template
   files above. `deploy/AIRGAP.md` does not exist yet (confirmed).
5. +1 assertion in `deploy/k3s/verify.sh` (currently 164 lines, four lettered
   sections A–D ending ~line 162) — e.g. assert `registries.yaml` is present
   and non-empty on both nodes if air-gap mode is declared, so a future
   deploy can't silently regress to direct internet pulls.

## Open questions for the work site (not resolvable from this repo)

- Mirror URL scheme, auth mechanism, and CA trust for the internal
  registry/PyPI/npm mirrors.
- Pull-through cache vs. fully pre-seeded mirror — changes whether
  `registries.yaml` needs `endpoint` (pull-through) or the images must be
  pre-pushed before deploy.
- Whether `cc-graphiti` is built on-site (needs the base image
  `zepai/knowledge-graph-mcp:1.0.2-standalone` mirrored too, plus a build
  toolchain) or shipped as pre-built tarballs from the connected side.
  Recommend tarballs — matches the existing two-node
  build-locally-then-import flow (`build-graphiti-image.sh`) and avoids
  needing docker (Pi) and podman (chromebox) build tooling on the air-gapped
  site at all.
- k3s version parity between whatever version is developed/tested here and
  whatever air-gap tarball ships to the work site — a mismatch is an
  ImagePullBackOff or a control-plane join failure discovered only at the
  work site.
- Whether `@fugood/whisper.node` / `node-pty` need special handling
  (prebuilt binary hosting) beyond a plain npm mirror — unresolved, flagged
  above, not yet investigated.
