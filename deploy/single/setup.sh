#!/usr/bin/env bash
# ============================================================================
# setup.sh — the deterministic driver for the single-node install.
#
#   Design record: docs/superpowers/specs/2026-08-25-deterministic-setup.md.
#   The rule it exists to enforce: an AGENT elicits answers, diagnoses a
#   failure, and conducts the interview. EVERYTHING that mutates anything is
#   this script. If a conductor is composing a command, it is off the rails.
#
#   This file ORCHESTRATES; it does not reimplement. Every real operation
#   already lives in a script next to it (make-secrets.sh, resolve-images.sh,
#   discover-llm.sh, build-*-image.sh, verify.sh) or in compose.yaml, and is
#   called, not copied.
#
#   SUBSTRATE: Compose (2026-09-03 deploy refactor, D1). compose.yaml replaced
#   render.sh + five envsubst templates + `podman kube play` choreography.
#   Readiness lives in that file as healthchecks and depends_on, so the deploy
#   phases are a `compose up -d --wait` rather than a play-then-poll sequence.
#
#   PHASES — each a subcommand, each individually re-runnable via the SAME
#   code path as the full run (kubeadm's shape):
#
#     validate    offline check of .env (the repo-root one — THE answer file
#                 since v2.42.0; deploy/single/.env is retired). No side effects.
#     preflight   named environment checks. No side effects.
#     machine     write the podman MACHINE from .env — the CA into its trust
#                 store, the registries mirror/insecure drop-in, the proxy
#                 drop-in. A no-op where there is no machine (bare Linux).
#                 `./setup.sh machine --dry-run` reports the diff instead, and
#                 that is what preflight calls.
#     fetch       acquire every dependency (public or mirror) — the only phase
#                 that needs the network; stops for the operator per artifact.
#     llm         secrets + LiteLLM up + probe its aliases + MEASURE the
#                 embedding dimension into .env
#     stack       assert the local images + bring the core stack up (compose)
#     app         venv, editable install, the derived .env values, mint the
#                 spine's virtual key, cockpit build
#     verify      verify.sh (deployed) then live, then the capability manifest
#     test        the pytest gate, via the venv (no activation stumbles)
#     boot        elicit the operator's name (once), start the API detached,
#                 assert the roster hired          (counterpart: ./setup.sh stop)
#     demo        fixture email -> triage -> YOUR approval in the cockpit ->
#                 a real execution + provenance verified on the event log
#
#     stop        stop the API that `boot` started
#     status      re-run postconditions only, nothing mutating
#     diagnose    write <state>/setup-diagnostics.txt for pasting to Claude
#
#   No argument = ALL ELEVEN phases in order — zero to a working, human-approved
#   demo in one command (2026-08-28), stopping at the first hard failure or
#   gate. There is no state file: every step is idempotent and the late phases
#   probe REALITY to skip (a healthy API skips test+boot; a decided proposal
#   skips demo), so RESUME IS RE-RUN.
#
#   OUTPUT PROTOCOL (cloud-init's exit taxonomy, Replicated's check lines):
#     stdout   one line per check: `PASS|WARN|FAIL|USERACTION <check>: <message>`
#     stderr   everything else — subprocess output, detail, progress
#     exit 0   clean · 1 hard failure · 2 completed with warnings
#              · 3 stopped for USER ACTION (the operator's move, not an error)
#     log      every check line is also appended, timestamped, to
#              $CC_STATE_DIR/setup-log.txt — the durable history a re-run
#              (or a diagnosing agent) reads first. NOTHING this script writes
#              lands inside the checkout except the answer file itself
#              (2026-09-23 design record, D7) — so `git status` is clean after
#              every command and an update never merges around a log file.
#
#   Secret VALUES are never printed. Keys are referred to by NAME.
# ============================================================================

# NOT -e: a phase's checks must all report, and hard aborts are explicit
# (`|| return 1`) so the reason is always a FAIL line rather than a silent
# exit. -u and pipefail still hold.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
# ONE answer file (2026-09-23 design record, D1): the repo-root .env holds the
# app's configuration AND this profile's. deploy/single/.env is retired and is
# migrated into it by load_env below. compose is always invoked with
# --env-file "$ENV_FILE" — it has no .env of its own to find any more.
ENV_FILE="$REPO_ROOT/.env"
# shellcheck source=../env-lib.sh
. "$REPO_ROOT/deploy/env-lib.sh"
# The podman MACHINE's configuration, rendered as text. Pure functions, so the
# decisions are unit-tested on Linux even though the writer can only run where
# a machine exists (Windows/macOS) — design record D4.
# shellcheck source=machine-lib.sh
. "$HERE/machine-lib.sh"
# Python on Windows encodes a PIPED stdout in the ANSI code page, so the em
# dashes in register-models.py's operator banner reached the log as cp1252
# bytes inside otherwise-UTF-8 output (2026-09-03 Windows run: `�`).
export PYTHONUTF8=1

# Windows has no real python3: the WindowsApps stub answers `command -v` but
# exits 49 (2026-08-21 Windows validation, W2) — so probe by RUNNING it.
PY=""
for c in python3 python; do
  command -v "$c" >/dev/null 2>&1 && "$c" -c '' 2>/dev/null && { PY="$c"; break; }
done
[[ -n "$PY" ]] || PY="uv run --python 3.12 python"
# $PY may be multiple words (the uv fallback) — always invoke it unquoted.

# ── output protocol ─────────────────────────────────────────────────────────
# Exit taxonomy (2026-08-27 contract): 0 clean · 1 hard failure · 2 warnings ·
# 3 USER ACTION REQUIRED — the run stopped deliberately for the operator; the
# last USERACTION line says what for. A conductor (human or agent) re-runs the
# phase after acting; every phase is idempotent so that always converges.
FAILS=0; WARNS=0; ACTIONS=0
CURPHASE=""
# Both resolved by init_state() before the first log line — the state
# directory is where every generated file goes (D7), and it is not knowable
# until .env has been consulted for CC_STATE_DIR.
STATE_DIR=""
LOGFILE=""
logline() { printf '%s %s %s\n' "$(date -u +%FT%TZ)" "${CURPHASE:-run}" "$*" >>"$LOGFILE" 2>/dev/null || true; }
pass() { printf 'PASS %s: %s\n' "$1" "$2"; logline "PASS $1: $2"; }
warn() { printf 'WARN %s: %s\n' "$1" "$2"; WARNS=$((WARNS+1)); logline "WARN $1: $2"; }
fail() { printf 'FAIL %s: %s\n' "$1" "$2"; FAILS=$((FAILS+1)); logline "FAIL $1: $2"; }
useraction() { printf 'USERACTION %s: %s\n' "$1" "$2"; ACTIONS=$((ACTIONS+1)); logline "USERACTION $1: $2"; }
note() { printf '%s\n' "$*" >&2; }

# Run a step, sending all of its chatter to stderr. PASS on success, FAIL on
# anything else — the step's own output is the detail, on stderr where the
# protocol says detail goes.
step() { # step <check-name> <success-message> <cmd...>
  local name="$1" msg="$2"; shift 2
  note "--> $*"
  if "$@" >&2; then pass "$name" "$msg"; return 0; fi
  fail "$name" "failed — see stderr for the command's own output"
  return 1
}

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

# ── the state directory (D7) ────────────────────────────────────────────────
# Resolved ONCE per run, before anything is logged. cc_state_dir creates it
# 0700 and, on the first run, writes the resolved absolute path back into .env
# — the one .env write a read-only command makes, so bash and Python never
# disagree about the spelling of a Windows path.
init_state() {
  [[ -n "$STATE_DIR" ]] && return 0
  STATE_DIR="$(cc_state_dir "$ENV_FILE" "$REPO_ROOT")" \
    || STATE_DIR="${TMPDIR:-/tmp}/central-command-state"
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  LOGFILE="$STATE_DIR/setup-log.txt"
}

# ── the podman machine, if there is one ─────────────────────────────────────
# On bare Linux there is none and every machine-aware check below is a no-op.
# Resolved once: `podman machine list` is a subprocess and load_env runs per
# phase. An empty answer is cached as the single space, so "asked and there is
# none" is distinguishable from "not asked yet" under set -u.
MACHINE_NAME=""
machine_name() {
  if [[ -z "$MACHINE_NAME" ]]; then
    MACHINE_NAME=" "
    if command -v podman >/dev/null 2>&1; then
      local n; n="$(podman machine list --format '{{.Name}}' 2>/dev/null | head -1 | tr -d '\r')"
      [[ -n "$n" ]] && MACHINE_NAME="$n"
    fi
  fi
  [[ "$MACHINE_NAME" == " " ]] || printf '%s' "$MACHINE_NAME"
}

# One command inside the machine. `podman machine ssh <name> -- <cmd>` is
# non-interactive when a command is given (podman-machine-ssh(1): an
# interactive session is established only when NO command is provided).
# </dev/null on every call: an ssh that inherits this script's stdin eats the
# manifest a caller is reading from.
machine_sh() { # machine_sh <shell-command>
  local m; m="$(machine_name)"
  [[ -n "$m" ]] || return 1
  podman machine ssh "$m" -- "$1" </dev/null 2>/dev/null
}
# Same, with stdin piped in — how a file gets INTO the machine without a share.
machine_sh_stdin() { # machine_sh_stdin <shell-command> < file
  local m; m="$(machine_name)"
  [[ -n "$m" ]] || return 1
  podman machine ssh "$m" -- "$1" 2>/dev/null
}

# ── the compose provider ────────────────────────────────────────────────────
# `podman compose` on the targets; `docker compose` is the dev-box fallback.
# Detected once, by RUNNING it (the same reason $PY is probed by running):
# `podman compose` exists as a subcommand even when no provider is installed
# behind it, and answers with an error only when actually invoked.
# podman's insecure flag set, resolved by load_env (see cc_export_tls_env).
PULL_TLS=()

