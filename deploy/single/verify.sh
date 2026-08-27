#!/usr/bin/env bash
# ============================================================================
# Assert the single-node stack is actually up and actually configured.
#
#   Not a smoke test that prints "OK" if podman is running. Each check hits
#   the thing it names on the port the app will use, and the exit code is the
#   result. Run it after every `podman kube play`.
#
#   THE LIVE-COMPLETION CHECK IS SEPARATE. A stack can be perfectly deployed
#   and still have a wrong/expired upstream key, and those are different
#   failures with different fixes. So the default run proves the PROXY is up
#   and CONFIGURED (its model list contains our three aliases); pass
#   CC_VERIFY_LIVE=1 to additionally spend a real token on a completion.
#
#   Usage:  ./verify.sh            # deployment + configuration
#           CC_VERIFY_LIVE=1 ./verify.sh   # ... plus one real completion
# ============================================================================
set -uo pipefail   # NOT -e: every check must run, so the report is complete.

# Windows has no real python3: the WindowsApps stub answers `command -v` but
# exits 49 (2026-08-21 Windows validation, W2) — so probe by RUNNING it.
PY=""
for c in python3 python; do
  command -v "$c" >/dev/null 2>&1 && "$c" -c '' 2>/dev/null && { PY="$c"; break; }
done
[[ -n "$PY" ]] || PY="uv run --python 3.12 python"
# $PY may be multiple words (the uv fallback) — always invoke it unquoted: $PY -c ...

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$HERE/.env" ]] || { echo "FATAL: $HERE/.env not found" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
. "$HERE/.env"
set +a

: "${CC_POD_PREFIX:=cc-}"
: "${CC_NETWORK:=cc-single}"
: "${CC_PG_PORT:=5442}"
: "${CC_LITELLM_PORT:=4000}"
: "${CC_GRAPHITI_PORT:=8000}"
: "${CC_NEO4J_BOLT_PORT:=7687}"
: "${CC_NEO4J_HTTP_PORT:=7474}"
: "${CC_N8N_PORT:=5678}"
: "${CC_CRAWLER_PORT:=8091}"
: "${CC_ENABLE_CRAWLER:=1}"
: "${CC_ENABLE_SANDBOX:=1}"
: "${CC_SANDBOX_RUNNER_PORT:=8090}"
: "${CC_VERIFY_MAX_WAIT:=600}"

PASS=0; FAIL=0; SKIP=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
skip() { printf '  \033[33mSKIP\033[0m  %s\n' "$1"; SKIP=$((SKIP+1)); }

check() { # check <description> <command...>
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$desc"; else bad "$desc"; fi
}

# Poll until a command succeeds. Startup order is not deterministic under
# kube play (there are no readiness gates), so a one-shot check would be a
# race, not an assertion.
wait_for() { # wait_for <seconds> <command...>
  # CC_VERIFY_MAX_WAIT caps every poll. Default is above the largest budget
  # here, so a normal run is unchanged; `setup.sh diagnose` sets it low,
  # because on a dead stack the honest answer arrives immediately and the
  # patient answer takes a quarter of an hour of a broken operator's time.
  local secs="$1"; (( secs > CC_VERIFY_MAX_WAIT )) && secs="$CC_VERIFY_MAX_WAIT"
  local deadline=$(( SECONDS + secs )); shift
  while (( SECONDS < deadline )); do
    "$@" >/dev/null 2>&1 && return 0
    sleep 3
  done
  return 1
}

echo "== network and pods (prefix=${CC_POD_PREFIX}, network=${CC_NETWORK})"

# Capture, THEN grep. `podman ... | grep -q` exits at the first match, podman
# takes SIGPIPE, and under pipefail the pipeline reports failure on success.
nets="$(podman network ls --format '{{.Name}}' 2>/dev/null)"
check "network ${CC_NETWORK} exists" grep -qx "$CC_NETWORK" <<<"$nets"

pods="$(podman pod ps --format '{{.Name}} {{.Status}}' 2>/dev/null)"
for p in postgres litellm-db litellm-redis litellm neo4j graphiti; do
  name="${CC_POD_PREFIX}${p}"
  line="$(grep -E "^${name} " <<<"$pods")"
  if [[ -z "$line" ]]; then
    bad "pod ${name} exists"
  elif [[ "$line" == *Running* ]]; then
    ok "pod ${name} Running"
  else
    bad "pod ${name} Running (is: ${line#* })"
  fi
