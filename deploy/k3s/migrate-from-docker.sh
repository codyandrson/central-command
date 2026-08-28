#!/usr/bin/env bash
# ============================================================================
# ONE-SHOT cutover: deploy/pi/docker-compose.yml  ->  k3s.
#
#   Run ON THE PI. This is the destructive step — it stops the Docker stack and
#   the API. Everything before it (manifests, secrets, images) is reversible;
#   this is not, short of restoring from deploy/pi/backup.sh.
#
#   ORDER MATTERS, and the order is: dump everything FIRST, with the sources
#   still running and verified, and only then tear anything down. A migration
#   that stops the source before proving the dump is good has no way back.
#
#   WHAT CARRIES OVER UNCHANGED (make-secrets.sh already copied them):
#     LITELLM_SALT_KEY    or every stored LiteLLM virtual key fails to decrypt
#     N8N_ENCRYPTION_KEY  or the Gmail OAuth credential is present but unusable
#
#   Neo4j moves ARCHITECTURE as well as host (arm64 Pi -> amd64 chromebox), so
#   it is a logical dump/load via neo4j-admin, never a copy of the store files.
#   A raw store directory is not portable across arch and would be silently
#   corrupt. The Postgres dumps are logical for the same reason.
#
#   Idempotent-ish: safe to re-run after a failure BEFORE the docker stack is
#   torn down (step 5). After that, re-running re-restores from the same dumps.
#
#   Usage:  ./deploy/k3s/migrate-from-docker.sh          # full cutover
#           DUMP_ONLY=1 ./deploy/k3s/migrate-from-docker.sh   # rehearse: dump + verify, change nothing
# ============================================================================
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NS=central_command
COMPOSE="docker compose -f ${REPO}/deploy/pi/docker-compose.yml"
KUBECTL=(sudo k3s kubectl)
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
# OUTSIDE the repo — these dumps carry real mail, credentials and graph content.
WORK="${CC_MIGRATE_DIR:-/home/codyslab/cc-migrate}/${STAMP}"
CHROMEBOX="${CC_COMPUTE_SSH:-chromebox_admin@100.113.118.28}"   # compute-node ssh target; override in deploy/pi/.env
DUMP_ONLY="${DUMP_ONLY:-0}"

umask 077
mkdir -p "$WORK"; chmod 700 "$(dirname "$WORK")" "$WORK"

say()  { printf '\n[migrate] %s\n' "$*"; }
step() { printf '   %s\n' "$*"; }
die()  { printf '\n[migrate] FATAL: %s\n' "$*" >&2; exit 1; }

set -a; [[ -f "${REPO}/deploy/pi/.env" ]] && . "${REPO}/deploy/pi/.env"; set +a

