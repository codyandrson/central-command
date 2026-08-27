# cc-sandbox:1 — the image every sandbox Job runs (D-sandbox, slice 1).
#
# Layer 2 (defense-in-depth, dormant): @anthropic-ai/sandbox-runtime (srt) +
# bubblewrap ship in the image, but srt's own netns-removal step does not
# survive under gVisor's netstack (verified 2026-08-03 on the chromebox —
# `RTM_NEWADDR: No child processes`; see the plan's residual note). The two
# layers actually load-bearing here are gVisor (the pod's runtimeClassName)
# and the namespace's default-deny NetworkPolicy — both outside this image.
# srt stays installed in case a later slice finds a way to use it (bwrap
# --share-net, fs-only isolation, is confirmed to work under gVisor).
FROM docker.io/library/python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs npm bubblewrap git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/sandbox-runtime \
    && npm cache clean --force

RUN useradd -m -u 1000 -s /bin/bash sandbox \
    && mkdir -p /workspace \
    && chown -R sandbox:sandbox /workspace

USER sandbox
WORKDIR /workspace
