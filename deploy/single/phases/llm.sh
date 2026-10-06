# shellcheck shell=bash
# ============================================================================
# phases/llm.sh — the `llm` phase of the single-node install (deploy/single).
#
#   LLM: secrets, LiteLLM up, the catalog (declared in .env or entered in the
#   LiteLLM UI — the run's one deliberate pause), every alias probed, and the
#   embedding dimension MEASURED into .env.
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
#   ROWS (steps.tsv, phase `llm`) — step, kind, probe; a probe marked
#   (setup.sh) is shared with another phase or the driver and lives there:
#     secrets                            run    p_secrets
#     up-litellm                         run    p_up_litellm
#     litellm-live                       run    p_litellm_live  (setup.sh)
#     up-speech                          run    p_speech_up
#     catalog-declared                   run    p_always  (setup.sh)
#     catalog                            run    p_catalog_aliases  (setup.sh)
#     catalog-filled                     gate   p_catalog_filled  (setup.sh)
#     probe-chat                         run    p_alias_cc_default
#     probe-structured                   run    p_alias_graphiti_llm
#     probe-rerank                       run    p_rerank_decided
#     speech-live                        run    p_speech_up
#     speech-model                       run    p_speech_models
#     probe-tts                          run    p_alias_cc_tts
#     probe-stt                          run    p_alias_cc_stt
#     probe-embed                        run    p_alias_cc_embedding
#     embed-dimension                    run    p_embed_dim  (setup.sh)
# ============================================================================

[[ -n "${CC_PHASE_LLM_LOADED:-}" ]] && return 0
CC_PHASE_LLM_LOADED=1

