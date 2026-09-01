#!/usr/bin/env bash
# ============================================================================
# Build cc-crawler:1 for the CHROMEBOX ONLY and import it into the chromebox's
# containerd — single-arch, pinned, exactly like build-sandbox-image.sh.
#
#   The crawler pod carries a REQUIRED nodeAffinity to chromebox (Chromium is
#   the memory hog the placement rule keeps off the Pi), so this image never
#   needs to exist on the Pi and is NOT built there.
#
#   Built with podman, NATIVELY (no QEMU). podman tags local builds
#   `localhost/<name>` unless told otherwise, which the kubelet then fails to
#   match and tries to pull from Docker Hub instead — ImagePullBackOff,
#   discovered only when the pod is scheduled. Always build/tag with the
#   fully-qualified reference, and verify with `grep -qx` on a CAPTURED list
#   (under `set -o pipefail`, `ctr images ls | grep -q` reports failure on
#   SUCCESS because grep's early exit SIGPIPEs ctr).
#
#   Usage:  ./deploy/k3s/build-crawler-image.sh
#   Takes a while: the layer installs Chromium + its Debian dependencies.
#
#   CC_BUILD_ONLY=1 stops after the build (no import/verify) — the stager's
#   cache-warming mode; see build-graphiti-image.sh for why.
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKERFILE="$REPO_ROOT/central_command/crawler/Dockerfile"
SERVICE="$REPO_ROOT/central_command/crawler/service.py"
IMAGE=cc-crawler:1
IMAGE_REF="docker.io/library/${IMAGE}"
CHROMEBOX="${CC_COMPUTE_SSH:-chromebox_admin@100.113.118.28}"   # compute-node ssh target; override in deploy/pi/.env
CTR_NS=k8s.io  # k3s's containerd keeps kubernetes images in this namespace

for f in "$DOCKERFILE" "$SERVICE"; do
  [[ -f "$f" ]] || { echo "FATAL: $f not found" >&2; exit 1; }
done

echo "==> building amd64 on the chromebox (podman, native)"
ssh "$CHROMEBOX" 'rm -rf ~/cc-crawler-build && mkdir -p ~/cc-crawler-build'
scp -q "$DOCKERFILE" "$CHROMEBOX:~/cc-crawler-build/Dockerfile"
scp -q "$SERVICE" "$CHROMEBOX:~/cc-crawler-build/service.py"
# Unquoted heredoc on purpose: the flag and refs expand locally, so the
# remote side runs with this invocation's values baked in.
ssh "$CHROMEBOX" bash -se <<REMOTE
set -euo pipefail
cd ~/cc-crawler-build
# Fully-qualified tag, or podman writes localhost/<name> — see the note above.
podman build --platform linux/amd64 -t "$IMAGE_REF" -f Dockerfile .
if [[ "${CC_BUILD_ONLY:-0}" != 1 ]]; then
  podman save --format docker-archive "$IMAGE_REF" -o /tmp/cc-crawler.tar
  sudo k3s ctr -n "$CTR_NS" images import /tmp/cc-crawler.tar
  rm -f /tmp/cc-crawler.tar
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
  echo "FATAL: cc-crawler would ImagePullBackOff on the chromebox." >&2
  exit 1
fi
