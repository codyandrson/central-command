# shellcheck shell=bash
# ============================================================================
# phases/app.sh — the `app` phase of the single-node install (deploy/single).
#
#   APP: the venv and the editable install, the derived .env values, the
#   spine's own scoped LiteLLM key, and the cockpit build.
#
#   SOURCED by deploy/single/setup.sh, never executed: setup.sh sources the
#   ten phase files in steps.tsv's phase order before main runs, and this
#   file defines functions and constants and does nothing else, so sourcing it
#   twice is harmless. What more than one phase uses — the output protocol,
#   load_env and the .env helpers, compose, the image catch-up, the process
#   helpers `stop` shares, the probes two phases read — is setup.sh's.
#
#   Design record: docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md
#   D11 — "a thin orchestrator sourcing step files in order" (Sentry's
#   installer), one file per manifest phase (v2.58.0). The functions below
#   MOVED here from setup.sh unchanged, comments and all; the records they
#   cite are the ones that shaped them.
#
#   ROWS (steps.tsv, phase `app`) — step, kind, probe; a probe marked
#   (setup.sh) is shared with another phase or the driver and lives there:
#     venv                               run    p_venv  (setup.sh)
#     install                            run    p_install
#     graphiti-patches                   run    p_graphiti_patches
#     app-env                            run    p_env_file
#     mint-key                           run    p_mint_key
#     app-llm-base-url                   run    p_llm_base_url
#     app-default-model                  run    p_default_model
#     app-embed-alias                    run    p_embed_alias
#     app-litellm-db-url                 run    p_litellm_db_url
#     app-link-litellm                   run    p_link_litellm
#     app-link-neo4j                     run    p_link_neo4j
#     app-link-n8n                       run    p_link_n8n
#     app-link-crawler                   run    p_link_crawler
#     app-link-sandbox                   run    p_link_sandbox
#     cockpit                            run    p_cockpit_build
# ============================================================================

[[ -n "${CC_PHASE_APP_LOADED:-}" ]] && return 0
CC_PHASE_APP_LOADED=1

# Set only when the current value is empty/placeholder — the operator's own
# edits are never overwritten. This is what makes the app phase re-runnable.
# What it writes now is always a DERIVED value (one composed from other keys
# in the same file), never a copy of a second file: with one answer file, the
# same fact has one key.
set_kv_if_unset() { # set_kv_if_unset <file> <key> <value> <check-name>
  local cur; cur="$(get_kv "$1" "$2")"
  if [[ -z "$3" ]] && is_placeholder "$cur"; then
    warn "$4" "$2 has no value to derive — the keys it is composed from are empty in .env"
    return 0
  fi
  if is_placeholder "$cur"; then
    set_kv "$1" "$2" "$3" && pass "$4" "$2 derived into .env"
  else
    pass "$4" "$2 already set — left alone"
  fi
}
# ─────────────────────────────────────────────────────────────────────────────
# PHASE: app — the Python environment, the app's .env, the spine's own key.
# ─────────────────────────────────────────────────────────────────────────────

# ── the spine's own LiteLLM key, and its SCOPE (2026-10-01 record, D4/P2) ────
# The key used to be minted with a hard-coded ["cc-default", "cc-tts",
# "cc-stt"] — a SECOND hand-kept alias list beside cc_required_aliases (the ONE
# list, deploy/env-lib.sh), and it had already drifted: graphiti-llm,
# cc-embedding and the then-reranker alias were never in it. MEASURED before this fix:
# that is NOT what breaks graph embedding — the graph writer embeds with the
# ADMIN key, not this one — but the record's fix stands on its own: a scope is
# a list, a list has one definition, so the key is scoped to exactly the
# aliases this deployment requires and nothing else.
#
# LiteLLM endpoint shapes, from the vendored docs (never a live write to check
# them): docs/vendor/litellm/docs/proxy/virtual_keys.md — POST /key/generate
# takes {"models": [...], "metadata": {...}} and answers {"key": "sk-..."};
# GET /key/info?key=<key> under the master key answers {"key": ..., "info":
# {"models": [...]}}; POST /key/update takes {"key": <key>, <field>: ...}.
# docs/vendor/litellm/docs/proxy/key_auth_arch.md: "The empty list and the
# literal `*` both mean 'all models on the proxy'" — which is why an empty
# scope is LEFT ALONE below rather than "fixed" into a narrower one.

