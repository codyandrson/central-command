# shellcheck shell=bash
# ============================================================================
# phases/check.sh — the `check` phase of the single-node install (deploy/single).
#
#   CHECK: the dry GATE: everything that can be checked without changing
#   anything, in nine sections and one table. It COMPOSES `validate` (the
#   answer file, offline) and `preflight` (this host and its podman machine),
#   which stay callable on their own and live here with it.
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
#   ROWS (steps.tsv, phase `check`) — step, kind, probe; a probe marked
#   (setup.sh) is shared with another phase or the driver and lives there:
#     tree-pristine                      run    p_tree_pristine  (setup.sh)
#     ca-bundle                          human  p_ca_bundle
#     linger                             run    p_linger
# ============================================================================

[[ -n "${CC_PHASE_CHECK_LOADED:-}" ]] && return 0
CC_PHASE_CHECK_LOADED=1

# ── the compose FLOOR (2026-09-25) ──────────────────────────────────────────
# Detecting a provider is not enough: this profile needs podman-compose 1.6.0
# or newer, and the work site ran 1.5.0. Two things setup.sh relies on landed
# in exactly that release (2026-06-03): `up --wait` (the deploy phases wait on
# compose.yaml's healthchecks rather than polling) and the config-hash change
# that made a re-run of `up -d` idempotent — under 1.5.0 the second run dies
# with `container name ... is already in use`, so every re-run of the install
# failed. docker compose has no floor: its `--wait` predates v2 and it has
# always reconciled an existing container.
#
# Numeric dotted compare, a >= b. Non-numeric suffixes (rc1, -dev) are cut off
# the component, so 1.6.0rc1 reads as 1.6.0 — a floor is not a release gate.
version_ge() { # version_ge <a> <b>  -> 0 when a >= b
  local a="$1" b="$2" ia=() ib=() i x y
  IFS=. read -r -a ia <<<"$a"
  IFS=. read -r -a ib <<<"$b"
  for (( i = 0; i < ${#ia[@]} || i < ${#ib[@]}; i++ )); do
    x="${ia[i]:-0}"; y="${ib[i]:-0}"
    x="${x%%[!0-9]*}"; y="${y%%[!0-9]*}"
    x="${x:-0}"; y="${y:-0}"
    (( 10#$x > 10#$y )) && return 0
    (( 10#$x < 10#$y )) && return 1
  done
  return 0
}
# PURE: prints nothing, returns 0 (ok) / 1 (too old) / 2 (unparseable), and
# reports WHAT it read in two globals so the caller can name it.
#
# The text it parses is a whole `<provider> compose version` output, which on
# podman is THREE lines — the external-provider banner, `podman version 5.8.3`,
# then `podman-compose version 1.6.0`. So: never `head -1` (that is the
# banner), and match the PRODUCT name, not the first number on the page.
COMPOSE_VERSION=""
COMPOSE_FLAVOUR=""
COMPOSE_FLOOR_PODMAN="1.6.0"
compose_version_floor_ok() { # compose_version_floor_ok <provider> <version-output>
  local provider="$1" out="$2" v
  COMPOSE_VERSION=""; COMPOSE_FLAVOUR=""
  # podman-compose FIRST: `podman compose version` prints a `podman version`
  # line too, and podman can also drive docker-compose as its external
  # provider (then there is no podman-compose line at all and no floor).
  v="$(printf '%s\n' "$out" | sed -n 's/.*podman-compose version[: ]*v*\([0-9][0-9.]*\).*/\1/p' | tail -1)"
  if [[ -n "$v" ]]; then
    COMPOSE_FLAVOUR="podman-compose"; COMPOSE_VERSION="$v"
    version_ge "$v" "$COMPOSE_FLOOR_PODMAN" && return 0
    return 1
  fi
  v="$(printf '%s\n' "$out" | sed -n 's/.*[Dd]ocker [Cc]ompose version[: ]*v*\([0-9][0-9.]*\).*/\1/p' | tail -1)"
  if [[ -n "$v" ]]; then
    COMPOSE_FLAVOUR="docker-compose"; COMPOSE_VERSION="$v"
    return 0
  fi
  COMPOSE_FLAVOUR="$provider"
  return 2
}

# ── check's answers section, driven by the schema ────────────────────────────
# Replaces the hand-written required-key list v2.44.0 carried: a required key
# left blank is a USERACTION naming it and the command that asks it; a key that
# IS set but fails its own validator is a FAIL carrying the validator's reason.
# Nothing here writes, and no validator may — check executes nothing.
check_schema_answers() {
  q_schema_load || return 1
  local row key prompt req validator when val reason
  local miss=0 bad=0
  while IFS= read -r row; do
    key="$(q_field "$row" 1)"
    prompt="$(q_field "$row" 3)"
    req="$(q_field "$row" 5)"
    validator="$(q_field "$row" 6)"
    when="$(q_field "$row" 7)"
    q_when_holds "$when" q_current || continue
    val="${Q_ANS[$key]}"
    if [[ -z "$val" ]]; then
      if [[ "$req" == y ]]; then
        useraction "answers-$key" "$key is required and .env does not set it — ./setup.sh configure asks it: $prompt"
        miss=$((miss+1))
      fi
      continue
    fi
    [[ -n "$validator" && "$validator" != "-" ]] || continue
    if ! reason="$("$validator" "$val")"; then
      fail "answers-$key" "$key is set but not usable: $reason (./setup.sh configure asks it: $prompt)"
      bad=$((bad+1))
    fi
  done < <(q_rows "$QUESTIONS")
  if (( miss == 0 && bad == 0 )); then
    pass "answers-schema" "every question in questions.tsv that applies to these flags is answered and valid (no row is REQUIRED: each has a working default or a documented blank meaning, and a blank upstream LLM means the catalog is entered in the LiteLLM UI at the llm phase's pause)"
  fi
  return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: validate — offline. Is the answer file answerable-from?
# ─────────────────────────────────────────────────────────────────────────────
# The phases below are COMPOSED by `check` (design record D5), so each one that
# check reuses is its own function: check must run the same code, never a second
# copy of the same probe.
phase_validate() {
  validate_answers || return 1
  validate_compose_config
}

validate_answers() {
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

  validate_ports

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

  # 127.0.0.1, never localhost: Windows resolves localhost to ::1 first and the
  # podman machine publishes IPv4-only. A fact about the ANSWER FILE's content,
  # so it lives with the other offline .env checks (it was in preflight until
  # v2.44.0, where `check`'s answers section is its home).
  #
  # EXEMPT: CC_REGISTRY_DOCKERIO/_GHCR/_MCR and operator CC_IMG_* pins (F26,
  # 2026-09-24 Windows testbed run). Those name a registry MIRROR — typically
  # the one the podman machine itself publishes — and `localhost:5000` is the
  # ONE spelling that reaches it from BOTH the Windows host and inside the
  # machine; `127.0.0.1` does not reach the machine's published port the same
  # way from inside it (measured on the testbed). The loopback rule is about
  # the APP's own URLs, which this key never carries.
  local lh; lh="$(grep -n 'localhost' "$ENV_FILE" | grep -v '^[0-9]*:#' \
    | grep -vE '^[0-9]+:(CC_REGISTRY_(DOCKERIO|GHCR|MCR)|CC_IMG_[A-Za-z0-9_]+)=')"
  [[ -z "$lh" ]] \
    && pass "loopback-addressing" ".env uses 127.0.0.1 throughout" \
    || warn "loopback-addressing" ".env mentions localhost — use 127.0.0.1 (Windows resolves localhost to ::1 first)"

  # schema.sql is bind-mounted into the spine's initdb directory straight from
  # the repo, so its absence is a broken deployment, not just a broken test.
  [[ -f "$REPO_ROOT/central_command/db/schema.sql" ]] \
    && pass "repo-layout" "schema.sql found — running inside the repo" \
    || fail "repo-layout" "central_command/db/schema.sql not found — is this the Central Command repo?"
}

# THE ports, in one place: numeric, in range, and distinct — two services on one
# hostPort is a published port that half-starts the stack. `check` calls this
# and then adds a LISTENER probe per port, which needs no second list.
CC_PORT_KEYS=(CC_PG_PORT CC_LITELLM_PORT CC_LITELLM_DB_PORT CC_GRAPHITI_PORT
              CC_NEO4J_BOLT_PORT CC_NEO4J_HTTP_PORT CC_N8N_PORT CC_CRAWLER_PORT
              CC_SPEECH_PORT CC_COCKPIT_PORT CC_API_PORT)
validate_ports() {
  local seen="" p val dup=0 bad_port=0
  for p in "${CC_PORT_KEYS[@]}"; do
    val="${!p:-}"
    [[ -z "$val" ]] && continue
    if [[ ! "$val" =~ ^[0-9]+$ ]] || (( val < 1 || val > 65535 )); then
      fail "port-${p}" "$p=$val is not a valid port"; bad_port=1; continue
    fi
    if grep -qx "$val" <<<"$seen"; then dup=1; fail "port-${p}" "$p=$val collides with another CC_*_PORT"; fi
    seen="$seen$val"$'\n'
  done
  (( bad_port || dup )) || pass "ports" "all configured ports are numeric, in range and distinct"
}

# Does the deployment file parse with THIS .env? The arithmetic validate used to
# do by hand — service references, port collisions, profile membership,
# interpolation — is compose's now.
#
# Since v2.44.0 the provider's own WARNINGS are read too: both providers print
# `variable is not set` (docker compose) / `Missing required variable`
# (podman-compose) on stderr and still exit 0, rendering an EMPTY value into a
# credential or an image ref. A silent empty password is the failure this check
# exists to prevent, so each named variable becomes a FAIL.
validate_compose_config() {
  compose_detect || {
    warn "compose-config" "no compose provider here to validate compose.yaml with (the host section checks for one)"
    return 0
  }
  local out rc=0
  out="$(compose --profile n8n --profile crawler --profile speech config 2>&1 >/dev/null)" || rc=$?
  if (( rc )); then
    note "$out"
    fail "compose-config" "compose.yaml does not validate — see: $(printf '%s ' "${COMPOSE_BIN[@]}")--env-file .env -f deploy/single/compose.yaml config"
    return 0
  fi
  local unset_keys="" line
  # `variable is not set` (compose-go), `Missing required variable` / `not set`
  # (podman-compose) — the KEY is what either message quotes.
  while IFS= read -r line; do
    [[ "$line" == *"is not set"* || "$line" == *"Missing required variable"* ]] || continue
    local k; k="$(printf '%s' "$line" | grep -oE '\b[A-Z][A-Z0-9_]{2,}\b' | head -1)"
    [[ -n "$k" ]] || continue
    [[ " $unset_keys " == *" $k "* ]] || unset_keys="${unset_keys:+$unset_keys }$k"
  done <<<"$out"
  if [[ -n "$unset_keys" ]]; then
    note "$out"
    local k g generated
    for k in $unset_keys; do
      generated=0
      for g in "${CC_GENERATED_KEYS[@]}"; do [[ "$g" == "$k" ]] && generated=1; done
      if (( generated )); then
        # Blank ON PURPOSE in a fresh .env: make-secrets.sh generates it during
        # the llm phase. A FAIL here would refuse every first install.
        warn "compose-var-${k}" "compose.yaml needs $k and .env does not set it yet — deploy/single/make-secrets.sh generates it (the llm phase runs it). Until then compose would render an EMPTY credential, which it reports as a warning and not an error"
      else
        fail "compose-var-${k}" "compose.yaml needs $k and .env does not set it — the render would substitute an EMPTY value (a blank credential or a bare image tag), which compose reports as a warning and not an error"
      fi
    done
  fi
  pass "compose-config" "compose.yaml is valid with this .env (all profiles)${unset_keys:+ — but see the compose-var-* lines above}"
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: preflight — is this MACHINE able to run the install?
# ─────────────────────────────────────────────────────────────────────────────
phase_preflight() {
  preflight_host || return 1
  machine_report
}

# ── the tree is the release, or nothing runs (2026-10-01 design record, D10) ─
# The operator's rule: an install configures through .env and the environment,
# and never rewrites any part of Central Command; anything else it needs is a
# FINDING, carried back to a development session, fixed properly, and released.
# It was prose in the skill until now ("never a script, a Dockerfile,
# images.txt") and the 2026-09-24 work-site session regenerated the npm lock,
# hand-edited images.txt and commented out lock pins anyway — each a defect
# later blamed on something else.
#
# So this row, plus the same test at the top of load_env for every mutating
# phase and at the top of update.sh's apply. There is no flag past it; the
# developer bypass CC_SETUP_UNLEDGERED does NOT cover it.
check_tree_pristine() {
  local rc=0
  cc_tree_diff "$REPO_ROOT" || rc=$?
  # The verdict p_tree_pristine would reach, remembered for the rest of the run
  # (setup.sh, P5): the row's probe and every mutating phase's guard ask again.
  (( rc == 1 )) || TREE_PRISTINE_HELD=1
  case "$rc" in
    0) pass "tree-pristine" "the working tree and HEAD carry no difference from the installed release — this deployment is the release it claims to be" ;;
    1) fail "tree-pristine" "this deployment DIFFERS from the release it claims to be: $TREE_DIFF_PATHS. An install configures through .env and never rewrites any part of Central Command (2026-10-01 design record, D10) — restore those paths (git restore -- <path>) and carry the change back to a development session as a finding: ./setup.sh report writes one. There is no flag past this line" ;;
    *) useraction "tree-pristine" "this tree has no git baseline, so nothing can prove it still matches the release it claims to be. Run ./update.sh init ONCE (it snapshots the unzipped tree and creates the \`upstream\`/\`local\` branches), then re-run — it is also what makes this deployment updatable" ;;
  esac
  return 0
}

# Everything about THIS host. `check`'s host section is exactly this function.
preflight_host() {
  load_env || return 1
  check_tree_pristine

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
    local cver; cver="$("${COMPOSE_BIN[@]}" version 2>/dev/null)"
    pass "compose-provider" "$(printf '%s ' "${COMPOSE_BIN[@]}")($(printf '%s\n' "$cver" | head -1))"
    # ...and the VERSION, separately: a provider that answers can still be too
    # old to run this profile (the work site's podman-compose 1.5.0, 2026-09-25).
    local frc=0; compose_version_floor_ok "${COMPOSE_BIN[0]}" "$cver" || frc=$?
    case "$frc" in
      0)
        if [[ "$COMPOSE_FLAVOUR" == "podman-compose" ]]; then
          pass "compose-version" "${COMPOSE_FLAVOUR} ${COMPOSE_VERSION} (floor ${COMPOSE_FLOOR_PODMAN})"
        else
          pass "compose-version" "${COMPOSE_FLAVOUR} ${COMPOSE_VERSION} (no floor)"
        fi
        ;;
      1)
        fail "compose-version" "podman-compose ${COMPOSE_VERSION} is older than ${COMPOSE_FLOOR_PODMAN} — "'this profile needs `up --wait`, added in 1.6.0 (the deploy phases wait on compose.yaml healthchecks), AND the 1.6.0 config-hash fix, without which a SECOND `up -d` dies with "container name ... is already in use". Upgrade it: `uv tool install podman-compose==1.6.0`, or `pip install podman-compose==1.6.0` from your CC_PYPI_INDEX_URL mirror in the air gap'
        ;;
      *)
        warn "compose-version" "could not read a version from \`${COMPOSE_BIN[*]} version\` — saw: $(printf '%s\n' "$cver" | tr '\n' '|' | cut -c1-160). podman-compose must be ${COMPOSE_FLOOR_PODMAN} or newer; check it by hand"
        ;;
    esac
  else
    fail "compose-provider" "neither 'podman compose' nor 'docker compose' answers — install podman-compose (or Podman Desktop's compose support)"
    fail "compose-version" "no compose provider to read a version from — podman-compose must be ${COMPOSE_FLOOR_PODMAN} or newer"
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

  # RAM: on a machine-backed podman the number that matters is the MACHINE's,
  # not this host's — every container runs inside it, so /proc/meminfo answers
  # about the wrong computer and a 16 GB laptop with the default 2 GiB machine
  # passed a check it should have failed (ledger F6, Windows testbed
  # 2026-09-24). The host figure stays, as a note. The verdict itself lives in
  # machine-lib.sh so it can be unit-tested where no machine exists.
  local host_gb="" mach_mib="" mem_verdict
  if [[ -r /proc/meminfo ]]; then
    local kb; kb="$(awk '/^MemTotal:/{print $2}' /proc/meminfo)"
    [[ "$kb" =~ ^[0-9]+$ ]] && host_gb=$(( kb / 1024 / 1024 ))
  fi
  local mmem_machine; mmem_machine="$(machine_name)"
  if [[ -n "$mmem_machine" ]]; then
    # Read-only (check EXECUTES nothing). MiB — see cc_memory_verdict's header.
    mach_mib="$(podman machine inspect --format '{{.Resources.Memory}}' "$mmem_machine" 2>/dev/null | head -1 | tr -d ' \r')"
  fi
  mem_verdict="$(cc_memory_verdict "$host_gb" "$mach_mib")"
  case "$mem_verdict" in
    PASS\ *) pass "memory" "${mem_verdict#PASS }" ;;
    *)       warn "memory" "${mem_verdict#WARN }" ;;
  esac
  local freegb
  freegb="$(df -Pk "$REPO_ROOT" 2>/dev/null | awk 'NR==2{print int($4/1024/1024)}')"
  if [[ "$freegb" =~ ^[0-9]+$ ]] && (( freegb >= 20 )); then
    pass "disk" "${freegb} GB free at the repo root"
  else
    warn "disk" "${freegb:-unknown} GB free at the repo root — images alone need ~15 GB"
  fi

  # Rootless podman without lingering dies with your last login session and
  # takes every container with it. Hit for real over SSH during validation.
  # A FAIL since v2.57.0 (2026-10-01 record, D6): `boot` now supervises the
  # API, the cockpit and the sandbox runner as systemd --user units, and
  # without lingering those die with the session too — the install would be
  # "supervised" only while somebody is logged in. The verdict is
  # supervise-lib.sh's (pure, tested); the row is check/linger.
  local lv; lv="$(linger_verdict)"
  case "$lv" in
    PASS\ *) pass "linger" "${lv#PASS }" ;;
    FAIL\ *) fail "linger" "${lv#FAIL }" ;;
    *)       pass "linger" "${lv#NA }" ;;
  esac

  # On Windows the OS trust store is what schannel curl and podman.exe
  # consult — CC_CA_BUNDLE alone never reaches them. Usually the corporate CA
  # is there by policy; when it is not, the fix is the operator's (admin).
  cc_uname_s
  if [[ -n "${CC_CA_BUNDLE:-}" && ( "$UNAME_S" == MINGW* || "$UNAME_S" == MSYS* ) ]] && command -v certutil >/dev/null 2>&1; then
    local thumb; thumb="$(openssl x509 -in "$CC_CA_BUNDLE" -noout -fingerprint -sha1 2>/dev/null | sed 's/.*=//; s/://g')"
    if [[ -n "$thumb" ]] && certutil -store Root "$thumb" >/dev/null 2>&1; then
      pass "ca-windows-store" "CC_CA_BUNDLE's CA is in the Windows Root store (schannel curl and podman.exe trust it)"
    else
      useraction "ca-windows-store" "CC_CA_BUNDLE's CA is NOT in the Windows Root store — Git's curl (schannel) and podman.exe will not trust the proxy; as Administrator: certutil -addstore Root \"$(cygpath -w "$CC_CA_BUNDLE" 2>/dev/null || echo "$CC_CA_BUNDLE")\""
    fi
  fi

  # Index probe: INFORMATIONAL, against the CONFIGURED indexes (a mirror in
  # .env, else the public ones) — the real acquisition is the fetch phase.
  # Unreachable indexes are a fact about the network; CC_AIRGAP is how the
  # operator says it is deliberate. `deploy/discover.sh` is the tool that maps
  # what this network can actually reach and which mirrors to write into .env.
  # The WARN names only the indexes that did not answer (index_reachable).
  local u reach=1 probes unreached=""
  probes="${CC_PYPI_INDEX_URL:-https://pypi.org/simple/} ${CC_NPM_REGISTRY:-https://registry.npmjs.org/}"
  for u in $probes; do
    index_reachable "$u" || { reach=0; unreached="$unreached $u"; }
  done
  if (( reach )); then
    pass "package-indexes" "reachable:$(printf ' %s' $probes)"
  elif [[ "$CC_AIRGAP" == "1" ]]; then
    pass "package-indexes" "unreachable, as expected with CC_AIRGAP=1 — fetch will say per artifact"
  else
    warn "package-indexes" "unreachable:${unreached} — run deploy/discover.sh to map this network, then set the mirror seams in .env (see .env.example's deployment section); deploy/AIRGAP.md"
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
    pass "podman-registries" "podman sees: ${regs:-no registries.conf entries (fully-qualified refs only)} (the machine phase owns the drop-in that puts entries there, and prints its diff before it writes one)"
  fi
}