done

echo
echo "== the spine"
# psql from inside the container: it is guaranteed present there, and the
# host is not guaranteed to have a client at all (Windows especially).
PG_EXEC=(podman exec "${CC_POD_PREFIX}postgres-postgres" psql -U central_command -d central_command -tAc)
if wait_for 90 "${PG_EXEC[@]}" 'select 1'; then
  ok "postgres accepts queries"
  # The schema is auto-loaded on a FRESH database only, by initdb. If these
  # tables are missing the container came up on a volume that skipped it, and
  # nothing will apply it later.
  tables="$("${PG_EXEC[@]}" "select tablename from pg_tables where schemaname='public'" 2>/dev/null)"
  for t in agent session proposal work_item audit_event task; do
    check "schema table ${t} present" grep -qx "$t" <<<"$tables"
  done
else
  bad "postgres accepts queries"
fi
# The port contract the app depends on: CC_DATABASE_URL points at loopback.
check "postgres published on 127.0.0.1:${CC_PG_PORT}" \
  bash -c "exec 3<>/dev/tcp/127.0.0.1/${CC_PG_PORT}"

echo
echo "== litellm"
LL="http://127.0.0.1:${CC_LITELLM_PORT}"
if wait_for 180 curl -fsS "${LL}/health/liveliness"; then
  ok "proxy answers /health/liveliness"
  # Configured, not merely alive: the three aliases this profile exists to
  # provide must be in the model list, under the master key.
  models="$(curl -fsS -H "Authorization: Bearer ${LITELLM_MASTER_KEY:-}" "${LL}/v1/models" 2>/dev/null)"
  for m in cc-default graphiti-llm cc-embedding; do
    check "model alias ${m} registered" grep -qF "\"$m\"" <<<"$models"
  done
else
  bad "proxy answers /health/liveliness"
  skip "model aliases (proxy is down)"
fi

if [[ "${CC_VERIFY_LIVE:-0}" == "1" ]]; then
  body="$(curl -fsS -m 60 -H "Authorization: Bearer ${LITELLM_MASTER_KEY:-}" \
    -H 'Content-Type: application/json' \
    -d '{"model":"cc-default","messages":[{"role":"user","content":"reply with the single word: ok"}],"max_tokens":8}' \
    "${LL}/v1/chat/completions" 2>/dev/null)"
  check "live completion through cc-default" grep -qF '"content"' <<<"$body"

  emb="$(curl -fsS -m 60 -H "Authorization: Bearer ${LITELLM_MASTER_KEY:-}" \
    -H 'Content-Type: application/json' \
    -d '{"model":"cc-embedding","input":"dimension probe"}' \
    "${LL}/v1/embeddings" 2>/dev/null)"
  # CC_EMBED_DIM is written into the Neo4j vector index. A mismatch does not
  # error at write time — it corrupts retrieval silently — so prove it here.
  # Parse with python, not text-mangling: the first element is glued to
  # `"embedding":[` so a line-count regex reads 1023 for a true 1024 — and the
  # "fix" a user would infer (set CC_EMBED_DIM=1023) corrupts the index this
  # check exists to protect (rehearsal finding F3, 2026-08-21).
  dim="$($PY -c 'import json,sys; print(len(json.load(sys.stdin)["data"][0]["embedding"]))' <<<"$emb" 2>/dev/null || echo parse-error)"
  if [[ "$dim" == "${CC_EMBED_DIM:-}" ]]; then
    ok "embedding dimension is ${CC_EMBED_DIM} as configured"
  else
    bad "embedding dimension: configured ${CC_EMBED_DIM:-unset}, endpoint returned ${dim}"
  fi
else
  skip "live completion + embedding dimension (set CC_VERIFY_LIVE=1)"
fi

echo
echo "== the graph"
check "neo4j bolt reachable on 127.0.0.1:${CC_NEO4J_BOLT_PORT}" \
  bash -c "exec 3<>/dev/tcp/127.0.0.1/${CC_NEO4J_BOLT_PORT}"
