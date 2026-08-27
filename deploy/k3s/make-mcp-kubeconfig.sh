#!/usr/bin/env bash
# ============================================================================
# Extract the cc-mcp-deployer ServiceAccount's token + cluster CA from
# deploy/k3s/61-mcp.yaml's Secret, and write a standalone kubeconfig for
# settings.mcp_deploy_kubeconfig (CC_MCP_DEPLOY_KUBECONFIG) — the credential
# gateway/executor.py's mcp.server_deploy handler applies manifests with.
#
#   Namespace-scoped by construction: the token IS the cc-mcp-deployer SA's
#   token (Role: deployments/services/pods in cc-mcp only, no ClusterRole) —
#   this kubeconfig can create/inspect a Deployment+Service in cc-mcp and
#   nothing else, anywhere. Sibling of make-sandbox-kubeconfig.sh, same idiom
#   including the go-template `{{index .data "ca.crt"}}` form — the naive
#   `.data."ca.crt"` form does not resolve the dotted key and is a live-found
#   bug (do not "simplify" back to it).
#
#   Written to /home/codyslab/.cc-mcp-deployer.kubeconfig, mode 0600. Idempotent:
#   re-running rewrites the file with whatever the Secret currently holds (a
#   token rotation is picked up by re-running this, not by editing the file).
#
#   Usage:  ./deploy/k3s/make-mcp-kubeconfig.sh
# ============================================================================
set -euo pipefail

NS=cc-mcp
SA=cc-mcp-deployer
SECRET=cc-mcp-deployer-token
OUT="/home/codyslab/.cc-mcp-deployer.kubeconfig"
SERVER="https://127.0.0.1:6443"
KUBECTL=(sudo k3s kubectl)

"${KUBECTL[@]}" -n "$NS" get secret "$SECRET" >/dev/null 2>&1 || {
  echo "FATAL: secret/$SECRET not found in namespace $NS — apply deploy/k3s/61-mcp.yaml first" >&2
  exit 1
}

TOKEN=$("${KUBECTL[@]}" -n "$NS" get secret "$SECRET" -o go-template='{{.data.token | base64decode}}')
CA_FILE=$(mktemp)
trap 'rm -f "$CA_FILE"' EXIT
"${KUBECTL[@]}" -n "$NS" get secret "$SECRET" -o go-template='{{index .data "ca.crt"}}' | base64 -d > "$CA_FILE"

TMP_OUT=$(mktemp)
KUBECONFIG="$TMP_OUT" kubectl config set-cluster cc-mcp \
  --server="$SERVER" --certificate-authority="$CA_FILE" --embed-certs=true >/dev/null
KUBECONFIG="$TMP_OUT" kubectl config set-credentials "$SA" --token="$TOKEN" >/dev/null
KUBECONFIG="$TMP_OUT" kubectl config set-context cc-mcp \
  --cluster=cc-mcp --user="$SA" --namespace="$NS" >/dev/null
KUBECONFIG="$TMP_OUT" kubectl config use-context cc-mcp >/dev/null

mv "$TMP_OUT" "$OUT"
chmod 0600 "$OUT"
echo "wrote $OUT (namespace=$NS, server=$SERVER)"
