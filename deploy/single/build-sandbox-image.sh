#!/usr/bin/env bash
# ============================================================================
# Build the sandbox image for the SINGLE-NODE profile, with podman, locally.
#
#   Same Dockerfile as the k3s profile (deploy/k3s/sandbox.Dockerfile) — only
#   the reference form and the delivery differ. deploy/k3s's version builds on
#   the chromebox over ssh and imports into containerd as
#   docker.io/library/cc-sandbox:1, because the kubelet normalises a bare name
#   to docker.io/library and cannot see a localhost/ ref at all. Here the
#   runtime is podman, which resolves `localhost/` out of local storage and
#   would SEARCH REGISTRIES for a bare name — so localhost/ is the correct ref
#   and no ssh/ctr dance exists.
#
#   Containment trade, stated plainly: the container this image runs in is
#   rootless podman, NOT gVisor. Weaker isolation than the k3s profile.
#
#   The Dockerfile COPYs nothing, so it is built against an EMPTY context —
#   nothing of the repo is sent to the build.
#
#   Usage:  ./build-sandbox-image.sh
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
DOCKERFILE="$REPO_ROOT/deploy/k3s/sandbox.Dockerfile"
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
IMAGE_REF="localhost/cc-sandbox:1"

[[ -f "$DOCKERFILE" ]] || { echo "FATAL: $DOCKERFILE not found" >&2; exit 1; }

CTX="$(mktemp -d)"
trap 'rm -rf "$CTX"' EXIT

echo "==> building $IMAGE_REF from $DOCKERFILE (empty context)"
podman build "${BUILD_ARGS[@]}" -t "$IMAGE_REF" -f "$DOCKERFILE" "$CTX"

# Exact match, not a substring: the point is to catch a near-miss like a bare
# `cc-sandbox:1`, which a fuzzy grep would happily pass.
#
# And CAPTURE BEFORE GREPPING. `podman images ... | grep -q` exits at the first
# match, podman takes SIGPIPE, and under `set -o pipefail` the pipeline reports
# FAILURE on success. This exact inversion has bitten this repo before.
have="$(podman images --format '{{.Repository}}:{{.Tag}}')"
if grep -qx "$IMAGE_REF" <<<"$have"; then
  echo "    OK  $IMAGE_REF"
else
  echo "FATAL: $IMAGE_REF is not in local storage — the runner would fail to resolve it." >&2
  exit 1
fi