# One package index, reachable or not — asked with HEAD, not GET: the public
# simple index's ROOT is the whole index (46.7 MB, 6-6.5 s at the 2026-10-02
# testbed's evening bandwidth), so a GET under -m 10 measured the link's speed
# rather than reachability, and on a slower evening one run printed
# `package-indexes: unreachable` and then, 16 s later, `index-pypi ... answers
# HTTP 200` (F17). A server that refuses HEAD (curl 22: an HTTP error) gets the
# GET it always got; a connection that failed is not asked twice.
index_reachable() { # index_reachable <url>  -> 0 = it answered
  local rc=0
  curl -fsS -I -m 10 -o /dev/null "$1" 2>/dev/null || rc=$?
  if (( rc == 22 )); then rc=0; curl -fsS -m 10 -o /dev/null "$1" 2>/dev/null || rc=$?; fi
  return "$rc"
}

# The machine, REPORTED: current state plus the diff the `machine` phase would
# apply. `preflight` ends with this and `check`'s machine section IS this — one
# function, so the two can never drift. Nothing here writes.
machine_report() {
  load_env || return 1
  [[ -n "$(machine_name)" ]] || {
    pass "machine" "no podman machine on this host — nothing to configure (bare Linux runs containers directly)"
    return 0
  }
  machine_report_proxy_egress
  phase_machine --dry-run
}

