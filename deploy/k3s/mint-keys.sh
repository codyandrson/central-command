#!/usr/bin/env bash
# ============================================================================
# Mint the four LiteLLM virtual keys against the RUNNING proxy and write them
# back where each one is read from.
#
#   Three go into deploy/pi/.env (replacing init-env.sh's PENDING placeholders):
#     GRAPHITI_LLM_API_KEY  -> ["graphiti-llm"]                    extraction
#     EMBEDDER_API_KEY      -> ["cc-embedding"]            embeddings (role alias)
#     RERANKER_API_KEY      -> ["cc-default","cc-rerank"]  cross-encoder (role alias)
#   One goes into the REPO-ROOT .env, which is the app's own env:
#     CC_LLM_API_KEY        -> ["cc-default"]        alias cc-spine
#
#   Each is scoped to its own model group so a leak in one cannot spend through
#   another. `tags` is deliberately absent from every body — a tags field 403s
#   on non-Enterprise LiteLLM.
#
#   This closes the chicken-and-egg: the proxy must exist before a virtual key
#   can, and the proxy's Secret must exist before the proxy can. init-env.sh
#   writes PENDING, make-secrets.sh accepts it, the trio comes up, this runs.
#
#   IDEMPOTENT: a value that is already set and is not PENDING is KEPT. Set
#   FORCE=1 to mint a replacement anyway (the old key is NOT deleted — revoke it
#   in the LiteLLM UI if you mean to).
#
#   Then it re-runs make-secrets.sh and restarts cc-graphiti, because the keys
#   reach the pod through a Secret and a Secret change is not a pod restart.
#   On a clean install cc-graphiti does not exist yet (§4 applies it after this
#   runs in §1) — that case is a note, not an error.
#
#   Usage:  ./deploy/k3s/mint-keys.sh        [FORCE=1]
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PI_ENV="$REPO_ROOT/deploy/pi/.env"
ROOT_ENV="$REPO_ROOT/.env"
BASE_URL="${CC_LITELLM_URL:-http://127.0.0.1:4000}"
KUBECTL=(sudo k3s kubectl)
NS=central-command

[[ -f "$PI_ENV" ]] || { echo "FATAL: $PI_ENV not found — run ./deploy/k3s/init-env.sh first" >&2; exit 1; }
[[ -f "$ROOT_ENV" ]] || { echo "FATAL: $ROOT_ENV not found — cp .env.example .env first" >&2; exit 1; }

MASTER="$(sed -n 's/^LITELLM_MASTER_KEY=//p' "$PI_ENV" | head -1)"
[[ -n "$MASTER" ]] || { echo "FATAL: LITELLM_MASTER_KEY empty in $PI_ENV" >&2; exit 1; }

curl -fsS "$BASE_URL/health/liveliness" >/dev/null \
  || { echo "FATAL: no LiteLLM proxy at $BASE_URL — bring the trio up first." >&2; exit 1; }

current() {  # current <file> <name>
  sed -n "s/^$2=//p" "$1" | head -1
}

set_var() {  # set_var <file> <name> <value>
  local f="$1" name="$2" val="$3"
  if grep -q "^${name}=" "$f"; then
    # Minted keys are `sk-` + URL-safe base64 — no sed metacharacters, but use
    # a delimiter that cannot appear in one anyway.
    sed -i "s|^${name}=.*|${name}=${val}|" "$f"
  else
    printf '%s=%s\n' "$name" "$val" >>"$f"
  fi
}

mint() {  # mint <alias> <json-array-of-models> -> prints the key on stdout
  local alias="$1" models="$2" out
  out="$(curl -fsS -X POST "$BASE_URL/key/generate" \
    -H "Authorization: Bearer $MASTER" -H 'Content-Type: application/json' \
    -d "{\"key_alias\": \"$alias\", \"models\": $models}")" \
    || { echo "FATAL: /key/generate failed for $alias" >&2; exit 1; }
  python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])' <<<"$out"
}

ensure_key() {  # ensure_key <file> <var> <alias> <models-json>
  local f="$1" var="$2" alias="$3" models="$4" cur
  cur="$(current "$f" "$var")"
  if [[ -n "$cur" && "$cur" != PENDING && "${FORCE:-}" != 1 ]]; then
    echo "  kept    $var (already set; FORCE=1 to re-mint)"
    return
  fi
  set_var "$f" "$var" "$(mint "$alias" "$models")"
  echo "  minted  $var -> alias $alias, models $models"
}

echo "minting virtual keys against $BASE_URL:"
ensure_key "$PI_ENV"   GRAPHITI_LLM_API_KEY graphiti-llm        '["graphiti-llm"]'
ensure_key "$PI_ENV"   EMBEDDER_API_KEY     graphiti-embeddings '["cc-embedding"]'
# cc-default is in scope alongside the reranker because Graphiti falls back to
# the logprob-classifier prompts on cc-default when GRAPHITI_RERANK_MODEL is
# unset or the rerank route fails (40-graph.yaml).
ensure_key "$PI_ENV"   RERANKER_API_KEY     graphiti-reranker   '["cc-default","cc-rerank"]'
# The spine's key is a VIRTUAL key, never the master key (config.py's contract).
ensure_key "$ROOT_ENV" CC_LLM_API_KEY       cc-spine            '["cc-default"]'

echo
echo "re-applying secrets (the keys reach the pods through a Secret):"
"$REPO_ROOT/deploy/k3s/make-secrets.sh"

echo
# At this script's own documented clean-install position (README §1) the graph
# manifests have NOT been applied yet — §4 does that — so a bare `rollout
# restart` errors NotFound and `set -e` kills the script AFTER the keys are
# written but BEFORE the instructions below print. Capture-then-test, never
# `| grep -q`, which inverts under pipefail when kubectl takes SIGPIPE.
if "${KUBECTL[@]}" -n "$NS" get deploy/cc-graphiti >/dev/null 2>&1; then
  echo "restarting cc-graphiti (a Secret change is not a pod restart):"
  "${KUBECTL[@]}" -n "$NS" rollout restart deploy/cc-graphiti
  "${KUBECTL[@]}" -n "$NS" rollout status deploy/cc-graphiti --timeout=180s
else
  echo "cc-graphiti not deployed yet — it will read the fresh Secret when §4 first creates it."
fi

echo
echo "Done. cc-uvicorn also reads CC_LLM_API_KEY — restart it if it is running:"
echo "  sudo systemctl restart cc-uvicorn"