# ── 0. Preflight ────────────────────────────────────────────────────────────
say "0/7 preflight"
for c in cc-postgres cc-litellm-db cc-n8n-db cc-neo4j; do
  [[ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" == "true" ]] \
    || die "$c is not running — dump the sources while they are healthy, not after"
done
step "docker stack running"
"${KUBECTL[@]}" -n "$NS" get secret cc-litellm >/dev/null 2>&1 \
  || die "secrets missing — run ./deploy/k3s/make-secrets.sh first"
step "k3s secrets present"
# The floating image must exist on BOTH nodes, under the EXACT reference the
# kubelet resolves `cc-graphiti:1.0.2-anthropic` to, or the pod ImagePullBackOffs
# on whichever node lacks it.
#
# Capture the listing into a variable before grepping. `ctr images ls | grep -q`
# looks right and is wrong under `set -o pipefail`: grep -q exits at the first
# match, ctr takes SIGPIPE, and the pipeline reports failure on SUCCESS. That
# inverted check failed this script's own preflight on 2026-07-31 with the image
# demonstrably present.
GRAPHITI_REF="docker.io/library/cc-graphiti:1.0.2-anthropic"
pi_imgs="$(sudo k3s ctr -n k8s.io images ls -q)"
grep -qx "$GRAPHITI_REF" <<<"$pi_imgs" \
  || die "$GRAPHITI_REF missing on the Pi — run build-graphiti-image.sh"
cb_imgs="$(ssh "$CHROMEBOX" 'sudo k3s ctr -n k8s.io images ls -q')"
grep -qx "$GRAPHITI_REF" <<<"$cb_imgs" \
  || die "$GRAPHITI_REF missing on the chromebox — run build-graphiti-image.sh"
step "cc-graphiti present on both nodes as $GRAPHITI_REF"

# ── 1. Dump Postgres (sources still live) ───────────────────────────────────
# Same validity checks as backup.sh: gzip-valid AND carrying pg_dump's own
# completion marker. A truncated dump that gunzips cleanly is THE failure mode
# that makes people think they migrated successfully.
say "1/7 dumping Postgres stores"
dump_pg() {
  local label="$1" container="$2" user="$3" db="$4"
  local f="${WORK}/${label}.sql.gz"
  docker exec "$container" pg_dump -U "$user" -d "$db" \
      --clean --if-exists --no-owner --no-privileges 2>/dev/null | gzip > "$f" \
    || die "$label pg_dump failed"
  gzip -t "$f" 2>/dev/null || die "$label dump is not valid gzip"
  gunzip -c "$f" | tail -5 | grep -q 'PostgreSQL database dump complete' \
    || die "$label dump is TRUNCATED (no completion marker)"
  step "$label -> $(du -h "$f" | cut -f1)"
}
dump_pg central_command cc-postgres   central_command central_command
dump_pg litellm     cc-litellm-db llmproxy    litellm
dump_pg n8n         cc-n8n-db     "${N8N_DB_USER:-n8n}" "${N8N_DB_NAME:-n8n}"

# Row counts to compare against after the restore. Assert on what we moved.
say "1b/7 recording source row counts"
src_counts() {  # src_counts <container> <user> <db>
  docker exec "$1" psql -U "$2" -d "$3" -tAc \
    "select table_name||'='||(xpath('/row/c/text()', query_to_xml(
       'select count(*) as c from '||quote_ident(table_schema)||'.'||quote_ident(table_name),
       false,true,'')))[1]::text::bigint
     from information_schema.tables where table_schema='public' and table_type='BASE TABLE'
     order by table_name;" 2>/dev/null
}
src_counts cc-postgres   central_command central_command > "${WORK}/counts.central_command.src"
src_counts cc-litellm-db llmproxy    litellm     > "${WORK}/counts.litellm.src"
src_counts cc-n8n-db "${N8N_DB_USER:-n8n}" "${N8N_DB_NAME:-n8n}" > "${WORK}/counts.n8n.src"
step "central_command: $(wc -l < "${WORK}/counts.central_command.src") tables"
step "litellm:     $(wc -l < "${WORK}/counts.litellm.src") tables"
step "n8n:         $(wc -l < "${WORK}/counts.n8n.src") tables"

# ── 2. Dump Neo4j (offline; brief graph outage) ─────────────────────────────
# Neo4j Community has no online backup, so this is stop -> dump -> start.
# cc-graphiti is stopped for the SAME window deliberately: with neo4j down it
# still ACKs add_memory {submitted:true} and the queued episode dies async and
# silently. Stopped, an in-window caller gets a loud transport error instead.
say "2/7 dumping Neo4j (brief stop of cc-graphiti + cc-neo4j)"
graph_back() { docker start cc-neo4j >/dev/null 2>&1; docker start cc-graphiti >/dev/null 2>&1; return 0; }
trap graph_back EXIT; trap 'exit 130' INT; trap 'exit 143' TERM
docker stop -t 30  cc-graphiti >/dev/null
docker stop -t 120 cc-neo4j    >/dev/null
NEO_DUMP="${WORK}/neo4j.dump"
docker run --rm -v cc_neo4j_data:/data --entrypoint neo4j-admin \
    docker.io/library/neo4j:5.26.2 database dump neo4j --to-stdout 2>/dev/null > "$NEO_DUMP" \
  || die "neo4j dump failed"
