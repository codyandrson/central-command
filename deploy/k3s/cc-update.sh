#!/usr/bin/env bash
# ============================================================================
# cc-update.sh — the EXTERNAL updater for the k3s deployment (one-click apply).
#
#   Design (2026-08-28, from the two-survey research pass; see the
#   setup-update contract spec): the cockpit NEVER applies its own update.
#   cc-nerve's only privileged act is writing /run/cc-update/trigger;
#   cc-update.path starts cc-update.service (root, Type=oneshot), whose
#   ExecStartPre CONSUMES the trigger (mv -> request.json) so the
#   recheck-on-exit loop in systemd.path(5) cannot fire, and this script does
#   everything else. Prior art: Portainer's detached updater + persisted
#   schedule record; greenboot's the-updater-owns-health-and-rollback;
#   PackageKit's never-mutate-the-live-process.
#
#   Pipeline (no-op exits BEFORE any mutation):
#     resolve target (highest semver tag on origin, fetched) -> version gate
#     (no downgrade, min_upgrade_from honored) -> checkpoint tag -> spine DB
#     dump -> stop cc-uvicorn + cc-sandbox-runner (cc-nerve stays up serving
#     progress; restarted last) -> merge the tag -> schema (additive-only,
#     idempotent) -> pip + cockpit rebuild AS codyslab (root-owned files in
#     the checkout break every later build) -> start services -> health poll
#     -> on failure: git reset --hard to the checkpoint, rebuild, restart,
#     re-poll — the rolled_back state is honest about what schema kept
#     (additive-only means old code runs fine against it).
#
#   Status: /var/lib/cc-update/status.json, written ATOMICALLY (tmp + mv) at
#   every phase change — the UI re-reads it after its own restart; the full
#   detail channel is `journalctl -u cc-update`. Files live OUTSIDE the repo
#   tree on purpose: rollback's reset --hard would clobber anything inside.
# ============================================================================
set -uo pipefail

REPO=/home/codyslab/Central Command
RUNAS=(runuser -u codyslab --)
RUNDIR=/run/cc-update
STATEDIR=/var/lib/cc-update
STATUS="$STATEDIR/status.json"
REQUEST="$RUNDIR/request.json"
BACKUPS=/home/codyslab/cc-backups
K=(k3s kubectl -n central-command)
STARTED_AT="$(date -u +%FT%TZ)"

note() { printf '%s\n' "$*"; }   # journald captures stdout — that IS the log