# Poll until an HTTP endpoint answers. Never a bare sleep: a fixed sleep is
# either a race or wasted minutes. Most readiness now lives in compose.yaml's
# healthchecks; this stays for the two waits that are honestly host-side —
# LiteLLM's first-boot Prisma migration and the speech engine, whose image's
# tool surface is not ours to assume in a healthcheck.
wait_http() { # wait_http <url> <seconds>
  local deadline=$(( SECONDS + $2 ))
  until curl -fsS -m 5 "$1" >/dev/null 2>&1; do
    (( SECONDS < deadline )) || return 1
    sleep 3
  done
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: llm — LiteLLM first, then everything is proven through its aliases.
# ─────────────────────────────────────────────────────────────────────────────
# The USER-ACTION gate for a probe failure. The proxy is alive at this point,
# so the operator can act in its UI; this prints the where and the what.
# Key VALUES are never printed — names only, per the output protocol.
#
# The line is printed under the ROW's name, `catalog-filled` — the manifest's
# rule is that a step's name IS the check-name it prints, and the ledger marks
# a row `gate` only when its own name said USERACTION. Until P5 this said
# `llm-models`, which is no row: the pause was recorded by probes instead, and
# the first laptop run's ledger read `catalog-filled done` at the very stop.
llm_gate() { # llm_gate <what-failed>
  useraction "catalog-filled" "$1 — operator action needed; see the instructions on stderr, then re-run: ./setup.sh (it resumes at llm)"
  note ""
  note "== LiteLLM needs your attention =="
  note "The proxy is UP. Its model catalog lives in its database and is yours"
  note "to fill in — setup created the required aliases as skeletons and stops"
  note "here, on purpose, so the providers are right before anything else is"
  note "deployed. No agent registers or edits models on your behalf."
  note ""
  note "  UI:           http://127.0.0.1:${CC_LITELLM_PORT}/ui   (Models + Endpoints)"
  note "  login:        username 'admin', password = CC_LLM_PROXY_ADMIN_KEY"
  note "                (or UI_USERNAME/UI_PASSWORD if set in .env)"
  note "                (the value is in the repo-root .env — not printed here)"
  note ""
  note "  For each alias listed above, edit the row: replace every PLACEHOLDER"
  note "  (the model id after the prefix, the api_base host) and enter the API"
  note "  key — or create a credential under Endpoints and attach it. Keep:"
  note "    cc-default     openai/<your chat model>          the spine's alias"
  note "    graphiti-llm   openai/<model>   PLAIN prefix — same as cc-default"
  note "                   (the old chat_completions/ bridge prefix now 404s;"
  note "                   the app's graphiti-core client uses chat-completions)"
  note "    cc-embedding   openai/<your embedding model>     dimension is permanent"
  note "    cc-rerank      OPTIONAL — graph search's reranker; the run does not"
  note "                   pause for it. A dedicated reranker (cohere/<id>, api_base"
  note "                   ending /v1/rerank, mode rerank), or a chat model with"
  note "                   thinking OFF. Setup probes which, and uses none if neither"
  note "                   answers."
  note "    cc-tts         openai/<your TTS model>           cockpit read-aloud"
  note "    cc-stt         openai/<your Whisper model>       cockpit voice input"
  if [[ "$CC_ENABLE_SPEECH" == "1" ]]; then
  note "  The bundled speech engine (cc-speech) answers both — enter, verbatim:"
  note "    cc-tts   model openai/${CC_SPEECH_TTS_MODEL}   api_base http://speech:8000/v1   key none"
  note "    cc-stt   model openai/${CC_SPEECH_STT_MODEL}   api_base http://speech:8000/v1   key none"
  note "  (or point cc-stt at hosted Whisper-convention models of your own)"
  fi
  note "  api_base is what the CONTAINER dials: a server on this machine is"
  note "  http://host.containers.internal:<port>/v1, never 127.0.0.1. A key the"
  note "  server ignores can be 'none', but the field must be non-empty."
  note ""
  note "  Not sure what your server names its models? Its own list answers that"
  note "  (GET <base url>/models). Declared in .env instead (below), every model"
  note "  id is checked against that list by the dry check ./setup.sh runs first."
  note "  A probe failed after you filled things in? The same upstream declared in"
  note "  .env and passing that check, but failing here = the alias row is wrong;"
  note "  failing the check too = the URL or key is wrong."
  note "  'curl: (28) Operation timed out' = the backend answered nothing within"
  note "  CC_PROBE_TIMEOUT seconds (default 300) — a shared or queued server may"
  note "  need longer:  CC_PROBE_TIMEOUT=900 ./setup.sh"
  note ""
  note ""
  note "  OR SKIP THIS PAUSE ENTIRELY (v2.44.0): declare the upstream in the"
  note "  repo-root .env and re-run — setup registers the rows itself."
  note "  Keys this deployment wants:"
  note "    $(llm_undeclared_keys)"
  note "  (the model keys take the UPSTREAM model id; a 127.0.0.1 base URL is"
  note "  rewritten to host.containers.internal for the row, because the row is"
  note "  dialled by a CONTAINER. The dry check ./setup.sh runs first probes them"
  note "  from THIS host before any of this is deployed.)"
  note ""
  note "  If the endpoint is only reachable through an egress PROXY: this release"
  note "  cannot reach it. CC_PROXY covers host-side downloads and the podman"
  note "  machine's pulls and builds; CC_CA_BUNDLE and CC_TLS_INSECURE reach the"
  note "  LiteLLM container (SSL_CERT_FILE / SSL_VERIFY) — but NO .env key gives"
  note "  that container a proxy (an open item in the 2026-09-23 design record)."
  note "  Do not add one to a tracked file: the install refuses a tree that differs"
  note "  from its release. It is a finding — ./setup.sh report writes the file to"
  note "  hand over."
  note ""
  note "When it looks right, re-run:  ./setup.sh   (it resumes at llm, validates every alias, then continues)"
}

# The CC_LLM_UPSTREAM_MODEL_* keys (plus the base/key pair) this deployment
# needs and .env does not have. Empty = the catalog can be registered from the
# answer file and the UI pause is unnecessary. ONE list, from
# deploy/env-lib.sh's cc_required_aliases, shared with the `check` command.
llm_undeclared_keys() {
  local out="" a key
  [[ -n "${CC_LLM_UPSTREAM_BASE_URL:-}" ]] || out="CC_LLM_UPSTREAM_BASE_URL"
  [[ -n "${CC_LLM_UPSTREAM_API_KEY:-}" ]] || out="${out:+$out }CC_LLM_UPSTREAM_API_KEY"
  for a in $(cc_required_aliases); do
    key="$(cc_alias_env_key "$a")"
    [[ -n "${!key:-}" ]] || out="${out:+$out }${key}(${a})"
  done
  printf '%s' "$out"
}

phase_llm() {
  load_env || return 1

  step "secrets" "credentials generated into the repo-root .env" "$HERE/make-secrets.sh" || return 1
  # make-secrets.sh may have generated CC_LLM_PROXY_ADMIN_KEY into the .env we
  # sourced before it; compose reads that file itself (--env-file), and the
  # register step below needs the key in this environment.
  load_env || return 1

  # `up -d` converges rather than collides on a re-run — a container whose
  # definition is unchanged is left alone. Named services only: the graph must
  # not be created before the embedding dimension has been MEASURED, and
  # compose brings each service's healthy dependencies up with it.
  step "up-litellm" "litellm + its database and redis are up and healthy" \
    compose up -d --wait litellm || return 1
  # ...and on the image their refs resolve to NOW: `up` leaves a running
  # container alone when only the image behind its ref changed (a re-pulled
  # main-stable, a new postgres:16 digest). See image_drift.
  catch_up_images "up-litellm" litellm-db litellm-redis litellm || return 1

  # LiteLLM runs its Prisma migrations at boot; 5 minutes is the honest budget.
  if wait_http "http://127.0.0.1:${CC_LITELLM_PORT}/health/liveliness" 300; then
    pass "litellm-live" "proxy answers /health/liveliness on 127.0.0.1:${CC_LITELLM_PORT}"
  else
    fail "litellm-live" "proxy never answered /health/liveliness — run: ./setup.sh report"
    return 1
  fi

  # The speech engine starts HERE, not in the stack phase: its aliases are
  # probed below, and a probe against a service that is not up yet is a false
  # gate. No --wait: it has no healthcheck, because its first boot spends up
  # to half an hour downloading models (polled for real further down).
  if [[ "$CC_ENABLE_SPEECH" == "1" ]]; then
    step "up-speech" "speech engine started on 127.0.0.1:${CC_SPEECH_PORT}" \
      compose --profile speech up -d speech || return 1
  else
    pass "up-speech" "skipped (CC_ENABLE_SPEECH=0 — cc-tts/cc-stt point at engines of your own)"
  fi

  # The catalog is DB-stored (store_model_in_db) and managed in the proxy's
  # UI — operator decision, 2026-08-30. register-models.py is CREATE-ONLY: an
  # absent alias becomes a skeleton (name + invariants + PLACEHOLDER where the
  # provider goes) and an existing one is never touched. It exits 3 on a
  # fresh catalog, on a placeholder left in, or on a broken invariant — each
  # is the operator's, so it is the USER-ACTION gate, before anything else
  # deploys.
  #
  # register-models.py is SHARED with the k3s profile and reads the admin
  # credential as LITELLM_MASTER_KEY, so the one fact is handed over under the
  # name that script looks for — the seam, rather than a second key.
#
  # SINCE v2.44.0 the upstream may be DECLARED in .env (design record D3), and
  # then register-models.py creates REAL rows and there is nothing to pause
  # for. The keys are already exported by load_env, so the script sees them; the
  # pause below is the FALLBACK, not the path.
  local undeclared; undeclared="$(llm_undeclared_keys)"
  # NOT a WARN when the catalog is undeclared (v2.45.1): entering the provider in
  # the LiteLLM UI is the PRIMARY methodology — the same one the k3s profile
  # uses — so an install that takes it is not a degraded install. A WARN here
  # also made every successful UI-driven run finish at exit 2, which reads as
  # "completed with warnings" over a deliberate choice.
  if [[ -z "$undeclared" ]]; then
    pass "catalog-declared" "every alias ($(cc_required_aliases)) is declared in .env — registering real rows, so there is no UI pause"
  else
    pass "catalog-declared" "the catalog is the LiteLLM UI's (the primary method, as on k3s): this phase creates the alias skeletons and PAUSES at exit 3 for you to fill in the provider. The optional shortcut past that pause is: $undeclared"
  fi
  local rrc=0
  CC_LITELLM_URL="http://127.0.0.1:${CC_LITELLM_PORT}" \
  LITELLM_MASTER_KEY="${CC_LLM_PROXY_ADMIN_KEY:-}" \
    $PY "$REPO_ROOT/deploy/pi/litellm/register-models.py" --policy "$HERE/models.json" \
      --require "$(cc_required_aliases)" >&2 || rrc=$?
  # --require is what makes exit 3 mean "a REQUIRED alias is not filled in":
  # cc-tts/cc-stt with CC_ENABLE_SPEECH=0 are created as skeletons and
  # reported `optional`, never a pause. Before it, a catalog filled in through
  # the UI still paused here whenever .env did not ALSO declare the alias —
  # the Windows testbed (2026-09-25) sat at this gate with every required row
  # `ok` and two speech skeletons nothing on that install would ever call.
  case "$rrc" in
    0) pass "catalog" "every required alias ($(cc_required_aliases)) is registered, filled in and consistent" ;;
    3)
      llm_gate "the model catalog needs your provider details for a required alias (see the list above)${undeclared:+. Declaring them in .env instead removes this pause entirely: $undeclared}"
      return 3
      ;;
    *) fail "catalog" "register-models.py failed (exit $rrc) — run: ./setup.sh report"; return 1 ;;
  esac

  # The probes — one real request per alias, through the proxy, exactly the
  # call production makes. A failure is the same gate: the row is the
  # operator's to fix.
  if ! step "probe-chat" "a real completion came back through the cc-default alias" \
    "$HERE/discover-llm.sh" --proxy chat cc-default; then
    llm_gate "the cc-default alias did not return a completion"
    return 3
  fi
  if ! step "probe-structured" "graphiti-llm returned schema-constrained JSON through chat/completions" \
    "$HERE/discover-llm.sh" --proxy structured graphiti-llm; then
    llm_gate "the graphiti-llm alias did not return schema-constrained JSON (a chat_completions/ prefix on the registration is a likely cause — it should be a plain openai/<model>)"
    return 3
  fi
  rerank_decide || return $?

  # Speech: one synthesis, then transcribe what it said — the round trip proves
  # both aliases with real audio.
  if [[ "$CC_ENABLE_SPEECH" == "1" ]]; then
    if wait_http "http://127.0.0.1:${CC_SPEECH_PORT}/health" 120; then
      pass "speech-live" "cc-speech answers /health on 127.0.0.1:${CC_SPEECH_PORT}"
    else
      fail "speech-live" "cc-speech never answered /health — run: ./setup.sh report"
      return 1
    fi
    # The engine boots EMPTY: speaches 0.8.3 has no preload setting (upstream
    # master's `preload_models` is unreleased, checked 2026-09-03), so models
    # are installed through its API. The first call downloads from
    # CC_HF_ENDPOINT into the speech-models volume; a re-run answers 201 at once.
    local m
    for m in "$CC_SPEECH_TTS_MODEL" "$CC_SPEECH_STT_MODEL"; do
      step "speech-model" "speech model ${m} installed" \
        curl -fsS --max-time 1800 -o /dev/null -X POST "http://127.0.0.1:${CC_SPEECH_PORT}/v1/models/${m}" \
        || { fail "speech-model" "could not install ${m} — CC_HF_ENDPOINT reachable? run: ./setup.sh report"; return 1; }
    done
  fi
  # The speech pair is probed only when this deployment REQUIRES it
  # (cc_required_aliases, i.e. CC_ENABLE_SPEECH=1). With speech off the two
  # aliases are skeletons by design and probing them was a guaranteed gate —
  # the Windows testbed (2026-09-25) cleared the catalog and then stopped
  # here on "cc-tts did not return audio" for an engine it had not installed.
  if [[ "${CC_ENABLE_SPEECH:-1}" == "1" ]]; then
    # A .mp3 suffix so the file's name agrees with its bytes on the far side.
    local mp3; mp3="$(mktemp --suffix=.mp3)"
    if ! step "probe-tts" "cc-tts synthesised speech" \
      "$HERE/discover-llm.sh" --proxy speech cc-tts "$mp3"; then
      rm -f "$mp3"; llm_gate "the cc-tts alias did not return audio"
      return 3
    fi
    if ! step "probe-stt" "cc-stt transcribed what cc-tts said" \
      "$HERE/discover-llm.sh" --proxy transcribe cc-stt "$mp3"; then
      rm -f "$mp3"; llm_gate "the cc-stt alias did not return a transcription"
      return 3
    fi
    rm -f "$mp3"
  else
    pass "probe-tts" "skipped (CC_ENABLE_SPEECH=0 — cc-tts/cc-stt are not required here)"
    pass "probe-stt" "skipped (CC_ENABLE_SPEECH=0)"
  fi

  # THE measurement. Never a model card: a mis-sized vector corrupts the Neo4j
  # index instead of erroring, and the dimension is permanent once it exists.
  # stderr stays visible: this is the one probe whose command is captured
  # rather than run through step(), and a silenced 404 ("no router for
  # requested model") reads exactly like a timeout (2026-09-04 Windows run).
  local dim
  note "--> $HERE/discover-llm.sh --proxy embed cc-embedding"
  dim="$("$HERE/discover-llm.sh" --proxy embed cc-embedding | tail -1)"
  if [[ ! "$dim" =~ ^[0-9]+$ ]]; then
    fail "probe-embed" "failed — see stderr for the command's own output"
    llm_gate "the cc-embedding alias did not return a vector"
    return 3
  fi
  pass "probe-embed" "cc-embedding returned a ${dim}-dimension vector"

  local cur; cur="$(get_kv "$ENV_FILE" CC_EMBED_DIM)"
  if [[ -n "$cur" && "$cur" != "$dim" ]]; then
    fail "embed-dimension" "CC_EMBED_DIM is already ${cur} but the endpoint returns ${dim} — REFUSING to change it. It is written into the Neo4j vector index; changing it means dropping the index and re-embedding the graph. Fix the model, or clear CC_EMBED_DIM deliberately on a graph you are willing to lose."
    return 1
  fi
  set_kv "$ENV_FILE" CC_EMBED_DIM "$dim"
  pass "embed-dimension" "CC_EMBED_DIM=${dim} recorded in .env"
}

