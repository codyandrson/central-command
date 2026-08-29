#!/usr/bin/env bash
# ============================================================================
# Create the Central Command Secrets and ConfigMaps in k3s FROM deploy/pi/.env.
#
#   Secrets are created IMPERATIVELY, on purpose. deploy/pi/.env is gitignored
#   and is the single source of truth for credentials; rendering them into a
#   YAML file under deploy/k3s/ would put base64'd secrets in a tree that gets
#   pushed. base64 is not encryption. So: no secret literals live in this repo.
#
#   TWO KEYS MUST CARRY OVER UNCHANGED — this script copies, never generates:
#     LITELLM_SALT_KEY    encrypts stored LiteLLM virtual keys
#     N8N_ENCRYPTION_KEY  decrypts the stored Gmail OAuth credential
#   A restore under a different value leaves the rows present but undecryptable.
#
#   Idempotent: every object is applied with --dry-run=client | kubectl apply,
#   so re-running updates in place rather than erroring on "already exists".
#
#   Usage:  ./deploy/k3s/make-secrets.sh
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/deploy/pi/.env"
NS=central-command

if [[ ! -f "$ENV_FILE" ]]; then
  echo "FATAL: $ENV_FILE not found — it is the source of truth for secrets." >&2
  exit 1
fi

# k3s puts kubectl behind `k3s kubectl` and its kubeconfig is root-owned.
KUBECTL=(sudo k3s kubectl)

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

require() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "FATAL: $name is empty or unset in $ENV_FILE" >&2
    exit 1
  fi
}
for k in LITELLM_MASTER_KEY LITELLM_SALT_KEY LITELLM_POSTGRES_PASSWORD \
         NEO4J_PASSWORD GRAPHITI_LLM_API_KEY EMBEDDER_API_KEY RERANKER_API_KEY \
         N8N_ENCRYPTION_KEY N8N_DB_PASSWORD; do
  require "$k"
done

# PENDING is ACCEPTED, deliberately. The three Graphiti keys are LiteLLM virtual
# keys, so on a fully-clean deployment they cannot exist until the proxy is up —
# and the proxy cannot come up until this script has made its Secret. init-env.sh
# writes the literal PENDING to break that cycle; `require` only refuses EMPTY,
# so nothing here needed loosening. Say so out loud rather than shipping a
# Graphiti that authenticates with the word PENDING and nobody noticing.
pending=""
for k in GRAPHITI_LLM_API_KEY EMBEDDER_API_KEY RERANKER_API_KEY; do
  [[ "${!k}" == "PENDING" ]] && pending+=" $k"
done
if [[ -n "$pending" ]]; then
  echo "NOTE: still PENDING (placeholder, not a working key):$pending" >&2
  echo "      Graphiti will 401 until ./deploy/k3s/mint-keys.sh mints them." >&2
fi

apply_secret() {  # apply_secret <name> <k=v>...
  local name="$1"; shift
  local args=()
  for kv in "$@"; do args+=(--from-literal="$kv"); done
  "${KUBECTL[@]}" -n "$NS" create secret generic "$name" "${args[@]}" \
    --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f - >/dev/null
  echo "  secret/$name"
}

echo "namespace/$NS"
"${KUBECTL[@]}" apply -f "$REPO_ROOT/deploy/k3s/00-namespace.yaml" >/dev/null

echo "secrets:"
# The proxy resolves its own DSN from this one value, so the password never has
# to be reassembled in the manifest.
litellm_args=(
  "LITELLM_MASTER_KEY=$LITELLM_MASTER_KEY"
  "LITELLM_SALT_KEY=$LITELLM_SALT_KEY"
  "LITELLM_POSTGRES_PASSWORD=$LITELLM_POSTGRES_PASSWORD"
  "DATABASE_URL=postgresql://llmproxy:${LITELLM_POSTGRES_PASSWORD}@litellm-db:5432/litellm"
  "OPENAI_API_KEY=${OPENAI_API_KEY:-}"
  "GEMINI_API_KEY=${GEMINI_API_KEY:-}"
)
# UI_USERNAME/UI_PASSWORD (admin-UI login) are OPTIONAL and only added when
# set, so the Secret simply lacks the key and 30-litellm.yaml's
# `optional: true` secretKeyRef leaves the env var unset — never an empty
# string: LiteLLM reads UI_PASSWORD with os.getenv(..., None), so "" would
# become the real admin password (login_utils.py, verified 2026-08-29).
[[ -n "${UI_USERNAME:-}" ]] && litellm_args+=("UI_USERNAME=$UI_USERNAME")
[[ -n "${UI_PASSWORD:-}" ]] && litellm_args+=("UI_PASSWORD=$UI_PASSWORD")
apply_secret cc-litellm "${litellm_args[@]}"