[[ "$(stat -c%s "$NEO_DUMP")" -gt 1024 ]] || die "neo4j dump is implausibly small"
step "neo4j -> $(du -h "$NEO_DUMP" | cut -f1)"
graph_back; trap - EXIT INT TERM

if [[ "$DUMP_ONLY" == "1" ]]; then
  say "DUMP_ONLY: dumps verified in ${WORK}. Nothing was changed. Docker stack still serving."
  exit 0
fi

# ── 3. Bring up the k3s stores (stateful only, so restores land before use) ──
say "3/7 applying k3s manifests"
for f in 00-namespace 20-postgres 30-litellm 40-graph 50-n8n; do
  "${KUBECTL[@]}" apply -f "${REPO}/deploy/k3s/${f}.yaml" >/dev/null || die "apply $f failed"
  step "applied ${f}.yaml"
done
# Hold the CONSUMERS at zero until their data is in place. Starting litellm
# against an empty database lets Prisma create a fresh schema that then collides
# with the restore; starting n8n does the same to its workflow tables.
"${KUBECTL[@]}" -n "$NS" scale deploy/cc-litellm deploy/cc-n8n deploy/cc-graphiti --replicas=0 >/dev/null
step "consumers (litellm, n8n, graphiti) held at 0 replicas"

say "4/7 waiting for the k3s data stores to be ready"
for d in cc-postgres cc-litellm-db cc-n8n-db; do
  "${KUBECTL[@]}" -n "$NS" rollout status deploy/"$d" --timeout=300s >/dev/null \
    || die "$d never became ready"
  step "$d ready"
done

# ── 5. Stop the Docker stack + the API ──────────────────────────────────────
# Only now, with dumps taken and targets up. Postgres 5442 cannot be bound by
# both stacks at once, so the old one must go before the restore.
say "5/7 stopping the Docker stack and the API"
sudo systemctl stop cc-uvicorn cc-nerve 2>/dev/null
step "cc-uvicorn, cc-nerve stopped"
$COMPOSE down >/dev/null 2>&1 || die "compose down failed"
step "docker stack down"
# The Pi-pinned pods hold hostPort 5442/5678; they could not bind while docker
# held them. Restart them now that the ports are free.
"${KUBECTL[@]}" -n "$NS" rollout restart deploy/cc-postgres deploy/cc-n8n-db >/dev/null
"${KUBECTL[@]}" -n "$NS" rollout status deploy/cc-postgres --timeout=300s >/dev/null || die "cc-postgres did not come back on 5442"
step "cc-postgres holding 127.0.0.1:5442"

# ── 6. Restore ──────────────────────────────────────────────────────────────
say "6/7 restoring"
restore_pg() {  # restore_pg <label> <deploy> <user> <db>
  local label="$1" dep="$2" user="$3" db="$4"
  gunzip -c "${WORK}/${label}.sql.gz" \
    | "${KUBECTL[@]}" -n "$NS" exec -i deploy/"$dep" -- psql -U "$user" -d "$db" -v ON_ERROR_STOP=0 >/dev/null 2>&1 \
    || die "$label restore failed"
  step "$label restored"
}
restore_pg central_command cc-postgres   central_command central_command
restore_pg litellm     cc-litellm-db llmproxy    litellm
restore_pg n8n         cc-n8n-db     "${N8N_DB_USER:-n8n}" "${N8N_DB_NAME:-n8n}"

# Neo4j: the PVC lives on the CHROMEBOX and is ReadWriteOnce, so the load runs
# in a dedicated pod while cc-neo4j is at 0 replicas. neo4j-admin will not load
# into a running database.
step "loading neo4j on the chromebox"
"${KUBECTL[@]}" -n "$NS" scale deploy/cc-neo4j --replicas=0 >/dev/null
"${KUBECTL[@]}" -n "$NS" wait --for=delete pod -l app=cc-neo4j --timeout=180s >/dev/null 2>&1
cat <<'LOADER' | "${KUBECTL[@]}" -n "$NS" apply -f - >/dev/null
apiVersion: v1
kind: Pod
metadata: {name: neo4j-loader, namespace: central-command}
spec:
  nodeSelector: {kubernetes.io/hostname: chromebox}
  restartPolicy: Never
  containers:
    - name: loader
      image: docker.io/library/neo4j:5.26.2
      command: ["sleep", "3600"]
      volumeMounts: [{name: data, mountPath: /data}]
  volumes:
    - name: data
      persistentVolumeClaim: {claimName: cc-neo4j-data}