if wait_for 180 curl -fsS "http://127.0.0.1:${CC_NEO4J_HTTP_PORT}/"; then
  ok "neo4j http answers"
else
  bad "neo4j http answers"
fi

# The derived image patches FastMCP's DNS-rebinding allowlist to exactly
# {graphiti, localhost, 127.0.0.1}; any other Host gets HTTP 421. Reaching it
# as 127.0.0.1 is on the list, so a 421 here means the patch is missing.
gh="$(wait_for 240 curl -fsS "http://127.0.0.1:${CC_GRAPHITI_PORT}/health" && \
      curl -fsS "http://127.0.0.1:${CC_GRAPHITI_PORT}/health" 2>/dev/null)"
if [[ -n "$gh" ]]; then
  ok "graphiti MCP /health answers"
else
  bad "graphiti MCP /health answers"
fi
# Graphiti holds a bolt session open to Neo4j; a config or credential error
# shows up in its log, not in /health.
glog="$(podman logs "${CC_POD_PREFIX}graphiti-graphiti" 2>&1 | tail -200)"
if grep -qiE 'authentication failure|unable to retrieve routing|could not connect|traceback' <<<"$glog"; then
  bad "graphiti log is clean of connection/auth errors"
else
  ok "graphiti log is clean of connection/auth errors"
fi

# n8n is optional — absent is not a failure, only broken-while-present is.
echo
echo "== n8n (optional)"
if grep -qE "^${CC_POD_PREFIX}n8n " <<<"$pods"; then
  if wait_for 180 curl -fsS "http://127.0.0.1:${CC_N8N_PORT}/healthz"; then
    ok "n8n answers /healthz"
  else
    bad "n8n answers /healthz"
  fi
else
  skip "n8n not played (no n8n-backed integration selected)"
fi

# The crawler follows the n8n pattern: absent-because-disabled is a skip,
# broken-while-enabled is a failure. The timeout is deliberately the largest
# in this file — the image is Chromium, and a cold first start is slow enough
# that a 60s check would report a healthy pod as dead.
echo
echo "== crawler (optional, CC_ENABLE_CRAWLER=${CC_ENABLE_CRAWLER})"
if [[ "$CC_ENABLE_CRAWLER" == "1" ]]; then
  if grep -qE "^${CC_POD_PREFIX}crawler " <<<"$pods"; then
    ok "pod ${CC_POD_PREFIX}crawler exists"
    if wait_for 300 curl -fsS "http://127.0.0.1:${CC_CRAWLER_PORT}/healthz"; then
      ok "crawler answers /healthz"
    else
      bad "crawler answers /healthz"
    fi
  else
    bad "pod ${CC_POD_PREFIX}crawler exists"
  fi
else
  skip "crawler not played (CC_ENABLE_CRAWLER=0)"
fi

# The sandbox has no pod: sandbox CONTAINERS are created on demand by the
# runner, which is a HOST process the operator starts alongside the API. So
# the deployable artifact to assert is the IMAGE; a runner that is not
# answering yet is a skip, not a failure.
echo
echo "== sandbox (optional, CC_ENABLE_SANDBOX=${CC_ENABLE_SANDBOX})"
if [[ "$CC_ENABLE_SANDBOX" == "1" ]]; then
  # Capture, THEN grep — `podman images | grep -q` inverts under pipefail.
  imgs="$(podman images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null)"
  check "image localhost/cc-sandbox:1 present" grep -qx 'localhost/cc-sandbox:1' <<<"$imgs"
  # The runner exposes no /health route (it is /sessions and friends), so the
  # honest liveness question here is "is anything listening".
  if bash -c "exec 3<>/dev/tcp/127.0.0.1/${CC_SANDBOX_RUNNER_PORT}" 2>/dev/null; then
    ok "sandbox runner answers on 127.0.0.1:${CC_SANDBOX_RUNNER_PORT}"
  else
    skip "sandbox runner not answering on 127.0.0.1:${CC_SANDBOX_RUNNER_PORT} (a host process — see README)"
  fi
else
  skip "sandbox disabled (CC_ENABLE_SANDBOX=0)"
fi

echo
printf 'passed %d, failed %d, skipped %d\n' "$PASS" "$FAIL" "$SKIP"
[[ "$FAIL" -eq 0 ]]
