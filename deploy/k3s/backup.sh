#!/usr/bin/env bash
# ============================================================================
# Central Command disaster-recovery backup — k3s edition (2026-07-31).
#
#   Supersedes deploy/pi/backup.sh, which drove the Docker stack. Same four
#   stores, same validity checks, same retention rule, same exit contract. The
#   only real change is HOW each store is reached, plus one new fact: the graph
#   now lives on the CHROMEBOX, so its dump runs there.
#
#     central_command  the spine: ledger, proposals, event log, charters, roster
#     litellm      virtual keys + spend + DB-registered models
#     n8n          workflows + the ENCRYPTED Gmail OAuth credential
#     neo4j        the knowledge graph            (chromebox)
#
#   Two keys are captured ALONGSIDE the dumps, because without them the dumps
#   are undecryptable ciphertext, not a backup:
#     N8N_ENCRYPTION_KEY  decrypts the Gmail credential
#     LITELLM_SALT_KEY    decrypts the stored virtual keys
#   Written 0600 into a backup directory OUTSIDE the git repo, on purpose —
#   dumps and key material must never sit in a tree that gets pushed.
#
#   Neo4j Community has no online backup, so the graph is a scale-to-0 -> dump
#   -> scale-to-1 cycle. cc-graphiti is scaled down for the SAME window
#   deliberately: with neo4j gone it still ACKs add_memory {submitted:true} and
#   the queued episode dies async and silently. Down, an in-window caller gets a
#   loud transport error instead.
#
#   Exits NON-ZERO if any store failed, so a silent failure streak cannot be
#   mistaken for success.
#
#   Restore: see the RESTORE section at the bottom.
# ============================================================================
set -uo pipefail

REPO="${REPO:-/home/codyslab/central-command}"
OUT="${CC_BACKUP_DIR:-/home/codyslab/cc-backups}"
RETAIN_DAYS="${CC_BACKUP_RETAIN_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NS=central-command
K=(k3s kubectl -n "$NS")   # runs as root from the systemd timer

# 077 from the very top: EVERY artifact here is sensitive, not just the key
# file. The n8n dump carries the encrypted Gmail credential and the spine dump
# carries real mail content.
umask 077
mkdir -p "$OUT"; chmod 700 "$OUT"

set -a
# shellcheck disable=SC1091
[[ -f "${REPO}/deploy/pi/.env" ]] && . "${REPO}/deploy/pi/.env"
set +a

# One backup at a time — an overlapping run would fight over the neo4j scale-down.
exec 9>"${OUT}/.backup.lock"
if ! flock -n 9; then
  echo "[backup] another backup holds the lock — this run is skipped" >&2
  exit 1
fi

failed=()
say() { printf '[backup] %s\n' "$*"; }

# --- Postgres stores ---------------------------------------------------------
# pg_dump runs INSIDE the pod, so the host needs no postgres client — and it
# dumps to a FILE in the pod, never through exec's stdout: kubectl exec
# silently drops the tail of a large fast stream and still exits 0 (bitten
# 2026-09-01, the litellm pre-update dump). In-pod, pg_dump's exit code
# guards completeness; the transfer out is checksum-verified and retried.
# A dump is only accepted when it is gzip-valid AND carries pg_dump's own
# completion marker — a truncated dump that gunzips cleanly is the failure mode
# that makes people think they have backups.
dump_pg() {
  local label="$1" dep="$2" user="$3" db="$4"
  local file="${OUT}/${label}_${STAMP}.sql.gz" tmp=/tmp/cc-dump.sql.gz
  say "dumping ${label}…"
  if ! "${K[@]}" exec deploy/"$dep" -- pg_dump -U "$user" -d "$db" \
        --clean --if-exists --no-owner --no-privileges -Z6 -f "$tmp" 2>/dev/null; then
    say "ERROR: ${label} pg_dump failed"; failed+=("$label"); return 1
  fi
  local want got try
  want="$("${K[@]}" exec deploy/"$dep" -- sha256sum "$tmp")"; want="${want%% *}"
  [[ -n "$want" ]] || { say "ERROR: ${label} could not checksum the in-pod dump"; failed+=("$label"); return 1; }
  for try in 1 2 3; do
    "${K[@]}" exec deploy/"$dep" -- cat "$tmp" > "$file"
    got="$(sha256sum "$file")"; got="${got%% *}"
    [[ "$got" == "$want" ]] && break
    if [[ "$try" == 3 ]]; then
      say "ERROR: ${label} transfer truncated 3 times (checksum mismatch)"; rm -f "$file"; failed+=("$label"); return 1
    fi
  done
  "${K[@]}" exec deploy/"$dep" -- rm -f "$tmp"
  if ! gzip -t "$file" 2>/dev/null; then
    say "ERROR: ${label} dump is not valid gzip"; rm -f "$file"; failed+=("$label"); return 1
  fi
  if ! gunzip -c "$file" | tail -5 | grep -q 'PostgreSQL database dump complete'; then
    say "ERROR: ${label} dump is TRUNCATED (no completion marker)"; rm -f "$file"; failed+=("$label"); return 1
  fi
  say "  ok → $(basename "$file") ($(du -h "$file" | cut -f1))"
}