# The machine's OWN process environment for pulls is set at `podman machine
# start` from the host environment (doc-verified); writing a systemd drop-in
# inside the machine is not, and is an open item in the design record. So this
# one stays a USERACTION — it is the operator's move, not a phase's.
machine_report_proxy_egress() {
  [[ -n "${CC_PROXY:-}" ]] || return 0
  local mproxy; mproxy="$(machine_sh 'printenv HTTPS_PROXY https_proxy 2>/dev/null | head -1' | tr -d '\r')"
  if [[ "$mproxy" != "$CC_PROXY" ]]; then
    useraction "machine-egress" "the podman machine's own environment carries no proxy while CC_PROXY is set — a pull would go direct or fail. podman takes the proxy from the HOST environment at machine start, so: podman machine stop && HTTPS_PROXY=\"\$CC_PROXY\" HTTP_PROXY=\"\$CC_PROXY\" podman machine start (the value is in .env — not printed here). The 'machine' phase writes the containers.conf proxy drop-in, which covers pulls and BUILDS but not the VM's own environment."
  else
    pass "machine-egress" "the podman machine carries the host proxy"
  fi
}

# ═════════════════════════════════════════════════════════════════════════════
# COMMAND: check — everything dry, one table, exit codes as today
# ═════════════════════════════════════════════════════════════════════════════
# Design record D5 (2026-09-23). The operator's stated experience: run a
# pre-deployment check that prints plainly what it is checking and what failed,
# triage the failures with the agent, re-run, loop until green with NOTHING
# CHANGED BUT .env — then run setup, which succeeds because every input was
# already validated. This is that command.
#
#   * it EXECUTES nothing: no pull, no build, no `compose up`, no install, no
#     secret generation. `tests/test_single_check_is_dry.py` walks the call
#     graph and fails the suite if one appears;
#   * it writes nothing inside the checkout but `.env`, and only two keys
#     there: CC_STATE_DIR (resolved once, so bash and Python agree on the
#     spelling) and CC_EMBED_DIM (MEASURED from the upstream embedder — see
#     check_llm; never declared, never overwritten);
#   * it REUSES the phases rather than re-implementing their probes: the
#     answers section IS `validate`, the host section IS `preflight`, the
#     machine section IS `machine --dry-run`, the compose section IS validate's
#     compose render. A second copy of a probe is a probe that drifts;
#   * its ceiling is honest and printed: it proves INPUTS, not builds.
#
# The `all` driver runs it FIRST and refuses to continue on any FAIL or
# USERACTION (KOTS's hard preflight gate); WARN needs --accept-warnings or an
# interactive yes (rustup's rule: an installer that cannot ask does not guess).

