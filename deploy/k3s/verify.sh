#!/usr/bin/env bash
# ============================================================================
# Prove the k3s deployment is correct. Run this ON THE PI.
#
#   Ported from deploy/pi/verify.sh, which checked the Docker stack. Every
#   assertion it made is still made here; the access mechanism changed and two
#   sections gained new obligations.
#
#   It checks four things:
#     A. every service Central Command depends on is UP and answering;
#     B. the migrated data is present and DECRYPTABLE;
#     C. PLACEMENT matches the design — the float/pin split is the whole point
#        of the migration, and "it works right now" does not prove it. A pod
#        that happened to land on the right node is indistinguishable from one
#        that was pinned there, until the day it moves;
#     D. the host's own config still points at loopback.
#
#   On (D) and the old "nothing points off-box" rule: the premise CHANGED on
#   2026-07-31. The deployment is deliberately two nodes now, so the chromebox
#   is a legitimate dependency and the Pi no longer stands alone. What must
#   still hold is narrower and still worth guarding: every CC_* endpoint in .env
#   resolves to LOOPBACK, because ServiceLB binds the real port on this node
#   whichever node the pod runs on. If that check ever goes red it means someone
#   hard-coded a node address and quietly broke failover.
# ============================================================================
set -uo pipefail

# --clean-install: section B asserts the stores are ALIVE, AUTHENTICATED and
# schema-loaded, without asserting migrated instance data. A fresh install has
# a near-empty event log, no n8n workflows (instance data — README.md phase 8)
# and an empty graph BY DESIGN; failing on those would train people to ignore
# red. The roster check stays in both modes: schema.sql seeds it, so an empty
# roster on a clean install is a real failure (the 2026-07-26 seed-guard bug).
CLEAN=0
[[ "${1:-}" == "--clean-install" ]] && CLEAN=1

cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
NS=central-command
K=(sudo k3s kubectl -n "$NS")

set -a
# shellcheck disable=SC1091
[[ -f deploy/pi/.env ]] && . deploy/pi/.env
set +a

# Topology is ROLE LABELS, not hostnames (2026-08-27 contract): manifests pin
# by cc-role/anchor (small stateful stores, control plane) and cc-role/compute
# (JVM/gVisor/Chromium muscle). Resolve each role to its node once; every
# placement assertion below compares against these, so a node swap is a
# relabel, not a verify.sh edit. SSH target for the compute node comes from
# deploy/pi/.env (CC_COMPUTE_SSH), defaulting to today's chromebox.
ANCHOR_NODE="$(sudo k3s kubectl get nodes -l cc-role/anchor=true -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
COMPUTE_NODE="$(sudo k3s kubectl get nodes -l cc-role/compute=true -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
COMPUTE_SSH="${CC_COMPUTE_SSH:-chromebox_admin@100.113.118.28}"

PASS=0; FAIL=0; SKIP=0
ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
skip() { printf '  \033[33mskip\033[0m  %s\n' "$1"; SKIP=$((SKIP+1)); }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }

echo
echo "== A. services are up and answering =="
# The floating services are reached at the SAME loopback addresses as before the
# migration — that is ServiceLB doing its job, and testing them from the host is
# exactly the property worth asserting.
check "postgres (spine)  127.0.0.1:5442" "${K[*]} exec deploy/cc-postgres -- pg_isready -U central_command"
check "litellm            127.0.0.1:4000" "curl -fsS http://127.0.0.1:4000/health/liveliness"
check "litellm: live routing policy == model-preferences.yaml" "python3 deploy/pi/litellm/policy.py --check"
check "graphiti MCP       127.0.0.1:8000" "curl -fsS http://127.0.0.1:8000/health"
check "n8n                127.0.0.1:5678" "curl -fsS http://127.0.0.1:5678/healthz"
check "crawler            127.0.0.1:8091" "curl -fsS http://127.0.0.1:8091/healthz"
check "control plane API  127.0.0.1:8080" "curl -fsS http://127.0.0.1:8080/health"
check "victorialogs       127.0.0.1:9428" "curl -fsS http://127.0.0.1:9428/health"
check "nerve cockpit      127.0.0.1:3080" "curl -fsS -o /dev/null http://127.0.0.1:3080"
# neo4j is ClusterIP-only by design now (the app reaches the graph solely
# through Graphiti's MCP endpoint), so it is probed from INSIDE the cluster.
# Probing it on 127.0.0.1:7474 would be a false FAIL — and adding a host binding
# to make that check pass would be a real regression.
check "neo4j              ClusterIP (in-cluster only)" \
  "${K[*]} exec deploy/cc-neo4j -- wget -qO- http://localhost:7474"

