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
#     validate    offline check of .env. No side effects.
#     preflight   named environment checks. No side effects.
#     fetch       acquire every dependency (public or mirror) — the only phase
#                 that needs the network; stops for the operator per artifact.
#     llm         secrets + LiteLLM up + probe its aliases + MEASURE the
#                 embedding dimension into .env
#     stack       assert the local images + bring the core stack up (compose)
#     app         venv, editable install, root .env, mint the spine's virtual
#                 key, cockpit build
#     verify      verify.sh (deployed) then live, then the capability manifest
#     test        the pytest gate, via the venv (no activation stumbles)
#     boot        elicit the operator's name (once), start the API detached,
#                 assert the roster hired          (counterpart: ./setup.sh stop)
#     demo        fixture email -> triage -> YOUR approval in the cockpit ->
#                 dry-run execution + provenance verified on the event log
#
#     stop        stop the API that `boot` started
#     status      re-run postconditions only, nothing mutating
#     diagnose    write setup-diagnostics.txt for pasting to Claude
#
#   No argument = ALL TEN phases in order — zero to a working, human-approved
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
#              deploy/single/setup-log.txt — the durable history a re-run
#              (or a diagnosing agent) reads first
#
#   Secret VALUES are never printed. Keys are referred to by NAME.
# ============================================================================

# NOT -e: a phase's checks must all report, and hard aborts are explicit
# (`|| return 1`) so the reason is always a FAIL line rather than a silent
# exit. -u and pipefail still hold.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
ENV_FILE="$HERE/.env"
APP_ENV="$REPO_ROOT/.env"

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
LOGFILE="$HERE/setup-log.txt"
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
# LiteLLM's first-boot Prisma migration and the speech engine's ~1GB model
# download (long enough that "unhealthy" is the normal state for half an hour,
# which is why that service carries no healthcheck at all).
wait_http() { # wait_http <url> <seconds>
  local deadline=$(( SECONDS + $2 ))
  until curl -fsS -m 5 "$1" >/dev/null 2>&1; do
    (( SECONDS < deadline )) || return 1
    sleep 3
  done
}