LOADER
"${KUBECTL[@]}" -n "$NS" wait --for=condition=Ready pod/neo4j-loader --timeout=300s >/dev/null \
  || die "neo4j loader pod never became ready"
"${KUBECTL[@]}" -n "$NS" exec -i pod/neo4j-loader -- \
    neo4j-admin database load neo4j --from-stdin --overwrite-destination=true < "$NEO_DUMP" \
  || die "neo4j load failed"
# The loader runs as root; the real neo4j pod runs as uid 7474. Leaving
# root-owned store files behind makes it fail to start with a permissions error
# that reads like corruption.
"${KUBECTL[@]}" -n "$NS" exec pod/neo4j-loader -- chown -R 7474:7474 /data >/dev/null
"${KUBECTL[@]}" -n "$NS" delete pod/neo4j-loader --wait=true >/dev/null
step "neo4j loaded"

# ── 7. Start everything and verify ──────────────────────────────────────────
say "7/7 starting consumers"
"${KUBECTL[@]}" -n "$NS" scale deploy/cc-neo4j deploy/cc-litellm deploy/cc-graphiti deploy/cc-n8n --replicas=1 >/dev/null
for d in cc-neo4j cc-litellm cc-graphiti cc-n8n; do
  "${KUBECTL[@]}" -n "$NS" rollout status deploy/"$d" --timeout=420s >/dev/null \
    && step "$d ready" || say "WARNING: $d did not become ready — check: k3s kubectl -n $NS logs deploy/$d"
done
sudo systemctl start cc-uvicorn cc-nerve
step "cc-uvicorn, cc-nerve started"

say "verifying restored row counts against the sources"
dst_counts() {  # dst_counts <deploy> <user> <db>
  "${KUBECTL[@]}" -n "$NS" exec -i deploy/"$1" -- psql -U "$2" -d "$3" -tAc \
    "select table_name||'='||(xpath('/row/c/text()', query_to_xml(
       'select count(*) as c from '||quote_ident(table_schema)||'.'||quote_ident(table_name),
       false,true,'')))[1]::text::bigint
     from information_schema.tables where table_schema='public' and table_type='BASE TABLE'
     order by table_name;" 2>/dev/null
}
drift=0
compare() {  # compare <label> <deploy> <user> <db>
  dst_counts "$2" "$3" "$4" > "${WORK}/counts.$1.dst"
  if diff -q "${WORK}/counts.$1.src" "${WORK}/counts.$1.dst" >/dev/null; then
    step "$1: row counts MATCH ($(wc -l < "${WORK}/counts.$1.src") tables)"
  else
    step "$1: ROW COUNTS DIFFER —"
    diff "${WORK}/counts.$1.src" "${WORK}/counts.$1.dst" | head -20
    drift=1
  fi
}
compare central_command cc-postgres   central_command central_command
compare litellm     cc-litellm-db llmproxy    litellm
compare n8n         cc-n8n-db     "${N8N_DB_USER:-n8n}" "${N8N_DB_NAME:-n8n}"

say "placement"
"${KUBECTL[@]}" -n "$NS" get pods -o wide --no-headers | awk '{printf "   %-28s %-10s %s\n", $1, $3, $7}'

if [[ "$drift" == "1" ]]; then
  say "DONE, BUT ROW COUNTS DRIFTED. Dumps kept at ${WORK} — do not delete them until this is explained."
  exit 1
fi
say "DONE. Dumps kept at ${WORK} (delete once you are satisfied)."
say "Docker volumes were NOT removed — 'docker volume ls' still shows cc_*; that is the rollback path."