# jq-free JSON writer: every value passes through python's json.dumps, so a
# git error message with quotes in it cannot corrupt the file.
write_status() { # write_status <state> <phase> [error]
  local tmp
  tmp="$(mktemp "$STATEDIR/.status.XXXXXX")" || return 0
  python3 - "$1" "$2" "${3:-}" "${CUR_VERSION:-}" "${TARGET_VERSION:-}" "$STARTED_AT" >"$tmp" <<'PY'
import json, sys
from datetime import datetime, timezone
state, phase, error, cur, target, started = sys.argv[1:7]
out = {"state": state, "phase": phase, "current": cur, "target": target,
       "started_at": started, "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
if error: out["error"] = error
if state in ("success", "failed", "rolled_back"): out["finished_at"] = out["updated_at"]
print(json.dumps(out))
PY
  chmod 644 "$tmp" && mv -f "$tmp" "$STATUS"
}

phase() { note "== $1"; write_status running "$1"; }

start_services()  { systemctl start cc-uvicorn cc-sandbox-runner; systemctl restart cc-nerve; }

healthy() { # poll the API up to 2 minutes; it is the load-bearing process
  local i
  for i in $(seq 1 30); do
    curl -fsS -m 5 http://127.0.0.1:8080/health >/dev/null 2>&1 && return 0
    sleep 4
  done
  return 1
}

rebuild() { # as codyslab, never root
  "${RUNAS[@]}" env VIRTUAL_ENV="$REPO/.venv" PATH="/home/codyslab/.local/bin:$PATH" \
    bash -c "cd '$REPO' && uv pip install -e '.[dev,runtime]'" || return 1
  "${RUNAS[@]}" bash -c "cd '$REPO/web' && npm install && npm run build" || return 1
}

die() { # <phase> <message> — best-effort recovery: services back up, honest status
  note "FAILED at $1: $2"
  write_status failed "$1" "$2"
  start_services || true
  exit 1
}

# A SIGTERM (TimeoutStartSec) must not leave a silent corpse.
trap 'write_status failed "${PHASE_NOW:-unknown}" "terminated (timeout or stop)"; exit 143' TERM

main() {
  mkdir -p "$STATEDIR"

  PHASE_NOW=resolve; phase "resolving target version"
  local requested=""
  [[ -f "$REQUEST" ]] && requested="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("target",""))' "$REQUEST" 2>/dev/null)"

  CUR_VERSION="$(sed -n 's/^version=//p' "$REPO/VERSION" | head -1)"
  [[ -n "$CUR_VERSION" ]] || die resolve "no version= in $REPO/VERSION"

  # Dirty tracked files would be clobbered by rollback's reset --hard — refuse
  # before touching anything (same rule as update.sh apply).
  if [[ -n "$("${RUNAS[@]}" git -C "$REPO" status --porcelain --untracked-files=no)" ]]; then
    die resolve "uncommitted changes to tracked files in $REPO — commit them first"
  fi

  "${RUNAS[@]}" git -C "$REPO" fetch origin --tags --quiet || die resolve "git fetch origin failed"
  TARGET_VERSION="${requested:-$("${RUNAS[@]}" git -C "$REPO" tag -l 'v[0-9]*' | sed 's/^v//' | sort -V | tail -1)}"
  TARGET_VERSION="${TARGET_VERSION#v}"
  [[ -n "$TARGET_VERSION" ]] || die resolve "no semver tags found on origin"

  if [[ "$TARGET_VERSION" == "$CUR_VERSION" ]]; then
    note "already at $CUR_VERSION — nothing to do"
    write_status success up-to-date
    exit 0
  fi
  # Version gate: no downgrade, and honor the target's min_upgrade_from.
  if [[ "$(printf '%s\n%s\n' "$TARGET_VERSION" "$CUR_VERSION" | sort -V | head -1)" == "$TARGET_VERSION" ]]; then
    die resolve "refusing a downgrade: installed $CUR_VERSION, target $TARGET_VERSION"
  fi
  local minfrom
  minfrom="$("${RUNAS[@]}" git -C "$REPO" show "v$TARGET_VERSION:VERSION" 2>/dev/null | sed -n 's/^min_upgrade_from=//p' | head -1)"
  if [[ -n "$minfrom" && "$(printf '%s\n%s\n' "$CUR_VERSION" "$minfrom" | sort -V | head -1)" == "$CUR_VERSION" && "$CUR_VERSION" != "$minfrom" ]]; then
    die resolve "v$TARGET_VERSION requires upgrading from >= $minfrom (installed $CUR_VERSION) — apply the intermediate release first"
  fi

  PHASE_NOW=checkpoint; phase "tagging rollback checkpoint"
  local ckpt="pre-update-$(date -u +%Y%m%dT%H%M%SZ)"
  "${RUNAS[@]}" git -C "$REPO" config user.name  >/dev/null 2>&1 || "${RUNAS[@]}" git -C "$REPO" config user.name "Central Command"
  "${RUNAS[@]}" git -C "$REPO" config user.email >/dev/null 2>&1 || "${RUNAS[@]}" git -C "$REPO" config user.email "update@localhost"
  "${RUNAS[@]}" git -C "$REPO" tag "$ckpt" || die checkpoint "could not tag"

  PHASE_NOW=db-backup; phase "dumping the spine database"
  mkdir -p "$BACKUPS" && chmod 700 "$BACKUPS"
  local dump="$BACKUPS/central_command_pre-update_$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
  if ! "${K[@]}" exec deploy/cc-postgres -- pg_dump -U central_command -d central_command | gzip >"$dump" || [[ ! -s "$dump" ]]; then
    rm -f "$dump"; die db-backup "pg_dump via kubectl failed — refusing to update without a spine backup"
  fi
  chown codyslab:codyslab "$dump" 2>/dev/null; chmod 600 "$dump"

  PHASE_NOW=stop; phase "stopping cc-uvicorn and cc-sandbox-runner"
  systemctl stop cc-uvicorn cc-sandbox-runner || die stop "systemctl stop failed"

  PHASE_NOW=merge; phase "merging v$TARGET_VERSION"
  if ! "${RUNAS[@]}" git -C "$REPO" merge --no-edit "v$TARGET_VERSION"; then
    "${RUNAS[@]}" git -C "$REPO" merge --abort 2>/dev/null
    die merge "merge of v$TARGET_VERSION conflicts with local commits — resolve by hand (services restarted on the OLD version)"
  fi

  PHASE_NOW=schema; phase "applying schema (additive-only, idempotent)"
  "${K[@]}" exec -i deploy/cc-postgres -- psql -q -U central_command -d central_command -v ON_ERROR_STOP=1 \
    <"$REPO/central_command/db/schema.sql" || die schema "schema.sql failed — old code is compatible with a partial additive apply, services restarted"

  PHASE_NOW=rebuild; phase "rebuilding venv and cockpit"
  rebuild || die rebuild "pip/npm build failed"

  PHASE_NOW=restart; phase "starting services"
  start_services || die restart "systemctl start failed"

  PHASE_NOW=health; phase "health check"
  if healthy; then
    note "updated $CUR_VERSION -> $TARGET_VERSION"
    write_status success done
    exit 0
  fi

  # ── rollback: the updater owns this (greenboot's shape) ────────────────────
  PHASE_NOW=rollback; phase "unhealthy after update — rolling back to $ckpt"
  systemctl stop cc-uvicorn cc-sandbox-runner || true
  "${RUNAS[@]}" git -C "$REPO" reset --hard "$ckpt" || die rollback "reset --hard $ckpt failed — MANUAL intervention needed"
  rebuild || die rollback "rebuild of the rolled-back tree failed — MANUAL intervention needed"
  start_services || true
  if healthy; then
    write_status rolled_back "rolled back to $CUR_VERSION" "v$TARGET_VERSION failed its post-update health check; schema changes (additive-only) were kept"
    exit 1
  fi
  die rollback "still unhealthy after rollback — check journalctl -u cc-uvicorn"
}

main "$@"