# ── the compose provider ────────────────────────────────────────────────────
# `podman compose` on the targets; `docker compose` is the dev-box fallback.
# Detected once, by RUNNING it (the same reason $PY is probed by running):
# `podman compose` exists as a subcommand even when no provider is installed
# behind it, and answers with an error only when actually invoked.
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
# Every compose call goes through here: one file, one profile set, one place
# to get the flags right.
compose() { # compose <args...>
  compose_detect || { fail "compose" "no compose provider — install podman-compose (or the docker compose plugin)"; return 1; }
  "${COMPOSE_BIN[@]}" -f "$HERE/compose.yaml" "$@"
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
  [[ -f "$ENV_FILE" ]] || { fail "answer-file" "$ENV_FILE not found — start from: cp env.example .env"; return 1; }
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
  # Tool-facing exports. uv reads no PIP_* variable (and UV_INDEX_URL is
  # deprecated), npm reads npm_config_* case-insensitively — so one seam each,
  # fanned out here to every name the tools actually look at.
  if [[ -n "${CC_PYPI_INDEX_URL:-}" ]]; then
    export PIP_INDEX_URL="$CC_PYPI_INDEX_URL" UV_DEFAULT_INDEX="$CC_PYPI_INDEX_URL"
  fi
  if [[ -n "${CC_PYTHON_MIRROR:-}" ]]; then export UV_PYTHON_INSTALL_MIRROR="$CC_PYTHON_MIRROR"; fi
  if [[ -n "${CC_NPM_REGISTRY:-}" ]]; then export NPM_CONFIG_REGISTRY="$CC_NPM_REGISTRY"; fi
  # Trust + proxy seams, same fan-out pattern: one .env fact, exported under
  # every name the acquisition tools actually read. podman pulls consult the
  # HOST trust store instead (deploy/AIRGAP.md).
  if [[ -n "${CC_CA_BUNDLE:-}" ]]; then
    export CURL_CA_BUNDLE="$CC_CA_BUNDLE" SSL_CERT_FILE="$CC_CA_BUNDLE" \
           REQUESTS_CA_BUNDLE="$CC_CA_BUNDLE" NODE_EXTRA_CA_CERTS="$CC_CA_BUNDLE" \
           NPM_CONFIG_CAFILE="$CC_CA_BUNDLE"
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

# Read one key's value out of a dotenv file WITHOUT sourcing it (the app's
# .env is not ours to execute). Last assignment wins, matching dotenv readers.
get_kv() { # get_kv <file> <key>
  local f="$1" k="$2" line out=""
  [[ -f "$f" ]] || { printf ''; return 0; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" == "$k="* ]] && out="${line#*=}"
  done <"$f"
  printf '%s' "$out"
}

# Set one key, in place, preserving the file's mode and every comment.
# printf/read are BUILTINS, so unlike `sed -i s|..|VALUE|` the value never
# appears in an argv and never shows up in `ps`.
set_kv() { # set_kv <file> <key> <value>
  local f="$1" k="$2" v="$3" tmp line found=0
  tmp="$(mktemp)" || return 1
  chmod 600 "$tmp" 2>/dev/null
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == "$k="* ]]; then
      printf '%s=%s\n' "$k" "$v" >>"$tmp"; found=1
    else
      printf '%s\n' "$line" >>"$tmp"
    fi
  done <"$f"
  (( found )) || printf '%s=%s\n' "$k" "$v" >>"$tmp"
  cat "$tmp" >"$f"     # rewrite in place: keeps the mode and the inode
  rm -f "$tmp"
}

# A value is "unset" for our purposes if it is empty or still a template
# placeholder. CHANGEME is the app .env.example's marker.
is_placeholder() { [[ -z "$1" || "$1" == *CHANGEME* ]]; }

# Set only when the current value is empty/placeholder — the operator's own
# edits are never overwritten. This is what makes the app phase re-runnable.
set_kv_if_unset() { # set_kv_if_unset <file> <key> <value> <check-name>
  local cur; cur="$(get_kv "$1" "$2")"
  if [[ -z "$3" ]] && is_placeholder "$cur"; then
    warn "$4" "$2 has no value to copy — the source variable is empty in deploy/single/.env"
    return 0
  fi
  if is_placeholder "$cur"; then
    set_kv "$1" "$2" "$3" && pass "$4" "$2 set in $(basename "$(dirname "$1")")/.env"
  else
    pass "$4" "$2 already set — left alone"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: validate — offline. Is the answer file answerable-from?
# ─────────────────────────────────────────────────────────────────────────────
phase_validate() {
  load_env || return 1
  pass "answer-file" "$ENV_FILE present"

  # The LLM provider (endpoint, key, model ids) is NOT an answer here any
  # more (2026-08-30): it is entered in the LiteLLM UI during the llm phase's
  # pause and stored in the proxy's database. A leftover from an older .env
  # is harmless but misleading, so say so.
  local v stale=""
  for v in CC_LLM_BASE_URL CC_LLM_API_KEY CC_CHAT_MODEL CC_EMBED_MODEL CC_EMBED_BASE_URL CC_EMBED_API_KEY; do
    [[ -n "${!v:-}" ]] && stale="$stale $v"
  done
  if [[ -n "$stale" ]]; then
    warn "provider-in-env" "ignored (the provider is configured in the LiteLLM UI now):$stale"
  else
    pass "provider-in-env" "no provider settings in .env — they belong in the LiteLLM UI"
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
      fail "compose-config" "compose.yaml does not validate — see: $(printf '%s ' "${COMPOSE_BIN[@]}")-f deploy/single/compose.yaml config"
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
    warn "package-indexes" "unreachable:$(printf ' %s' $probes) — run deploy/discover.sh to map this network, then set the mirror seams in .env (see env.example); deploy/AIRGAP.md"
  fi

  # Discovery cross-check (read-only). If /discover ran, its discovery.env
  # records the observed failure CLASS per resource (classes only — no
  # hostnames). It is EVIDENCE, never authority: a mismatch WARNs and names
  # the seam; the decision stays in .env. The conf file (operator's probe
  # answers, including diagnostic flags) is deliberately never read here.
  local denv="$REPO_ROOT/deploy/discovery.out/discovery.env"
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
      warn "discovery-crosscheck" "discovery observed failures with no seam set:$dmiss — the prescription is in deploy/discovery.out/discovery-report.md"
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
    pass "podman-registries" "podman sees: ${regs:-no registries.conf entries (fully-qualified refs only)}"
  fi
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
    2) pass "resolve-images" "resolved, with substitutions — see the WARN lines above and installed.manifest" ;;
    *) fail "resolve-images" "image resolution failed (exit $rc) — the FAIL/USERACTION lines above name the seam"; return 1 ;;
  esac
  # resolve-images.sh writes CC_IMG_* into .env; re-read so this shell has them.
  load_env || return 1

  local var ref check
  while IFS= read -r var; do
    ref="${!var:-}"; [[ -z "$ref" ]] && continue
    check="image-${var#CC_IMG_}"; check="${check,,}"
    if have_image "$ref"; then pass "$check" "$ref present"; continue; fi
    if podman pull -q "$ref" >/dev/null 2>&1 </dev/null; then
      pass "$check" "$ref pulled"
    else
      fail "$check" "$ref could not be pulled — seam: the CC_REGISTRY_* entry for its registry in .env (or a registries.conf mirror podman sees)"
    fi
  done < <(compgen -A variable CC_IMG_ | sort)
}