# The section table. ONE list: `check --list` prints it, the section headings
# come from it, and tests/test_single_check_is_dry.py pins it against the table
# in deploy/single/README.md. `id<TAB>what it does`.
CHECK_SECTIONS=(
  "answers|.env present and sourceable; every questions.tsv key required by these flags set, and every set key valid; ports valid, unique and free"
  "host|podman, the compose provider, the host tools, RAM/disk, the Windows CA store"
  "machine|the podman machine's CA, registries and proxy — current state and the diff the machine phase would apply"
  "images|every images.txt row resolves against its registry, including the three build bases and operator pins"
  "indexes|PyPI, npm, the Python resolution, the apt archive, the CPython download mirror"
  "llm|the upstream endpoint FROM THIS HOST — the model list, one chat, one structured, one embedding — ONLY when .env declares it; with the catalog left to the LiteLLM UI this section says so and probes nothing"
  "integrations|Jira and Confluence FROM THIS HOST — scripts/atlassian_probe.py against the configured credentials, ONLY when .env sets CC_JIRA_BASE_URL; blank says so and probes nothing"
  "compose|compose.yaml renders with this .env, with no variable it requires left unset"
  "models|the speech models' source (Hugging Face or a pre-placed volume) and the cockpit's whisper model"
)

check_list() {
  local row
  for row in "${CHECK_SECTIONS[@]}"; do
    printf '%-9s %s\n' "${row%%|*}" "${row#*|}"
  done
}

check_section() { # check_section <id>
  local row
  for row in "${CHECK_SECTIONS[@]}"; do
    [[ "${row%%|*}" == "$1" ]] || continue
    note ""; note "== $1 — ${row#*|}"
    logline "== section $1"
    return 0
  done
  note ""; note "== $1"
}

# ── one read-only HTTP probe, classified the way discover.sh classifies ──────
# The failure MODE is the diagnosis: a timeout is a default-deny firewall, a
# certificate error is interception or an untrusted CA, refused is a firewall
# REJECT, DNS is a resolver that does not answer public names. Prints
# "<http-code> <class>"; the TLS knobs reach curl through cc_export_tls_env
# (CURL_CA_BUNDLE, and `insecure` in the state dir's .curlrc via CURL_HOME).
http_probe() { # http_probe <url> [HEAD]
  local url="$1" code rc=0 flags=(-sS -o /dev/null --max-time 20)
  [[ "${2:-}" == HEAD ]] && flags+=(-I)
  code="$(curl "${flags[@]}" -w '%{http_code}' "$url" 2>/dev/null)" || rc=$?
  case "$rc" in
    0)  printf '%s answered' "$code" ;;
    5)  printf '000 the PROXY name does not resolve (curl 5) — CC_PROXY' ;;
    6)  printf '000 DNS does not resolve this name (curl 6)' ;;
    7)  printf '000 connection refused (curl 7)' ;;
    28) printf '000 timed out after 20s — a silent drop, the classic default-deny (curl 28)' ;;
    60) printf '000 CERTIFICATE failure (curl 60) — CC_CA_BUNDLE, or CC_TLS_INSECURE=1' ;;
    *)  printf '000 unreachable (curl %s)' "$rc" ;;
  esac
}

# PASS on 2xx/3xx (a mirror redirecting is a mirror answering), FAIL otherwise,
# naming the seam that governs the source.
probe_http() { # probe_http <check> <url> <what> <seam> [HEAD]
  local out code rest
  out="$(http_probe "$2" "${5:-GET}")"; code="${out%% *}"; rest="${out#* }"
  if [[ "$code" == 2* || "$code" == 3* ]]; then
    pass "$1" "$3 answers HTTP $code"
  elif [[ "$rest" == answered ]]; then
    fail "$1" "$3 answered HTTP $code — seam: $4"
  else
    fail "$1" "$3: $rest — seam: $4"
  fi
}

# ── section: answers ────────────────────────────────────────────────────────

# The seams whose BLANK value means "the public host is contacted". If every one
# of these names a mirror, a one-certificate bundle is complete by construction:
# nothing public is dialled. One blank is enough to need the public roots too.
PUBLIC_SOURCE_SEAMS=(CC_REGISTRY_DOCKERIO CC_REGISTRY_GHCR CC_REGISTRY_MCR
                        CC_PYPI_INDEX_URL CC_PYTHON_MIRROR CC_NPM_REGISTRY
                        CC_APT_MIRROR CC_HF_ENDPOINT)

# check/ca-bundle (2026-10-01 design record, D1 — a `human` row): the PEM
# CC_CA_BUNDLE names is ON DISK. The operator places it; nothing in the
# driver can, so a missing or empty file is a USERACTION naming the key and
# the path (exit 3), never a FAIL. p_ca_bundle (with the probes, below) is
# the one definition of "placed"; this only says it, and why not. 0 = placed
# or not applicable, 1 = the USERACTION was printed.
check_ca_bundle_placed() {
  local ca why
  ca="$(q_unquote "$(get_kv "$ENV_FILE" CC_CA_BUNDLE)")"
  if [[ -z "$ca" ]]; then
    pass "ca-bundle" "CC_CA_BUNDLE is blank — the system trust store is used, so there is no bundle to place"
    return 0
  fi
  if p_ca_bundle; then
    pass "ca-bundle" "CC_CA_BUNDLE names $ca, a readable, non-empty file"
    return 0
  fi
  why="$(v_path_readable "$ca")" || true
  [[ -n "$why" ]] || why="the file is EMPTY: $(q_norm_path_answer "$ca")"
  useraction "ca-bundle" "YOUR MOVE: place the PEM bundle CC_CA_BUNDLE names in $ENV_FILE at $ca ($why) — your corporate root, plus the public roots unless every source seam names a mirror. Nothing in setup can fetch it for you; once the file is there, re-run ./setup.sh"
  return 1
}

