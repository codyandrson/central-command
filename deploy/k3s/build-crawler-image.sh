#!/usr/bin/env bash
# ============================================================================
# Build cc-crawler:1 for the COMPUTE NODE ONLY and import it into that node's
# containerd — single-arch, pinned, exactly like build-sandbox-image.sh.
#
#   The crawler pod carries a REQUIRED nodeAffinity to the compute node (Chromium
#   is the memory hog the placement rule keeps off the anchor), so this image never
#   needs to exist on the anchor and is NOT built there.
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

# The compute node is an answer, never a default: the environment (setup.sh's
# load_env, the updater), then this checkout's deploy/k3s/.env; else this exits
# here, naming the key, before anything changes.
# shellcheck source=instance-lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/instance-lib.sh"
COMPUTE_SSH="$(cc_k3s_compute_ssh "${BASH_SOURCE[0]}")"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKERFILE="$REPO_ROOT/central_command/crawler/Dockerfile"
SERVICE="$REPO_ROOT/central_command/crawler/service.py"
IMAGE=cc-crawler:1
IMAGE_REF="docker.io/library/${IMAGE}"
CTR_NS=k8s.io  # k3s's containerd keeps kubernetes images in this namespace

for f in "$DOCKERFILE" "$SERVICE"; do
  [[ -f "$f" ]] || { echo "FATAL: $f not found" >&2; exit 1; }
done

echo "==> building amd64 on the compute node (podman, native)"
ssh "$COMPUTE_SSH" 'rm -rf ~/cc-crawler-build && mkdir -p ~/cc-crawler-build'
scp -q "$DOCKERFILE" "$COMPUTE_SSH:~/cc-crawler-build/Dockerfile"
scp -q "$SERVICE" "$COMPUTE_SSH:~/cc-crawler-build/service.py"
# Unquoted heredoc on purpose: the flag and refs expand locally, so the
# remote side runs with this invocation's values baked in.
ssh "$COMPUTE_SSH" bash -se <<REMOTE
set -euo pipefail
cd ~/cc-crawler-build
# An EMPTY cc-ca.crt, because the Dockerfile's optional-CA step is a GLOB COPY
# (`cc-ca.cr[t]`) and zero matches is an ERROR under buildah — which is what
# `podman build` is (containers/podman#25229, containers/buildah#3284), even
# though BuildKit treats it as a no-op. The k3s profile has no CC_CA_BUNDLE seam,
# so the file is always empty here and the Dockerfile's `-s` test reads that as
# "no CA" and removes it again. The single-node profile stages a real one.
: >cc-ca.crt
# Fully-qualified tag, or podman writes localhost/<name> — see the note above.
podman build --platform linux/amd64 -t "$IMAGE_REF" -f Dockerfile .
if [[ "${CC_BUILD_ONLY:-0}" != 1 ]]; then
  podman save --format docker-archive "$IMAGE_REF" -o /tmp/cc-crawler.tar
  sudo k3s ctr -n "$CTR_NS" images import /tmp/cc-crawler.tar
  rm -f /tmp/cc-crawler.tar
fi
REMOTE
if [[ "${CC_BUILD_ONLY:-0}" == 1 ]]; then
  echo "==> CC_BUILD_ONLY=1 — build cached on the compute node, nothing imported"
  exit 0
fi
echo "    imported into the compute node's containerd"

echo
echo "==> verifying the compute node holds the exact reference the kubelet will look up"
found=$(ssh "$COMPUTE_SSH" "sudo k3s ctr -n $CTR_NS images ls -q")
if echo "$found" | grep -qx "$IMAGE_REF"; then
  echo "  OK  $IMAGE_REF"
else
  echo "  MISSING $IMAGE_REF" >&2
  echo "FATAL: cc-crawler would ImagePullBackOff on the compute node." >&2
  exit 1
fi
