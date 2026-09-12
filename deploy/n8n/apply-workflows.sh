#!/usr/bin/env bash
# deploy/n8n/apply-workflows.sh — put the shipped n8n workflows into the running n8n.
#
# The files under deploy/n8n/workflows/ are the SOURCE OF TRUTH for the n8n
# façades Central Command calls; the canvas in n8n is a rendering of them.
# Both updaters run this when a release touches deploy/n8n/, and a fresh
# install runs it once. Idempotent: import UPSERTS by workflow id (the ids in
# the files are stable on purpose), so re-running converges — and a hand edit
# on the canvas is overwritten by the next release, which is the point.
#
# Each step, and why it is shaped the way it is:
#   1. render __CC_EMAIL_FACADE_TOKEN__ from .env into a private copy — the
#      token never rides the repo; the Code node embeds it as a literal;
#   2. copy the files INTO the n8n container and check a sha256 THERE — never
#      stream bulk data through exec (kubectl exec truncates silently);
#   3. `n8n import:workflow` — assigns to the instance owner's personal
#      project and resolves credentials BY NAME ({"id": null, "name": …}), so
#      a deployment only has to name its Gmail credential "Gmail account";
#   4. activate by SQL: the CLI's --activeState=fromJson is refused outside
#      queue mode and the import always lands active=false; active=true +
#      activeVersionId=versionId is exactly what n8n's own activation writes;
#   5. restart n8n (webhooks are registered at boot from the active
#      workflows) and poll the webhook until it answers.
#
# Usage: apply-workflows.sh --k3s | --podman
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
WF_DIR="$HERE/workflows"
NS="${CC_K8S_NAMESPACE:-central-command}"

usage() { echo "usage: $0 --k3s | --podman" >&2; exit 2; }
[[ $# -eq 1 ]] || usage
case "$1" in
  --k3s)    RT=k3s ;;
  --podman) RT=podman ;;
  *) usage ;;
esac

note() { printf '%s\n' "$*" >&2; }
die()  { note "apply-workflows: $*"; exit 1; }
get_kv() { sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -1 | tr -d '"'"'"; }

