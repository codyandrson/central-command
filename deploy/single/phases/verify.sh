# shellcheck shell=bash
# ============================================================================
# phases/verify.sh — the `verify` phase of the single-node install (deploy/single).
#
#   VERIFY: verify.sh (deployed, then live), the application's own
#   self-check, then the capability manifest.
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
#   ROWS (steps.tsv, phase `verify`) — step, kind, probe; a probe marked
#   (setup.sh) is shared with another phase or the driver and lives there:
#     verify-deployed                    run    p_verify_deployed
#     verify-live                        run    p_verify_live
#     selfcheck                          run    p_selfcheck
# ============================================================================

[[ -n "${CC_PHASE_VERIFY_LOADED:-}" ]] && return 0
CC_PHASE_VERIFY_LOADED=1

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: verify — deployed, then live, then the mandatory disclosure.
# ─────────────────────────────────────────────────────────────────────────────
capability_manifest() {
  load_env || return 1
  echo
  echo "== capability manifest — what THIS profile installed"
  echo "   Disclosure is mandatory: no profile may ship less capability than"
  echo "   another without saying so."
  echo
  echo "   installed:"
  echo "     postgres    the spine (schema auto-loaded)"
  echo "     litellm     the proxy + its own postgres and redis"
  echo "     neo4j       the knowledge graph store"
  echo "     graphiti    the graph's MCP service"
  if [[ "$CC_ENABLE_SANDBOX" == "1" ]]; then
    echo "     sandbox     agent sandbox on the PODMAN backend, rootless — WEAKER"
    echo "                 ISOLATION than the k3s profile's gVisor. The runner is a"
    echo "                 host process you start yourself (see README.md)."
  fi
  [[ "$CC_ENABLE_CRAWLER" == "1" ]] && \
    echo "     crawler     browser-rendering crawl service on 127.0.0.1:${CC_CRAWLER_PORT}"
  [[ "$CC_ENABLE_SPEECH" == "1" ]] && \
    echo "     speech      Whisper STT + Kokoro TTS engine on 127.0.0.1:${CC_SPEECH_PORT} (cc-tts / cc-stt)"
  [[ "$CC_ENABLE_N8N" == "1" ]] && \
    echo "     n8n         the integration facade (Gmail OAuth lives here)"
  echo
  echo "   NOT in this profile:"
  echo "     vlogs       the log console. Fluent Bit's container input tails"
  echo "                 CRI-format /var/log/containers/*.log, which podman does"
  echo "                 not produce, so the collector needs a redesign rather"
  echo "                 than a port. Deferred deliberately. Use instead:"
  echo "                   podman compose --env-file .env -f deploy/single/compose.yaml logs <service>"
  echo "                   ./setup.sh report"
  [[ "$CC_ENABLE_SANDBOX" == "1" ]] || echo "     sandbox     disabled by CC_ENABLE_SANDBOX=0"
  [[ "$CC_ENABLE_CRAWLER" == "1" ]] || echo "     crawler     disabled by CC_ENABLE_CRAWLER=0 (rung-1 HTTP fetch still works)"
  [[ "$CC_ENABLE_SPEECH" == "1" ]] || echo "     speech      disabled by CC_ENABLE_SPEECH=0 (cc-tts / cc-stt point at your own engines)"
  [[ "$CC_ENABLE_N8N" == "1" ]] || echo "     n8n         disabled by CC_ENABLE_N8N=0 (no n8n-backed integration selected)"
  return 0
}

phase_verify() {
  load_env || return 1
  step "verify-deployed" "every deployment/configuration assertion passed" \
    "$HERE/verify.sh" || return 1
  step "verify-live" "a real completion and the embedding dimension both check out" \
    env CC_VERIFY_LIVE=1 "$HERE/verify.sh" || return 1
  # AFTER verify.sh, as the record orders it, and with --pre-boot: the host
  # processes boot starts are not running yet. Its two model requests are the
  # only two in the whole install that prove what the agents will experience.
  # A FAIL here leaves verify/selfcheck not done, and boot/boot-api and
  # demo/demo-feed REQUIRE it — which is how an .env with an empty
  # CC_LLM_API_KEY stops reaching boot (P2's acceptance criterion).
  selfcheck_emit --pre-boot || return 1
  capability_manifest
}

# ── verify ──────────────────────────────────────────────────────────────────
# verify.sh itself polls for a quarter of an hour on a broken stack and its
# live half spends a token, so neither is a probe. What these read is the
# cheapest evidence that the SUBJECT of each assertion still exists; that the
# assertions passed is recorded by the row's version + fingerprint.
p_verify_deployed() {
  p_up_stack
}

p_verify_live() {
  local base key
  base="$(get_kv "$ENV_FILE" CC_LLM_BASE_URL)"
  [[ -n "$base" ]] || return 1
  key="$(get_kv "$ENV_FILE" CC_LLM_API_KEY)"
  [[ -n "$key" ]] || return 1
  p_kv_set CC_EMBED_DIM || return 1
  # The APP's own credential, not the admin key — the spine key has gone
  # missing three times by three mechanisms, and this is the cheap half of
  # catching it. The real proof is P2's self-check.
  printf 'Authorization: Bearer %s\n' "$key" \
    | curl -fsS -m 15 -o /dev/null -H @- "${base%/}/v1/models" 2>/dev/null
}

# verify/selfcheck. The self-check itself spends two model requests, so it is
# never a probe; what this reads is the cheapest evidence that it COULD still
# pass: the three app-facing keys it cannot pass without are set, and the
# spine key — the app's own credential, never the admin key — still answers
# /v1/models. An empty CC_LLM_API_KEY is false here, and stays false however
# the ledger row got written, which is what keeps it from reaching boot.
p_selfcheck() {
  local k base key
  for k in CC_LLM_API_KEY CC_LLM_BASE_URL CC_DEFAULT_MODEL; do
    is_placeholder "$(get_kv "$ENV_FILE" "$k")" && return 1
  done
  base="$(get_kv "$ENV_FILE" CC_LLM_BASE_URL)"
  key="$(get_kv "$ENV_FILE" CC_LLM_API_KEY)"
  printf 'Authorization: Bearer %s\n' "$key" \
    | curl -fsS -m 15 -o /dev/null -H @- "${base%/}/v1/models" 2>/dev/null
}
