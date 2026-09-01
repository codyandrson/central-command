#!/usr/bin/env bash
# ============================================================================
# Build cc-sandbox:1 for the CHROMEBOX ONLY and import it into the
# chromebox's containerd — single-arch, pinned (design §9.3: a newly built
# image defaults to one node's architecture, not the multi-arch floating
# pattern, until an operator deliberately escalates it).
#
#   Sandbox pods carry a REQUIRED nodeAffinity to chromebox (gVisor is only
#   installed there), so this image never needs to exist on the Pi.
#
#   Built with podman, NATIVELY (no QEMU) — same reasoning as
#   build-graphiti-image.sh. podman tags local builds `localhost/<name>`
#   unless told otherwise, which the kubelet then fails to match and tries to
#   pull from Docker Hub instead — ImagePullBackOff, discovered only when the
#   pod is scheduled. Always build/tag with the fully-qualified reference.
#   `ctr images ls -q | grep -qx` (capture into a variable FIRST, under
#   `set -o pipefail`, `ctr` gets SIGKILL'd by grep's early exit and the
#   pipeline reports failure even though the image is present).
#
#   Usage:  ./deploy/k3s/build-sandbox-image.sh
#
#   CC_BUILD_ONLY=1 stops after the build (no import/verify) — the stager's
#   cache-warming mode; see build-graphiti-image.sh for why.
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKERFILE="$REPO_ROOT/deploy/k3s/sandbox.Dockerfile"
IMAGE=cc-sandbox:1
IMAGE_REF="docker.io/library/${IMAGE}"
CHROMEBOX="${CC_COMPUTE_SSH:-chromebox_admin@100.113.118.28}"   # compute-node ssh target; override in deploy/pi/.env
CTR_NS=k8s.io  # k3s's containerd keeps kubernetes images in this namespace

[[ -f "$DOCKERFILE" ]] || { echo "FATAL: $DOCKERFILE not found" >&2; exit 1; }

echo "==> building amd64 on the chromebox (podman, native)"
ssh "$CHROMEBOX" 'rm -rf ~/cc-sandbox-build && mkdir -p ~/cc-sandbox-build'
scp -q "$DOCKERFILE" "$CHROMEBOX:~/cc-sandbox-build/Dockerfile"
# Unquoted heredoc on purpose: the flag and refs expand locally, so the
# remote side runs with this invocation's values baked in.
ssh "$CHROMEBOX" bash -se <<REMOTE
set -euo pipefail
cd ~/cc-sandbox-build
# Fully-qualified tag, or podman writes localhost/<name> — see the note above.
podman build --platform linux/amd64 -t "$IMAGE_REF" -f Dockerfile .
if [[ "${CC_BUILD_ONLY:-0}" != 1 ]]; then
  podman save --format docker-archive "$IMAGE_REF" -o /tmp/cc-sandbox.tar
  sudo k3s ctr -n "$CTR_NS" images import /tmp/cc-sandbox.tar
  rm -f /tmp/cc-sandbox.tar
fi
REMOTE
if [[ "${CC_BUILD_ONLY:-0}" == 1 ]]; then
  echo "==> CC_BUILD_ONLY=1 — build cached on the chromebox, nothing imported"
  exit 0
fi
echo "    imported into the chromebox's containerd"

echo
echo "==> verifying the chromebox holds the exact reference the kubelet will look up"
found=$(ssh "$CHROMEBOX" "sudo k3s ctr -n $CTR_NS images ls -q")
if echo "$found" | grep -qx "$IMAGE_REF"; then
  echo "  OK  $IMAGE_REF"
else
  echo "  MISSING $IMAGE_REF" >&2
  echo "FATAL: cc-sandbox would ImagePullBackOff on the chromebox." >&2
  exit 1
fi
