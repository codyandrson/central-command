#!/usr/bin/env bash
# ============================================================================
# setup.sh — the deterministic driver for the MULTI-NODE k3s install.
#
#   Design record: docs/superpowers/specs/2026-08-27-setup-update-contract.md
#   ("k3s substrate (Phase B)"). Sibling of deploy/single/setup.sh, same shape,
#   same protocol; the substrate is the only difference.
#
#   The rule it exists to enforce: an AGENT elicits answers, diagnoses a
#   failure, and conducts the interview. EVERYTHING that mutates anything is
#   this script. If a conductor is composing a command, it is off the rails.
#
#   This file ORCHESTRATES; it does not reimplement. Every real operation
#   already lives next to it (init-env.sh, make-secrets.sh, mint-keys.sh,
#   build-*-image.sh, make-*-kubeconfig.sh, install-gvisor.sh, verify.sh, and
#   deploy/pi/litellm/{register-models,policy}.py) and is CALLED, not copied.
#   README.md stays the prose runbook; this mechanizes its §1–§6 and §9.
#
#   PHASES — each a subcommand, each individually re-runnable via the SAME
#   code path as the full run:
#
#     validate    offline check of both .env files. No side effects.
#     preflight   named checks of the CLUSTER and both hosts. Read-only.
#     llm         secrets + the LiteLLM trio + register/policy/mint + probes
#                 THROUGH the proxy (README §1's "absent" fork, made code)
#     stack       build the three local images if missing, apply every
#                 manifest, mint the kubeconfigs, gVisor
#     app         venv, editable install, root .env, web/.env, cockpit build,
#                 systemd units — ends at the first-boot USER-ACTION gate
#     verify      verify.sh (+ --clean-install passthrough) + README §9 smokes
#
#     status      re-run postconditions only, nothing mutating
#     diagnose    write setup-diagnostics.txt for pasting to Claude
#
#   No argument = all six phases in order, stopping at the first hard failure
#   or gate. There is no state file: every step is idempotent, so RESUME IS
#   RE-RUN.
#
#   OUTPUT PROTOCOL (identical to the single-node driver):
#     stdout   one line per check: `PASS|WARN|FAIL|USERACTION <check>: <msg>`
#     stderr   everything else — subprocess output, detail, progress
#     exit 0   clean · 1 hard failure · 2 completed with warnings
#              · 3 stopped for USER ACTION (the operator's move, not an error)
#     log      every check line is also appended, timestamped, to
#              deploy/k3s/setup-log.txt — the durable history a re-run (or a
#              diagnosing agent) reads first
#
#   Secret VALUES are never printed. Keys are referred to by NAME.
# ============================================================================

# NOT -e: a phase's checks must all report, and hard aborts are explicit
# (`|| return 1`) so the reason is always a FAIL line rather than a silent
# exit. -u and pipefail still hold.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
ENV_FILE="$REPO_ROOT/deploy/pi/.env"     # the CONTAINERS' env (make-secrets.sh)
APP_ENV="$REPO_ROOT/.env"                # the APP's env (CC_*)
NS=central-command

# k3s hides kubectl behind `k3s kubectl` and its kubeconfig is root-owned —
# same array idiom as verify.sh and mint-keys.sh. Never a bare `kubectl`.
K=(sudo k3s kubectl -n "$NS")
KROOT=(sudo k3s kubectl)

# BatchMode: a passwordless-ssh check that PROMPTS is a check that hangs.
SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes)

# Every deployment the manifests declare. rollout status on each is the honest
# "the stack is up" assertion — `get pods` reports a CrashLooping pod as
# Running until the probe flips it.
DEPLOYMENTS=(cc-postgres cc-litellm-db cc-litellm-redis cc-litellm
             cc-neo4j cc-graphiti cc-n8n-db cc-n8n cc-crawler cc-vlogs)

PY=python3

# ── output protocol ─────────────────────────────────────────────────────────
# Exit taxonomy (2026-08-27 contract): 0 clean · 1 hard failure · 2 warnings ·
# 3 USER ACTION REQUIRED — the run stopped deliberately for the operator; the
# last USERACTION line says what for. Every phase is idempotent, so re-running
# after acting always converges.
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

# Poll until an HTTP endpoint answers. Never a bare sleep: LiteLLM runs its
# Prisma migrations at boot, so a fixed sleep is either a race or dead time.
wait_http() { # wait_http <url> <seconds>
  local deadline=$(( SECONDS + $2 ))
  until curl -fsS -m 5 "$1" >/dev/null 2>&1; do
    (( SECONDS < deadline )) || return 1
    sleep 3
  done
}