dump_pg central_command cc-postgres   central_command central_command
dump_pg litellm     cc-litellm-db llmproxy    litellm
dump_pg n8n         cc-n8n-db     "${N8N_DB_USER:-n8n}" "${N8N_DB_NAME:-n8n}"

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

# --- Neo4j: offline dump on the chromebox ------------------------------------
# The PVC is ReadWriteOnce and node-pinned, so the dumper pod must land on the
# same node and cc-neo4j must be down first.
neo4j_ok=0
graph_back() {
  "${K[@]}" scale deploy/cc-neo4j --replicas=1 >/dev/null 2>&1 9>&-
  "${K[@]}" scale deploy/cc-graphiti --replicas=1 >/dev/null 2>&1 9>&-
  "${K[@]}" delete pod/neo4j-dumper --ignore-not-found --wait=false >/dev/null 2>&1 9>&-
  return 0
}
# Whatever happens next — error, SIGTERM from a timer timeout, Ctrl-C — the
# graph stack comes back. Leaving neo4j at 0 replicas would silently halt it.
trap graph_back EXIT
trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM

say "dumping neo4j (brief scale-down of cc-graphiti + cc-neo4j)…"
"${K[@]}" scale deploy/cc-graphiti --replicas=0 >/dev/null 9>&-
"${K[@]}" scale deploy/cc-neo4j    --replicas=0 >/dev/null 9>&-
"${K[@]}" wait --for=delete pod -l app=cc-neo4j --timeout=180s >/dev/null 2>&1 9>&-

neo4j_file="${OUT}/neo4j_${STAMP}.dump.gz"
"${K[@]}" delete pod/neo4j-dumper --ignore-not-found >/dev/null 2>&1 9>&-
cat <<'DUMPER' | "${K[@]}" apply -f - >/dev/null 2>&1 9>&-
apiVersion: v1
kind: Pod
metadata: {name: neo4j-dumper, namespace: central-command}
spec:
  nodeSelector: {cc-role/compute: "true"}
  restartPolicy: Never
  containers:
    - name: dumper
      image: docker.io/library/neo4j:5.26.2
      command: ["sleep", "1800"]
      volumeMounts: [{name: data, mountPath: /data}]
  volumes:
    - name: data
      persistentVolumeClaim: {claimName: cc-neo4j-data}
DUMPER

if "${K[@]}" wait --for=condition=Ready pod/neo4j-dumper --timeout=300s >/dev/null 2>&1 9>&- \
   && "${K[@]}" exec pod/neo4j-dumper -- \
        neo4j-admin database dump neo4j --to-stdout 2>/dev/null 9>&- | gzip > "$neo4j_file" \
   && [[ "$(stat -c%s "$neo4j_file" 2>/dev/null || echo 0)" -gt 1024 ]] \
   && gzip -t "$neo4j_file" 2>/dev/null; then
  say "  ok → $(basename "$neo4j_file") ($(du -h "$neo4j_file" | cut -f1))"
  neo4j_ok=1
else
  say "ERROR: neo4j dump failed — the graph is NOT backed up tonight"
  rm -f "$neo4j_file"
  failed+=("neo4j")
fi

graph_back
trap - EXIT HUP INT TERM

# Wait for the graph to actually come back rather than assuming it did.
if "${K[@]}" rollout status deploy/cc-neo4j --timeout=300s >/dev/null 2>&1; then
  say "neo4j back up"
else
  say "WARNING: cc-neo4j did not become ready after the restart — check it"
fi

