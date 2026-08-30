#!/usr/bin/env bash
# ============================================================================
# Build the crawler image for the SINGLE-NODE profile, with podman, locally.
#
#   Same Dockerfile and context as the k3s profile (central_command/crawler/) —
#   only the reference form and the delivery differ. deploy/k3s's version
#   builds on the chromebox over ssh and imports into containerd as
#   docker.io/library/cc-crawler:1, because the kubelet normalises a bare name
#   to docker.io/library and cannot see a localhost/ ref. Here the runtime is
#   podman, which resolves `localhost/` out of local storage and would SEARCH
#   REGISTRIES for a bare name — so localhost/ is the correct ref, and there is
#   no ssh/ctr dance.
#
#   Native build only. No --platform, no QEMU: one machine building for itself.
#   Takes a while: the layer installs Chromium and its Debian dependencies.
#
#   Usage:  ./build-crawler-image.sh
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
CTX="$REPO_ROOT/central_command/crawler"
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
IMAGE_REF="localhost/cc-crawler:1"

for f in "$CTX/Dockerfile" "$CTX/service.py"; do
  [[ -f "$f" ]] || { echo "FATAL: $f not found" >&2; exit 1; }
done

echo "==> building $IMAGE_REF from $CTX"
podman build "${BUILD_ARGS[@]}" -t "$IMAGE_REF" -f "$CTX/Dockerfile" "$CTX"

# Exact match, and CAPTURE BEFORE GREPPING — `podman images | grep -q` inverts
# under `set -o pipefail` (grep exits first, podman takes SIGPIPE).
have="$(podman images --format '{{.Repository}}:{{.Tag}}')"
if grep -qx "$IMAGE_REF" <<<"$have"; then
  echo "    OK  $IMAGE_REF"
else
  echo "FATAL: $IMAGE_REF is not in local storage — kube play would fail to resolve it." >&2
  exit 1
fi
