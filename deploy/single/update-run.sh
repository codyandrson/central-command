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
#   copies it to .update/run.sh first and spawns the copy.
#
#   Failure contract: apply exit 1 -> automatic ./update.sh rollback (state
#   "rolled_back"), matching the k3s helper. Apply exit 3 (a fetch/llm pause
#   — the operator's move) -> restart the OLD-or-merged tree so the cockpit
#   is reachable again, state "failed" with the pause's USERACTION text and
#   the CLI command that finishes the job.
# ============================================================================
set -uo pipefail

TARGET="${1:?usage: update-run.sh <target-version> <deploy-single-dir>}"
SINGLE="${2:?usage: update-run.sh <target-version> <deploy-single-dir>}"
UPD="$SINGLE/.update"
STATUS="$UPD/status.json"
LOG="$UPD/apply.log"
REPO_ROOT="$(cd "$SINGLE/../.." && pwd)"

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
CC_UPDATE_DRIVEN=1 "$SINGLE/update.sh" apply
rc=$?

if (( rc == 1 )); then
  apply_err="$(last_protocol_line)"
  write_status running "rollback"
  if CC_UPDATE_DRIVEN=1 "$SINGLE/update.sh" rollback && restart_api; then
    write_status rolled_back "rollback" "apply failed and was rolled back — ${apply_err:-see deploy/single/.update/apply.log}"
  else
    write_status failed "rollback" "apply failed AND rollback did not come back healthy — read deploy/single/.update/apply.log, then: ./update.sh rollback && ./setup.sh boot"
  fi
  exit 1
fi

if (( rc == 3 )); then
  # A deliberate pause (fetch seam, LiteLLM catalog) — the operator's move.
  # Bring the API back up so the cockpit showing this status is reachable.
  pause_line="$(last_protocol_line)"
  restart_api || true
  write_status failed "operator-action" "${pause_line:-the update paused for your action} — finish in a terminal: cd deploy/single && ./update.sh apply, then ./setup.sh boot"
  exit 3
fi

write_status running "restart"
if restart_api; then
  write_status success "done"
  echo "== update-run finished: healthy on v$(current_version) =="
else
  write_status failed "restart" "the updated API never answered /health — read deploy/single/uvicorn.log; roll back with: ./update.sh rollback && ./setup.sh boot"
  exit 1
fi
