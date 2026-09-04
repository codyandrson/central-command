#!/usr/bin/env bash
# ============================================================================
# Build the derived graphiti image for the SINGLE-NODE profile, with podman.
#
#   cc-graphiti is the one image in this stack that is built rather than
#   pulled: the published zepai/knowledge-graph-mcp:1.0.2-standalone needs the
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
# deploy/single/.env — blank = public. The registry prefix must also be the
# name setup.sh fetch tagged the base image under, so the FROM resolves from
# local storage without a pull.
if [[ -f "$HERE/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$HERE/.env"
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
: "${CC_GRAPHITI_TAG:=1.0.2-anthropic}"
IMAGE_REF="localhost/cc-graphiti:${CC_GRAPHITI_TAG}"

[[ -d "$CTX" ]] || { echo "FATAL: build context $CTX not found" >&2; exit 1; }

echo "==> building $IMAGE_REF from $CTX"
podman build "${BUILD_ARGS[@]}" -t "$IMAGE_REF" "$CTX"

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
