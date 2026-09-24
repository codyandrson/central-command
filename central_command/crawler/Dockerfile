# cc-crawler:1 — Crawl4AI + Playwright/Chromium behind a small FastAPI.
#
# Built NATIVELY with podman (no QEMU) — on the chromebox for k3s (the pod
# carries a REQUIRED nodeAffinity there), on the one machine for the
# single-node profile.
#
# BASE = Microsoft's own Playwright image (2026-08-30). It ships the browsers
# AND their OS libraries ("This image includes the Playwright browsers and
# browser system dependencies. The Playwright package/dependency is not
# included"), so this build touches NO apt mirror and NO browser CDN — the
# old python:slim base needed `playwright install --with-deps`, i.e. Debian's
# archive plus Microsoft's CDN, three mirrors for one image. Now the only
# external sources are the registry (the base) and PyPI. The playwright
# PACKAGE version MUST equal the image tag: the baked browsers are the build
# that version expects, and a mismatch downloads (or fails to) at runtime.
# The base REF comes from the manifest (2026-09-23 design record, D2):
# resolve-images.sh resolves images.txt's `crawler-base` row and the single-node
# build script passes it as CC_IMG_PLAYWRIGHT_PYTHON, so a mirror that lacks the
# locked tag or re-namespaces the path reaches this build too. The ARG default
# must equal images.txt's row — tests/test_single_airgap_seams.py fails the
# suite if they drift, and the playwright PACKAGE version below must equal the
# tag either way.
ARG CC_REGISTRY_MCR=mcr.microsoft.com
ARG CC_IMG_PLAYWRIGHT_PYTHON=${CC_REGISTRY_MCR}/playwright/python:v1.62.0-noble
FROM ${CC_IMG_PLAYWRIGHT_PYTHON}
# pip honours PIP_INDEX_URL from the environment; blank = PyPI.
ARG PIP_INDEX_URL=

# ── trust inside the build: one CA knob, one insecure knob (D4) ─────────────
# The CA is a BUILD SECRET, never the context and never a build-arg (a
# build-arg is visible in `podman history`). `required=false` lets a build that
# passes no --secret — the k3s build script — skip the branch instead of
# failing. podman >= 3.3 for `--secret`; no `# syntax=` directive needed with
# podman/buildah; the secret lands at /run/secrets/<id>.
ARG CC_TLS_INSECURE=0
RUN --mount=type=secret,id=cc_ca,required=false set -e; \
    if [ "$CC_TLS_INSECURE" = 1 ]; then \
      printf 'Acquire::https::Verify-Peer "false";\n' >/etc/apt/apt.conf.d/99cc-insecure; \
    fi; \
    if [ -s /run/secrets/cc_ca ]; then \
      if [ ! -f /etc/ssl/certs/ca-certificates.crt ]; then \
        apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*; \
      fi; \
      mkdir -p /usr/local/share/ca-certificates; \
      cp /run/secrets/cc_ca /usr/local/share/ca-certificates/cc-ca.crt; \
      update-ca-certificates; \
    fi
# One static path, because a Dockerfile cannot make an ENV conditional:
# `update-ca-certificates` appends the corporate root to the SYSTEM bundle, so
# with a secret this file holds it and without one it is the base's own set.
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    NPM_CONFIG_CAFILE=/etc/ssl/certs/ca-certificates.crt \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Everything PINNED — a browser-automation stack is not something to let
# float on a rebuild, and a mirror rebuild must produce the bytes the bundle
# holds. No [torch]/[transformer] extras: those pull PyTorch/HF models for
# features this service does not use. httpx is listed explicitly (crawl4ai
# pulls it in anyway) because the service imports it directly. Ubuntu's
# python is PEP 668 "externally managed"; this container IS the environment.
# With verification off, pip needs the INDEX HOST named (PIP_TRUSTED_HOST takes
# hosts, not URLs) — derived from PIP_INDEX_URL in the same RUN rather than a
# second seam to keep in step. Credentials in the URL are stripped with the
# scheme.
RUN if [ "$CC_TLS_INSECURE" = 1 ] && [ -n "$PIP_INDEX_URL" ]; then \
      h="${PIP_INDEX_URL#*://}"; h="${h##*@}"; export PIP_TRUSTED_HOST="${h%%/*}"; \
    fi; \
    pip install --no-cache-dir --break-system-packages \
        playwright==1.62.0 crawl4ai==0.9.2 fastapi==0.141.1 uvicorn==0.52.4 httpx==0.28.1

# crawl4ai's post-install creates its home dir and, unless CRAWL4AI_MODE=api,
# runs `playwright install --with-deps --force chromium` (apt + CDN — exactly
# what this base exists to avoid). api mode skips the browser step and keeps
# the rest. The trailing python line is the build's own check that the
# package sees the baked browser: it fails loud if the version pair drifts.
RUN CRAWL4AI_MODE=api crawl4ai-setup \
    && chmod -R a+rX /ms-playwright \
    && python3 -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch(); b.close(); p.stop()"

# The noble base already has an `ubuntu` user at uid 1000 (useradd exits 4
# on the collision — found at the first build on this base). The pods run as
# uid 1000, so evict it rather than renumber.
RUN userdel -r ubuntu 2>/dev/null; useradd -m -u 1000 crawler
COPY service.py /app/service.py
USER crawler
WORKDIR /app

EXPOSE 8091
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8091"]
