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
# instance repo instead (CLAUDE.md, the sensitivity rule).
set -uo pipefail
SECRET_SHAPES='sk-ant-[A-Za-z0-9_-]{20,}|ATATT[0-9A-Za-z_-]{10,}|gh[pousr]_[0-9A-Za-z]{20,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|xox[baprs]-[0-9A-Za-z-]{10,}'
LIST="${CC_PRIVATE_IDENTIFIERS:-$HOME/.cc-private-identifiers}"
ZERO=0000000000000000000000000000000000000000
rc=0
while read -r _lref lsha _rref rsha; do
  [[ "$lsha" == "$ZERO" ]] && continue                    # branch delete
  if [[ "$rsha" == "$ZERO" ]]; then range="$lsha"; else range="$rsha..$lsha"; fi
  added="$(git diff "$range" -- . ':!docs/vendor' | grep -E '^\+[^+]' | grep -vE '^\+\+\+ ' || true)"
  [[ -z "$added" ]] && continue
  hits="$(grep -nE "$SECRET_SHAPES" <<<"$added" || true)"
  if [[ -n "$hits" ]]; then echo "pre-push: SECRET SHAPE in added lines:" >&2; sed 's/^/  /' <<<"$hits" | cut -c1-160 >&2; rc=1; fi
  if [[ -f "$LIST" ]]; then
    while IFS= read -r pat; do
      [[ -z "$pat" || "$pat" == \#* ]] && continue
      if grep -qE -- "$pat" <<<"$added"; then
        echo "pre-push: PRIVATE IDENTIFIER (pattern #$(grep -nxF -- "$pat" "$LIST" | cut -d: -f1)) in added lines — move it to the instance repo" >&2; rc=1
      fi
    done <"$LIST"
  else
    echo "pre-push: WARNING $LIST missing — only secret shapes were checked" >&2
  fi
done
[[ $rc -ne 0 ]] && echo "pre-push: REFUSED (scripts/prepush-scan.sh)" >&2
exit $rc
