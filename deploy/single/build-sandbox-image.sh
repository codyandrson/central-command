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
#   The context is STAGED (D4 amendment, 2026-09-24): this Dockerfile is copied
#   into `<state>/build/cc-sandbox/` beside a `cc-ca.crt` — the corporate CA when
#   CC_CA_BUNDLE is set, an EMPTY file when it is not — and that directory is the
#   build context. Nothing else of the repo is sent to the build.
#
#   Usage:  ./build-sandbox-image.sh
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
DOCKERFILE="$REPO_ROOT/deploy/k3s/sandbox.Dockerfile"
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
# resolve-images.sh resolves images.txt's `sandbox-base` row against the (mirror)
# registry and writes CC_IMG_PYTHON, so a mirror that lacks the
# locked tag — or re-namespaces the PATH, which no CC_REGISTRY_* host can
# express — reaches this build too. Unset (a bare run, or the k3s build
# script) falls back to the Dockerfile's ARG default, which is the same
# locked ref by test.
if [[ -n "${CC_IMG_PYTHON:-}" ]]; then
  BUILD_ARGS+=(--build-arg "CC_IMG_PYTHON=${CC_IMG_PYTHON}")
fi

# The two trust knobs (2026-09-23 design record, D4, AMENDED 2026-09-24), fanned
# out in one place for the host side and handed to the build separately: no
# exported variable reaches a build container.
#   * --tls-verify=false when insecure, for the base-image pull this build does
#   * --build-arg CC_TLS_INSECURE, which the Dockerfile uses for apt/pip/npm
#   * the CA as a FILE in a STAGED build context (cc-ca.crt), which the
#     Dockerfile copies into the image's trust store
#
# The CA used to travel as `podman build --secret id=cc_ca,src=$CC_CA_BUNDLE`.
# That is BROKEN on Windows against a podman machine — podman joins a Windows
# separator into the Linux-side temp path:
#   open /mnt/c/.../tmp.X\podman-build-secret-N: The system cannot find the path
#   specified
# (measured on the 2026-09-24 Windows Podman Desktop run, reproduced with a
# trivial Dockerfile and every spelling of the context path; the same build
# without --secret succeeds). So with CC_CA_BUNDLE set, none of the three local
# images could build on the one platform this profile targets. The amendment:
# a CA certificate is PUBLIC material — it is the private key that is secret,
# and we never had one — so it is just a file in the context, and the context is
# STAGED outside the checkout because nothing here may write inside it (D7).
# shellcheck source=../env-lib.sh
. "$REPO_ROOT/deploy/env-lib.sh"
STATE_DIR="$(cc_state_dir "$REPO_ROOT/.env" "$REPO_ROOT" 2>/dev/null)" || STATE_DIR=""
cc_export_tls_env "$STATE_DIR"
TLS_ARGS=()
if [[ "${CC_TLS_INSECURE:-0}" == "1" ]]; then
  TLS_ARGS+=(--tls-verify=false --build-arg "CC_TLS_INSECURE=1")
  # The tls-insecure WARN is gated through cc_tls_insecure_warn_once (F25);
  # `|| true` because this script runs under `set -e` and a suppressed
  # (already-warned) run must not abort on the gate's 1.
  cc_tls_insecure_warn_once "this build's base-image pull and the apt/pip/npm fetches inside it" || true
fi
CA_SRC=""
if [[ -n "${CC_CA_BUNDLE:-}" ]]; then
  if [[ -r "$CC_CA_BUNDLE" ]]; then
    CA_SRC="$CC_CA_BUNDLE"
  else
    echo "FATAL: CC_CA_BUNDLE is set to $CC_CA_BUNDLE, which is not readable" >&2
    exit 1
  fi
fi
IMAGE_REF="localhost/cc-sandbox:1"

[[ -f "$DOCKERFILE" ]] || { echo "FATAL: $DOCKERFILE not found" >&2; exit 1; }

# The STAGED BUILD CONTEXT (D4 amendment, 2026-09-24). `build/` under the state
# directory is REGENERABLE — deleted and rebuilt on every run — so a CA from a
# previous run can never linger in it and the tree is safe to delete at any time.
# Nothing is written inside the checkout (tests/test_single_no_tree_writes.py).
if [[ -n "$STATE_DIR" ]]; then
  STAGED="$STATE_DIR/build/cc-sandbox"
else
  STAGED="$(mktemp -d)"
  trap 'rm -rf "$STAGED"' EXIT
fi
BUILD_CMD=(podman build "${BUILD_ARGS[@]}" "${TLS_ARGS[@]}"
           -t "$IMAGE_REF" -f "$STAGED/sandbox.Dockerfile" "$STAGED")

# CC_BUILD_DRY_RUN=1 prints what WOULD run and touches nothing — no staging, no
# build, no podman. It is how tests pin the resolved command and the context
# path on a host with no podman.
if [[ "${CC_BUILD_DRY_RUN:-0}" == "1" ]]; then
  echo "DRY-RUN context: $STAGED (staged from $DOCKERFILE)"
  if [[ -n "$CA_SRC" ]]; then
    echo "DRY-RUN ca: $STAGED/cc-ca.crt <- $CA_SRC"
  else
    echo "DRY-RUN ca: $STAGED/cc-ca.crt EMPTY (no CC_CA_BUNDLE — the Dockerfile's -s test reads an empty file as 'no CA')"
  fi
  printf 'DRY-RUN build:'; printf ' %s' "${BUILD_CMD[@]}"; printf '\n'
  exit 0
fi

cc_stage_build_context "$STAGED" "$CA_SRC" "$DOCKERFILE" >/dev/null

echo "==> building $IMAGE_REF from $STAGED (staged from $DOCKERFILE)"
"${BUILD_CMD[@]}"

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