# CC_CA_BUNDLE REPLACES the trust store — it does not add to it. That is the
# correct `cacert` semantics for curl, and the same for pip's PIP_CERT, npm's
# cafile, node's NODE_EXTRA_CA_CERTS, git and the copy installed inside a build.
# So an operator who answers with ONLY the corporate root loses pypi.org,
# registry.npmjs.org and deb.debian.org — curl exit 60, which reads like a
# broken mirror rather than a bundle that is missing the public roots. This is a
# WARN, not a FAIL: a site whose every seam is a mirror is right to carry one
# certificate, and only the operator knows which it is.
#
# The count is `BEGIN CERTIFICATE` occurrences — no openssl needed, and it reads
# the file without executing anything (check executes nothing).
check_ca_bundle_covers_everything() {
  local ca; ca="$(q_unquote "$(get_kv "$ENV_FILE" CC_CA_BUNDLE)")"
  [[ -n "$ca" ]] || return 0
  # Unreadable or empty is check/ca-bundle's USERACTION (check_ca_bundle_placed,
  # above), and check stops there; nothing to add here.
  [[ -r "$ca" ]] || return 0
  local n; n="$(grep -c 'BEGIN CERTIFICATE' "$ca" 2>/dev/null)" || n=0
  [[ "$n" =~ ^[0-9]+$ ]] || n=0
  local blank="" k
  for k in "${PUBLIC_SOURCE_SEAMS[@]}"; do
    is_placeholder "$(q_unquote "$(get_kv "$ENV_FILE" "$k")")" && blank="${blank:+$blank }$k"
  done
  if (( n <= 1 )) && [[ -n "$blank" ]]; then
    warn "answers-ca-bundle" "CC_CA_BUNDLE holds $n certificate(s), and it REPLACES the trust store rather than adding to it — while these seams are blank, so PUBLIC hosts will be contacted: $blank. A bundle with only your corporate root then fails those with curl exit 60. Make a COMBINED bundle: on Linux, cat corporate.pem /etc/ssl/certs/ca-certificates.crt > bundle.pem; on Windows, append the corporate root to a copy of curl's cacert.pem from https://curl.se/docs/caextract.html — or point every seam listed above at a mirror"
  elif (( n <= 1 )); then
    pass "answers-ca-bundle" "CC_CA_BUNDLE holds $n certificate(s) and every public source seam names a mirror, so nothing public is dialled — the bundle does not need the public roots"
  else
    pass "answers-ca-bundle" "CC_CA_BUNDLE holds $n certificates (it REPLACES the trust store, so it must carry every CA this install meets)"
  fi
}

check_required_keys() {
  local k blank=""
  for k in "${CC_GENERATED_KEYS[@]}"; do
    is_placeholder "$(get_kv "$ENV_FILE" "$k")" && blank="${blank:+$blank }$k"
  done
  if [[ -n "$blank" ]]; then
    warn "answers-secrets" "not generated yet: $blank — ./setup.sh configure generates them (so does the llm phase). check never generates a secret, so this stays a WARN: --accept-warnings is how you say 'yes, generate them'"
  else
    pass "answers-secrets" "every credential make-secrets.sh owns is set"
  fi

  check_ca_bundle_covers_everything

  # Everything else an answer file must carry is the SCHEMA's business now
  # (v2.45.0, design record D6): questions.tsv is the one list, so a key that
  # `configure` asks and `check` does not validate cannot exist. A required key
  # left blank is a USERACTION naming the key and the command that asks it; a
  # key that IS set but fails its own validator is a FAIL carrying the reason.
  check_schema_answers
}

check_ports_free() {
  # A port held by one of THIS install's containers is not a conflict — it is
  # an install that is already deployed, which is the normal state for every
  # re-run of check.
  local published=""
  command -v podman >/dev/null 2>&1 && published="$(podman ps --format '{{.Ports}}' 2>/dev/null)"
  local p val busy=0
  for p in "${CC_PORT_KEYS[@]}"; do
    val="${!p:-}"
    [[ "$val" =~ ^[0-9]+$ ]] || continue
    port_listener "$val" || continue
    if [[ "$published" == *":${val}->"* ]]; then
      pass "port-free-${p}" "$p=$val is published by a container of this install already — not a conflict"
    elif [[ "$p" == CC_API_PORT && -f "$STATE_DIR/uvicorn.pid" ]] \
      || [[ "$p" == CC_COCKPIT_PORT && -f "$STATE_DIR/cockpit.pid" ]]; then
      # `boot` starts these two as HOST processes, not containers, and records a
      # pid file in the state dir — that is how a re-run tells its own listener
      # from a foreign one. (./setup.sh stop is the counterpart.)
      pass "port-free-${p}" "$p=$val is held by the process this install started (pid file in $STATE_DIR) — ./setup.sh stop releases it"
    else
      fail "port-free-${p}" "$p=$val already has a LISTENER that is not one of this install's containers: the stack could not publish it. Stop what holds it, or change $p in .env"
      busy=1
    fi
  done
  (( busy )) || pass "ports-free" "no foreign listener on any configured port"
}

# ── section: images ─────────────────────────────────────────────────────────
# resolve-images.sh --dry-run prints this profile's protocol itself (one line
# per image, the operator pins, the substitutions) and writes nothing; only its
# exit code is folded into check's counters.
check_images() {
  local rc=0
  "$HERE/resolve-images.sh" --dry-run || rc=$?
  case "$rc" in
    0) pass "images" "every images.txt row resolves to its locked tag on its registry" ;;
    2) warn "images" "resolved, with substitutions or operator pins — see the WARN lines above; a green verify phase is what makes a substitution supported" ;;
    3) useraction "images" "image resolution stopped for you — see the USERACTION line above (the seams are CC_REGISTRY_* and a CC_IMG_<NAME> pin)" ;;
    *) fail "images" "image resolution failed (exit $rc) — the FAIL lines above name the seam per image" ;;
  esac
}

# ── section: indexes ────────────────────────────────────────────────────────
# The two package indexes are each asked for ONE SMALL PROJECT THE INSTALL
# ITSELF NEEDS — its metadata, by the same GET pip/uv and npm make, through the
# configured seam with the CA/insecure/proxy settings applied. That is what the
# probe proves: this index serves package metadata from here. The project is
# taken from the lockfiles on purpose, because a MIRROR is only obliged to hold
# what the install resolves: an offline mirror seeded from requirements.lock
# and web/package-lock.json answers for these two, and would 404 for an
# arbitrary one. The old choices were exactly that kind: `pip` and `npm` are in
# NEITHER lockfile, and `npm`'s packument is 25.8 MB — under the 20 s cap a
# slow evening FAILed the gate on a healthy registry (the same trap as the
# host section's F17, 2026-10-02 testbed run). Not HEAD: a mirror may refuse
# it, and a HEAD proves less than the metadata GET the tools actually make.
# tests/test_single_check_is_dry.py pins both names to their lockfiles.
INDEX_PROBE_PYPI_PROJECT=h11          # requirements.lock (httpcore, uvicorn)
INDEX_PROBE_NPM_PACKAGE=picocolors    # web/package-lock.json (postcss, @babel/code-frame)

