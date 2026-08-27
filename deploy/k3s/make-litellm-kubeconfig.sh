#!/usr/bin/env bash
# ============================================================================
# Extract the cc-litellm-operator ServiceAccount's token + cluster CA from
# deploy/k3s/31-litellm-rbac.yaml's Secret, and write a standalone kubeconfig
# for settings.litellm_kubeconfig (CC_LITELLM_KUBECONFIG) — the credential
# gateway/executor.py's litellm.apply_config_change handler re-renders
# configmap/cc-litellm-config and restarts deploy/cc-litellm with.
#
#   Namespace-scoped by construction: the token IS the cc-litellm-operator
#   SA's token (Role, no ClusterRole) — it can update ONE ConfigMap
#   (cc-litellm-config), patch ONE Deployment (cc-litellm) and read pods in
#   `central_command`, and nothing else, anywhere. It holds no secrets access at
#   all. Sibling of make-mcp-kubeconfig.sh, same idiom including the
#   go-template `{{index .data "ca.crt"}}` form — the naive `.data."ca.crt"`
#   form does not resolve the dotted key and is a live-found bug (do not
#   "simplify" back to it).
#
#   Written to /home/codyslab/.cc-litellm-operator.kubeconfig, mode 0600.
#   Idempotent: re-running rewrites the file with whatever the Secret
#   currently holds (a token rotation is picked up by re-running this, not by
#   editing the file).
#
#   2026-08-06: also mints a SECOND kubeconfig for cc-litellm-logreader — the
#   runtime's read-only identity for litellm_read_logs (pods get/list +
#   pods/log get ONLY, no configmaps/deployments/secrets). Kept distinct from
#   the write identity above because runtime/ tools may hold READ credentials
#   only; the Executor's write kubeconfig must never reach a runtime tool.
#   Written to /home/codyslab/.cc-litellm-logreader.kubeconfig, mode 0600.
#
#   Usage:  ./deploy/k3s/make-litellm-kubeconfig.sh
#           then put the path in the REPO-ROOT .env as
#           CC_LITELLM_KUBECONFIG=/home/codyslab/.cc-litellm-operator.kubeconfig
#           and restart cc-uvicorn (a merged change is not live until its
#           process restarts).
# ============================================================================
set -euo pipefail

NS=central_command
SERVER="https://127.0.0.1:6443"
KUBECTL=(sudo k3s kubectl)

# Mints ONE kubeconfig for a (ServiceAccount, token Secret, output path)
# triple. Called twice below: once for the Executor's write identity
# (cc-litellm-operator), once for the runtime's read-only log identity
# (cc-litellm-logreader, added 2026-08-06 for litellm-manager troubleshooting
# reads) — same shape, different Role, so one function instead of copy-paste.
mint() {
  local sa="$1" secret="$2" out="$3"

  "${KUBECTL[@]}" -n "$NS" get secret "$secret" >/dev/null 2>&1 || {
    echo "FATAL: secret/$secret not found in namespace $NS — apply deploy/k3s/31-litellm-rbac.yaml first" >&2
    exit 1
  }

  local token ca_file tmp_out
  token=$("${KUBECTL[@]}" -n "$NS" get secret "$secret" -o go-template='{{.data.token | base64decode}}')
  ca_file=$(mktemp)
  "${KUBECTL[@]}" -n "$NS" get secret "$secret" -o go-template='{{index .data "ca.crt"}}' | base64 -d > "$ca_file"

  tmp_out=$(mktemp)
  KUBECONFIG="$tmp_out" kubectl config set-cluster cc-litellm \
    --server="$SERVER" --certificate-authority="$ca_file" --embed-certs=true >/dev/null
  KUBECONFIG="$tmp_out" kubectl config set-credentials "$sa" --token="$token" >/dev/null
  KUBECONFIG="$tmp_out" kubectl config set-context cc-litellm \
    --cluster=cc-litellm --user="$sa" --namespace="$NS" >/dev/null
  KUBECONFIG="$tmp_out" kubectl config use-context cc-litellm >/dev/null

  rm -f "$ca_file"
  mv "$tmp_out" "$out"
  chmod 0600 "$out"
  echo "wrote $out (namespace=$NS, server=$SERVER)"
}

# Write identity — the Executor's, WRITE verbs (configmaps, deployments).
# Put the path in the REPO-ROOT .env as CC_LITELLM_KUBECONFIG.
mint cc-litellm-operator cc-litellm-operator-token \
  "/home/codyslab/.cc-litellm-operator.kubeconfig"

# Read-only identity — the runtime's, pods get/list + pods/log get ONLY.
# Put the path in the REPO-ROOT .env as CC_LITELLM_LOG_KUBECONFIG.
mint cc-litellm-logreader cc-litellm-logreader-token \
  "/home/codyslab/.cc-litellm-logreader.kubeconfig"