echo
if [[ "$CLEAN" == "1" ]]; then
  echo "== B. stores initialized (clean-install mode — no instance data asserted) =="
else
  echo "== B. migrated data is present and decryptable =="
fi
check "spine: agents on the roster" \
  "test \"\$(${K[*]} exec deploy/cc-postgres -- psql -qtAX -U central_command -d central_command -c \"select count(*) from agent where role <> ''\")\" -gt 0"
if [[ "$CLEAN" == "1" ]]; then
  # -ge 0 is not vacuous: it proves schema.sql actually loaded (the table
  # exists and answers), which only happens on a FRESH cc-pgdata PVC.
  check "spine: schema loaded (event log queryable; fresh log may be near-empty)" \
    "test \"\$(${K[*]} exec deploy/cc-postgres -- psql -qtAX -U central_command -d central_command -c 'select count(*) from audit_event')\" -ge 0"
  skip "n8n: facade workflow + Gmail credential — instance data, restored in README.md phase 8"
  # The RUNNING process's mode, not the unit file's: a go-live drop-in in
  # cc-uvicorn.service.d/ outlives a teardown and beats Environment= in the
  # unit (2026-08-29: a "clean" install executed its demo approval live).
  # systemd's value is in the process env; absent that, .env decides.
  check "executor: the running API is in dry_run (a leftover go-live drop-in would make it live)" \
    "mode=\$(sudo tr '\\0' '\\n' </proc/\$(systemctl show -p MainPID --value cc-uvicorn)/environ | sed -n 's/^CC_EXECUTOR_MODE=//p'); [[ -z \$mode ]] && mode=\$(sed -n 's/^CC_EXECUTOR_MODE=//p' .env); [[ \$mode == dry_run ]]"
  skip "n8n: (run without --clean-install after the phase-8 restore to assert both)"
else
  check "spine: event log carried over" \
    "test \"\$(${K[*]} exec deploy/cc-postgres -- psql -qtAX -U central_command -d central_command -c 'select count(*) from audit_event')\" -gt 100"
  check "n8n: the email facade workflow is active" \
    "test \"\$(${K[*]} exec deploy/cc-n8n-db -- psql -qtAX -U ${N8N_DB_USER:-n8n} -d ${N8N_DB_NAME:-n8n} -c \"select count(*) from workflow_entity where name = 'cc-email-facade' and active\")\" = 1"
  # The real test of N8N_ENCRYPTION_KEY: ask n8n to decrypt its own credential
  # store. Written to a file inside the pod and grepped for the OAuth token's
  # SHAPE — the secret itself is never printed, and the file is removed.
  check "n8n: the Gmail credential DECRYPTS (the encryption key travelled)" \
    "${K[*]} exec deploy/cc-n8n -- sh -c 'n8n export:credentials --all --decrypted --output=/tmp/c.json >/dev/null 2>&1 && grep -q oauthTokenData /tmp/c.json; rc=\$?; rm -f /tmp/c.json; exit \$rc'"
fi
# Run as functions, not eval'd strings: the SQL needs double quotes (LiteLLM's
# table names are CamelCase) and layering those through eval silently mangles
# the query into a false FAIL.
litellm_keys() {
  local n
  n=$("${K[@]}" exec deploy/cc-litellm-db -- psql -qtAX -U llmproxy -d litellm \
        -c 'select count(*) from "LiteLLM_VerificationToken"' 2>/dev/null) || return 1
  [[ "${n:-0}" -gt 0 ]]
}
graph_nodes() {
  local n
  n=$("${K[@]}" exec deploy/cc-neo4j -- cypher-shell -u neo4j -p "${NEO4J_PASSWORD:-}" \
        --format plain 'match (n) return count(n)' 2>/dev/null | tail -1) || return 1
  if [[ "$CLEAN" == "1" ]]; then
    # A numeric answer proves connectivity AND the password; an empty graph is
    # the designed clean-install state, so 0 passes here.
    [[ "${n:-x}" =~ ^[0-9]+$ ]]
  else
    [[ "${n:-0}" -gt 0 ]]
  fi
}
# The LiteLLM trio survives a clean install untouched (README.md), so its keys
# must be present and decryptable in BOTH modes.
if litellm_keys; then ok "litellm: virtual keys survived the salt"; else bad "litellm: virtual keys survived the salt"; fi
if [[ "$CLEAN" == "1" ]]; then
  if graph_nodes; then ok "graph: neo4j answers cypher (fresh graph may be empty)"; else bad "graph: neo4j answers cypher (fresh graph may be empty)"; fi
