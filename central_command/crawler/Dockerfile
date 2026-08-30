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
ARG CC_REGISTRY_MCR=mcr.microsoft.com
FROM ${CC_REGISTRY_MCR}/playwright/python:v1.62.0-noble
# pip honours PIP_INDEX_URL from the environment; blank = PyPI.
ARG PIP_INDEX_URL=

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Everything PINNED — a browser-automation stack is not something to let
# float on a rebuild, and a mirror rebuild must produce the bytes the bundle
# holds. No [torch]/[transformer] extras: those pull PyTorch/HF models for
# features this service does not use. httpx is listed explicitly (crawl4ai
# pulls it in anyway) because the service imports it directly. Ubuntu's
# python is PEP 668 "externally managed"; this container IS the environment.
RUN pip install --no-cache-dir --break-system-packages \
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
