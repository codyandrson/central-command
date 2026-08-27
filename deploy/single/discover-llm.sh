#!/usr/bin/env bash
# ============================================================================
# Probe the user's OpenAI-compatible endpoint — the FIRST step of setup
# (2026-08-21 setup & onboarding design, decisions 2 and 3).
#
#   Deterministic on purpose: the /setup skill (Claude Code — a hard
#   prerequisite of setup) drives this script rather than probing freehand,
#   so every install answers these questions the same way.
#
#   Usage (endpoint from deploy/single/.env, or override via env vars):
#     ./discover-llm.sh models              # list model ids the endpoint serves
#     ./discover-llm.sh chat  <model-id>    # prove a real completion works
#     ./discover-llm.sh embed <model-id>    # embed one string, print DIMENSION
#
#   TWO RUNGS, and the difference is the diagnosis (2026-08-25, LiteLLM-first):
#
#     --proxy   target THIS STACK's LiteLLM on 127.0.0.1:${CC_LITELLM_PORT}
#               with the master key from .env, and probe the ALIASES:
#                 ./discover-llm.sh --proxy chat  cc-default
#                 ./discover-llm.sh --proxy embed cc-embedding
#               This is the normal validation path — it proves what production
#               actually uses (spine -> proxy -> upstream), not a path nothing
#               takes at runtime.
#
#     (direct)  the default. Used for two jobs only: the phase-1 LISTING (you
#               cannot list an upstream's models through a proxy that has not
#               registered them yet), and TROUBLESHOOTING — when a --proxy
#               probe fails, re-run it direct to tell "bad upstream/key" from
#               "broken deployment/registration". They are different failures
#               with different fixes.
#
#   `embed` (and `models`' second listing) uses CC_EMBED_BASE_URL /
#   CC_EMBED_API_KEY when set — for installs whose embeddings are served by a
#   different server than chat. Unset falls back to the chat pair. NOTE: .env
#   is only read when CC_LLM_BASE_URL/CC_LLM_API_KEY are absent from the
#   environment, so override either all of them or none.
#
#   The embed probe IS the dimension discovery: never trust a model card for
#   this — a mis-sized vector corrupts the Neo4j index instead of erroring,
#   and the dimension is effectively permanent once the index exists.
# ============================================================================
set -euo pipefail

# Windows has no real python3: the WindowsApps stub answers `command -v` but
# exits 49 (2026-08-21 Windows validation, W2) — so probe by RUNNING it.
PY=""
for c in python3 python; do
  command -v "$c" >/dev/null 2>&1 && "$c" -c '' 2>/dev/null && { PY="$c"; break; }
done
[[ -n "$PY" ]] || PY="uv run --python 3.12 python"
# $PY may be multiple words (the uv fallback) — always invoke it unquoted: $PY -c ...

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROXY=0
[[ "${1:-}" == "--proxy" ]] && { PROXY=1; shift; }

if (( PROXY )) || [[ -z "${CC_LLM_BASE_URL:-}" || -z "${CC_LLM_API_KEY:-}" ]]; then
  [[ -f "$HERE/.env" ]] && { set -a; . "$HERE/.env"; set +a; }
fi

# --proxy: same probes, one endpoint — this stack's own LiteLLM, addressed by
# ALIAS. Chat and embeddings collapse to the one base URL on purpose: the split
# upstream lives in the proxy's per-alias api_base now, which is exactly what
# these probes exist to prove.
if (( PROXY )); then
  [[ -n "${LITELLM_MASTER_KEY:-}" ]] || {
    echo "FATAL: --proxy needs LITELLM_MASTER_KEY from $HERE/.env (run make-secrets.sh)" >&2
    exit 1
  }
  CC_LLM_BASE_URL="http://127.0.0.1:${CC_LITELLM_PORT:-4000}/v1"
  CC_LLM_API_KEY="$LITELLM_MASTER_KEY"
  CC_EMBED_BASE_URL="$CC_LLM_BASE_URL"
  CC_EMBED_API_KEY="$CC_LLM_API_KEY"
