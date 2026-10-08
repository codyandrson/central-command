#!/usr/bin/env bash
# ============================================================================
# setup.sh — the deterministic driver for the MULTI-NODE k3s install.
#
#   Design record: docs/superpowers/specs/2026-08-27-setup-update-contract.md
#   ("k3s substrate (Phase B)"). Sibling of deploy/single/setup.sh, same shape,
#   same protocol; the substrate is the only difference.
#
#   The rule it exists to enforce: an AGENT elicits answers, diagnoses a
#   failure. EVERYTHING that mutates anything is
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
#     stack       build the two local images if missing, apply every
#                 manifest, mint the kubeconfigs, gVisor
#     app         venv, editable install + the graphiti-core patches, root
#                 .env, web/.env, cockpit build,
#                 systemd units, first boot (the roster hires itself), the
#                 bundled skills imported
#     verify      verify.sh (+ --clean-install passthrough) + README §9 smokes
#
#     status      re-run postconditions only, nothing mutating
#     diagnose    write setup-diagnostics.txt for pasting to Claude
#     reset       DESTRUCTIVE, never part of a full run: back up, then delete
#                 the spine database and the graph so the next run is a
#                 first-run install on the same cluster (README §8a). Changes
#                 nothing without --confirm-wipe.
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
             cc-neo4j cc-n8n-db cc-n8n cc-crawler cc-vlogs cc-speech)

PY=python3

# The control-plane API. cc-uvicorn.service binds it; it never moves.
API_URL=http://127.0.0.1:8080

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

# ── Systems-page browser links (v2.52.0) ────────────────────────────────────
# The cockpit's Systems view shows an "Open →" link per service only when its
# CC_*_UI_URL / CC_*_DOCS_URL / CC_NEO4J_BROWSER_URL is set. Those are
# DISPLAY values, so no phase had ever filled them: a fresh .env (the
# 2026-09-26 clean slate) lost every link and nobody noticed until the page
# was opened. On this profile each one is derivable — the browser host is the
# node's tailnet name and the port is whatever `tailscale serve` already maps
# onto the service's loopback port (README §6 has the operator add those
# entries). A ServiceLB service (LiteLLM, VictoriaLogs, the crawler) needs no
# serve entry and must not get one: ServiceLB binds its port on every
# interface, so the link is plain http on that port. Only llama-swap is NOT derivable — it runs on the compute host,
# and nothing in either env file says where — so it stays a WARN with the
# shape to type. Every write is set_kv_if_unset: an operator's own URL wins.

# The node's tailnet DNS name, without the trailing dot — "" when tailscale
# is not up (then every link is skipped with one WARN, not seven).
tailnet_dns_name() {
  command -v tailscale >/dev/null 2>&1 || return 0
  tailscale status --self --json 2>/dev/null | python3 -c '
import json, sys
try:
    print((json.load(sys.stdin).get("Self") or {}).get("DNSName", "").rstrip("."))
except Exception:
    pass'
}

# `tailscale serve status --json` reduced to "<loopback target port> <served
# port>" lines: HTTPS handlers proxying to http://127.0.0.1:<target> and raw
# TCP forwards to 127.0.0.1:<target> (Neo4j's bolt). One line per mapping.
tailnet_serve_map() {
  command -v tailscale >/dev/null 2>&1 || return 0
  tailscale serve status --json 2>/dev/null | python3 -c '
import json, sys
from urllib.parse import urlsplit
try:
    st = json.load(sys.stdin) or {}
except Exception:
    sys.exit(0)
for hostport, web in (st.get("Web") or {}).items():
    served = hostport.rsplit(":", 1)[-1]
    for path, h in (web.get("Handlers") or {}).items():
        proxy = h.get("Proxy") or ""
        u = urlsplit(proxy)
        if path == "/" and u.hostname in ("127.0.0.1", "localhost") and u.port:
            print(u.port, served)
for served, tcp in (st.get("TCP") or {}).items():
    fwd = tcp.get("TCPForward") or ""
    if fwd.startswith(("127.0.0.1:", "localhost:")):
        print(fwd.rsplit(":", 1)[-1], served)'
}

# served_port <map> <loopback target port> — the tailnet port fronting it, or "".
served_port() {
  local target="$2" line
  while IFS= read -r line; do
    [[ "${line%% *}" == "$target" ]] && { printf '%s' "${line#* }"; return 0; }
  done <<<"$1"
  printf ''
}

derive_systems_links() {
  local host map
  host="$(tailnet_dns_name)"
  if [[ -z "$host" ]]; then
    warn "app-links" "tailscale is not up, so the Systems-page links (CC_*_UI_URL, CC_*_DOCS_URL, CC_NEO4J_BROWSER_URL) were not derived — re-run: ./deploy/k3s/setup.sh app once it is"
    return 0
  fi
  map="$(tailnet_serve_map)"

  # One row per ServiceLB (type: LoadBalancer) service: key, service port,
  # path. Plain http on every interface, no serve entry involved — and a
  # serve entry on the same port can never answer: ServiceLB's hostPort DNAT
  # takes the connection ahead of tailscaled and replies in plain http to the
  # TLS handshake (the Traefik-on-443 collision, README §6, on another port).
  # tests/test_systems_links_derived.py pins these rows to the manifests.
  local row key target path port
  for row in \
      "CC_LLM_PROXY_UI_URL 4000 /ui/" \
      "CC_VLOGS_UI_URL 9428 /select/vmui/" \
      "CC_CRAWLER_DOCS_URL 8091 /docs"; do
    read -r key port path <<<"$row"
    set_kv_if_unset "$APP_ENV" "$key" "http://${host}:${port}${path}" "app-link-${key,,}"
  done

  # One row per tailscale-serve-fronted service (loopback-only on the host,
  # so serve is the only way a browser reaches it): key, loopback port the
  # service listens on, and the path the browser lands on.
  for row in \
      "CC_N8N_UI_URL 5678 " \
      "CC_SANDBOX_DOCS_URL 8090 /docs" \
      "CC_DB_UI_URL 8092 /"; do
    read -r key target path <<<"$row"
    if ! is_placeholder "$(get_kv "$APP_ENV" "$key")"; then
      pass "app-link-${key,,}" "$key already set — left alone"; continue
    fi
    port="$(served_port "$map" "$target")"
    if [[ -z "$port" ]]; then
      warn "app-link-${key,,}" "$key not derived — no tailscale-serve entry fronts 127.0.0.1:${target}; add one (tailscale serve --bg --https=${target} http://127.0.0.1:${target}) and re-run: ./deploy/k3s/setup.sh app"
      continue
    fi
    set_kv_if_unset "$APP_ENV" "$key" "https://${host}:${port}${path}" "app-link-${key,,}"
  done

  # Neo4j Browser: the HTTPS entry over 7474 plus, when the bolt TCP forward
  # (7687, TLS-terminated by tailscale) is served too, a ?dbms= that lands
  # the browser on the right database over bolt+s.
  if is_placeholder "$(get_kv "$APP_ENV" CC_NEO4J_BROWSER_URL)"; then
    local http_port bolt_port url
    http_port="$(served_port "$map" 7474)"
    bolt_port="$(served_port "$map" 7687)"
    if [[ -z "$http_port" ]]; then
      warn "app-link-cc_neo4j_browser_url" "CC_NEO4J_BROWSER_URL not derived — no tailscale-serve entry fronts 127.0.0.1:7474 (the cc-graph-bolt relay); add one (tailscale serve --bg --https=7474 http://127.0.0.1:7474) and re-run: ./deploy/k3s/setup.sh app"
    else
      url="https://${host}:${http_port}/browser/"
      [[ -n "$bolt_port" ]] && url="${url}?dbms=bolt%2Bs%3A%2F%2F${host}%3A${bolt_port}"
      set_kv_if_unset "$APP_ENV" CC_NEO4J_BROWSER_URL "$url" "app-link-cc_neo4j_browser_url"
    fi
  else
    pass "app-link-cc_neo4j_browser_url" "CC_NEO4J_BROWSER_URL already set — left alone"
  fi

  # llama-swap lives on the compute host; nothing here knows its address.
  if is_placeholder "$(get_kv "$APP_ENV" CC_LLAMA_SWAP_UI_URL)"; then
    pass "app-link-cc_llama_swap_ui_url" "CC_LLAMA_SWAP_UI_URL is empty — not derivable (llama-swap runs on the compute host); if you run one, set it in the app's .env to http://<compute-host-tailnet-ip>:8081/ui for a Systems-page link + liveness probe"
  else
    pass "app-link-cc_llama_swap_ui_url" "CC_LLAMA_SWAP_UI_URL already set — left alone"
  fi
}

