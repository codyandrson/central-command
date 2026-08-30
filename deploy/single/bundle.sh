#!/usr/bin/env bash
# ============================================================================
# The single-node profile's AIR-GAP BUNDLE: export on a connected machine,
# import on the disconnected one.
#
#   A MIRROR IS THE PRIMARY PATH. deploy/AIRGAP.md's whole design is that an
#   enterprise mirror (CC_REGISTRY_*, PIP_INDEX_URL, npm registry) serves the
#   install unchanged. This bundle is the operator's EXPLICIT per-artifact
#   fallback for what the mirror does not carry — chosen one artifact at a
#   time with CC_SOURCE_<X>=bundle in deploy/single/.env, never a whole
#   parallel install path.
#
#   IMPORT VERIFIES TARBALL CHECKSUMS, NOT REGISTRY DIGESTS. images.txt pins
#   a sha256 manifest digest per image and that is verified at EXPORT time, by
#   setup.sh fetch pulling by digest. A `podman save`/`podman load` round-trip
#   does NOT preserve it (the archive is a single-platform docker-archive, not
#   the manifest list the digest names), so on this side integrity is the
#   sha256 of the tarball recorded in MANIFEST — Zarf's pattern: a manifest of
#   checksums, verified before anything is loaded.
#
#   THE WHEELHOUSE IS OS/ARCH/PYTHON SPECIFIC. `pip download` resolves binary
#   wheels for the interpreter that runs it, so it must be built on the same
#   OS, the same architecture and Python 3.12 — an arm64 wheelhouse installs
#   into nothing on an amd64 target. RELEASE is checked for the same class of
#   reason: a bundle from another release carries another release's lockfile
#   and image set, so it is simply the wrong bundle.
#
#   Usage:  ./bundle.sh export <dir> [images|graphiti|sandbox|crawler|cockpit|python|all]
#           ./bundle.sh import <dir> [ ... same ... ]      (default: all)
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"

