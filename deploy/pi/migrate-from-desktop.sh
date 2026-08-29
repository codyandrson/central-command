#!/usr/bin/env bash
# ============================================================================
# Move Central Command's DATA from the desktop homelab to the Raspberry Pi.
#
#   Run this ON THE DESKTOP (it needs podman there and ssh to the Pi). It is
#   idempotent and designed to run TWICE:
#
#     1. once while the desktop is still live, to populate the Pi for testing
#        (pass --allow-live: the copy is a point-in-time snapshot and will go
#        stale the moment the desktop dispatches more work);
#     2. once at CUTOVER with the desktop services stopped, so the Pi carries
#        the authoritative final state.
#
#   Four stores move:
#     central_command  the spine        pg_dump  -> cc-postgres on the Pi
#     litellm      keys + spend     pg_dump  -> cc-litellm-db  (needs the same
#                                               LITELLM_SALT_KEY, or the stored
#                                               virtual keys stop decrypting)
#     n8n          workflows+creds  pg_dump  -> cc-n8n-db      (needs the same
#                                               N8N_ENCRYPTION_KEY, or the Gmail
#                                               OAuth credential is unreadable)
#     neo4j        the graph        offline dump -> cc-neo4j
#
#   Neo4j Community has NO online backup, so the graph dump is a
#   stop -> dump -> start cycle on BOTH hosts. Graphiti is stopped for the same
#   window on purpose: with neo4j down it still ACKs add_memory {submitted:true}
#   and the queued episode dies async and silently — stopped, callers get a
#   loud transport error instead. (Same reasoning as the homelab's
#   scripts/backup-db.sh, which this borrows its incantations from.)
# ============================================================================
set -euo pipefail

PI_HOST="${PI_HOST:-codyslab@100.74.236.97}"
PI_DIR="${PI_DIR:-/home/codyslab/central-command}"
COMPOSE="docker compose -f ${PI_DIR}/deploy/pi/docker-compose.yml"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ALLOW_LIVE=0
[[ "${1:-}" == "--allow-live" ]] && ALLOW_LIVE=1

# --- Refuse to produce a silently-stale "final" copy ------------------------
# The desktop's dispatcher, feed poller, heartbeat and auditor all run live. If
# they are still running, anything dumped here is out of date before it lands.
if systemctl --user is-active --quiet cc-uvicorn 2>/dev/null; then
  if [[ "$ALLOW_LIVE" -eq 0 ]]; then
    echo "REFUSING: cc-uvicorn is still running on this desktop." >&2
    echo "  For a TEST copy:  $0 --allow-live" >&2
    echo "  For the CUTOVER:  systemctl --user stop cc-uvicorn cc-nerve   # then re-run" >&2
    exit 1
  fi
  echo "!! cc-uvicorn is LIVE — this is a point-in-time snapshot, not a cutover copy."
fi

say() { printf '\n[migrate] %s\n' "$*"; }

# --- 1. Postgres dumps ------------------------------------------------------
# --clean --if-exists so a re-run overwrites cleanly; the Pi's cc-postgres has
# already initialised itself from schema.sql and would otherwise collide.
# --no-owner --no-privileges because roles differ across the two deployments
# (the desktop's n8n DB lives in a shared cluster owned by `postgres`).
say "dumping the spine (central_command)…"
podman exec cc-postgres pg_dump -U central_command -d central_command \
  --clean --if-exists --no-owner --no-privileges | gzip > "$WORK/central_command.sql.gz"

say "dumping litellm (virtual keys, spend, models)…"
podman exec litellm-db-1 pg_dump -U llmproxy -d litellm \
  --clean --if-exists --no-owner --no-privileges | gzip > "$WORK/litellm.sql.gz"

say "dumping n8n (workflows + the encrypted Gmail credential)…"
podman exec nat-postgres pg_dump -U postgres -d n8n \
  --clean --if-exists --no-owner --no-privileges | gzip > "$WORK/n8n.sql.gz"

