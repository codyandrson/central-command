#!/usr/bin/env bash
# ============================================================================
# vendor_manifest.sh — the one-line fingerprint of docs/vendor/.
#
#   `docs/vendor/` is 47k of the repo's 48k tracked files (~553 MB) and it MUST
#   travel inside the release zip: on the air-gapped target it is the only
#   offline reference there will ever be (docs/vendor/README.md). Almost no
#   release changes it, yet `update.sh import` used to unzip it, `git rm` it,
#   tar-copy it and re-add it every time — over 70 minutes on NTFS with
#   Defender (Windows testbed, 2026-09-24, ledger F19).
#
#   So the tree carries its own fingerprint, `docs/vendor/MANIFEST`, and the
#   importer skips the whole subtree when the zip's fingerprint equals the
#   deployed one. The fingerprint is a sha256 over `git ls-files`' BLOB HASHES
#   — git already has them in the index, so this costs milliseconds and reads
#   no file content.
#
#   Two deliberate narrowings of what goes into the digest:
#     * the MODE is dropped (only `<path>\t<blob>` is hashed) so a checkout on
#       a filesystem without an exec bit or without symlink support cannot
#       change the digest — docs/vendor holds both 100755 and 120000 entries;
#     * the MANIFEST line itself is excluded, because it holds the digest.
#
#   Usage:
#     scripts/vendor_manifest.sh            regenerate docs/vendor/MANIFEST
#     scripts/vendor_manifest.sh --check    compare, print, exit 1 on mismatch
#     scripts/vendor_manifest.sh --print    print the computed line only
#
#   Sourced as a library by the fetch scripts and by tests; it defines
#   `cc_vendor_manifest_line` and only runs a command when executed directly.
# ============================================================================
set -uo pipefail

CC_VENDOR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CC_VENDOR_MANIFEST_REL="docs/vendor/MANIFEST"

# The digest is computed from the INDEX (`git ls-files -s`), which is what a
# commit — and therefore the release zip — will contain. A working tree that
# differs from the index would make the digest a claim about something else, and
# the whole point is that equal digests mean identical trees: so say so instead
# of hashing. The MANIFEST itself is exempt (it is being written).
cc_vendor_manifest_unstaged() { # -> the offending porcelain lines, if any
  git -C "$CC_VENDOR_ROOT" status --porcelain --untracked-files=all -- docs/vendor \
    | grep -v " ${CC_VENDOR_MANIFEST_REL}$" || true
}

cc_vendor_manifest_line() { # -> "sha256:<hex>" on stdout
  local hex
  hex="$(
    git -C "$CC_VENDOR_ROOT" ls-files -s -- docs/vendor \
      | sed 's/^[0-7]\{6\} \([0-9a-f]\{40,\}\) [0-9]\{1,\}\t\(.*\)$/\2\t\1/' \
      | grep -v "^${CC_VENDOR_MANIFEST_REL}	" \
      | LC_ALL=C sort \
      | sha256sum | cut -d' ' -f1
  )"
  [[ -n "$hex" ]] || return 1
  printf 'sha256:%s\n' "$hex"
}

cc_vendor_manifest_write() {
  local line unstaged
  unstaged="$(cc_vendor_manifest_unstaged)"
  if [[ -n "$unstaged" ]]; then
    printf 'docs/vendor differs from the index; `git add docs/vendor` first (a fetch leaves new/changed files unstaged), then re-run:\n%s\n' \
      "$(printf '%s\n' "$unstaged" | head -5)" >&2
    return 1
  fi
  line="$(cc_vendor_manifest_line)" || return 1
  printf '%s\n' "$line" >"$CC_VENDOR_ROOT/$CC_VENDOR_MANIFEST_REL"
  printf '%s' "$line"
}

cc_vendor_manifest_check() { # 0 = matches, 1 = differs/missing (reason on stdout)
  local want have unstaged
  unstaged="$(cc_vendor_manifest_unstaged)"
  if [[ -n "$unstaged" ]]; then
    echo "docs/vendor differs from the index — \`git add docs/vendor\`, then re-run scripts/vendor_manifest.sh:"
    printf '%s\n' "$unstaged" | head -5
    return 1
  fi
  want="$(cc_vendor_manifest_line)" || { echo "cannot compute: is this a git checkout?"; return 1; }
  if [[ ! -f "$CC_VENDOR_ROOT/$CC_VENDOR_MANIFEST_REL" ]]; then
    echo "missing $CC_VENDOR_MANIFEST_REL — regenerate with scripts/vendor_manifest.sh"
    return 1
  fi
  have="$(tr -d ' \t\r\n' <"$CC_VENDOR_ROOT/$CC_VENDOR_MANIFEST_REL")"
  want="${want//[$' \t\r\n']/}"
  if [[ "$have" != "$want" ]]; then
    echo "$CC_VENDOR_MANIFEST_REL says $have, the tree hashes to $want — regenerate with scripts/vendor_manifest.sh"
    return 1
  fi
  echo "$have"
  return 0
}

# Only act when RUN, not when sourced.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-}" in
    --check)
      if out="$(cc_vendor_manifest_check)"; then
        echo "PASS vendor-manifest: $out"
        exit 0
      fi
      echo "FAIL vendor-manifest: $out"
      exit 1
      ;;
    --print) cc_vendor_manifest_line ;;
    ""|--write)
      line="$(cc_vendor_manifest_write)" || { echo "FAIL vendor-manifest: could not compute (is this a git checkout?)" >&2; exit 1; }
      echo "wrote $CC_VENDOR_MANIFEST_REL: $line"
      ;;
    *) echo "usage: vendor_manifest.sh [--check|--print|--write]" >&2; exit 1 ;;
  esac
fi
