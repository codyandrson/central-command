# shellcheck shell=bash
# ============================================================================
# phases/test.sh — the `test` phase of the single-node install (deploy/single).
#
#   TEST: the pytest gate, through the venv — once per release, by the
#   ledger.
#
#   SOURCED by deploy/single/setup.sh, never executed: setup.sh sources the
#   ten phase files in steps.tsv's phase order before main runs, and this
#   file defines functions and constants and does nothing else, so sourcing it
#   twice is harmless. What more than one phase uses — the output protocol,
#   load_env and the .env helpers, compose, the image catch-up, the process
#   helpers `stop` shares, the probes two phases read — is setup.sh's.
#
#   Design record: docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md
#   D11 — "a thin orchestrator sourcing step files in order" (Sentry's
#   installer), one file per manifest phase (v2.58.0). The functions below
#   MOVED here from setup.sh unchanged, comments and all; the records they
#   cite are the ones that shaped them.
#
#   ROWS (steps.tsv, phase `test`) — step, kind, probe; a probe marked
#   (setup.sh) is shared with another phase or the driver and lives there:
#     test                               run    p_always  (setup.sh)
# ============================================================================

[[ -n "${CC_PHASE_TEST_LOADED:-}" ]] && return 0
CC_PHASE_TEST_LOADED=1

phase_test() {
  load_env || return 1
  # No `api_up` skip (D5): see the block comment above — the ledger row is what
  # makes this once per release.
  local py; py="$(venv_python)" || { fail "test" ".venv missing — run: ./setup.sh (it resumes at fetch, which creates it)"; return 1; }
  note "the offline suite is SEQUENTIAL and takes ~10 minutes — this is the gate, not a formality"
  step "test" "the offline suite is green" in_repo "$py" -m pytest -q || return 1
}
