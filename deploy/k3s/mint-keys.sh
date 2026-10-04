#!/usr/bin/env bash
# ============================================================================
# Mint the LiteLLM virtual keys against the RUNNING proxy and write them
# back where each one is read from.
#
#   One goes into the REPO-ROOT .env, which is the app's own env:
#     CC_LLM_API_KEY  -> alias cc-spine, models SPINE_MODELS below
#   One goes into web/.env (when it exists), the cockpit's Node server:
#     OPENAI_API_KEY  -> ["cc-tts","cc-stt"]   alias cc-cockpit
#
#   The app's key reaches every alias the app calls ITSELF: cc-default (the
#   agents), cc-tts/cc-stt (api/speech.py forwards the cockpit's voice traffic
#   with it), and — since graphiti-core runs inside the app (design record
#   2026-10-04, D2) — graphiti-llm (extraction), cc-embedding (the graph's
#   embedder) and cc-rerank (graph search's cross-encoder, CC_GRAPH_RERANK_ALIAS).
#   The three Graphiti-only keys this script used to mint (GRAPHITI_LLM_API_KEY,
#   EMBEDDER_API_KEY, RERANKER_API_KEY) left with the Graphiti server; nothing
#   reads them, and they are revoked in the LiteLLM UI by hand once the
#   previous release is no longer a rollback target.
#
#   Each key is scoped to its own model group so a leak in one cannot spend
#   through another. `tags` is deliberately absent from every body — a tags
#   field 403s on non-Enterprise LiteLLM.
#
#   IDEMPOTENT: a value that is already set and is not PENDING is KEPT. Set
#   FORCE=1 to mint a replacement anyway (the old key is NOT deleted — revoke it
#   in the LiteLLM UI if you mean to).
#
#   SCOPE, for a key that is kept: the app key's model list is READ and, when it
#   is a non-empty list missing an alias in SPINE_MODELS, the missing ones are
#   ADDED through /key/update — a union, so a model an operator added is never
#   removed, and the key's VALUE never changes (nothing restarts). An EMPTY list
#   is LiteLLM's "every model" and is left alone. This is how an EXISTING
#   install gets the graph aliases: cc-update.sh runs `--scope-only` on every
#   update, and the llm phase of setup.sh runs this script whole. Same rule as
#   the single-node profile's mint_spine_key.
#
#   Then it re-runs make-secrets.sh: cc-crawler-llm carries CC_LLM_API_KEY, and
#   on a clean install that Secret can only be made once this key exists.
#
#   Usage:  ./deploy/k3s/mint-keys.sh                 [FORCE=1]
#           ./deploy/k3s/mint-keys.sh --scope-only    only the scope check above:
#                                                     no minting, no Secrets,
#                                                     no kubectl. Exit 1 when the
#                                                     scope could not be read or
#                                                     widened.
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PI_ENV="$REPO_ROOT/deploy/pi/.env"
ROOT_ENV="$REPO_ROOT/.env"
WEB_ENV="$REPO_ROOT/web/.env"
BASE_URL="${CC_LITELLM_URL:-http://127.0.0.1:4000}"

# The ONE list of what the app's own key must reach on this profile.
SPINE_MODELS='["cc-default","cc-tts","cc-stt","graphiti-llm","cc-embedding","cc-rerank"]'

SCOPE_ONLY=0
case "${1:-}" in
  --scope-only) SCOPE_ONLY=1 ;;
  "") ;;
  *) echo "usage: $0 [--scope-only]" >&2; exit 1 ;;
esac

[[ -f "$PI_ENV" ]] || { echo "FATAL: $PI_ENV not found — run ./deploy/k3s/init-env.sh first" >&2; exit 1; }
[[ -f "$ROOT_ENV" ]] || { echo "FATAL: $ROOT_ENV not found — cp .env.example .env first" >&2; exit 1; }

MASTER="$(sed -n 's/^LITELLM_MASTER_KEY=//p' "$PI_ENV" | head -1)"
[[ -n "$MASTER" ]] || { echo "FATAL: LITELLM_MASTER_KEY empty in $PI_ENV" >&2; exit 1; }