COMPOSE_BIN=()
compose_detect() {
  (( ${#COMPOSE_BIN[@]} )) && return 0
  local c
  for c in podman docker; do
    command -v "$c" >/dev/null 2>&1 || continue
    if "$c" compose version >/dev/null 2>&1; then COMPOSE_BIN=("$c" compose); return 0; fi
  done
  return 1
}
# Every compose call goes through here: one file, one answer file, one profile
# set, one place to get the flags right.
#
# --env-file is MANDATORY now and it goes BEFORE -f: compose's default
# environment file is the one beside the compose file, and that file
# (deploy/single/.env) no longer exists. Both providers declare --env-file and
# -f on the same top-level parser — docker compose's CLI options are
# order-independent and podman-compose's argparse takes them in any order
# before the subcommand (podman_compose.py, verified 2026-09-23) — so this
# position works on both, and anyone running compose BY HAND must pass it too.
compose() { # compose <args...>
  compose_detect || { fail "compose" "no compose provider — install podman-compose (or the docker compose plugin)"; return 1; }
  "${COMPOSE_BIN[@]}" --env-file "$ENV_FILE" -f "$HERE/compose.yaml" "$@"
}
# CC_ENABLE_* -> --profile flags, into the global PROFILE_FLAGS. The sandbox is
# deliberately absent: it has no container here (its containers are created on
# demand by the runner, a host process), so its only deployable artifact is
# the image.
PROFILE_FLAGS=()
compose_profile_flags() {
  PROFILE_FLAGS=()
  [[ "${CC_ENABLE_N8N:-0}" == 1 ]] && PROFILE_FLAGS+=(--profile n8n)
  [[ "${CC_ENABLE_CRAWLER:-1}" == 1 ]] && PROFILE_FLAGS+=(--profile crawler)
  [[ "${CC_ENABLE_SPEECH:-1}" == 1 ]] && PROFILE_FLAGS+=(--profile speech)
  return 0
}

# ── .env helpers ────────────────────────────────────────────────────────────
load_env() {
  init_state
  if [[ ! -f "$ENV_FILE" ]]; then
    fail "answer-file" "$ENV_FILE not found — start from: cp .env.example .env (at the repo root; since v2.42.0 there is ONE answer file and deploy/single/.env is not it)"
    return 1
  fi
  # MIGRATION, before anything reads a value: an install made before v2.42.0
  # keeps its answers in deploy/single/.env (plus web/.env and
  # deploy/discovery.conf). Each is folded into $ENV_FILE under the CC_ name
  # where the same fact already had one, then MOVED to
  # $STATE_DIR/migrated/ — never deleted, never printed. A no-op once the old
  # files are gone, which is why it can run at the top of every phase.
  local mig
  if ! mig="$(cc_migrate_legacy_env "$ENV_FILE" "$REPO_ROOT" "$STATE_DIR")"; then
    fail "env-migrate" "could not migrate the retired config files into $ENV_FILE — check the permissions on $STATE_DIR"
    return 1
  fi
  if [[ -n "$mig" ]]; then
    pass "env-migrate" "$mig"
  fi
  # Sourceable, before the first source. An unquoted value with a space is a
  # COMMAND under `set -a; . .env` — the variable ends up unset here and the
  # `set -e` scripts (make-secrets.sh, verify.sh, the build scripts) abort
  # outright, with a message that names a word from the value rather than the
  # key. Caught once, here, by NAME.
  local bad; bad="$(cc_env_unquoted_keys "$ENV_FILE")"
  if [[ -n "$bad" ]]; then
    fail "answer-file" "these keys in .env have unquoted values containing a space (or a shell metacharacter) — the deploy scripts SOURCE this file, so each would be run as a command instead of assigned. Wrap the value in double quotes: $bad"
    return 1
  fi
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  # Still the container-name prefix (compose's container_name), which is how
  # verify.sh, diagnose and update.sh address containers. It is no longer a
  # DNS name: compose gives every service its own.
  : "${CC_POD_PREFIX:=cc-}"
  : "${CC_LITELLM_PORT:=4000}"
  : "${CC_LITELLM_DB_PORT:=5443}"
  : "${CC_CRAWLER_PORT:=8091}"
  : "${CC_SPEECH_PORT:=8093}"
  : "${CC_SPEECH_TTS_MODEL:=speaches-ai/Kokoro-82M-v1.0-ONNX}"
  : "${CC_SPEECH_STT_MODEL:=Systran/faster-whisper-small}"
  : "${CC_ENABLE_N8N:=0}"
  : "${CC_ENABLE_CRAWLER:=1}"
  : "${CC_ENABLE_SANDBOX:=1}"
  : "${CC_ENABLE_SPEECH:=1}"
  : "${CC_AIRGAP:=0}"
  : "${CC_TLS_INSECURE:=0}"
  # Tool-facing exports. uv reads no PIP_* variable (and UV_INDEX_URL is
  # deprecated), npm reads npm_config_* case-insensitively — so one seam each,
  # fanned out here to every name the tools actually look at.
  if [[ -n "${CC_PYPI_INDEX_URL:-}" ]]; then
    export PIP_INDEX_URL="$CC_PYPI_INDEX_URL" UV_DEFAULT_INDEX="$CC_PYPI_INDEX_URL"
  fi
  if [[ -n "${CC_PYTHON_MIRROR:-}" ]]; then export UV_PYTHON_INSTALL_MIRROR="$CC_PYTHON_MIRROR"; fi
  if [[ -n "${CC_NPM_REGISTRY:-}" ]]; then export NPM_CONFIG_REGISTRY="$CC_NPM_REGISTRY"; fi
  # ── the two trust knobs (2026-09-23 design record, D4) ────────────────────
  # CC_CA_BUNDLE and CC_TLS_INSECURE, fanned out by deploy/env-lib.sh's
  # cc_export_tls_env to every name the host-side toolchain reads (curl, uv,
  # pip, npm, node, git) plus the generated .curlrc in the state dir. ONE
  # function, called by every command in this profile, so the list cannot drift
  # per script. podman is not reachable that way at all — its pulls and builds
  # verify inside the MACHINE, which is what the `machine` phase writes and
  # what --tls-verify=false covers.
  cc_export_tls_env "$STATE_DIR"
  # The one flag set podman takes for it (no variable reaches a pull or build).
  PULL_TLS=()
  [[ "$CC_TLS_INSECURE" == "1" ]] && PULL_TLS=(--tls-verify=false)
  # Never silent, never a PASS. The operator dropped the "never disable
  # verification" rule on 2026-09-23 (the site assumes security through
  # isolation), so this is the trade stated once per run — not a lecture, and
  # not a refusal.
  if [[ "$CC_TLS_INSECURE" == "1" ]]; then
    warn "tls-insecure" "$(cc_tls_insecure_warn_text "curl, uv, pip, npm, node and git on this host; podman pulls and the three image builds (--tls-verify=false); the podman machine's registries drop-in; LiteLLM's outbound calls (SSL_VERIFY=False). NOT the speech engine — Hugging Face's client has no insecure switch, so that one needs CC_CA_BUNDLE or pre-placed snapshots")"
  fi
  # ── the CA, for the two containers that talk upstream ─────────────────────
  # DERIVED, exported for compose, and never written into .env: they are
  # composed from CC_CA_BUNDLE and CC_TLS_INSECURE, and v2.42.0's rule is that
  # one fact has one key. Only LiteLLM (the enterprise LLM endpoint) and the
  # optional speech engine (Hugging Face) make outbound TLS connections.
  #
  # The mount SOURCE must be a path the container runtime can see: on a
  # podman-machine host that is the copy the `machine` phase installed INSIDE
  # the machine, not the host path. /dev/null is the neutral source when no CA
  # is configured — a bind mount of it is accepted and reads as empty, so
  # compose needs no conditional volume list.
  export CC_LITELLM_SSL_VERIFY=True
  export CC_CA_BUNDLE_IN_CONTAINER=""
  export CC_CA_BUNDLE_MOUNT_SRC="/dev/null"
  [[ "$CC_TLS_INSECURE" == "1" ]] && export CC_LITELLM_SSL_VERIFY=False
  if [[ -n "${CC_CA_BUNDLE:-}" ]]; then
    export CC_CA_BUNDLE_IN_CONTAINER="/etc/cc/ca.pem"
    if [[ -n "$(machine_name)" ]]; then
      export CC_CA_BUNDLE_MOUNT_SRC="$CC_MACHINE_CA_PEM"
    else
      export CC_CA_BUNDLE_MOUNT_SRC="$CC_CA_BUNDLE"
    fi
  fi
  if [[ -n "${CC_PROXY:-}" ]]; then
    export http_proxy="$CC_PROXY" https_proxy="$CC_PROXY" \
           HTTP_PROXY="$CC_PROXY" HTTPS_PROXY="$CC_PROXY"
    # Loopback must bypass the proxy — every phase curls 127.0.0.1.
    export no_proxy="127.0.0.1,localhost,host.containers.internal${no_proxy:+,$no_proxy}"
    export NO_PROXY="$no_proxy"
  fi
  return 0
}

# get_kv / set_kv / is_placeholder live in deploy/env-lib.sh, because the
# migration and the state-dir resolution need them before this file's own
# helpers would be available, and update.sh needs the same three.
get_kv()         { cc_get_kv "$@"; }
set_kv()         { cc_set_kv "$@"; }
is_placeholder() { cc_is_placeholder "$@"; }

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
# PHASE: validate — offline. Is the answer file answerable-from?
# ─────────────────────────────────────────────────────────────────────────────
phase_validate() {
  load_env || return 1
  pass "answer-file" "$ENV_FILE present (the one answer file — app and deployment)"

  # The UPSTREAM provider (its endpoint, key and model ids) is NOT an answer
  # here (2026-08-30): it is entered in the LiteLLM UI during the llm phase's
  # pause and stored in the proxy's database. CC_LLM_BASE_URL / CC_LLM_API_KEY
  # are a different fact and belong in this file — they are how the APP reaches
  # the proxy. Only the retired upstream keys are called out.
  local v stale=""
  for v in CC_CHAT_MODEL CC_EMBED_MODEL CC_EMBED_BASE_URL CC_EMBED_API_KEY; do
    [[ -n "${!v:-}" ]] && stale="$stale $v"
  done
  if [[ -n "$stale" ]]; then
    warn "provider-in-env" "ignored (the upstream provider is configured in the LiteLLM UI now):$stale"
  else
    pass "provider-in-env" "no retired upstream-provider settings in .env"
  fi

  # Ports: numeric, in range, and distinct — two services on one hostPort is a
  # published port that half-starts the stack.
  local seen="" p val dup=0 bad_port=0
  for p in CC_PG_PORT CC_LITELLM_PORT CC_LITELLM_DB_PORT CC_GRAPHITI_PORT \
           CC_NEO4J_BOLT_PORT CC_NEO4J_HTTP_PORT CC_N8N_PORT CC_CRAWLER_PORT CC_SPEECH_PORT; do
    val="${!p:-}"
    [[ -z "$val" ]] && continue
    if [[ ! "$val" =~ ^[0-9]+$ ]] || (( val < 1 || val > 65535 )); then
      fail "port-${p}" "$p=$val is not a valid port"; bad_port=1; continue
    fi
    if grep -qx "$val" <<<"$seen"; then dup=1; fail "port-${p}" "$p=$val collides with another CC_*_PORT"; fi
    seen="$seen$val"$'\n'
  done
  (( bad_port || dup )) || pass "ports" "all configured ports are numeric, in range and distinct"

  # Mode flags must be exactly 0 or 1 — "true" would read as false everywhere.
  local f
  for f in CC_ENABLE_N8N CC_ENABLE_CRAWLER CC_ENABLE_SANDBOX CC_ENABLE_SPEECH CC_AIRGAP; do
    if [[ "${!f}" == "0" || "${!f}" == "1" ]]; then
      pass "flag-${f}" "${!f}"
    else
      fail "flag-${f}" "$f must be 0 or 1, got '${!f}'"
    fi
  done

  [[ -n "${CC_POD_PREFIX:-}" ]] \
    && pass "name-CC_POD_PREFIX" "${CC_POD_PREFIX}" \
    || fail "name-CC_POD_PREFIX" "CC_POD_PREFIX is empty"

  # schema.sql is bind-mounted into the spine's initdb directory straight from
  # the repo, so its absence is a broken deployment, not just a broken test.
  [[ -f "$REPO_ROOT/central_command/db/schema.sql" ]] \
    && pass "repo-layout" "schema.sql found — running inside the repo" \
    || fail "repo-layout" "central_command/db/schema.sql not found — is this the Central Command repo?"

  # The arithmetic validate used to do by hand — service references, port
  # collisions, profile membership, interpolation — is compose's now. This is
  # the whole check: does the deployment file parse with THIS .env?
  if compose_detect; then
    if compose --profile n8n --profile crawler --profile speech config >/dev/null 2>&1; then
      pass "compose-config" "compose.yaml is valid with this .env (all profiles)"
    else
      fail "compose-config" "compose.yaml does not validate — see: $(printf '%s ' "${COMPOSE_BIN[@]}")--env-file .env -f deploy/single/compose.yaml config"
    fi
  else
    warn "compose-config" "no compose provider here to validate compose.yaml with (preflight checks for one)"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: preflight — is this MACHINE able to run the install?
# ─────────────────────────────────────────────────────────────────────────────
phase_preflight() {
  load_env || return 1

  if command -v podman >/dev/null 2>&1; then
    local pv major minor
    pv="$(podman version --format '{{.Client.Version}}' 2>/dev/null)"
    major="${pv%%.*}"; minor="${pv#*.}"; minor="${minor%%.*}"
    if [[ "$major" =~ ^[0-9]+$ ]] && { (( major > 4 )) || { (( major == 4 )) && (( minor >= 9 )); }; }; then
      pass "podman-version" "podman $pv"
    else
      fail "podman-version" "podman ${pv:-unknown} — 4.9 or newer is required"
    fi
    if podman info >/dev/null 2>&1; then
      pass "podman-running" "podman answers (machine/service up)"
    else
      fail "podman-running" "podman is installed but not answering — on Windows/macOS run: podman machine start"
    fi
  else
    fail "podman-version" "podman not found on PATH"
    fail "podman-running" "podman not found on PATH"
  fi

  # The deployment substrate. `podman compose` is the target; `docker compose`
  # is the dev-box fallback. Neither answering is a hard stop — nothing after
  # fetch can run without one.
  if compose_detect; then
    pass "compose-provider" "$(printf '%s ' "${COMPOSE_BIN[@]}")($("${COMPOSE_BIN[@]}" version 2>/dev/null | head -1))"
  else
    fail "compose-provider" "neither 'podman compose' nor 'docker compose' answers — install podman-compose (or Podman Desktop's compose support)"
  fi

  local t
  for t in curl openssl git uv; do
    command -v "$t" >/dev/null 2>&1 \
      && pass "tool-${t}" "present" \
      || fail "tool-${t}" "$t not found on PATH"
  done
  # python is checked by RUNNING it — the Windows stub passes `command -v`.
  if [[ "$PY" == uv* ]]; then
    warn "tool-python" "no working python3 on PATH — falling back to '$PY' (needs uv)"
  else
    pass "tool-python" "$PY"
  fi

  if command -v node >/dev/null 2>&1; then
    local nv; nv="$(node -v 2>/dev/null)"; nv="${nv#v}"
    if [[ "${nv%%.*}" =~ ^[0-9]+$ ]] && (( ${nv%%.*} >= 22 )); then
      pass "node-version" "node v$nv"
    else
      warn "node-version" "node v$nv is older than 22 — the cockpit build will be skipped"
    fi
  else
    warn "node-version" "node not found — the cockpit build will be skipped (the API still runs)"
  fi

  # RAM/disk: informational on anything that is not Linux (podman machine's
  # reported memory is meaningless on WSL2 — 2026-08-21 W-notes).
  if [[ -r /proc/meminfo ]]; then
    local kb gb; kb="$(awk '/^MemTotal:/{print $2}' /proc/meminfo)"; gb=$(( kb / 1024 / 1024 ))
    (( gb >= 4 )) && pass "memory" "${gb} GB total" \
      || warn "memory" "${gb} GB total — the stack wants ~3 GB plus your own workload"
  else
    warn "memory" "cannot read total memory here — need ~3 GB for the stack"
  fi
  local freegb
  freegb="$(df -Pk "$REPO_ROOT" 2>/dev/null | awk 'NR==2{print int($4/1024/1024)}')"
  if [[ "$freegb" =~ ^[0-9]+$ ]] && (( freegb >= 20 )); then
    pass "disk" "${freegb} GB free at the repo root"
  else
    warn "disk" "${freegb:-unknown} GB free at the repo root — images alone need ~15 GB"
  fi

  # Rootless podman without lingering dies with your last login session and
  # takes every container with it. Hit for real over SSH during validation.
  if command -v loginctl >/dev/null 2>&1; then
    local linger; linger="$(loginctl show-user "${USER:-$(id -un)}" --property=Linger 2>/dev/null)"
    [[ "$linger" == "Linger=yes" ]] \
      && pass "linger" "lingering enabled" \
      || warn "linger" "run 'loginctl enable-linger ${USER:-$(id -un)}' or containers die with your login session"
  else
    pass "linger" "no logind here — not applicable"
  fi

  # On Windows the OS trust store is what schannel curl and podman.exe
  # consult — CC_CA_BUNDLE alone never reaches them. Usually the corporate CA
  # is there by policy; when it is not, the fix is the operator's (admin).
  if [[ -n "${CC_CA_BUNDLE:-}" && ( "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ) ]] && command -v certutil >/dev/null 2>&1; then
    local thumb; thumb="$(openssl x509 -in "$CC_CA_BUNDLE" -noout -fingerprint -sha1 2>/dev/null | sed 's/.*=//; s/://g')"
    if [[ -n "$thumb" ]] && certutil -store Root "$thumb" >/dev/null 2>&1; then
      pass "ca-windows-store" "CC_CA_BUNDLE's CA is in the Windows Root store (schannel curl and podman.exe trust it)"
    else
      useraction "ca-windows-store" "CC_CA_BUNDLE's CA is NOT in the Windows Root store — Git's curl (schannel) and podman.exe will not trust the proxy; as Administrator: certutil -addstore Root \"$(cygpath -w "$CC_CA_BUNDLE" 2>/dev/null || echo "$CC_CA_BUNDLE")\""
    fi
  fi

  # A podman MACHINE (Windows/macOS) has its own egress: the host's CC_PROXY /
  # CC_CA_BUNDLE never reach a pull unless the VM carries them. Since v2.43.0
  # the `machine` phase APPLIES the CA and the registry/proxy drop-ins, so
  # preflight only REPORTS current state and what that phase would change — no
  # hand-instructions for what a phase now does. The machine's own egress for
  # pulls is still set at `podman machine start` from the host environment
  # (doc-verified; writing systemd drop-ins inside the machine is not, and is
  # an open item in the design record), so THAT one keeps its USERACTION.
  if [[ -n "$(machine_name)" ]]; then
    if [[ -n "${CC_PROXY:-}" ]]; then
      local mproxy; mproxy="$(machine_sh 'printenv HTTPS_PROXY https_proxy 2>/dev/null | head -1' | tr -d '\r')"
      if [[ "$mproxy" != "$CC_PROXY" ]]; then
        useraction "machine-egress" "the podman machine's own environment carries no proxy while CC_PROXY is set — a pull would go direct or fail. podman takes the proxy from the HOST environment at machine start, so: podman machine stop && HTTPS_PROXY=\"\$CC_PROXY\" HTTP_PROXY=\"\$CC_PROXY\" podman machine start (the value is in .env — not printed here). The 'machine' phase writes the containers.conf proxy drop-in, which covers pulls and BUILDS but not the VM's own environment."
      else
        pass "machine-egress" "the podman machine carries the host proxy"
      fi
    fi
    # Both of these are now REPORTS plus the diff the machine phase will apply.
    phase_machine --dry-run
  fi

  # 127.0.0.1, never localhost: Windows resolves localhost to ::1 first and the
  # podman machine publishes IPv4-only.
  local lh; lh="$(grep -n 'localhost' "$ENV_FILE" | grep -v '^[0-9]*:#')"
  [[ -z "$lh" ]] \
    && pass "loopback-addressing" ".env uses 127.0.0.1 throughout" \
    || warn "loopback-addressing" ".env mentions localhost — use 127.0.0.1 (Windows resolves localhost to ::1 first)"

  # Index probe: INFORMATIONAL, against the CONFIGURED indexes (a mirror in
  # .env, else the public ones) — the real acquisition is the fetch phase.
  # Unreachable indexes are a fact about the network; CC_AIRGAP is how the
  # operator says it is deliberate. `deploy/discover.sh` is the tool that maps
  # what this network can actually reach and which mirrors to write into .env.
  local u reach=1 probes
  probes="${CC_PYPI_INDEX_URL:-https://pypi.org/simple/} ${CC_NPM_REGISTRY:-https://registry.npmjs.org/}"
  for u in $probes; do
    curl -fsS -m 10 -o /dev/null "$u" 2>/dev/null || reach=0
  done
  if (( reach )); then
    pass "package-indexes" "reachable:$(printf ' %s' $probes)"
  elif [[ "$CC_AIRGAP" == "1" ]]; then
    pass "package-indexes" "unreachable, as expected with CC_AIRGAP=1 — fetch will say per artifact"
  else
    warn "package-indexes" "unreachable:$(printf ' %s' $probes) — run deploy/discover.sh to map this network, then set the mirror seams in .env (see .env.example's deployment section); deploy/AIRGAP.md"
  fi

  # Discovery cross-check (read-only). If /discover ran, its discovery.env —
  # in the state directory since v2.42.0, never in the checkout — records the
  # observed failure CLASS per resource (classes only, no hostnames). It is
  # EVIDENCE, never authority: a mismatch WARNs and names the seam; the
  # decision stays in .env, which is now also where the prober's own inputs
  # live (deploy/discovery.conf is retired).
  local denv="$STATE_DIR/discovery/discovery.env"
  if [[ -f "$denv" ]]; then
    dcls() { sed -n "s/^DISCO_$1=\"\(.*\)\"\$/\1/p" "$denv" | tail -1; }
    local pair dk seam cls dmiss=""
    for pair in DOCKERIO:CC_REGISTRY_DOCKERIO GHCR:CC_REGISTRY_GHCR \
                PYPI:CC_PYPI_INDEX_URL NPM:CC_NPM_REGISTRY; do
      dk="${pair%%:*}"; seam="${pair#*:}"; cls="$(dcls "$dk")"
      case "$cls" in
        dns|refused|timeout|unreachable|error)
          [[ -z "$(eval "printf '%s' \"\${$seam:-}\"")" ]] && dmiss="$dmiss $dk($cls)->$seam" ;;
      esac
    done
    [[ "$(dcls TLS_INTERCEPT)" == "1" && -z "${CC_CA_BUNDLE:-}" ]] \
      && dmiss="$dmiss tls-intercept->CC_CA_BUNDLE"
    if [[ -n "$dmiss" ]]; then
      warn "discovery-crosscheck" "discovery observed failures with no seam set:$dmiss — the prescription is in $STATE_DIR/discovery/discovery-report.md"
    else
      pass "discovery-crosscheck" "discovery's observations and the .env seams agree"
    fi
  fi

  # What podman will actually consult for pulls. On macOS/Windows this is the
  # MACHINE's registries.conf, not any file on the host — `podman info` is
  # the one view that answers for both. Logged, never judged: a mirror can be
  # configured as a registries.conf mirror OR as a CC_REGISTRY_* prefix.
  if command -v podman >/dev/null 2>&1; then
    local regs; regs="$(podman info --format '{{range .Registries}}{{.}} {{end}}' 2>/dev/null | tr -s ' ')"
    pass "podman-registries" "podman sees: ${regs:-no registries.conf entries (fully-qualified refs only)} (the 'machine' phase owns the drop-in that puts entries there — ./setup.sh machine --dry-run reports the diff)"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: machine — tell the podman MACHINE what .env says. Idempotent.
# ─────────────────────────────────────────────────────────────────────────────
# Windows and macOS run podman inside a VM, and that VM is a SECOND HOST: the
# operator's CC_CA_BUNDLE, CC_REGISTRY_* and CC_PROXY do not reach a pull or a
# build unless the machine itself carries them, and the Windows-side
# registries.conf is parsed but NOT honoured for a machine-backed connection
# (podman#16532). Until v2.43.0 preflight printed instructions and the operator
# typed them; the operator authorised setup writing the machine on 2026-09-23.
#
# THE RULES THIS PHASE FOLLOWS, because it writes another host:
#   * a no-op where there is no machine (bare Linux) — never a FAIL;
#   * DROP-INS only, never the main registries.conf / containers.conf
#     (containers-registries.conf.d(5): drop-ins load after the main file, in
#     alpha-numerical order, and merge rather than replace);
#   * the DIFF is printed BEFORE each write, and a proxy VALUE is never printed;
#   * `--dry-run` reports and writes nothing — that is what preflight calls;
#   * one PASS/FAIL per item, and a live PROBE for the CA rather than a
#     settings read (Podman Desktop's CA propagation is a known rough edge,
#     podman-desktop#3821).
MACHINE_CHANGED=()

# Show what a write would change, without ever printing a proxy value.
machine_diff() { # machine_diff <label> <remote-path> <desired-text>
  local label="$1" path="$2" desired="$3" current
  current="$(machine_sh "cat '$path' 2>/dev/null")"
  if [[ "$current" == "$desired" ]]; then
    return 1   # nothing to do
  fi
  note ""
  note "---- $label: $path"
  if [[ -z "$current" ]]; then
    note "     (absent in the machine; would be created)"
  else
    note "     (present and DIFFERENT; would be replaced)"
  fi
  note "$(cc_redact_proxy "$desired" | sed 's/^/     + /')"
  return 0
}

# Write a file inside the machine, as root, via stdin — the content never
# appears in an argv and needs no share between host and VM.
machine_put() { # machine_put <remote-path> <text>
  local path="$1"
  printf '%s' "$2" | machine_sh_stdin "sudo mkdir -p '$(dirname "$path")' && sudo tee '$path' >/dev/null" >/dev/null
}

phase_machine() {
  local dry=0
  [[ "${1:-}" == "--dry-run" ]] && dry=1
  MACHINE_CHANGED=()
  load_env || return 1

  local m; m="$(machine_name)"
  if [[ -z "$m" ]]; then
    pass "machine" "no podman machine on this host — nothing to configure (bare Linux runs containers directly)"
    return 0
  fi
  if ! machine_sh 'true' >/dev/null; then
    fail "machine" "podman machine '$m' exists but does not answer 'podman machine ssh' — start it: podman machine start"
    return 1
  fi
  pass "machine" "podman machine '$m' answers ssh$( (( dry )) && echo ' (--dry-run: reporting only, nothing written)')"

  # ── the CA ────────────────────────────────────────────────────────────────
  # Two paths, both wanted where available. --import-native-ca brings the
  # WINDOWS trust store in (which is where an enterprise CA usually already is,
  # by policy) and is podman >= 6.0 — so it is probed, not assumed. Installing
  # CC_CA_BUNDLE as an anchor covers the case the host store does not (a
  # self-signed mirror CA the operator holds as a file).
  if podman machine set --help 2>/dev/null | grep -q -- '--import-native-ca'; then
    if (( dry )); then
      note "     machine phase will apply: podman machine set --import-native-ca=true"
      pass "machine-native-ca" "podman supports --import-native-ca (the host trust store would be imported at the machine's next start)"
    elif [[ -f "$STATE_DIR/machine.import-native-ca" ]]; then
      pass "machine-native-ca" "podman machine set --import-native-ca=true was applied on $(cat "$STATE_DIR/machine.import-native-ca") (the host trust store is imported at every start)"
    elif podman machine set --import-native-ca=true >/dev/null 2>&1; then
      # Recorded in the state dir so a re-run neither re-applies it nor demands
      # another restart: `podman machine inspect` has no documented field for
      # it, and the setting only takes effect at the machine's next start.
      date -u +%Y-%m-%dT%H:%M:%SZ >"$STATE_DIR/machine.import-native-ca"
      pass "machine-native-ca" "podman machine set --import-native-ca=true applied (the host trust store is imported at every start)"
      MACHINE_CHANGED+=(import-native-ca)
    else
      warn "machine-native-ca" "podman machine set --import-native-ca=true was rejected — the CA anchor below is then the only path"
    fi
  else
    pass "machine-native-ca" "this podman has no --import-native-ca (it arrived in podman 6.0) — the CA anchor below is the path"
  fi

  if [[ -n "${CC_CA_BUNDLE:-}" ]]; then
    if [[ ! -r "$CC_CA_BUNDLE" ]]; then
      fail "machine-ca" "CC_CA_BUNDLE is set to $CC_CA_BUNDLE, which this host cannot read"
    else
      local want cur
      want="$(cat "$CC_CA_BUNDLE")"
      cur="$(machine_sh "cat '$CC_MACHINE_CA_PEM' 2>/dev/null")"
      if [[ "$cur" == "$want" ]]; then
        pass "machine-ca" "CC_CA_BUNDLE is already installed in the machine at $CC_MACHINE_CA_PEM"
      elif (( dry )); then
        note ""
        note "---- CA anchor: $CC_MACHINE_CA_PEM"
        note "     ($( [[ -z "$cur" ]] && echo absent || echo 'present and DIFFERENT' ); would install CC_CA_BUNDLE, then update-ca-trust)"
        warn "machine-ca" "the machine does not trust CC_CA_BUNDLE — machine phase will apply: install CC_CA_BUNDLE at $CC_MACHINE_CA_PEM, then update-ca-trust (the full run does this in the next phase; on its own: ./setup.sh machine)"
      else
        # The machine image is Fedora CoreOS (podman-machine-init(1)), so
        # ca-trust is the framework; the Debian/Ubuntu pair is the fallback for
        # a custom machine image.
        local rc=0
        if machine_sh 'command -v update-ca-trust >/dev/null 2>&1'; then
          machine_put "$CC_MACHINE_CA_PEM" "$want" || rc=1
          machine_sh 'sudo update-ca-trust' >/dev/null || rc=1
        else
          machine_put "/usr/local/share/ca-certificates/cc-ca.crt" "$want" || rc=1
          machine_sh 'sudo update-ca-certificates' >/dev/null || rc=1
        fi
        if (( rc )); then
          fail "machine-ca" "could not install CC_CA_BUNDLE into the machine — check that 'podman machine ssh $m -- sudo true' works"
        else
          pass "machine-ca" "CC_CA_BUNDLE installed in the machine and the trust store rebuilt"
          MACHINE_CHANGED+=(ca)
        fi
      fi
    fi
    # The VERIFY is a live probe from INSIDE the machine, not a settings read:
    # a present anchor file proves nothing about what a pull will accept. exit
    # 60 is curl's certificate failure, which is the answer this asks for.
    if (( ! dry )); then
      local probe_host rc2=0
      probe_host="$(cc__mhost "${CC_REGISTRY_DOCKERIO:-}")"
      [[ -n "$probe_host" ]] || probe_host="registry-1.docker.io"
      machine_sh "curl -fsSI --max-time 15 https://${probe_host}/v2/ >/dev/null" || rc2=$?
      if (( rc2 == 60 )); then
        fail "machine-ca-probe" "from inside the machine, https://${probe_host}/v2/ fails with a CERTIFICATE error (curl exit 60) — the CA is still not trusted there"
      elif (( rc2 == 0 )); then
        pass "machine-ca-probe" "from inside the machine, https://${probe_host}/v2/ answers with no certificate error"
      else
        warn "machine-ca-probe" "from inside the machine, https://${probe_host}/v2/ did not answer (curl exit $rc2) — not a certificate failure (60), so this is reachability, not trust"
      fi
    fi
  fi

  # ── the registries drop-in ────────────────────────────────────────────────
  local regconf; regconf="$(cc_render_registries_conf)"
  if [[ -z "$regconf" ]]; then
    pass "machine-registries" "no mirror or insecure host to declare (CC_REGISTRY_* unset and CC_TLS_INSECURE=0)"
  elif machine_diff "registries drop-in" "$CC_MACHINE_REGISTRIES_CONF" "$regconf"; then
    if (( dry )); then
      warn "machine-registries" "the machine's registries drop-in does not match .env — machine phase will apply: write $CC_MACHINE_REGISTRIES_CONF (diff above; the full run does this in the next phase)"
    elif machine_put "$CC_MACHINE_REGISTRIES_CONF" "$regconf"; then
      pass "machine-registries" "wrote $CC_MACHINE_REGISTRIES_CONF in the machine (podman re-reads its configuration per invocation — no restart)"
      MACHINE_CHANGED+=(registries)
    else
      fail "machine-registries" "could not write $CC_MACHINE_REGISTRIES_CONF in the machine"
    fi
  else
    pass "machine-registries" "$CC_MACHINE_REGISTRIES_CONF already matches .env"
  fi

  # ── the proxy drop-in ─────────────────────────────────────────────────────
  # containers.conf(5) `[engine] env` is the podman/buildah PROCESS
  # environment, which is what pulls and builds travel through. The value is
  # never printed — only the key name.
  local proxyconf; proxyconf="$(cc_render_proxy_conf)"
  if [[ -z "$proxyconf" ]]; then
    pass "machine-proxy" "CC_PROXY is unset — no proxy drop-in to write"
  elif machine_diff "proxy drop-in (values redacted)" "$CC_MACHINE_PROXY_CONF" "$proxyconf"; then
    if (( dry )); then
      warn "machine-proxy" "the machine's containers.conf proxy drop-in does not match CC_PROXY — machine phase will apply: write $CC_MACHINE_PROXY_CONF (the value is never printed; the full run does this in the next phase)"
    elif machine_put "$CC_MACHINE_PROXY_CONF" "$proxyconf"; then
      pass "machine-proxy" "wrote $CC_MACHINE_PROXY_CONF in the machine from CC_PROXY (key names only — the value is not printed)"
      MACHINE_CHANGED+=(proxy)
    else
      fail "machine-proxy" "could not write $CC_MACHINE_PROXY_CONF in the machine"
    fi
  else
    pass "machine-proxy" "$CC_MACHINE_PROXY_CONF already matches CC_PROXY"
  fi

  # Restarting: only --import-native-ca needs one (it imports at START).
  if (( ! dry )) && (( ${#MACHINE_CHANGED[@]} )); then
    local r; r="$(cc_machine_restart_needed "${MACHINE_CHANGED[@]}")" \
      && useraction "machine-restart" "$r"
  fi
  return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: fetch — acquire EVERY dependency before anything is deployed.
# ─────────────────────────────────────────────────────────────────────────────
# The check IS the acquisition: an image is proven by RESOLVING it against the
# registry (resolve-images.sh: constraint/lock/resolve, design record D2) and
# then pulling the resolved ref, a build by building it (podman caches layers,
# so a retry costs the failing layer), the Python graph by resolving it, the
# cockpit by `npm ci`. Each failure names the seam that governs it, and the
# phase ends in USERACTION (exit 3): the operator fixes the mirror seam
# (deploy/discover.sh maps what the network can reach) and re-runs — acquired
# artifacts fast-forward. Nothing falls back on its own — a fallback chosen at
# 11pm by a script is a decision nobody can find later.
# CAPTURE BEFORE GREPPING: `podman images | grep -q` inverts under pipefail
# (grep exits at the first match, podman takes SIGPIPE, the pipeline reports
# failure on success).
have_image() { local imgs; imgs="$(podman images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null)"; grep -qx "$1" <<<"$imgs"; }

# Pull every ref resolve-images.sh wrote into .env. The refs are TAGGED, not
# digest-pinned: the lock's digest is verified at resolution time against the
# registry, and a substituted tag is deliberately trusted from the mirror.
fetch_images() {
  local rc=0
  "$HERE/resolve-images.sh" || rc=$?
  case "$rc" in
    0) pass "resolve-images" "every image resolved to its locked tag" ;;
    2) pass "resolve-images" "resolved, with substitutions — see the WARN lines above and $STATE_DIR/installed.manifest" ;;
    *) fail "resolve-images" "image resolution failed (exit $rc) — the FAIL/USERACTION lines above name the seam"; return 1 ;;
  esac
  # resolve-images.sh writes CC_IMG_* into .env; re-read so this shell has them.
  load_env || return 1

  local var ref check
  while IFS= read -r var; do
    ref="${!var:-}"; [[ -z "$ref" ]] && continue
    check="image-${var#CC_IMG_}"; check="${check,,}"
    if have_image "$ref"; then pass "$check" "$ref present"; continue; fi
    # --tls-verify=false when the operator has turned verification off: no
    # exported variable reaches a podman pull (D4).
    if podman pull -q "${PULL_TLS[@]}" "$ref" >/dev/null 2>&1 </dev/null; then
      pass "$check" "$ref pulled"
    else
      fail "$check" "$ref could not be pulled — seams: the CC_REGISTRY_* entry for its registry in .env, $var itself (an exact ref the resolver must use as-is, including a re-namespaced PATH), or the registries drop-in ./setup.sh machine writes"
    fi
  done < <(compgen -A variable CC_IMG_ | sort)
}

fetch_local() { # fetch_local <check> <ref> <build-script> <seams>
  local check="$1" ref="$2" script="$3" seams="$4"
  if have_image "$ref"; then pass "$check" "$ref present"; return 0; fi
  if "$script" >&2; then
    pass "$check" "$ref built"
  else
    fail "$check" "$ref failed to build against your sources — seams: $seams (see .env.example's deployment section)"
  fi
}

phase_fetch() {
  load_env || return 1
  : "${CC_GRAPHITI_TAG:=1.0.2-anthropic}"
  [[ -f "$HERE/images.txt" ]] || { fail "images-txt" "$HERE/images.txt missing"; return 1; }

  fetch_images

  fetch_local "image-graphiti" "localhost/cc-graphiti:${CC_GRAPHITI_TAG}" \
    "$HERE/build-graphiti-image.sh" "CC_IMG_ZEPAI_KNOWLEDGE_GRAPH_MCP (the base ref, resolved from images.txt), CC_REGISTRY_DOCKERIO, CC_APT_MIRROR, CC_PYPI_INDEX_URL, CC_CA_BUNDLE, CC_TLS_INSECURE"
  if [[ "$CC_ENABLE_SANDBOX" == 1 ]]; then
    fetch_local "image-sandbox" "localhost/cc-sandbox:1" \
      "$HERE/build-sandbox-image.sh" "CC_IMG_PYTHON (the base ref, resolved from images.txt), CC_REGISTRY_DOCKERIO, CC_APT_MIRROR, CC_NPM_REGISTRY, CC_CA_BUNDLE, CC_TLS_INSECURE"
  else
    pass "image-sandbox" "skipped (CC_ENABLE_SANDBOX=0)"
  fi
  if [[ "$CC_ENABLE_CRAWLER" == 1 ]]; then
    fetch_local "image-crawler" "localhost/cc-crawler:1" \
      "$HERE/build-crawler-image.sh" "CC_IMG_PLAYWRIGHT_PYTHON (the base ref, resolved from images.txt), CC_REGISTRY_MCR, CC_PYPI_INDEX_URL, CC_CA_BUNDLE, CC_TLS_INSECURE"
  else
    pass "image-crawler" "skipped (CC_ENABLE_CRAWLER=0)"
  fi

  # Python: the venv now (its interpreter may itself be a download — seam
  # CC_PYTHON_MIRROR), then a resolve of exactly what the app phase installs.
  if ! venv_python >/dev/null; then
    if ! uv venv --python 3.12 "$REPO_ROOT/.venv" >&2; then
      fail "venv" "uv could not create a Python 3.12 venv — no 3.12 on PATH and the download failed; seam: CC_PYTHON_MIRROR"
    fi
  fi
  if venv_python >/dev/null; then
    export VIRTUAL_ENV="$REPO_ROOT/.venv"
    if [[ "$CC_AIRGAP" == "1" ]]; then
      in_repo uv pip install --dry-run -r "$REPO_ROOT/requirements.lock" >&2 \
        && pass "python" "requirements.lock resolves against ${CC_PYPI_INDEX_URL:-PyPI}" \
        || fail "python" "requirements.lock does not resolve — seam: CC_PYPI_INDEX_URL"
    else
      in_repo uv pip install --dry-run -e ".[dev,runtime]" >&2 \
        && pass "python" "[dev,runtime] resolves against ${CC_PYPI_INDEX_URL:-PyPI}" \
        || fail "python" "the Python dependencies do not resolve — seam: CC_PYPI_INDEX_URL; or CC_AIRGAP=1 (lock only)"
    fi
  fi

  # Cockpit: `npm ci` is the acquisition; the build itself is the app phase's.
  if command -v node >/dev/null 2>&1; then
    local nv; nv="$(node -v 2>/dev/null)"; nv="${nv#v}"
    if [[ "${nv%%.*}" =~ ^[0-9]+$ ]] && (( ${nv%%.*} >= 22 )); then
      in_web npm ci >&2 \
        && pass "cockpit" "npm tree installed from ${CC_NPM_REGISTRY:-registry.npmjs.org}" \
        || fail "cockpit" "npm ci failed — seam: CC_NPM_REGISTRY"
    else
      warn "cockpit" "node v$nv is older than 22 — cockpit not fetched; the API runs without it"
    fi
  else
    warn "cockpit" "node not found — cockpit not fetched; the API runs without it"
  fi

  if (( FAILS )); then
    useraction "fetch" "$FAILS artifact(s) could not be acquired — fix the seam(s) named above in the repo-root .env and re-run ./setup.sh fetch (acquired ones fast-forward); deploy/discover.sh maps what this network can reach, deploy/AIRGAP.md maps the seams"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: llm — LiteLLM first, then everything is proven through its aliases.
# ─────────────────────────────────────────────────────────────────────────────
# The USER-ACTION gate for a probe failure. The proxy is alive at this point,
# so the operator can act in its UI; this prints the where and the what.
# Key VALUES are never printed — names only, per the output protocol.
llm_gate() { # llm_gate <what-failed>
  useraction "llm-models" "$1 — operator action needed; see the instructions on stderr, then re-run: ./setup.sh llm"
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
  note "                   Graphiti's MCP server uses the stock chat client)"
  note "    cc-embedding   openai/<your embedding model>     dimension is permanent"
  note "    gpt-4.1-nano   openai/<your chat model>          Graphiti's reranker"
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
  note "  Not sure what your server names its models? List them directly:"
  note "    CC_LLM_BASE_URL=<url>/v1 CC_LLM_API_KEY=<key> ./discover-llm.sh models"
  note "  A probe failed after you filled things in? Direct works + proxy fails ="
  note "  the alias row is wrong; direct fails = the URL or key is wrong."
  note "  'curl: (28) Operation timed out' = the backend answered nothing within"
  note "  CC_PROBE_TIMEOUT seconds (default 300) — a shared or queued server may"
  note "  need longer:  CC_PROBE_TIMEOUT=900 ./setup.sh llm"
  note ""
  note "When it looks right, re-run:  ./setup.sh llm   (it validates every alias, then continues)"
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

  # LiteLLM runs its Prisma migrations at boot; 5 minutes is the honest budget.
  if wait_http "http://127.0.0.1:${CC_LITELLM_PORT}/health/liveliness" 300; then
    pass "litellm-live" "proxy answers /health/liveliness on 127.0.0.1:${CC_LITELLM_PORT}"
  else
    fail "litellm-live" "proxy never answered /health/liveliness — run: ./setup.sh diagnose"
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
  local rrc=0
  CC_LITELLM_URL="http://127.0.0.1:${CC_LITELLM_PORT}" \
  LITELLM_MASTER_KEY="${CC_LLM_PROXY_ADMIN_KEY:-}" \
    $PY "$REPO_ROOT/deploy/pi/litellm/register-models.py" --policy "$HERE/models.json" >&2 || rrc=$?
  case "$rrc" in
    0) pass "catalog" "every required alias is registered, filled in and consistent" ;;
    3) llm_gate "the model catalog needs your provider details (see the alias list above)"; return 3 ;;
    *) fail "catalog" "register-models.py failed (exit $rrc) — run: ./setup.sh diagnose"; return 1 ;;
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
  if ! step "probe-rerank-model" "a completion came back through gpt-4.1-nano (Graphiti's reranker alias)" \
    "$HERE/discover-llm.sh" --proxy chat gpt-4.1-nano; then
    llm_gate "the gpt-4.1-nano alias did not return a completion"
    return 3
  fi

  # Speech: one synthesis, then transcribe what it said — the round trip proves
  # both aliases with real audio.
  if [[ "$CC_ENABLE_SPEECH" == "1" ]]; then
    if wait_http "http://127.0.0.1:${CC_SPEECH_PORT}/health" 120; then
      pass "speech-live" "cc-speech answers /health on 127.0.0.1:${CC_SPEECH_PORT}"
    else
      fail "speech-live" "cc-speech never answered /health — run: ./setup.sh diagnose"
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
        || { fail "speech-model" "could not install ${m} — CC_HF_ENDPOINT reachable? run: ./setup.sh diagnose"; return 1; }
    done
  fi
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

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: stack — assert the local images, then bring the whole stack up.
# ─────────────────────────────────────────────────────────────────────────────
# Images are the fetch phase's job; here they are only ASSERTED, so a missing
# one is a clear "run fetch" and never a surprise build (or pull) mid-deploy.
need_image() { # need_image <check-name> <image-ref>
  have_image "$2" && { pass "$1" "$2 present"; return 0; }
  fail "$1" "$2 is not in local storage — run: ./setup.sh fetch"
  return 1
}

phase_stack() {
  load_env || return 1
  : "${CC_GRAPHITI_TAG:=1.0.2-anthropic}"

  need_image "image-graphiti" "localhost/cc-graphiti:${CC_GRAPHITI_TAG}" || return 1
  if [[ "$CC_ENABLE_SANDBOX" == "1" ]]; then
    need_image "image-sandbox" "localhost/cc-sandbox:1" || return 1
  else
    pass "image-sandbox" "skipped (CC_ENABLE_SANDBOX=0)"
  fi
  if [[ "$CC_ENABLE_CRAWLER" == "1" ]]; then
    need_image "image-crawler" "localhost/cc-crawler:1" || return 1
  else
    pass "image-crawler" "skipped (CC_ENABLE_CRAWLER=0)"
  fi

  # The dimension is written into the Neo4j vector index and is effectively
  # permanent, so the graph may not be created before it has been MEASURED.
  [[ -n "${CC_EMBED_DIM:-}" ]] || {
    fail "embed-dimension" "CC_EMBED_DIM is empty in .env — run: ./setup.sh llm (it measures it through the cc-embedding alias)"
    return 1
  }

  # ONE call brings the whole stack up: compose.yaml's depends_on/healthchecks
  # carry the order that used to be a sequence of plays, and --wait blocks
  # until every service with a healthcheck is healthy. The optional components
  # are profiles, so the CC_ENABLE_* flags select them by name and nothing has
  # to be skipped by hand.
  compose_profile_flags
  step "up-stack" "the stack is up and healthy (${PROFILE_FLAGS[*]:-no optional profiles})" \
    compose "${PROFILE_FLAGS[@]}" up -d --wait || return 1

  # `restart: always` is honoured by podman-restart.service, which a podman
  # MACHINE (Windows/macOS) ships disabled: after a host reboot every
  # container sat Exited (2026-09-17 Windows run). Enable it where there is
  # a machine; a Linux host with a system podman has no machine and skips.
  # The machine's containers are ROOTLESS by default (the `user` account),
  # so the unit that restarts them is the USER instance — the system unit
  # restarted nothing after the 2026-09-18 reboot. Enable both; the user's
  # session lingers on a podman machine, so the user unit runs at boot.
  if [[ -n "$(podman machine list --format '{{.Name}}' 2>/dev/null)" ]]; then
    # `--global`, not `--user enable`: the machine's ~/.config/systemd is
    # root-owned, so a user enable is "Access denied"; --global writes
    # /etc/systemd/user and covers every user instance.
    if podman machine ssh -- 'sudo systemctl --global enable podman-restart.service && XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user start podman-restart.service; sudo systemctl enable --now podman-restart.service' </dev/null >/dev/null 2>&1; then
      pass "restart-on-boot" "podman-restart.service enabled in the podman machine, user and system instances (containers return after a host reboot)"
    else
      warn "restart-on-boot" "could not enable podman-restart.service in the podman machine — run: podman machine ssh -- sudo systemctl enable --now podman-restart.service"
    fi
  else
    pass "restart-on-boot" "no podman machine (system podman) — restart policies apply natively"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: app — the Python environment, the app's .env, the spine's own key.
# ─────────────────────────────────────────────────────────────────────────────
# `env -C` would be shorter but is not portable to Git Bash's coreutils; a
# subshell cd is. Passed to step() as the command, which calls it like any
# other.
in_repo() { ( cd "$REPO_ROOT" && "$@" ); }
in_web()  { ( cd "$REPO_ROOT/web" && "$@" ); }

venv_python() {
  [[ -x "$REPO_ROOT/.venv/bin/python" ]] && { printf '%s' "$REPO_ROOT/.venv/bin/python"; return 0; }
  [[ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]] && { printf '%s' "$REPO_ROOT/.venv/Scripts/python.exe"; return 0; }
  return 1
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
  local cur_key; cur_key="$(get_kv "$ENV_FILE" CC_LLM_API_KEY)"
  if is_placeholder "$cur_key"; then
    [[ -n "${CC_LLM_PROXY_ADMIN_KEY:-}" ]] || { fail "mint-key" "CC_LLM_PROXY_ADMIN_KEY is missing from .env — run the llm phase first (make-secrets.sh generates it)"; return 1; }
    local body minted
    # The master key travels via `-H @-` (stdin), so it is not visible in `ps`
    # while the request runs. NOT `-H @<(...)`: native Windows curl cannot
    # open MSYS's /proc fd paths (found live 2026-08-28). No `tags` field:
    # tags are an Enterprise feature and their presence 403s a community proxy.
    body="$(printf 'Authorization: Bearer %s\n' "$CC_LLM_PROXY_ADMIN_KEY" | \
      curl -sS --fail-with-body -m 60 \
      -H @- \
      -H 'Content-Type: application/json' \
      -d '{"models": ["cc-default", "cc-tts", "cc-stt"], "metadata": {"cc": "spine"}}' \
      "http://127.0.0.1:${CC_LITELLM_PORT}/key/generate" 2>&1)"
    minted="$($PY -c 'import json,sys; print(json.load(sys.stdin).get("key",""))' <<<"$body" 2>/dev/null)"
    if [[ -z "$minted" ]]; then
      fail "mint-key" "/key/generate did not return a key — is the proxy up? (the response is NOT echoed; run ./setup.sh diagnose)"
      return 1
    fi
    set_kv "$ENV_FILE" CC_LLM_API_KEY "$minted"
    pass "mint-key" "minted a LiteLLM virtual key scoped to cc-default + cc-tts + cc-stt and stored it as CC_LLM_API_KEY"
  else
    pass "mint-key" "CC_LLM_API_KEY already set — not minting a second key (a key minted before v2.21.0 lacks cc-tts/cc-stt: add them to it in the proxy UI, or clear CC_LLM_API_KEY to re-mint)"
  fi

  # DERIVED values — the last two things in .env nobody should have to type.
  # Until v2.42.0 this block COPIED five values out of deploy/single/.env into
  # the app's .env (CC_LLM_PROXY_ADMIN_KEY, CC_NEO4J_PASSWORD,
  # CC_LITELLM_SALT_KEY, CC_EMBED_DIM and this URL). With one answer file the
  # same fact has ONE key and there is nothing to copy: make-secrets.sh
  # generates the CC_ names directly and the llm phase writes CC_EMBED_DIM
  # where the app already reads it. What is left is genuinely COMPOSED from
  # other keys in this same file.
  set_kv_if_unset "$ENV_FILE" CC_EMBED_ALIAS "cc-embedding" "app-embed-alias"
  set_kv_if_unset "$ENV_FILE" CC_LITELLM_DB_URL \
    "postgresql://llmproxy:${LITELLM_POSTGRES_PASSWORD:-}@127.0.0.1:${CC_LITELLM_DB_PORT}/litellm" \
    "app-litellm-db-url"

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
      step "cockpit" "cockpit built (web/)" \
        in_web bash -c '[[ -d node_modules ]] || npm ci; npm run build' || return 1
    else
      warn "cockpit" "node v$nv is older than 22 — cockpit not built; the API runs without it"
    fi
  else
    warn "cockpit" "node not found — cockpit not built; the API runs without it"
  fi
}

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
  echo "                   ./setup.sh diagnose"
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
  capability_manifest
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASES test / boot / demo — install-to-working-demo in ONE command
# (2026-08-28, operator decision: "me having to run a whole series of commands
# myself seems relatively pointless"). Deterministic like everything above;
# the two genuinely-human moments (naming the operator, approving the demo
# proposal) are IN-PROCESS gates on a terminal, exit-3 gates otherwise.
# Idempotency probes REALITY, never a state file: a healthy API skips
# test+boot, a decided proposal in the event log skips demo.
# ─────────────────────────────────────────────────────────────────────────────
is_tty() { [[ -t 0 ]]; }

api_url() { # the app's own port, from the root .env when set
  local p; p="$(get_kv "$ENV_FILE" CC_API_PORT)"; printf 'http://127.0.0.1:%s' "${p:-8080}"
}
api_up() { curl -fsS -m 5 "$(api_url)/health" >/dev/null 2>&1; }

venv_uvicorn() {
  [[ -x "$REPO_ROOT/.venv/bin/uvicorn" ]] && { printf '%s' "$REPO_ROOT/.venv/bin/uvicorn"; return 0; }
  [[ -x "$REPO_ROOT/.venv/Scripts/uvicorn.exe" ]] && { printf '%s' "$REPO_ROOT/.venv/Scripts/uvicorn.exe"; return 0; }
  return 1
}

# One JSON field out of a GET, via the venv-independent $PY. Empty on any
# error — callers treat empty as "not there yet".
api_json() { # api_json <url> <python-expr over `d`>
  curl -fsS -m 10 "$1" 2>/dev/null | $PY -c "
import json,sys
try: d=json.load(sys.stdin); print($2)
except Exception: pass" 2>/dev/null
}

poll_until() { # poll_until <seconds> <interval> <cmd...> — true once cmd succeeds
  local deadline=$(( SECONDS + $1 )) ivl="$2"; shift 2
  until "$@"; do (( SECONDS < deadline )) || return 1; sleep "$ivl"; done
}

phase_test() {
  load_env || return 1
  if api_up; then
    pass "test" "skipped — the API is already up and healthy; to re-gate by hand: .venv python -m pytest -q"
    return 0
  fi
  local py; py="$(venv_python)" || { fail "test" ".venv missing — run: ./setup.sh app"; return 1; }
  note "the offline suite is SEQUENTIAL and takes ~10 minutes — this is the gate, not a formality"
  step "test" "the offline suite is green" in_repo "$py" -m pytest -q || return 1
}

phase_boot() {
  load_env || return 1
  if api_up; then
    pass "boot-api" "API already answering at $(api_url) — not starting a second one"
  else
    # CC_OPERATOR_NAME is the one value only a human can supply. On a
    # terminal, ask it here (elicitation IS allowed to be a prompt — it is
    # the script asking, deterministically); headless, the cockpit asks.
    local opname; opname="$(get_kv "$ENV_FILE" CC_OPERATOR_NAME)"
    if is_placeholder "$opname"; then
      if is_tty; then
        note ""
        note "== one question before first boot =="
        read -rp "What should the agents call you? " opname
        [[ -n "$opname" ]] || { fail "operator-name" "no name given — first boot needs one"; return 1; }
        set_kv "$ENV_FILE" CC_OPERATOR_NAME "$opname"
        pass "operator-name" "CC_OPERATOR_NAME recorded in the root .env"
      else
        # Headless: the cockpit asks on first run (v2.37.0) — gating here made
        # that prompt unreachable on this profile.
        pass "operator-name" "not set — the cockpit asks on first run (agents say 'the operator' until then)"
      fi
    else
      pass "operator-name" "CC_OPERATOR_NAME already set — left alone"
    fi

    local uv_bin; uv_bin="$(venv_uvicorn)" || { fail "boot-api" "uvicorn not in .venv — run: ./setup.sh app"; return 1; }
    note "--> starting uvicorn detached (log: $STATE_DIR/uvicorn.log · stop: ./setup.sh stop)"
    ( cd "$REPO_ROOT" && nohup "$uv_bin" central_command.api.app:app --host 127.0.0.1 \
        --port "$(api_url | sed 's/.*://')" >>"$STATE_DIR/uvicorn.log" 2>&1 &
      echo $! >"$STATE_DIR/uvicorn.pid" )
    if wait_http "$(api_url)/health" 90; then
      pass "boot-api" "API answering at $(api_url) (first boot hires the roster)"
    else
      fail "boot-api" "API never answered /health — read $STATE_DIR/uvicorn.log"
      return 1
    fi
  fi

  # The roster is hired AFTER startup completes; one read right after /health
  # saw zero agents on 2026-09-18 (Windows, v2.36.4) — poll, do not sample.
  local n i; for i in $(seq 1 30); do
    n="$(api_json "$(api_url)/api/agents" 'len(d.get("agents", d if isinstance(d, list) else []))')"
    [[ "$n" =~ ^[0-9]+$ ]] && (( n > 0 )) && break
    sleep 2
  done
  if [[ "$n" =~ ^[0-9]+$ ]] && (( n > 0 )); then
    pass "boot-roster" "$n agents on the roster"
  else
    fail "boot-roster" "the roster is empty — read $STATE_DIR/uvicorn.log (seed guard? database?)"
    return 1
  fi

  # The cockpit is the Node server in web/ (server-dist), NOT the SPA uvicorn
  # serves from web/dist: every panel is a route or a WebSocket proxy that
  # server owns, so the SPA alone sits at CONNECTING with 404s (2026-09-17
  # Windows run). On k3s it is the cc-nerve unit; here it is a second
  # detached process.
  #
  # web/.env is RETIRED on this profile (v2.42.0): the server's `dotenv/config`
  # loads that file from cwd and does NOT override variables already present in
  # the environment, so EXPORTING the three settings is exactly equivalent and
  # keeps the checkout clean. The k3s profile still writes web/.env — there the
  # file is cc-nerve's, not ours.
  local cport="${CC_COCKPIT_PORT:-3080}" curl_ok
  if [[ ! -f "$REPO_ROOT/web/server-dist/index.js" ]]; then
    warn "boot-cockpit" "web/server-dist is missing (node absent at build time?) — the API runs, the cockpit does not"
  elif curl -fsS -m 5 "http://127.0.0.1:${cport}/" >/dev/null 2>&1; then
    pass "boot-cockpit" "cockpit already answering at http://127.0.0.1:${cport} — not starting a second one"
  else
    note "--> starting the cockpit server detached (log: $STATE_DIR/cockpit.log · stop: ./setup.sh stop)"
    # CC_UPDATE_BACKEND=api: the API owns the update routes on this profile and
    # the Node server only proxies them.
    ( cd "$REPO_ROOT/web" && PORT="$cport" GATEWAY_URL="$(api_url)" CC_UPDATE_BACKEND=api \
        nohup node server-dist/index.js >>"$STATE_DIR/cockpit.log" 2>&1 &
      echo $! >"$STATE_DIR/cockpit.pid" )
    if wait_http "http://127.0.0.1:${cport}/" 60; then
      pass "boot-cockpit" "cockpit answering at http://127.0.0.1:${cport} (proxies to $(api_url))"
    else
      fail "boot-cockpit" "the cockpit server never answered — read $STATE_DIR/cockpit.log"
      return 1
    fi
  fi
  # The API and the cockpit are host processes, not containers: podman-restart
  # brings the containers back after a reboot, nothing brings these two. On
  # Windows a logon-triggered scheduled task re-runs this phase (idempotent —
  # a process that answers is left alone), the same mechanism the podman
  # machine itself starts with. Linux hosts have systemd units for this.
  if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]] && command -v schtasks >/dev/null 2>&1; then
    local wrapper="$STATE_DIR/cc-boot.cmd" bashw
    bashw="$(cygpath -w "$(command -v bash)")"
    # The wrapper and its log live in the state dir with everything else
    # generated; the `cd` is still the checkout, because that is where
    # setup.sh is.
    printf '@echo off\r\n"%s" -lc "cd '\''%s'\'' && ./setup.sh boot >> '\''%s/boot-at-logon.log'\'' 2>&1"\r\n' \
      "$bashw" "$HERE" "$STATE_DIR" >"$wrapper"
    # An onlogon task needs an elevated shell ("Access is denied" otherwise,
    # 2026-09-18); the user's Startup folder needs nothing — same moment, a
    # console window while boot runs. Task first, Startup folder as the fallback.
    local startup="$APPDATA/Microsoft/Windows/Start Menu/Programs/Startup"
    if schtasks //create //f //tn cc-boot //sc onlogon //tr "$(cygpath -w "$wrapper")" >/dev/null 2>&1; then
      pass "boot-at-logon" "scheduled task cc-boot re-runs ./setup.sh boot at every logon (log: $STATE_DIR/boot-at-logon.log)"
    elif [[ -d "$startup" ]] && cp "$wrapper" "$startup/cc-boot.cmd" 2>/dev/null; then
      pass "boot-at-logon" "Startup-folder entry cc-boot.cmd re-runs ./setup.sh boot at every logon (no elevation; an elevated shell can instead: schtasks /create /f /tn cc-boot /sc onlogon /tr \"$(cygpath -w "$wrapper")\")"
    else
      warn "boot-at-logon" "could not register a logon entry — after a reboot, run: ./setup.sh boot"
    fi
  fi
  note "cockpit: http://127.0.0.1:${cport}  (the feed, the drain and every schedule are OFF until you turn them on)"
}

demo_decided() { # true once the event log shows a decided proposal
  local c; c="$(api_json "$(api_url)/api/events?kind=proposal.decided&limit=1" 'len(d.get("events",[]))')"
  [[ "$c" =~ ^[1-9] ]]
}
demo_awaiting() {
  local c; c="$(api_json "$(api_url)/api/dispatch" 'd.get("awaiting_human",0)')"
  [[ "$c" =~ ^[1-9] ]]
}

phase_demo() {
  load_env || return 1
  api_up || { fail "demo" "the API is not running — ./setup.sh boot"; return 1; }

  if demo_decided; then
    pass "demo" "skipped — the event log already shows a decided proposal (the loop is proven on this install)"
    return 0
  fi

  if ! demo_awaiting; then
    # Knowledge-only email: triage proposes a graph episode, which the
    # Executor performs for REAL against the local graph — no Jira needed.
    # (The invoice fixture named a Jira issue no fresh install has; dry_run
    # used to make that free.)
    local eml="$REPO_ROOT/fixtures/emails/007-ownership-change.eml"
    [[ -f "$eml" ]] || { fail "demo-feed" "$eml missing"; return 1; }
    step "demo-feed" "fixture email enrolled (a repeat Message-ID is a no-op by design)" \
      bash -c "$PY -c 'import json,sys,pathlib;print(json.dumps({\"text\":pathlib.Path(sys.argv[1]).read_text()}))' '$eml' \
        | curl -fsS -X POST -H 'content-type: application/json' -d @- '$(api_url)/api/emails'" || return 1

    # Fire the dispatcher only when nothing is already working the queue.
    local busy; busy="$(api_json "$(api_url)/api/dispatch" 'd.get("in_flight",0)')"
    if [[ "$busy" =~ ^[1-9] ]]; then
      pass "demo-dispatch" "a run is already in flight — riding it"
    else
      # /api/dispatch/step AWAITS the whole triage run, and on a modest or shared
      # backend that outlives any curl ceiling (30 s here read as FAIL while the
      # run went on to park its proposal — 2026-09-17 Windows run). curl's 28
      # means "still running", not "failed": the proposal poll below is the wait.
      note "--> curl -fsS -m 30 -X POST $(api_url)/api/dispatch/step"
      local drc=0; curl -fsS -m 30 -X POST "$(api_url)/api/dispatch/step" >&2 || drc=$?
      case "$drc" in
        0)  pass "demo-dispatch" "dispatcher claimed the item (a real inference against your endpoint ran)" ;;
        28) pass "demo-dispatch" "dispatcher claimed the item — the inference is still running (outlived the 30 s call; polling for the proposal)" ;;
        *)  fail "demo-dispatch" "POST /api/dispatch/step failed (curl $drc) — read $STATE_DIR/uvicorn.log"; return 1 ;;
      esac
    fi

    note "triage is thinking — a real model call; this commonly takes a few minutes"
    if ! poll_until 600 10 demo_awaiting; then
      local failed; failed="$(api_json "$(api_url)/api/dispatch" '(d.get("ledger") or {}).get("FAILED",0)')"
      if [[ "$failed" =~ ^[1-9] ]]; then
        fail "demo" "the triage run FAILED — read $STATE_DIR/uvicorn.log; recover with POST $(api_url)/api/work/<item_id>/requeue (never re-POST the email: a repeat Message-ID is a silent no-op)"
      else
        fail "demo" "no proposal parked within 10 minutes — read $STATE_DIR/uvicorn.log and $(api_url)/api/dispatch"
      fi
      return 1
    fi
  fi

  # ── the operator's moment — never scripted away ────────────────────────────
  useraction "demo-approve" "a proposal is waiting in the Decisions Inbox — open http://127.0.0.1:${CC_COCKPIT_PORT:-3080}, review it, and decide (approve to see the Executor perform it)"
  if ! is_tty; then
    return 3
  fi
  note ""
  note "== your move =="
  note "Open http://127.0.0.1:${CC_COCKPIT_PORT:-3080} -> Decisions Inbox. Read the proposal and its evidence,"
  note "then decide. This gate IS the product; nothing here will decide for you."
  note "(waiting — checks every 10s, Ctrl-C to abandon and re-run later)"
  if ! poll_until 1800 10 demo_decided; then
    fail "demo" "no decision within 30 minutes — re-run ./setup.sh demo whenever you are ready; it resumes here"
    return 1
  fi
  pass "demo-decided" "decision recorded on the event log"
  # The gate above counted a USERACTION; the operator has now taken it in this
  # very run, so the phase must not exit 3 ("stopped for your action") on a
  # completed install (2026-09-18 Windows run: PASS, PASS, exit 3).
  ACTIONS=0

  local execd; execd="$(api_json "$(api_url)/api/events?kind=proposal.executed&limit=1" 'len(d.get("events",[]))')"
  local wfail; wfail="$(api_json "$(api_url)/api/events?kind=work.failed&limit=1" 'len(d.get("events",[]))')"
  if [[ "$execd" =~ ^[1-9] ]]; then
    pass "demo-executed" "the Executor performed the approved action and stamped provenance (a real write to the local graph)"
  elif [[ "$wfail" =~ ^[1-9] ]]; then
    fail "demo-executed" "the approval was recorded but execution FAILED — read $STATE_DIR/uvicorn.log (the graph service is the usual suspect: ./setup.sh status)"
    return 1
  else
    # Reject/dismiss is a legitimate decision — the loop is still proven.
    pass "demo-executed" "no execution event — you rejected or dismissed, which proves the gate just as well"
  fi
  note ""
  note "The install is complete and the spine is proven end to end."
  note "Deliberately still OFF: the mail feed, the dispatch drain, and every"
  note "recurring schedule — the cockpit's Crons tab and the root .env flip"
  note "each one when YOU decide. The rest of onboarding is your EA's: the"
  note "cockpit asks your name, then the team tour asks about your world."
}

# Stop one detached server: TERM its pid file, then PROVE the port is free.
# Under Git Bash `kill` reports success against a native Windows process it
# never signalled (2026-09-17: "sent TERM", API still answering, boot then
# "already answering" on the stale process) — so the listener on the port is
# what gets killed when the signal did not land.
stop_listener() { # stop_listener <name> <pidfile> <port> <probe-path>
  local name="$1" pidf="$2" port="$3" path="$4" pid
  if [[ -f "$pidf" ]]; then
    pid="$(cat "$pidf")"; kill "$pid" 2>/dev/null || true; rm -f "$pidf"
  fi
  local i; for i in 1 2 3 4 5; do
    curl -fsS -m 2 -o /dev/null "http://127.0.0.1:${port}${path}" 2>/dev/null || break
    sleep 1
  done
  if curl -fsS -m 2 -o /dev/null "http://127.0.0.1:${port}${path}" 2>/dev/null; then
    if command -v taskkill >/dev/null 2>&1; then
      pid="$(netstat -ano 2>/dev/null | grep LISTENING | grep ":${port} " | awk '{print $NF}' | head -1)"
      [[ -n "$pid" ]] && taskkill //F //PID "$pid" >/dev/null 2>&1
      sleep 1
    fi
    if curl -fsS -m 2 -o /dev/null "http://127.0.0.1:${port}${path}" 2>/dev/null; then
      fail "stop-$name" "$name still answers on 127.0.0.1:${port} after TERM — stop it where you started it"
      return 1
    fi
  fi
  pass "stop-$name" "$name is down on 127.0.0.1:${port}"
}

cmd_stop() {
  CURPHASE=stop
  load_env >/dev/null 2>&1 || true
  stop_listener cockpit "$STATE_DIR/cockpit.pid" "${CC_COCKPIT_PORT:-3080}" /
  stop_listener uvicorn "$STATE_DIR/uvicorn.pid" "$(api_url | sed 's/.*://')" /health
}

# ─────────────────────────────────────────────────────────────────────────────
# status — postconditions only. Mutates nothing.
# ─────────────────────────────────────────────────────────────────────────────
phase_status() {
  phase_validate || true
  load_env || return 1
  pass "state-dir" "$STATE_DIR (logs, diagnostics, installed.manifest, pids — nothing inside the checkout)"

  if venv_python >/dev/null; then pass "venv" ".venv present"; else fail "venv" ".venv missing — run: ./setup.sh app"; fi
  local k
  for k in CC_LLM_API_KEY CC_EMBED_DIM CC_NEO4J_PASSWORD CC_LITELLM_SALT_KEY; do
    if is_placeholder "$(get_kv "$ENV_FILE" "$k")"; then
      fail "app-${k}" "$k is unset in the app's .env — run: ./setup.sh app"
    else
      pass "app-${k}" "$k is set in the app's .env"
    fi
  done
  step "verify-deployed" "every deployment/configuration assertion passed" "$HERE/verify.sh" || true
}

# ─────────────────────────────────────────────────────────────────────────────
# diagnose — the support bundle. NAMES of keys, never values.
# ─────────────────────────────────────────────────────────────────────────────
env_key_names() { # env_key_names <file>
  local line k v
  [[ -f "$1" ]] || { echo "  (file not present: $1)"; return 0; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
    k="${line%%=*}"; v="${line#*=}"
    if [[ -z "$v" ]]; then echo "  $k = (empty)"; else echo "  $k = (set)"; fi
  done <"$1"
}

phase_diagnose() {
  init_state
  local out="$STATE_DIR/setup-diagnostics.txt"
  load_env || true
  : "${CC_POD_PREFIX:=cc-}"
  {
    echo "Central Command single-node setup diagnostics — $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "Paste this whole file to Claude. It contains key NAMES only, never values."
    echo
    echo "== state directory (everything this install GENERATES is in here;"
    echo "   nothing is written inside the checkout)"
    echo "  $STATE_DIR"
    echo
    echo "== host"
    uname -a 2>&1
    echo
    echo "== tool versions"
    for t in podman uv node npm git curl openssl; do
      printf '%s: ' "$t"
      if command -v "$t" >/dev/null 2>&1; then
        # openssl has no --version; everything else here does.
        case "$t" in
          openssl) openssl version 2>&1 | head -1 ;;
          *)       { "$t" --version 2>&1 || true; } | head -1 ;;
        esac
      else
        echo "(not found)"
      fi
    done
    printf 'python: '; $PY -V 2>&1 | head -1
    echo
    echo "== setup-log.txt (last 40 lines — WHERE the run stopped)"
    tail -40 "$LOGFILE" 2>/dev/null || echo "  (no log yet)"
    echo
    echo "== .env — the one answer file (names only)"
    env_key_names "$ENV_FILE"
    echo
    echo "== discovery (classes only — the REPORT names internal hosts, so it is"
    echo "   pointed to, never inlined here)"
    if [[ -f "$STATE_DIR/discovery/discovery.env" ]]; then
      cat "$STATE_DIR/discovery/discovery.env"
      echo "  (full report: $STATE_DIR/discovery/discovery-report.md)"
    else
      echo "  (no discovery run on this machine — deploy/discover.sh)"
    fi
    echo
    echo "== compose services"
    compose_detect && "${COMPOSE_BIN[@]}" --env-file "$ENV_FILE" -f "$HERE/compose.yaml" ps -a 2>&1 || echo "  (no compose provider)"
    echo
    echo "== podman containers"
    podman ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}' 2>&1
    echo
    echo "== images"
    podman images --format '{{.Repository}}:{{.Tag}}' 2>&1
    echo
    echo "== last 100 log lines per cc container"
    # Names, THEN loop — a `podman ... | grep` pipeline inverts under pipefail
    # (grep exits at the first match and podman takes SIGPIPE).
    ctrs="$(podman ps -a --format '{{.Names}}' 2>/dev/null)"
    while IFS= read -r c; do
      [[ -z "$c" || "$c" != "${CC_POD_PREFIX}"* ]] && continue
      echo "---- $c"
      podman logs --tail 100 "$c" 2>&1
    done <<<"$ctrs"
    echo
    echo "== verify.sh (short poll budget — a bundle is collected FROM a broken"
    echo "   stack, where the patient answer costs a quarter of an hour)"
    CC_VERIFY_MAX_WAIT=15 "$HERE/verify.sh" 2>&1
  } >"$out"
  chmod 600 "$out" 2>/dev/null
  pass "diagnostics" "state dir is $STATE_DIR; wrote $out — paste it to Claude"
}

# ─────────────────────────────────────────────────────────────────────────────
run_phase() { # run_phase <name>  -> 0 clean / 1 hard fail / 2 warnings / 3 user action
  FAILS=0; WARNS=0; ACTIONS=0
  CURPHASE="$1"
  note ""
  note "======== phase: $1"
  "phase_$1"
  # A gate outranks a FAIL: the same event often prints both (the probe FAILs,
  # then the gate says whose move it is), and the exit code must say "stopped
  # for you", not "broken".
  (( ACTIONS )) && return 3
  (( FAILS )) && return 1
  (( WARNS )) && return 2
  return 0
}

# An initialized update.sh repo with unmerged imports means this tree is an
# EXISTING deployment mid-update, not a fresh install — the full run must not
# plow through it (same-tool-detects-mode, 2026-08-27 contract).
pending_update() {
  command -v git >/dev/null 2>&1 || return 1
  git -C "$REPO_ROOT" rev-parse --verify -q upstream >/dev/null 2>&1 || return 1
  ! git -C "$REPO_ROOT" merge-base --is-ancestor upstream local 2>/dev/null
}

usage() {
  cat >&2 <<USAGE
usage: ./setup.sh [validate|preflight|machine|fetch|llm|stack|app|verify|
                   test|boot|demo|stop|status|diagnose]
       ./setup.sh machine --dry-run    # report the diff, write nothing

  no argument   runs validate -> preflight -> machine -> fetch -> llm -> stack
                -> app -> verify -> test -> boot -> demo: zero to a working,
                human-approved demo in one command, stopping at the first
                phase that hard-fails or needs you
  machine       tells the podman MACHINE what .env says — the CA, the
                registries mirror/insecure drop-in, the proxy drop-in. A no-op
                on bare Linux (no machine); idempotent; prints the diff before
                each write. --dry-run reports only.
  fetch         acquires EVERY dependency (images by digest, the local image
                builds, the Python resolution, the cockpit's npm tree)
                before anything is deployed; stops (exit 3) naming the .env
                seam for each artifact it cannot get — deploy/AIRGAP.md
  test          the pytest gate (via the venv — no activation needed)
  boot          asks your name (once), starts the API detached, checks roster
  demo          feeds a fixture email, waits for YOUR approval in the
                cockpit, verifies the execution + provenance
  stop          stops the API this script started (boot's counterpart)
  exit codes    0 clean · 1 hard failure · 2 completed with warnings
                3 stopped for USER ACTION (see the last USERACTION line)
  status log    every check is appended to <state>/setup-log.txt, outside the
                checkout (./setup.sh diagnose prints the state dir first)
USAGE
}

main() {
  local cmd="${1:-all}"
  init_state           # the log file lives in there — resolve before logging
  logline "run start: ./setup.sh $cmd"
  case "$cmd" in
    machine)
      # The one phase that takes a flag: --dry-run reports the diff and writes
      # nothing (which is what preflight calls it as).
      FAILS=0; WARNS=0; ACTIONS=0; CURPHASE=machine
      note ""; note "======== phase: machine${2:+ $2}"
      phase_machine "${2:-}"
      local mrc=0
      (( ACTIONS )) && mrc=3; (( FAILS )) && mrc=1; (( ! ACTIONS && ! FAILS && WARNS )) && mrc=2
      logline "run end: ./setup.sh machine ${2:-} -> exit $mrc"
      exit $mrc
      ;;
    validate|preflight|fetch|llm|stack|app|verify|test|boot|demo|status|diagnose)
      run_phase "$cmd"; local prc=$?
      logline "run end: ./setup.sh $cmd -> exit $prc"
      exit $prc
      ;;
    stop)
      cmd_stop
      local src=0; (( WARNS )) && src=2; (( FAILS )) && src=1
      logline "run end: ./setup.sh stop -> exit $src"
      exit $src
      ;;
    all)
      if pending_update; then
        CURPHASE="dispatch"
        useraction "existing-install" "this tree is an existing deployment with an unapplied update — use ./update.sh plan (then apply), not a fresh setup run"
        logline "run end: ./setup.sh all -> exit 3"
        exit 3
      fi
      local worst=0 rc p
      for p in validate preflight machine fetch llm stack app verify test boot demo; do
        run_phase "$p"; rc=$?
        if (( rc == 1 )); then
          note ""
          note "phase '$p' failed. Fix the FAIL line above, then re-run just that phase:"
          note "    ./setup.sh $p"
          note "If the cause is not obvious: ./setup.sh diagnose  (then paste the file to Claude)"
          logline "run end: ./setup.sh all -> exit 1 (phase $p)"
          exit 1
        fi
        if (( rc == 3 )); then
          note ""
          note "phase '$p' stopped for YOUR action — see the USERACTION line above."
          note "When done, re-run:  ./setup.sh   (idempotent — it fast-forwards to here)"
          logline "run end: ./setup.sh all -> exit 3 (phase $p)"
          exit 3
        fi
        (( rc > worst )) && worst=$rc
      done
      note ""
      note "all phases complete."
      if ! git -C "$REPO_ROOT" rev-parse --verify -q upstream >/dev/null 2>&1; then
        note "make this deployment updatable (one-time): ./update.sh init"
      fi
      logline "run end: ./setup.sh all -> exit $worst"
      exit "$worst"
      ;;
    -h|--help|help) usage; exit 0 ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"
