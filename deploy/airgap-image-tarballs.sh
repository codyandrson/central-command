#!/usr/bin/env bash
# ============================================================================
# Export or import the locally-built images that have no registry to pull
# from, for carrying to an air-gapped site as tarballs — the two images
# AIRGAP.md says should travel this way (cc-graphiti, cc-crawler), matching
# the exact refs the two-node build-locally flow produces
# (build-graphiti-image.sh, build-crawler-image.sh).
#
#   export: run wherever the image was built (docker on the Pi for
#   cc-graphiti's arm64 build; podman on the chromebox for both amd64
#   builds) — saves whichever of these refs docker/podman knows about into
#   OUTPUT_DIR as one tarball per image.
#
#   import: run on the destination node — loads the tarballs with
#   `ctr images import` (containerd, k8s.io namespace, matching how the
#   build scripts import).
#
#   k3s ONLY (2026-08-30). The single-node profile has its own
#   deploy/single/bundle.sh: it saves under the refs that profile's templates
#   reference (localhost/cc-*, not docker.io/library/cc-* — a tarball from
#   here `podman load`s under the wrong name), covers the pulled images, the
#   wheelhouse and the pre-built cockpit, and verifies a checksum MANIFEST
#   before loading anything. The `--substrate single` path this script used
#   to offer did none of that and is gone.
#
#   Usage:
#     ./deploy/airgap-image-tarballs.sh export  /path/to/out-dir
#     ./deploy/airgap-image-tarballs.sh import  /path/to/tarball-dir
# ============================================================================
set -euo pipefail

IMAGE_REFS=(
  "docker.io/library/cc-graphiti:1.0.2-anthropic"
  "docker.io/library/cc-crawler:1"
)
CTR_NS=k8s.io  # k3s's containerd keeps kubernetes images in this namespace

usage() {
  echo "Usage: $0 export <output-dir>" >&2
  echo "       $0 import <input-dir>" >&2
  exit 1
}

tarball_name() {
  # docker.io/library/cc-graphiti:1.0.2-anthropic -> cc-graphiti_1.0.2-anthropic.tar
  echo "$1" | sed -e 's#.*/##' -e 's/:/_/' -e 's/$/.tar/'
}

save_tool() {
  if command -v docker >/dev/null 2>&1; then echo docker; return; fi
  if command -v podman >/dev/null 2>&1; then echo podman; return; fi
  echo "FATAL: neither docker nor podman found" >&2
  exit 1
}

[[ $# -ge 2 ]] || usage
MODE=$1
DIR=$2
shift 2
[[ $# -eq 0 ]] || { echo "FATAL: unknown arg $1 (single-node? use deploy/single/bundle.sh)" >&2; exit 1; }

case "$MODE" in
  export)
    mkdir -p "$DIR"
    TOOL=$(save_tool)
    for ref in "${IMAGE_REFS[@]}"; do
      if ! "$TOOL" image inspect "$ref" >/dev/null 2>&1; then
        echo "  skip $ref (not present locally)"
        continue
      fi
      out="$DIR/$(tarball_name "$ref")"
      echo "==> $TOOL save $ref -> $out"
      if [[ "$TOOL" == podman ]]; then
        podman save --format docker-archive "$ref" -o "$out"
      else
        docker save "$ref" -o "$out"
      fi
    done
    ;;
  import)
    [[ -d "$DIR" ]] || { echo "FATAL: $DIR not found" >&2; exit 1; }
    command -v k3s >/dev/null 2>&1 || { echo "FATAL: k3s not on PATH — this importer is k3s-only; single-node uses deploy/single/bundle.sh" >&2; exit 1; }
    for ref in "${IMAGE_REFS[@]}"; do
      tar="$DIR/$(tarball_name "$ref")"
      [[ -f "$tar" ]] || { echo "  skip $ref (no $tar)"; continue; }
      echo "==> importing $tar (k3s)"
      sudo k3s ctr -n "$CTR_NS" images import "$tar"
    done
    ;;
  *)
    usage
    ;;
esac
