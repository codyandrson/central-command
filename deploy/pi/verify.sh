#!/usr/bin/env bash
# ============================================================================
# Prove the Pi deployment stands on its own.
#
#   Run this ON THE PI. It checks two different things, and the second is the
#   one that matters for this deployment:
#
#     A. every service Central Command depends on is UP and answering here;
#     B. nothing in the configuration points at another host — i.e. the stack
#        keeps working with the rest of the homelab powered off.
#
#   (B) cannot be proven by "it works right now": the desktop is reachable over
#   Tailscale, so a stray desktop hostname would keep working today and fail
#   the first time that machine is off. So we grep the config instead of
#   trusting a green run.
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.."
# NEO4J_PASSWORD for the cypher-shell probe. Values are used, never echoed.
set -a; [[ -f deploy/pi/.env ]] && . ./deploy/pi/.env; set +a

PASS=0; FAIL=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }

echo
echo "== A. local services =="
check "postgres (spine)  127.0.0.1:5442" "docker exec cc-postgres pg_isready -U central_command"
check "litellm            127.0.0.1:4000" "curl -fsS http://127.0.0.1:4000/health/liveliness"
check "neo4j              127.0.0.1:7474" "curl -fsS http://127.0.0.1:7474"
check "graphiti MCP       127.0.0.1:8000" "curl -fsS http://127.0.0.1:8000/health"
check "n8n                127.0.0.1:5678" "curl -fsS http://127.0.0.1:5678/healthz"
check "control plane API  127.0.0.1:8080" "curl -fsS http://127.0.0.1:8080/health"
check "nerve cockpit      127.0.0.1:3080" "curl -fsS -o /dev/null http://127.0.0.1:3080"

echo
echo "== B. migrated data is present =="
check "spine: agents on the roster" \
  "test \"\$(docker exec cc-postgres psql -qtAX -U central_command -d central_command -c \"select count(*) from agent where role <> ''\")\" -gt 0"
check "spine: event log carried over" \
  "test \"\$(docker exec cc-postgres psql -qtAX -U central_command -d central_command -c 'select count(*) from audit_event')\" -gt 100"
check "n8n: the email facade workflow is active" \
  "test \"\$(docker exec cc-n8n-db psql -qtAX -U n8n -d n8n -c \"select count(*) from workflow_entity where name = 'cc-email-facade' and active\")\" = 1"
# The real test of N8N_ENCRYPTION_KEY: ask n8n to decrypt its own credential
# store. Exported to a file inside the container and grepped for the OAuth
# token's SHAPE — the secret itself is never printed, and the file is removed.
check "n8n: the Gmail credential DECRYPTS (the encryption key travelled)" \
  "docker exec cc-n8n sh -c 'n8n export:credentials --all --decrypted --output=/tmp/c.json >/dev/null 2>&1 && grep -q oauthTokenData /tmp/c.json; rc=\$?; rm -f /tmp/c.json; exit \$rc'"
# Run as functions, not eval'd strings: the SQL needs double quotes (LiteLLM's
# table names are CamelCase) and layering those through eval silently mangles
# the query into a false FAIL.
litellm_keys() {
  local n
  n=$(docker exec cc-litellm-db psql -qtAX -U llmproxy -d litellm \
        -c 'select count(*) from "LiteLLM_VerificationToken"' 2>/dev/null) || return 1
  [[ "${n:-0}" -gt 0 ]]
}
graph_nodes() {
  local n
  n=$(docker exec cc-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
        --format plain 'match (n) return count(n)' 2>/dev/null | tail -1) || return 1
  [[ "${n:-0}" -gt 0 ]]
}
if litellm_keys; then ok "litellm: virtual keys survived the salt"; else bad "litellm: virtual keys survived the salt"; fi
if graph_nodes;  then ok "graph: episodes present in neo4j";        else bad "graph: episodes present in neo4j";        fi