# The app's .env must exist before mint-keys.sh runs — it writes CC_LLM_API_KEY
# INTO it and FATALs if the file is absent (mint-keys.sh:42). That is why this
# lives here and is called from the llm phase, not only from app.
#
# CC_EXECUTOR_MODE stays at .env.example's `live` (v2.37.0; a fresh install
# used to be forced to dry_run, and the unit pinned it — see the unit file's
# history). The approval gate is the safety; nothing proposes on its own until
# the operator enables the feed, the drain or a schedule. An existing file is
# never touched.
ensure_app_env() {
  if [[ -f "$APP_ENV" ]]; then
    pass "app-env" "the app's .env already exists — only empty values will be filled"
    return 0
  fi
  cp "$REPO_ROOT/.env.example" "$APP_ENV" || { fail "app-env" "could not create $APP_ENV"; return 1; }
  chmod 600 "$APP_ENV"
  pass "app-env" "created the app's .env from .env.example (0600)"
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
      fail "answer-${v}" "$v is PENDING — a placeholder, not a credential; clear it and run: ./deploy/k3s/init-env.sh"
    else
      pass "answer-${v}" "$v is set"
    fi
  done
  # GRAPHITI_LLM_API_KEY / EMBEDDER_API_KEY / RERANKER_API_KEY are no longer
  # read by anything (the Graphiti server left; design record 2026-10-04, D10)
  # — an existing file may still carry them, and that is harmless.

  # The app's .env. validate REPORTS ONLY — it mutates nothing, so the fix is
  # printed rather than performed (the llm phase creates it, for mint-keys.sh).
  if [[ -f "$APP_ENV" ]]; then
    pass "app-env" "the app's .env is present"
  else
    warn "app-env" "the repo-root .env is missing — the llm phase creates it (or: cp .env.example .env && chmod 600 .env)"
  fi

  # Jira/Confluence ANSWERS, shape only (v2.53.0). Until this existed nothing in
  # either installer asked for them, and the reference deployment discovered
  # mid-tour that the team had no Jira. This phase REPORTS ONLY — the live probe
  # belongs to the verify phase, which runs scripts/atlassian_probe.py against
  # the same keys. Blank is a PASS with the consequence stated, never a
  # USERACTION: validate is the first phase of the `all` chain and exit 3 there
  # would make a deliberately Jira-less install impossible to finish in one
  # command (the single profile's v2.45.1 rule for the LLM catalog).
  if [[ -f "$APP_ENV" ]]; then
    local jbase jmail jtoken
    jbase="$(get_kv "$APP_ENV" CC_JIRA_BASE_URL)"
    jmail="$(get_kv "$APP_ENV" CC_JIRA_EMAIL)"
    jtoken="$(get_kv "$APP_ENV" CC_JIRA_API_TOKEN)"
    if [[ -z "$jbase" ]]; then
      pass "jira" "CC_JIRA_BASE_URL is blank in the app's .env — this deployment has no Jira. That is a valid answer, not a gate: any agent holding a jira or confluence capability then fails at execution with \"Jira is not configured\", the jira-expert's introduction included. To enable it, fill CC_JIRA_BASE_URL, CC_JIRA_EMAIL and CC_JIRA_API_TOKEN (plus the CC_CONFLUENCE_* set for the wiki) and re-run validate"
    elif [[ -z "$jtoken" || -z "$jmail" ]]; then
      fail "jira" "CC_JIRA_BASE_URL is set but the credentials are incomplete in the app's .env (CC_JIRA_EMAIL$([[ -z "$jmail" ]] && echo ' MISSING'), CC_JIRA_API_TOKEN$([[ -z "$jtoken" ]] && echo ' MISSING')) — every Jira read needs the token, and Cloud needs the email for Basic auth (a Data Center PAT instead: CC_JIRA_API_FLAVOR=server, CC_JIRA_AUTH_MODE=bearer)"
    else
      pass "jira" "jira answers present (CC_JIRA_BASE_URL + CC_JIRA_EMAIL + CC_JIRA_API_TOKEN) — probed live in the verify phase"
    fi
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

  # One builder: podman on the compute node builds both local images (sandbox
  # and crawler are compute-REQUIRED, so amd64 only, natively, no QEMU). The
  # anchor's docker was only ever for the arm64 half of the retired Graphiti
  # image, which floated and so had to exist on both nodes.
  if compute_ssh 'command -v podman' >/dev/null 2>&1; then
    pass "builder-podman" "podman present on the compute node (native amd64 builds, no QEMU)"
  else
    fail "builder-podman" "podman not found on $CC_COMPUTE_SSH — the amd64 builds are native there"
  fi

  local t
  for t in curl openssl uv git ssh scp socat; do
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

  # Terminated-pod garbage collection (v2.39.2). Kubernetes never collects
  # dead pod records below a 12500 cluster-wide threshold, so every anchor
  # reboot leaves a generation of Succeeded/Failed pods behind. A WARN, not a
  # FAIL: nothing stops working without it, the install can proceed.
  if sudo grep -qs 'terminated-pod-gc-threshold' /etc/rancher/k3s/config.yaml /etc/rancher/k3s/config.yaml.d/*.yaml 2>/dev/null; then
    pass "k3s-pod-gc" "terminated-pod-gc-threshold is set on the k3s server"
  else
    warn "k3s-pod-gc" "no terminated-pod-gc-threshold on the k3s server — install deploy/k3s/host/10-central-command.yaml (README §1) and restart k3s"
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
  note "The proxy is UP. Its model catalog lives in its database and is yours"
  note "to fill in — setup created the declared aliases as skeletons and stops"
  note "here, on purpose, so the providers are right before anything else is"
  note "deployed. No agent registers or edits models on your behalf."
  note ""
  note "  UI:           http://127.0.0.1:4000/ui   (Models + Endpoints)"
  note "  login:        username 'admin', password = LITELLM_MASTER_KEY"
  note "                (or UI_USERNAME/UI_PASSWORD if set in .env)"
  note "                (the value is in deploy/pi/.env — not printed here)"
  note ""
  note "  For each alias listed above, edit the row: replace every PLACEHOLDER"
  note "  (model id after the prefix, api_base host) and enter the key — for"
  note "  the Anthropic rows create the credential under Endpoints and attach"
  note "  it (ANTHROPIC_API_KEY is deliberately NOT in the pod). The invariants"
  note "  the re-run checks are in deploy/pi/litellm/model-preferences.yaml:"
  note "    graphiti-llm           MUST be a PLAIN openai/<model> — same as"
  note "                           cc-default. The old chat_completions/"
  note "                           bridge prefix is now WRONG: the app's"
  note "                           in-process graphiti-core client speaks"
  note "                           chat-completions, and a bridged alias 404s."
  note "    qwen3-rerank-local     api_base MUST end in /v1/rerank, mode rerank"
  note "    cc-rerank              graph search's reranker ROLE: a dedicated reranker"
  note "                           (cohere/<id>, api_base ending /v1/rerank, mode"
  note "                           rerank — like qwen3-rerank-local) or a chat model"
  note "                           with logprobs and thinking OFF (openai/<id>). The"
  note "                           probe decides which (CC_GRAPH_RERANK_KIND)."
  note "    cc-embedding           the embedding ROLE — same upstream as"
  note "                           qwen3-embedding-local; both rows must exist"
  note "    cc-tts / cc-stt        the speech ROLES; the bundled cc-speech pod"
  note "                           answers both — enter, verbatim:"
  note "      cc-tts  model openai/speaches-ai/Kokoro-82M-v1.0-ONNX  api_base http://cc-speech:8093/v1  key none"
  note "      cc-stt  model openai/Systran/faster-whisper-small     api_base http://cc-speech:8093/v1  key none"
  note "                           (or point cc-stt at hosted Whisper-convention models)"
  note "  A key a local server ignores can be 'none', but must be non-empty."
  note ""
  note "  Not sure what a server names its models? List them directly:"
  note "    CC_LLM_BASE_URL=<url>/v1 CC_LLM_API_KEY=<key> deploy/single/discover-llm.sh models"
  note "  A probe failed after you filled things in? Direct works + proxy fails ="
  note "  the alias row is wrong; direct fails = the server or its key is wrong."
  note ""
  note "When it looks right, re-run:  ./deploy/k3s/setup.sh llm   (it validates every alias, then continues)"
}

# Probe an alias THROUGH this cluster's proxy. discover-llm.sh sources an .env
# only in its --proxy mode — the repo-root one since v2.42.0, where it expects
# the SINGLE-NODE profile's CC_LLM_PROXY_ADMIN_KEY and CC_LITELLM_PORT, so that
# mode is still not usable here. Passing CC_LLM_BASE_URL/CC_LLM_API_KEY as env
# vars (DIRECT mode, which sources nothing) is what keeps it on our proxy, and
# keeps the key out of argv.
probe_alias() { # probe_alias <chat|structured|embed|speech|transcribe|rerank|rerank-chat> <alias> [file]
  CC_LLM_BASE_URL="http://127.0.0.1:4000/v1" \
  CC_LLM_API_KEY="${LITELLM_MASTER_KEY:-}" \
  CC_EMBED_BASE_URL="http://127.0.0.1:4000/v1" \
  CC_EMBED_API_KEY="${LITELLM_MASTER_KEY:-}" \
    "$REPO_ROOT/deploy/single/discover-llm.sh" "$@"
}

rerank_decide_k3s() {
  local alias kind found=""
  if grep -q '^CC_GRAPH_RERANK_ALIAS=$' "$APP_ENV" 2>/dev/null; then
    pass "probe-rerank" "CC_GRAPH_RERANK_ALIAS is empty in the app's .env — your explicit 'no reranker'; not probed (fact search ranks by rank fusion alone)"
    return 0
  fi
  alias="$(get_kv "$APP_ENV" CC_GRAPH_RERANK_ALIAS)"; alias="${alias:-cc-rerank}"
  kind="$(get_kv "$APP_ENV" CC_GRAPH_RERANK_KIND)"
  # cc-rerank is OPTIONAL in the declaration (v2.62.1): an unfilled skeleton
  # no longer pauses the catalog step, so a site with no reranker finishes the
  # install. Unfilled, with no alias or kind in the app's .env, is "no
  # reranker" — written as an explicit empty CC_GRAPH_RERANK_ALIAS, so neither
  # the app phase's default nor the updater's turns it on behind the operator.
  # Unfilled while the app's .env NAMES it is the gate: search would error.
  local state
  state="$($PY "$REPO_ROOT/deploy/pi/litellm/register-models.py" --row-state "$alias" 2>/dev/null)" || state=""
  if [[ "$state" != filled ]]; then
    if [[ -z "$state" ]]; then
      fail "probe-rerank" "could not read the proxy's /model/info to see whether $alias is filled — run: ./deploy/k3s/setup.sh diagnose"
      return 1
    fi
    if ! grep -q '^CC_GRAPH_RERANK_ALIAS=' "$APP_ENV" && [[ -z "$kind" ]]; then
      set_kv "$APP_ENV" CC_GRAPH_RERANK_ALIAS ""
      pass "probe-rerank" "no reranker: $alias is $state in the LiteLLM catalog — CC_GRAPH_RERANK_ALIAS= (empty, an explicit off) written to the app's .env; fact search ranks by rank fusion alone. To turn reranking on: fill the $alias row in the LiteLLM UI (a dedicated reranker, cohere/<id> with api_base ending /v1/rerank and mode rerank; or a chat model with logprobs and thinking OFF, openai/<id>), set CC_GRAPH_RERANK_ALIAS=$alias in the app's .env, and re-run ./deploy/k3s/setup.sh llm"
      return 0
    fi
    llm_gate "the $alias alias is $state in the LiteLLM catalog, but the app's .env names it (CC_GRAPH_RERANK_ALIAS=$alias${kind:+, CC_GRAPH_RERANK_KIND=$kind}) — fill the row (a dedicated reranker, or a chat model with logprobs and thinking off), or put the line CC_GRAPH_RERANK_ALIAS= (empty) in the app's .env to run without a reranker; graph search would ERROR meanwhile"
    return 3
  fi
  case "$kind" in
    "")
      if probe_alias rerank "$alias"; then found=rerank
      elif probe_alias rerank-chat "$alias"; then found=chat
      fi
      if [[ -z "$found" ]]; then
        llm_gate "the $alias alias answers neither LiteLLM's /rerank nor a True/False chat question with logprobs (a chat model must not think first — disable thinking on the row); graph search would ERROR with it configured"
        return 3
      fi
      set_kv "$APP_ENV" CC_GRAPH_RERANK_KIND "$found"
      pass "probe-rerank" "$alias is a ${found} reranker — CC_GRAPH_RERANK_KIND=$found written to the app's .env"
      ;;
    rerank|chat)
      local sub=rerank; [[ "$kind" == chat ]] && sub=rerank-chat
      if ! probe_alias "$sub" "$alias"; then
        llm_gate "CC_GRAPH_RERANK_KIND=$kind, but $alias does not answer as a $kind reranker — fix the row, or empty CC_GRAPH_RERANK_KIND in the app's .env to re-detect; graph search would ERROR meanwhile"
        return 3
      fi
      pass "probe-rerank" "the $kind reranker $alias answers (CC_GRAPH_RERANK_KIND=$kind is set, so it was proven, not re-detected)"
      ;;
    *)
      fail "probe-rerank" "CC_GRAPH_RERANK_KIND=$kind in the app's .env is not rerank or chat — fix or empty it (the API refuses to build the graph client meanwhile)"
      return 1
      ;;
  esac
  return 0
}

phase_llm() {
  load_env || return 1

  step "init-env" "deploy/pi/.env bootstrapped (idempotent — nothing set was overwritten)" \
    "$HERE/init-env.sh" || return 1
  # init-env.sh may have generated values into the file we sourced before it.
  load_env || return 1

  step "secrets" "namespace, the Secrets and the two file-built ConfigMaps applied from deploy/pi/.env" \
    "$HERE/make-secrets.sh" || return 1

  # Only the LiteLLM trio here. README §1's fork ("already running" vs
  # "absent") is resolved by apply itself: applying an unchanged manifest is a
  # no-op, so both arms are the same command.
  step "apply-litellm" "namespace + LiteLLM manifests applied" \
    "${KROOT[@]}" apply -f "$HERE/00-namespace.yaml" -f "$HERE/30-litellm.yaml" -f "$HERE/31-litellm-rbac.yaml" \
    || return 1
  # The speech engine goes in HERE, not with the stack: its aliases are probed
  # below, and a probe against a pod that is not up yet is a false gate. Its
  # first boot downloads the models, so it gets the whole catalog pause to
  # come up; the rollout wait is at the probe.
  step "apply-speech" "speech engine manifest applied" \
    "${KROOT[@]}" apply -f "$HERE/90-speech.yaml" || return 1

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

  # The catalog is DB-stored and managed in the proxy's UI (operator
  # decision, 2026-08-30). register-models.py is CREATE-ONLY: an absent alias
  # becomes a skeleton (name + invariants + PLACEHOLDER where the provider
  # goes); an existing one is never touched. Exit 3 — a fresh catalog, a
  # placeholder left in, a broken invariant — is the USER-ACTION gate, and it
  # fires BEFORE the policy, the keys and every later phase.
  local rrc=0
  $PY "$REPO_ROOT/deploy/pi/litellm/register-models.py" >&2 || rrc=$?
  case "$rrc" in
    0) pass "catalog" "every declared alias is registered, filled in and consistent" ;;
    3) llm_gate "the model catalog needs your provider details (see the alias list above)"; return 3 ;;
    *) fail "catalog" "register-models.py failed (exit $rrc) — run: ./deploy/k3s/setup.sh diagnose"; return 1 ;;
  esac
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
  # Idempotent (a set, non-PENDING value is kept, and a kept key's scope is
  # WIDENED to the graph aliases when it lacks them — how an install that
  # predates the in-process graph client gets them); it also re-runs
  # make-secrets.sh for the crawler's Secret.
  step "mint-keys" "CC_LLM_API_KEY minted (or kept), scoped to cc-default, speech and the graph aliases" \
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
  if ! step "probe-structured" "graphiti-llm returned schema-constrained JSON through chat/completions" \
    probe_alias structured graphiti-llm; then
    llm_gate "the graphiti-llm alias did not return schema-constrained JSON (a chat_completions/ prefix on the registration is a likely cause — it should be a plain openai/<model>)"
    return 3
  fi
  # cc-rerank's KIND, decided by PROBING (design record 2026-10-04, D3 as
  # rebuilt in v2.62.0) — the same two probes as the single-node profile:
  # LiteLLM's /rerank first (a dedicated reranker), then the chat shape (a
  # True/False answer with logprobs, thinking off). This profile REQUIRES the
  # alias in its catalog, so "neither" is the same USER-ACTION gate as every
  # other probe here. The kind is written to the app's .env only when the key
  # is empty — a kind that is set is PROVEN, never re-detected (empty it to
  # re-detect). An explicitly EMPTY CC_GRAPH_RERANK_ALIAS is the operator's
  # "no reranker" and is not probed.
  rerank_decide_k3s || return $?
  # cc-embedding is the embedding ROLE alias (2026-08-30, parity with the
  # single-node profile) — the app's graph client embeds through exactly that
  # name; qwen3-rerank-local/qwen3-embedding-local stay as real-model rows.
  local dim
  dim="$(probe_alias embed cc-embedding 2>/dev/null | tail -1)"
  if [[ ! "$dim" =~ ^[0-9]+$ ]]; then
    llm_gate "the cc-embedding alias did not return a vector"
    return 3
  fi
  pass "probe-embed" "cc-embedding returned a ${dim}-dimension vector"

  # Speech: one synthesis, then transcribe what it said — both aliases proven
  # with real audio. A .mp3 suffix so the file's name agrees with its bytes.
  step "rollout-cc-speech" "cc-speech rolled out (first boot downloads ~1GB of models)" \
    "${K[@]}" rollout status deploy/cc-speech --timeout=1800s || return 1
  local mp3; mp3="$(mktemp --suffix=.mp3)"
  if ! step "probe-tts" "cc-tts synthesised speech" probe_alias speech cc-tts "$mp3"; then
    rm -f "$mp3"; llm_gate "the cc-tts alias did not return audio"
    return 3
  fi
  if ! step "probe-stt" "cc-stt transcribed what cc-tts said" probe_alias transcribe cc-stt "$mp3"; then
    rm -f "$mp3"; llm_gate "the cc-stt alias did not return a transcription"
    return 3
  fi
  rm -f "$mp3"
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

  local compute_imgs
  compute_imgs="$(images_compute)"

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

# ── the bundled skills ───────────────────────────────────────────────────────
# The same step as the single-node profile's boot/skills-imported (design
# record 2026-10-01, D7), which this driver lacked: a fresh spine had no
# skills until an operator imported the folders by hand. Every
# skills/<id>/SKILL.md folder this release ships: "<id>\t<abs dir>" per line.
# The id is the FOLDER name, passed to the importer explicitly
# (tests/test_single_boot_supervision.py proves each equals the id the
# importer would derive), so a skill imported by hand from the same folder is
# recognised as the same skill.
bundled_skill_dirs() {
  local d
  for d in "$REPO_ROOT"/skills/*/; do
    [[ -f "${d}SKILL.md" ]] || continue
    d="${d%/}"
    printf '%s\t%s\n' "${d##*/}" "$d"
  done
  return 0
}

