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
#
# Mirror seams (2026-08-30): a build container sees none of the host's mirror
# configuration, so each external source is a build-arg — blank = public. The
# single-node driver passes them from deploy/single/.env; the k3s build script
# passes nothing. See deploy/pi/graphiti/Dockerfile for why the apt file is
# rewritten from /etc/os-release rather than sed'ed.
ARG CC_REGISTRY_DOCKERIO=docker.io
FROM ${CC_REGISTRY_DOCKERIO}/library/python:3.12-slim-bookworm
ARG CC_APT_MIRROR=
ARG CC_APT_SECURITY_MIRROR=
# npm reads any npm_config_* variable case-insensitively; blank = npmjs.
ARG NPM_CONFIG_REGISTRY=

RUN if [ -n "$CC_APT_MIRROR" ]; then \
      . /etc/os-release && [ "$ID" = debian ] || { echo "CC_APT_MIRROR: base is '$ID', only Debian is supported" >&2; exit 1; }; \
      rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*; \
      printf 'Types: deb\nURIs: %s\nSuites: %s %s-updates\nComponents: main\n' \
        "$CC_APT_MIRROR" "$VERSION_CODENAME" "$VERSION_CODENAME" >/etc/apt/sources.list.d/debian.sources; \
      [ -z "$CC_APT_SECURITY_MIRROR" ] || printf '\nTypes: deb\nURIs: %s\nSuites: %s-security\nComponents: main\n' \
        "$CC_APT_SECURITY_MIRROR" "$VERSION_CODENAME" >>/etc/apt/sources.list.d/debian.sources; \
    fi

# The apt packages ride Debian stable's own versioning (bookworm only moves
# them for security fixes); the npm package is PINNED because npm's `latest`
# is a moving tag and a mirror rebuild must produce the bytes the bundle holds.
RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs npm bubblewrap git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/sandbox-runtime@0.0.74 \
    && npm cache clean --force

RUN useradd -m -u 1000 -s /bin/bash sandbox \
    && mkdir -p /workspace \
    && chown -R sandbox:sandbox /workspace

USER sandbox
WORKDIR /workspace
