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
# single-node driver passes them from the repo-root .env; the k3s build script
# passes nothing. See deploy/pi/graphiti/Dockerfile for why the apt file is
# rewritten from /etc/os-release rather than sed'ed.
# The base REF comes from the manifest (2026-09-23 design record, D2):
# resolve-images.sh resolves images.txt's `sandbox-base` row and the single-node
# build script passes it as CC_IMG_PYTHON, so a mirror that lacks the locked tag
# or re-namespaces the path reaches this build too. The ARG default must equal
# images.txt's row — tests/test_single_airgap_seams.py fails the suite if the
# two drift, which is what keeps a bare `podman build` (and the k3s build
# script, which passes no image arg) on the tested base.
ARG CC_REGISTRY_DOCKERIO=docker.io
ARG CC_IMG_PYTHON=${CC_REGISTRY_DOCKERIO}/library/python:3.12-slim-bookworm
FROM ${CC_IMG_PYTHON}
ARG CC_APT_MIRROR=
ARG CC_APT_SECURITY_MIRROR=
# npm reads any npm_config_* variable case-insensitively; blank = npmjs.
ARG NPM_CONFIG_REGISTRY=

# ── trust inside the build: one CA knob, one insecure knob (D4) ─────────────
# The CA arrives as a FILE IN THE BUILD CONTEXT — `cc-ca.crt` — and this layer
# copies it into the image's trust store. It used to be a BUILD SECRET
# (`--mount=type=secret,id=cc_ca`); that is broken on Windows against a podman
# machine (podman joins a Windows separator into the Linux-side temp path:
# `open /mnt/c/.../tmp.X\podman-build-secret-N: The system cannot find the path
# specified`, measured 2026-09-24), so with CC_CA_BUNDLE set none of the three
# local images could build there. A CA certificate is PUBLIC material — the
# private key is the secret, and no build ever saw one — so the secret mechanism
# bought nothing but that failure. `deploy/single/build-*.sh` STAGES the context
# outside the checkout and puts the CA in it; the k3s build scripts build from
# the repo context, which has no cc-ca.crt.
#
# `cc-ca.cr[t]` is a GLOB, which is what makes the file optional: zero matches is
# a silent no-op under BuildKit (verified with docker 29.8.1) — but an ERROR
# under buildah, i.e. `podman build` (containers/podman#25229,
# containers/buildah#3284). So every script that builds this with PODMAN puts a
# cc-ca.crt in the context even when there is no CA: an EMPTY one, which the
# `-s` test below reads exactly as the old `[ -s /run/secrets/cc_ca ]` did, and
# which is removed again rather than left in the trust directory.
ARG CC_TLS_INSECURE=0
COPY cc-ca.cr[t] /usr/local/share/ca-certificates/
RUN set -e; \
    if [ "$CC_TLS_INSECURE" = 1 ]; then \
      printf 'Acquire::https::Verify-Peer "false";\n' >/etc/apt/apt.conf.d/99cc-insecure; \
    fi; \
    if [ -s /usr/local/share/ca-certificates/cc-ca.crt ]; then \
      if [ ! -f /etc/ssl/certs/ca-certificates.crt ]; then \
        apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*; \
      fi; \
      update-ca-certificates; \
    else \
      rm -f /usr/local/share/ca-certificates/cc-ca.crt; \
    fi
# `update-ca-certificates` appends the corporate root to the SYSTEM bundle, so
# one static path is right in both cases: with a CA in the context it holds the
# CA, without one it is the base image's own set. A Dockerfile cannot make an ENV
# conditional, which is why it is the bundle and not the installed copy.
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    NPM_CONFIG_CAFILE=/etc/ssl/certs/ca-certificates.crt \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

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
# is a moving tag and a mirror rebuild must reproduce the same bytes.
# The npm step honours the insecure knob inline rather than through a global
# npmrc: the global file's path depends on the node PREFIX, and an exported
# variable in the same RUN is the one form that is certainly read (npm reads any
# npm_config_* case-insensitively).
RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs npm bubblewrap git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && if [ "$CC_TLS_INSECURE" = 1 ]; then export NPM_CONFIG_STRICT_SSL=false; fi \
    && npm install -g @anthropic-ai/sandbox-runtime@0.0.74 \
    && npm cache clean --force

RUN useradd -m -u 1000 -s /bin/bash sandbox \
    && mkdir -p /workspace \
    && chown -R sandbox:sandbox /workspace

USER sandbox
WORKDIR /workspace