fetch_local() { # fetch_local <check> <ref> <build-script> <seams>
  local check="$1" ref="$2" script="$3" seams="$4"
  if have_image "$ref"; then pass "$check" "$ref present"; return 0; fi
  if "$script" >&2; then
    pass "$check" "$ref built"
  else
    fail "$check" "$ref failed to build against your sources — seams: $seams (see env.example)"
  fi
}

phase_fetch() {
  load_env || return 1
  : "${CC_GRAPHITI_TAG:=1.0.2-anthropic}"
  [[ -f "$HERE/images.txt" ]] || { fail "images-txt" "$HERE/images.txt missing"; return 1; }

  fetch_images

  fetch_local "image-graphiti" "localhost/cc-graphiti:${CC_GRAPHITI_TAG}" \
    "$HERE/build-graphiti-image.sh" "CC_REGISTRY_DOCKERIO, CC_APT_MIRROR, CC_PYPI_INDEX_URL"
  if [[ "$CC_ENABLE_SANDBOX" == 1 ]]; then
    fetch_local "image-sandbox" "localhost/cc-sandbox:1" \
      "$HERE/build-sandbox-image.sh" "CC_REGISTRY_DOCKERIO, CC_APT_MIRROR, CC_NPM_REGISTRY"
  else
    pass "image-sandbox" "skipped (CC_ENABLE_SANDBOX=0)"
  fi
  if [[ "$CC_ENABLE_CRAWLER" == 1 ]]; then
    fetch_local "image-crawler" "localhost/cc-crawler:1" \
      "$HERE/build-crawler-image.sh" "CC_REGISTRY_MCR, CC_PYPI_INDEX_URL"
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
    useraction "fetch" "$FAILS artifact(s) could not be acquired — fix the seam(s) named above in deploy/single/.env and re-run ./setup.sh fetch (acquired ones fast-forward); deploy/discover.sh maps what this network can reach, deploy/AIRGAP.md maps the seams"
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
  note "  login:        username 'admin', password = LITELLM_MASTER_KEY"
  note "                (or UI_USERNAME/UI_PASSWORD if set in .env)"
  note "                (the value is in deploy/single/.env — not printed here)"
  note ""
  note "  For each alias listed above, edit the row: replace every PLACEHOLDER"
  note "  (the model id after the prefix, the api_base host) and enter the API"
  note "  key — or create a credential under Endpoints and attach it. Keep:"
  note "    cc-default     openai/<your chat model>          the spine's alias"
  note "    graphiti-llm   openai/chat_completions/<model>   the prefix is REQUIRED"
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
  note ""
  note "When it looks right, re-run:  ./setup.sh llm   (it validates every alias, then continues)"
}