# ── llm/probe-rerank: graph search's reranker, decided by PROBING ────────────
# Design record 2026-10-04, D3 as rebuilt in v2.62.0 — the operator's decision:
# every deployment gets the best reranking its environment provides, through ONE
# optional alias, `cc-rerank`. A dedicated reranker (LiteLLM's /rerank) if the
# alias is one; otherwise an ordinary chat model asked True/False per candidate
# (nearly as good, measured 2026-10-05, and slower); otherwise none (rank fusion
# alone). The probes are discover-llm.sh's `rerank` and `rerank-chat`, through
# the proxy under the admin key; the app's own key gains the alias in `app`
# (spine_scope_aliases), and verify/selfcheck's graph-rerank row re-proves it
# AS THE APP.
#
#   CC_GRAPH_RERANK_KIND set (by the operator, or by an earlier run — a written
#     kind is a pin): that kind is PROVEN, never re-detected; a failure stops
#     the run (USERACTION), because a configured reranker that fails makes fact
#     search ERROR. To re-detect, empty the key and re-run.
#   no kind: probe /rerank, then the chat shape. Found: write the kind, and the
#     alias when .env has none. Neither: when the operator set
#     CC_GRAPH_RERANK_ALIAS that is a USERACTION (same reason); when nothing
#     names the alias, a WARN — it stays UNUSED, and the run says why and how.
#   cc-rerank not mapped (absent, or still its PLACEHOLDER skeleton) and no
#     alias in .env: PASS, no reranker. Never mapped to the extraction model
#     automatically — that costs up to 50 chat calls per agent fact search (2 x
#     the tools' 25-fact limit) on the model that extracts; the operator opts in
#     by answering CC_LLM_UPSTREAM_MODEL_CC_RERANK.
rerank_probe_kind() { # rerank_probe_kind <rerank|chat> <alias>
  local sub=rerank
  [[ "$1" == chat ]] && sub=rerank-chat
  note "--> $HERE/discover-llm.sh --proxy $sub $2"
  "$HERE/discover-llm.sh" --proxy "$sub" "$2" >&2
}

