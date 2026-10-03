# shellcheck shell=bash
# ============================================================================
# phases/demo.sh — the `demo` phase of the single-node install (deploy/single).
#
#   DEMO: fixture email -> triage -> YOUR approval in the cockpit -> a real
#   execution and its provenance, verified on the event log.
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
#   ROWS (steps.tsv, phase `demo`) — step, kind, probe; a probe marked
#   (setup.sh) is shared with another phase or the driver and lives there:
#     demo-feed                          run    p_demo_fed
#     demo-dispatch                      run    p_demo_fed
#     demo-approve                       gate   p_demo_decided
#     demo-decided                       run    p_demo_decided
#     demo-executed                      run    p_demo_decided
# ============================================================================

[[ -n "${CC_PHASE_DEMO_LOADED:-}" ]] && return 0
CC_PHASE_DEMO_LOADED=1

demo_decided() { # true once the event log shows a decided proposal
  local c; c="$(api_json "$(api_url)/api/events?kind=proposal.decided&limit=1" 'len(d.get("events",[]))')"
  [[ "$c" =~ ^[1-9] ]]
}
demo_awaiting() {
  local c; c="$(api_json "$(api_url)/api/dispatch" 'd.get("awaiting_human",0)')"
  [[ "$c" =~ ^[1-9] ]]
}

phase_demo() {
  load_env || return 1
  # Under the first row's name: nothing of the demo can run without the API,
  # and a check-name that is no row would mark none of them.
  api_up || { fail "demo-feed" "the API is not running — run ./setup.sh (it resumes at boot, which starts it)"; return 1; }

  if demo_decided; then
    pass "demo" "skipped — the event log already shows a decided proposal (the loop is proven on this install)"
    return 0
  fi

  if ! demo_awaiting; then
    # Knowledge-only email: triage proposes a graph episode, which the
    # Executor performs for REAL against the local graph — no Jira needed.
    # (The invoice fixture named a Jira issue no fresh install has; dry_run
    # used to make that free.)
    local eml="$REPO_ROOT/fixtures/emails/007-ownership-change.eml"
    [[ -f "$eml" ]] || { fail "demo-feed" "$eml missing"; return 1; }
    # pipefail INSIDE the body (D5): a `bash -c` child does not inherit this
    # script's, so a python that failed to read the fixture handed curl an empty
    # body and the step's status was curl's alone.
    step "demo-feed" "fixture email enrolled (a repeat Message-ID is a no-op by design)" \
      bash -c "set -o pipefail; $PY -c'import json,sys,pathlib;print(json.dumps({\"text\":pathlib.Path(sys.argv[1]).read_text()}))' '$eml' \
        | curl -fsS -X POST -H 'content-type: application/json' -d @- '$(api_url)/api/emails'" || return 1

    # Fire the dispatcher only when nothing is already working the queue.
    local busy; busy="$(api_json "$(api_url)/api/dispatch" 'd.get("in_flight",0)')"
    if [[ "$busy" =~ ^[1-9] ]]; then
      pass "demo-dispatch" "a run is already in flight — riding it"
    else
      # /api/dispatch/step AWAITS the whole triage run, and on a modest or shared
      # backend that outlives any curl ceiling (30 s here read as FAIL while the
      # run went on to park its proposal — 2026-09-17 Windows run). curl's 28
      # means "still running", not "failed": the proposal poll below is the wait.
      note "--> curl -fsS -m 30 -X POST $(api_url)/api/dispatch/step"
      local drc=0; curl -fsS -m 30 -X POST "$(api_url)/api/dispatch/step" >&2 || drc=$?
      case "$drc" in
        0)  pass "demo-dispatch" "dispatcher claimed the item (a real inference against your endpoint ran)" ;;
        28) pass "demo-dispatch" "dispatcher claimed the item — the inference is still running (outlived the 30 s call; polling for the proposal)" ;;
        *)  fail "demo-dispatch" "POST /api/dispatch/step failed (curl $drc) — read $STATE_DIR/uvicorn.log"; return 1 ;;
      esac
    fi

    note "triage is thinking — a real model call; this commonly takes a few minutes"
    if ! poll_until 600 10 demo_awaiting; then
      local failed; failed="$(api_json "$(api_url)/api/dispatch" '(d.get("ledger") or {}).get("FAILED",0)')"
      if [[ "$failed" =~ ^[1-9] ]]; then
        fail "demo-dispatch" "the triage run FAILED — read $STATE_DIR/uvicorn.log; recover with POST $(api_url)/api/work/<item_id>/requeue (never re-POST the email: a repeat Message-ID is a silent no-op)"
      else
        fail "demo-dispatch" "no proposal parked within 10 minutes — read $STATE_DIR/uvicorn.log and $(api_url)/api/dispatch"
      fi
      return 1
    fi
  fi

  # ── the operator's moment — never scripted away ────────────────────────────
  useraction "demo-approve" "a proposal is waiting in the Decisions Inbox — open http://127.0.0.1:${CC_COCKPIT_PORT:-3080}, review it, and decide (approve to see the Executor perform it)"
  if ! is_tty; then
    return 3
  fi
  note ""
  note "== your move =="
  note "Open http://127.0.0.1:${CC_COCKPIT_PORT:-3080} -> Decisions Inbox. Read the proposal and its evidence,"
  note "then decide. This gate IS the product; nothing here will decide for you."
  note "(waiting — checks every 10s, Ctrl-C to abandon and re-run later)"
  if ! poll_until 1800 10 demo_decided; then
    fail "demo" "no decision within 30 minutes — run ./setup.sh whenever you are ready; it resumes here, at demo"
    return 1
  fi
  pass "demo-decided" "decision recorded on the event log"
  # The gate above counted a USERACTION; the operator has now taken it in this
  # very run, so the phase must not exit 3 ("stopped for your action") on a
  # completed install (2026-09-18 Windows run: PASS, PASS, exit 3).
  ACTIONS=0

  local execd; execd="$(api_json "$(api_url)/api/events?kind=proposal.executed&limit=1" 'len(d.get("events",[]))')"
  local wfail; wfail="$(api_json "$(api_url)/api/events?kind=work.failed&limit=1" 'len(d.get("events",[]))')"
  if [[ "$execd" =~ ^[1-9] ]]; then
    pass "demo-executed" "the Executor performed the approved action and stamped provenance (a real write to the local graph)"
  elif [[ "$wfail" =~ ^[1-9] ]]; then
    fail "demo-executed" "the approval was recorded but execution FAILED — read $STATE_DIR/uvicorn.log (the graph service is the usual suspect: ./setup.sh status)"
    return 1
  else
    # Reject/dismiss is a legitimate decision — the loop is still proven.
    pass "demo-executed" "no execution event — you rejected or dismissed, which proves the gate just as well"
  fi
  note ""
  note "The install is complete and the spine is proven end to end."
  note "Deliberately still OFF: the mail feed, the dispatch drain, and every"
  note "recurring schedule — the cockpit's Crons tab and the root .env flip"
  note "each one when YOU decide. The rest of onboarding is your EA's: the"
  note "cockpit asks your name, then the team tour asks about your world."
}

# ── demo ────────────────────────────────────────────────────────────────────
p_demo_fed() {
  demo_decided && return 0
  demo_awaiting
}

p_demo_decided() {
  demo_decided
}