# The key's scope: cc_required_aliases plus the reranker alias when .env names
# one (cc_scope_aliases, deploy/env-lib.sh) — never a second list. The reranker
# joins only once the llm phase has proven it and written CC_GRAPH_RERANK_ALIAS
# (or the operator set it), because graph search calls it with THIS key.
spine_scope_aliases() {
  cc_scope_aliases "$(get_kv "$ENV_FILE" CC_GRAPH_RERANK_ALIAS)"
}

# The scope as a JSON array.
spine_aliases_json() {
  local a out=""
  for a in $(spine_scope_aliases); do out="${out:+$out, }\"$a\""; done
  printf '[%s]' "$out"
}

# One string made safe inside a double-quoted curl-config value (and, the same
# two escapes, inside a JSON string): backslash and double quote.
cfg_quote() { # cfg_quote <text>
  local s="${1//\\/\\\\}"
  printf '%s' "${s//\"/\\\"}"
}

# Percent-encoding for a query value, in bash builtins only: the value is a
# credential, and a credential may not reach an argv (`ps` shows argv).
url_encode() { # url_encode <text>
  local s="$1" out="" c i
  for (( i = 0; i < ${#s}; i++ )); do
    c="${s:i:1}"
    case "$c" in
      [A-Za-z0-9._~-]) out+="$c" ;;
      *) printf -v c '%%%02X' "'$c"; out+="$c" ;;
    esac
  done
  printf '%s' "$out"
}

# A request to the proxy's management API as a curl CONFIG, printed for curl to
# read from STDIN (`curl -K -`). Both secrets — the admin key in the header and,
# for /key/info and /key/update, the spine key in the URL or the body — travel
# that way, so neither is ever in an argv. NOT a temp file and NOT `-K <(...)`:
# nothing may be written inside the checkout, and native Windows curl cannot
# open MSYS's /proc fd paths (found live 2026-08-28) — stdin is the one channel
# that works on every host this profile runs on.
proxy_cfg() { # proxy_cfg <path-and-query> [json-body]
  printf 'url = "%s"\n' "$(cfg_quote "http://127.0.0.1:${CC_LITELLM_PORT}/$1")"
  printf 'header = "%s"\n' "$(cfg_quote "Authorization: Bearer ${CC_LLM_PROXY_ADMIN_KEY:-}")"
  if [[ -n "${2:-}" ]]; then
    printf 'header = "Content-Type: application/json"\n'
    printf 'data = "%s"\n' "$(cfg_quote "$2")"
  fi
}

mint_spine_key() {
  local cur_key models
  cur_key="$(get_kv "$ENV_FILE" CC_LLM_API_KEY)"
  models="$(spine_aliases_json)"
  if is_placeholder "$cur_key"; then
    [[ -n "${CC_LLM_PROXY_ADMIN_KEY:-}" ]] || { fail "mint-key" "CC_LLM_PROXY_ADMIN_KEY is missing from .env — run the llm phase first (make-secrets.sh generates it)"; return 1; }
    local body minted
    # The master key travels via `-H @-` (stdin), so it is not visible in `ps`
    # while the request runs; the body carries no secret, so it may sit in the
    # argv. No `tags` field: tags are an Enterprise feature and their presence
    # 403s a community proxy.
    body="$(printf 'Authorization: Bearer %s\n' "$CC_LLM_PROXY_ADMIN_KEY" | \
      curl -sS --fail-with-body -m 60 \
      -H @- \
      -H 'Content-Type: application/json' \
      -d "{\"models\": $models, \"metadata\": {\"cc\": \"spine\"}}" \
      "http://127.0.0.1:${CC_LITELLM_PORT}/key/generate" 2>&1)"
    minted="$($PY -c 'import json,sys; print(json.load(sys.stdin).get("key",""))' <<<"$body" 2>/dev/null)"
    if [[ -z "$minted" ]]; then
      fail "mint-key" "/key/generate did not return a key — is the proxy up? (the response is NOT echoed; run ./setup.sh report)"
      return 1
    fi
    set_kv "$ENV_FILE" CC_LLM_API_KEY "$minted"
    local list; list="$(spine_scope_aliases)"
    pass "mint-key" "minted a LiteLLM virtual key scoped to ${list// / + } (cc_required_aliases, plus the reranker alias when set) and stored it as CC_LLM_API_KEY"
    return 0
  fi

  # ALREADY SET: never re-mint, never change the value — an operator (or an
  # earlier release) put it there and the app is running on it. What CAN have
  # drifted is its SCOPE: a key minted before this fix lacks four aliases, and
  # CC_ENABLE_SPEECH turning on adds two. So the scope is READ and, when it is
  # a non-empty list missing a required alias, the missing ones are ADDED —
  # a union: a model an operator added is never removed. A failure to ask is a
  # WARN, not a FAIL: this step proves nothing about the key working, the
  # self-check (verify/selfcheck) does.
  if [[ -z "${CC_LLM_PROXY_ADMIN_KEY:-}" ]]; then
    warn "mint-key" "CC_LLM_API_KEY already set; its scope was NOT checked against cc_required_aliases because CC_LLM_PROXY_ADMIN_KEY is empty in .env (the self-check proves whether the key reaches the aliases)"
    return 0
  fi
  local info plan state missing union
  info="$(proxy_cfg "key/info?key=$(url_encode "$cur_key")" | curl -sS --fail-with-body -m 30 -K - 2>/dev/null)" || info=""
  # One python pass, the JSON on STDIN and only alias NAMES in the argv:
  #   line 1  empty | covered | missing   (anything else = could not read it)
  #   line 2  the missing aliases, space-separated
  #   line 3  the UNION as a JSON array, current order first
  plan="$($PY -c '
import json, sys
try:
    m = (json.load(sys.stdin).get("info") or {})["models"]
    assert isinstance(m, list)
except Exception:
    sys.exit(0)
want = sys.argv[1:]
gone = [a for a in want if a not in m]
print("empty" if not m else ("missing" if gone else "covered"))
print(" ".join(gone))
print(json.dumps(m + gone))
' $(spine_scope_aliases) <<<"$info" 2>/dev/null)" || plan=""
  state="$(sed -n 1p <<<"$plan")"
  missing="$(sed -n 2p <<<"$plan")"
  union="$(sed -n 3p <<<"$plan")"
  case "$state" in
    empty)
      pass "mint-key" "CC_LLM_API_KEY already set; its model list is EMPTY, which LiteLLM reads as every model on the proxy — left alone (not minting a second key)"
      ;;
    covered)
      pass "mint-key" "CC_LLM_API_KEY already set; its scope already covers every alias this deployment requires ($(spine_scope_aliases)) — not minting a second key"
      ;;
    missing)
      if proxy_cfg "key/update" "{\"key\": \"$(cfg_quote "$cur_key")\", \"models\": $union}" \
           | curl -sS --fail-with-body -m 30 -K - >/dev/null 2>&1; then
        pass "mint-key" "CC_LLM_API_KEY already set; ADDED $missing to its scope (every model it already had is kept, and the key's value is unchanged)"
      else
        warn "mint-key" "CC_LLM_API_KEY already set and its scope lacks $missing, but /key/update did not succeed — add them to the key in the LiteLLM UI (the self-check names the alias the app cannot reach)"
      fi
      ;;
    *)
      warn "mint-key" "CC_LLM_API_KEY already set; could not read its scope from the proxy's /key/info under CC_LLM_PROXY_ADMIN_KEY (is the proxy up on CC_LITELLM_PORT?) — not checked against cc_required_aliases. The self-check proves whether the key reaches the aliases"
      ;;
  esac
  return 0
}