phase_llm() {
  load_env || return 1

  step "secrets" "credentials generated into deploy/single/.env" "$HERE/make-secrets.sh" || return 1
  # make-secrets.sh may have generated LITELLM_MASTER_KEY into the .env we
  # sourced before it; compose reads that file itself, and the register step
  # below needs the key in this environment.
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
  # deploys. LITELLM_MASTER_KEY is in the environment from load_env.
  local rrc=0
  CC_LITELLM_URL="http://127.0.0.1:${CC_LITELLM_PORT}" \
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
  if ! step "probe-structured" "graphiti-llm returned schema-constrained JSON through the Responses bridge" \
    "$HERE/discover-llm.sh" --proxy structured graphiti-llm; then
    llm_gate "the graphiti-llm alias did not return structured output (is the openai/chat_completions/ prefix intact?)"
    return 3
  fi
  if ! step "probe-rerank-model" "a completion came back through gpt-4.1-nano (Graphiti's reranker alias)" \
    "$HERE/discover-llm.sh" --proxy chat gpt-4.1-nano; then
    llm_gate "the gpt-4.1-nano alias did not return a completion"
    return 3
  fi

  # Speech: one synthesis, then transcribe what it said — the round trip proves
  # both aliases with real audio. First boot downloads the models, so the
  # engine gets the same wait LiteLLM did.
  if [[ "$CC_ENABLE_SPEECH" == "1" ]]; then
    if wait_http "http://127.0.0.1:${CC_SPEECH_PORT}/health" 600; then
      pass "speech-live" "cc-speech answers /health on 127.0.0.1:${CC_SPEECH_PORT}"
    else
      fail "speech-live" "cc-speech never answered /health (first boot downloads ~1GB of models — CC_HF_ENDPOINT reachable?) — run: ./setup.sh diagnose"
      return 1
    fi
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
  local dim
  dim="$("$HERE/discover-llm.sh" --proxy embed cc-embedding 2>/dev/null | tail -1)"
  if [[ ! "$dim" =~ ^[0-9]+$ ]]; then
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
  pass "embed-dimension" "CC_EMBED_DIM=${dim} recorded in deploy/single/.env"
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
    fail "embed-dimension" "CC_EMBED_DIM is empty in deploy/single/.env — run: ./setup.sh llm (it measures it through the cc-embedding alias)"
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

  local fresh=0
  if [[ -f "$APP_ENV" ]]; then
    pass "app-env" "the app's .env already exists — only empty values will be filled"
  else
    cp "$REPO_ROOT/.env.example" "$APP_ENV" || { fail "app-env" "could not create $APP_ENV"; return 1; }
    chmod 600 "$APP_ENV" 2>/dev/null
    fresh=1
    # chmod is a silent no-op on NTFS (2026-08-21 Windows validation, W8) —
    # verify the mode actually took rather than claiming it.
    local mode; mode="$(stat -c %a "$APP_ENV" 2>/dev/null || echo unknown)"
    if [[ "$mode" == "600" ]]; then
      pass "app-env" "created the app's .env from .env.example (0600)"
    else
      warn "app-env" "created the app's .env from .env.example, but the filesystem did not apply 0600 (Windows/NTFS) — it relies on the account's ACLs"
    fi
  fi

  # The spine gets its OWN LiteLLM virtual key, never the master key: a leak of
  # the agents' credential must not be able to reconfigure the proxy.
  local cur_key; cur_key="$(get_kv "$APP_ENV" CC_LLM_API_KEY)"
  if is_placeholder "$cur_key"; then
    [[ -n "${LITELLM_MASTER_KEY:-}" ]] || { fail "mint-key" "LITELLM_MASTER_KEY is missing from deploy/single/.env — run the llm phase first"; return 1; }
    local body minted
    # The master key travels via `-H @-` (stdin), so it is not visible in `ps`
    # while the request runs. NOT `-H @<(...)`: native Windows curl cannot
    # open MSYS's /proc fd paths (found live 2026-08-28). No `tags` field:
    # tags are an Enterprise feature and their presence 403s a community proxy.
    body="$(printf 'Authorization: Bearer %s\n' "$LITELLM_MASTER_KEY" | \
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
    set_kv "$APP_ENV" CC_LLM_API_KEY "$minted"
    pass "mint-key" "minted a LiteLLM virtual key scoped to cc-default + cc-tts + cc-stt and stored it as CC_LLM_API_KEY"
  else
    pass "mint-key" "CC_LLM_API_KEY already set — not minting a second key (a key minted before v2.21.0 lacks cc-tts/cc-stt: add them to it in the proxy UI, or clear CC_LLM_API_KEY to re-mint)"
  fi

  # Cross-file values: the same fact lives in two files, so copy it rather than
  # ask the operator to keep them in sync by hand.
  set_kv_if_unset "$APP_ENV" CC_LLM_PROXY_ADMIN_KEY "${LITELLM_MASTER_KEY:-}"  "app-admin-key"
  set_kv_if_unset "$APP_ENV" CC_EMBED_ALIAS         "cc-embedding"             "app-embed-alias"
  set_kv_if_unset "$APP_ENV" CC_EMBED_DIM           "${CC_EMBED_DIM:-}"        "app-embed-dim"
  set_kv_if_unset "$APP_ENV" CC_NEO4J_PASSWORD      "${NEO4J_PASSWORD:-}"      "app-neo4j-password"
  set_kv_if_unset "$APP_ENV" CC_LITELLM_SALT_KEY    "${LITELLM_SALT_KEY:-}"    "app-litellm-salt"
  set_kv_if_unset "$APP_ENV" CC_LITELLM_DB_URL \
    "postgresql://llmproxy:${LITELLM_POSTGRES_PASSWORD:-}@127.0.0.1:${CC_LITELLM_DB_PORT}/litellm" \
    "app-litellm-db-url"

  # dry_run is the safe first state: the Executor LOGS writes instead of
  # performing them. Forced only on a .env we just created — an operator who
  # deliberately set `live` keeps it across a re-run.
  if (( fresh )); then
    set_kv "$APP_ENV" CC_EXECUTOR_MODE dry_run
    pass "app-executor-mode" "CC_EXECUTOR_MODE=dry_run (nothing reaches the outside world until you change it)"
  else
    set_kv_if_unset "$APP_ENV" CC_EXECUTOR_MODE dry_run "app-executor-mode"
  fi
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
  echo "                   podman compose -f deploy/single/compose.yaml logs <service>"
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
  local p; p="$(get_kv "$APP_ENV" CC_API_PORT)"; printf 'http://127.0.0.1:%s' "${p:-8080}"
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
    # the script asking, deterministically); headless, gate.
    local opname; opname="$(get_kv "$APP_ENV" CC_OPERATOR_NAME)"
    if is_placeholder "$opname"; then
      if is_tty; then
        note ""
        note "== one question before first boot =="
        read -rp "What should the agents call you? " opname
        [[ -n "$opname" ]] || { fail "operator-name" "no name given — first boot needs one"; return 1; }
        set_kv "$APP_ENV" CC_OPERATOR_NAME "$opname"
        pass "operator-name" "CC_OPERATOR_NAME recorded in the root .env"
      else
        useraction "operator-name" "set CC_OPERATOR_NAME in the root .env, then re-run: ./setup.sh boot"
        return 3
      fi
    else
      pass "operator-name" "CC_OPERATOR_NAME already set — left alone"
    fi

    local uv_bin; uv_bin="$(venv_uvicorn)" || { fail "boot-api" "uvicorn not in .venv — run: ./setup.sh app"; return 1; }
    note "--> starting uvicorn detached (log: $HERE/uvicorn.log · stop: ./setup.sh stop)"
    ( cd "$REPO_ROOT" && nohup "$uv_bin" central_command.api.app:app --host 127.0.0.1 \
        --port "$(api_url | sed 's/.*://')" >>"$HERE/uvicorn.log" 2>&1 &
      echo $! >"$HERE/uvicorn.pid" )
    if wait_http "$(api_url)/health" 90; then
      pass "boot-api" "API answering at $(api_url) (first boot hires the roster)"
    else
      fail "boot-api" "API never answered /health — read $HERE/uvicorn.log"
      return 1
    fi
  fi

  local n; n="$(api_json "$(api_url)/api/agents" 'len(d.get("agents", d if isinstance(d, list) else []))')"
  if [[ "$n" =~ ^[0-9]+$ ]] && (( n > 0 )); then
    pass "boot-roster" "$n agents on the roster"
  else
    fail "boot-roster" "the roster is empty — read $HERE/uvicorn.log (seed guard? database?)"
    return 1
  fi
  note "cockpit: $(api_url)  (a fresh install runs the executor in dry_run — nothing touches the world until you flip it)"
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
    local eml="$REPO_ROOT/fixtures/emails/001-invoice-due.eml"
    [[ -f "$eml" ]] || { fail "demo-feed" "$eml missing"; return 1; }
    step "demo-feed" "fixture email enrolled (a repeat Message-ID is a no-op by design)" \
      bash -c "$PY -c 'import json,sys,pathlib;print(json.dumps({\"text\":pathlib.Path(sys.argv[1]).read_text()}))' '$eml' \
        | curl -fsS -X POST -H 'content-type: application/json' -d @- '$(api_url)/api/emails'" || return 1

    # Fire the dispatcher only when nothing is already working the queue.
    local busy; busy="$(api_json "$(api_url)/api/dispatch" 'd.get("in_flight",0)')"
    if [[ "$busy" =~ ^[1-9] ]]; then
      pass "demo-dispatch" "a run is already in flight — riding it"
    else
      step "demo-dispatch" "dispatcher claimed the item (a real inference against your endpoint starts now)" \
        curl -fsS -m 30 -X POST "$(api_url)/api/dispatch/step" || return 1
    fi

    note "triage is thinking — a real model call; this commonly takes a few minutes"
    if ! poll_until 600 10 demo_awaiting; then
      local failed; failed="$(api_json "$(api_url)/api/dispatch" '(d.get("ledger") or {}).get("FAILED",0)')"
      if [[ "$failed" =~ ^[1-9] ]]; then
        fail "demo" "the triage run FAILED — read $HERE/uvicorn.log; recover with POST $(api_url)/api/work/<item_id>/requeue (never re-POST the email: a repeat Message-ID is a silent no-op)"
      else
        fail "demo" "no proposal parked within 10 minutes — read $HERE/uvicorn.log and $(api_url)/api/dispatch"
      fi
      return 1
    fi
  fi

  # ── the operator's moment — never scripted away ────────────────────────────
  useraction "demo-approve" "a proposal is waiting in the Decisions Inbox — open $(api_url), review it, and decide (approve to see the dry-run execution)"
  if ! is_tty; then
    return 3
  fi
  note ""
  note "== your move =="
  note "Open $(api_url) -> Decisions Inbox. Read the proposal and its evidence,"
  note "then decide. This gate IS the product; nothing here will decide for you."
  note "(waiting — checks every 10s, Ctrl-C to abandon and re-run later)"
  if ! poll_until 1800 10 demo_decided; then
    fail "demo" "no decision within 30 minutes — re-run ./setup.sh demo whenever you are ready; it resumes here"
    return 1
  fi
  pass "demo-decided" "decision recorded on the event log"

  local execd; execd="$(api_json "$(api_url)/api/events?kind=proposal.executed&limit=1" 'len(d.get("events",[]))')"
  if [[ "$execd" =~ ^[1-9] ]]; then
    pass "demo-executed" "execution recorded with provenance (dry-run: a logged simulation — flip CC_EXECUTOR_MODE deliberately for live writes)"
  else
    # Reject/dismiss is a legitimate decision — the loop is still proven.
    pass "demo-executed" "no execution event — you rejected or dismissed, which proves the gate just as well"
  fi
  note ""
  note "The install is complete and the spine is proven end to end."
  note "Deliberately still OFF: live executor mode, the mail feed, the dispatch"
  note "drain, and every recurring schedule — the cockpit's Crons tab and the"
  note "root .env flip each one when YOU decide."
}