# ── .env helpers (same three as the single-node driver) ─────────────────────
load_env() {
  [[ -f "$ENV_FILE" ]] || { fail "answer-file" "$ENV_FILE not found — run: ./deploy/k3s/init-env.sh"; return 1; }
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  : "${CC_COMPUTE_SSH:=chromebox_admin@100.113.118.28}"
  : "${CC_AIRGAP:=0}"
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

# A value is "unset" for our purposes if it is empty or still a placeholder.
# CHANGEME is .env.example's marker; PENDING is init-env.sh's.
is_placeholder() { [[ -z "$1" || "$1" == *CHANGEME* || "$1" == PENDING ]]; }

# Set only when the current value is empty/placeholder — the operator's own
# edits are never overwritten. This is what makes the app phase re-runnable.
set_kv_if_unset() { # set_kv_if_unset <file> <key> <value> <check-name>
  local cur; cur="$(get_kv "$1" "$2")"
  if [[ -z "$3" ]] && is_placeholder "$cur"; then
    warn "$4" "$2 has no value to copy — the source variable is empty in deploy/pi/.env"
    return 0
  fi
  if is_placeholder "$cur"; then
    set_kv "$1" "$2" "$3" && pass "$4" "$2 set in the app's .env"
  else
    pass "$4" "$2 already set — left alone"
  fi
}

# The app's .env must exist before mint-keys.sh runs — it writes CC_LLM_API_KEY
# INTO it and FATALs if the file is absent (mint-keys.sh:42). That is why this
# lives here and is called from the llm phase, not only from app.
#
# CC_EXECUTOR_MODE is forced to dry_run on a file we just created: .env.example
# ships `live`, and a fresh install must not reach the outside world before the
# operator says so. An existing file is never touched — a deliberate `live`
# survives every re-run.
ensure_app_env() {
  if [[ -f "$APP_ENV" ]]; then
    pass "app-env" "the app's .env already exists — only empty values will be filled"
    return 0
  fi
  cp "$REPO_ROOT/.env.example" "$APP_ENV" || { fail "app-env" "could not create $APP_ENV"; return 1; }
  chmod 600 "$APP_ENV"
  set_kv "$APP_ENV" CC_EXECUTOR_MODE dry_run
  pass "app-env" "created the app's .env from .env.example (0600), CC_EXECUTOR_MODE=dry_run"
}

# ssh to the compute node, non-interactively. Used read-only everywhere in
# preflight; the build scripts do their own ssh.
compute_ssh() { ssh "${SSH_OPTS[@]}" "$CC_COMPUTE_SSH" "$@"; }

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: validate — offline. Are both answer files answerable-from?
# ─────────────────────────────────────────────────────────────────────────────
phase_validate() {
  load_env || return 1
  pass "answer-file" "$ENV_FILE present"

  # The GENERATED block (deploy/pi/.env.example). init-env.sh fills every one
  # of these; an empty one means it was never run, or was edited since.
  local v
  for v in NEO4J_PASSWORD LITELLM_MASTER_KEY LITELLM_SALT_KEY \
           LITELLM_POSTGRES_PASSWORD N8N_ENCRYPTION_KEY N8N_DB_PASSWORD; do
    if [[ -z "${!v:-}" ]]; then
      fail "answer-${v}" "$v is empty in deploy/pi/.env — run: ./deploy/k3s/init-env.sh"
    elif [[ "${!v}" == PENDING ]]; then
      fail "answer-${v}" "$v is PENDING — only the three Graphiti virtual-key slots may hold that"
    else
      pass "answer-${v}" "$v is set"
    fi
  done

  # The three Graphiti slots are the ONLY legal home for PENDING: they are
  # LiteLLM virtual keys, which cannot exist before the proxy does. make-
  # secrets.sh accepts the placeholder; mint-keys.sh replaces it in the llm
  # phase. PENDING here is therefore a NOTE, not a warning — it is the
  # designed pre-mint state.
  local pending=""
  for v in GRAPHITI_LLM_API_KEY EMBEDDER_API_KEY RERANKER_API_KEY; do
    if [[ -z "${!v:-}" ]]; then
      fail "answer-${v}" "$v is empty — run ./deploy/k3s/init-env.sh (it writes PENDING to break the chicken-and-egg)"
    elif [[ "${!v}" == PENDING ]]; then
      pending+=" $v"
    else
      pass "answer-${v}" "$v is minted"
    fi
  done
  [[ -n "$pending" ]] && pass "graphiti-keys-pending" "still PENDING (expected before the llm phase mints them):$pending"

  # The app's .env. validate REPORTS ONLY — it mutates nothing, so the fix is
  # printed rather than performed (the llm phase creates it, for mint-keys.sh).
  if [[ -f "$APP_ENV" ]]; then
    pass "app-env" "the app's .env is present"
  else
    warn "app-env" "the repo-root .env is missing — the llm phase creates it (or: cp .env.example .env && chmod 600 .env)"
  fi

  [[ -f "$REPO_ROOT/central_command/db/schema.sql" ]] \
    && pass "repo-layout" "schema.sql found — running inside the repo" \
    || fail "repo-layout" "central_command/db/schema.sql not found — is this the Central Command repo?"
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: preflight — is this CLUSTER able to run the install? Read-only.
# ─────────────────────────────────────────────────────────────────────────────
phase_preflight() {
  load_env || return 1

  if ! command -v k3s >/dev/null 2>&1; then
    fail "k3s" "k3s not found on PATH — this profile assumes an installed cluster (README §1)"
    return 1
  fi
  pass "k3s" "$(k3s --version 2>/dev/null | head -1)"

  # The unit files hardcode the checkout path, and the app phase installs
  # them verbatim — so a driver run from a DIFFERENT checkout builds the venv
  # and writes the .env here while systemd starts uvicorn THERE (2026-08-29:
  # a stale sibling checkout's .env had the feed enabled and a PENDING key;
  # the API polled real Gmail and 401'd for twenty minutes). Every absolute
  # path in every unit we install must live under REPO_ROOT (Environment= is
  # exempt: the minted kubeconfigs live in $HOME by design).
  local unit stray
  stray=""
  for unit in "$HERE"/cc-*.service "$HERE"/cc-*.path "$HERE"/cc-*.timer "$REPO_ROOT/deploy/pi/cc-nerve.service"; do
    if grep -E '^(WorkingDirectory|ExecStart|ExecStartPre|EnvironmentFile)=' "$unit" 2>/dev/null \
       | grep -oE '/home/[^ :"]+' | grep -vq "^$REPO_ROOT"; then
      stray="$stray $(basename "$unit")"
    fi
  done
  if [[ -n "$stray" ]]; then
    fail "unit-paths" "unit files point outside this checkout ($REPO_ROOT):$stray — fix the hardcoded paths before installing them"
    return 1
  fi
  pass "unit-paths" "every unit file's paths live under $REPO_ROOT"

  # Both nodes Ready. Capture then inspect — a `get nodes | grep` pipeline
  # inverts under pipefail when kubectl takes SIGPIPE.
  local nodes notready
  nodes="$("${KROOT[@]}" get nodes --no-headers 2>&1)"
  if [[ -z "$nodes" || "$nodes" == *"error"* || "$nodes" == *"refused"* ]]; then
    fail "nodes-ready" "cannot reach the cluster: $nodes"
    return 1
  fi
  notready="$(awk '$2 != "Ready" {print $1"="$2}' <<<"$nodes")"
  if [[ -n "$notready" ]]; then
    fail "nodes-ready" "not Ready: $notready"
  else
    pass "nodes-ready" "$(awk '{printf "%s ", $1}' <<<"$nodes")— all Ready"
  fi

  # Role labels, not hostnames (2026-08-27 contract). Exactly one node each:
  # zero leaves every pinned pod Pending with no event naming the cause, and
  # two makes placement non-deterministic (verify.sh takes items[0]).
  local role n count
  for role in anchor compute; do
    n="$("${KROOT[@]}" get nodes -l "cc-role/${role}=true" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)"
    count="$(wc -w <<<"$n")"
    if (( count == 1 )); then
      pass "role-${role}" "cc-role/${role} -> ${n}"
    else
      fail "role-${role}" "cc-role/${role} resolves to ${count} nodes ('${n}') — label exactly one: sudo k3s kubectl label node <name> cc-role/${role}=true"
    fi
  done

  # Passwordless ssh to the compute node: every build script, install-gvisor.sh
  # and verify.sh shell over it. BatchMode turns "would prompt" into a failure
  # instead of a hang.
  if compute_ssh true 2>/dev/null; then
    pass "compute-ssh" "passwordless ssh to $CC_COMPUTE_SSH"
  else
    fail "compute-ssh" "cannot ssh to $CC_COMPUTE_SSH without a prompt — fix the key, or set CC_COMPUTE_SSH in deploy/pi/.env"
    return 1
  fi

  # Two builders, one per architecture, per README §1 and §3: docker builds the
  # arm64 graphiti image HERE; podman builds every amd64 image THERE. Neither
  # is optional — the graphiti image must exist on both nodes or a failover
  # ImagePullBackOffs.
  command -v docker >/dev/null 2>&1 \
    && pass "builder-docker" "docker present on this node (arm64 graphiti build)" \
    || fail "builder-docker" "docker not found — build-graphiti-image.sh builds the arm64 half with it (README §1)"
  if compute_ssh 'command -v podman' >/dev/null 2>&1; then
    pass "builder-podman" "podman present on the compute node (native amd64 builds, no QEMU)"
  else
    fail "builder-podman" "podman not found on $CC_COMPUTE_SSH — the amd64 builds are native there"
  fi

  local t
  for t in curl openssl uv git ssh scp; do
    command -v "$t" >/dev/null 2>&1 \
      && pass "tool-${t}" "present" \
      || fail "tool-${t}" "$t not found on PATH"
  done

  # Debian 12 ships 3.11; the toolchain is uv-managed CPython 3.12. `uv python
  # find` only LOOKS — it never installs.
  if uv python find 3.12 >/dev/null 2>&1; then
    pass "python-3.12" "uv can resolve CPython 3.12"
  else
    fail "python-3.12" "no CPython 3.12 available to uv — run: uv python install 3.12"
  fi
  # register-models.py / policy.py run under the SYSTEM python3 (they are called
  # before the venv exists) and both import yaml.
  if $PY -c 'import yaml' 2>/dev/null; then
    pass "python-yaml" "system python3 has PyYAML (register-models.py / policy.py need it)"
  else
    fail "python-yaml" "system python3 cannot import yaml — apt install python3-yaml"
  fi

  if command -v node >/dev/null 2>&1; then
    local nv; nv="$(node -v 2>/dev/null)"; nv="${nv#v}"
    if [[ "${nv%%.*}" =~ ^[0-9]+$ ]] && (( ${nv%%.*} >= 22 )); then
      pass "node-version" "node v$nv"
    else
      warn "node-version" "node v$nv is older than 22 — the cockpit build will be skipped (the API still runs)"
    fi
  else
    warn "node-version" "node not found — the cockpit build will be skipped (the API still runs)"
  fi

  # gVisor's HOST half. The RuntimeClass applies unconditionally, so a missing
  # runsc stays green everywhere until the first sandbox Job hangs on "failed
  # to create shim" — weeks later, reading like random infra breakage. Same
  # assertion verify.sh makes, made BEFORE the install instead of after.
  local cc_ok
  cc_ok="$(compute_ssh 'test -x /usr/local/bin/runsc \
      && sudo grep -q "runtimes\.runsc" /var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.tmpl \
      && echo yes' 2>/dev/null)"
  if [[ "$cc_ok" == "yes" ]]; then
    pass "gvisor-host" "runsc installed and registered on the compute node"
  else
    fail "gvisor-host" "runsc missing/unregistered on the compute node — run: ./deploy/k3s/install-gvisor.sh"
  fi

  # Air-gap probe: INFORMATIONAL. Unreachable indexes are a fact about the
  # network, and CC_AIRGAP is how the operator says it is deliberate.
  local u reach=1
  for u in https://pypi.org/simple/ https://registry.npmjs.org/; do
    curl -fsS -m 10 -o /dev/null "$u" 2>/dev/null || reach=0
  done
  if (( reach )); then
    pass "package-indexes" "pypi and npm are reachable"
  elif [[ "$CC_AIRGAP" == "1" ]]; then
    pass "package-indexes" "unreachable, as expected with CC_AIRGAP=1 (installs come from requirements.lock)"
  else
    warn "package-indexes" "pypi and/or npm unreachable — set CC_AIRGAP=1 and see deploy/AIRGAP.md (and registries.yaml.example for images)"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: llm — LiteLLM first; everything after is proven through its aliases.
# ─────────────────────────────────────────────────────────────────────────────
# The USER-ACTION gate for a probe failure. The proxy is alive at this point,
# so the operator can act in its UI; this prints the where and the what.
# Key VALUES are never printed — names only, per the output protocol.
llm_gate() { # llm_gate <what-failed>
  useraction "llm-models" "$1 — operator action needed; see the instructions on stderr, then re-run: ./deploy/k3s/setup.sh llm"
  note ""
  note "== LiteLLM needs your attention =="
  note "The proxy is UP but a model alias is not answering. Fix it yourself —"
  note "no agent should register or edit models on your behalf."
  note ""
  note "  UI:           http://127.0.0.1:4000/ui"
  note "  login:        username 'admin', password = LITELLM_MASTER_KEY"
  note "                (or UI_USERNAME/UI_PASSWORD if set in .env)"
  note "                (the value is in deploy/pi/.env — not printed here)"
  note ""
  note "  The catalog is DB-stored (store_model_in_db), and its declaration is"
  note "  deploy/pi/litellm/model-preferences.yaml. The aliases that must exist"
  note "  and answer, with the two facts most easily got wrong:"
  note "    cc-default             the spine's chat alias"
  note "    graphiti-llm           MUST be openai/chat_completions/<model> —"
  note "                           the Responses->chat bridge prefix"
  note "    qwen3-embedding-local  embeddings (this profile's embed alias)"
  note "    qwen3-rerank-local     api_base MUST end in /v1/rerank"
  note ""
  note "  Is the fault upstream or in the proxy? One command tells you — point"
  note "  discover-llm.sh DIRECTLY at the upstream server in that alias's"
  note "  api_base, with that server's own key:"
  note "    CC_LLM_BASE_URL=<upstream>/v1 CC_LLM_API_KEY=<upstream key> \\"
  note "      deploy/single/discover-llm.sh chat <upstream model id>"
  note "  Direct works + proxy fails = registration/alias problem (re-run"
  note "  register-models.py, or fix it in the UI). Direct fails = the upstream"
  note "  server or its key is wrong."
  note ""
  note "When it looks right, re-run:  ./deploy/k3s/setup.sh llm   (idempotent)"
}

# Probe an alias THROUGH this cluster's proxy. discover-llm.sh only sources
# deploy/single/.env when CC_LLM_BASE_URL/CC_LLM_API_KEY are absent from the
# environment (its --proxy mode reads the OTHER profile's .env, so it is not
# usable here) — passing both as env vars is what keeps it on our proxy, and
# keeps the key out of argv.
probe_alias() { # probe_alias <chat|embed> <alias>
  CC_LLM_BASE_URL="http://127.0.0.1:4000/v1" \
  CC_LLM_API_KEY="${LITELLM_MASTER_KEY:-}" \
  CC_EMBED_BASE_URL="http://127.0.0.1:4000/v1" \
  CC_EMBED_API_KEY="${LITELLM_MASTER_KEY:-}" \
    "$REPO_ROOT/deploy/single/discover-llm.sh" "$1" "$2"
}

phase_llm() {
  load_env || return 1

  step "init-env" "deploy/pi/.env bootstrapped (idempotent — nothing set was overwritten)" \
    "$HERE/init-env.sh" || return 1
  # init-env.sh may have generated values into the file we sourced before it.
  load_env || return 1

  step "secrets" "namespace, five Secrets and three ConfigMaps applied from deploy/pi/.env" \
    "$HERE/make-secrets.sh" || return 1

  # Only the LiteLLM trio here. README §1's fork ("already running" vs
  # "absent") is resolved by apply itself: applying an unchanged manifest is a
  # no-op, so both arms are the same command.
  step "apply-litellm" "namespace + LiteLLM manifests applied" \
    "${KROOT[@]}" apply -f "$HERE/00-namespace.yaml" -f "$HERE/30-litellm.yaml" -f "$HERE/31-litellm-rbac.yaml" \
    || return 1

  local d
  for d in cc-litellm-db cc-litellm-redis cc-litellm; do
    step "rollout-${d}" "$d rolled out" \
      "${K[@]}" rollout status "deploy/$d" --timeout=300s || return 1
  done

  # LiteLLM runs its Prisma migrations at boot; 5 minutes is the honest budget.
  if wait_http "http://127.0.0.1:4000/health/liveliness" 300; then
    pass "litellm-live" "proxy answers /health/liveliness on 127.0.0.1:4000 (ServiceLB, whichever node it landed on)"
  else
    fail "litellm-live" "proxy never answered /health/liveliness — run: ./deploy/k3s/setup.sh diagnose"
    return 1
  fi

  # The catalog is DB-stored, so a fresh cc-litellm-pgdata boots EMPTY and
  # there is nothing in config.yaml to restore it from. Rebuild it from the
  # tracked declaration — never a hand-composed POST /model/new. The dry-run
  # goes to stderr as the plan, then the same script applies it.
  step "register-plan" "registration plan printed (dry run)" \
    $PY "$REPO_ROOT/deploy/pi/litellm/register-models.py" --dry-run || return 1
  step "register-models" "every declared model registered/reconciled" \
    $PY "$REPO_ROOT/deploy/pi/litellm/register-models.py" || return 1
  step "policy-apply" "routing policy + costs applied onto the registered models" \
    $PY "$REPO_ROOT/deploy/pi/litellm/policy.py" --apply || return 1

  # REQUIRED, not hygiene: the adaptive router reads member preferences only
  # when it is CONSTRUCTED, so without this restart /adaptive_router/state
  # stays stale even though --check may still pass (policy.py --apply prints
  # the same warning).
  step "litellm-restart" "cc-litellm restarted so the adaptive router re-reads the preferences" \
    "${K[@]}" rollout restart deploy/cc-litellm || return 1
  step "litellm-restarted" "cc-litellm back up" \
    "${K[@]}" rollout status deploy/cc-litellm --timeout=300s || return 1
  if ! wait_http "http://127.0.0.1:4000/health/liveliness" 300; then
    fail "litellm-relive" "proxy never came back after the restart — run: ./deploy/k3s/setup.sh diagnose"
    return 1
  fi
  step "policy-check" "the live routing policy matches model-preferences.yaml" \
    $PY "$REPO_ROOT/deploy/pi/litellm/policy.py" --check || return 1

  # mint-keys.sh writes CC_LLM_API_KEY into the REPO-ROOT .env and FATALs if
  # that file does not exist — so it has to exist by now, not by the app phase.
  ensure_app_env || return 1
  # Mint AFTER registering: each key is scoped to model groups that must exist.
  # Idempotent (a set, non-PENDING value is kept); it also re-runs
  # make-secrets.sh and restarts cc-graphiti if that deployment exists yet.
  step "mint-keys" "the three Graphiti virtual keys + CC_LLM_API_KEY minted (or kept)" \
    "$HERE/mint-keys.sh" || return 1

  # The probes. A failure here is a USER-ACTION gate, not a plain FAIL
  # (2026-08-27 contract): the proxy is UP, so the fix is the operator's —
  # correct the model in the LiteLLM UI or in the declaration — never an agent
  # improvising registrations.
  if ! step "probe-chat" "a real completion came back through the cc-default alias" \
    probe_alias chat cc-default; then
    llm_gate "the cc-default alias did not return a completion"
    return 3
  fi
  # qwen3-embedding-local is THIS profile's embed alias (mint-keys.sh scopes
  # EMBEDDER_API_KEY to exactly that name) — the single-node profile's
  # `cc-embedding` does not exist here.
  local dim
  dim="$(probe_alias embed qwen3-embedding-local 2>/dev/null | tail -1)"
  if [[ ! "$dim" =~ ^[0-9]+$ ]]; then
    llm_gate "the qwen3-embedding-local alias did not return a vector"
    return 3
  fi
  pass "probe-embed" "qwen3-embedding-local returned a ${dim}-dimension vector"
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: stack — local images, every manifest, the kubeconfigs, gVisor.
# ─────────────────────────────────────────────────────────────────────────────
# Build only what is missing, and check the EXACT ref the build scripts verify
# (`docker.io/library/...`): podman tags local builds `localhost/<name>`, which
# the kubelet never matches and then tries to pull from a registry that does
# not serve it. Capture THEN grep — `ctr images ls | grep -q` inverts under
# pipefail (grep exits first, ctr takes SIGPIPE).
images_anchor()  { sudo k3s ctr -n k8s.io images ls -q 2>/dev/null; }
images_compute() { compute_ssh 'sudo k3s ctr -n k8s.io images ls -q' 2>/dev/null; }

phase_stack() {
  load_env || return 1

  local anchor_imgs compute_imgs
  anchor_imgs="$(images_anchor)"
  compute_imgs="$(images_compute)"

  # cc-graphiti FLOATS, so it must exist on BOTH nodes — a missing copy is
  # invisible until the day the pod actually moves.
  local gref=docker.io/library/cc-graphiti:1.0.2-anthropic
  if grep -qx "$gref" <<<"$anchor_imgs" && grep -qx "$gref" <<<"$compute_imgs"; then
    pass "image-graphiti" "$gref present on both nodes"
  else
    step "image-graphiti" "$gref built (arm64 here with docker, amd64 there with podman) and imported on both nodes" \
      "$HERE/build-graphiti-image.sh" || return 1
  fi

  # cc-sandbox and cc-crawler are compute-REQUIRED (gVisor and Chromium live
  # there), so they are single-arch by design and only checked there.
  local sref=docker.io/library/cc-sandbox:1
  if grep -qx "$sref" <<<"$compute_imgs"; then
    pass "image-sandbox" "$sref present on the compute node"
  else
    step "image-sandbox" "$sref built on the compute node" "$HERE/build-sandbox-image.sh" || return 1
  fi
  local cref=docker.io/library/cc-crawler:1
  if grep -qx "$cref" <<<"$compute_imgs"; then
    pass "image-crawler" "$cref present on the compute node"
  else
    step "image-crawler" "$cref built on the compute node (slow — it installs Chromium)" \
      "$HERE/build-crawler-image.sh" || return 1
  fi

  # `apply -f <dir>` reads the *.yaml files only; the scripts, units,
  # sandbox.Dockerfile and registries.yaml.example are ignored. Re-applying the
  # LiteLLM trio from the llm phase is a no-op.
  step "apply-manifests" "every manifest in deploy/k3s/ applied" \
    "${KROOT[@]}" apply -f "$HERE/" || return 1

  local d
  for d in "${DEPLOYMENTS[@]}"; do
    step "rollout-${d}" "$d rolled out" \
      "${K[@]}" rollout status "deploy/$d" --timeout=600s || return 1
  done
  step "rollout-cc-fluentbit" "the log collector is ready on every node" \
    "${K[@]}" rollout status ds/cc-fluentbit --timeout=300s || return 1

  # AFTER the manifests: each script reads a ServiceAccount token Secret the
  # manifests create. Re-runnable — a token rotation is picked up by running
  # the script again, never by editing the file.
  step "kubeconfig-sandbox" "~/.cc-sandbox-runner.kubeconfig written (0600)" \
    "$HERE/make-sandbox-kubeconfig.sh" || return 1
  step "kubeconfig-mcp" "~/.cc-mcp-deployer.kubeconfig written (0600)" \
    "$HERE/make-mcp-kubeconfig.sh" || return 1
  step "kubeconfig-litellm" "~/.cc-litellm-operator.kubeconfig + ~/.cc-litellm-logreader.kubeconfig written (0600)" \
    "$HERE/make-litellm-kubeconfig.sh" || return 1

  # Idempotent: reports-and-exits when runsc is already installed. Run here
  # rather than in preflight because the RuntimeClass now exists, which is what
  # lets it also do its pod-level proof.
  step "gvisor" "gVisor present on the compute node (with the RuntimeClass applied, its pod-level proof ran too)" \
    "$HERE/install-gvisor.sh" || return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: app — the Python environment, both .env files, the cockpit, systemd.
# ─────────────────────────────────────────────────────────────────────────────
# `env -C` would be shorter but a subshell cd is what the sibling driver uses.
in_repo() { ( cd "$REPO_ROOT" && "$@" ); }
in_web()  { ( cd "$REPO_ROOT/web" && "$@" ); }

phase_app() {
  load_env || return 1

  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    pass "venv" ".venv already present at the repo root"
  else
    step "venv" ".venv created with CPython 3.12 (Debian 12 ships 3.11)" \
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

  ensure_app_env || return 1

  # CC_LLM_API_KEY is minted BY mint-keys.sh in the llm phase, straight into
  # this file — nothing here mints one.
  if is_placeholder "$(get_kv "$APP_ENV" CC_LLM_API_KEY)"; then
    fail "app-llm-key" "CC_LLM_API_KEY is unset in the app's .env — mint it: ./deploy/k3s/setup.sh llm"
    return 1
  fi
  pass "app-llm-key" "CC_LLM_API_KEY is set (a virtual key scoped to cc-default, never the master key)"

  # Cross-file values: the same fact lives in two files, so copy it rather than
  # ask the operator to keep them in sync by hand.
  set_kv_if_unset "$APP_ENV" CC_LLM_PROXY_ADMIN_KEY "${LITELLM_MASTER_KEY:-}"     "app-admin-key"
  set_kv_if_unset "$APP_ENV" CC_EMBED_ALIAS         "qwen3-embedding-local"       "app-embed-alias"
  set_kv_if_unset "$APP_ENV" CC_NEO4J_PASSWORD      "${NEO4J_PASSWORD:-}"         "app-neo4j-password"
  set_kv_if_unset "$APP_ENV" CC_LITELLM_SALT_KEY    "${LITELLM_SALT_KEY:-}"       "app-litellm-salt"
  set_kv_if_unset "$APP_ENV" CC_LITELLM_DB_URL \
    "postgresql://llmproxy:${LITELLM_POSTGRES_PASSWORD:-}@127.0.0.1:5443/litellm" "app-litellm-db-url"
  # The three minted kubeconfig paths. The sandbox one is deliberately absent:
  # it is pinned in cc-sandbox-runner.service, not in the app's env.
  set_kv_if_unset "$APP_ENV" CC_MCP_DEPLOY_KUBECONFIG  "/home/codyslab/.cc-mcp-deployer.kubeconfig"       "app-mcp-kubeconfig"
  set_kv_if_unset "$APP_ENV" CC_LITELLM_KUBECONFIG     "/home/codyslab/.cc-litellm-operator.kubeconfig"   "app-litellm-kubeconfig"
  set_kv_if_unset "$APP_ENV" CC_LITELLM_LOG_KUBECONFIG "/home/codyslab/.cc-litellm-logreader.kubeconfig"  "app-litellm-log-kubeconfig"

  # THE measurement. Never a model card: a mis-sized vector corrupts the Neo4j
  # index instead of erroring, and the dimension is permanent once the index
  # exists.
  local dim cur
  dim="$(probe_alias embed qwen3-embedding-local 2>/dev/null | tail -1)"
  if [[ ! "$dim" =~ ^[0-9]+$ ]]; then
    fail "app-embed-dim" "the embed alias did not return a vector — run: ./deploy/k3s/setup.sh llm"
    return 1
  fi
  cur="$(get_kv "$APP_ENV" CC_EMBED_DIM)"
  if [[ -n "$cur" && "$cur" != "$dim" ]]; then
    fail "app-embed-dim" "CC_EMBED_DIM is already ${cur} but the endpoint returns ${dim} — REFUSING to change it. It is written into the Neo4j vector index; changing it means dropping the index and re-embedding the graph. Fix the model, or clear CC_EMBED_DIM deliberately on a graph you are willing to lose."
    return 1
  fi
  set_kv "$APP_ENV" CC_EMBED_DIM "$dim"
  pass "app-embed-dim" "CC_EMBED_DIM=${dim} recorded in the app's .env"

  # web/.env must exist BEFORE cc-nerve starts, or every cockpit panel that
  # proxies to the backend 502s with `fetch failed`: the code default
  # (GATEWAY_URL=http://127.0.0.1:18789, the vendored Nerve gateway) is never
  # Central Command's own API on 8080.
  if [[ -f "$REPO_ROOT/web/.env" ]]; then
    pass "web-env" "web/.env already exists — left alone"
  else
    {
      printf 'PORT=3080\n'
      printf 'GATEWAY_URL=http://127.0.0.1:8080\n'
      [[ -n "${CC_COCKPIT_ORIGIN:-}" ]] && printf 'ALLOWED_ORIGINS=%s\n' "$CC_COCKPIT_ORIGIN"
    } >"$REPO_ROOT/web/.env"
    chmod 600 "$REPO_ROOT/web/.env"
    if [[ -n "${CC_COCKPIT_ORIGIN:-}" ]]; then
      pass "web-env" "web/.env written (PORT, GATEWAY_URL, ALLOWED_ORIGINS=$CC_COCKPIT_ORIGIN)"
    else
      # Not fatal — a browser on this host works. Over the tailnet the page
      # loads and the WebSocket then 403s, visible only in the server log.
      warn "web-env" "web/.env written without ALLOWED_ORIGINS — set CC_COCKPIT_ORIGIN in deploy/pi/.env to your cockpit URL and add the line (README §6); tailnet browsers 403 on the WS upgrade without it"
    fi
  fi

  local nv=""
  command -v node >/dev/null 2>&1 && { nv="$(node -v 2>/dev/null)"; nv="${nv#v}"; }
  if [[ "${nv%%.*}" =~ ^[0-9]+$ ]] && (( ${nv%%.*} >= 22 )); then
    step "cockpit" "cockpit built (web/)" in_web bash -c 'npm ci && npm run build' || return 1
  else
    warn "cockpit" "node ${nv:-not found} is not >= 22 — cockpit not built; the API runs without it"
  fi

  # systemd units from deploy/k3s/, NOT deploy/pi/ — the old
  # deploy/pi/cc-uvicorn.service has `ExecStartPre=docker compose up -d`, which
  # resurrects the Docker stack and fights k3s for 5442. cc-nerve is the one
  # unit that still legitimately lives in deploy/pi/.
  local u
  for u in cc-uvicorn.service cc-sandbox-runner.service cc-graph-bolt.service \
           cc-backup.service cc-backup.timer cc-update.service cc-update.path; do
    step "unit-${u}" "$u installed" sudo cp "$HERE/$u" /etc/systemd/system/ || return 1
  done
  # The one-click updater's channel dirs (trigger tmpfs + durable status) —
  # cc-nerve's "apply update" button writes the trigger; cc-update.path fires
  # the root one-shot. See cc-update.sh's header for the design.
  step "update-tmpfiles" "cc-update trigger/status directories declared and created" \
    sudo bash -c "cp '$HERE/cc-update-tmpfiles.conf' /etc/tmpfiles.d/cc-update.conf && systemd-tmpfiles --create /etc/tmpfiles.d/cc-update.conf" || return 1
  step "unit-cc-nerve.service" "cc-nerve.service installed (from deploy/pi/)" \
    sudo cp "$REPO_ROOT/deploy/pi/cc-nerve.service" /etc/systemd/system/ || return 1
  step "daemon-reload" "systemd reloaded" sudo systemctl daemon-reload || return 1

  # cc-uvicorn is ENABLED, NOT STARTED — the first-boot hold. The API reads the
  # instance's env once at start, so a boot before the onboarding interview
  # runs the whole app under stale answers (it hired an agent under the
  # rehearsal persona once, 2026-08-26). `enable` without `--now` still brings
  # it up on every subsequent boot.
  step "enable-cc-uvicorn" "cc-uvicorn enabled but NOT started (first-boot hold — the interview lands first)" \
    sudo systemctl enable cc-uvicorn || return 1
  step "enable-rest" "cc-nerve, cc-sandbox-runner, cc-graph-bolt, the backup timer and the update watcher enabled and started" \
    sudo systemctl enable --now cc-nerve cc-sandbox-runner cc-graph-bolt cc-backup.timer cc-update.path || return 1

  # The gate DISSOLVES once the API runs: a re-run after first boot must
  # converge to clean and continue into verify, not stop here forever.
  if systemctl is-active --quiet cc-uvicorn; then
    pass "first-boot" "cc-uvicorn is running — first boot already happened"
    return 0
  fi
  useraction "first-boot" "the stack is installed and holding — conduct the onboarding interview, then start the API and verify"
  note ""
  note "== your move: first boot is deliberately manual =="
  note "cc-uvicorn is enabled but NOT running. The API reads the instance's env"
  note "once at start, so the interview has to land first."
  note ""
  note "  1. conduct the onboarding interview (the /setup skill drives it)"
  note "  2. sudo systemctl start cc-uvicorn      # ExecStartPre waits on 127.0.0.1:5442"
  note "  3. ./deploy/k3s/setup.sh verify --clean-install"
  note ""
  note "Then README.md phase 8: INSTANCE DATA — what a clean install does NOT"
  note "restore (the n8n Gmail credential above all). Decide each deliberately."
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: verify — verify.sh, then README §9's smoke checks.
# ─────────────────────────────────────────────────────────────────────────────
# --clean-install is PASSED THROUGH from argv, never guessed:
#   ./deploy/k3s/setup.sh verify --clean-install
# In that mode section B asserts the stores are alive, authenticated and
# schema-loaded instead of asserting migrated instance data, and 0 failures is
# the gate with no expected red. After the phase-8 restore choices, run it
# again WITHOUT the flag — that is the full form and the steady-state truth.
VERIFY_ARGS=()

phase_verify() {
  load_env || return 1
  if (( ${#VERIFY_ARGS[@]} )); then
    step "verify" "verify.sh passed (${VERIFY_ARGS[*]})" "$HERE/verify.sh" "${VERIFY_ARGS[@]}" || return 1
  else
    step "verify" "verify.sh passed (full form — instance data asserted)" "$HERE/verify.sh" || return 1
  fi

  # README §9's smoke checks. verify.sh already covers these; they are cheap,
  # and they are what an operator reads when verify.sh's 30-odd lines scroll.
  local svc url
  for svc in "litellm=http://127.0.0.1:4000/health/liveliness" \
             "graphiti=http://127.0.0.1:8000/health" \
             "control-plane-api=http://127.0.0.1:8080/health" \
             "n8n=http://127.0.0.1:5678/healthz"; do
    url="${svc#*=}"
    if curl -fsS -m 10 -o /dev/null "$url"; then
      pass "smoke-${svc%%=*}" "$url answers"
    else
      fail "smoke-${svc%%=*}" "$url did not answer"
    fi
  done
}

# ─────────────────────────────────────────────────────────────────────────────
# status — postconditions only. Mutates nothing.
# ─────────────────────────────────────────────────────────────────────────────
phase_status() {
  phase_validate || true
  load_env || return 1

  [[ -x "$REPO_ROOT/.venv/bin/python" ]] \
    && pass "venv" ".venv present" \
    || fail "venv" ".venv missing — run: ./deploy/k3s/setup.sh app"
  [[ -f "$REPO_ROOT/web/.env" ]] \
    && pass "web-env" "web/.env present" \
    || fail "web-env" "web/.env missing — run: ./deploy/k3s/setup.sh app (cc-nerve 502s without it)"

  local k
  for k in CC_LLM_API_KEY CC_EMBED_DIM CC_NEO4J_PASSWORD CC_LITELLM_SALT_KEY \
           CC_MCP_DEPLOY_KUBECONFIG CC_LITELLM_KUBECONFIG; do
    if is_placeholder "$(get_kv "$APP_ENV" "$k")"; then
      fail "app-${k}" "$k is unset in the app's .env — run: ./deploy/k3s/setup.sh app"
    else
      pass "app-${k}" "$k is set in the app's .env"
    fi
  done

  local u
  for u in cc-uvicorn cc-nerve cc-sandbox-runner cc-graph-bolt cc-backup.timer cc-update.path; do
    if systemctl is-active --quiet "$u"; then
      pass "unit-${u}" "active"
    elif systemctl is-enabled --quiet "$u" 2>/dev/null; then
      # cc-uvicorn reads exactly this way between the app phase and first boot.
      warn "unit-${u}" "enabled but not running"
    else
      fail "unit-${u}" "not enabled — run: ./deploy/k3s/setup.sh app"
    fi
  done

  step "verify" "verify.sh passed" "$HERE/verify.sh" "${VERIFY_ARGS[@]}" || true
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
  {
    echo "Central Command k3s setup diagnostics — $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "Paste this whole file to Claude. It contains key NAMES only, never values."
    echo
    echo "== host"
    uname -a 2>&1
    echo "compute node ssh target: ${CC_COMPUTE_SSH:-<unset>}"
    ssh "${SSH_OPTS[@]}" "${CC_COMPUTE_SSH:-nowhere}" 'uname -a' 2>&1
    echo
    echo "== tool versions"
    local t
    for t in k3s docker podman uv node npm git curl openssl; do
      printf '%s: ' "$t"
      if command -v "$t" >/dev/null 2>&1; then
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
    echo "== deploy/pi/.env (names only)"
    env_key_names "$ENV_FILE"
    echo
    echo "== the app's .env (names only)"
    env_key_names "$APP_ENV"
    echo
    echo "== nodes"
    "${KROOT[@]}" get nodes -o wide --show-labels 2>&1
    echo
    echo "== pods"
    "${K[@]}" get pods -o wide 2>&1
    echo
    echo "== rollout status"
    local d
    for d in "${DEPLOYMENTS[@]}"; do
      printf '%s: ' "$d"
      "${K[@]}" rollout status "deploy/$d" --timeout=5s 2>&1 | tail -1
    done
    echo
    echo "== recent events (last 50)"
    "${K[@]}" get events --sort-by=.lastTimestamp 2>&1 | tail -50
    echo
    echo "== last 100 log lines per cc- pod"
    # Names, THEN loop — a `kubectl ... | grep` pipeline inverts under pipefail.
    local pods p
    pods="$("${K[@]}" get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null)"
    while IFS= read -r p; do
      [[ -z "$p" || "$p" != cc-* ]] && continue
      echo "---- $p"
      "${K[@]}" logs --tail=100 --all-containers "$p" 2>&1
    done <<<"$pods"
    echo
    echo "== verify.sh"
    "$HERE/verify.sh" 2>&1
  } >"$out"
  chmod 600 "$out"
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

usage() {
  cat >&2 <<USAGE
usage: ./deploy/k3s/setup.sh [validate|preflight|llm|stack|app|verify|status|diagnose]
                             [--clean-install]

  no argument     runs validate -> preflight -> llm -> stack -> app -> verify,
                  stopping at the first phase that hard-fails or needs you
  --clean-install passed through to verify.sh (verify/status phases): section B
                  asserts the stores are initialized instead of asserting
                  migrated instance data. Use it on a fresh install.
  exit codes      0 clean · 1 hard failure · 2 completed with warnings
                  3 stopped for USER ACTION (see the last USERACTION line)
  status log      every check is appended to deploy/k3s/setup-log.txt

  The prose runbook this mechanizes is deploy/k3s/README.md — §1-§6 and §9.
USAGE
}

main() {
  local cmd="all" a
  for a in "$@"; do
    case "$a" in
      --clean-install) VERIFY_ARGS+=("$a") ;;
      -h|--help|help)  usage; exit 0 ;;
      -*)              usage; exit 1 ;;
      *)               cmd="$a" ;;
    esac
  done

  logline "run start: ./setup.sh $*"
  case "$cmd" in
    validate|preflight|llm|stack|app|verify|status|diagnose)
      run_phase "$cmd"; local prc=$?
      logline "run end: ./setup.sh $cmd -> exit $prc"
      exit $prc
      ;;
    all)
      local worst=0 rc p
      for p in validate preflight llm stack app verify; do
        run_phase "$p"; rc=$?
        if (( rc == 1 )); then
          note ""
          note "phase '$p' failed. Fix the FAIL line above, then re-run just that phase:"
          note "    ./deploy/k3s/setup.sh $p"
          note "If the cause is not obvious: ./deploy/k3s/setup.sh diagnose  (then paste the file to Claude)"
          logline "run end: ./setup.sh all -> exit 1 (phase $p)"
          exit 1
        fi
        if (( rc == 3 )); then
          note ""
          note "phase '$p' stopped for YOUR action — see the USERACTION line above."
          note "When done, re-run:  ./deploy/k3s/setup.sh   (idempotent — it fast-forwards to here)"
          logline "run end: ./setup.sh all -> exit 3 (phase $p)"
          exit 3
        fi
        (( rc > worst )) && worst=$rc
      done
      note ""
      note "all phases complete."
      logline "run end: ./setup.sh all -> exit $worst"
      exit "$worst"
      ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"