# ── runtime seams ────────────────────────────────────────────────────────────
if [[ $RT == k3s ]]; then
  ENV_FILE="$REPO/.env"
  DB_ENV="$REPO/deploy/pi/.env"          # where the k3s profile keeps N8N_DB_*
  K=(k3s kubectl -n "$NS")
  [[ $EUID -eq 0 ]] || K=(sudo k3s kubectl -n "$NS")
  n8n_pod()  { "${K[@]}" get pod -l app=cc-n8n -o jsonpath='{.items[0].metadata.name}'; }
  n8n_exec() { "${K[@]}" exec "$(n8n_pod)" -- "$@"; }
  n8n_cp()   { "${K[@]}" cp "$1" "$(n8n_pod):$2"; }
  n8n_psql() { "${K[@]}" exec -i deploy/cc-n8n-db -- psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" -tA "$@"; }
  n8n_restart() {
    "${K[@]}" rollout restart deploy/cc-n8n
    "${K[@]}" rollout status deploy/cc-n8n --timeout=180s
  }
  present() { "${K[@]}" get deploy cc-n8n >/dev/null 2>&1; }
  FACADE_URL="http://127.0.0.1:5678/webhook/cc-email-facade"
else
  ENV_FILE="$REPO/deploy/single/.env"
  DB_ENV="$ENV_FILE"
  PFX="$(get_kv "$ENV_FILE" CC_POD_PREFIX)"; PFX="${PFX:-cc-}"
  C_N8N="${PFX}n8n"; C_DB="${PFX}n8n-db"
  n8n_exec() { podman exec "$C_N8N" "$@"; }
  n8n_cp()   { podman cp "$1" "$C_N8N:$2"; }
  n8n_psql() { podman exec -i "$C_DB" psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" -tA "$@"; }
  n8n_restart() { podman restart "$C_N8N" >/dev/null; }
  present() { podman container exists "$C_N8N" 2>/dev/null; }
  port="$(get_kv "$ENV_FILE" CC_N8N_PORT)"; FACADE_URL="http://127.0.0.1:${port:-5678}/webhook/cc-email-facade"
fi
DB_USER="$(get_kv "$DB_ENV" N8N_DB_USER)"; DB_USER="${DB_USER:-n8n}"
DB_NAME="$(get_kv "$DB_ENV" N8N_DB_NAME)"; DB_NAME="${DB_NAME:-n8n}"

present || { note "apply-workflows: n8n is not deployed here — nothing to apply"; exit 0; }

# ── 1. render the token ──────────────────────────────────────────────────────
TOKEN="$(get_kv "$ENV_FILE" CC_EMAIL_FACADE_TOKEN)"
[[ -n "$TOKEN" ]] || die "CC_EMAIL_FACADE_TOKEN is empty in $ENV_FILE — the façade would accept nobody"
# It lands inside a JS string literal inside a JSON string: keep it to
# characters that need no escaping in either, or refuse rather than corrupt.
[[ "$TOKEN" =~ ^[A-Za-z0-9_.:+=-]+$ ]] || die "CC_EMAIL_FACADE_TOKEN has characters outside [A-Za-z0-9_.:+=-]; re-mint it"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
chmod 700 "$STAGE"
for f in "$WF_DIR"/*.json; do
  sed "s/__CC_EMAIL_FACADE_TOKEN__/$TOKEN/g" "$f" >"$STAGE/$(basename "$f")"
done
IDS="$(sed -n 's/^  "id": "\([A-Za-z0-9]*\)",$/\1/p' "$WF_DIR"/*.json | tr '\n' ' ')"
[[ -n "$IDS" ]] || die "no workflow ids found under $WF_DIR"

# ── 2. copy in, verify bytes ─────────────────────────────────────────────────
IN="/tmp/cc-workflows"
n8n_exec sh -c "rm -rf $IN && mkdir -p $IN"
for f in "$STAGE"/*.json; do
  n8n_cp "$f" "$IN/$(basename "$f")"
  want="$(sha256sum "$f" | cut -d' ' -f1)"
  got="$(n8n_exec sha256sum "$IN/$(basename "$f")" | cut -d' ' -f1)"
  [[ "$want" == "$got" ]] || die "$(basename "$f") did not arrive intact (sha mismatch)"
done

# ── 3. import (upsert by id, credentials by name) ────────────────────────────
n8n_exec n8n import:workflow --separate --input="$IN" >&2 \
  || die "n8n import:workflow failed (see above)"
n8n_exec sh -c "rm -rf $IN"

# ── 4. activate ──────────────────────────────────────────────────────────────
in_list="$(printf "'%s'," $IDS)"; in_list="${in_list%,}"
n8n_psql -c "update workflow_entity set active = true, \"activeVersionId\" = \"versionId\" where id in ($in_list);" >/dev/null \
  || die "activation UPDATE failed"
missing_cred="$(n8n_psql -c "select count(*) from workflow_entity we, jsonb_array_elements(we.nodes::jsonb) n where we.id in ($in_list) and n->'credentials' is not null and (n->'credentials'->'gmailOAuth2'->>'id') is null;")"
if [[ "${missing_cred:-0}" != 0 ]]; then
  note "USERACTION n8n: $missing_cred node(s) found no credential named \"Gmail account\" (type Gmail OAuth2 API) — create it in the n8n UI with that exact name, then re-run this script"
fi

# ── 5. restart and prove the webhook answers ─────────────────────────────────
n8n_restart
code=000
for _ in $(seq 1 60); do
  # A wrong token is answered by the WORKFLOW (HTTP 500 "Error in workflow");
  # 404 means the webhook is not registered, i.e. the workflow is not active.
  code="$(curl -s -o /dev/null -w '%{http_code}' -m 5 -X POST "$FACADE_URL" -H 'content-type: application/json' -d '{"mode":"list","scope_query":"x"}' 2>/dev/null || echo 000)"
  if [[ "$code" != "404" && "$code" != "000" ]]; then
    note "apply-workflows: façade webhook registered (HTTP $code without a token) — applied: $IDS"
    exit 0
  fi
  sleep 3
done
die "façade webhook still not registered after restart (last HTTP $code) — check n8n's logs and that the workflows show active in the n8n UI"
