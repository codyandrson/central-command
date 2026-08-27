#!/usr/bin/env bash
# ============================================================================
# Bootstrap deploy/pi/.env for a clean k3s install.
#
#   Same generating idiom as deploy/single/make-secrets.sh — the k3s substrate
#   simply never had one, so the 2026-08-23 clean-install run generated nine
#   credentials by hand. This is that job, tracked.
#
#   What it does:
#     * copies deploy/pi/.env.example -> deploy/pi/.env if absent, chmod 600
#     * fills every GENERATED-marked variable that is EMPTY
#       (`openssl rand -hex 32`; LITELLM_MASTER_KEY as `sk-` + hex)
#     * writes the literal PENDING into the three Graphiti virtual-key slots —
#       they can only be minted once the proxy is up, and make-secrets.sh only
#       refuses on EMPTY, so a PENDING placeholder lets the LiteLLM trio come
#       up first. ./deploy/k3s/mint-keys.sh replaces them afterwards.
#     * leaves ELICITED variables alone and prints which ones you must supply.
#
#   IDEMPOTENT: a non-empty value is never overwritten. That is the entire
#   safety story for the two keys that must never rotate —
#     LITELLM_SALT_KEY    encrypts the stored LiteLLM virtual keys
#     N8N_ENCRYPTION_KEY  decrypts the stored Gmail OAuth credential
#   On a RESTORE, paste those two in BEFORE running this.
#
#   Usage:  ./deploy/k3s/init-env.sh
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/deploy/pi/.env"
TEMPLATE="$REPO_ROOT/deploy/pi/.env.example"

if [[ ! -f "$ENV_FILE" ]]; then
  [[ -f "$TEMPLATE" ]] || { echo "FATAL: $TEMPLATE missing" >&2; exit 1; }
  cp "$TEMPLATE" "$ENV_FILE"
  echo "created $ENV_FILE from .env.example"
fi
chmod 600 "$ENV_FILE"

# Read current values WITHOUT sourcing: .env may legitimately hold values with
# spaces, and a half-filled template is not guaranteed to be valid shell.
current() {  # current <name> -> the value, or empty
  sed -n "s/^$1=//p" "$ENV_FILE" | head -1
}

set_var() {  # set_var <name> <value>
  local name="$1" val="$2"
  if grep -q "^${name}=" "$ENV_FILE"; then
    # Values here are hex / sk-hex / PENDING — no sed metacharacters possible.
    sed -i "s|^${name}=.*|${name}=${val}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$name" "$val" >>"$ENV_FILE"
  fi
}

ensure() {  # ensure <name> [prefix] — generate only when empty
  local name="$1" prefix="${2:-}"
  if [[ -n "$(current "$name")" ]]; then
    echo "  kept      $name (already set)"
    return
  fi
  set_var "$name" "${prefix}$(openssl rand -hex 32)"
  echo "  generated $name"
}

placeholder() {  # placeholder <name> — PENDING only when empty
  local name="$1"
  if [[ -n "$(current "$name")" ]]; then
    echo "  kept      $name (already set)"
    return
  fi
  set_var "$name" PENDING
  echo "  PENDING   $name (mint-keys.sh fills this)"
}

echo "GENERATED credentials:"
ensure NEO4J_PASSWORD
ensure LITELLM_MASTER_KEY sk-
ensure LITELLM_SALT_KEY
ensure LITELLM_POSTGRES_PASSWORD
ensure N8N_ENCRYPTION_KEY
ensure N8N_DB_PASSWORD

echo
echo "Graphiti virtual keys (chicken-and-egg: the proxy must exist first):"
placeholder GRAPHITI_LLM_API_KEY
placeholder EMBEDDER_API_KEY
placeholder RERANKER_API_KEY

echo
echo "ELICITED — supply these yourself if you need them (empty is fine and is"
echo "today's state for all of them):"
for v in OPENAI_API_KEY GEMINI_API_KEY; do
  [[ -n "$(current "$v")" ]] && echo "  set       $v" || echo "  empty     $v  (optional provider credential)"
done
echo "  defaulted N8N_DB_USER / N8N_DB_NAME / N8N_HOST / N8N_WEBHOOK_URL / TIMEZONE"
echo "            — make-secrets.sh substitutes working values when blank."

echo
echo "Next:"
echo "  1. ./deploy/k3s/make-secrets.sh   (PENDING passes; empty would not)"
echo "  2. bring the stack up, register models:"
echo "       python3 deploy/pi/litellm/register-models.py --dry-run   # then without"
echo "  3. ./deploy/k3s/mint-keys.sh      (replaces the three PENDINGs + CC_LLM_API_KEY)"
echo
echo "Back $ENV_FILE up OUTSIDE this tree. LITELLM_SALT_KEY and"
echo "N8N_ENCRYPTION_KEY can never be rotated without losing what they encrypt."