if [[ -f "$HERE/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$HERE/.env"
  set +a
fi
: "${CC_REGISTRY_DOCKERIO:=docker.io}"
: "${CC_REGISTRY_GHCR:=ghcr.io}"
: "${CC_REGISTRY_MCR:=mcr.microsoft.com}"
: "${CC_GRAPHITI_TAG:=1.0.2-anthropic}"

WARNS=0; FAILS=0
pass() { printf 'PASS %s: %s\n' "$1" "$2"; }
warn() { printf 'WARN %s: %s\n' "$1" "$2"; WARNS=$((WARNS+1)); }
fail() { printf 'FAIL %s: %s\n' "$1" "$2"; FAILS=$((FAILS+1)); }

usage() { echo "Usage: $0 export|import <dir> [images|graphiti|sandbox|crawler|cockpit|python|all]" >&2; exit 1; }

[[ $# -ge 2 ]] || usage
MODE=$1; DIR=$2; WHAT=${3:-all}
IMAGES_TXT="$HERE/images.txt"
VERSION_VALUE="$(grep -E '^version=' "$REPO_ROOT/VERSION" | head -1 | cut -d= -f2)"

want() { [[ "$WHAT" == all || "$WHAT" == "$1" ]]; }

# docker.io/library/postgres:16 -> postgres_16.tar   (same rule as
# deploy/airgap-image-tarballs.sh)
tarball_name() { echo "$1" | sed -e 's#.*/##' -e 's/:/_/' -e 's/$/.tar/'; }

# Each images.txt line -> "<mirror-ref> <canonical-ref>". Canonical is the
# PUBLIC host: that is the ref the image is saved under, whatever mirror it
# came from, so an import restores the name the templates reference.
have_images_txt() {
  [[ -f "$IMAGES_TXT" ]] || { fail bundle-images-txt "$IMAGES_TXT not found"; return 1; }
}

image_refs() {
  local key path _digest _component host canon
  while read -r key path _digest _component; do
    if [[ -z "$key" || "$key" == \#* ]]; then continue; fi
    case "$key" in
      dockerio) host=$CC_REGISTRY_DOCKERIO; canon=docker.io ;;
      ghcr)     host=$CC_REGISTRY_GHCR;     canon=ghcr.io ;;
      mcr)      host=$CC_REGISTRY_MCR;      canon=mcr.microsoft.com ;;
      *) warn bundle-images-txt "unknown registry key '$key' — skipping $path"; continue ;;
    esac
    echo "$host/$path $canon/$path"
  done < <(sed 's/#.*//' "$IMAGES_TXT")
}

# ---------------------------------------------------------------- export ---

save_ref() {  # <ref-to-save-as> [alternate ref to retag from]
  local canon=$1 mirror=${2:-} out="$DIR/images/$(tarball_name "$1")"
  if ! podman image inspect "$canon" >/dev/null 2>&1; then
    if [[ -n "$mirror" && "$mirror" != "$canon" ]] && podman image inspect "$mirror" >/dev/null 2>&1; then
      podman tag "$mirror" "$canon"
    else
      warn "export-$(basename "$out" .tar)" "not in local storage — skipped"
      return 0
    fi
  fi
  podman save --format docker-archive "$canon" -o "$out" >&2
  pass "export-$(basename "$out" .tar)" "$canon -> images/$(tarball_name "$canon")"
}

export_all() {
  mkdir -p "$DIR"
  if want images && have_images_txt; then
    mkdir -p "$DIR/images"
    while read -r mirror canon; do save_ref "$canon" "$mirror"; done < <(image_refs)
  fi
  for n in graphiti sandbox crawler; do
    want "$n" || continue
    mkdir -p "$DIR/images"
    if [[ "$n" == graphiti ]]; then
      save_ref "localhost/cc-graphiti:$CC_GRAPHITI_TAG"
    else
      save_ref "localhost/cc-$n:1"
    fi
  done
  if want python; then export_python; fi
  if want cockpit; then export_cockpit; fi
  write_manifest
}

export_python() {
  command -v uv >/dev/null 2>&1 || { fail export-python "uv not found — needed for a throwaway 3.12 venv"; return 0; }
  local tmp; tmp=$(mktemp -d)
  # --seed installs pip: uv has no `uv pip download`, and the wheels must be
  # resolved by a 3.12 interpreter of THIS os/arch. PIP_INDEX_URL is honoured
  # straight from the environment by pip itself.
  if uv venv --seed --python 3.12 "$tmp" >&2 &&
     "$tmp/bin/python" -m pip download -r "$REPO_ROOT/requirements.lock" -d "$DIR/wheelhouse" >&2; then
    pass export-python "wheelhouse: $(find "$DIR/wheelhouse" -type f | wc -l) files"
  else
    fail export-python "pip download failed (see stderr)"
  fi
  rm -rf "$tmp"
}

COCKPIT_DIRS=(dist server-dist bin-dist node_modules)

export_cockpit() {
  local major
  command -v node >/dev/null 2>&1 || { fail export-cockpit "node not found (need >= 22)"; return 0; }
  major=$(node -v | sed -e 's/^v//' -e 's/\..*//')
  [[ "$major" -ge 22 ]] || { fail export-cockpit "node $(node -v) — need >= 22"; return 0; }
  ( cd "$REPO_ROOT/web" && npm ci && npm run build ) >&2 || { fail export-cockpit "npm build failed"; return 0; }
  local present=()
  for d in "${COCKPIT_DIRS[@]}"; do
    if [[ -d "$REPO_ROOT/web/$d" ]]; then present+=("$d"); else warn export-cockpit "web/$d absent after build"; fi
  done
  [[ ${#present[@]} -gt 0 ]] || { fail export-cockpit "nothing built"; return 0; }
  tar -cf "$DIR/cockpit.tar" -C "$REPO_ROOT/web" "${present[@]}"
  pass export-cockpit "cockpit.tar: ${present[*]}"
}

write_manifest() {
  echo "$VERSION_VALUE" > "$DIR/RELEASE"
  ( cd "$DIR" && find . -type f ! -name MANIFEST | sed 's#^\./##' | sort | xargs -r sha256sum > MANIFEST )
  pass bundle-manifest "$(wc -l < "$DIR/MANIFEST") files, release $VERSION_VALUE"
}

# ---------------------------------------------------------------- import ---

# The MANIFEST lines this artifact needs, as a `sha256sum -c` input.
manifest_pattern() {
  case "$1" in
    all)      echo '.' ;;
    images)   echo 'images/' ;;
    graphiti) echo "images/cc-graphiti_" ;;
    sandbox)  echo 'images/cc-sandbox_' ;;
    crawler)  echo 'images/cc-crawler_' ;;
    cockpit)  echo 'cockpit.tar' ;;
    python)   echo 'wheelhouse/' ;;
  esac
}