fi
[[ -n "${CC_LLM_BASE_URL:-}" && -n "${CC_LLM_API_KEY:-}" ]] || {
  echo "FATAL: set CC_LLM_BASE_URL and CC_LLM_API_KEY (env or $HERE/.env)" >&2
  exit 1
}

# Embeddings may live on a different server than chat (CC_EMBED_BASE_URL /
# CC_EMBED_API_KEY); unset means "same server", so the probes below are
# unchanged for a single-endpoint install.
: "${CC_EMBED_BASE_URL:=$CC_LLM_BASE_URL}"
: "${CC_EMBED_API_KEY:=$CC_LLM_API_KEY}"

# The Authorization header travels via `-H @<(...)` — a /dev/fd path — never
# as a command-line argument, so the key is not visible in `ps` while a probe
# runs. (curl >= 7.55 for -H @file; process substitution works in Git Bash.)
_api()  { curl -sS --fail-with-body --max-time 60 -H @<(printf 'Authorization: Bearer %s\n' "$CC_LLM_API_KEY") "$@"; }
_eapi() { curl -sS --fail-with-body --max-time 60 -H @<(printf 'Authorization: Bearer %s\n' "$CC_EMBED_API_KEY") "$@"; }

case "${1:-}" in
  models)
    LIST='
import json, sys
for m in sorted(x["id"] for x in json.load(sys.stdin)["data"]):
    print(m)'
    # The embedding model may not be here: it lives on the EMBED endpoint when
    # that is a separate server. Both are listed when they differ.
    [[ "$CC_EMBED_BASE_URL" == "$CC_LLM_BASE_URL" ]] || echo "== ${CC_LLM_BASE_URL} (chat)"
    _api "${CC_LLM_BASE_URL}/models" | $PY -c "$LIST"
    if [[ "$CC_EMBED_BASE_URL" != "$CC_LLM_BASE_URL" ]]; then
      echo "== ${CC_EMBED_BASE_URL} (embeddings)"
      _eapi "${CC_EMBED_BASE_URL}/models" | $PY -c "$LIST"
    fi
    ;;
  chat)
    [[ -n "${2:-}" ]] || { echo "usage: $0 chat <model-id>" >&2; exit 1; }
    _api -H 'Content-Type: application/json' \
      -d "{\"model\": \"$2\", \"max_tokens\": 128, \"messages\": [{\"role\": \"user\", \"content\": \"Reply with the single word: pong\"}]}" \
      "${CC_LLM_BASE_URL}/chat/completions" | $PY -c '
import json, sys
r = json.load(sys.stdin)
msg = r["choices"][0]["message"]
text = (msg.get("content") or "").strip()
reasoning = (msg.get("reasoning_content") or "").strip()
if not text and not reasoning:
    sys.exit("FATAL: completion returned empty content")
if not text:
    print(f"chat ok: {r['\''model'\'']} -> (reasoning model, no content field; got reasoning_content) — consider raising max_tokens if this recurs")
else:
    print(f"chat ok: {r['\''model'\'']} -> {text!r}")'
    ;;
  embed)
    [[ -n "${2:-}" ]] || { echo "usage: $0 embed <model-id>" >&2; exit 1; }
    _eapi -H 'Content-Type: application/json' \
      -d "{\"model\": \"$2\", \"input\": \"dimension probe\"}" \
      "${CC_EMBED_BASE_URL}/embeddings" | $PY -c '
import json, sys
v = json.load(sys.stdin)["data"][0]["embedding"]
if not v:
    sys.exit("FATAL: embedding returned an empty vector")
print(len(v))'
    ;;
  *)
    echo "usage: $0 [--proxy] models | chat <model-id> | embed <model-id>" >&2
    exit 1
    ;;
esac
