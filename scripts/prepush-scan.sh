#!/usr/bin/env bash
# pre-push guard for the PUBLIC repo: refuse any push whose ADDED lines match
# a secret shape or an entry in the operator's private identifier list.
#
#   install:  ln -sf ../../scripts/prepush-scan.sh .git/hooks/pre-push
#   list:     ~/.cc-private-identifiers — one extended regex per line, kept
#             OUTSIDE the repo (it is, by definition, the thing we never track)
#
# git feeds "<local ref> <local sha> <remote ref> <remote sha>" per ref on
# stdin. Never weaken this to get a push through — move the content to the
# instance repo instead (AGENTS.md, the sensitivity rule).
#
# What a push is scanned for (R9, PROCESS-030): EVERY commit it publishes, one
# at a time — a new branch, a new tag and an existing ref alike, so a secret
# added in one commit and reverted in the next is still caught — plus, for an
# existing ref whose old tip is a local commit, the net diff old..new as well
# (what this guard always did). An annotated tag also has its MESSAGE scanned,
# and only the message: the tagger line carries the operator's own name and
# email, which the private-identifier list would refuse on every release tag.
# A git failure REFUSES; nothing here fails open.
set -uo pipefail
SECRET_SHAPES='sk-ant-[A-Za-z0-9_-]{20,}|ATATT[0-9A-Za-z_-]{10,}|gh[pousr]_[0-9A-Za-z]{20,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|xox[baprs]-[0-9A-Za-z-]{10,}'
LIST="${CC_PRIVATE_IDENTIFIERS:-$HOME/.cc-private-identifiers}"
ZERO=0000000000000000000000000000000000000000
rc=0

# The added lines of a patch on stdin, each as "+<content>". Headers
# ("+++ b/path") are skipped by position (between "diff --git" and the first
# "@@"), not by shape, so an added line that itself starts with "+" is kept.
# A combined diff ("@@@", two-column prefix, as a merge shows without -m)
# is normalised to the same single "+".
added_lines() {
  awk '
    /^diff / { inhunk = 0; w = 1; next }
    /^@@@ /  { inhunk = 1; w = 2; next }
    /^@@ /   { inhunk = 1; w = 1; next }
    inhunk && substr($0, 1, w) ~ /\+/ { print "+" substr($0, w + 1) }
  '
}

while read -r _lref lsha rref rsha; do
  [[ -z "${lsha:-}" || "$lsha" == "$ZERO" ]] && continue   # branch delete
  added=""
  fail_git() { echo "pre-push: REFUSING $rref — git could not $1 ($lsha); fix that, or push after fetching" >&2; rc=1; }

  # Every commit this push publishes. `--not --remotes` leaves out what some
  # remote-tracking ref already holds (a tag on published commits scans
  # nothing); an existing ref's old tip is excluded too when it is a local
  # commit. Never `--remotes=<name>` from $1: that argument may be a URL.
  excl=(--remotes)
  existing_local=0
  if [[ "$rsha" != "$ZERO" ]] && git cat-file -e "${rsha}^{commit}" </dev/null 2>/dev/null; then
    existing_local=1; excl+=("$rsha")
  fi
  if ! log_out="$(git log -p -m --format= --no-ext-diff "$lsha" --not "${excl[@]}" -- . ':!docs/vendor' </dev/null)"; then
    fail_git "list the commits being pushed"; continue
  fi
  added+="$(added_lines <<<"$log_out")"$'\n'

  # An existing ref with a local old tip keeps the net diff it was always held to.
  if (( existing_local )); then
    if ! net_out="$(git diff --no-ext-diff "$rsha..$lsha" -- . ':!docs/vendor' </dev/null)"; then
      fail_git "diff the old tip against the new one"; continue
    fi
    added+="$(added_lines <<<"$net_out")"$'\n'
  fi

  # An annotated tag: its MESSAGE only (everything after the header's blank line).
  if ! otype="$(git cat-file -t "$lsha" </dev/null)"; then fail_git "read the object type"; continue; fi
  if [[ "$otype" == tag ]]; then
    if ! tagobj="$(git cat-file -p "$lsha" </dev/null)"; then fail_git "read the tag"; continue; fi
    added+="$(sed '1,/^$/d; s/^/+/' <<<"$tagobj")"$'\n'
  fi

  added="$(grep -E '^\+' <<<"$added" || true)"
  [[ -z "$added" ]] && continue
  hits="$(grep -nE "$SECRET_SHAPES" <<<"$added" || true)"
  if [[ -n "$hits" ]]; then echo "pre-push: SECRET SHAPE in added lines ($rref):" >&2; sed 's/^/  /' <<<"$hits" | cut -c1-160 >&2; rc=1; fi
  if [[ -f "$LIST" ]]; then
    while IFS= read -r pat; do
      [[ -z "$pat" || "$pat" == \#* ]] && continue
      if grep -qE -- "$pat" <<<"$added"; then
        echo "pre-push: PRIVATE IDENTIFIER (pattern #$(grep -nxF -- "$pat" "$LIST" | cut -d: -f1)) in added lines ($rref) — move it to the instance repo" >&2; rc=1
      fi
    done <"$LIST"
  else
    echo "pre-push: WARNING $LIST missing — only secret shapes were checked" >&2
  fi
done
[[ $rc -ne 0 ]] && echo "pre-push: REFUSED (scripts/prepush-scan.sh)" >&2
exit $rc
