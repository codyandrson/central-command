# Air-gapped mirrors

The work-site environment already has mirrors for images, PyPI, and npm.
These are the config seams so Central Command can point at them — not a
mirroring project.

- **Container images** — `deploy/k3s/registries.yaml.example`. Fill in
  `MIRROR_HOST`, copy to `/etc/rancher/k3s/registries.yaml` on EVERY node,
  restart `k3s`/`k3s-agent`. Optionally add `--disable-default-registry-endpoint`
  so a misconfigured mirror fails loud instead of silently reaching the
  internet.
- **pip/uv** — `deploy/airgap.env.example`. `PIP_INDEX_URL`, `UV_INDEX_URL`,
  `UV_PYTHON_INSTALL_MIRROR`, export before `pip install` / `uv sync`.
- **npm** — `web/.npmrc.example`. Copy to `web/.npmrc` with the real
  `MIRROR_HOST`.

## What these seams don't cover

- The k3s **install script** itself (`get.k3s.io`) still needs vendoring or a
  mirror; the airgap-images tarball (`k3s-airgap-images-<arch>.tar.zst`) is
  the standard k3s alternative for its own bundled images.
- The **NodeSource apt repo** for Node 22 (Debian ships 18) — needs an
  internal apt mirror or a vendored package.
- `cc-graphiti` should ship as **pre-built tarballs** imported via
  `ctr images import`, matching the existing two-node build-locally flow
  (`build-graphiti-image.sh`) — not built on-site.
- `cc-crawler` likewise ships as a **pre-built tarball** for the chromebox
  (`build-crawler-image.sh`). Chromium and its Debian dependencies are BAKED
  INTO the image — nothing downloads a browser at runtime — so mirroring the
  image is the whole requirement. Building it on-site instead would need
  PyPI + the Debian apt repo + Playwright's CDN (`playwright install` fetches
  the browser build), which is three mirrors for one image.
  `deploy/airgap-image-tarballs.sh export`/`import` does the save/load for
  both images (docker or podman, whichever built them, on export; `ctr
  images import` on k3s or `podman load` on single-node, on import).
- `ghcr.io/berriai/litellm-database:main-stable` was a **moving tag**; pinned
  to a digest 2026-08-25 in `deploy/k3s/30-litellm.yaml` and
  `deploy/pi/docker-compose.yml` (bumping is now a deliberate act).
- **Whisper STT models** phone home to HuggingFace on first voice-input use
  (`WHISPER_MODELS_BASE_URL` in `web/server/lib/constants.ts`, default
  `https://huggingface.co/ggerganov/whisper.cpp/resolve/main`). Override it
  with the `WHISPER_MODELS_BASE_URL` env var to point at an internal mirror,
  or simply pre-place the `ggml-*.bin` file at `config.whisperModelDir` —
  `whisper-local.ts` checks for the local file before ever downloading, so a
  pre-placed model needs no mirror and no env var at all.
- `deploy/pi/graphiti/Dockerfile` runs `apt-get install -y patch` at build
  time — needs a reachable Debian mirror (or the package vendored) if the
  image is ever rebuilt on-site rather than shipped as a tarball (see below).

No `verify.sh` assertion was added: it asserts the live homelab, which has
no mirrors — a mirror check there would fail at home.