phase_app() {
  load_env || return 1

  if venv_python >/dev/null; then
    pass "venv" ".venv already present at the repo root"
  else
    step "venv" ".venv created with CPython 3.12" \
      uv venv --python 3.12 "$REPO_ROOT/.venv" || return 1
  fi
  export VIRTUAL_ENV="$REPO_ROOT/.venv"

  # Air-gapped installs resolve nothing: requirements.lock is the frozen,
  # suite-tested resolution, and `-e . --no-deps` adds the package itself
  # without letting pip re-resolve against a mirror.
  if [[ "$CC_AIRGAP" == "1" ]]; then
    step "install" "installed from requirements.lock (air-gapped)" \
      in_repo uv pip install -r "$REPO_ROOT/requirements.lock" || return 1
    step "install-editable" "central_command installed editable, no dependency resolution" \
      in_repo uv pip install -e . --no-deps || return 1
  else
    step "install" "central_command installed editable with [dev,runtime]" \
      in_repo uv pip install -e ".[dev,runtime]" || return 1
  fi

  # app/graphiti-patches: the two carried graphiti-core fixes (design record
  # 2026-10-04, D7) onto the package the install just put down — after EVERY
  # install, because an install puts back a pristine copy. The applier is the
  # venv's own Python (no `patch` binary is assumed), idempotent, atomic
  # (temp file + os.replace, so uv's hardlinked cache is never written
  # through) and loud: a file that is neither pristine nor patched FAILS
  # here — the version is pinned, so that is a release defect, not drift.
  local vpy; vpy="$(venv_python)" || { fail "graphiti-patches" "no venv Python to apply the graphiti-core fixes with"; return 1; }
  step "graphiti-patches" "the carried graphiti-core fixes are applied to the installed package (deploy/graphiti-patches/)" \
    in_repo "$vpy" scripts/apply_graphiti_patches.py || return 1

  # There is no second .env to create any more (v2.42.0): load_env required
  # the one answer file before this phase could run. Its MODE is still worth
  # asserting — on this profile it holds every credential in the install — and
  # chmod is a silent no-op on NTFS (2026-08-21 Windows validation, W8), so the
  # mode is verified rather than claimed.
  chmod 600 "$ENV_FILE" 2>/dev/null
  local mode; mode="$(stat -c %a "$ENV_FILE" 2>/dev/null || echo unknown)"
  if [[ "$mode" == "600" ]]; then
    pass "app-env" ".env is the one answer file, mode 0600"
  else
    warn "app-env" ".env is the one answer file, but the filesystem did not apply 0600 (Windows/NTFS) — it relies on the account's ACLs"
  fi

  # The spine gets its OWN LiteLLM virtual key, never the master key: a leak of
  # the agents' credential must not be able to reconfigure the proxy.
  mint_spine_key || return 1

  # DERIVED values — the last two things in .env nobody should have to type.
  # Until v2.42.0 this block COPIED five values out of deploy/single/.env into
  # the app's .env (CC_LLM_PROXY_ADMIN_KEY, CC_NEO4J_PASSWORD,
  # CC_LITELLM_SALT_KEY, CC_EMBED_DIM and this URL). With one answer file the
  # same fact has ONE key and there is nothing to copy: make-secrets.sh
  # generates the CC_ names directly and the llm phase writes CC_EMBED_DIM
  # where the app already reads it. What is left is genuinely COMPOSED from
  # other keys in this same file.
  # The app dials the proxy THIS profile deployed. .env.example carries the
  # default, but since v2.45.0 `configure` writes only what it asks (a
  # preseed stays byte-identical), so a configure-born .env had NO
  # CC_LLM_BASE_URL and the API answered every live model resolve with
  # `LLMProviderNotConfigured` — the Windows testbed's demo feed was a 500
  # (2026-09-25). Composed from the port answer, like the two below.
  set_kv_if_unset "$ENV_FILE" CC_LLM_BASE_URL "http://127.0.0.1:${CC_LITELLM_PORT}" "app-llm-base-url"
  # …and WHICH model the agents run on: this profile's proxy carries the
  # cc-default alias, and the code default (an Anthropic model id) is what a
  # bare .env falls back to — the testbed's first live triage was a 403 from
  # the minted key, which can only reach the aliases (2026-09-25).
  set_kv_if_unset "$ENV_FILE" CC_DEFAULT_MODEL "openai:cc-default" "app-default-model"
  set_kv_if_unset "$ENV_FILE" CC_EMBED_ALIAS "cc-embedding" "app-embed-alias"
  set_kv_if_unset "$ENV_FILE" CC_LITELLM_DB_URL \
    "postgresql://llmproxy:${LITELLM_POSTGRES_PASSWORD:-}@127.0.0.1:${CC_LITELLM_DB_PORT}/litellm" \
    "app-litellm-db-url"

  # The Systems page's "Open →" links (v2.52.0). Display-only, so no phase
  # had ever filled them and a configure-born .env showed a Systems page with
  # no links at all. On this profile every service sits on a loopback port
  # this file already answers, and the cockpit is browsed on the same
  # machine — so the link IS the port. Composed like the three above; an
  # operator's own URL (a reverse proxy, a tailnet name) is never overwritten.
  # Off-by-flag services get no link: an absent link is "not installed", not
  # "misconfigured". VictoriaLogs, pgweb and llama-swap are not part of this
  # profile, so their keys stay empty on purpose.
  set_kv_if_unset "$ENV_FILE" CC_LLM_PROXY_UI_URL "http://127.0.0.1:${CC_LITELLM_PORT}/ui/" "app-link-litellm"
  set_kv_if_unset "$ENV_FILE" CC_NEO4J_BROWSER_URL \
    "http://127.0.0.1:${CC_NEO4J_HTTP_PORT:-7474}/browser/?dbms=bolt%3A%2F%2F127.0.0.1%3A${CC_NEO4J_BOLT_PORT:-7687}" \
    "app-link-neo4j"
  if [[ "${CC_ENABLE_N8N:-0}" == 1 ]]; then
    set_kv_if_unset "$ENV_FILE" CC_N8N_UI_URL "http://127.0.0.1:${CC_N8N_PORT:-5678}" "app-link-n8n"
  fi
  if [[ "${CC_ENABLE_CRAWLER:-1}" == 1 ]]; then
    set_kv_if_unset "$ENV_FILE" CC_CRAWLER_DOCS_URL "http://127.0.0.1:${CC_CRAWLER_PORT}/docs" "app-link-crawler"
  fi
  if [[ "${CC_ENABLE_SANDBOX:-1}" == 1 ]]; then
    local sbx; sbx="$(get_kv "$ENV_FILE" CC_SANDBOX_RUNNER_URL)"
    set_kv_if_unset "$ENV_FILE" CC_SANDBOX_DOCS_URL "${sbx:-http://127.0.0.1:8090}/docs" "app-link-sandbox"
  fi

  # CC_EXECUTOR_MODE is left at .env.example's `live` (v2.37.0). A fresh
  # install used to be forced to dry_run — a global no-op on EVERY capability,
  # including the internal ones — and the operator forgot the flip more often
  # than it protected anything: a day of approvals nobody knew were simulated,
  # a tour whose episodes evaporated, a rename that ran production dry for 40
  # minutes. The approval gate is the safety; the feed, the drain and every
  # schedule stay off until the operator turns them on.
  # CC_OPERATOR_NAME is deliberately NOT set here: it is the interview's, and
  # the default ("the operator") is correct until someone is asked.

  if command -v node >/dev/null 2>&1; then
    local nv; nv="$(node -v 2>/dev/null)"; nv="${nv#v}"
    if [[ "${nv%%.*}" =~ ^[0-9]+$ ]] && (( ${nv%%.*} >= 22 )); then
      # fetch already ran `npm ci`; only a tree it did not leave gets one here.
      # TWO steps, never one `A || B; C` body (2026-10-01 design record, D5):
      # `[[ -d node_modules ]] || npm ci; npm run build` handed the step the
      # BUILD's exit status, so a failed `npm ci` followed by a build that
      # happened to succeed against a stale tree was a PASS. Both carry the
      # check-name `cockpit`, so a failed install is what app/cockpit's ledger
      # row records, whatever its probe would read.
      #
      # And neither runs when its record says the tree is already what it
      # would produce (P5, F12 — deploy/env-lib.sh's cc_cockpit_current, the
      # question p_cockpit_npm and p_cockpit_build ask too): the 2026-10-02
      # Windows run spent ~66 s rebuilding an unchanged cockpit on every
      # `app`. Each record is dropped before its command starts and written
      # only after it succeeded.
      if ! cc_cockpit_current "$STATE_DIR" npm "$REPO_ROOT"; then
        cc_cockpit_forget "$STATE_DIR" npm
        step "cockpit" "cockpit npm tree installed (fetch had not left one from this lockfile)" \
          in_web npm ci || return 1
        cc_cockpit_record "$STATE_DIR" npm "$REPO_ROOT" \
          || warn "cockpit" "could not record the npm tree's inputs in $(cc_cockpit_record_file "$STATE_DIR" npm) — the next run reinstalls it"
      fi
      if cc_cockpit_current "$STATE_DIR" build "$REPO_ROOT"; then
        pass "cockpit" "cockpit build current — web/dist and web/server-dist were built from these inputs (web/src, web/server, the lockfile, the build configs), so npm run build was skipped"
      else
        cc_cockpit_forget "$STATE_DIR" build
        step "cockpit" "cockpit built (web/) — its inputs recorded, so an unchanged tree skips the next build" \
          in_web npm run build || return 1
        cc_cockpit_record "$STATE_DIR" build "$REPO_ROOT" \
          || warn "cockpit" "could not record the cockpit build's inputs in $(cc_cockpit_record_file "$STATE_DIR" build) — the next run rebuilds it"
      fi
    else
      warn "cockpit" "node v$nv is older than 22 — cockpit not built; the API runs without it"
    fi
  else
    warn "cockpit" "node not found — cockpit not built; the API runs without it"
  fi
}