else
  if graph_nodes; then ok "graph: episodes survived the arm64 -> amd64 move"; else bad "graph: episodes survived the arm64 -> amd64 move"; fi
fi

echo
echo "== C. placement matches the design =="
# The float/pin split IS the migration. Assert it explicitly rather than
# inferring it from a green service check. Roles first: an unlabeled node
# makes every pinned pod Pending with no event that names the cause.
if [[ -n "$ANCHOR_NODE" && -n "$COMPUTE_NODE" ]]; then
  ok "role labels resolve: anchor=$ANCHOR_NODE compute=$COMPUTE_NODE"
else
  bad "role labels missing — label the nodes: kubectl label node <anchor> cc-role/anchor=true ; kubectl label node <compute> cc-role/compute=true"
fi
node_of() { "${K[@]}" get pod -l "app=$1" -o jsonpath='{.items[0].spec.nodeName}' 2>/dev/null; }
pinned() {  # pinned <app> <expected-node>
  local got; got="$(node_of "$1")"
  if [[ "$got" == "$2" ]]; then ok "$1 pinned to $2"; else bad "$1 should be on $2 but is on '${got:-<none>}'"; fi
}
for a in cc-postgres cc-litellm-db cc-litellm-redis cc-n8n cc-n8n-db; do pinned "$a" "$ANCHOR_NODE"; done
pinned cc-neo4j "$COMPUTE_NODE"
# Same call as neo4j: stateful, so pinned, and off the anchor. Accepted
# consequence: compute node down = log console down (the logs themselves
# survive on each node).
pinned cc-vlogs "$COMPUTE_NODE"
# The collector must cover EVERY node or a whole host's logs silently vanish
# from the console — desired comes from the node count, so compare, don't count.
fb="$("${K[@]}" get ds cc-fluentbit -o jsonpath='{.status.desiredNumberScheduled} {.status.numberReady}' 2>/dev/null)"
if [[ -n "$fb" && "${fb% *}" -gt 0 && "${fb% *}" == "${fb#* }" ]]; then
  ok "fluent-bit collector ready on every node (${fb#* }/${fb% *})"
else
  bad "fluent-bit collector not covering every node (ready/desired: ${fb:-<none>})"
fi
# cc-crawler is compute-REQUIRED (Chromium never runs on the anchor) — the
# accepted consequence is that browser-rendered import is down when the
# compute node is; rungs 0/1 of the import ladder don't need it.
pinned cc-crawler "$COMPUTE_NODE"
# The floating pair is NOT asserted to a node — that would defeat the point.
# What matters is that they PREFER the compute node and can run on either.
for a in cc-litellm cc-graphiti; do
  got="$(node_of "$a")"
  if [[ "$got" == "$COMPUTE_NODE" ]]; then
    ok "$a floating, currently on $COMPUTE_NODE (preferred)"
  elif [[ "$got" == "$ANCHOR_NODE" ]]; then
    ok "$a floating, currently on $ANCHOR_NODE (fallback — expected only if the compute node is down/cordoned)"
  else
    bad "$a is not scheduled anywhere (got '${got:-<none>}')"
  fi
done
# A pod holding a local-path PVC can never move, so an unbound PVC is a pod that
# will never start.
unbound="$("${K[@]}" get pvc -o jsonpath='{range .items[*]}{.metadata.name}={.status.phase} {end}' 2>/dev/null | tr ' ' '\n' | grep -v '=Bound$' | grep -v '^$' || true)"
if [[ -z "$unbound" ]]; then ok "every PVC is Bound"; else bad "unbound PVCs: $unbound"; fi
# The floating image must exist on BOTH nodes under the exact reference the
# kubelet resolves. Capture then grep — `ctr ... | grep -q` inverts under
# pipefail (grep exits early, ctr takes SIGPIPE).
REF=docker.io/library/cc-graphiti:1.0.2-anthropic
pi_i="$(sudo k3s ctr -n k8s.io images ls -q 2>/dev/null)"
cb_i="$(ssh -o ConnectTimeout=10 "$COMPUTE_SSH" 'sudo k3s ctr -n k8s.io images ls -q' 2>/dev/null)"
if grep -qx "$REF" <<<"$pi_i" && grep -qx "$REF" <<<"$cb_i"; then
  ok "cc-graphiti image present on BOTH nodes (failover would not ImagePullBackOff)"
