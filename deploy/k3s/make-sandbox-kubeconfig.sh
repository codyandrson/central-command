#!/usr/bin/env bash
# ============================================================================
# Extract the cc-sandbox-runner ServiceAccount's token + cluster CA from
# deploy/k3s/60-sandbox.yaml's Secret, and write a standalone kubeconfig the
# sandbox-runner systemd unit (cc-sandbox-runner.service) points at via
# CC_SANDBOX_KUBECONFIG.
#
#   Namespace-scoped by construction: the token IS the cc-sandbox-runner SA's
#   token (Role: jobs/pods/exec/log in cc-sandbox only, no ClusterRole) — this
#   kubeconfig can make/exec/delete sandbox pods and nothing else, anywhere.
#
#   Written to /home/codyslab/.cc-sandbox-runner.kubeconfig, mode 0600 (same
#   posture as any other credential on this box — never under the repo, never
#   world-readable). Idempotent: re-running just rewrites the file with
#   whatever the Secret currently holds (a token rotation is picked up by
#   re-running this, not by editing the file).
#
#   Usage:  ./deploy/k3s/make-sandbox-kubeconfig.sh
# ============================================================================
set -euo pipefail

NS=cc-sandbox
SA=cc-sandbox-runner
SECRET=cc-sandbox-runner-token
OUT="/home/codyslab/.cc-sandbox-runner.kubeconfig"
SERVER="https://127.0.0.1:6443"
KUBECTL=(sudo k3s kubectl)

"${KUBECTL[@]}" -n "$NS" get secret "$SECRET" >/dev/null 2>&1 || {
  echo "FATAL: secret/$SECRET not found in namespace $NS — apply deploy/k3s/60-sandbox.yaml first" >&2
  exit 1
}

TOKEN=$("${KUBECTL[@]}" -n "$NS" get secret "$SECRET" -o go-template='{{.data.token | base64decode}}')
CA_FILE=$(mktemp)
trap 'rm -f "$CA_FILE"' EXIT
"${KUBECTL[@]}" -n "$NS" get secret "$SECRET" -o go-template='{{index .data "ca.crt"}}' | base64 -d > "$CA_FILE"

TMP_OUT=$(mktemp)
KUBECONFIG="$TMP_OUT" kubectl config set-cluster cc-sandbox \
  --server="$SERVER" --certificate-authority="$CA_FILE" --embed-certs=true >/dev/null
KUBECONFIG="$TMP_OUT" kubectl config set-credentials "$SA" --token="$TOKEN" >/dev/null
KUBECONFIG="$TMP_OUT" kubectl config set-context cc-sandbox \
  --cluster=cc-sandbox --user="$SA" --namespace="$NS" >/dev/null
KUBECONFIG="$TMP_OUT" kubectl config use-context cc-sandbox >/dev/null

mv "$TMP_OUT" "$OUT"
chmod 0600 "$OUT"
echo "wrote $OUT (namespace=$NS, server=$SERVER)"