curl -fsS -m 10 "$BASE_URL/health/liveliness" >/dev/null \
  || { echo "FATAL: no LiteLLM proxy at $BASE_URL — bring the trio up first." >&2; exit 1; }

current() {  # current <file> <name>
  sed -n "s/^$2=//p" "$1" | head -1
}

set_var() {  # set_var <file> <name> <value>
  local f="$1" name="$2" val="$3"
  if grep -q "^${name}=" "$f"; then
    # Minted keys are `sk-` + URL-safe base64 — no sed metacharacters, but use
    # a delimiter that cannot appear in one anyway.
    sed -i "s|^${name}=.*|${name}=${val}|" "$f"
  else
    printf '%s=%s\n' "$name" "$val" >>"$f"
  fi
}

# One proxy call under the master key. The key — and a body that carries
# another key — travel in a curl config on STDIN, never in an argv (`ps`).
proxy() {  # proxy <path> [json-body] -> response body; non-zero on HTTP error
  local path="$1" body="${2:-}"
  body="${body//\\/\\\\}"; body="${body//\"/\\\"}"
  {
    printf 'url = "%s/%s"\n' "$BASE_URL" "$path"
    printf 'header = "Authorization: Bearer %s"\n' "$MASTER"
    if [[ -n "$body" ]]; then
      printf 'header = "Content-Type: application/json"\n'
      printf 'data = "%s"\n' "$body"
    fi
  } | curl -sS -f -m 30 -K - 2>/dev/null
}

generate() {  # generate <alias> <json-array-of-models> -> raw response body; non-zero on HTTP error
  curl -sS -f -X POST "$BASE_URL/key/generate" \
    -H "Authorization: Bearer $MASTER" -H 'Content-Type: application/json' \
    -d "{\"key_alias\": \"$1\", \"models\": $2}" 2>/dev/null
}
mint() {  # mint <alias> <json-array-of-models> -> prints the key on stdout; non-zero on failure
  local alias="$1" models="$2" out
  if ! out="$(generate "$alias" "$models")"; then
    # LiteLLM requires key aliases to be unique across all keys. The alias
    # already existing while OUR slot is empty means a previous instance minted
    # it and its value is gone with that instance's .env (the 2026-09-26
    # re-deploy that kept the LiteLLM database — README §8 item 4 — hit this:
    # /key/generate 400'd, the failure was swallowed inside a command
    # substitution, and an EMPTY CC_LLM_API_KEY was written and reported as
    # minted). A key nothing holds is a key to revoke: delete BY ALIAS and mint
    # again. A key that IS held stays "kept" above and never reaches here.
    local probe
    probe="$(curl -sS -X POST "$BASE_URL/key/generate" \
      -H "Authorization: Bearer $MASTER" -H 'Content-Type: application/json' \
      -d "{\"key_alias\": \"$alias\", \"models\": $models}" 2>/dev/null || true)"
    if [[ "$probe" == *"already exists"* ]]; then
      echo "  revoke  alias $alias (exists on the proxy, but no file holds its key)" >&2
      curl -sS -f -X POST "$BASE_URL/key/delete" \
        -H "Authorization: Bearer $MASTER" -H 'Content-Type: application/json' \
        -d "{\"key_aliases\": [\"$alias\"]}" >/dev/null 2>&1 \
        || { echo "FATAL: /key/delete by alias failed for $alias" >&2; return 1; }
      out="$(generate "$alias" "$models")" \
        || { echo "FATAL: /key/generate still failing for $alias after revoking the stale alias" >&2; return 1; }
    else
      echo "FATAL: /key/generate failed for $alias: ${probe:0:200}" >&2
      return 1
    fi
  fi
  python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])' <<<"$out"
}
ensure_key() {  # ensure_key <file> <var> <alias> <models-json>
  local f="$1" var="$2" alias="$3" models="$4" cur key
  cur="$(current "$f" "$var")"
  if [[ -n "$cur" && "$cur" != PENDING && "${FORCE:-}" != 1 ]]; then
    echo "  kept    $var (already set; FORCE=1 to re-mint)"
    return
  fi
  # Capture FIRST, then write: `set_var … "$(mint …)"` let a failed mint write
  # an empty value under `set -e` (a substitution inside an argument does not
  # trip it), and setup reported the step green. An empty key is never written.
  key="$(mint "$alias" "$models")" || exit 1
  [[ -n "$key" && "$key" == sk-* ]] || { echo "FATAL: minted value for $var is not a key" >&2; exit 1; }
  set_var "$f" "$var" "$key"
  echo "  minted  $var -> alias $alias, models $models"
}