check_indexes() {
  # The PyPI SIMPLE index, probed at a project page rather than the root: a
  # mirror may serve / as a portal and still resolve.
  local pypi="${CC_PYPI_INDEX_URL:-https://pypi.org/simple}"
  if [[ -z "${CC_PYPI_INDEX_URL:-}" && "$CC_AIRGAP" == "1" ]]; then
    warn "index-pypi" "CC_AIRGAP=1 and CC_PYPI_INDEX_URL is unset, so the PUBLIC index is what a resolve would use — set the mirror; the resolution check below is what decides"
  else
    probe_http "index-pypi" "${pypi%/}/${INDEX_PROBE_PYPI_PROJECT}/" "the PyPI simple index (${pypi})" "CC_PYPI_INDEX_URL"
  fi
  local npm="${CC_NPM_REGISTRY:-https://registry.npmjs.org}"
  probe_http "index-npm" "${npm%/}/${INDEX_PROBE_NPM_PACKAGE}" "the npm registry (${npm})" "CC_NPM_REGISTRY"

  # apt runs INSIDE the three image builds, and the suite comes from each base
  # image: zepai/knowledge-graph-mcp and library/python:3.12-slim-bookworm are
  # Debian BOOKWORM, mcr playwright/python:v1.62.0-noble is Ubuntu NOBLE
  # (images.txt's locked tags say so). Hardcoded here on purpose — a suite is a
  # property of the base image, not an operator answer.
  local apt="${CC_APT_MIRROR:-https://deb.debian.org/debian}"
  probe_http "index-apt" "${apt%/}/dists/bookworm/Release" \
    "the Debian bookworm archive (${apt}) — apt runs inside the graphiti and sandbox builds" "CC_APT_MIRROR"
  if [[ "$CC_ENABLE_CRAWLER" == "1" ]]; then
    probe_http "index-apt-ubuntu" "http://archive.ubuntu.com/ubuntu/dists/noble/Release" \
      "the Ubuntu noble archive — apt runs inside the CRAWLER build (its base is Microsoft's Playwright image, Ubuntu noble). CC_APT_MIRROR is a DEBIAN path and cannot stand in" "CC_ENABLE_CRAWLER=0, or an archive.ubuntu.com mirror"
  fi

  # The CPython download only matters when the host has none to find.
  if command -v uv >/dev/null 2>&1 && uv python find 3.12 >/dev/null 2>&1; then
    pass "python-3.12" "uv finds a CPython 3.12 here — no interpreter download needed"
  elif [[ -n "${CC_PYTHON_MIRROR:-}" ]]; then
    probe_http "python-3.12" "$CC_PYTHON_MIRROR" "the python-build-standalone mirror (uv must DOWNLOAD a CPython 3.12: none was found here)" "CC_PYTHON_MIRROR" HEAD
  elif [[ "$CC_AIRGAP" == "1" ]]; then
    # In the air gap that download CANNOT happen, so a WARN here is a lie the
    # operator only finds out about in the app phase, after everything else
    # installed. The work site had Python 3.14 only (2026-09-25).
    fail "python-3.12" "no CPython 3.12 on this host and CC_AIRGAP=1, so \`uv venv --python 3.12\` would have to download one from python-build-standalone (github.com) — unreachable here. Two ways out: install CPython 3.12 on this host, or set CC_PYTHON_MIRROR to a mirror of the python-build-standalone releases"
  else
    warn "python-3.12" "no CPython 3.12 on this host, so uv must download one from python-build-standalone (github.com), and CC_PYTHON_MIRROR is unset — set it, or install CPython 3.12"
  fi

  # THE Python resolution — the same step the fetch phase runs, from the same
  # function, so what check proves is what fetch performs. A resolve needs a
  # TARGET environment: the install's own .venv when it exists, otherwise a
  # throwaway one in the state directory (never inside the checkout, and never
  # the install's venv — creating that one is the fetch phase's).
  if venv_python >/dev/null; then
    export VIRTUAL_ENV="$REPO_ROOT/.venv"
    resolve_python_deps
  elif command -v uv >/dev/null 2>&1 && uv venv --python 3.12 "$STATE_DIR/check-venv" >&2; then
    export VIRTUAL_ENV="$STATE_DIR/check-venv"
    resolve_python_deps
    unset VIRTUAL_ENV
  else
    warn "python" "no .venv yet and no throwaway venv could be created in $STATE_DIR — the Python graph cannot be resolved until then; seams: CC_PYTHON_MIRROR, CC_PYPI_INDEX_URL"
  fi
}

# ── section: llm ────────────────────────────────────────────────────────────
# The upstream, probed DIRECTLY from this host with curl, before any container
# exists — that is what lets an LLM misconfiguration fail before setup rather
# than during it. The key travels via `-H @-` (stdin), so it is not in an argv
# and never in `ps`; no probe prints it.
upstream_curl() { # upstream_curl <curl args...>
  printf 'Authorization: Bearer %s\n' "${CC_LLM_UPSTREAM_API_KEY:-}" \
    | curl -sS --max-time "${CC_PROBE_TIMEOUT:-60}" -H @- "$@"
}

# One probe through discover-llm.sh in DIRECT mode — the same script the llm
# phase runs against the proxy, so the two rungs ask the identical question and
# a difference between them is the diagnosis (direct works + proxy fails = the
# alias row; direct fails = the URL, the key or the model id).
upstream_probe() { # upstream_probe <check> <message> <mode> <model-id> [extra...]
  local check="$1" msg="$2"; shift 2
  note "--> discover-llm.sh $1 $2 (direct, against CC_LLM_UPSTREAM_BASE_URL)"
  if CC_LLM_BASE_URL="$CC_LLM_UPSTREAM_BASE_URL" CC_LLM_API_KEY="$CC_LLM_UPSTREAM_API_KEY" \
     CC_EMBED_BASE_URL="$CC_LLM_UPSTREAM_BASE_URL" CC_EMBED_API_KEY="$CC_LLM_UPSTREAM_API_KEY" \
     "$HERE/discover-llm.sh" "$@" >&2; then
    pass "$check" "$msg"
    return 0
  fi
  fail "$check" "$msg — FAILED; see the command's own output on stderr"
  return 1
}