# --- 2. Neo4j offline dump --------------------------------------------------
say "dumping the graph (stops nat-graphiti + nat-neo4j briefly)…"
neo4j_was_running="$(podman inspect -f '{{.State.Running}}' nat-neo4j 2>/dev/null || echo false)"
graphiti_was_running="$(podman inspect -f '{{.State.Running}}' nat-graphiti 2>/dev/null || echo false)"
restore_graph_stack() {
  [[ "$neo4j_was_running" == "true" ]] && podman start nat-neo4j >/dev/null 2>&1 || true
  [[ "$graphiti_was_running" == "true" ]] && podman start nat-graphiti >/dev/null 2>&1 || true
}
trap 'restore_graph_stack; rm -rf "$WORK"' EXIT
[[ "$graphiti_was_running" == "true" ]] && podman stop -t 30 nat-graphiti >/dev/null
[[ "$neo4j_was_running" == "true" ]] && podman stop -t 120 nat-neo4j >/dev/null

NEO4J_VOLUME="${NEO4J_VOLUME:-n8n-agentic-team_neo4j_data}"
podman run --rm -v "${NEO4J_VOLUME}:/data" --entrypoint neo4j-admin \
  docker.io/library/neo4j:5.26.2 database dump neo4j --to-stdout \
  | gzip > "$WORK/neo4j.dump.gz"
[[ "$(stat -c%s "$WORK/neo4j.dump.gz")" -gt 1024 ]] || { echo "neo4j dump looks empty — aborting" >&2; exit 1; }
gzip -t "$WORK/neo4j.dump.gz"
restore_graph_stack
trap 'rm -rf "$WORK"' EXIT
say "graph dumped, desktop graph stack restarted"

ls -lh "$WORK"

# --- 3. Ship it -------------------------------------------------------------
say "transferring to ${PI_HOST}…"
ssh "$PI_HOST" "mkdir -p ${PI_DIR}/deploy/pi/migration && chmod 700 ${PI_DIR}/deploy/pi/migration"
scp -q "$WORK"/*.gz "${PI_HOST}:${PI_DIR}/deploy/pi/migration/"

# --- 4. Restore on the Pi ---------------------------------------------------
# Each consumer is stopped around its own restore: a running litellm re-runs
# prisma migrations, and a running n8n holds workflow rows open.
say "restoring on the Pi…"
ssh "$PI_HOST" "set -euo pipefail
cd ${PI_DIR}
M=${PI_DIR}/deploy/pi/migration

echo '  spine → cc-postgres'
${COMPOSE} up -d postgres >/dev/null
until docker exec cc-postgres pg_isready -U central_command >/dev/null 2>&1; do sleep 2; done
gunzip -c \$M/central_command.sql.gz | docker exec -i cc-postgres psql -q -U central_command -d central_command >/dev/null

echo '  litellm → cc-litellm-db'
${COMPOSE} stop litellm >/dev/null 2>&1 || true
${COMPOSE} up -d litellm-db >/dev/null
until docker exec cc-litellm-db pg_isready -U llmproxy -d litellm >/dev/null 2>&1; do sleep 2; done
gunzip -c \$M/litellm.sql.gz | docker exec -i cc-litellm-db psql -q -U llmproxy -d litellm >/dev/null

echo '  n8n → cc-n8n-db'
${COMPOSE} stop n8n >/dev/null 2>&1 || true
${COMPOSE} up -d n8n-db >/dev/null
until docker exec cc-n8n-db pg_isready -U n8n >/dev/null 2>&1; do sleep 2; done
gunzip -c \$M/n8n.sql.gz | docker exec -i cc-n8n-db psql -q -U n8n -d n8n >/dev/null

echo '  graph → cc-neo4j (offline load)'
${COMPOSE} up -d neo4j >/dev/null            # first run initialises the store
sleep 5
${COMPOSE} stop graphiti neo4j >/dev/null 2>&1 || true
gunzip -c \$M/neo4j.dump.gz | docker run --rm -i -v cc_neo4j_data:/data \\
  --entrypoint neo4j-admin docker.io/library/neo4j:5.26.2 \\
  database load neo4j --from-stdin --overwrite-destination=true

echo '  bringing the full stack back up'
${COMPOSE} up -d >/dev/null
"

say "done (${STAMP}). Verify with deploy/pi/verify.sh on the Pi."
