#!/usr/bin/env bash
# Regenerate requirements.lock as a UNIVERSAL lock (v2.46.0).
#
# The lock used to be a `pip freeze` of the Linux venv: no platform markers,
# so `CC_AIRGAP=1` (which installs from the lock instead of resolving) could
# not install on Windows — uvloop has no Windows wheels, and magika 0.6.3 caps
# onnxruntime at 1.20.1 on win32 only. A universal resolution carries the
# markers, so ONE file installs on every platform the app supports.
#
# The CURRENT pins are the constraints: regenerating must not move a version
# that was not deliberately bumped (the lock is the suite-tested graph). To
# bump a package, edit its pin in the constraint set this script derives, or
# drop it from the constraints and let the resolver pick.
#
# Deliberate act — run it, run the full suite, commit both together.
set -euo pipefail
cd "$(dirname "$0")/.."
cons="$(mktemp)"; out=""; trap 'rm -f "$cons" "$out"' EXIT
# Every current pin as a constraint, keeping its marker (a forked pin stays
# forked). Comments and blank lines dropped.
grep -E '^[A-Za-z0-9_.-]+==' requirements.lock >"$cons"
[[ -s "$cons" ]] || { echo "requirements.lock has no pins to constrain — refusing" >&2; exit 1; }
out="$(mktemp)"
uv pip compile pyproject.toml --extra dev --extra runtime --universal \
  --python-version 3.12 -c "$cons" --no-header --no-annotate -q -o "$out"
[[ -s "$out" ]] || { echo "uv pip compile produced nothing — lock left untouched" >&2; exit 1; }
{
  cat <<'HDR'
# requirements.lock — the air-gap reproducibility pin: pyproject.toml's loose
# >= bounds resolve to WHATEVER a mirror serves; this file resolves to the
# exact graph the offline suite passed against.
#
# UNIVERSAL since v2.46.0 (2026-09-25): `uv pip compile --universal`, so a
# pin that only applies on one platform carries its marker (uvloop is not
# for win32; magika 0.6.3 caps onnxruntime at 1.20.1 on win32 only). Before
# that it was a Linux `pip freeze` and `CC_AIRGAP=1` could not install on
# Windows at all. `tests/test_requirements_lock.py` pins the markers.
#
# Install with:
#   uv pip install -r requirements.lock && uv pip install -e . --no-deps
# Regenerate with:  scripts/regen_lock.sh   (a deliberate act — see AGENTS.md)
HDR
  cat "$out"
} >requirements.lock.new
mv requirements.lock.new requirements.lock
echo "requirements.lock regenerated ($(grep -cE '^[A-Za-z0-9_.-]+==' requirements.lock) pins)"