check_llm() {
  local a key
  # THE PRIMARY METHODOLOGY IS THE UI (v2.45.1). The operator's own practice —
  # and what the k3s profile has always done — is to enter the provider details
  # in the LiteLLM UI at the llm phase's pause: LiteLLM handles provider nuance
  # (credentials, per-provider parameters, routing) that a flat answer file
  # cannot, and one method across both profiles beats two. So a blank catalog is
  # a PASS with the consequence stated, never a USERACTION: there is nothing for
  # the operator to fix here, only a pause to expect later.
  if [[ -z "${CC_LLM_UPSTREAM_BASE_URL:-}" ]]; then
    pass "llm" "catalog will be entered in the LiteLLM UI — setup pauses at the llm phase (exit 3) until the aliases answer. Nothing about the LLM is proven before that pause, which is the trade for entering the provider where LiteLLM can express it. The optional shortcut past the pause is CC_LLM_UPSTREAM_BASE_URL + CC_LLM_UPSTREAM_API_KEY + one CC_LLM_UPSTREAM_MODEL_<ALIAS> per alias ($(cc_required_aliases)); with those set this section probes the endpoint from here instead"
    return 0
  fi
  if [[ -z "${CC_LLM_UPSTREAM_API_KEY:-}" ]]; then
    fail "llm-upstream" "CC_LLM_UPSTREAM_BASE_URL is set but CC_LLM_UPSTREAM_API_KEY is not — a declared upstream needs both (use none if the server ignores keys; LiteLLM needs something to send). Clear the base URL to go back to entering the catalog in the LiteLLM UI"
    return 0
  fi
  local base="${CC_LLM_UPSTREAM_BASE_URL%/}"

  # What the endpoint SAYS it serves. Some gateways do not implement /models at
  # all; that is a WARN, because it costs the membership check and nothing else.
  local out code listed="" have_list=0
  out="$STATE_DIR/check-models.json"
  code="$(upstream_curl -o "$out" -w '%{http_code}' "${base}/models" 2>/dev/null)" || code=000
  if [[ "$code" == 200 ]]; then
    listed="$($PY -c 'import json,sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
print("\n".join(str(m.get("id","")) for m in (d.get("data") or [])))' "$out" 2>/dev/null)"
    have_list=1
    pass "llm-models" "GET ${base}/models lists $(grep -c . <<<"$listed") model id(s)"
  elif [[ "$code" == 404 ]]; then
    warn "llm-models" "GET ${base}/models answers 404 — some gateways do not implement the model list, so MEMBERSHIP of your CC_LLM_UPSTREAM_MODEL_* ids could not be checked. The round trips below are then the whole proof"
  else
    fail "llm-models" "GET ${base}/models answered HTTP ${code} — seams: CC_LLM_UPSTREAM_BASE_URL (is it the /v1 base?), CC_LLM_UPSTREAM_API_KEY, CC_PROXY, CC_CA_BUNDLE"
  fi
  rm -f "$out"

  # Membership, per declared id.
  local id ids="" missing=""
  for a in $(cc_required_aliases); do
    key="$(cc_alias_env_key "$a")"; id="${!key:-}"
    [[ -n "$id" ]] || { warn "llm-declared-${a}" "$key is unset — this alias cannot be checked (the llm phase will pause for it in the LiteLLM UI)"; continue; }
    ids="${ids:+$ids }${a}=${id}"
    (( have_list )) || continue
    if grep -qxF "$id" <<<"$listed"; then
      pass "llm-member-${a}" "$key=$id is in the endpoint's model list"
    else
      missing="${missing:+$missing }${key}=${id}"
    fi
  done
  [[ -z "$missing" ]] || fail "llm-members" "declared model id(s) the endpoint does not list: $missing — fix the id, or the endpoint"

  # One CHAT round trip per DISTINCT chat model id (three aliases commonly name
  # one model; a second identical call proves nothing and costs tokens).
  local seen="" cid
  for a in cc-default graphiti-llm gpt-4.1-nano; do
    key="$(cc_alias_env_key "$a")"; cid="${!key:-}"
    [[ -n "$cid" ]] || continue
    [[ " $seen " == *" $cid "* ]] && { pass "llm-chat-${a}" "same model id as an alias already probed ($cid) — one round trip covers both"; continue; }
    seen="${seen:+$seen }$cid"
    upstream_probe "llm-chat-${a}" "a real completion came back from $cid (the $a upstream)" chat "$cid"
  done

  # The STRUCTURED round trip, for graphiti-llm's id only: Graphiti's MCP server
  # drives extraction through chat/completions with a json_schema
  # response_format, and an endpoint that ignores the schema is the one failure
  # a plain chat probe cannot see.
  key="$(cc_alias_env_key graphiti-llm)"; cid="${!key:-}"
  [[ -z "$cid" ]] || upstream_probe "llm-structured" "$cid returned schema-constrained JSON (what Graphiti needs)" structured "$cid"

  # The EMBEDDING round trip, which is also THE measurement of CC_EMBED_DIM.
  key="$(cc_alias_env_key cc-embedding)"; cid="${!key:-}"
  if [[ -n "$cid" ]]; then
    local dim
    note "--> discover-llm.sh embed $cid (direct, against CC_LLM_UPSTREAM_BASE_URL)"
    dim="$(CC_LLM_BASE_URL="$CC_LLM_UPSTREAM_BASE_URL" CC_LLM_API_KEY="$CC_LLM_UPSTREAM_API_KEY" \
           CC_EMBED_BASE_URL="$CC_LLM_UPSTREAM_BASE_URL" CC_EMBED_API_KEY="$CC_LLM_UPSTREAM_API_KEY" \
           "$HERE/discover-llm.sh" embed "$cid" | tail -1)"
    if [[ ! "$dim" =~ ^[0-9]+$ ]]; then
      fail "llm-embed" "$cid did not return a vector — see stderr"
    else
      pass "llm-embed" "$cid returned a ${dim}-dimension vector"
      # MEASURED, never declared, and never overwritten: the dimension is
      # written into the Neo4j vector index and is permanent once that index
      # exists. Writing it here is inside check's "may update .env" allowance —
      # the llm phase re-measures it through the proxy alias and FAILS on a
      # mismatch, which is what catches a changed embedder.
      local cur; cur="$(get_kv "$ENV_FILE" CC_EMBED_DIM)"
      if [[ -z "$cur" ]]; then
        set_kv "$ENV_FILE" CC_EMBED_DIM "$dim"
        pass "llm-embed-dim" "CC_EMBED_DIM=${dim} recorded in .env (check MAY update .env — this key and CC_STATE_DIR are the only two it writes)"
      elif [[ "$cur" == "$dim" ]]; then
        pass "llm-embed-dim" "CC_EMBED_DIM=${cur} in .env matches what the endpoint returns"
      else
        fail "llm-embed-dim" "CC_EMBED_DIM is already ${cur} in .env but ${cid} returns ${dim} — REFUSING to change it: it is written into the Neo4j vector index, and changing it means dropping the index and re-embedding the graph. Fix the model, or clear CC_EMBED_DIM deliberately on a graph you are willing to lose"
      fi
    fi
  fi

  # Speech: MEMBERSHIP only. An audio round trip from the host would prove the
  # upstream, not the deployment — the bundled engine is what usually serves
  # these two aliases, and it does not exist yet at check time.
  for a in cc-tts cc-stt; do
    key="$(cc_alias_env_key "$a")"; cid="${!key:-}"
    [[ -n "$cid" ]] || continue
    pass "llm-speech-${a}" "$key=$cid declared (membership checked above; no audio round trip from the host — the llm phase probes these through the proxy)"
  done
}

# ── section: integrations ───────────────────────────────────────────────────
# Jira and Confluence, PROVEN FROM THIS HOST before the UI exists (v2.53.0).
# Until this section existed, nothing in the installers or the onboarding ever
# asked for these credentials or tested them: the operator of the reference
# deployment discovered mid-tour that the team had no Jira configured, against a
# Confluence token nobody had ever exercised. The answers now come from
# questions.tsv and the proof comes from scripts/atlassian_probe.py, which is the
# one walk of every endpoint each flavor uses — never a second probe here.
#
# It EXECUTES a read-only probe, which is what the llm section does too: check
# proves INPUTS, and a credential's only proof is a round trip. It writes one
# file, into the state dir.
check_integrations() {
  if [[ -z "${CC_JIRA_BASE_URL:-}" ]]; then
    pass "integrations" "no Jira configured (CC_JIRA_BASE_URL is blank) — that is a valid answer, and the consequence is stated rather than probed: agents holding a jira or confluence capability FAIL at execution with \"Jira is not configured\", and the jira-expert's and confluence-expert's introductions fail with them. To enable it, set CC_JIRA_BASE_URL + CC_JIRA_EMAIL + CC_JIRA_API_TOKEN (and the CC_CONFLUENCE_* set for the wiki), then re-run this check"
    return 0
  fi
  if [[ -z "${CC_JIRA_API_TOKEN:-}" ]]; then
    fail "integrations-answers" "CC_JIRA_BASE_URL is set but CC_JIRA_API_TOKEN is not — every Jira read needs the token (Cloud: an API token from id.atlassian.com; Data Center: a personal access token). Clear CC_JIRA_BASE_URL to go back to no Jira"
    return 0
  fi
  if [[ -z "${CC_JIRA_EMAIL:-}" && "${CC_JIRA_API_FLAVOR:-cloud}" == "cloud" \
        && "${CC_JIRA_AUTH_MODE:-}" != "bearer" ]]; then
    fail "integrations-answers" "CC_JIRA_EMAIL is blank under flavor cloud — Cloud authenticates with Basic (email + API token). Set the email, or set CC_JIRA_API_FLAVOR=server / CC_JIRA_AUTH_MODE=bearer if the token is a Data Center PAT"
    return 0
  fi

  # The probe imports central_command and httpx, so on a FRESH host — where the
  # venv does not exist yet and $PY is the bare interpreter or the uv fallback —
  # it cannot run. That is not a failure of the ANSWERS: the verify phase probes
  # the same credentials once the app phase has built the venv.
  if ! ( cd "$REPO_ROOT" && $PY -c 'import httpx, central_command.config' ) >/dev/null 2>&1; then
    warn "integrations-probe" "the live Jira/Confluence probe is DEFERRED to the verify phase: this host's python ($PY) cannot import httpx + central_command yet, which is normal before the app phase builds .venv. The answers are present; nothing about them is proven here"
    return 0
  fi

  local out="$STATE_DIR/check-atlassian.txt" rc=0
  note "--> scripts/atlassian_probe.py --quiet (read-only, against CC_JIRA_BASE_URL)"
  ( cd "$REPO_ROOT" && $PY scripts/atlassian_probe.py --quiet ) >"$out" 2>&1 || rc=$?
  local counts line
  counts="$(grep -E '^[0-9]+ checks, [0-9]+ failed' "$out" | tail -1)"
  if (( rc == 0 )); then
    pass "integrations-probe" "${counts:-the probe passed} — every endpoint the configured flavors use answered (full output: $out)"
    return 0
  fi
  # The FAIL lines are reprinted VERBATIM: the probe scrubs every token and
  # every email address out of its own output by construction, so its reasons
  # are the reasons, and paraphrasing them would lose the status code. BOUNDED at
  # six: an unreachable host fails every endpoint (a transport line plus a
  # no-response line each), and eighteen identical DNS errors bury the rest of
  # the report instead of explaining it. The file has all of them.
  local shown=0 total
  total="$(grep -c '^FAIL ' "$out")"
  while IFS= read -r line; do
    (( shown < 6 )) || break
    shown=$((shown+1))
    fail "integrations-probe" "$line"
  done < <(grep '^FAIL ' "$out")
  (( total > shown )) && fail "integrations-probe" "...and $(( total - shown )) more FAIL line(s) — the whole report is in $out"
  grep -q '^FAIL ' "$out" || fail "integrations-probe" "scripts/atlassian_probe.py exited $rc with no FAIL line — see $out"
  note "${counts:-}"
  note "seams: CC_JIRA_BASE_URL, CC_JIRA_EMAIL, CC_JIRA_API_TOKEN, CC_JIRA_API_FLAVOR, CC_JIRA_AUTH_MODE, the CC_CONFLUENCE_* set, CC_PROXY, CC_CA_BUNDLE"
  return 0
}