echo
echo "== C. standalone: nothing points off-box =="
# The desktop's hostnames must appear NOWHERE in runtime config. Tailscale
# would happily keep resolving them today and strand the Pi tomorrow.
#
# SETTINGS ONLY, not comments (2026-07-26): this matched a PROSE line in .env
# describing where the local models run, and went red on a file that configures
# nothing off-box. A permanently-red check is one everyone learns to scroll
# past — the same reasoning that parked section D below. A commented-out
# setting is inert too, so skipping `#` lines costs no real coverage.
if grep -rInE '^[^#]*(codys-lab-z690|100\.73\.68\.9)' .env web/.env deploy/pi/.env 2>/dev/null; then
  bad "a desktop hostname/IP is still referenced above"
else
  ok "no desktop hostname or IP in .env / web/.env / deploy/pi/.env (settings, not comments)"
fi
# Every CC_* endpoint must be loopback. Strip the scheme (and any userinfo, as
# in postgresql://user:pw@host/db) before testing the HOST — matching on the
# raw line let `http://localhost:...` read as non-loopback.
offhost="$(grep -hoE '^CC_[A-Z_]*(URL|DATABASE_URL)=.*' .env 2>/dev/null \
  | grep -vE '=[a-z+]+://([^@/]*@)?(localhost|127\.0\.0\.1)([:/]|$)' || true)"
if [[ -n "$offhost" ]]; then
  bad "non-loopback endpoint in .env:"; printf '        %s\n' "$offhost"
else
  ok "every CC_* endpoint resolves to this host"
fi

# The provider agents talk to. Since 2026-07-26 there is no enable flag and no
# ambient fallback: presence of these two IS the configuration, so their absence
# is not a degraded mode — it is LLMProviderNotConfigured raised on the first
# agent run. A deploy that forgets them looks healthy right up until something
# tries to think, which is the exact class of silent gap this script exists for.
# (Where they POINT is already covered by the loopback check above.)
missing_llm=""
for v in CC_LLM_BASE_URL CC_LLM_API_KEY; do
  grep -qE "^${v}=." .env 2>/dev/null || missing_llm+=" $v"
done
if [[ -n "$missing_llm" ]]; then
  bad "the LLM provider is not configured — missing/empty in .env:$missing_llm"
else
  ok "LLM provider configured (CC_LLM_BASE_URL + CC_LLM_API_KEY)"
fi

# == D. routing policy matches the declaration ==
# Not "does the proxy work" but "is the proxy configured the way this repo
# says". A silently-drifted adaptive router does not error — it degrades to a
# coin flip, which is precisely how it was found on 2026-07-26.
#
# PARKED (2026-07-26), not deleted. The check itself is correct and its logic is
# covered by tests/test_litellm_policy.py. It was disabled because
# model-preferences.yaml had not been applied: the adaptive router it was
# written for turned out to be config.yaml-only, and its complexity classifier
# turned out to correlate INVERSELY with real difficulty
# (see docs/superpowers/specs/2026-07-26-quality-router-migration-design.md).
#
# A verification script that is permanently red teaches everyone to ignore it,
# which costs more than the check is worth while the routing direction is
# undecided. Re-enable this line in the same change that applies a declaration.
#
# ⚠️ THE PREMISE ABOVE EXPIRED (verified 2026-07-29). The declaration IS applied:
# `python3 deploy/pi/litellm/policy.py --check` exits 0 against the live proxy
# ("routing policy matches model-preferences.yaml"). So this check would PASS,
# not hang red, and the condition this comment sets for re-enabling it
# ("in the same change that applies a declaration") has been met. Left parked
# rather than flipped because turning a deployment gate back on is an operator
# decision, not a doc cleanup — uncomment when you want verify.sh to guard
# routing drift again. (An earlier note in the journal claiming the file was
# "never applied" is the stale half of this same contradiction.)
#
# check "litellm: live routing policy == model-preferences.yaml" \
#   "python3 deploy/pi/litellm/policy.py --check"

echo
printf '  %d passed, %d failed\n\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