# The ids the library holds, one per line — RETIRED ones included (GET
# /api/skills includes them by default): a bundled skill the operator retired
# is still "held", and re-importing it would undo their decision.
# 1 = the API did not answer, which is not the same as an empty library.
skills_library_ids() {
  local out
  out="$(curl -fsS -m 15 "$API_URL/api/skills" 2>/dev/null)" || return 1
  printf '%s' "$out" | $PY -c 'import json,sys; [print(s.get("id","")) for s in json.load(sys.stdin).get("skills",[])]' 2>/dev/null
}

# The importer is the API's own `POST /api/skills/import` — body {"path": <a
# SKILL.md + references/ folder on this host>, "skill_id": <id>} — which reads
# the folder from disk. CREATE-ONLY: a skill the library already holds is
# never re-imported, even when this release changed the bundled copy; the
# operator's library is theirs once a skill is in it.
app_skills_import() {
  local have id d body resp code why="" added=() kept=() failed=()
  if ! have="$(skills_library_ids)"; then
    fail "skills-imported" "GET $API_URL/api/skills did not answer, so the bundled skills were not imported — read: journalctl -u cc-uvicorn -n 50"
    return 1
  fi
  while IFS=$'\t' read -r id d; do
    [[ -n "$id" ]] || continue
    if [[ $'\n'"$have"$'\n' == *$'\n'"$id"$'\n'* ]]; then
      kept+=("$id")
      continue
    fi
    body="$($PY -c 'import json,sys; print(json.dumps({"path": sys.argv[1], "skill_id": sys.argv[2]}))' "$d" "$id")"
    note "--> POST $API_URL/api/skills/import  {skill_id: $id}"
    resp="$(printf '%s' "$body" | curl -sS -m 120 -X POST -H 'content-type: application/json' \
      -w '\n%{http_code}' -d @- "$API_URL/api/skills/import" 2>&1)"
    code="${resp##*$'\n'}"
    if [[ "$code" == 200 ]]; then
      added+=("$id")
    else
      failed+=("$id")
      why="${resp%$'\n'*}"; why="${why//$'\n'/ }"; why="${why:0:240}"
    fi
  done < <(bundled_skill_dirs)
  if (( ${#failed[@]} )); then
    fail "skills-imported" "could not import ${failed[*]} through POST $API_URL/api/skills/import: ${why:-no answer} — read: journalctl -u cc-uvicorn -n 50"
    return 1
  fi
  if (( ${#added[@]} + ${#kept[@]} == 0 )); then
    pass "skills-imported" "this release bundles no skills (no skills/*/SKILL.md) — nothing to import"
    return 0
  fi
  local msg=""
  (( ${#added[@]} )) && msg="imported ${#added[@]} bundled skill(s): ${added[*]}"
  if (( ${#kept[@]} )); then
    msg="${msg:+$msg; }${#kept[@]} already in the library and LEFT AS THEY ARE: ${kept[*]}"
  fi
  pass "skills-imported" "$msg — create-only: a skill the library holds is never overwritten, even when this release changed the bundled copy (re-import one on purpose with POST /api/skills/import and its skill_id)"
}

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
  # The two carried graphiti-core fixes (deploy/graphiti-patches/, design
  # record 2026-10-04 D7), applied after EVERY dependency install — an install
  # puts back a pristine package. Idempotent; a hunk that matches neither the
  # pristine nor the patched file FAILS here (the version is pinned, so that
  # is a real finding). Without them the ingest worker refuses extraction.
  step "graphiti-patches" "the carried graphiti-core fixes are applied to the venv (deploy/graphiti-patches/)" \
    in_repo "$REPO_ROOT/.venv/bin/python" scripts/apply_graphiti_patches.py || return 1

  ensure_app_env || return 1

  # CC_LLM_API_KEY is minted BY mint-keys.sh in the llm phase, straight into
  # this file — nothing here mints one.
  if is_placeholder "$(get_kv "$APP_ENV" CC_LLM_API_KEY)"; then
    fail "app-llm-key" "CC_LLM_API_KEY is unset in the app's .env — mint it: ./deploy/k3s/setup.sh llm"
    return 1
  fi
  pass "app-llm-key" "CC_LLM_API_KEY is set (a virtual key scoped by mint-keys.sh, never the master key)"

  # Cross-file values: the same fact lives in two files, so copy it rather than
  # ask the operator to keep them in sync by hand.
  set_kv_if_unset "$APP_ENV" CC_LLM_PROXY_ADMIN_KEY "${LITELLM_MASTER_KEY:-}"     "app-admin-key"
  set_kv_if_unset "$APP_ENV" CC_EMBED_ALIAS         "cc-embedding"                "app-embed-alias"
  set_kv_if_unset "$APP_ENV" CC_NEO4J_PASSWORD      "${NEO4J_PASSWORD:-}"         "app-neo4j-password"
  set_kv_if_unset "$APP_ENV" CC_LITELLM_SALT_KEY    "${LITELLM_SALT_KEY:-}"       "app-litellm-salt"
  set_kv_if_unset "$APP_ENV" CC_LITELLM_DB_URL \
    "postgresql://llmproxy:${LITELLM_POSTGRES_PASSWORD:-}@127.0.0.1:5443/litellm" "app-litellm-db-url"
  # The three minted kubeconfig paths. The sandbox one is deliberately absent:
  # it is pinned in cc-sandbox-runner.service, not in the app's env.
  set_kv_if_unset "$APP_ENV" CC_MCP_DEPLOY_KUBECONFIG  "/home/codyslab/.cc-mcp-deployer.kubeconfig"       "app-mcp-kubeconfig"
  set_kv_if_unset "$APP_ENV" CC_LITELLM_KUBECONFIG     "/home/codyslab/.cc-litellm-operator.kubeconfig"   "app-litellm-kubeconfig"
  set_kv_if_unset "$APP_ENV" CC_LITELLM_LOG_KUBECONFIG "/home/codyslab/.cc-litellm-logreader.kubeconfig"  "app-litellm-log-kubeconfig"

  # Settings with a value THIS profile needs, added only when the key is
  # ABSENT: an operator's own value — an empty one included, which is an
  # explicit "off" — is never touched. The same table cc-update.sh's
  # APP_ENV_DEFAULTS carries for an existing install; keep the two in step.
  #   CC_GRAPH_RERANK_ALIAS=cc-rerank — this profile registers the cc-rerank
  #   cross-encoder, and graph search uses it only when this names it.
  local row key
  for row in "CC_GRAPH_RERANK_ALIAS=cc-rerank"; do
    key="${row%%=*}"
    if grep -q "^${key}=" "$APP_ENV"; then
      pass "app-${key,,}" "$key already present in the app's .env — left alone"
    else
      set_kv "$APP_ENV" "$key" "${row#*=}" && pass "app-${key,,}" "$row added to the app's .env"
    fi
  done

  # The Systems page's "Open →" links, derived from tailscale (see
  # derive_systems_links above). Display-only; a WARN, never a FAIL.
  derive_systems_links

  # The façade tokens (v2.51.0). What the control plane sends and what the
  # n8n webhooks check — apply-workflows.sh renders them into the workflow
  # files. No installer generated them before; a fresh .env carried two empty
  # values and apply-workflows died on the first. hex keeps them inside the
  # alphabet the render step accepts ([A-Za-z0-9_.:+=-]); an existing value is
  # never overwritten (the live workflows were rendered from it).
  local tokvar
  for tokvar in CC_EMAIL_FACADE_TOKEN CC_CALENDAR_FACADE_TOKEN; do
    if is_placeholder "$(get_kv "$APP_ENV" "$tokvar")"; then
      set_kv "$APP_ENV" "$tokvar" "$(openssl rand -hex 24)" \
        && pass "app-${tokvar,,}" "$tokvar generated (rendered into the façade workflows below)"
    else
      pass "app-${tokvar,,}" "$tokvar already set — left alone"
    fi
  done

  # THE measurement. Never a model card: nothing schema-side enforces the
  # width (no Neo4j vector index exists — similarity is per-row cosine), so a
  # mis-sized vector silently breaks search instead of erroring, and the
  # dimension is effectively permanent once vectors are stored.
  local dim cur
  dim="$(probe_alias embed cc-embedding 2>/dev/null | tail -1)"
  if [[ ! "$dim" =~ ^[0-9]+$ ]]; then
    fail "app-embed-dim" "the embed alias did not return a vector — run: ./deploy/k3s/setup.sh llm"
    return 1
  fi
  cur="$(get_kv "$APP_ENV" CC_EMBED_DIM)"
  if [[ -n "$cur" && "$cur" != "$dim" ]]; then
    fail "app-embed-dim" "CC_EMBED_DIM is already ${cur} but the endpoint returns ${dim} — REFUSING to change it. Every stored vector in the graph is ${cur}-wide and nothing schema-side enforces that; changing the embedder is a re-embed-the-whole-graph migration (skills/graphiti/references/operations.md). Fix the model, or clear CC_EMBED_DIM deliberately on a graph you are willing to lose."
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

  # The cockpit's voice features (read-aloud, voice input) go through THIS
  # cluster's LiteLLM by alias — never an external provider. The Node server
  # reads these as its "OpenAI" endpoint; the key is a virtual key scoped to
  # the two speech aliases (mint-keys.sh, which keeps every key already set).
  local wenv="$REPO_ROOT/web/.env"
  set_kv_if_unset "$wenv" OPENAI_BASE_URL  "http://127.0.0.1:4000/v1" "web-speech-url"
  set_kv_if_unset "$wenv" OPENAI_TTS_MODEL "cc-tts"                   "web-speech-tts"
  set_kv_if_unset "$wenv" OPENAI_STT_MODEL "cc-stt"                   "web-speech-stt"
  set_kv_if_unset "$wenv" STT_PROVIDER     "openai"                   "web-speech-provider"
  if [[ -z "$(get_kv "$wenv" OPENAI_API_KEY)" ]]; then
    step "web-speech-key" "cockpit virtual key minted into web/.env (OPENAI_API_KEY, scope cc-tts + cc-stt)" \
      "$HERE/mint-keys.sh" || return 1
  else
    pass "web-speech-key" "web/.env OPENAI_API_KEY already set"
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

  # First boot is no longer held for an interview (v2.37.0): the operator's
  # name is asked in the cockpit and stored as an app setting, and the team
  # tour asks the rest. The unit's ExecStartPre waits on 127.0.0.1:5442.
  step "enable-units" "cc-uvicorn, cc-nerve, cc-sandbox-runner, cc-graph-bolt, the backup timer and the update watcher enabled and started" \
    sudo systemctl enable --now cc-uvicorn cc-nerve cc-sandbox-runner cc-graph-bolt cc-backup.timer cc-update.path || return 1

  # The n8n façades (v2.51.0): render the tokens into the shipped workflows
  # and import them. Before this the runbook told the operator to run the
  # script by hand after creating the Gmail credential; a missing credential
  # is now the script's own USERACTION line, not a reason to skip the import.
  # WARN, never FAIL: n8n is one integration, not the install.
  if bash "$HERE/../n8n/apply-workflows.sh" --k3s; then
    pass "n8n-workflows" "façade workflows applied into n8n (deploy/n8n/apply-workflows.sh)"
  else
    warn "n8n-workflows" "apply-workflows.sh did not finish — usual cause: no credential named \"Gmail account\" / \"Google Calendar account\" in n8n yet (README §8). Create it, then: ./deploy/n8n/apply-workflows.sh --k3s"
  fi

  # The bundled skills go in through the API's own importer, so the API must
  # be answering first — on a first boot it hires the roster before it does.
  if wait_http "$API_URL/health" 180; then
    pass "api-up" "the control-plane API answers at $API_URL"
    app_skills_import
  else
    fail "api-up" "the control-plane API did not answer at $API_URL/health within 180s, so the bundled skills were not imported — read: journalctl -u cc-uvicorn -n 50, then re-run: ./deploy/k3s/setup.sh app"
  fi

  note ""
  note "First boot hires the roster. Next: ./deploy/k3s/setup.sh verify --clean-install,"
  note "then README.md phase 8: INSTANCE DATA — what a clean install does NOT"
  note "restore (the n8n Gmail credential above all). Decide each deliberately."
  note "Open the cockpit: it asks your name. Then Crons -> 'Team tour (onboarding)'"
  note "-> Run now: your EA hires itself and hosts the tour."
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
# PHASE: reset — wipe the INSTANCE, keep the SYSTEM. Never part of `all`.
# ─────────────────────────────────────────────────────────────────────────────
# "Start over without tearing it down": the spine database and the knowledge
# graph go (roster, charters, ledger, proposals, sessions, event log, the
# operator's name; every node and edge), so the next ./setup.sh lands on the
# designed first-run state. What is slow or impossible to redo STAYS — the
# cluster, the images, the LiteLLM database (models, keys, spend), the n8n
# database (the OAuth credentials) and deploy/pi/.env (the two keys that must
# never rotate). README §8a is the prose.
#
# Three properties, each pinned by tests/test_k3s_reset_phase.py:
#   * a bare `reset` changes NOTHING — it prints the plan and stops with a
#     USERACTION; only --confirm-wipe mutates;
#   * nothing is wiped until backup.sh has exited 0 and every file of that
#     dump set is linked into keep-pre-reset-<stamp>/, a folder the nightly
#     retention never reads (it prunes top-level files only);
#   * RESET_STORES is the whole list of what is deleted. The LiteLLM and n8n
#     volumes are not in it, and deploy/pi/.env is never touched.
#
# The app's .env and web/.env are MOVED into the keep folder unless --keep-env
# is given: a carried-over .env is a migration, not a fresh install (the
# 2026-09-26 rebuild that kept one never showed the onboarding's gaps).
RESET_CONFIRM=0
RESET_KEEP_ENV=0
# <deployment>|<pvc>|<manifest in deploy/k3s/>
RESET_STORES=("cc-postgres|cc-pgdata|20-postgres.yaml"
              "cc-neo4j|cc-neo4j-data|40-graph.yaml")
# The spine's and the graph's consumers, all outside the cluster. The app
# phase's `enable --now` starts them again.
RESET_UNITS=(cc-uvicorn cc-sandbox-runner cc-nerve)

# Poll until a `kubectl get … -o name` comes back empty.
wait_gone() { # wait_gone <seconds> <kubectl get args...>
  local deadline=$(( SECONDS + $1 )); shift
  while [[ -n "$("$@" -o name 2>/dev/null)" ]]; do
    (( SECONDS < deadline )) || return 1
    sleep 3
  done
}

# The pvc-protection controller counts EVERY scheduled pod that mounts a claim
# as its user — a pod in a terminal phase included (an eviction, an OOM kill
# the GC never collected); only a pod already shut down (deletionTimestamp
# with grace 0) is ignored. So a dead pod blocks the claim's deletion for as
# long as it exists (2026-10-07, the third real run of this phase). Once no
# live pod is left, everything still carrying the label is dead: remove it.
reset_dead_pods() { # reset_dead_pods <deployment> <pvc>
  step "reset-dead-pods-$1" "dead $1 pods removed (a dead pod still counts as a user of $2)" \
    "${K[@]}" delete pods -l "app=$1" --ignore-not-found --wait=true --timeout=120s
}

# A store's deployment and claim, from its manifest, running on an empty volume.
reset_recreate_store() { # reset_recreate_store <deployment> <pvc> <manifest>
  local dep="$1" pvc="$2" manifest="$3"
  step "reset-recreate-${dep}" "$manifest re-applied (a new, empty $pvc)" \
    "${KROOT[@]}" apply -f "$HERE/$manifest" || return 1
  step "reset-start-${dep}" "$dep scaled to 1" \
    "${K[@]}" scale "deploy/$dep" --replicas=1 || return 1
  step "reset-rollout-${dep}" "$dep rolled out on the empty volume" \
    "${K[@]}" rollout status "deploy/$dep" --timeout=600s || return 1
}

# One store: stop its pod, delete the volume, recreate both from the manifest.
reset_store() { # reset_store <deployment> <pvc> <manifest>
  local dep="$1" pvc="$2" manifest="$3" pv
  pv="$("${K[@]}" get pvc "$pvc" -o jsonpath='{.spec.volumeName}' 2>/dev/null)"
  step "reset-stop-${dep}" "$dep scaled to 0" \
    "${K[@]}" scale "deploy/$dep" --replicas=0 || return 1
  # Only a LIVE pod holds the volume. A pod in a terminal phase (Failed,
  # Succeeded — an eviction, an OOM kill the GC never collected) keeps the
  # label and never leaves on a scale-down; waiting for it would never end
  # (2026-10-06, the first real run of this phase). pvc-protection ignores
  # those too, so the delete below does not wait on them either.
  if wait_gone 180 "${K[@]}" get pods -l "app=$dep" \
       --field-selector 'status.phase!=Succeeded,status.phase!=Failed'; then
    pass "reset-drained-${dep}" "no live $dep pod is left holding $pvc"
  else
    fail "reset-drained-${dep}" "a $dep pod is still live after 180s — $pvc was NOT deleted. Listed: $("${K[@]}" get pods -l "app=$dep" -o wide 2>&1 | tr '\n' ';'). Look at: sudo k3s kubectl -n $NS describe pod -l app=$dep"
    return 1
  fi
  reset_dead_pods "$dep" "$pvc" || return 1
  step "reset-wipe-${pvc}" "$pvc deleted" \
    "${K[@]}" delete pvc "$pvc" --ignore-not-found --timeout=180s || return 1
  # local-path removes the directory when the PV goes (reclaimPolicy Delete).
  # A PV that outlives its claim is a Retain policy: the new claim still gets
  # a new, empty volume, and the old data is left on the node.
  if [[ -n "$pv" ]]; then
    if wait_gone 180 "${KROOT[@]}" get "pv/$pv"; then
      pass "reset-released-${pvc}" "the volume behind $pvc ($pv) is gone"
    else
      warn "reset-released-${pvc}" "the volume $pv still exists after 180s — its data is still on the node (a Retain reclaim policy?). The store below starts on a NEW volume regardless; delete the old one by hand: sudo k3s kubectl delete pv $pv"
    fi
  fi
  reset_recreate_store "$dep" "$pvc" "$manifest"
}

# The proof that both stores are the first-run ones: the schema loaded and
# seeded a roster, the mail ledger is empty, the graph has no node. Neo4j
# answers bolt a little after its pod reports ready, hence the short poll.
reset_assert_fresh() {
  local roster ledger nodes deadline
  roster="$("${K[@]}" exec deploy/cc-postgres -- psql -qtAX -U central_command -d central_command \
             -c "select count(*) from agent where role <> ''" 2>/dev/null)"
  ledger="$("${K[@]}" exec deploy/cc-postgres -- psql -qtAX -U central_command -d central_command \
             -c 'select count(*) from work_item' 2>/dev/null)"
  if [[ "$roster" =~ ^[0-9]+$ ]] && (( roster > 0 )) && [[ "$ledger" == 0 ]]; then
    pass "reset-spine-fresh" "the spine is a first-run database (schema loaded, ${roster} seeded agents, empty mail ledger)"
  else
    fail "reset-spine-fresh" "the spine does not look like a first-run database (seeded agents: '${roster}', mail ledger rows: '${ledger}') — schema.sql loads only on an empty volume; look at: sudo k3s kubectl -n $NS logs deploy/cc-postgres"
  fi
  deadline=$(( SECONDS + 120 ))
  while :; do
    nodes="$("${K[@]}" exec deploy/cc-neo4j -- cypher-shell -u neo4j -p "${NEO4J_PASSWORD:-}" \
              --format plain 'match (n) return count(n)' 2>/dev/null | tail -1)"
    [[ "$nodes" =~ ^[0-9]+$ ]] && break
    (( SECONDS < deadline )) || break
    sleep 5
  done
  if [[ "$nodes" == 0 ]]; then
    pass "reset-graph-fresh" "the graph is empty and answers with the configured password"
  else
    fail "reset-graph-fresh" "the graph did not answer as an empty store (node count: '${nodes}') — look at: sudo k3s kubectl -n $NS logs deploy/cc-neo4j"
  fi
}

phase_reset() {
  local bdir="${CC_BACKUP_DIR:-$HOME/cc-backups}" row dep pvc manifest

  if (( ! RESET_CONFIRM )); then
    note "reset would, in this order:"
    note "  1. run deploy/k3s/backup.sh and link its dump set into"
    note "     $bdir/keep-pre-reset-<stamp>/ (kept past the nightly retention)"
    note "  2. stop ${RESET_UNITS[*]}"
    note "  3. delete what agents deployed: MCP servers (cc-mcp), sandbox jobs (cc-sandbox),"
    note "     untracked folders under servers/ (moved into the keep folder)"
    for row in "${RESET_STORES[@]}"; do
      IFS='|' read -r dep pvc manifest <<<"$row"
      note "  4. DELETE the volume ${pvc} and recreate ${dep} empty"
    done
    if (( RESET_KEEP_ENV )); then
      note "  5. keep the app's .env and web/.env (--keep-env)"
    else
      note "  5. move the app's .env and web/.env into the keep folder (--keep-env keeps them)"
    fi
    note "It keeps: the cluster, the images, the LiteLLM and n8n databases, deploy/pi/.env, the logs."
    useraction "reset-confirm" "nothing was changed. reset deletes this deployment's spine database and knowledge graph after backing them up (the plan is on stderr); to do it, re-run: ./deploy/k3s/setup.sh reset --confirm-wipe"
    return 0
  fi

  load_env || return 1

  # A store a previous reset run stopped (it scales to 0 before it deletes,
  # and a failure between the two leaves it there) cannot be dumped. Bring it
  # back first, so a re-run after a partial failure can take its backup.
  local replicas deleting
  for row in "${RESET_STORES[@]}"; do
    IFS='|' read -r dep pvc manifest <<<"$row"
    # A claim a previous run asked to delete and that is still here is held by
    # a dead pod (see reset_dead_pods); no pod can start on it, so it cannot
    # be dumped either. Finish that deletion and recreate the store EMPTY: the
    # backup below then dumps an empty store, and the dump worth keeping is
    # the previous run's keep-pre-reset-* folder.
    deleting="$("${K[@]}" get pvc "$pvc" -o jsonpath='{.metadata.deletionTimestamp}' 2>/dev/null)"
    if [[ -n "$deleting" ]]; then
      note "$pvc has been awaiting deletion since $deleting (a previous reset run) — finishing it"
      reset_dead_pods "$dep" "$pvc" || return 1
      if wait_gone 180 "${K[@]}" get pvc "$pvc"; then
        pass "reset-resume-wipe-${pvc}" "$pvc is gone"
      else
        fail "reset-resume-wipe-${pvc}" "$pvc is still awaiting deletion after its dead pods were removed — something else holds it. Look at: sudo k3s kubectl -n $NS describe pvc $pvc"
        return 1
      fi
      reset_recreate_store "$dep" "$pvc" "$manifest" || return 1
      warn "reset-resume-${dep}" "$dep now runs on a NEW, EMPTY volume (a previous run had already deleted $pvc) — the backup below dumps that empty store; the dump worth keeping is the previous run's keep-pre-reset-* folder"
      continue
    fi
    replicas="$("${K[@]}" get "deploy/$dep" -o jsonpath='{.spec.replicas}' 2>/dev/null)"
    [[ "$replicas" == 0 ]] || continue
    note "$dep is at 0 replicas (a previous reset run stopped it) — starting it so it can be backed up"
    step "reset-resume-${dep}" "$dep scaled back to 1 for the backup" \
      "${K[@]}" scale "deploy/$dep" --replicas=1 || return 1
    step "reset-resume-rollout-${dep}" "$dep running again" \
      "${K[@]}" rollout status "deploy/$dep" --timeout=600s || return 1
  done

  # ── 1. the backup, and its dump set out of the retention's reach ──────────
  local stamp keep marker label f
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  keep="$bdir/keep-pre-reset-$stamp"
  marker="$(mktemp)" || { fail "reset-backup" "mktemp failed"; return 1; }
  touch -d '2 seconds ago' "$marker"
  if ! step "reset-backup" "backup.sh dumped all four stores and the keys" \
       sudo env REPO="$REPO_ROOT" CC_BACKUP_DIR="$bdir" "$HERE/backup.sh"; then
    rm -f "$marker"
    note "nothing was stopped or wiped: reset does not proceed without a complete backup."
    return 1
  fi
  sudo install -d -m 700 "$keep" && sudo chown --reference="$bdir" "$keep" \
    || { rm -f "$marker"; fail "reset-keep" "could not create $keep — nothing was wiped"; return 1; }
  for label in central_command litellm n8n neo4j keys; do
    f="$(sudo find "$bdir" -maxdepth 1 -type f -name "${label}_*" -newer "$marker" | sort | tail -1)"
    if [[ -z "$f" ]]; then
      rm -f "$marker"
      fail "reset-keep" "backup.sh exited 0 but left no new ${label}_* file in $bdir — nothing was wiped"
      return 1
    fi
    # A hard link costs no space and survives the prune of the original name.
    sudo ln "$f" "$keep/" 2>/dev/null || sudo cp -p "$f" "$keep/" \
      || { rm -f "$marker"; fail "reset-keep" "could not keep $(basename "$f") in $keep — nothing was wiped"; return 1; }
  done
  rm -f "$marker"
  pass "reset-keep" "this dump set is kept in $keep (outside the nightly retention)"

  # schema.sql reaches the spine through the cc-schema-sql ConfigMap, ONCE, on
  # the empty volume, and nothing applies it at runtime — so the ConfigMap is
  # rebuilt from THIS checkout before the volume goes, or the new spine is
  # born with whatever schema the cluster last held. Still before anything
  # stops: a failure here leaves the deployment running.
  step "reset-schema-configmap" "Secrets and ConfigMaps rebuilt from this checkout (the schema the empty spine will load)" \
    "$HERE/make-secrets.sh" || return 1

  # ── 2. the consumers ───────────────────────────────────────────────────────
  local u
  for u in "${RESET_UNITS[@]}"; do
    if systemctl is-active --quiet "$u"; then
      step "reset-stop-${u}" "$u stopped" sudo systemctl stop "$u" || return 1
    else
      pass "reset-stop-${u}" "$u was not running"
    fi
  done

  # ── 3. what agents deployed — the rows that knew about it are about to go ──
  if "${KROOT[@]}" -n cc-mcp delete deployments,services --all --ignore-not-found --timeout=120s >&2; then
    pass "reset-mcp-servers" "no agent-deployed MCP server is left in cc-mcp"
  else
    warn "reset-mcp-servers" "could not clear cc-mcp — a deployment there is now an orphan the new instance does not know; look at: sudo k3s kubectl -n cc-mcp get deploy,svc"
  fi
  if "${KROOT[@]}" -n cc-sandbox delete jobs --all --ignore-not-found --timeout=120s >&2; then
    pass "reset-sandbox-jobs" "no sandbox job is left in cc-sandbox"
  else
    warn "reset-sandbox-jobs" "could not clear cc-sandbox — look at: sudo k3s kubectl -n cc-sandbox get jobs"
  fi
  # servers/<name>/ arrives only through an approved mcp.sync_source; a folder
  # git does not track is the old instance's. Tracked examples stay.
  # Without git there is no telling the two apart, so nothing is moved.
  local d moved=0
  if git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    for d in "$REPO_ROOT"/servers/*/; do
      [[ -d "$d" ]] || continue
      d="${d%/}"
      [[ -n "$(git -C "$REPO_ROOT" ls-files -- "servers/${d##*/}" 2>/dev/null | head -1)" ]] && continue
      sudo install -d -m 700 "$keep/servers" && sudo mv "$d" "$keep/servers/" && moved=$((moved+1)) \
        || warn "reset-mcp-sources" "could not move $d into $keep/servers/ — move it by hand before an agent syncs a server of that name"
    done
    pass "reset-mcp-sources" "${moved} synced MCP source folder(s) moved into the keep folder"
  else
    warn "reset-mcp-sources" "this checkout is not a git work tree, so the old instance's synced folders under servers/ cannot be told from the shipped examples — none was moved; remove the old instance's by hand"
  fi

  # ── 4. the two stores ──────────────────────────────────────────────────────
  for row in "${RESET_STORES[@]}"; do
    IFS='|' read -r dep pvc manifest <<<"$row"
    reset_store "$dep" "$pvc" "$manifest" || return 1
  done
  reset_assert_fresh
  (( FAILS )) && return 1

  # ── 5. the app's answers ───────────────────────────────────────────────────
  if (( RESET_KEEP_ENV )); then
    pass "reset-env" "the app's .env and web/.env were kept (--keep-env)"
  else
    local src dst
    sudo install -d -m 700 "$keep/env" && sudo chown --reference="$bdir" "$keep/env" \
      || { fail "reset-env" "could not create $keep/env — the env files were left in place"; return 1; }
    for row in "$APP_ENV|root.env" "$REPO_ROOT/web/.env|web.env"; do
      src="${row%%|*}"; dst="$keep/env/${row##*|}"
      [[ -f "$src" ]] || continue
      sudo mv "$src" "$dst" || { fail "reset-env" "could not move $src to $dst"; return 1; }
    done
    pass "reset-env" "the app's .env and web/.env moved to $keep/env/ — the next setup run starts from .env.example"
  fi

  # Outside the repo AND the cluster, so nothing above reaches it, and it beats
  # .env: a dry_run pin here would simulate every approval on the new instance.
  local dropins
  dropins="$(ls /etc/systemd/system/cc-uvicorn.service.d/ 2>/dev/null | tr '\n' ' ')"
  if [[ -n "${dropins// /}" ]]; then
    warn "reset-unit-dropins" "cc-uvicorn still has systemd drop-ins from the old instance (${dropins% }) in /etc/systemd/system/cc-uvicorn.service.d/ — they override the app's .env; read them and remove any you do not mean to carry over"
  fi

  note ""
  note "The instance is gone and the app is stopped. To install on the empty stores:"
  if (( ! RESET_KEEP_ENV )); then
    note "  (optional, to give your answers BEFORE first boot)  cp .env.example .env && chmod 600 .env,"
    note "  then fill what the installer does not ask: CC_JIRA_* / CC_CONFLUENCE_*, CC_BACKLOG_CUTOFF_DATE."
    note "  The old values are in $keep/env/root.env."
  fi
  note "    ./deploy/k3s/setup.sh --clean-install"
  note "The pre-reset dump set and its restore keys: $keep/"
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
       ./deploy/k3s/setup.sh reset [--confirm-wipe] [--keep-env]

  no argument     runs validate -> preflight -> llm -> stack -> app -> verify,
                  stopping at the first phase that hard-fails or needs you
  --clean-install passed through to verify.sh (verify/status phases): section B
                  asserts the stores are initialized instead of asserting
                  migrated instance data. Use it on a fresh install.
  reset           DESTRUCTIVE and never part of the no-argument run: backs up
                  all four stores, then deletes the spine database and the
                  knowledge graph and recreates both empty. Keeps the cluster,
                  the LiteLLM and n8n databases and deploy/pi/.env. Without
                  --confirm-wipe it only prints the plan. --keep-env keeps the
                  app's .env and web/.env (default: moved beside the backup).
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
      --confirm-wipe)  RESET_CONFIRM=1 ;;
      --keep-env)      RESET_KEEP_ENV=1 ;;
      -h|--help|help)  usage; exit 0 ;;
      -*)              usage; exit 1 ;;
      *)               cmd="$a" ;;
    esac
  done

  logline "run start: ./setup.sh $*"
  case "$cmd" in
    validate|preflight|llm|stack|app|verify|status|diagnose|reset)
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