rerank_decide() {
  local alias kind pinned_alias unfilled found=""
  pinned_alias="$(q_unquote "$(get_kv "$ENV_FILE" CC_GRAPH_RERANK_ALIAS)")"
  kind="$(q_unquote "$(get_kv "$ENV_FILE" CC_GRAPH_RERANK_KIND)")"
  alias="${pinned_alias:-cc-rerank}"
  local fix="fact search ERRORS until it is fixed (a configured reranker that fails is never skipped). Fix the $alias row in the LiteLLM UI — a dedicated reranker answers /rerank; a chat model must return logprobs and have thinking OFF on the alias — or empty CC_GRAPH_RERANK_ALIAS in .env to run without a reranker, then re-run: ./setup.sh"

  if [[ -z "$pinned_alias" ]]; then
    if ! unfilled="$(catalog_unfilled "$alias")"; then
      warn "probe-rerank" "could not read the proxy's /model/info to see whether $alias is mapped — no reranker this run (fact search ranks by rank fusion alone); re-run ./setup.sh"
      return 0
    fi
    if [[ -n "$unfilled" ]]; then
      pass "probe-rerank" "no reranker: $alias is not mapped (absent, or still its skeleton) — fact search ranks by rank fusion alone, the lower-quality ordering. To turn reranking on: answer CC_LLM_UPSTREAM_MODEL_CC_RERANK (./setup.sh configure — the same model as graphiti-llm is the one-line choice when no dedicated reranker exists), or fill the $alias row in the LiteLLM UI and set CC_GRAPH_RERANK_ALIAS=$alias in .env; then re-run ./setup.sh"
      return 0
    fi
  fi

  if [[ -n "$kind" ]]; then
    case "$kind" in
      rerank|chat) ;;
      *) fail "probe-rerank" "CC_GRAPH_RERANK_KIND=$kind is not a reranker kind — set rerank or chat, or empty it so setup probes; the API refuses to build the graph client meanwhile"; return 1 ;;
    esac
    if rerank_probe_kind "$kind" "$alias"; then
      [[ -n "$pinned_alias" ]] || set_kv "$ENV_FILE" CC_GRAPH_RERANK_ALIAS "$alias"
      pass "probe-rerank" "the $kind reranker $alias answers (CC_GRAPH_RERANK_KIND=$kind is set, so it was proven, not re-detected — empty the key to re-detect)"
      return 0
    fi
    useraction "probe-rerank" "CC_GRAPH_RERANK_KIND=$kind, but $alias does not answer as a $kind reranker — $fix"
    return 3
  fi

  if rerank_probe_kind rerank "$alias"; then
    found=rerank
  elif rerank_probe_kind chat "$alias"; then
    found=chat
  fi
  if [[ -n "$found" ]]; then
    [[ -n "$pinned_alias" ]] || set_kv "$ENV_FILE" CC_GRAPH_RERANK_ALIAS "$alias"
    set_kv "$ENV_FILE" CC_GRAPH_RERANK_KIND "$found"
    if [[ "$found" == rerank ]]; then
      pass "probe-rerank" "$alias is a dedicated reranker (/rerank answered) — CC_GRAPH_RERANK_ALIAS=$alias, CC_GRAPH_RERANK_KIND=rerank written to .env; graph search reranks with it"
    else
      pass "probe-rerank" "$alias answers True/False with logprobs as a chat model — CC_GRAPH_RERANK_ALIAS=$alias, CC_GRAPH_RERANK_KIND=chat written to .env; graph search reranks with it (nearly the quality of a dedicated reranker, slower: one chat call per candidate)"
    fi
    return 0
  fi
  if [[ -n "$pinned_alias" ]]; then
    useraction "probe-rerank" "CC_GRAPH_RERANK_ALIAS=$alias answers neither a /rerank request nor a True/False chat question with logprobs — $fix"
    return 3
  fi
  warn "probe-rerank" "$alias is mapped but answers neither a /rerank request nor a True/False chat question with logprobs, so it is NOT used — fact search ranks by rank fusion alone. Usual causes: the endpoint returns no logprobs; the model is a reasoning model and thinking is not disabled on the alias (chat_template_kwargs enable_thinking false on a llama.cpp backend); a rerank row whose api_base does not end in /v1/rerank. Fix the row, set CC_GRAPH_RERANK_ALIAS=$alias in .env, and re-run ./setup.sh (it then probes again, and stops if the alias still fails)"
  return 0
}