# ── section: models ─────────────────────────────────────────────────────────
check_models() {
  if [[ "$CC_ENABLE_SPEECH" != "1" ]]; then
    pass "speech-models" "skipped (CC_ENABLE_SPEECH=0 — cc-tts/cc-stt point at engines of your own)"
  else
    local hf="${CC_HF_ENDPOINT:-https://huggingface.co}" m n=0
    # The pre-placed alternative: a snapshot already in the engine's volume.
    # Its CONTENTS cannot be read without starting a container, and check starts
    # nothing — so a present volume downgrades a missing hub to a WARN.
    local vol="central-command_speech-models" have_vol=0
    command -v podman >/dev/null 2>&1 && podman volume exists "$vol" 2>/dev/null && have_vol=1
    for m in "$CC_SPEECH_TTS_MODEL" "$CC_SPEECH_STT_MODEL"; do
      n=$((n+1))
      local out code
      out="$(http_probe "${hf%/}/api/models/${m}")"; code="${out%% *}"
      if [[ "$code" == 2* || "$code" == 3* ]]; then
        pass "speech-model-${n}" "${m} is on the hub at ${hf} (HTTP $code)"
      elif (( have_vol )); then
        warn "speech-model-${n}" "${m} is NOT reachable at ${hf} (${out#* }), but the ${vol} volume exists — check cannot verify its contents without starting a container, so this may be a pre-placed snapshot. Seams: CC_HF_ENDPOINT, or CC_ENABLE_SPEECH=0"
      else
        fail "speech-model-${n}" "${m} is not reachable at ${hf} (${out#* }) and there is no ${vol} volume holding a pre-placed snapshot — seams: CC_HF_ENDPOINT, pre-place the snapshot, or CC_ENABLE_SPEECH=0. huggingface_hub has NO insecure switch: an intercepted TLS path needs CC_CA_BUNDLE"
      fi
    done
  fi

  # The cockpit's LOCAL whisper engine: unused once cc-stt is registered, so
  # nothing here is a FAIL — it is a source the operator may or may not need.
  local wdir="${WHISPER_MODEL_DIR:-$HOME/.nerve/models}"
  if compgen -G "$wdir/*.bin" >/dev/null 2>&1; then
    pass "whisper-local" "$wdir holds a ggml .bin — the cockpit's local whisper engine needs no download"
  elif [[ -n "${WHISPER_MODELS_BASE_URL:-}" ]]; then
    probe_http "whisper-local" "$WHISPER_MODELS_BASE_URL" "the whisper model source for the cockpit's local engine" "WHISPER_MODELS_BASE_URL" HEAD
  else
    pass "whisper-local" "no ggml .bin in $wdir and no WHISPER_MODELS_BASE_URL — not needed while cc-stt serves the cockpit's voice input (it is the fallback engine)"
  fi
}

check_summary() {
  note ""
  printf 'CHECK: %d pass, %d warn, %d fail, %d action\n' "$PASSES" "$WARNS" "$FAILS" "$ACTIONS"
  printf 'NOTE: check proves inputs, not builds: a local image build can still fail inside the build, and the proxy alias probes run in the llm phase\n'
  printf 'state dir: %s\n' "$STATE_DIR"
  logline "CHECK: $PASSES pass, $WARNS warn, $FAILS fail, $ACTIONS action"
}

phase_check() {
  check_section answers
  if ! validate_answers; then check_summary; return 1; fi
  check_required_keys
  check_ports_free
  # check/ca-bundle, the operator's move (D1/D2: a `human` row whose probe is
  # false is a GATE). Every section after this one dials TLS through
  # CC_CA_BUNDLE — load_env's cc_export_tls_env made it the trust store of
  # curl, uv, pip, npm, node and git — so with the file absent each would FAIL
  # on a certificate file that is not there and name the wrong thing. The
  # USERACTION names the file; the rest of check runs once it is placed.
  if ! check_ca_bundle_placed; then check_summary; return 3; fi

  check_section host
  preflight_host

  check_section machine
  machine_report

  check_section images
  check_images

  check_section indexes
  check_indexes

  check_section llm
  check_llm

  check_section integrations
  check_integrations

  check_section compose
  validate_compose_config

  check_section models
  check_models

  check_summary
}

# check/linger (D6). What `loginctl show-user` says, judged by supervise-lib.sh's
# cc_linger_verdict (PASS / FAIL / NA, pure and tested). Read-only: `show-user`
# changes nothing — `enable-linger` is the operator's command, named in the FAIL.
linger_verdict() {
  local user out rc=0
  user="${USER:-$(id -un 2>/dev/null)}"
  if ! command -v loginctl >/dev/null 2>&1; then
    printf 'NA no loginctl here (no logind) — not applicable'
    return 0
  fi
  out="$(loginctl show-user "$user" --property=Linger 2>&1)" || rc=$?
  cc_linger_verdict "$rc" "$out" "$user"
}

p_linger() {
  [[ "$(linger_verdict)" != FAIL\ * ]]
}

# check/ca-bundle (D1, a `human` row). Unset is not applicable, so done. Set,
# the file must be what CC_CA_BUNDLE's question used to demand of the answer —
# questions-lib.sh's v_path_readable, reused rather than restated — and
# non-empty: the bundle REPLACES the trust store (cc_export_tls_env), and an
# empty one leaves every TLS call trusting nothing.
p_ca_bundle() {
  local ca
  ca="$(q_unquote "$(get_kv "$ENV_FILE" CC_CA_BUNDLE)")"
  [[ -n "$ca" ]] || return 0
  v_path_readable "$ca" >/dev/null || return 1
  [[ -s "$(q_norm_path_answer "$ca")" ]]
}
