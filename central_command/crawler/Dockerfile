# cc-crawler:1 — Crawl4AI + Playwright/Chromium behind a small FastAPI.
#
# amd64 only, built NATIVELY on the chromebox with podman (no QEMU) — same
# reasoning as sandbox.Dockerfile / build-graphiti-image.sh. The pod carries a
# REQUIRED nodeAffinity to the chromebox, so this image never needs to exist
# on the Pi.
#
# Chromium is BAKED IN (PLAYWRIGHT_BROWSERS_PATH below): nothing downloads a
# browser at runtime, which is what makes the service air-gap-safe once the
# image is mirrored.
FROM docker.io/library/python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# crawl4ai is PINNED — a browser-automation stack is not something to let float
# under an implicit `latest` on a rebuild. No [torch]/[transformer] extras:
# those pull PyTorch/HF models for features this service does not use.
# httpx is listed explicitly (crawl4ai/litellm pull it in anyway) because the
# service imports it directly for the LiteLLM preflight — an implicit
# transitive dep is not a dep you get to import.
RUN pip install --no-cache-dir crawl4ai==0.9.2 fastapi uvicorn httpx

# `playwright install --with-deps chromium` is the apt-level step (it installs
# the OS libraries Chromium needs on Debian slim); `crawl4ai-setup` is
# crawl4ai's own post-install (browser check + its home dir). Upstream's own
# Dockerfile runs both. chmod so the non-root user can execute the browser.
RUN playwright install --with-deps chromium \
    && crawl4ai-setup \
    && chmod -R a+rX /ms-playwright \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 crawler
COPY service.py /app/service.py
USER crawler
WORKDIR /app

EXPOSE 8091
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8091"]