# ensure_scope <file> <var> <models-json> — widen a HELD key to cover <models>.
# Returns 1 only when the scope could not be read or the update was refused.
ensure_scope() {
  local f="$1" var="$2" want="$3" cur info plan state missing union
  cur="$(current "$f" "$var")"
  if [[ -z "$cur" || "$cur" == PENDING ]]; then
    echo "  scope   $var not minted yet — nothing to widen (the llm phase of ./deploy/k3s/setup.sh mints it)"
    return 0
  fi
  info="$(proxy "key/info?key=$cur")" || {
    echo "  WARN    could not read $var's scope from $BASE_URL/key/info under the master key" >&2
    return 1
  }
  # One python pass, the JSON on STDIN and only alias NAMES in the argv:
  #   line 1  empty | covered | missing   (anything else = could not read it)
  #   line 2  the missing aliases, space-separated
  #   line 3  the UNION as a JSON array, current order first
  plan="$(python3 -c '
import json, sys
try:
    m = (json.load(sys.stdin).get("info") or {})["models"]
    assert isinstance(m, list)
except Exception:
    sys.exit(0)
want = json.loads(sys.argv[1])
gone = [a for a in want if a not in m]
print("empty" if not m else ("missing" if gone else "covered"))
print(" ".join(gone))
print(json.dumps(m + gone))
' "$want" <<<"$info" 2>/dev/null)" || plan=""
  state="$(sed -n 1p <<<"$plan")"
  missing="$(sed -n 2p <<<"$plan")"
  union="$(sed -n 3p <<<"$plan")"
  case "$state" in
    empty)
      echo "  scope   $var's model list is EMPTY, which LiteLLM reads as every model — left alone" ;;
    covered)
      echo "  scope   $var already reaches $want" ;;
    missing)
      if proxy "key/update" "{\"key\": \"$cur\", \"models\": $union}" >/dev/null; then
        echo "  widened $var: ADDED $missing (every model it had is kept; the key's value is unchanged)"
      else
        echo "  WARN    $var lacks $missing and /key/update was refused — add them to the key in the LiteLLM UI" >&2
        return 1
      fi ;;
    *)
      echo "  WARN    could not parse $var's scope from /key/info" >&2
      return 1 ;;
  esac
}

if (( SCOPE_ONLY )); then
  echo "checking the app key's scope against $BASE_URL:"
  ensure_scope "$ROOT_ENV" CC_LLM_API_KEY "$SPINE_MODELS"
  exit $?
fi

echo "minting virtual keys against $BASE_URL:"
# The spine's key is a VIRTUAL key, never the master key (config.py's contract).
ensure_key   "$ROOT_ENV" CC_LLM_API_KEY cc-spine "$SPINE_MODELS"
ensure_scope "$ROOT_ENV" CC_LLM_API_KEY "$SPINE_MODELS" \
  || echo "  (the scope was not widened — graph calls 403 until the key reaches $SPINE_MODELS)" >&2
# The cockpit's Node server (cc-nerve) speaks to the proxy on its own key,
# scoped to the two speech aliases only. web/.env may not exist yet at this
# script's clean-install position (setup's app phase writes it) — then the
# app phase mints it.
if [[ -f "$WEB_ENV" ]]; then
  ensure_key "$WEB_ENV"  OPENAI_API_KEY       cc-cockpit          '["cc-tts","cc-stt"]'
fi

echo
echo "re-applying secrets (cc-crawler-llm carries CC_LLM_API_KEY):"
"$REPO_ROOT/deploy/k3s/make-secrets.sh"

echo
echo "Done. cc-uvicorn reads CC_LLM_API_KEY — restart it if a key was MINTED"
echo "(a widened scope needs no restart):"
echo "  sudo systemctl restart cc-uvicorn"