else
  bad "cc-graphiti image missing on a node — floating would break on failover"
fi
# The RuntimeClass object applies unconditionally, so a chromebox missing the
# runsc HOST half stays green everywhere until the first sandbox Job hangs at
# a "failed to create shim" event, weeks later. Assert the host half here.
gvisor_ok="$(ssh -o ConnectTimeout=10 "$COMPUTE_SSH" \
  'test -x /usr/local/bin/runsc \
   && sudo grep -q "runtimes\.runsc" /var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.tmpl \
   && echo yes' 2>/dev/null)"
if [[ "$gvisor_ok" == "yes" ]]; then
  ok "gvisor: runsc installed + registered on the compute node (sandbox Jobs can run)"
else
  bad "gvisor: runsc missing/unregistered on the compute node — run deploy/k3s/install-gvisor.sh"
fi

echo
echo "== D. host config still points at loopback =="
# Every CC_* endpoint must be loopback. Strip the scheme (and any userinfo, as
# in postgresql://user:pw@host/db) before testing the HOST — matching on the
# raw line let `http://localhost:...` read as non-loopback.
#
# This is what proves ServiceLB is carrying the floating services: if someone
# "fixes" a connection problem by pointing .env at a node's tailnet address, the
# stack keeps working today and stops failing over tomorrow. That is exactly the
# class of silent regression this section exists to catch.
#
# TWO EXEMPTIONS, both proven by the 2026-08-23 clean-install run, which scored
# "29 passed, 1 failed" on a healthy stack and made the documented zero-failure
# gate unachievable:
#   * EXTERNAL SaaS by design — CC_JIRA_*, CC_CONFLUENCE_*, CC_FORGE_* point at
#     atlassian.net (or a vendor host) and CANNOT be loopback. They are not
#     carried by ServiceLB and never will be, so flagging them tests nothing.
#   * EMPTY values — `CC_LLM_PROXY_UI_URL=` is "not configured", not
#     "misconfigured"; the presence checks below are what catch a required var.
# CC_LLM_PROXY_UI_URL, CC_N8N_UI_URL, CC_VLOGS_UI_URL and CC_NEO4J_BROWSER_URL
# are exempt for the same reason: the app never calls them — they are the
# Systems view's "Open →" links the cockpit hands to the operator's BROWSER
# (central_command/api/systems.py), so they must be tailnet addresses, not
# loopback.
offhost="$(grep -hoE '^CC_[A-Z_]*(URL|DATABASE_URL)=.+' .env 2>/dev/null \
  | grep -vE '^CC_(JIRA|CONFLUENCE|FORGE)_|^CC_(LLM_PROXY|N8N|VLOGS)_UI_URL=|^CC_NEO4J_BROWSER_URL=' \
  | grep -vE '=[a-z+]+://([^@/]*@)?(localhost|127\.0\.0\.1)([:/]|$)' || true)"
if [[ -n "$offhost" ]]; then
  bad "non-loopback endpoint in .env:"; printf '        %s\n' "$offhost"
else
  ok "every CC_* endpoint resolves to loopback (ServiceLB is carrying the floats)"
fi
# Presence of these two IS the LLM configuration — there is no enable flag and
# no ambient fallback, so their absence is not a degraded mode but
# LLMProviderNotConfigured raised on the first agent run. A deploy that forgets
# them looks healthy right up until something tries to think.
missing_llm=""
for v in CC_LLM_BASE_URL CC_LLM_API_KEY; do
  grep -qE "^${v}=." .env 2>/dev/null || missing_llm+=" $v"
done
if [[ -n "$missing_llm" ]]; then
  bad "the LLM provider is not configured — missing/empty in .env:$missing_llm"
else
  ok "LLM provider configured (CC_LLM_BASE_URL + CC_LLM_API_KEY)"
fi

echo
if [[ "$SKIP" -gt 0 ]]; then
  printf '  %d passed, %d failed, %d skipped (clean-install mode)\n\n' "$PASS" "$FAIL" "$SKIP"
else
  printf '  %d passed, %d failed\n\n' "$PASS" "$FAIL"
fi
[[ "$FAIL" -eq 0 ]]
