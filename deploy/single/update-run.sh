#!/usr/bin/env bash
# ============================================================================
# update-run.sh — the DETACHED runner behind the cockpit's "Apply update now"
# on the single-node profile (2026-09-03).
#
#   The API cannot apply its own update: update.sh refuses to mutate under a
#   live API, and the apply restarts the very process that would drive it.
#   On k3s that job belongs to the root cc-update systemd unit; here there is
#   no systemd, so the API spawns THIS script fully detached and dies mid-run
#   by design. It brackets update.sh apply with ./setup.sh stop / boot and
#   writes the same status.json shape the cockpit's update dialog polls
#   (web/src/components/UpdateBadge.tsx), which polls straight through the
#   restart.
#
#   Invoked as:  update-run.sh <target-version> <deploy/single dir>
#   ...but never from its checked-in path: the merge rewrites this very file
#   while a run is in flight, and bash reads scripts incrementally — the API
#   copies it to <state>/update/run.sh first and spawns the copy, handing over
#   CC_UPDATE_DIR so both sides poll the same directory. Since v2.42.0 that
#   directory is in the STATE DIR, never inside the checkout (design record
#   2026-09-23, D7).
#
#   Failure contract (since v2.57.0 it reads WHERE the apply stopped, because
#   `update.sh apply` now acquires the new release BEFORE it merges — 2026-10-01
#   design record, D5 — so a stop no longer implies a moved tree):
#     * apply exit 1 AFTER the merge (HEAD moved) -> automatic ./update.sh
#       rollback (state "rolled_back"), matching the k3s helper;
#     * apply exit 1 BEFORE the merge (HEAD where it was: the staged
#       acquisition, the version gate, the backup) -> NO rollback. There is
#       nothing to roll back, and `rollback` resets to the NEWEST pre-update-*
#       tag — an EARLIER update's, i.e. it would have DOWNGRADED a deployment
#       this run never touched. Restart, state "failed" naming the line;
#     * apply exit 3 (the operator's move) -> state "failed", phase
#       "operator-action" — what the dialog shows as "needs the operator" —
#       with the pause's own USERACTION sentence and the command that finishes
#       the job. The API is restarted so the cockpit is reachable again, EXCEPT
#       for the pre-ledger adoption pause (USERACTION ledger-adopt): its merged
#       tree's `setup.sh boot` is refused by the very ledger it asks the
#       operator to build, so the sentence says `./setup.sh` brings it back.
# ============================================================================
set -uo pipefail

TARGET="${1:?usage: update-run.sh <target-version> <deploy-single-dir>}"
SINGLE="${2:?usage: update-run.sh <target-version> <deploy-single-dir>}"
REPO_ROOT="$(cd "$SINGLE/../.." && pwd)"
# CC_UPDATE_DIR is what the API passes (api/update.py `_update_dir`); without
# it — a run started by hand — derive the same default from the state dir.
if [[ -n "${CC_UPDATE_DIR:-}" ]]; then
  UPD="$CC_UPDATE_DIR"
else
  # shellcheck source=../env-lib.sh
  . "$REPO_ROOT/deploy/env-lib.sh"
  STATE_DIR="$(cc_state_dir "$REPO_ROOT/.env" "$REPO_ROOT")" \
    || STATE_DIR="${TMPDIR:-/tmp}/central-command-state"
  UPD="$STATE_DIR/update"
fi
STATUS="$UPD/status.json"
LOG="$UPD/apply.log"

mkdir -p "$UPD"
exec >>"$LOG" 2>&1
STARTED_AT="$(date -u +%FT%TZ)"
echo "== update-run $STARTED_AT: target v$TARGET =="

json_esc() { local s="${1//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/ · }"; printf '%s' "$s"; }
current_version() { sed -n 's/^version=//p' "$REPO_ROOT/VERSION" 2>/dev/null | head -1; }

write_status() { # write_status <state> <phase> [error]
  local tmp="$STATUS.tmp" now; now="$(date -u +%FT%TZ)"
  {
    printf '{"state":"%s","phase":"%s","current":"%s","target":"%s","started_at":"%s","updated_at":"%s"' \
      "$1" "$2" "$(json_esc "$(current_version)")" "$(json_esc "$TARGET")" "$STARTED_AT" "$now"
    [[ "$1" != "running" ]] && printf ',"finished_at":"%s"' "$now"
    [[ -n "${3:-}" ]] && printf ',"error":"%s"' "$(json_esc "$3")"
    printf '}\n'
  } >"$tmp" && mv -f "$tmp" "$STATUS"
}

# The last USERACTION/FAIL line a child wrote to this same log — the one
# sentence worth surfacing in the dialog.
last_protocol_line() { grep -E '^(USERACTION|FAIL) ' "$LOG" 2>/dev/null | tail -1; }

