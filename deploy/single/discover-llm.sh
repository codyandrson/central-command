#!/usr/bin/env bash
# ============================================================================
# Probe the user's OpenAI-compatible endpoint — the FIRST step of setup
# (2026-08-21 setup & onboarding design, decisions 2 and 3).
#
#   Deterministic on purpose: the /setup skill (Claude Code — a hard
#   prerequisite of setup) drives this script rather than probing freehand,
#   so every install answers these questions the same way.
#
#   Usage (direct mode: endpoint via CC_LLM_BASE_URL / CC_LLM_API_KEY env vars):
#     ./discover-llm.sh models                   # list model ids the endpoint serves
#     ./discover-llm.sh chat       <model-id>    # prove a real completion works
#     ./discover-llm.sh structured <model-id>    # a Responses-API json_schema round trip
#     ./discover-llm.sh embed      <model-id>    # embed one string, print DIMENSION
#     ./discover-llm.sh speech     <model-id> <out.mp3>   # synthesise one sentence
#     ./discover-llm.sh transcribe <model-id> <audio>     # transcribe it back
#
#   TWO RUNGS, and the difference is the diagnosis (2026-08-25, LiteLLM-first):
#
#     --proxy   target THIS STACK's LiteLLM on 127.0.0.1:${CC_LITELLM_PORT}
#               with the master key from .env, and probe the ALIASES:
#                 ./discover-llm.sh --proxy chat       cc-default
#                 ./discover-llm.sh --proxy structured graphiti-llm
#                 ./discover-llm.sh --proxy embed      cc-embedding
#                 ./discover-llm.sh --proxy speech     cc-tts /tmp/p.mp3
#                 ./discover-llm.sh --proxy transcribe cc-stt /tmp/p.mp3
#               speech then transcribe is the round trip setup runs: the
#               transcription must contain what was synthesised.
#               `structured` is the graphiti-llm check: graphiti_core drives
#               extraction through /v1/responses with a json_schema, and a
#               registration without the openai/chat_completions/ bridge
#               prefix answers PROSE with HTTP 200 — the one failure a plain
#               chat probe cannot see.
#               This is the normal validation path — it proves what production
#               actually uses (spine -> proxy -> upstream), not a path nothing
#               takes at runtime.
#
#     (direct)  TROUBLESHOOTING only, and LISTING what an upstream serves
#               before you fill the aliases in. The provider endpoint is not
#               in .env any more (2026-08-30: it is entered in the LiteLLM UI
#               during setup's pause), so pass it explicitly:
#                 CC_LLM_BASE_URL=https://host/v1 CC_LLM_API_KEY=... \
#                   ./discover-llm.sh chat <upstream model id>
#               When a --proxy probe fails, the direct run tells "bad
#               upstream/key" from "broken registration": different failures,
#               different fixes.
#
#   `embed` (and `models`' second listing) uses CC_EMBED_BASE_URL /
#   CC_EMBED_API_KEY when set — for an embedder on a different server. Unset
#   falls back to the chat pair.
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

# .env holds the proxy's master key and port — nothing about the upstream.
if (( PROXY )); then
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
  echo "FATAL: direct mode needs CC_LLM_BASE_URL and CC_LLM_API_KEY in the environment (or use --proxy)" >&2
  exit 1
}

# Embeddings may live on a different server than chat (CC_EMBED_BASE_URL /
# CC_EMBED_API_KEY); unset means "same server", so the probes below are
# unchanged for a single-endpoint install.
: "${CC_EMBED_BASE_URL:=$CC_LLM_BASE_URL}"
: "${CC_EMBED_API_KEY:=$CC_LLM_API_KEY}"

# The Authorization header travels via `-H @-` — curl reads it from STDIN —
# never as a command-line argument, so the key is not visible in `ps` while a
# probe runs. NOT `-H @<(...)`: that hands native Windows curl an MSYS-virtual
# /proc/<pid>/fd path it cannot open ("Failed to open /proc/752/fd/63", found
# live 2026-08-28 on Git Bash); a stdin pipe is a real handle everywhere.
# (curl >= 7.55 for -H @file/@-. No probe uses stdin for anything else.)
_api()  { printf 'Authorization: Bearer %s\n' "$CC_LLM_API_KEY"   | curl -sS --fail-with-body --max-time 60 -H @- "$@"; }
_eapi() { printf 'Authorization: Bearer %s\n' "$CC_EMBED_API_KEY" | curl -sS --fail-with-body --max-time 60 -H @- "$@"; }

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
  structured)
    [[ -n "${2:-}" ]] || { echo "usage: $0 structured <model-id>" >&2; exit 1; }
    # The exact call graphiti_core makes: /v1/responses with text.format
    # json_schema. Through the proxy this is what proves the bridge prefix.
    _api -H 'Content-Type: application/json' \
      -d "{\"model\": \"$2\", \"input\": \"What word follows ping? Answer as JSON.\", \"text\": {\"format\": {\"type\": \"json_schema\", \"name\": \"probe\", \"strict\": true, \"schema\": {\"type\": \"object\", \"properties\": {\"answer\": {\"type\": \"string\"}}, \"required\": [\"answer\"], \"additionalProperties\": false}}}}" \
      "${CC_LLM_BASE_URL}/responses" | $PY -c '
import json, sys
r = json.load(sys.stdin)
text = "".join(c.get("text") or "" for o in r.get("output", []) if o.get("type") == "message"
               for c in o.get("content", []))
try:
    answer = json.loads(text)["answer"]
except Exception:
    sys.exit(f"FATAL: structured output is not the requested JSON (got {text[:120]!r}) — "
             "is the model registered as openai/chat_completions/<id>?")
m = r.get("model")
print(f"structured ok: {m} -> {answer!r}")'
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
  speech)
    [[ -n "${2:-}" && -n "${3:-}" ]] || { echo "usage: $0 speech <model-id> <out.mp3>" >&2; exit 1; }
    # No voice: the engine's default stands — a voice name is per-engine and
    # belongs in the alias row, not here.
    _api -H 'Content-Type: application/json' \
      -d "{\"model\": \"$2\", \"input\": \"Central Command is listening.\", \"response_format\": \"mp3\"}" \
      -o "$3" "${CC_LLM_BASE_URL}/audio/speech"
    sz="$(wc -c <"$3" | tr -d ' ')"
    (( sz > 1000 )) || { echo "FATAL: speech returned ${sz} bytes — not audio" >&2; exit 1; }
    echo "speech ok: $2 -> ${sz} bytes of audio in $3"
    ;;
  transcribe)
    [[ -n "${2:-}" && -f "${3:-}" ]] || { echo "usage: $0 transcribe <model-id> <audio-file>" >&2; exit 1; }
    _api -F "model=$2" -F "file=@$3" "${CC_LLM_BASE_URL}/audio/transcriptions" | $PY -c '
import json, sys
text = (json.load(sys.stdin).get("text") or "").strip()
if not text:
    sys.exit("FATAL: transcription returned empty text")
if "command" not in text.lower():
    sys.exit(f"FATAL: transcription {text!r} does not contain what was synthesised (\"Central Command is listening.\")")
print(f"transcribe ok: {sys.argv[1]} -> {text!r}")' "$2"
    ;;
  *)
    echo "usage: $0 [--proxy] models | chat <model-id> | structured <model-id> | embed <model-id> | speech <model-id> <out.mp3> | transcribe <model-id> <audio>" >&2
    exit 1
    ;;
esac