# NEO4J_AUTH is the container's expected "user/password" form; NEO4J_PASSWORD is
# what Graphiti wants on its own. Same secret, two shapes, one source value.
apply_secret cc-neo4j \
  "NEO4J_AUTH=neo4j/${NEO4J_PASSWORD}" \
  "NEO4J_PASSWORD=$NEO4J_PASSWORD"

# All three are LiteLLM virtual keys pointed at the workstation's local models —
# Graphiti reaches no external provider since 2026-08-01. Each is scoped to its
# own model group so a leak in one cannot spend through another:
#   GRAPHITI_LLM_API_KEY -> ["graphiti-llm"]          entity extraction
#   EMBEDDER_API_KEY     -> ["qwen3-embedding-local"] embeddings
#   RERANKER_API_KEY     -> ["cc-default","qwen3-rerank-local"] cross-encoder
# ANTHROPIC_API_KEY is intentionally absent — nothing in the graphiti config
# selects the anthropic provider any more.
apply_secret cc-graphiti \
  "GRAPHITI_LLM_API_KEY=$GRAPHITI_LLM_API_KEY" \
  "EMBEDDER_API_KEY=$EMBEDDER_API_KEY" \
  "RERANKER_API_KEY=$RERANKER_API_KEY"

apply_secret cc-n8n \
  "N8N_ENCRYPTION_KEY=$N8N_ENCRYPTION_KEY" \
  "N8N_DB_USER=${N8N_DB_USER:-n8n}" \
  "N8N_DB_PASSWORD=$N8N_DB_PASSWORD" \
  "N8N_DB_NAME=${N8N_DB_NAME:-n8n}" \
  "N8N_HOST=${N8N_HOST:-localhost}" \
  "N8N_WEBHOOK_URL=${N8N_WEBHOOK_URL:-http://localhost:5678/}" \
  "TIMEZONE=${TIMEZONE:-UTC}"

# cc-crawler's OPTIONAL LLM mode. Created only when CC_LLM_API_KEY is present:
# the crawler runs fine without this Secret (70-crawler.yaml marks the
# secretRef optional) and simply falls back to heuristic pruning with a
# warning, so a missing key must not fail this script.
#
# CC_LLM_API_KEY is reused here because it is the key already in .env, and it
# points at THIS cluster's LiteLLM — never an external provider. A dedicated
# scoped virtual key (the Graphiti precedent) was offered and DECLINED by the
# operator 2026-08-07: the key only reaches the loopback/tailnet-only proxy
# fronting local models, so compromise buys free local inference, not spend
# or data. Revisit only if the shared key's MCP object_permission list ever
# fronts something sensitive (recorded in STATUS.md).
# CC_LLM_API_KEY lives in the REPO ROOT .env (the app's env), not in
# deploy/pi/.env — the app reads it directly, so it was never migrated here.
# Fall back to the root .env for this one var rather than duplicating a
# credential across two files.
if [[ -z "${CC_LLM_API_KEY:-}" && -f "$REPO_ROOT/.env" ]]; then
  CC_LLM_API_KEY="$(grep -E '^CC_LLM_API_KEY=' "$REPO_ROOT/.env" | head -1 | cut -d= -f2-)"
fi
if [[ -n "${CC_LLM_API_KEY:-}" ]]; then
  apply_secret cc-crawler-llm \
    "LLM_BASE_URL=${CC_CRAWLER_LLM_BASE_URL:-http://litellm:4000/v1}" \
    "LLM_API_KEY=$CC_LLM_API_KEY" \
    "LLM_MODEL=${CC_CRAWLER_LLM_MODEL:-cc-default}"
else
  echo "  (skipped secret/cc-crawler-llm — CC_LLM_API_KEY unset; crawler stays pruning-only)"
fi

echo "configmaps:"
# These three are FILES in the repo, mounted the same way compose mounted them.
apply_cm_file() {  # apply_cm_file <name> <key>=<path>
  local name="$1" spec="$2"
  "${KUBECTL[@]}" -n "$NS" create configmap "$name" --from-file="$spec" \
    --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f - >/dev/null
  echo "  configmap/$name"
}
apply_cm_file cc-litellm-config  "config.yaml=$REPO_ROOT/deploy/pi/litellm/config.yaml"
apply_cm_file cc-graphiti-config "config.yaml=$REPO_ROOT/deploy/pi/graphiti/config.yaml"
apply_cm_file cc-schema-sql      "01-schema.sql=$REPO_ROOT/central_command/db/schema.sql"

echo
echo "Done. Secrets and configmaps are in namespace $NS."
echo "Nothing secret was written to disk inside the repo."
