#!/usr/bin/env bash
# ============================================================================
# Build cc-graphiti:1.0.2-anthropic for BOTH architectures and import it into
# each node's containerd.
#
#   cc-graphiti is the one image in the stack that is built here rather than
#   pulled. Every other image (litellm-database, postgres:16, redis:7-alpine,
#   neo4j:5.26.2, n8n:2.29.0) is multi-arch upstream, verified 2026-07-31 —
#   and so is this one's base, zepai/knowledge-graph-mcp:1.0.2-standalone.
#
#   Because cc-graphiti FLOATS, the image has to exist on both nodes: arm64 on
#   the Pi, amd64 on the chromebox. There is no registry in the homelab, so each
#   node's containerd gets a direct import. That is also why the Deployment sets
#   imagePullPolicy: IfNotPresent — `Always` would try to pull a tag that exists
#   in no registry and CrashLoop the pod.
#
#   NO QEMU. The Pi builds arm64 with docker; the chromebox builds amd64
#   NATIVELY with podman (which it already has). Emulated cross-builds are slow
#   and a source of subtle wheel-selection bugs, and are unnecessary here.
#
#   Usage:  ./deploy/k3s/build-graphiti-image.sh
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CTX="$REPO_ROOT/deploy/pi/graphiti"
IMAGE=cc-graphiti:1.0.2-anthropic
# Kubernetes normalises the bare `cc-graphiti:1.0.2-anthropic` in the Deployment
# to this fully-qualified reference, so BOTH nodes must hold it under exactly
# this name. Docker produces it implicitly; podman does NOT — it tags local
# builds `localhost/<name>`, which the kubelet then fails to match and tries to
# pull from Docker Hub, giving ImagePullBackOff on the chromebox only. Found
# 2026-07-31 on the first two-arch build. Always build/tag with the full ref.
IMAGE_REF="docker.io/library/${IMAGE}"
CHROMEBOX=chromebox_admin@100.113.118.28
# k3s's containerd keeps Kubernetes images in the k8s.io namespace. An import
# into the default namespace is invisible to the kubelet.
CTR_NS=k8s.io

[[ -d "$CTX" ]] || { echo "FATAL: build context $CTX not found" >&2; exit 1; }

echo "==> [1/2] arm64 on the Pi (docker)"
docker build --platform linux/arm64 -t "$IMAGE_REF" -t "$IMAGE" "$CTX"
TMP_ARM=$(mktemp /tmp/cc-graphiti-arm64.XXXXXX.tar)
trap 'rm -f "$TMP_ARM"' EXIT
docker save "$IMAGE_REF" -o "$TMP_ARM"
sudo k3s ctr -n "$CTR_NS" images import "$TMP_ARM"
echo "    imported into the Pi's containerd"

echo "==> [2/2] amd64 on the chromebox (podman, native)"
# Ship the context rather than the image: a native build there beats emulating
# x86 here, and the context is a Dockerfile plus a config file.
ssh "$CHROMEBOX" 'rm -rf ~/cc-graphiti-build && mkdir -p ~/cc-graphiti-build'
tar -C "$CTX" -cf - . | ssh "$CHROMEBOX" 'tar -C ~/cc-graphiti-build -xf -'
ssh "$CHROMEBOX" bash -se <<REMOTE
set -euo pipefail
cd ~/cc-graphiti-build
# Fully-qualified tag, or podman writes localhost/<name> — see the note above.
podman build --platform linux/amd64 -t "$IMAGE_REF" .
podman save --format docker-archive "$IMAGE_REF" -o /tmp/cc-graphiti-amd64.tar
sudo k3s ctr -n "$CTR_NS" images import /tmp/cc-graphiti-amd64.tar
rm -f /tmp/cc-graphiti-amd64.tar
REMOTE
echo "    imported into the chromebox's containerd"

echo
echo "==> verifying BOTH nodes hold the exact reference the kubelet will look up"
# Exact-match, not a substring: the whole point of this check is to catch a
# near-miss like localhost/cc-graphiti, which a fuzzy grep would happily pass.
fail=0
printf '  %-14s ' raspberrypi
if sudo k3s ctr -n "$CTR_NS" images ls -q | grep -qx "$IMAGE_REF"; then echo "OK  $IMAGE_REF"; else echo "MISSING $IMAGE_REF"; fail=1; fi
printf '  %-14s ' chromebox
if ssh "$CHROMEBOX" "sudo k3s ctr -n $CTR_NS images ls -q | grep -qx '$IMAGE_REF'"; then echo "OK  $IMAGE_REF"; else echo "MISSING $IMAGE_REF"; fail=1; fi
[[ $fail -eq 0 ]] || { echo; echo "FATAL: cc-graphiti would ImagePullBackOff on a node that lacks the exact ref." >&2; exit 1; }