# The same, from THIS apply's lines only and skipping update.sh's `acquire`
# summary: for a stop before the merge the line worth showing is the one that
# names the seam or the alias (resolve-images, catalog-probe, …), and the
# summary only says "the line(s) above" — which a dialog does not have.
seam_line() {
  tail -n +"$(( LOG_AT_APPLY + 1 ))" "$LOG" 2>/dev/null \
    | grep -E '^(USERACTION|FAIL) ' | grep -vE '^(USERACTION|FAIL) acquire: ' | tail -1
}

# Where `local` points. Empty when it cannot be read — then the stop is treated
# as AFTER the merge (the pre-v2.57.0 contract), never as "nothing changed".
head_rev() { git -C "$REPO_ROOT" rev-parse -q --verify HEAD 2>/dev/null; }

api_port() { local p; p="$(sed -n 's/^CC_API_PORT=//p' "$REPO_ROOT/.env" 2>/dev/null | tail -1)"; printf '%s' "${p:-8080}"; }
api_up() { curl -fsS -m 3 "http://127.0.0.1:$(api_port)/health" >/dev/null 2>&1; }

restart_api() { # best-effort boot + health wait; true iff healthy
  "$SINGLE/setup.sh" boot
  local i
  for i in $(seq 1 90); do api_up && return 0; sleep 1; done
  return 1
}

write_status running "starting"
# Let the API flush the 202 that spawned us before we take it down.
sleep 2

write_status running "stop-api"
"$SINGLE/setup.sh" stop || true
for i in $(seq 1 30); do api_up || break; sleep 1; done
if api_up; then
  write_status failed "stop-api" "the API is still answering after ./setup.sh stop — stop it yourself, then run: ./update.sh apply"
  exit 1
fi

write_status running "apply"
HEAD_BEFORE="$(head_rev)"
LOG_AT_APPLY="$(wc -l <"$LOG" 2>/dev/null | tr -d ' ')"; LOG_AT_APPLY="${LOG_AT_APPLY:-0}"
CC_UPDATE_DRIVEN=1 "$SINGLE/update.sh" apply
rc=$?
HEAD_AFTER="$(head_rev)"
# Nothing moved: the apply stopped BEFORE the merge (D5's staged acquisition,
# the version gate, the spine backup). Provable only when both reads succeeded.
UNMOVED=0
[[ -n "$HEAD_BEFORE" && "$HEAD_BEFORE" == "$HEAD_AFTER" ]] && UNMOVED=1

if (( UNMOVED )) && (( rc == 1 || rc == 3 )); then
  why="$(seam_line)"
  restart_api || true
  if (( rc == 3 )); then
    write_status failed "operator-action" "${why:-the update paused for your action before anything was changed} — NOTHING was changed (still v$(current_version)): fix what it names, then apply the update again (here, or: cd deploy/single && ./update.sh apply)"
    exit 3
  fi
  write_status failed "apply" "${why:-the update stopped before anything was changed} — NOTHING was changed and nothing was rolled back (still v$(current_version)): fix what it names, then apply the update again. Details: $LOG"
  exit 1
fi

if (( rc == 1 )); then
  apply_err="$(last_protocol_line)"
  write_status running "rollback"
  if CC_UPDATE_DRIVEN=1 "$SINGLE/update.sh" rollback && restart_api; then
    write_status rolled_back "rollback" "apply failed and was rolled back — ${apply_err:-see $LOG}"
  else
    write_status failed "rollback" "apply failed AND rollback did not come back healthy — read $LOG, then: ./update.sh rollback, then ./setup.sh (it resumes in order and starts the API)"
  fi
  exit 1
fi

if (( rc == 3 )); then
  # A deliberate pause AFTER the merge — the operator's move. Same state the
  # fetch/llm pauses always used ("failed" + "operator-action", which the
  # dialog renders as needing the operator): no new state, so every cockpit
  # that reads status.json already understands it.
  pause_line="$(last_protocol_line)"
  if [[ "$pause_line" == "USERACTION ledger-adopt: "* ]]; then
    # The PRE-LEDGER install (update.sh's ledger_adoption_gate). NOT rolled
    # back — the tree it would restore can never write a ledger, so it could
    # never update — and NOT restarted: `./setup.sh boot` on the merged tree is
    # refused until the ledger is built, which is the sentence's whole point.
    # ./setup.sh's own boot phase brings the API back.
    write_status failed "operator-action" "${pause_line#USERACTION ledger-adopt: } — the API stays stopped until then; in a terminal: cd deploy/single && ./setup.sh (its boot phase starts the API again)"
    exit 3
  fi
  # Bring the API back up so the cockpit showing this status is reachable.
  restart_api || true
  write_status failed "operator-action" "${pause_line:-the update paused for your action} — finish in a terminal: cd deploy/single && ./update.sh apply, then ./setup.sh (it resumes at boot, which starts the API)"
  exit 3
fi

write_status running "restart"
if restart_api; then
  write_status success "done"
  echo "== update-run finished: healthy on v$(current_version) =="
else
  write_status failed "restart" "the updated API never answered /health — read the uvicorn.log beside $LOG's directory (./setup.sh report prints the state dir); roll back with: ./update.sh rollback, then ./setup.sh (it resumes in order and starts the API)"
  exit 1
fi