# ── llm ─────────────────────────────────────────────────────────────────────
p_secrets() {
  local k
  for k in "${CC_GENERATED_KEYS[@]}"; do
    is_placeholder "$(get_kv "$ENV_FILE" "$k")" && return 1
  done
  return 0
}

# llm/up-litellm: the proxy answers AND the trio runs the images their refs
# resolve to now — the same question the stack row asks, for the three services
# this phase brings up (catch_up_images there).
p_up_litellm() {
  p_litellm_live || return 1
  image_drift litellm-db litellm-redis litellm
}

p_speech_up() {
  p_off CC_ENABLE_SPEECH 1 1 && return 0
  curl -fsS -m 5 -o /dev/null \
    "http://127.0.0.1:$(p_flag CC_SPEECH_PORT 8093)/health" 2>/dev/null
}

p_speech_models() {
  p_off CC_ENABLE_SPEECH 1 1 && return 0
  local listed m
  listed="$(curl -fsS -m 15 "http://127.0.0.1:$(p_flag CC_SPEECH_PORT 8093)/v1/models" 2>/dev/null)" || return 1
  for m in "$(p_flag CC_SPEECH_TTS_MODEL speaches-ai/Kokoro-82M-v1.0-ONNX)" \
           "$(p_flag CC_SPEECH_STT_MODEL Systran/faster-whisper-small)"; do
    [[ "$listed" == *"$m"* ]] || return 1
  done
  return 0
}

p_alias() { # p_alias <alias>
  local listed
  listed="$(p_models_json)" || return 1
  [[ "$listed" == *"\"$1\""* ]]
}

p_alias_cc_default() {
  p_alias cc-default
}

p_alias_graphiti_llm() {
  p_alias graphiti-llm
}

# llm/probe-rerank: the decision is recorded — no reranker alias in use, or a
# kind beside it. (The WARN case, mapped but answering neither shape, leaves
# the alias empty and so reads done: the run said why, and setting
# CC_GRAPH_RERANK_ALIAS — a `reads` key — makes the next run probe again.)
p_rerank_decided() {
  is_placeholder "$(get_kv "$ENV_FILE" CC_GRAPH_RERANK_ALIAS)" && return 0
  ! is_placeholder "$(get_kv "$ENV_FILE" CC_GRAPH_RERANK_KIND)"
}

p_alias_cc_embedding() {
  p_alias cc-embedding
}

p_alias_cc_tts() {
  p_off CC_ENABLE_SPEECH 1 1 && return 0
  p_alias cc-tts
}

p_alias_cc_stt() {
  p_off CC_ENABLE_SPEECH 1 1 && return 0
  p_alias cc-stt
}
