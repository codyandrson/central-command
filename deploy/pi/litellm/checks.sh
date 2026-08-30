#!/usr/bin/env bash
# ============================================================================
# Is the LiteLLM proxy healthy AND still serving what the deployment needs?
#
#   The SAME script runs as the pre-check, the post-check and the rollback
#   re-check of gateway/executor.py's litellm.apply_config_change arc — and it
#   is a fine thing to run by hand:  ./deploy/pi/litellm/checks.sh
#
#   Exit 0 = green. Any failure exits nonzero at the FIRST failed check with a
#   line naming it, because the Executor puts this output on the proposal.
#
#   The master key comes from LITELLM_MASTER_KEY, falling back to
#   deploy/pi/.env (gitignored). It is never printed.
# ============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${CC_LITELLM_URL:-http://127.0.0.1:4000}"
REQUIRED_ALIASES=(cc-default graphiti-llm cc-embedding)

fail() { echo "CHECK FAILED: $*" >&2; exit 1; }

KEY="${LITELLM_MASTER_KEY:-}"
if [[ -z "$KEY" && -f "$HERE/../.env" ]]; then
  KEY="$(grep -m1 '^LITELLM_MASTER_KEY=' "$HERE/../.env" | cut -d= -f2-)"
fi
[[ -n "$KEY" ]] || fail "master key: LITELLM_MASTER_KEY not in the environment or deploy/pi/.env"

# 1. liveliness — the proxy answers at all.
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/health/liveliness")" \
  || fail "liveliness: could not reach $BASE"
[[ "$code" == "200" ]] || fail "liveliness: $BASE/health/liveliness returned HTTP $code"
echo "ok  liveliness"

# 2. the aliases nothing may drop: cc-default (every agent run), graphiti-llm
#    (graph extraction), cc-embedding (the embedding ROLE — Graphiti has NO
#    embedding fallback, so an embedder outage is a graph WRITE outage).
info="$(curl -s --max-time 20 -H "Authorization: Bearer $KEY" "$BASE/model/info")" \
  || fail "model/info: could not reach $BASE"
printf '%s' "$info" | python3 -c '
import json, sys
try:
    names = {m.get("model_name") for m in json.load(sys.stdin).get("data", [])}
except Exception as e:
    sys.exit(f"model/info: unparseable response ({e})")
missing = [a for a in sys.argv[1:] if a not in names]
if missing:
    sys.exit("model/info: required alias(es) missing: " + ", ".join(missing))
' "${REQUIRED_ALIASES[@]}" || fail "model/info alias check (reason on the line above)"
echo "ok  model/info carries ${REQUIRED_ALIASES[*]}"

# 3. real inference — a 1-token completion on cc-default, the exact route an
#    agent run takes (same shape as heartbeat/probes.py::probe_llm).
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 \
  -X POST "$BASE/v1/messages" \
  -H "x-api-key: $KEY" -H 'anthropic-version: 2023-06-01' \
  -H 'content-type: application/json' \
  -d '{"model":"cc-default","max_tokens":1,"messages":[{"role":"user","content":"ping"}]}')" \
  || fail "completion: could not reach $BASE"
[[ "$code" == "200" ]] || fail "completion: 1-token cc-default completion returned HTTP $code"
echo "ok  cc-default answers a 1-token completion"

# 4. live routing still matches the declared policy (model-preferences.yaml).
out="$(python3 "$HERE/policy.py" --check 2>&1)" || fail "policy.py --check: $out"
echo "ok  routing policy matches model-preferences.yaml"

echo "all checks green"