# --- Retention ---------------------------------------------------------------
# Prune ONLY what succeeded tonight. A failure streak longer than RETAIN_DAYS
# must never age out the last GOOD copy — that turns a bad week into data loss.
prune() { find "$OUT" -maxdepth 1 -type f -name "$1" -mtime "+${RETAIN_DAYS}" -delete 2>/dev/null || true; }
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
#   Postgres — scale the consumer to 0 first (a running litellm re-runs prisma
#   migrations; a running n8n holds workflow rows open):
#     k3s kubectl -n central-command scale deploy/cc-litellm --replicas=0
#     gunzip -c litellm_<stamp>.sql.gz | k3s kubectl -n central-command exec -i deploy/cc-litellm-db -- psql -U llmproxy -d litellm
#     k3s kubectl -n central-command scale deploy/cc-litellm --replicas=1
#   The spine's consumer is the API, which runs outside the cluster:
#     sudo systemctl stop cc-uvicorn
#     gunzip -c central_command_<stamp>.sql.gz | k3s kubectl -n central-command exec -i deploy/cc-postgres -- psql -U central_command -d central_command
#     sudo systemctl start cc-uvicorn
#
#   n8n — the ONLY holder of the Gmail OAuth credential, and the restore with
#   two traps that both bite silently. Do it in this order:
#
#     1. Pick the dump by CONTENT, not by date. The nightly backup runs
#        unconditionally, so a post-wipe empty instance gets dumped too and the
#        NEWEST n8n_<stamp>.sql.gz can hold zero workflows (hit 2026-08-26).
#        Count the rows before trusting one:
#          gunzip -c n8n_<stamp>.sql.gz | awk '/^COPY public.workflow_entity/,/^\.$/' | wc -l
#        (rows + 2 for the COPY line and the terminating dot; ~2 means empty).
#        Then pair it with the SAME-STAMP keys_<stamp>.env — a dump restored
#        under a different N8N_ENCRYPTION_KEY leaves the credential rows present
#        and undecryptable.
#     2. k3s kubectl -n central-command scale deploy/cc-n8n --replicas=0
#     3. Put that keys_<stamp>.env's N8N_ENCRYPTION_KEY into deploy/pi/.env, then
#          ./deploy/k3s/make-secrets.sh
#     4. Delete the key file the FRESH instance stamped onto the PVC. n8n writes
#        its generated key into /home/node/.n8n/config on FIRST boot and that
#        file wins over the env var, so a restore under the old key CrashLoops
#        with "Mismatching encryption keys" until it is gone. The PVC needs a
#        helper pod (cc-n8n is scaled to 0):
#          k3s kubectl -n central-command run n8n-fixer --image=busybox --restart=Never \
#            --overrides='{"spec":{"nodeSelector":{"cc-role/anchor":"true"},
#            "containers":[{"name":"n8n-fixer","image":"busybox","command":["sleep","600"],
#            "volumeMounts":[{"name":"d","mountPath":"/data"}]}],
#            "volumes":[{"name":"d","persistentVolumeClaim":{"claimName":"cc-n8n-data"}}]}}'
#          k3s kubectl -n central-command exec n8n-fixer -- rm -f /data/config
#          k3s kubectl -n central-command delete pod n8n-fixer
#     5. gunzip -c n8n_<stamp>.sql.gz | k3s kubectl -n central-command exec -i deploy/cc-n8n-db -- psql -U n8n -d n8n
#     6. k3s kubectl -n central-command scale deploy/cc-n8n --replicas=1
#     7. Verify BOTH halves — a healthy pod proves the key matched, not that the
#        credential decrypts:
#          curl -fsS http://127.0.0.1:5678/healthz
#          ./deploy/k3s/verify.sh        # without --clean-install: its n8n
#                                        # assertions cover façade-active and
#                                        # Gmail-credential-decrypts
#
#   Neo4j — offline load on the chromebox, overwriting the store. Same dumper
#   pod shape as above, with `database load --from-stdin`:
#     k3s kubectl -n central-command scale deploy/cc-graphiti deploy/cc-neo4j --replicas=0
#     # create the neo4j-dumper pod (see the manifest inlined above), then:
#     gunzip -c neo4j_<stamp>.dump.gz | k3s kubectl -n central-command exec -i pod/neo4j-dumper -- \
#       neo4j-admin database load neo4j --from-stdin --overwrite-destination=true
#     k3s kubectl -n central-command exec pod/neo4j-dumper -- chown -R 7474:7474 /data
#     k3s kubectl -n central-command delete pod/neo4j-dumper
#     k3s kubectl -n central-command scale deploy/cc-neo4j deploy/cc-graphiti --replicas=1
#   The chown is not optional: the dumper runs as root, the neo4j pod as 7474,
#   and root-owned store files fail to start with an error that reads like
#   corruption.
#
#   Keys — if restoring onto a NEW host, put keys_<stamp>.env's values into
#   deploy/pi/.env BEFORE running make-secrets.sh. Same-host restores already
#   have them.
# ============================================================================
