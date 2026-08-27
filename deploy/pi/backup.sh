#!/usr/bin/env bash
# ============================================================================
# Central Command disaster-recovery backup — the Pi holds the ONLY live copy.
#
#   Since the 2026-07-26 migration this host carries all four stores, and the
#   desktop's nightly cron backs up ITS n8n/Neo4j — a different stack that
#   Central Command no longer uses. Nothing was covering this data. This does.
#
#     central_command  the spine: ledger, proposals, event log, charters, roster
#     litellm      virtual keys + spend + DB-registered models
#     n8n          workflows + the ENCRYPTED Gmail OAuth credential
#     neo4j        the knowledge graph
#
#   Two keys are captured ALONGSIDE the dumps, because without them the dumps
#   are undecryptable ciphertext, not a backup:
#     N8N_ENCRYPTION_KEY  decrypts the Gmail credential
#     LITELLM_SALT_KEY    decrypts the stored virtual keys
#   They are written 0600 into the backup directory, which is OUTSIDE the git
#   repo on purpose — dumps and key material must never sit in a tree that
#   gets pushed to GitHub.
#
#   Neo4j Community has no online backup, so the graph is a stop -> dump ->
#   start cycle. Graphiti is stopped for the SAME window deliberately: with
#   neo4j down it still ACKs add_memory {submitted:true} and the queued episode
#   dies async and silently. Stopped, an in-window caller gets a loud transport
#   error instead. (The lesson is the homelab's scripts/backup-db.sh 2.1
#   review; it applies here unchanged.)
#
#   Run nightly from cc-backup.timer. Exits NON-ZERO if any store failed, so a
#   silent failure streak is impossible to mistake for success.
#
#   Restore: see the RESTORE section at the bottom of this file.
# ============================================================================
set -uo pipefail

REPO="${REPO:-/home/codyslab/Central Command}"
OUT="${CC_BACKUP_DIR:-/home/codyslab/cc-backups}"
RETAIN_DAYS="${CC_BACKUP_RETAIN_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
COMPOSE="docker compose -f ${REPO}/deploy/pi/docker-compose.yml"

# 077 from the very top: EVERY artifact here is sensitive, not just the key
# file. The n8n dump carries the encrypted Gmail credential and the spine dump
# carries real mail content. (First run wrote the dumps 0644 because the umask
# was set later, next to the keys — fixed.)
umask 077
mkdir -p "$OUT"
chmod 700 "$OUT"

# Container env (NEO4J_PASSWORD, N8N_ENCRYPTION_KEY, LITELLM_SALT_KEY). Values
# are used and written to 0600 files; never echoed.
set -a
# shellcheck disable=SC1091
[[ -f "${REPO}/deploy/pi/.env" ]] && . "${REPO}/deploy/pi/.env"
set +a

# One backup at a time — an overlapping run would fight over the neo4j stop.
exec 9>"${OUT}/.backup.lock"
if ! flock -n 9; then
  echo "[backup] another backup holds the lock — this run is skipped" >&2
  exit 1
fi

failed=()
say() { printf '[backup] %s\n' "$*"; }

# --- Postgres stores ---------------------------------------------------------
# pg_dump runs INSIDE the container, so the host needs no postgres client.
# A dump is only accepted when it is gzip-valid AND carries pg_dump's own
# completion marker — a truncated dump that gunzips cleanly is the failure mode
# that makes people think they have backups.
dump_pg() {
  local label="$1" container="$2" user="$3" db="$4"
  local file="${OUT}/${label}_${STAMP}.sql.gz"
  say "dumping ${label}…"
  if ! docker exec "$container" pg_dump -U "$user" -d "$db" \
        --clean --if-exists --no-owner --no-privileges 2>/dev/null | gzip > "$file"; then
    say "ERROR: ${label} pg_dump failed"; rm -f "$file"; failed+=("$label"); return 1
  fi
  if ! gzip -t "$file" 2>/dev/null; then
    say "ERROR: ${label} dump is not valid gzip"; rm -f "$file"; failed+=("$label"); return 1
  fi
  if ! gunzip -c "$file" | tail -5 | grep -q 'PostgreSQL database dump complete'; then
    say "ERROR: ${label} dump is TRUNCATED (no completion marker)"; rm -f "$file"; failed+=("$label"); return 1
  fi
  say "  ok → $(basename "$file") ($(du -h "$file" | cut -f1))"
}

dump_pg central_command cc-postgres    central_command central_command
dump_pg litellm     cc-litellm-db  llmproxy    litellm
dump_pg n8n         cc-n8n-db      "${N8N_DB_USER:-n8n}" "${N8N_DB_NAME:-n8n}"

# --- The keys that make those dumps restorable -------------------------------
keyfile="${OUT}/keys_${STAMP}.env"
{
  echo "# Captured with the ${STAMP} dumps. WITHOUT THESE THE DUMPS ARE USELESS."
  echo "N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY:-}"
  echo "LITELLM_SALT_KEY=${LITELLM_SALT_KEY:-}"
  echo "NEO4J_PASSWORD=${NEO4J_PASSWORD:-}"
} > "$keyfile"
chmod 600 "$keyfile"
if [[ -z "${N8N_ENCRYPTION_KEY:-}" || -z "${LITELLM_SALT_KEY:-}" ]]; then
  say "ERROR: a decryption key is missing from deploy/pi/.env — DR would be impossible"
  failed+=("keys")
else
  say "  ok → $(basename "$keyfile") (0600)"
