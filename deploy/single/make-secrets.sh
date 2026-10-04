#!/usr/bin/env bash
# ============================================================================
# Generate the single-node profile's credentials into the repo-root .env.
#
#   Compose reads .env natively (`--env-file <repo>/.env`), so there is no
#   second file to render (the old secrets.yaml existed only because `podman
#   kube play` takes YAML and nothing else) and, since v2.42.0, no second .env
#   either: the repo-root .env is the ONE answer file, app and deployment
#   together (2026-09-23 design record, D1). 0600, gitignored, never committed.
#
#   The three credentials the APP also reads are generated under their CC_
#   names — CC_LLM_PROXY_ADMIN_KEY, CC_LITELLM_SALT_KEY, CC_NEO4J_PASSWORD —
#   and compose hands each to its container under the name the container wants
#   (LITELLM_MASTER_KEY, LITELLM_SALT_KEY, NEO4J_AUTH). One fact, one key.
#
#   This script GENERATES the credentials .env leaves blank, so a first
#   install needs nothing from the user but the LLM endpoint and key entered
#   in the proxy UI. It never overwrites a value that is already set — which
#   is what makes it safe to re-run, and what protects the two keys that must
#   never change:
#     CC_LITELLM_SALT_KEY encrypts the stored LiteLLM virtual keys
#     N8N_ENCRYPTION_KEY  decrypts the stored Gmail OAuth credential
#
#   Usage:  ./make-secrets.sh
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

[[ -f "$ENV_FILE" ]] || {
  echo "FATAL: $ENV_FILE not found. Run ./setup.sh configure — it creates the answer file and asks what is missing." >&2
  exit 1
}

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
# The two trust knobs, fanned out in ONE place (2026-09-23 design record, D4).
# This script makes no network call of its own today; it is here so the fan-out
# cannot drift per script, and so `openssl` sees the same trust as everything
# else in the profile.
# shellcheck source=../env-lib.sh
. "$REPO_ROOT/deploy/env-lib.sh"
SECRETS_STATE_DIR="$(cc_state_dir "$ENV_FILE" "$REPO_ROOT" 2>/dev/null)" || SECRETS_STATE_DIR=""
cc_export_tls_env "$SECRETS_STATE_DIR"
if [[ "${CC_TLS_INSECURE:-0}" == "1" ]]; then
  # The tls-insecure WARN is gated through cc_tls_insecure_warn_once (F25) — a
  # run whose `setup.sh` invocation already warned prints nothing here.
  cc_tls_insecure_warn_once "openssl and any tool this script drives (it makes no network call of its own)" || true
fi

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
ensure CC_LLM_PROXY_ADMIN_KEY
ensure CC_LITELLM_SALT_KEY
ensure LITELLM_POSTGRES_PASSWORD
ensure CC_NEO4J_PASSWORD
ensure N8N_ENCRYPTION_KEY
ensure N8N_DB_PASSWORD
# The n8n façade tokens (v2.51.0): what the control plane sends and what the
# shipped webhooks check; apply-workflows.sh renders them into the workflow
# files. Blank ones made a fresh install die at the first import.
ensure CC_EMAIL_FACADE_TOKEN
ensure CC_CALENDAR_FACADE_TOKEN
# The sandbox runner's bearer token (v2.57.0, 2026-10-01 design record D6).
# runner.py enforces it whenever it is set and is OPEN when it is blank, and
# nothing generated it — so the shipped posture was an unauthenticated runner
# on a loopback port every local process can reach. `boot` starts the runner
# with .env exported (or its unit's EnvironmentFile=.env), so the runner and
# the API read this one value from the one file.
ensure CC_SANDBOX_RUNNER_TOKEN

# No graph credentials here: the Graphiti server and its three keys left the
# profile (design record 2026-10-04, D10) — graphiti-core runs inside the API
# on the app's own scoped key, CC_LLM_API_KEY, which the app phase mints
# (app/mint-key) from cc_required_aliases. The n8n database's user/name
# defaults are compose.yaml's, not generated here either.

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
echo "Back it up OUTSIDE this tree — CC_LITELLM_SALT_KEY and N8N_ENCRYPTION_KEY"
echo "can never be rotated without losing what they encrypt."