verify() {
  local pat lines
  pat=$(manifest_pattern "$WHAT")
  if [[ "$pat" == '.' ]]; then lines=$(cat "$DIR/MANIFEST")
  else lines=$(grep -F "  $pat" "$DIR/MANIFEST" || true); fi
  [[ -n "$lines" ]] || { fail bundle-verify "MANIFEST has no entries for '$WHAT'"; return 1; }
  if ( cd "$DIR" && sha256sum -c --quiet >&2 <<<"$lines" ); then
    pass bundle-verify "$(wc -l <<<"$lines") files checksum-clean"
  else
    fail bundle-verify "checksum mismatch or missing file — nothing loaded"
    return 1
  fi
}

load_tar() {  # <tarball name> <check> — <check> is the exact ref expected after load
  local tar="$DIR/images/$1" want_ref=$2 have
  [[ -f "$tar" ]] || { warn "import-$1" "not in bundle"; return 0; }
  podman load -i "$tar" >&2
  # Capture before grepping: `podman images | grep -q` exits at the first
  # match, podman takes SIGPIPE, and under pipefail the pipeline reports
  # failure on success. This inversion has bitten this repo before.
  have=$(podman images --format '{{.Repository}}:{{.Tag}}')
  if grep -qx "$want_ref" <<<"$have"; then
    pass "import-$1" "$want_ref"
  elif [[ "$want_ref" == localhost/* ]] && grep -qx "docker.io/library/${want_ref#localhost/}" <<<"$have"; then
    # A k3s-side export saved it under docker.io/library/; this profile's
    # templates reference localhost/.
    podman tag "docker.io/library/${want_ref#localhost/}" "$want_ref"
    pass "import-$1" "$want_ref (retagged from docker.io/library/)"
  else
    fail "import-$1" "loaded, but $want_ref is not in local storage"
  fi
}

import_all() {
  [[ -d "$DIR" && -f "$DIR/MANIFEST" && -f "$DIR/RELEASE" ]] || { fail bundle-dir "$DIR is not a bundle (need MANIFEST and RELEASE)"; return 0; }
  if [[ "$(cat "$DIR/RELEASE")" != "$VERSION_VALUE" ]]; then
    fail bundle-release "bundle is $(cat "$DIR/RELEASE"), this checkout is $VERSION_VALUE"
    return 0
  fi
  pass bundle-release "$VERSION_VALUE"
  verify || return 0

  if want images && have_images_txt; then
    while read -r _mirror canon; do
      load_tar "$(tarball_name "$canon")" "$canon"
    done < <(image_refs)
  fi
  if want graphiti; then load_tar "cc-graphiti_$CC_GRAPHITI_TAG.tar" "localhost/cc-graphiti:$CC_GRAPHITI_TAG"; fi
  if want sandbox;  then load_tar "cc-sandbox_1.tar" "localhost/cc-sandbox:1"; fi
  if want crawler;  then load_tar "cc-crawler_1.tar" "localhost/cc-crawler:1"; fi
  if want cockpit;  then import_cockpit; fi
  # Nothing to load for python: setup.sh installs with --no-index --find-links.
  if want python; then
    if [[ -d "$DIR/wheelhouse" ]]; then pass import-python "wheelhouse verified at $DIR/wheelhouse"
    else fail import-python "no $DIR/wheelhouse"; fi
  fi
  return 0
}

import_cockpit() {
  [[ -f "$DIR/cockpit.tar" ]] || { warn import-cockpit "not in bundle"; return 0; }
  for d in "${COCKPIT_DIRS[@]}"; do rm -rf "${REPO_ROOT:?}/web/$d"; done
  tar -xf "$DIR/cockpit.tar" -C "$REPO_ROOT/web"
  pass import-cockpit "extracted into web/"
}

case "$MODE" in
  export) export_all ;;
  import) import_all ;;
  *) usage ;;
esac

if   (( FAILS > 0 )); then exit 1
elif (( WARNS > 0 )); then exit 2
else exit 0
fi