fi

# --- Neo4j: offline dump -----------------------------------------------------
neo4j_ok=0
neo4j_was_running="$(docker inspect -f '{{.State.Running}}' cc-neo4j 2>/dev/null || echo false)"
graphiti_was_running="$(docker inspect -f '{{.State.Running}}' cc-graphiti 2>/dev/null || echo false)"

restore_graph_stack() {
  [[ "$graphiti_was_running" == "true" ]] && docker start cc-graphiti >/dev/null 2>&1 9>&-
  [[ "$neo4j_was_running" == "true" ]] && docker start cc-neo4j >/dev/null 2>&1 9>&-
  return 0
}
# Whatever happens next — error, SIGTERM from a timer timeout, Ctrl-C — the
# graph stack comes back. Leaving neo4j stopped would silently halt the graph.
trap restore_graph_stack EXIT
trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM

say "dumping neo4j (brief stop of cc-graphiti + cc-neo4j)…"
[[ "$graphiti_was_running" == "true" ]] && docker stop -t 30 cc-graphiti >/dev/null 9>&-
[[ "$neo4j_was_running" == "true" ]] && docker stop -t 120 cc-neo4j >/dev/null 9>&-

neo4j_file="${OUT}/neo4j_${STAMP}.dump.gz"
if docker run --rm -v cc_neo4j_data:/data --entrypoint neo4j-admin \
     docker.io/library/neo4j:5.26.2 database dump neo4j --to-stdout 2>/dev/null 9>&- \
     | gzip > "$neo4j_file" \
   && [[ "$(stat -c%s "$neo4j_file" 2>/dev/null || echo 0)" -gt 1024 ]] \
   && gzip -t "$neo4j_file" 2>/dev/null; then
  say "  ok → $(basename "$neo4j_file") ($(du -h "$neo4j_file" | cut -f1))"
  neo4j_ok=1
else
  say "ERROR: neo4j dump failed — the graph is NOT backed up tonight"
  rm -f "$neo4j_file"
  failed+=("neo4j")
fi

restore_graph_stack
trap - EXIT HUP INT TERM

# Wait for the graph to actually come back rather than assuming it did.
for _ in $(seq 1 75); do
  state="$(docker inspect -f '{{.State.Health.Status}}' cc-neo4j 2>/dev/null || echo unknown)"
  [[ "$state" == "healthy" ]] && break
  sleep 2
done
say "neo4j back up (health: ${state:-unknown})"
[[ "${state:-}" == "healthy" ]] || say "WARNING: neo4j is not healthy after the restart — check it"

# --- Retention ---------------------------------------------------------------
# Prune ONLY what succeeded tonight. A failure streak longer than RETAIN_DAYS
# must never age out the last GOOD copy — that turns a bad week into data loss.
prune() { find "$OUT" -maxdepth 1 -type f -name "$1" -mtime "+${RETAIN_DAYS}" -delete 2>/dev/null || true; }
# `failed` is declared above, so ${failed[@]} is safe under `set -u` on bash 5.x
# and expands to nothing when empty. Do NOT write ${#failed[@]:-0} — that is an
# invalid substitution which bash reports and then evaluates as false, quietly
# turning "a store failed" into exit 0 (caught on the first live run).
in_failed() { local n; for n in "${failed[@]}"; do [[ "$n" == "$1" ]] && return 0; done; return 1; }

in_failed central_command || prune 'central_command_*.sql.gz'
in_failed litellm     || prune 'litellm_*.sql.gz'
in_failed n8n         || prune 'n8n_*.sql.gz'
in_failed keys        || prune 'keys_*.env'
[[ "$neo4j_ok" == "1" ]] && prune 'neo4j_*.dump.gz'

# --- Verdict -----------------------------------------------------------------
if [[ ${#failed[@]} -gt 0 ]]; then
  say "done WITH ERRORS → ${OUT}/ — FAILED: ${failed[*]}"
  exit 1
fi
say "done → ${OUT}/ (retain ${RETAIN_DAYS}d, $(du -sh "$OUT" | cut -f1) total)"

# ============================================================================
# RESTORE (read this BEFORE you need it)
#
#   Postgres — stop the consumer first (a running litellm re-runs prisma
#   migrations; a running n8n holds workflow rows open):
#     docker compose -f deploy/pi/docker-compose.yml stop litellm
#     gunzip -c litellm_<stamp>.sql.gz | docker exec -i cc-litellm-db psql -U llmproxy -d litellm
#     docker compose -f deploy/pi/docker-compose.yml up -d litellm
#   The spine has no such consumer beyond the API:
#     sudo systemctl stop cc-uvicorn
#     gunzip -c central_command_<stamp>.sql.gz | docker exec -i cc-postgres psql -U central_command -d central_command
#     sudo systemctl start cc-uvicorn
#
#   Neo4j — offline load, overwriting the store:
#     docker compose -f deploy/pi/docker-compose.yml stop graphiti neo4j
#     gunzip -c neo4j_<stamp>.dump.gz | docker run --rm -i -v cc_neo4j_data:/data \
#       --entrypoint neo4j-admin docker.io/library/neo4j:5.26.2 \
#       database load neo4j --from-stdin --overwrite-destination=true
#     docker compose -f deploy/pi/docker-compose.yml up -d
#
#   Keys — if restoring onto a NEW host, put keys_<stamp>.env's values into
#   deploy/pi/.env BEFORE starting litellm or n8n. Same-host restores already
#   have them.
# ============================================================================
