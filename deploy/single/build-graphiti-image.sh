#!/usr/bin/env bash
# ============================================================================
# Build the derived graphiti image for the SINGLE-NODE profile, with podman.
#
#   cc-graphiti is the one image in this stack that is built rather than
#   pulled: the published zepai/knowledge-graph-mcp:1.1.0-standalone needs the
#   patches under deploy/pi/graphiti/patches/ (invalidation scope, dedupe
#   field order, the FastMCP Host allowlist). Every other image is multi-arch
#   upstream and just pulls.
#
#   IMAGE REFERENCE — the trap, and why this profile is the OPPOSITE of k3s.
#   podman tags a local build `localhost/<name>:<tag>`, and podman's own
#   resolver treats `localhost/` as "look in local storage, never a registry".
#   So for `podman kube play`, localhost/ IS the correct ref and a bare name
#   would be searched for in registries. deploy/k3s/build-graphiti-image.sh
#   does the reverse (docker.io/library/...) because the kubelet normalises a
#   bare name to docker.io/library and cannot see a localhost/ ref at all.
#   Same image, two ref forms, each wrong in the other's runtime.
#
#   Native build only. No --platform, no QEMU: this profile is one machine
#   building for itself.
#
#   Usage:  ./build-graphiti-image.sh
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
CTX="$REPO_ROOT/deploy/pi/graphiti"

# Mirror seams (2026-08-30): the build container sees none of the host's
# mirror configuration, so each seam travels as a build-arg from
# the repo-root .env — blank = public. The registry prefix must also be the
# name setup.sh fetch tagged the base image under, so the FROM resolves from
# local storage without a pull. (The answer file moved to the repo root in
# v2.42.0 — one file for the app and the deployment.)
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$REPO_ROOT/.env"
  set +a
fi
BUILD_ARGS=(
  --build-arg "CC_REGISTRY_DOCKERIO=${CC_REGISTRY_DOCKERIO:-docker.io}"
  --build-arg "CC_REGISTRY_MCR=${CC_REGISTRY_MCR:-mcr.microsoft.com}"
  --build-arg "CC_APT_MIRROR=${CC_APT_MIRROR:-}"
  --build-arg "CC_APT_SECURITY_MIRROR=${CC_APT_SECURITY_MIRROR:-}"
  --build-arg "PIP_INDEX_URL=${CC_PYPI_INDEX_URL:-}"
  --build-arg "UV_DEFAULT_INDEX=${CC_PYPI_INDEX_URL:-}"
  --build-arg "NPM_CONFIG_REGISTRY=${CC_NPM_REGISTRY:-}"
)

# The BASE REF comes from the image manifest (2026-09-23 design record, D2):
# resolve-images.sh resolves images.txt's `graphiti-base` row against the (mirror)
# registry and writes CC_IMG_ZEPAI_KNOWLEDGE_GRAPH_MCP, so a mirror that lacks the
# locked tag — or re-namespaces the PATH, which no CC_REGISTRY_* host can
# express — reaches this build too. Unset (a bare run, or the k3s build
# script) falls back to the Dockerfile's ARG default, which is the same
# locked ref by test.
if [[ -n "${CC_IMG_ZEPAI_KNOWLEDGE_GRAPH_MCP:-}" ]]; then
  BUILD_ARGS+=(--build-arg "CC_IMG_ZEPAI_KNOWLEDGE_GRAPH_MCP=${CC_IMG_ZEPAI_KNOWLEDGE_GRAPH_MCP}")
fi

# The two trust knobs (2026-09-23 design record, D4), fanned out in one place
# for the host side and handed to the build separately: no exported variable
# reaches a build container, and the CA must not enter the build CONTEXT.
#   * --tls-verify=false when insecure, for the base-image pull this build does
#   * --build-arg CC_TLS_INSECURE, which the Dockerfile uses for apt/pip/npm
#   * --secret id=cc_ca, which the Dockerfile installs into the image's trust
#     store — a SECRET, because a build-arg is visible in `podman history`
# shellcheck source=../env-lib.sh
. "$REPO_ROOT/deploy/env-lib.sh"
STATE_DIR="$(cc_state_dir "$REPO_ROOT/.env" "$REPO_ROOT" 2>/dev/null)" || STATE_DIR=""
cc_export_tls_env "$STATE_DIR"
TLS_ARGS=()
if [[ "${CC_TLS_INSECURE:-0}" == "1" ]]; then
  TLS_ARGS+=(--tls-verify=false --build-arg "CC_TLS_INSECURE=1")
  echo "WARN tls-insecure: $(cc_tls_insecure_warn_text "this build's base-image pull and the apt/pip/npm fetches inside it")"
fi
if [[ -n "${CC_CA_BUNDLE:-}" ]]; then
  if [[ -r "$CC_CA_BUNDLE" ]]; then
    TLS_ARGS+=(--secret "id=cc_ca,src=$CC_CA_BUNDLE")
  else
    echo "FATAL: CC_CA_BUNDLE is set to $CC_CA_BUNDLE, which is not readable" >&2
    exit 1
  fi
fi
: "${CC_GRAPHITI_TAG:=1.0.2-anthropic}"
IMAGE_REF="localhost/cc-graphiti:${CC_GRAPHITI_TAG}"

[[ -d "$CTX" ]] || { echo "FATAL: build context $CTX not found" >&2; exit 1; }

echo "==> building $IMAGE_REF from $CTX"
podman build "${BUILD_ARGS[@]}" "${TLS_ARGS[@]}" -t "$IMAGE_REF" "$CTX"

# Exact match, not a substring: the whole point is to catch a near-miss like
# a bare `cc-graphiti:tag`, which a fuzzy grep would happily pass.
#
# And CAPTURE BEFORE GREPPING. `podman images ... | grep -q` exits at the
# first match, podman takes SIGPIPE, and under `set -o pipefail` the pipeline
# reports FAILURE on success. This exact inversion has bitten this repo before.
have="$(podman images --format '{{.Repository}}:{{.Tag}}')"
if grep -qx "$IMAGE_REF" <<<"$have"; then
  echo "    OK  $IMAGE_REF"
else
  echo "FATAL: $IMAGE_REF is not in local storage — compose would fail to resolve it." >&2
  exit 1
fi
