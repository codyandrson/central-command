#!/usr/bin/env bash
# ============================================================================
# Generate the single-node profile's credentials into deploy/single/.env.
#
#   Compose reads .env natively, so there is no second file to render any
#   more (the old secrets.yaml existed only because `podman kube play` takes
#   YAML and nothing else). .env is the ONE file in this directory that holds
#   secret material: 0600, gitignored, and never committed.
#
#   This script GENERATES the credentials .env leaves blank, so a first
#   install needs nothing from the user but the LLM endpoint and key entered
#   in the proxy UI. It never overwrites a value that is already set — which
#   is what makes it safe to re-run, and what protects the two keys that must
#   never change:
#     LITELLM_SALT_KEY    encrypts the stored LiteLLM virtual keys
#     N8N_ENCRYPTION_KEY  decrypts the stored Gmail OAuth credential
#
#   Usage:  ./make-secrets.sh
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/.env"

[[ -f "$ENV_FILE" ]] || {
  echo "FATAL: $ENV_FILE not found. Start from: cp env.example .env" >&2
  exit 1
}

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# Set a variable in .env only if it is currently empty, generating a value.
# Idempotent by construction: a second run sees a non-empty value and leaves
# it alone, which is the entire safety story for the two never-rotate keys.
ensure() {
  local name="$1"
  if [[ -n "${!name:-}" ]]; then return; fi
  local val
  val="$(openssl rand -hex 24)"
  printf -v "$name" '%s' "$val"
  export "${name?}"
  if grep -q "^${name}=" "$ENV_FILE"; then
    # The value is hex, so no sed metacharacters can appear in it.
    sed -i "s|^${name}=.*|${name}=${val}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$name" "$val" >>"$ENV_FILE"
  fi
  echo "  generated $name"
}

echo "generating missing credentials in .env:"
ensure LITELLM_MASTER_KEY
ensure LITELLM_SALT_KEY
ensure LITELLM_POSTGRES_PASSWORD
ensure NEO4J_PASSWORD
ensure N8N_ENCRYPTION_KEY
ensure N8N_DB_PASSWORD

# Graphiti's three credentials SHOULD be LiteLLM virtual keys scoped to one
# model group each. Until the installer mints them they fall back to the
# master key — coarser, but a working install beats a broken one. The fallback
# is NOT generated here: compose.yaml expresses it as
# ${GRAPHITI_LLM_API_KEY:-${LITELLM_MASTER_KEY}}, so filling the variable in
# .env later takes effect without regenerating anything. Same for the n8n
# database's user/name defaults.

echo
# chmod is a silent no-op on NTFS (2026-08-21 Windows validation, W8) — verify
# the mode actually took instead of printing an unconditional "0600".
chmod 600 "$ENV_FILE" 2>/dev/null
mode="$(stat -c %a "$ENV_FILE" 2>/dev/null || echo unknown)"
if [[ "$mode" == "600" ]]; then
  echo "credentials are in $ENV_FILE (0600, gitignored)"
else
  echo "credentials are in $ENV_FILE (gitignored)"
  echo "NOTE: filesystem did not apply 0600 (Windows/NTFS) — the file relies"
  echo "on the user account's ACLs; single-user box assumed."
fi
echo "Back it up OUTSIDE this tree — LITELLM_SALT_KEY and N8N_ENCRYPTION_KEY"
echo "can never be rotated without losing what they encrypt."