cmd_stop() {
  CURPHASE=stop
  local pidf="$HERE/uvicorn.pid"
  if [[ -f "$pidf" ]]; then
    local pid; pid="$(cat "$pidf")"
    if kill "$pid" 2>/dev/null; then
      pass "stop" "sent TERM to uvicorn (pid $pid)"
    else
      warn "stop" "pid $pid was not running — removing the stale pid file"
    fi
    rm -f "$pidf"
  elif api_up; then
    warn "stop" "an API answers at $(api_url) but was not started by this script — stop it where you started it"
  else
    pass "stop" "nothing to stop"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# status — postconditions only. Mutates nothing.
# ─────────────────────────────────────────────────────────────────────────────
phase_status() {
  phase_validate || true
  load_env || return 1

  if venv_python >/dev/null; then pass "venv" ".venv present"; else fail "venv" ".venv missing — run: ./setup.sh app"; fi
  local k
  for k in CC_LLM_API_KEY CC_EMBED_DIM CC_NEO4J_PASSWORD CC_LITELLM_SALT_KEY; do
    if is_placeholder "$(get_kv "$APP_ENV" "$k")"; then
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
  local out="$HERE/setup-diagnostics.txt"
  load_env || true
  : "${CC_POD_PREFIX:=cc-}"
  {
    echo "Central Command single-node setup diagnostics — $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "Paste this whole file to Claude. It contains key NAMES only, never values."
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
    echo "== deploy/single/.env (names only)"
    env_key_names "$ENV_FILE"
    echo
    echo "== the app's .env (names only)"
    env_key_names "$APP_ENV"
    echo
    echo "== discovery (classes only — the REPORT names internal hosts, so it is"
    echo "   pointed to, never inlined here)"
    if [[ -f "$REPO_ROOT/deploy/discovery.out/discovery.env" ]]; then
      cat "$REPO_ROOT/deploy/discovery.out/discovery.env"
      echo "  (full report: deploy/discovery.out/discovery-report.md)"
    else
      echo "  (no discovery run on this machine — deploy/discover.sh)"
    fi
    echo
    echo "== compose services"
    compose_detect && "${COMPOSE_BIN[@]}" -f "$HERE/compose.yaml" ps -a 2>&1 || echo "  (no compose provider)"
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
  pass "diagnostics" "wrote $out — paste it to Claude"
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
usage: ./setup.sh [validate|preflight|fetch|llm|stack|app|verify|test|boot|
                   demo|stop|status|diagnose]

  no argument   runs validate -> preflight -> fetch -> llm -> stack -> app
                -> verify -> test -> boot -> demo: zero to a working,
                human-approved demo in one command, stopping at the first
                phase that hard-fails or needs you
  fetch         acquires EVERY dependency (images by digest, the local image
                builds, the Python resolution, the cockpit's npm tree)
                before anything is deployed; stops (exit 3) naming the .env
                seam for each artifact it cannot get — deploy/AIRGAP.md
  test          the pytest gate (via the venv — no activation needed)
  boot          asks your name (once), starts the API detached, checks roster
  demo          feeds a fixture email, waits for YOUR approval in the
                cockpit, verifies the dry-run execution + provenance
  stop          stops the API this script started (boot's counterpart)
  exit codes    0 clean · 1 hard failure · 2 completed with warnings
                3 stopped for USER ACTION (see the last USERACTION line)
  status log    every check is appended to deploy/single/setup-log.txt
USAGE
}

main() {
  local cmd="${1:-all}"
  logline "run start: ./setup.sh $cmd"
  case "$cmd" in
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
      for p in validate preflight fetch llm stack app verify test boot demo; do
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