p_env_file() {
  [[ -f "$ENV_FILE" ]]
}

# Installed means the package imports AND the venv carries the server boot
# starts it with (v2.58.0). On `import central_command` alone, a venv that had
# lost uvicorn read installed: the full run skipped app as done, and boot then
# failed on the missing server every time, with nothing that could repair it.
# Read-only: --check reports and exits 1 unless every carried fix is present.
p_graphiti_patches() {
  local py
  py="$(venv_python)" || return 1
  ( cd "$REPO_ROOT" && "$py" scripts/apply_graphiti_patches.py --check ) >/dev/null 2>&1
}

p_install() {
  local py
  py="$(venv_python)" || return 1
  venv_uvicorn >/dev/null || return 1
  ( cd "$REPO_ROOT" && "$py" -c 'import central_command' ) >/dev/null 2>&1
}

# Built AND built from this tree: web/server-dist/index.js and web/dist present,
# and `<state>/cockpit.build-inputs` equal to the hash of the build's inputs
# now — so a changed source file or lockfile reads false and the row re-runs.
p_cockpit_build() {
  p_node_ok || return 0
  cc_cockpit_current "$STATE_DIR" build "$REPO_ROOT"
}

# ── app ─────────────────────────────────────────────────────────────────────
p_mint_key() {
  p_kv_set CC_LLM_API_KEY
}

p_llm_base_url() {
  p_kv_set CC_LLM_BASE_URL
}

p_default_model() {
  p_kv_set CC_DEFAULT_MODEL
}

p_embed_alias() {
  p_kv_set CC_EMBED_ALIAS
}

p_litellm_db_url() {
  p_kv_set CC_LITELLM_DB_URL
}

p_link_litellm() {
  p_kv_set CC_LLM_PROXY_UI_URL
}

p_link_neo4j() {
  p_kv_set CC_NEO4J_BROWSER_URL
}

p_link_n8n() {
  p_off CC_ENABLE_N8N 0 1 && return 0
  p_kv_set CC_N8N_UI_URL
}

p_link_crawler() {
  p_off CC_ENABLE_CRAWLER 1 1 && return 0
  p_kv_set CC_CRAWLER_DOCS_URL
}

p_link_sandbox() {
  p_off CC_ENABLE_SANDBOX 1 1 && return 0
  p_kv_set CC_SANDBOX_DOCS_URL
}
