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
#     configure   ASK the operator every question in questions.tsv that .env
#                 does not answer yet (one schema, read here and by `check`),
#                 print the diff, write ONLY .env, then generate the
#                 credentials. The ONE command that creates .env from
#                 .env.example. Fail-closed: no TTY (or --non-interactive) and
#                 it asks nothing, listing the required keys at exit 3.
#                 Design record 2026-09-23, D6.
#     check       EVERYTHING that can be checked without changing anything:
#                 the answer file, this host, the podman machine's current
#                 state, every image ref against its registry, the package
#                 indexes, the UPSTREAM LLM probed from here WHEN .env declares
#                 it (otherwise the catalog is the LiteLLM UI's and the llm
#                 phase pauses for it), the compose
#                 render, Jira/Confluence probed from here when .env configures
#                 them, the speech models' source. Nine sections, one table,
#                 `./setup.sh check --list` names them. It is the GATE: the
#                 full run starts with it and refuses to go on past a FAIL or a
#                 USERACTION (WARN needs --accept-warnings or an interactive
#                 yes). Writes nothing but CC_STATE_DIR and CC_EMBED_DIM in
#                 .env. Design record 2026-09-23, D5.
#     validate    offline check of .env (the repo-root one — THE answer file
#                 since v2.42.0; deploy/single/.env is retired). No side
#                 effects; `check`'s answers + compose sections ARE this phase.
#     preflight   named environment checks. No side effects; `check`'s host and
#                 machine sections ARE this phase.
#     machine     write the podman MACHINE from .env — the CA into its trust
#                 store, the registries mirror/insecure drop-in, the proxy
#                 drop-in. A no-op where there is no machine (bare Linux).
#                 `./setup.sh machine --dry-run` reports the diff instead, and
#                 that is what preflight calls.
#     fetch       acquire every dependency (public or mirror) — the only phase
#                 that needs the network; stops for the operator per artifact.
#     llm         secrets + LiteLLM up + probe its aliases + MEASURE the
#                 embedding dimension into .env. THE ONE DELIBERATE STOP in the
#                 full run (operator's decision): unless .env declares the
#                 upstream, this phase creates the alias skeletons and exits 3
#                 so the provider details are entered in the LiteLLM UI — the
#                 catalog lives in LiteLLM's database, not in .env, and that is
#                 the same methodology the k3s profile uses. Re-run to continue.
#     stack       assert the local images + bring the core stack up (compose)
#     app         venv, editable install, the derived .env values, mint the
#                 spine's virtual key, cockpit build
#     verify      verify.sh (deployed) then live, then the capability manifest
#     test        the pytest gate, via the venv (no activation stumbles)
#     boot        elicit the operator's name (once), start the API, the sandbox
#                 runner and the cockpit server — through systemd --user units
#                 where a user manager answers (D6) — assert the roster hired,
#                 import the bundled skills  (counterpart: ./setup.sh stop)
#     demo        fixture email -> triage -> YOUR approval in the cockpit ->
#                 a real execution + provenance verified on the event log
#
#     stop        stop the three processes `boot` started, and prove each port free
#     status      the LEDGER table plus the postconditions. Nothing mutating
#     report      write <state>/report-<stamp>.txt — the one file a repository
#                 DEFECT travels in (`diagnose` is an alias for it)
#     acquire     INTERNAL to `update.sh apply`, never an operator command: the
#                 NEW release's fetch + a catalog probe, run from a staged
#                 worktree BEFORE the merge (CC_STAGED_FOR; see main)
#
#   THE LEDGER (2026-10-01 design record, D1/D2/D3). `deploy/single/steps.tsv`
#   declares every step of the install ONCE, in run order, with the .env keys
#   that shape it and a `probe` that reads whether its effect is present.
#   `<state>/ledger.tsv` records which steps are done, at which release, with
#   which input fingerprint. Three consequences, and they are the whole point:
#     * a phase whose prerequisites are not `done` is REFUSED without running,
#       naming the first one — `./setup.sh boot` after a failed `app` was
#       accepted until now, which is exactly the 2026-10-01 work-site state (an
#       empty CC_LLM_API_KEY, eight blank Systems links, a green `verify`);
#     * a phase that returns 0 while one of its rows probes false is a FAIL
#       naming the row — that is "the step after the one that failed never ran"
#       turning from invisible into a line;
#     * `./setup.sh` with no argument is THE command and the only recovery
#       there is: it skips what is still true and resumes at the first step
#       that is not. `<phase>` stays as a DEVELOPER form that refuses out of
#       order; there is no --force, because the escape hatch is the defect.
#
#   THE LOOP the operator runs: `configure` -> `check` -> triage (edit .env) ->
#   `check` -> ... -> `all`. configure asks, check proves, and the only file
#   either of them writes is the answer file. `./setup.sh` is then the one
#   command the operator runs, and runs again.
#
#   No argument = check, then the phases that CHANGE something, in order —
#   zero to a working, human-approved demo in one command (2026-08-28), stopping
#   at the first hard failure or gate. `validate` and `preflight` stay callable
#   on their own; the full run reaches them through `check`, which composes them
#   and adds what they never covered (design record 2026-09-23, D5). Every step
#   is still idempotent and every row still probes REALITY — but "there is no
#   state file" is RETIRED (2026-10-01): three reality probes and a handful of
#   `.env` placeholder tests were the whole record of what had completed, so a
#   mid-function abort abandoned every later step in its phase silently. The
#   ledger is that record now, and RESUME IS STILL RE-RUN — of the one command.
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
#
# THE INSTALL this run acts on, decided in this ONE place: normally the checkout
# this script sits in. The exception is the STAGED ACQUISITION (2026-10-01
# design record, D5): `update.sh apply` checks the NEW release out into a
# worktree under the state dir and runs THAT release's `setup.sh acquire`
# BEFORE it merges, with CC_STAGED_FOR naming the deployment's root. Such a run
# reads the DEPLOYMENT's answer file and state dir (the staged tree has no .env,
# and must not grow one), writes no ledger row (the staged tree is not the
# install, and its rows would carry the new version before the merge), is not
# held to the ledger's order (acquisition deploys nothing), and refuses every
# command but `acquire`. CC_STAGED_FOR is internal plumbing handed from
# update.sh to this one child, like CC_RUN_LOCK_PID — never an answer.
INSTALL_ROOT="$REPO_ROOT"
STAGED=0
if [[ -n "${CC_STAGED_FOR:-}" ]]; then
  INSTALL_ROOT="$CC_STAGED_FOR"
  STAGED=1
fi
ENV_FILE="$INSTALL_ROOT/.env"
# The template `configure` copies when there is no answer file yet. It is the
# ONE place .env is created (v2.45.0): check and the full run say "run
# configure" instead, because a command that silently invents an answer file is
# a command that can report PASS on a file nobody filled in.
ENV_TEMPLATE="$REPO_ROOT/.env.example"
# shellcheck source=../env-lib.sh
. "$REPO_ROOT/deploy/env-lib.sh"
# The question SCHEMA's validators and reader (design record D6). questions.tsv
# is data; this is the code that reads it, shared by `configure` (which ASKS)
# and `check`'s answers section (which VALIDATES).
# shellcheck source=questions-lib.sh
. "$HERE/questions-lib.sh"
QUESTIONS="$HERE/questions.tsv"
# The podman MACHINE's configuration, rendered as text. Pure functions, so the
# decisions are unit-tested on Linux even though the writer can only run where
# a machine exists (Windows/macOS) — design record D4.
# shellcheck source=machine-lib.sh
. "$HERE/machine-lib.sh"
# THE PROCESS, as data (2026-10-01 design record, D1). steps.tsv declares every
# step of the install once, in run order; ledger-lib.sh is the code that reads
# it, fingerprints a step's inputs and keeps the ledger. Same split as
# questions.tsv/questions-lib.sh, and for the same reason: a process defined in
# one place cannot drift from the process that runs.
# shellcheck source=ledger-lib.sh
. "$HERE/ledger-lib.sh"
STEPS="$HERE/steps.tsv"
# What `boot` writes to keep its three host processes running (2026-10-01
# design record, D6) — the systemd --user units and the Windows logon wrapper,
# rendered as text by pure functions so the text is tested where neither a
# user manager nor cmd.exe may be touched.
# shellcheck source=supervise-lib.sh
. "$HERE/supervise-lib.sh"
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
# --no-project is load-bearing: without it `uv run` DISCOVERS pyproject.toml
# from the cwd (these scripts run inside the checkout), SYNCS the project —
# a universal resolution of every platform, which a Windows mirror holding
# only Windows wheels cannot satisfy — and writes uv.lock into the tree. On
# the Windows testbed (2026-09-25, CC_AIRGAP=1 against a local mirror) every
# `$PY` call in the llm section died with "No solution found ... uvloop" and
# the endpoint looked empty. The fallback is an INTERPRETER, not a project.
[[ -n "$PY" ]] || PY="uv run --no-project --python 3.12 python"
# $PY may be multiple words (the uv fallback) — always invoke it unquoted.

# ── output protocol ─────────────────────────────────────────────────────────
# Exit taxonomy (2026-08-27 contract): 0 clean · 1 hard failure · 2 warnings ·
# 3 USER ACTION REQUIRED — the run stopped deliberately for the operator; the
# last USERACTION line says what for. A conductor (human or agent) re-runs the
# phase after acting; every phase is idempotent so that always converges.
FAILS=0; WARNS=0; ACTIONS=0; PASSES=0
CURPHASE=""
# Both resolved by init_state() before the first log line — the state
# directory is where every generated file goes (D7), and it is not knowable
# until .env has been consulted for CC_STATE_DIR.
STATE_DIR=""
LOGFILE=""
LEDGER=""
# The last FAIL/USERACTION message per CHECK-NAME, which is the ledger's
# `reason` column (2026-10-01 design record, D2: "the last FAIL/USERACTION
# message, verbatim"). Collected here rather than parsed back out of the log,
# because the protocol functions are the one place every message passes
# through. Safe to record: by the same protocol a message refers to a key by
# NAME and never carries its value.
#
# STEP_SAID says WHICH of the two it was (v2.56.0): a row whose own check-name
# printed a FAIL in this run is recorded `failed`, and one that printed a
# USERACTION `gate`, WHATEVER its probe reads — a probe is a cheap read of one
# effect, and `verify-live` FAILing inside verify.sh while its probe (the spine
# key answers /v1/models) held is how a failed verify was written down `done`
# and the next ./setup.sh skipped it. FAIL outranks USERACTION here exactly as
# it does in the exit code (D5): a later USERACTION never overwrites a FAIL.
declare -A STEP_MSG=()
declare -A STEP_SAID=()
logline() { printf '%s %s %s\n' "$(date -u +%FT%TZ)" "${CURPHASE:-run}" "$*" >>"$LOGFILE" 2>/dev/null || true; }
pass() { printf 'PASS %s: %s\n' "$1" "$2"; PASSES=$((PASSES+1)); logline "PASS $1: $2"; }
warn() { printf 'WARN %s: %s\n' "$1" "$2"; WARNS=$((WARNS+1)); logline "WARN $1: $2"; }
fail() { printf 'FAIL %s: %s\n' "$1" "$2"; FAILS=$((FAILS+1)); STEP_MSG["$1"]="$2"; STEP_SAID["$1"]=FAIL; logline "FAIL $1: $2"; }
useraction() { printf 'USERACTION %s: %s\n' "$1" "$2"; ACTIONS=$((ACTIONS+1)); [[ "${STEP_SAID[$1]:-}" == FAIL ]] || { STEP_MSG["$1"]="$2"; STEP_SAID["$1"]=USERACTION; }; logline "USERACTION $1: $2"; }
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
  # INSTALL_ROOT, not REPO_ROOT: the state dir's default name hashes the
  # install's path, and a STAGED run must land in the deployment's (the run
  # lock it nests under lives there) rather than one named after the worktree.
  STATE_DIR="$(cc_state_dir "$ENV_FILE" "$INSTALL_ROOT")" \
    || STATE_DIR="${TMPDIR:-/tmp}/central-command-state"
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  LOGFILE="$STATE_DIR/setup-log.txt"
  # THE LEDGER (D2), created EMPTY by every command — configure and check
  # included. Its existence is what says "this tree is a deployment", so it may
  # not wait for the first mutating phase to appear. A STAGED run never creates
  # it: it leaves the deployment's ledger exactly as it found it, absence
  # included (a pre-ledger install's has none yet).
  LEDGER="$STATE_DIR/ledger.tsv"
  if (( ! STAGED )) && [[ ! -f "$LEDGER" ]]; then
    printf '%s\n' "$LEDGER_HEADER" >"$LEDGER" 2>/dev/null || true
    chmod 600 "$LEDGER" 2>/dev/null || true
  fi
}

# ── the tree is pristine, or the driver refuses (D10) ───────────────────────
# The PROBE for check/tree-pristine, and the guard load_env runs at the top of
# every MUTATING phase. A tree with no git baseline passes here and is reported
# ONCE, by check's own row, as the USERACTION that names ./update.sh init —
# otherwise a fresh zip install would be unable to run a single phase before
# somebody had created a baseline it has no way to create itself.
p_tree_pristine() {
  local rc=0
  cc_tree_diff "$REPO_ROOT" || rc=$?
  (( rc == 1 )) && return 1
  return 0
}

# Which phases CHANGE something. Each re-tests the tree before it reads .env,
# so a modified release never reaches a mutation (and `check`, `validate`,
# `status` and `report` stay readable on a tree somebody is mid-way through
# repairing).
MUTATING_PHASES=" machine fetch llm stack app verify test boot demo "
MUTATING=0

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
      # The trailing `*` is podman's DEFAULT MARKER, not part of the name:
      # `podman machine list --format '{{.Name}}'` prints
      # `podman-machine-default*` for the default machine, and every
      # `podman machine ssh <name>` with that star in it fails — podman does not
      # match the name, takes the star-suffixed word as the COMMAND instead, and
      # every machine probe reports "does not answer 'podman machine ssh'" on a
      # machine that is running fine. Found on the 2026-09-24 Windows run, where
      # it FAILed check's whole machine section (podman 5.8.3). The default
      # machine is the normal case on Windows and macOS, so this is not an edge.
      local n; n="$(podman machine list --format '{{.Name}}' 2>/dev/null | head -1 | tr -d '\r')"
      n="${n%\*}"
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
#
# Its STDERR goes to the LOG, never to the terminal and never away (2026-10-01
# design record, D5): it used to be discarded, so a write into the machine that
# failed — `sudo tee` refused, an update-ca-trust that errored — left no trace
# anywhere, and the phase's FAIL line had nothing behind it. stdout is still the
# caller's (several read a file's content back through it). What can reach the
# log, checked against every caller: the commands carry paths, a registry HOST
# and fixed tool invocations, and no credential; the one PROXY VALUE that
# travels into the machine (the containers.conf drop-in) goes on machine_sh_stdin's
# STDIN, never in a command. machine_sh_log still strips CC_PROXY's value — and
# its host, which a curl connect error names — and any URL userinfo, because a
# tool inside the machine may print what its own environment holds.
machine_sh_log() { # machine_sh_log <command> <exit-code> <stderr-text>
  local err="$3" px="${CC_PROXY:-}" pxh
  [[ -n "${err//[[:space:]]/}" ]] || return 0
  if [[ -n "$px" ]]; then
    err="${err//"$px"/[REDACTED:CC_PROXY]}"
    pxh="${px#*://}"; pxh="${pxh#*@}"; pxh="${pxh%%/*}"
    [[ -n "$pxh" ]] && err="${err//"$pxh"/[REDACTED:CC_PROXY]}"
    pxh="${pxh%%:*}"
    [[ -n "$pxh" ]] && err="${err//"$pxh"/[REDACTED:CC_PROXY]}"
  fi
  err="$(printf '%s' "$err" | sed -E 's#([A-Za-z][A-Za-z0-9+.-]*://)[^/@[:space:]]+@#\1[REDACTED:url-userinfo]@#g' | tr '\r\n' '  ')"
  logline "machine-ssh: \`$1\` exited $2; its stderr: $err"
}

machine_sh() { # machine_sh <shell-command>
  local m err rc=0; m="$(machine_name)"
  [[ -n "$m" ]] || return 1
  # stderr into $err, stdout to fd 3 = this function's own stdout.
  { err="$(podman machine ssh "$m" -- "$1" </dev/null 2>&1 1>&3 3>&-)" || rc=$?; } 3>&1
  machine_sh_log "$1" "$rc" "$err"
  return "$rc"
}
# Same, with stdin piped in — how a file gets INTO the machine without a share.
machine_sh_stdin() { # machine_sh_stdin <shell-command> < file
  local m err rc=0; m="$(machine_name)"
  [[ -n "$m" ]] || return 1
  { err="$(podman machine ssh "$m" -- "$1" 2>&1 1>&3 3>&-)" || rc=$?; } 3>&1
  machine_sh_log "$1" "$rc" "$err"
  return "$rc"
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
# The two DERIVED variables that carry a CONTAINER-side POSIX path into the
# compose render (see the CA block in load_env). Under Git Bash they must be
# excluded from MSYS's path conversion: MSYS rewrites POSIX-looking values in
# the environment of a NATIVE Windows process, and `podman-compose.exe` is one.
# Measured on the 2026-09-24 Windows run against podman-compose under podman
# 5.8.3:
#   CC_CA_BUNDLE_MOUNT_SRC=/dev/null              -> `nul`
#       => RuntimeError: volume [nul] not defined in top level  (compose render
#          FAILS on every Windows install, with or without a CA)
#   CC_CA_BUNDLE_MOUNT_SRC=/etc/pki/.../cc-ca.pem -> C:/Program Files/Git/etc/pki/.../cc-ca.pem
#       => ValueError: could not parse mount  (and the same rewrite would have
#          put a Windows path into SSL_CERT_FILE inside a Linux container)
# MSYS2_ENV_CONV_EXCL is the documented opt-out and fixed both cases exactly.
# Harmless everywhere else: nothing but MSYS reads it.
COMPOSE_ENV_CONV_EXCL="CC_CA_BUNDLE_MOUNT_SRC;CC_CA_BUNDLE_IN_CONTAINER"

compose() { # compose <args...>
  compose_detect || { fail "compose" "no compose provider — install podman-compose (or the docker compose plugin)"; return 1; }
  MSYS2_ENV_CONV_EXCL="${MSYS2_ENV_CONV_EXCL:+${MSYS2_ENV_CONV_EXCL};}${COMPOSE_ENV_CONV_EXCL}" \
    "${COMPOSE_BIN[@]}" --env-file "$ENV_FILE" -f "$HERE/compose.yaml" "$@"
}
# CC_ENABLE_* -> --profile flags, into the global PROFILE_FLAGS. The sandbox is
# deliberately absent: it has no container here (its containers are created on
# demand by the runner, a host process), so its only deployable artifact is
# the image.
PROFILE_FLAGS=()
compose_profile_flags() {
  local prof
  PROFILE_FLAGS=()
  # compose_profile_on is the ONE flag->profile mapping: the image catch-up
  # asks it which services are enabled, so the two cannot disagree.
  for prof in n8n crawler speech; do
    compose_profile_on "$prof" && PROFILE_FLAGS+=(--profile "$prof")
  done
  return 0
}

# ── .env helpers ────────────────────────────────────────────────────────────
load_env() {
  init_state
  # D10, before a mutating phase reads a single answer: this deployment
  # configures through .env and never rewrites any part of Central Command, so
  # a tree that differs from the release it claims to be does not get to
  # mutate anything. There is no bypass variable for this one —
  # CC_SETUP_UNLEDGERED (D3) does not cover it. A STAGED run is exempt, and
  # not by accident: its tree is a sparse worktree of `upstream` that mutates no
  # install, and the DEPLOYMENT's tree was put through this same test
  # (cc_tree_diff) by update.sh's own gate before anything was staged.
  if (( MUTATING && ! STAGED )) && ! p_tree_pristine; then
    fail "tree-pristine" "this deployment DIFFERS from the release it claims to be, so the $CURPHASE phase will not run: $TREE_DIFF_PATHS. An install configures through .env and never rewrites any part of Central Command (2026-10-01 design record, D10) — restore those paths (git restore -- <path>), and carry the change back to a development session as a finding: ./setup.sh report writes one"
    return 1
  fi
  # USERACTION, not FAIL: "run configure" is the operator's move, which is what
  # exit 3 means in this protocol — and it is exactly the case `check_gate`'s
  # exit-3 text already talks the operator through. Reporting it as a hard
  # failure (exit 1) sent them to "fix the FAIL lines above" for a file that
  # simply has not been created yet (seen on the 2026-09-24 Windows run).
  if [[ ! -f "$ENV_FILE" ]]; then
    useraction "answer-file" "$ENV_FILE not found — run ./setup.sh configure (it creates it from .env.example and asks what is missing; no other command creates it). There is ONE answer file, the repo-root .env, and deploy/single/.env is not it"
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
    local tls_consumers="curl, uv, pip, npm, node and git on this host; podman pulls and the three image builds (--tls-verify=false); the podman machine's registries drop-in; LiteLLM's outbound calls (SSL_VERIFY=False). NOT the speech engine — Hugging Face's client has no insecure switch, so that one needs CC_CA_BUNDLE or pre-placed snapshots"
    # load_env runs once per PHASE, so this fires on every one of them without
    # the once-gate — that repetition (up to five WARN lines per `check`) is
    # F25. cc_tls_insecure_warn_once prints (and logs) it exactly once per run,
    # here and in every child script this run execs (deploy/env-lib.sh).
    logline "WARN tls-insecure: $(cc_tls_insecure_warn_text "$tls_consumers")"
    cc_tls_insecure_warn_once "$tls_consumers" && WARNS=$((WARNS+1))
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
# THE QUESTION SCHEMA (design record D6) — read by configure AND by check
# ─────────────────────────────────────────────────────────────────────────────
# questions.tsv declares every question ONCE: key, group, prompt, default,
# required, validator, `when` guard, secret. Two commands read it, which is the
# whole point — `configure` asks and `check` validates, so a new dependency is a
# new ROW instead of a new prompt in one place and a new check in another.
#
# Q_ANS  is "the answers so far": the value currently in .env, replaced by an
#        answer as configure collects one. `when` is evaluated against it.
# Q_DEF  is the schema's default per key, which is what a blank answer takes and
#        what a `when` clause falls back to for a key .env leaves blank (the
#        CC_ENABLE_* flags are the case that matters: load_env defaults them,
#        and the speech rows must be asked on a file that never named them).
declare -A Q_ANS=()
declare -A Q_DEF=()
declare -A Q_REQ=()

# The getter q_when_holds is handed: current answer, else the schema default.
q_current() { # q_current <key>
  local k="$1"
  if [[ -n "${Q_ANS[$k]:-}" ]]; then printf '%s' "${Q_ANS[$k]}"
  else printf '%s' "${Q_DEF[$k]:-}"; fi
}

# Load the schema and the current answers. `@state` is the one dynamic default:
# the state directory init_state already resolved, shown so the operator can
# override it rather than having to guess what the default would have been.
q_schema_load() {
  [[ -f "$QUESTIONS" ]] || { fail "questions" "$QUESTIONS is missing — it is release content, so re-extract the release"; return 1; }
  Q_ANS=(); Q_DEF=(); Q_REQ=()
  local row key def cur
  while IFS= read -r row; do
    key="$(q_field "$row" 1)"
    def="$(q_field "$row" 4)"
    [[ "$def" == "-" ]] && def=""
    [[ "$def" == "@state" ]] && def="$STATE_DIR"
    cur="$(q_unquote "$(get_kv "$ENV_FILE" "$key")")"
    is_placeholder "$cur" && cur=""
    Q_DEF["$key"]="$def"
    Q_ANS["$key"]="$cur"
    Q_REQ["$key"]="$(q_field "$row" 5)"
  done < <(q_rows "$QUESTIONS")
  return 0
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
# COMMAND: configure — ask what is missing, write nothing but .env
# ─────────────────────────────────────────────────────────────────────────────
# Plain `read -rp`: gum and whiptail both break under Git Bash's mintty pty
# (charmbracelet/gum#228), and there is no TUI to fail on a machine where the
# venv does not exist yet.
#
# FAIL-CLOSED (rustup's rule): with no TTY, or with --non-interactive, it never
# prompts and never guesses. It lists every unanswered REQUIRED key as a
# USERACTION and exits 3. That is what makes the answer file a PRESEED file: a
# .env filled in on a connected machine and carried across makes this run print
# nothing but `keep`.
Q_REPLY=""
q_ask() { # q_ask <prompt> <default-display> <default-value> <secret> <validator> <required>
  local prompt="$1" disp="$2" defv="$3" secret="$4" validator="$5" req="$6"
  local tries=0 reply reason
  Q_REPLY=""
  while (( tries < 3 )); do
    tries=$((tries+1))
    reply=""
    if [[ "$secret" == y ]]; then
      # -s so a key never reaches the scrollback; the newline read did not echo.
      read -rsp "$prompt [$disp]: " reply || return 1
      printf '\n' >&2
    else
      read -rp "$prompt [$disp]: " reply || return 1
    fi
    [[ -n "$reply" ]] || reply="$defv"
    # F33 — a PATH answer is normalised before it is validated or stored. On
    # MSYS (Git Bash) `/c/Users/me/ca.pem` is what tab completion produces and
    # what bash reads, and it is REJECTED by every native Windows tool the
    # install drives (curl.exe, podman.exe, the podman machine). cygpath -m
    # gives `C:/Users/me/ca.pem`, which bash, Python and both .exe accept.
    # Identity on Linux; see questions-lib.sh's q_norm_path_answer.
    [[ "$validator" == v_path* ]] && reply="$(q_norm_path_answer "$reply")"
    if [[ -z "$reply" ]]; then
      if [[ "$req" == y ]]; then note "  a value is required here."; continue; fi
      return 0   # blank IS an answer for an optional key
    fi
    if [[ -n "$validator" && "$validator" != "-" ]]; then
      if ! reason="$("$validator" "$reply")"; then
        note "  $reason"
        continue
      fi
    fi
    Q_REPLY="$reply"
    return 0
  done
  note "  three tries — leaving $prompt unanswered."
  return 1
}

cmd_configure() { # cmd_configure <args...>
  local ask_all=0 noninteractive=0 a
  for a in "$@"; do
    case "$a" in
      --all)             ask_all=1 ;;
      --non-interactive) noninteractive=1 ;;
      --accept-warnings) ;;    # harmless here; the driver scans for it globally
      *) note "configure: unknown option '$a'"; usage; return 1 ;;
    esac
  done

  # THE one place the answer file is created. check and the full run refuse to,
  # deliberately: an invented .env is a file whose checks pass and whose answers
  # nobody gave.
  if [[ ! -f "$ENV_FILE" ]]; then
    local tmpl="$ENV_TEMPLATE"
    if [[ ! -f "$tmpl" ]]; then
      fail "answer-file" "neither $ENV_FILE nor .env.example exists — this is not a Central Command checkout"
      return 1
    fi
    cp "$tmpl" "$ENV_FILE" || { fail "answer-file" "could not create $ENV_FILE from .env.example"; return 1; }
    chmod 600 "$ENV_FILE" 2>/dev/null || true
    pass "answer-file" "created $ENV_FILE from .env.example (the ONE answer file)"
  fi

  load_env || return 1
  q_schema_load || return 1

  local interactive=1
  [[ -t 0 ]] || interactive=0
  (( noninteractive )) && interactive=0

  local set_keys=() set_vals=() set_secret=() missing=() group_now=""
  local row key group prompt def req validator when secret val disp defv rc=0
  # The schema is read on fd 3, NOT stdin: `read -rp` below reads stdin, and a
  # `while read < <(...)` loop would feed it the next SCHEMA ROW as the
  # operator's answer (it did, once — every prompt "answered" by a tsv line).
  while IFS= read -r row <&3; do
    key="$(q_field "$row" 1)"
    group="$(q_field "$row" 2)"
    prompt="$(q_field "$row" 3)"
    def="${Q_DEF[$key]}"
    req="$(q_field "$row" 5)"
    validator="$(q_field "$row" 6)"
    when="$(q_field "$row" 7)"
    secret="$(q_field "$row" 8)"

    q_when_holds "$when" q_current || continue
    # `advanced` is the ports group: every default works, so it is asked only
    # when the operator says they want every question.
    [[ "$group" == advanced ]] && (( ! ask_all )) && continue

    # The header goes up BEFORE the keep/ask decision, so a `keep` line lands
    # under the group it belongs to rather than under the previous one.
    if [[ "$group" != "$group_now" ]]; then
      group_now="$group"
      note ""
      note "== $group — $(q_group_blurb "$group")"
    fi

    val="${Q_ANS[$key]}"
    # Is the value currently in .env usable? Three consequences: a valid value is
    # KEPT (this is what makes a carried-in answer file ask nothing), an invalid
    # one is re-asked, and an invalid one is NOT offered back as the default —
    # otherwise pressing Enter would re-accept exactly what check will FAIL on.
    local val_ok=1 val_why=""
    if [[ -n "$val" && -n "$validator" && "$validator" != "-" ]]; then
      val_why="$("$validator" "$val")" || val_ok=0
    fi
    if [[ -n "$val" ]] && (( val_ok )) && (( ! ask_all )); then
      note "  keep   $key"
      continue
    fi

    if (( ! interactive )); then
      # Never prompt, never guess. A required key with no value is the report;
      # a set-but-invalid key is check's FAIL, not something to overwrite here.
      [[ "$req" == y && -z "$val" ]] && missing+=("$key"$'\t'"$prompt")
      continue
    fi

    (( val_ok )) || note "  $key is set to something unusable: $val_why"
    # The default OFFERED: the current value when re-asking, else the schema's.
    defv="$val"; (( val_ok )) || defv=""
    [[ -n "$defv" ]] || defv="$def"
    if [[ "$secret" == y ]]; then
      disp="blank = keep what is there"; [[ -n "$val" ]] || disp="no default"
    else
      disp="$defv"; [[ -n "$disp" ]] || disp="blank"
    fi
    if q_ask "$prompt" "$disp" "$defv" "$secret" "$validator" "$req"; then
      if [[ "$Q_REPLY" == "$val" ]]; then
        note "  keep   $key"
      else
        Q_ANS["$key"]="$Q_REPLY"
        set_keys+=("$key"); set_vals+=("$Q_REPLY"); set_secret+=("$secret")
      fi
    else
      [[ "$req" == y ]] && missing+=("$key"$'\t'"$prompt")
    fi
  done 3< <(q_rows "$QUESTIONS")

  # Fail-closed: report and stop, before writing anything.
  if (( ! interactive )) && (( ${#missing[@]} )); then
    note ""
    local m
    for m in "${missing[@]}"; do
      useraction "${m%%$'\t'*}" "${m#*$'\t'} — set it in .env or run ./setup.sh configure in a terminal"
    done
    local why="stdin is not a terminal"
    (( noninteractive )) && why="--non-interactive was given"
    local hint=""
    case "${OSTYPE:-}$(uname -o 2>/dev/null)" in
      *[Mm][Ss][Yy][Ss]*|*[Cc][Yy][Gg]*)
        hint=" On MSYS/Cygwin (Git Bash under mintty) a non-TTY stdin is the classic symptom of running a script without winpty — try: winpty ./setup.sh configure" ;;
    esac
    note ""
    note "configure asked nothing: $why, and it does not guess. ${#missing[@]} required answer(s) are missing (listed above).${hint}"
    note "next: fill them into $ENV_FILE, or run ./setup.sh configure in a terminal"
    return 3
  fi

  # The diff BEFORE the write, secrets by name only.
  note ""
  if (( ${#set_keys[@]} )); then
    note "configure will write into $ENV_FILE:"
    local i
    for i in "${!set_keys[@]}"; do
      if [[ "${set_secret[$i]}" == y ]]; then
        note "  set    ${set_keys[$i]}=(secret, not printed)"
      else
        note "  set    ${set_keys[$i]}=${set_vals[$i]}"
      fi
    done
    for i in "${!set_keys[@]}"; do
      if ! cc_set_kv "$ENV_FILE" "${set_keys[$i]}" "$(q_quote "${set_vals[$i]}")"; then
        fail "configure" "could not write ${set_keys[$i]} into $ENV_FILE — check its permissions"
        return 1
      fi
    done
    chmod 600 "$ENV_FILE" 2>/dev/null || true
    pass "configure" "${#set_keys[@]} answer(s) written to $ENV_FILE (nothing else was touched)"
  else
    note "configure has nothing to write — every question that applies is already answered."
    pass "configure" "$ENV_FILE already answers every question that applies (a carried-in .env asks nothing)"
  fi

  # The credentials, so .env is COMPLETE before check runs. make-secrets.sh only
  # ever fills a blank — it is what protects the two keys that can never be
  # rotated (CC_LITELLM_SALT_KEY, N8N_ENCRYPTION_KEY).
  note ""
  note "--> $HERE/make-secrets.sh  (fills only the credentials that are still blank)"
  if "$HERE/make-secrets.sh" >&2; then
    pass "configure-secrets" "every credential make-secrets.sh owns is present in .env (it never overwrites one that is set)"
  else
    fail "configure-secrets" "make-secrets.sh failed — see its output on stderr"
    return 1
  fi

  if (( ${#missing[@]} )); then
    note ""
    local m
    for m in "${missing[@]}"; do
      useraction "${m%%$'\t'*}" "${m#*$'\t'} — set it in .env or run ./setup.sh configure in a terminal"
    done
    rc=3
  fi
  note ""
  note "next: ./setup.sh check"
  return $rc
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
  if [[ -n "${CC_CA_BUNDLE:-}" && ( "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ) ]] && command -v certutil >/dev/null 2>&1; then
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
# The credentials make-secrets.sh generates. check never generates one (that is
# a side effect, and the `llm` phase owns it) — a blank one is a WARN naming
# the command that fills it — and since v2.45.0 `./setup.sh configure` runs that
# command itself, so the WARN is what a run that SKIPPED configure looks like.
CC_GENERATED_KEYS=(CC_LLM_PROXY_ADMIN_KEY CC_LITELLM_SALT_KEY LITELLM_POSTGRES_PASSWORD
                   CC_NEO4J_PASSWORD N8N_ENCRYPTION_KEY N8N_DB_PASSWORD
                   CC_EMAIL_FACADE_TOKEN CC_CALENDAR_FACADE_TOKEN
                   CC_SANDBOX_RUNNER_TOKEN)

# The seams whose BLANK value means "the public host is contacted". If every one
# of these names a mirror, a one-certificate bundle is complete by construction:
# nothing public is dialled. One blank is enough to need the public roots too.
PUBLIC_SOURCE_SEAMS=(CC_REGISTRY_DOCKERIO CC_REGISTRY_GHCR CC_REGISTRY_MCR
                        CC_PYPI_INDEX_URL CC_PYTHON_MIRROR CC_NPM_REGISTRY
                        CC_APT_MIRROR CC_HF_ENDPOINT)

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
  # Unreadable is already the schema validator's FAIL; nothing to add here.
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

# Is anything LISTENING on a port this deployment wants to publish? `ss` where
# there is one, `netstat` on Windows, and a bash /dev/tcp connect as the
# fallback that exists everywhere.
port_listener() { # port_listener <port>  -> 0 = occupied
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk 'NR>1{print $4}' | grep -qE "[:.]${port}\$" && return 0
    return 1
  fi
  if command -v netstat >/dev/null 2>&1; then
    netstat -an 2>/dev/null | grep -iE 'listen' | grep -qE "[:.]${port}[^0-9]" && return 0
    return 1
  fi
  (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null && { exec 3<&- 2>/dev/null; return 0; }
  return 1
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
    2) warn "images" "resolved, with substitutions or operator pins — see the WARN lines above; a green ./setup.sh verify is what makes a substitution supported" ;;
    3) useraction "images" "image resolution stopped for you — see the USERACTION line above (the seams are CC_REGISTRY_* and a CC_IMG_<NAME> pin)" ;;
    *) fail "images" "image resolution failed (exit $rc) — the FAIL lines above name the seam per image" ;;
  esac
}

# ── section: indexes ────────────────────────────────────────────────────────
check_indexes() {
  # The PyPI SIMPLE index, probed at a project page rather than the root: a
  # mirror may serve / as a portal and still resolve.
  local pypi="${CC_PYPI_INDEX_URL:-https://pypi.org/simple}"
  if [[ -z "${CC_PYPI_INDEX_URL:-}" && "$CC_AIRGAP" == "1" ]]; then
    warn "index-pypi" "CC_AIRGAP=1 and CC_PYPI_INDEX_URL is unset, so the PUBLIC index is what a resolve would use — set the mirror; the resolution check below is what decides"
  else
    probe_http "index-pypi" "${pypi%/}/pip/" "the PyPI simple index (${pypi})" "CC_PYPI_INDEX_URL"
  fi
  local npm="${CC_NPM_REGISTRY:-https://registry.npmjs.org}"
  probe_http "index-npm" "${npm%/}/npm" "the npm registry (${npm})" "CC_NPM_REGISTRY"

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

# A LOCALLY BUILT image against the tree that is checked out NOW (v2.57.0).
# Its tag is fixed, so presence alone said nothing about a release that changed
# a Dockerfile or its context: the old image kept running. Each build writes
# the hash of its inputs as a label (deploy/env-lib.sh's cc_build_inputs_hash);
# the script itself answers what the hash SHOULD be (`--inputs-hash`, which
# writes nothing and calls no podman), so the inputs have one definition — the
# build script — and this compares. ONE `podman image inspect`, which fails
# when the tag is absent and prints the label otherwise; nothing is mutated,
# because the fetch/stack probes call this, and every run's PLAN evaluates
# them. Prints one word:
#   current     tag present, label equals this tree's inputs hash
#   absent      no such tag in local storage
#   unlabelled  tag present, no label — built by a release before v2.57.0
#   stale       tag present, label differs — a changed Dockerfile/context/arg
#               (or a ROLLBACK: the old tree's hash differs from the new image)
#   unknown     the inputs hash could not be computed (an unreadable CA, a
#               missing context) — never "current"; the build names the cause
local_image_state() { # local_image_state <ref> <build-script>
  local have want
  if ! have="$(podman image inspect --format "{{index .Config.Labels \"$(cc_build_inputs_label)\"}}" "$1" 2>/dev/null </dev/null)"; then
    printf 'absent'; return 0
  fi
  have="${have//$'\r'/}"; have="${have%%$'\n'*}"
  # A missing key prints the template's zero value; some podman versions spell
  # a nil map's as `<no value>`.
  [[ "$have" == "<no value>" ]] && have=""
  want="$("$2" --inputs-hash 2>/dev/null </dev/null)" || want=""
  want="${want//$'\r'/}"
  if [[ -z "$want" ]]; then printf 'unknown'
  elif [[ -z "$have" ]]; then printf 'unlabelled'
  elif [[ "$have" == "$want" ]]; then printf 'current'
  else printf 'stale'
  fi
}

# Pull every ref resolve-images.sh wrote into .env. The refs are TAGGED, not
# digest-pinned: the lock's digest is verified at resolution time against the
# registry, and a substituted tag is deliberately trusted from the mirror.
# Returns 3 for the ONE seam the operator must fill (resolve-images.sh exits 3
# when a mirror cannot serve a tag or an operator pin does not exist — nothing
# this script can do about either), 1 for anything else. D5: a failure is never
# reported as "stopped for your action".
fetch_images() {
  local rc=0 sd=""
  local -a rargs=()
  # A STAGED run (update.sh apply's acquisition, D5) resolves the NEW release's
  # images.txt into COPIES, never into the deployment's .env and
  # installed.manifest. Two reasons, both read off this resolver:
  #   * the tree is still the OLD release until the merge, and an apply that
  #     stops before it (a catalog pause, a refused fast-forward) must leave
  #     what compose reads exactly as it was;
  #   * on a PARTIAL failure — the very case staging exists for, a mirror
  #     lacking one tag — the resolver has already rewritten the keys it could
  #     resolve, so the real .env would hold half a new release. (It used to
  #     rewrite the manifest WITHOUT the one it could not, too, which turned
  #     that image's old value into an OPERATOR PIN for good; since v2.57.0 it
  #     carries the failed image's previous row forward — carry_forward.)
  # The copies are seeded from the real ones — every CC_IMG_* line (an operator
  # pin included) and the manifest — so the pin-versus-own-write judgement is
  # the same one the real run will make. Only CC_IMG_* and CC_STATE_DIR are
  # copied: the resolver reads every other answer from the environment load_env
  # exported, so no credential is duplicated. The post-merge `./setup.sh fetch`
  # then writes the real ones, with every artifact already present.
  if (( STAGED )); then
    sd="$(cc_stage_dir "$STATE_DIR")/acquire"
    mkdir -p "$sd" || { fail "resolve-images" "could not create $sd for the staged resolution"; return 1; }
    { grep -E '^(CC_IMG_[A-Z0-9_]+|CC_STATE_DIR)=' "$ENV_FILE" || true; } >"$sd/answers.env"
    chmod 600 "$sd/answers.env" 2>/dev/null || true
    rm -f "$sd/installed.manifest"
    if [[ -f "$STATE_DIR/installed.manifest" ]]; then
      cp "$STATE_DIR/installed.manifest" "$sd/installed.manifest" \
        || { fail "resolve-images" "could not copy installed.manifest into $sd"; return 1; }
    fi
    rargs=(--env-file "$sd/answers.env" --manifest "$sd/installed.manifest")
  fi
  "$HERE/resolve-images.sh" ${rargs[@]+"${rargs[@]}"} || rc=$?
  case "$rc" in
    0) pass "resolve-images" "every image resolved to its locked tag" ;;
    2) pass "resolve-images" "resolved, with substitutions — see the WARN lines above and $STATE_DIR/installed.manifest" ;;
    3) useraction "resolve-images" "image resolution stopped for you — the USERACTION line above names the seam (CC_REGISTRY_* for the mirror HOST, CC_IMG_<NAME> for an exact ref this resolver must use as-is). Nothing was deployed; fix the seam in the repo-root .env and re-run"
       return 3 ;;
    *) fail "resolve-images" "image resolution failed (exit $rc) — the FAIL lines above name the seam per image"; return 1 ;;
  esac
  # resolve-images.sh writes CC_IMG_* into .env; re-read so this shell has them.
  # Staged, it wrote them into the copy: source THAT, so what is pulled below is
  # the NEW release's refs (the build scripts inherit them from here too).
  if (( STAGED )); then
    set -a
    # shellcheck disable=SC1090,SC1091
    . "$sd/answers.env"
    set +a
  else
    load_env || return 1
  fi

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

# Build when the image is absent OR was built from other inputs (v2.57.0): an
# unlabelled image (an earlier release built it) counts as different and is
# rebuilt once. podman's layer cache makes a rebuild of an unchanged layer
# cheap; a changed one is exactly what must be rebuilt.
#
# A STAGED run (D5) builds ASIDE: the build script itself tags
# cc_staged_image_ref's `-staged` form when CC_STAGED_FOR is set, so this asks
# about — and names — that ref. The live tag the running deployment uses does
# not move before the merge; the post-merge fetch builds it, from the cache the
# staged build warmed, and its label is what local_image_state reads.
fetch_local() { # fetch_local <check> <ref> <build-script> <seams>
  local check="$1" ref="$2" script="$3" seams="$4" said aside=""
  if (( STAGED )); then
    # The new release did not change this image's inputs: the LIVE image is
    # already the one it needs, so there is nothing to prove and nothing to
    # build aside.
    if [[ "$(local_image_state "$ref" "$script")" == current ]]; then
      pass "$check" "$ref present and built from this release's inputs already"
      return 0
    fi
    ref="$(cc_staged_image_ref "$ref")"
    aside=" (aside — the running deployment's tag does not move before the merge)"
  fi
  case "$(local_image_state "$ref" "$script")" in
    current)    pass "$check" "$ref present and built from these inputs${aside}"
                (( STAGED )) || drop_staged_tag "$ref"
                return 0 ;;
    stale)      said="rebuilt — build inputs changed since the image was made" ;;
    unlabelled) said="rebuilt — build inputs changed since the image was made (it carried no $(cc_build_inputs_label) label: an earlier release built it)" ;;
    *)          said="built" ;;
  esac
  if "$script" >&2; then
    pass "$check" "$ref ${said}${aside}"
    (( STAGED )) || drop_staged_tag "$ref"
  else
    fail "$check" "$ref failed to build against your sources — seams: $seams (see .env.example's deployment section)"
  fi
}

# Once the LIVE tag is built from these inputs, the aside tag a staged
# acquisition left (see fetch_local) has done its job. `podman untag <img>
# <name>` removes exactly that NAME: never an image, never a layer — so it
# cannot evict the cache the live build was just served from, which is why this
# is `untag` and not `rmi` (podman's rmi also prunes dangling parents unless
# told not to, and when the aside image is not the live one that is the warm
# cache). If the two tags named different images, the aside one is left
# dangling like the image any rebuild replaces; `podman image prune` is the
# operator's. The NAME is passed explicitly: `untag` with no name strips EVERY
# name of the image, the live tag included. Absent (a fresh install, an update
# that built nothing new) is the common case and says nothing. Never from a
# probe: this mutates.
drop_staged_tag() { # drop_staged_tag <live-ref>
  local aside
  aside="$(cc_staged_image_ref "$1")"
  have_image "$aside" || return 0
  if podman untag "$aside" "$aside" >/dev/null 2>&1 </dev/null; then
    note "    untagged $aside — the staged acquisition's build, now that $1 is built"
  else
    note "    could not untag $aside (harmless: no container uses it) — podman untag $aside $aside"
  fi
  return 0
}

# The Python graph, RESOLVED — `uv pip install --dry-run`, which installs
# nothing. Shared by `fetch` (against the .venv it just created) and `check`
# (against the .venv if there is one, else a throwaway in the state dir), so
# what check proves is the resolution fetch performs. Needs a target
# environment in VIRTUAL_ENV.
resolve_python_deps() {
  if [[ "$CC_AIRGAP" == "1" ]]; then
    in_repo uv pip install --dry-run -r "$REPO_ROOT/requirements.lock" >&2 \
      && pass "python" "requirements.lock resolves against ${CC_PYPI_INDEX_URL:-PyPI}" \
      || fail "python" "requirements.lock does not resolve — seam: CC_PYPI_INDEX_URL"
  else
    in_repo uv pip install --dry-run -e ".[dev,runtime]" >&2 \
      && pass "python" "[dev,runtime] resolves against ${CC_PYPI_INDEX_URL:-PyPI}" \
      || fail "python" "the Python dependencies do not resolve — seam: CC_PYPI_INDEX_URL; or CC_AIRGAP=1 (lock only)"
  fi
}

phase_fetch() {
  load_env || return 1
  : "${CC_GRAPHITI_TAG:=1.0.2-anthropic}"
  [[ -f "$HERE/images.txt" ]] || { fail "images-txt" "$HERE/images.txt missing"; return 1; }

  # The ONE deliberate pause this phase keeps (D5): an unresolvable pin or a
  # mirror that lacks a tag is a seam only the operator can fill. Everything
  # else — a pull of a RESOLVED ref, a local build — is a FAIL, and this phase
  # returns 1 for it. Until 2026-10-01 it could not: its only FAIL path ended
  # in a USERACTION summary, the phase runner ranked USERACTION above FAIL, and
  # `update.sh apply` merged the new code straight past a failed build.
  local frc=0
  fetch_images || frc=$?

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
    resolve_python_deps
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
    # The guidance survives; the USERACTION does not. It was what made a failed
    # build read as "stopped for your action" (D5).
    note ""
    note "$FAILS artifact(s) could not be acquired. Fix the seam(s) named above in the"
    note "repo-root .env and re-run $( (( STAGED )) && printf './update.sh apply — nothing has been merged' || printf './setup.sh') (acquired ones fast-forward);"
    note "deploy/discover.sh maps what this network can reach, deploy/AIRGAP.md the seams."
    return 1
  fi
  (( frc == 3 )) && return 3
  return 0
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
  note ""
  note "  OR SKIP THIS PAUSE ENTIRELY (v2.44.0): declare the upstream in the"
  note "  repo-root .env and re-run — setup registers the rows itself."
  note "  Keys this deployment wants:"
  note "    $(llm_undeclared_keys)"
  note "  (the model keys take the UPSTREAM model id; a 127.0.0.1 base URL is"
  note "  rewritten to host.containers.internal for the row, because the row is"
  note "  dialled by a CONTAINER. ./setup.sh check probes them from THIS host"
  note "  before any of this is deployed.)"
  note ""
  note "  If the endpoint is only reachable through an egress PROXY: the CA and"
  note "  the insecure knob reach the LiteLLM container (SSL_CERT_FILE /"
  note "  SSL_VERIFY, via compose), but CC_PROXY does NOT — containers.conf's"
  note "  [engine] env covers pulls and builds, not containers. That is an OPEN"
  note "  item in the design record: add HTTP(S)_PROXY to the litellm service"
  note "  by hand if you need it."
  note ""
  note "When it looks right, re-run:  ./setup.sh llm   (it validates every alias, then continues)"
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

# ─────────────────────────────────────────────────────────────────────────────
# Does every container run the image its ref names NOW? (v2.57.0)
# ─────────────────────────────────────────────────────────────────────────────
# `compose up -d` converges on the SERVICE DEFINITION, not on the image behind
# it. podman-compose 1.6.0 — this profile's floor — recreates an existing
# container only when the sha256 of its service dict differs from the
# `io.podman.compose.config-hash` label it was created with (podman_compose.py,
# compose_up, read at the v1.6.0 tag on 2026-10-02). The image REF string is in
# that dict; the image ID behind it is not. So since this release made a
# changed Dockerfile REBUILD `localhost/cc-graphiti:<tag>` under the same tag —
# and equally when a third-party tag such as `postgres:16` is re-pulled to a new
# digest — `up` left the OLD container running and reported it healthy.
# (podman-compose's main branch has since added an image-ID comparison, still
# unreleased as 1.6.0, so the floor may not assume it. docker compose compares
# its `com.docker.compose.image` digest label already — mustRecreate in
# pkg/compose/convergence.go — and against it this pass simply finds nothing.)
#
# So after `up`, the stack and llm phases compare, per service, the image ID its
# container was created from with the ID its configured ref resolves to in
# local storage NOW, and recreate exactly the services that differ. The probes
# ask the same question, so the PLAN says the phase will run and a `done` row
# cannot hide a stale container.
#
# compose.yaml is the one list of services: this reads it — a fixed shape, the
# two-space service keys under `services:` and their four-space `image:`,
# `profiles:` and `volumes:` — rather than carrying a second table that could
# drift from it, and expands its `${VAR:-default}` refs the way compose does:
# this shell, then .env, then the default (p_flag). Read-only, and TWO podman
# calls per question however many services: one `ps` (every container of the
# project, its compose service label and the ID of the image it was created
# from) and one `images` (every local ref with its ID). The probes that call this
# run in the plan and again in the loop, on a Windows client that pays a round
# trip into the machine per call. podman CLI only — container storage is
# never read directly (it lives inside the machine on Windows).
STACK_LOADED=0
STACK_PROJECT=""
STACK_SVCS=()
declare -A STACK_PROFILE=() STACK_IMAGE=() STACK_VOLS=() STACK_NAMED_VOL=()
stack_load() {
  (( STACK_LOADED )) && return 0
  local f="$HERE/compose.yaml" kind a b c d
  [[ -f "$f" ]] || return 1
  STACK_PROJECT="$(sed -n 's/^name:[[:space:]]*//p' "$f" | head -1 | tr -d "\r\"'")"
  [[ -n "$STACK_PROJECT" ]] || return 1
  while IFS=$'\t' read -r kind a b c d; do
    case "$kind" in
      S) STACK_SVCS+=("$a"); STACK_PROFILE["$a"]="$b"; STACK_IMAGE["$a"]="$c"; STACK_VOLS["$a"]="$d" ;;
      V) STACK_NAMED_VOL["$a"]=1 ;;
    esac
  done < <(awk '
    function flush() {
      if (svc != "") printf "S\t%s\t%s\t%s\t%s\n", svc, prof, img, (vols == "" ? "-" : vols)
      svc = ""
    }
    { sub(/\r$/, "") }
    /^[^ #]/ { flush(); sect = $0; sub(/:.*/, "", sect); next }
    sect == "volumes" && /^  [A-Za-z0-9][A-Za-z0-9_.-]*:/ {
      v = $0; sub(/^  /, "", v); sub(/:.*/, "", v); printf "V\t%s\n", v; next
    }
    sect == "services" && /^  [A-Za-z0-9][A-Za-z0-9_.-]*:[[:space:]]*$/ {
      flush(); svc = $0; sub(/^  /, "", svc); sub(/:.*/, "", svc)
      prof = "-"; img = "-"; vols = ""; invol = 0; next
    }
    svc == "" { next }
    /^    image:/ {
      v = $0; sub(/^    image:[[:space:]]*/, "", v); sub(/[[:space:]]+#.*/, "", v)
      gsub(/^["\047]|["\047]$/, "", v); img = v; invol = 0; next
    }
    /^    profiles:/ {
      v = $0; sub(/^    profiles:[[:space:]]*\[/, "", v); sub(/\].*/, "", v)
      gsub(/[[:space:]"\047]/, "", v); prof = (v == "" ? "-" : v); invol = 0; next
    }
    /^    volumes:/ { invol = 1; next }
    invol && /^      - / {
      v = $0; sub(/^      - [[:space:]]*/, "", v); sub(/[[:space:]]+#.*/, "", v)
      gsub(/^["\047]|["\047]$/, "", v); vols = vols (vols == "" ? "" : " ") v; next
    }
    /^    [^ #]/ { invol = 0 }
    END { flush() }
  ' "$f")
  (( ${#STACK_SVCS[@]} )) || return 1
  STACK_LOADED=1
}

# compose's interpolation for the one form compose.yaml's image lines use:
# `${NAME}` and `${NAME:-default}`, no nesting. A value comes from this shell,
# else .env, else the default — p_flag's order, which is what compose sees
# through `--env-file` in a phase that has sourced .env.
compose_expand() { # compose_expand <text>
  local s="$1" out="" re='\$\{([A-Za-z_][A-Za-z0-9_]*)(:?-([^}]*))?\}'
  while [[ "$s" =~ $re ]]; do
    out+="${s%%"${BASH_REMATCH[0]}"*}"
    out+="$(p_flag "${BASH_REMATCH[1]}" "${BASH_REMATCH[3]}")"
    s="${s#*"${BASH_REMATCH[0]}"}"
  done
  printf '%s%s' "$out" "$s"
}

# Is a compose profile (or any of a service's comma-separated profiles) on for
# these flags? `-` is a service with no profile: always on. The ONE mapping of
# CC_ENABLE_* onto profiles — compose_profile_flags is built from it too.
compose_profile_on() { # compose_profile_on <profile[,profile...]|->
  local prof
  [[ -z "$1" || "$1" == "-" ]] && return 0
  for prof in ${1//,/ }; do
    case "$prof" in
      n8n)     [[ "$(p_flag CC_ENABLE_N8N 0)" == 1 ]] && return 0 ;;
      crawler) [[ "$(p_flag CC_ENABLE_CRAWLER 1)" == 1 ]] && return 0 ;;
      speech)  [[ "$(p_flag CC_ENABLE_SPEECH 1)" == 1 ]] && return 0 ;;
    esac
  done
  return 1
}

# THE QUESTION, for the named services (none = every service these flags
# enable). Prints nothing. -> 0 every one runs what its ref resolves to now;
# 1 at least one does not — DRIFT lists them, DRIFT_KIND says how:
#   differs      the container's image ID is not the ref's ID now
#   noimage      the ref is not in local storage at all
#   absent       no container for the service
#   undeclared   not a service compose.yaml defines
# with DRIFT_REF / DRIFT_HAVE / DRIFT_WANT for the sentence; 2 = podman could
# not be asked (never read as "current"). IDs compare with any `sha256:` prefix
# dropped and as a prefix of each other, so a truncated and a full spelling of
# one ID agree.
DRIFT=()
declare -A DRIFT_KIND=() DRIFT_REF=() DRIFT_HAVE=() DRIFT_WANT=()
image_drift() { # image_drift [service...]
  local svc ref ps imgs name id rt dg have want kind
  local -a svcs=("$@")
  local -A cid=() iid=()
  DRIFT=(); DRIFT_KIND=(); DRIFT_REF=(); DRIFT_HAVE=(); DRIFT_WANT=()
  stack_load || return 2
  if (( ! ${#svcs[@]} )); then
    for svc in "${STACK_SVCS[@]}"; do
      compose_profile_on "${STACK_PROFILE[$svc]}" && svcs+=("$svc")
    done
  fi
  # The service LABEL, not the container name: both providers set
  # com.docker.compose.project/.service (podman-compose alongside its own
  # io.podman.compose.* pair), so this needs no knowledge of container_name.
  ps="$(podman ps -a --filter "label=com.docker.compose.project=${STACK_PROJECT}" \
          --format '{{index .Labels "com.docker.compose.service"}} {{.ImageID}}' 2>/dev/null </dev/null)" \
    || return 2
  imgs="$(podman images --no-trunc --format '{{.Repository}}:{{.Tag}} {{.Digest}} {{.ID}}' 2>/dev/null </dev/null)" \
    || return 2
  while read -r name id; do
    [[ -n "$name" && -n "$id" ]] && cid["$name"]="${id#sha256:}"
  done <<<"${ps//$'\r'/}"
  while read -r rt dg id; do
    [[ -n "$rt" && -n "$id" ]] || continue
    iid["$rt"]="${id#sha256:}"
    # A digest-pinned ref (an operator's CC_IMG_<NAME>=host/path@sha256:...).
    [[ "$dg" == sha256:* ]] && iid["${rt%:*}@${dg}"]="${id#sha256:}"
  done <<<"${imgs//$'\r'/}"
  for svc in "${svcs[@]}"; do
    ref=""; have=""; want=""
    if [[ -z "${STACK_IMAGE[$svc]:-}" ]]; then
      kind=undeclared
    else
      ref="$(compose_expand "${STACK_IMAGE[$svc]}")"
      have="${cid[$svc]:-}"; want="${iid[$ref]:-}"
      if [[ -z "$have" ]]; then kind=absent
      elif [[ -z "$want" ]]; then kind=noimage
      elif [[ "$have" == "$want"* || "$want" == "$have"* ]]; then continue
      else kind=differs
      fi
    fi
    DRIFT+=("$svc")
    DRIFT_KIND["$svc"]="$kind"; DRIFT_REF["$svc"]="$ref"
    DRIFT_HAVE["$svc"]="$have"; DRIFT_WANT["$svc"]="$want"
  done
  (( ${#DRIFT[@]} )) && return 1
  return 0
}

# THE STATEFUL SERVICES, and where each one's IMAGE keeps its data (the image's
# contract, not ours — which is why this is a table and compose.yaml is not
# asked). A recreate replaces the container; it is safe for these only because
# that path is a NAMED volume, which `down`/recreate never removes (podman-
# compose's recreate tears down with volumes=False; docker compose keeps named
# volumes by design). stack_data_on_volume CHECKS that against compose.yaml on
# every recreate rather than trusting this comment, and
# tests/test_single_stack_catch_up.py pins both directions: every service with a
# named volume is listed here, and every one listed has its path on one.
#   postgres, litellm-db, n8n-db  PGDATA is a subdirectory of this mount
#   litellm-redis                 `--appendonly yes` writes here
#   neo4j                         the graph (the image's /logs is NOT data, and
#                                 an anonymous volume: a recreate starts it empty)
#   n8n                           its own state (the Gmail credential is in n8n-db)
#   speech                        the model snapshots — a download, or pre-placed
#                                 by hand on an air-gapped site
STACK_STATEFUL=(
  "postgres /var/lib/postgresql/data"
  "litellm-db /var/lib/postgresql/data"
  "n8n-db /var/lib/postgresql/data"
  "litellm-redis /data"
  "neo4j /data"
  "n8n /home/node/.n8n"
  "speech /home/ubuntu/.cache/huggingface/hub"
)
stack_data_path() { # stack_data_path <service>  -> prints the path, or nothing (stateless)
  local e
  for e in "${STACK_STATEFUL[@]}"; do
    [[ "${e%% *}" == "$1" ]] && { printf '%s' "${e#* }"; return 0; }
  done
  return 0
}

# 0 = recreating <service> loses nothing: it keeps no data, or compose.yaml
# mounts a NAMED volume (one the top-level `volumes:` declares) at its data
# path. A bind mount, an anonymous volume, or no mount at all is a 1.
stack_data_on_volume() { # stack_data_on_volume <service>
  local path v src dst
  local -a vols=()
  path="$(stack_data_path "$1")"
  [[ -n "$path" ]] || return 0
  stack_load || return 1
  read -r -a vols <<<"${STACK_VOLS[$1]:--}"
  for v in "${vols[@]}"; do
    [[ "$v" == "-" ]] && continue
    src="${v%%:*}"; dst="${v#*:}"; dst="${dst%%:*}"
    [[ "$dst" == "$path" && -n "${STACK_NAMED_VOL[$src]:-}" ]] && return 0
  done
  return 1
}

# THE CATCH-UP, after a phase's `compose up`: every drifted service whose data
# is safe is recreated in ONE call — compose's own force-recreate, scoped to
# those services (--no-deps: their dependencies are already up and are not
# touched; podman-compose additionally recreates a recreated service's RUNNING
# DEPENDENTS, its `down` having removed them — graphiti after neo4j, litellm
# after its database — all of them stateless or volume-backed), waiting on
# compose.yaml's healthchecks exactly as `up --wait` does. Then it asks again:
# a recreate that did not land is a FAIL, never a PASS. Every verdict is printed
# under the CALLER's check-name, so a failure here fails that ledger row.
catch_up_images() { # catch_up_images <check-name> [service...]
  local check="$1" rc=0 svc bad=0
  shift
  local -a redo=()
  local -A old=() new=() ref=()
  image_drift "$@" || rc=$?
  case "$rc" in
    0) pass "$check" "every container runs the image its ref resolves to now (${*:-every service these flags enable})"
       return 0 ;;
    2) fail "$check" "could not compare the containers' images with their refs — podman ps / podman images did not answer; run: ./setup.sh report"
       return 1 ;;
  esac
  for svc in "${DRIFT[@]}"; do
    case "${DRIFT_KIND[$svc]}" in
      differs)
        if stack_data_on_volume "$svc"; then
          redo+=("$svc")
          old["$svc"]="${DRIFT_HAVE[$svc]}"; new["$svc"]="${DRIFT_WANT[$svc]}"; ref["$svc"]="${DRIFT_REF[$svc]}"
          continue
        fi
        fail "$check" "$svc runs image ${DRIFT_HAVE[$svc]:0:12}, but ${DRIFT_REF[$svc]} now resolves to ${DRIFT_WANT[$svc]:0:12} — and it is NOT recreated: it keeps its data at $(stack_data_path "$svc"), which compose.yaml does not mount from a named volume, so a new container would start without it. The running container is left as it is; carry this back as a finding: ./setup.sh report" ;;
      noimage)
        fail "$check" "$svc's image ${DRIFT_REF[$svc]} is not in local storage, so its container cannot be brought onto it — run: ./setup.sh fetch" ;;
      absent)
        fail "$check" "$svc has no container after compose up — run: ./setup.sh report" ;;
      *)
        fail "$check" "$svc is not a service compose.yaml defines — this release's setup.sh and compose.yaml disagree; run: ./setup.sh report" ;;
    esac
    bad=1
  done
  if (( ${#redo[@]} )); then
    compose_profile_flags
    note "--> compose up -d --force-recreate --no-deps --wait ${redo[*]}   (their images changed behind unchanged refs)"
    if ! compose "${PROFILE_FLAGS[@]}" up -d --force-recreate --no-deps --wait "${redo[@]}" >&2; then
      fail "$check" "could not recreate ${redo[*]} on the image(s) their refs now resolve to — the provider's own error is on stderr above; re-run ./setup.sh (a container it stopped is started again by the next compose up)"
      return 1
    fi
    rc=0
    image_drift "${redo[@]}" || rc=$?
    if (( rc == 2 )); then
      fail "$check" "recreated ${redo[*]}, but could not ask podman whether they now run the right image — run: ./setup.sh report"
      return 1
    fi
    for svc in "${redo[@]}"; do
      if [[ " ${DRIFT[*]-} " == *" $svc "* ]]; then
        fail "$check" "$svc was recreated and still does not run ${ref[$svc]}'s image (${DRIFT_KIND[$svc]}) — run: ./setup.sh report"
        bad=1
      else
        pass "$check" "$svc recreated — its container ran image ${old[$svc]:0:12}, and ${ref[$svc]} now resolves to ${new[$svc]:0:12} (compose up leaves a running container alone when only the image behind its ref changed)"
      fi
    done
  fi
  (( bad )) && return 1
  return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: stack — assert the local images, then bring the whole stack up.
# ─────────────────────────────────────────────────────────────────────────────
# Images are the fetch phase's job; here they are only ASSERTED, so a missing
# one is a clear "run fetch" and never a surprise build (or pull) mid-deploy.
# "In local storage" means AS FETCH LEAVES IT (v2.57.0): the tag present AND
# its build-inputs label equal to this tree's. The stack rows' probes are the
# same p_image_* functions fetch's rows use, which compare the label — so a
# need_image that passed on the tag alone would PASS here and then have its
# own row FAIL on the probe after the phase. A stale image (a release changed
# its Dockerfile and fetch has not run since, or failed to rebuild) is the same
# clear "run fetch", never a build here.
need_image() { # need_image <check-name> <image-ref> <build-script>
  case "$(local_image_state "$2" "$3")" in
    current) pass "$1" "$2 present and built from this tree's build inputs"; return 0 ;;
    absent)  fail "$1" "$2 is not in local storage — run: ./setup.sh fetch" ;;
    *)       fail "$1" "$2 is in local storage but was not built from this tree's build inputs (its $(cc_build_inputs_label) label differs or is missing) — run: ./setup.sh fetch, which rebuilds it" ;;
  esac
  return 1
}

phase_stack() {
  load_env || return 1
  : "${CC_GRAPHITI_TAG:=1.0.2-anthropic}"

  need_image "image-graphiti" "localhost/cc-graphiti:${CC_GRAPHITI_TAG}" "$HERE/build-graphiti-image.sh" || return 1
  if [[ "$CC_ENABLE_SANDBOX" == "1" ]]; then
    need_image "image-sandbox" "localhost/cc-sandbox:1" "$HERE/build-sandbox-image.sh" || return 1
  else
    pass "image-sandbox" "skipped (CC_ENABLE_SANDBOX=0)"
  fi
  if [[ "$CC_ENABLE_CRAWLER" == "1" ]]; then
    need_image "image-crawler" "localhost/cc-crawler:1" "$HERE/build-crawler-image.sh" || return 1
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
  # `up` converged the DEFINITIONS; this converges the IMAGES (v2.57.0). A
  # release that rebuilt graphiti or the crawler under its fixed tag, or a
  # re-pulled third-party tag, leaves the old container running through `up`
  # — every enabled service whose container is not on the image its ref
  # resolves to now is recreated here, by name, and said so (image_drift).
  catch_up_images "up-stack" || return 1

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

# ── the spine's own LiteLLM key, and its SCOPE (2026-10-01 record, D4/P2) ────
# The key used to be minted with a hard-coded ["cc-default", "cc-tts",
# "cc-stt"] — a SECOND hand-kept alias list beside cc_required_aliases (the ONE
# list, deploy/env-lib.sh), and it had already drifted: graphiti-llm,
# cc-embedding and gpt-4.1-nano were never in it. MEASURED before this fix:
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

# The required aliases as a JSON array, built from cc_required_aliases — never
# a second list.
spine_aliases_json() {
  local a out=""
  for a in $(cc_required_aliases); do out="${out:+$out, }\"$a\""; done
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
    local list; list="$(cc_required_aliases)"
    pass "mint-key" "minted a LiteLLM virtual key scoped to ${list// / + } (cc_required_aliases) and stored it as CC_LLM_API_KEY"
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
' $(cc_required_aliases) <<<"$info" 2>/dev/null)" || plan=""
  state="$(sed -n 1p <<<"$plan")"
  missing="$(sed -n 2p <<<"$plan")"
  union="$(sed -n 3p <<<"$plan")"
  case "$state" in
    empty)
      pass "mint-key" "CC_LLM_API_KEY already set; its model list is EMPTY, which LiteLLM reads as every model on the proxy — left alone (not minting a second key)"
      ;;
    covered)
      pass "mint-key" "CC_LLM_API_KEY already set; its scope already covers every alias this deployment requires ($(cc_required_aliases)) — not minting a second key"
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
      if [[ ! -d "$REPO_ROOT/web/node_modules" ]]; then
        step "cockpit" "cockpit npm tree installed (fetch had not left one)" \
          in_web npm ci || return 1
      fi
      step "cockpit" "cockpit built (web/)" \
        in_web npm run build || return 1
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

# ── the self-check: the APPLICATION proving itself (2026-10-01 record, D4) ───
# verify.sh asks "is the deployment what it should be?" under the ADMIN key.
# The self-check asks the other question: "will the AGENTS work?" — the app's
# own Settings, the same .env, the credential the app actually holds, through
# the seam a real run takes. The two are different questions and both are
# asked. It exists because every check the install made could be green while
# the app could not do its job (2026-10-01 work site: an empty CC_LLM_API_KEY,
# eight blank Systems links, a green verify).
#
# The module is central_command/selfcheck.py, run from the install's venv with
# the repo root as cwd. Its contract: one protocol line per check on stdout,
# `PASS|WARN|FAIL selfcheck-<name>: <message>`, the message naming .env keys
# and never values; exit 1 on any FAIL, else 2 on any WARN, else 0.
# `--pre-boot` is the installer's mode: before `boot` the cockpit and the
# sandbox runner are not started yet, so those two are reported as not checked.
#
# It is READINESS-shaped (D11, goss): it GATES use — boot and demo require
# verify/selfcheck — and it restarts nothing.
SC_OUT=""; SC_RC=0; SC_WHY=""
selfcheck_exec() { # selfcheck_exec <module-args...>  -> 1 = it could not be started at all (SC_WHY says why)
  local py
  SC_OUT=""; SC_RC=0; SC_WHY=""
  if ! py="$(venv_python)"; then
    SC_WHY="the install's .venv has no python, so python -m central_command.selfcheck cannot run — run ./setup.sh (the fetch and app phases create it)"
    return 1
  fi
  # Its stderr is the module's own detail (an import error, a traceback) and
  # goes where the protocol says detail goes.
  SC_OUT="$(cd "$REPO_ROOT" && "$py" -m central_command.selfcheck "$@")" || SC_RC=$?
  return 0
}

# Re-emit the module's lines through THIS script's pass/warn/fail, under their
# own `selfcheck-<name>` names — so they are counted into the phase's verdict,
# logged, and carried into the ledger's reason like any other line — then the
# row's own `selfcheck` line. 0 = no FAIL (WARN-only is not a failure of the
# row), 1 = a check failed or the module could not run.
selfcheck_emit() { # selfcheck_emit <module-args...>
  local line verb name msg nfail=0 nwarn=0 npass=0 failed=""
  local re='^(PASS|WARN|FAIL) (selfcheck-[A-Za-z0-9_-]+): ?(.*)$'
  if ! selfcheck_exec "$@"; then
    fail "selfcheck" "$SC_WHY"
    return 1
  fi
  while IFS= read -r line; do
    line="${line%$'\r'}"
    if [[ "$line" =~ $re ]]; then
      verb="${BASH_REMATCH[1]}"; name="${BASH_REMATCH[2]}"; msg="${BASH_REMATCH[3]}"
      case "$verb" in
        PASS) pass "$name" "$msg"; npass=$((npass+1)) ;;
        WARN) warn "$name" "$msg"; nwarn=$((nwarn+1)) ;;
        FAIL) fail "$name" "$msg"; nfail=$((nfail+1)); failed="${failed:+$failed, }$name" ;;
      esac
    elif [[ -n "${line//[[:space:]]/}" ]]; then
      note "$line"
    fi
  done <<<"$SC_OUT"
  if (( nfail )); then
    fail "selfcheck" "$nfail check(s) failed ($failed) — each line above names the .env key or the command"
    return 1
  fi
  if (( npass + nwarn == 0 )); then
    fail "selfcheck" "python -m central_command.selfcheck printed no check line (exit $SC_RC) — the module could not run; its own error is on stderr above"
    return 1
  fi
  if (( SC_RC != 0 && SC_RC != 2 )); then
    fail "selfcheck" "python -m central_command.selfcheck exited $SC_RC without a FAIL line — it stopped part-way; its own error is on stderr above"
    return 1
  fi
  if (( nwarn )); then
    pass "selfcheck" "the application's own self-check passed: $npass check(s) passed, $nwarn warned (each WARN above names what to look at)"
  else
    pass "selfcheck" "the application's own self-check passed: $npass check(s), with the settings and the credential the agents will use"
  fi
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

# ─────────────────────────────────────────────────────────────────────────────
# PHASES test / boot / demo — install-to-working-demo in ONE command
# (2026-08-28, operator decision: "me having to run a whole series of commands
# myself seems relatively pointless"). Deterministic like everything above;
# the two genuinely-human moments (naming the operator, approving the demo
# proposal) are IN-PROCESS gates on a terminal, exit-3 gates otherwise.
# WHAT MAKES EACH RUN ONCE is the LEDGER (2026-10-01 design record, D2/D5), not
# a guess from the running system: `test`'s row is `done` per release, so the
# full run skips the suite at a version it already passed at and runs it again
# after an update, and `./setup.sh test` by name always runs it. A healthy API
# used to skip test too — which meant an update applied under a running API
# never ran the suite for the new code at all. `boot` still leaves an API that
# answers alone (it will not start a second one), and demo's probe reads the
# event log, so a decided proposal is never enrolled twice.
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
  # No `api_up` skip (D5): see the block comment above — the ledger row is what
  # makes this once per release.
  local py; py="$(venv_python)" || { fail "test" ".venv missing — run: ./setup.sh app"; return 1; }
  note "the offline suite is SEQUENTIAL and takes ~10 minutes — this is the gate, not a formality"
  step "test" "the offline suite is green" in_repo "$py" -m pytest -q || return 1
}

# ── the three host processes, and who keeps them running (D6) ───────────────
# 2026-10-01 design record, D6: "everything the install starts, it
# supervises". `boot` starts exactly three HOST processes — the API, the
# cockpit server and the sandbox runner (v2.57.0: until then the operator
# started the runner by hand, from the README) — and `stop` stops exactly
# those three. Each has a pid file and a log in the state dir:
# uvicorn.pid/.log, cockpit.pid/.log, sandbox.pid/.log.
#
# WHO KEEPS THEM RUNNING is decided once per run, by boot_supervisor:
#   systemd   Linux with a reachable user manager (`systemctl --user`
#             answers): boot writes one unit per process into
#             <state>/systemd/ (never the checkout), enables it, and starts
#             the process THROUGH it. One supervisor, never a nohup beside an
#             enabled unit — or a crash is restarted by systemd while `stop`
#             signals a pid systemd never owned, and Restart= revives what
#             `stop` killed;
#   windows   Git Bash: a detached start, and the logon entry (the
#             boot-at-logon row) runs ./setup.sh after a reboot;
#   detached  anything else (a container, WSL without systemd): a detached
#             start, and a WARN saying nothing restarts the processes after a
#             crash or a reboot — ./setup.sh is what brings them back.
SUP_MODE=""
SUP_ID=""

boot_supervisor() {
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) printf 'windows'; return 0 ;;
    Linux) ;;
    *) printf 'detached'; return 0 ;;
  esac
  if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    printf 'systemd'
  else
    printf 'detached'
  fi
}

# A unit's name for <kind> (api | cockpit | sandbox), from the install id —
# the identity the state dir is named by — so two installs on one host never
# collide (supervise-lib.sh's cc_sup_unit_name).
sup_unit() { # sup_unit <kind>
  [[ -n "$SUP_ID" ]] || SUP_ID="$(cc_install_id "$(cc_norm_path "$REPO_ROOT")")"
  cc_sup_unit_name "$SUP_ID" "$1"
}
sup_unit_file() { # sup_unit_file <kind>
  printf '%s/systemd/%s' "$STATE_DIR" "$(sup_unit "$1")"
}

boot_api_port() {
  local p; p="$(get_kv "$ENV_FILE" CC_API_PORT)"; printf '%s' "${p:-8080}"
}
cockpit_port() {
  p_flag CC_COCKPIT_PORT 3080
}
# The runner listens on CC_SANDBOX_RUNNER_URL's port — the URL the API dials is
# the one fact, and the port boot binds is read out of it, never a second key.
sandbox_port() {
  local u; u="$(q_unquote "$(get_kv "$ENV_FILE" CC_SANDBOX_RUNNER_URL)")"
  [[ -n "$u" ]] || u="http://127.0.0.1:8090"
  u="${u#*://}"; u="${u%%/*}"
  if [[ "$u" =~ :([0-9]+)$ ]]; then printf '%s' "${BASH_REMATCH[1]}"; else printf '8090'; fi
}

# The HTTP status the runner gives THIS install's token, on a path it does not
# serve: 404 = it answered and took the token (or enforces none), 401 = a
# runner is listening that will refuse every request the API sends (started by
# hand without it, or before .env's token changed), 000 = nothing answered.
# The token travels on stdin (`-H @-`), never in an argv.
sandbox_auth_code() {
  local tok code url
  url="http://127.0.0.1:$(sandbox_port)/"
  tok="$(q_unquote "$(get_kv "$ENV_FILE" CC_SANDBOX_RUNNER_TOKEN)")"
  if [[ -n "$tok" ]]; then
    code="$(printf 'Authorization: Bearer %s\n' "$tok" \
      | curl -sS -m 5 -o /dev/null -w '%{http_code}' -H @- "$url" 2>/dev/null)"
  else
    code="$(curl -sS -m 5 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null)"
  fi
  printf '%s' "${code:-000}"
}

# Is <kind> up and answering as THIS install needs it to?
proc_ready() { # proc_ready <kind>
  local c
  case "$1" in
    api)     api_up ;;
    cockpit) curl -fsS -m 5 -o /dev/null "http://127.0.0.1:$(cockpit_port)/" 2>/dev/null ;;
    sandbox) c="$(sandbox_auth_code)"; [[ "$c" != 000 && "$c" != 401 ]] ;;
    *)       return 1 ;;
  esac
}

# Everything about one process, into PROC_* — the SAME command line and
# environment whichever supervisor runs it ("the units run the same commands
# boot runs by hand", D6). Returns 1 with PROC_WHY when it cannot be started
# at all on this host.
#
# The environment: the process reads .env as before (exported by load_env's
# `set -a` for a hand start; EnvironmentFile= for a unit) — which is how
# CC_SANDBOX_RUNNER_TOKEN reaches the runner, the value the API sends. On top
# of it, PROC_ENV: what boot sets per process, and the derived trust/proxy
# variables load_env exported (cc_sup_passthrough_names), which a unit would
# otherwise not have.
proc_spec() { # proc_spec <api|cockpit|sandbox>
  PROC_ENV=(); PROC_CMD=(); PROC_WHY=""; PROC_AFTER="-"; PROC_KILL="-"
  local n uv node
  while IFS= read -r n; do
    [[ -n "${!n:-}" ]] && PROC_ENV+=("$n=${!n}")
  done < <(cc_sup_passthrough_names)
  case "$1" in
    api)
      PROC_CHECK=boot-api; PROC_WHAT="the API"; PROC_TITLE="API"
      PROC_PIDFILE="$STATE_DIR/uvicorn.pid"; PROC_LOG="$STATE_DIR/uvicorn.log"
      PROC_PORT="$(boot_api_port)"; PROC_URL="$(api_url)"; PROC_DIR="$REPO_ROOT"; PROC_WAIT=90
      # The API spawns the cockpit-driven updater, which STOPS the API and must
      # outlive it — see cc_render_unit's KillMode note.
      PROC_KILL=process
      uv="$(venv_uvicorn)" || { PROC_WHY="uvicorn not in .venv — run: ./setup.sh app"; return 1; }
      PROC_CMD=("$uv" central_command.api.app:app --host 127.0.0.1 --port "$PROC_PORT")
      ;;
    sandbox)
      PROC_CHECK=boot-sandbox; PROC_WHAT="the sandbox runner"; PROC_TITLE="sandbox runner"
      PROC_PIDFILE="$STATE_DIR/sandbox.pid"; PROC_LOG="$STATE_DIR/sandbox.log"
      PROC_PORT="$(sandbox_port)"; PROC_URL="http://127.0.0.1:$PROC_PORT"; PROC_DIR="$REPO_ROOT"; PROC_WAIT=60
      # This profile's backend is rootless podman (README, "Starting the
      # sandbox runner"); kubectl is the k3s profile's.
      PROC_ENV+=("CC_SANDBOX_BACKEND=podman")
      uv="$(venv_uvicorn)" || { PROC_WHY="uvicorn not in .venv — run: ./setup.sh app"; return 1; }
      PROC_CMD=("$uv" central_command.sandbox.runner:app --host 127.0.0.1 --port "$PROC_PORT")
      ;;
    cockpit)
      PROC_CHECK=boot-cockpit; PROC_WHAT="the cockpit server"; PROC_TITLE="cockpit server"
      PROC_PIDFILE="$STATE_DIR/cockpit.pid"; PROC_LOG="$STATE_DIR/cockpit.log"
      PROC_PORT="$(cockpit_port)"; PROC_URL="http://127.0.0.1:$PROC_PORT"; PROC_DIR="$REPO_ROOT/web"; PROC_WAIT=60
      PROC_AFTER="$(sup_unit api)"
      node="$(command -v node)" || { PROC_WHY="node is not on PATH, so the cockpit server cannot start (the API runs without it)"; return 1; }
      # web/.env is RETIRED on this profile (v2.42.0): the server's
      # `dotenv/config` loads that file from cwd and does NOT override variables
      # already present in the environment, so SETTING these is exactly
      # equivalent and keeps the checkout clean. CC_UPDATE_BACKEND=api: the API
      # owns the update routes on this profile and the Node server only
      # proxies them.
      PROC_ENV+=("PORT=$PROC_PORT" "GATEWAY_URL=$(api_url)" "CC_UPDATE_BACKEND=api")
      PROC_CMD=("$node" server-dist/index.js)
      ;;
    *) PROC_WHY="no such host process: $1"; return 1 ;;
  esac
}

# Write, enable and reload the units for <kind>... — into <state>/systemd/,
# 0600 (an Environment= line can carry CC_PROXY's userinfo). `enable <path>`
# LINKS a unit that lives outside the search path and enables it in one step.
sup_install_units() { # sup_install_units <kind>...
  local dir="$STATE_DIR/systemd" kind unit file text paths=() names=()
  mkdir -p "$dir" 2>/dev/null || { fail "boot-supervisor" "could not create $dir"; return 1; }
  chmod 700 "$dir" 2>/dev/null || true
  for kind in "$@"; do
    proc_spec "$kind" || { fail "$PROC_CHECK" "$PROC_WHY"; return 1; }
    unit="$(sup_unit "$kind")"; file="$dir/$unit"
    text="$(cc_render_unit "$unit" "Central Command $PROC_TITLE, 127.0.0.1:$PROC_PORT (install $SUP_ID)" \
      "$PROC_DIR" "$ENV_FILE" "$PROC_LOG" "$PROC_AFTER" "$PROC_KILL" \
      -- ${PROC_ENV[@]+"${PROC_ENV[@]}"} -- "${PROC_CMD[@]}")" \
      || { fail "boot-supervisor" "could not render $unit (the reason is on stderr)"; return 1; }
    if ! { printf '%s\n' "$text" >"$file.tmp" && { chmod 600 "$file.tmp" 2>/dev/null || true; } && mv -f "$file.tmp" "$file"; }; then
      rm -f "$file.tmp"
      fail "boot-supervisor" "could not write $file"
      return 1
    fi
    paths+=("$file"); names+=("$unit")
  done
  note "--> systemctl --user enable ${paths[*]} && systemctl --user daemon-reload"
  if ! systemctl --user enable "${paths[@]}" >&2; then
    fail "boot-supervisor" "systemctl --user enable refused the units in $dir (its own words are on stderr) — nothing was started"
    return 1
  fi
  if ! systemctl --user daemon-reload >&2; then
    fail "boot-supervisor" "systemctl --user daemon-reload failed (its own words are on stderr) — nothing was started"
    return 1
  fi
  pass "boot-supervisor" "systemd --user units ${names[*]} written to $dir and enabled: systemd restarts each on failure and starts it at boot (with lingering on — check/linger)"
}

# A flag turned a process off (CC_ENABLE_SANDBOX=0) after a boot that ran it:
# its unit would still start at the next boot, so it is disabled, stopped and
# removed. A unit that was never written is nothing to do.
sup_retire_unit() { # sup_retire_unit <kind>
  local unit file
  unit="$(sup_unit "$1")"; file="$STATE_DIR/systemd/$unit"
  [[ -f "$file" ]] || return 0
  note "--> systemctl --user disable --now $unit (its flag is off now)"
  systemctl --user disable --now "$unit" >&2 || true
  rm -f "$file"
  systemctl --user daemon-reload >&2 || true
  rm -f "$STATE_DIR/$1.pid"
}

# A detached start, from PROC_*. `setsid` (where it exists — not Git Bash) puts
# the process in a session and process group of its own, so `stop` can signal
# the whole group: a server that forks a worker, or a wrapper that spawns the
# real interpreter, is stopped with it. nohup: it outlives the terminal.
proc_start_detached() {
  local pre=()
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) ;;
    *) command -v setsid >/dev/null 2>&1 && pre=(setsid) ;;
  esac
  # The `cd` is its OWN statement, and the backgrounded thing is ONE simple
  # command. `( cd X && cmd & echo $! )` — the shape boot used until v2.57.0 —
  # backgrounds the whole `cd && cmd` LIST: a bash subshell that forks cmd and
  # waits for it, still holding the caller's stdout/stderr. So $! (the pid
  # file) named that wrapper bash, not the server — `stop` signalled a shell
  # and the server kept listening — and anything capturing setup.sh's output
  # waited forever on a pipe the wrapper never closed. A simple command is
  # forked and exec'd directly (env -> nohup -> setsid -> the server, one pid).
  ( cd "$PROC_DIR" || exit 1
    env ${PROC_ENV[@]+"${PROC_ENV[@]}"} nohup ${pre[@]+"${pre[@]}"} "${PROC_CMD[@]}" \
      >>"$PROC_LOG" 2>&1 </dev/null &
    echo $! >"$PROC_PIDFILE" )
}

# Under systemd the pid file holds the unit's MainPID: check's port test and
# `report` read the pid files to tell this install's listeners from foreign ones.
proc_record_pid() { # proc_record_pid <unit|''>
  [[ -n "$1" ]] || return 0
  local mp; mp="$(systemctl --user show -p MainPID --value "$1" 2>/dev/null)"
  [[ "$mp" =~ ^[1-9][0-9]*$ ]] && printf '%s\n' "$mp" >"$PROC_PIDFILE"
  return 0
}

# Start <kind> unless it already answers as this install needs it to. The
# cases, in order:
#   answers, and (no unit, or the unit is active)  -> PASS, left alone
#   answers, unit NOT active, but a pid file       -> an earlier boot's
#        detached start (a pre-v2.57.0 install): stopped, then started
#        THROUGH the unit, so there is one supervisor
#   answers, unit not active, no pid file          -> WARN: not ours to stop,
#        and nothing supervises it
#   the port is held but it does not answer as ours would (a runner refusing
#        this install's token) -> ours (unit or pid file): restarted; not
#        ours: FAIL naming the port
#   nothing there -> start: `systemctl --user restart` under systemd (restart,
#        not start: a unit that is active but not answering on THIS port —
#        CC_API_PORT changed — must pick up the rewritten unit), detached
#        otherwise; then wait for it.
proc_boot() { # proc_boot <kind>
  local kind="$1" unit="" active=0 how
  proc_spec "$kind" || { fail "$PROC_CHECK" "$PROC_WHY"; return 1; }
  if [[ "$SUP_MODE" == systemd ]]; then
    unit="$(sup_unit "$kind")"
    systemctl --user is-active --quiet "$unit" 2>/dev/null && active=1
  fi
  if proc_ready "$kind"; then
    if [[ -z "$unit" ]] || (( active )); then
      proc_record_pid "$unit"
      pass "$PROC_CHECK" "$PROC_WHAT already answers at $PROC_URL${unit:+ under $unit} — not starting a second one"
      return 0
    fi
    if [[ ! -f "$PROC_PIDFILE" ]]; then
      warn "$PROC_CHECK" "$PROC_WHAT already answers at $PROC_URL, but not through $unit and not from a start this install recorded — left alone, and NOTHING restarts it after a crash or a reboot. To hand it to systemd: stop whatever holds port $PROC_PORT, then run ./setup.sh"
      return 0
    fi
    note "$PROC_WHAT answers from an earlier detached start — handing it to $unit"
    if ! proc_halt "$PROC_PIDFILE" "$PROC_PORT" "$unit"; then
      fail "$PROC_CHECK" "$PROC_WHAT (an earlier detached start) still listens on 127.0.0.1:$PROC_PORT after ${HALT_HOW:-TERM} — run ./setup.sh stop, then ./setup.sh"
      return 1
    fi
  elif port_listener "$PROC_PORT"; then
    if (( active )) || [[ -f "$PROC_PIDFILE" ]]; then
      note "$PROC_WHAT holds 127.0.0.1:$PROC_PORT but does not answer as this install needs — restarting it"
      if ! proc_halt "$PROC_PIDFILE" "$PROC_PORT" "$unit"; then
        fail "$PROC_CHECK" "$PROC_WHAT on 127.0.0.1:$PROC_PORT does not answer as it should and would not stop (${HALT_HOW:-no signal could be sent}) — run ./setup.sh stop, then ./setup.sh"
        return 1
      fi
    elif [[ "$kind" == sandbox && "$(sandbox_auth_code)" == 401 ]]; then
      fail "$PROC_CHECK" "a sandbox runner this install did not start holds 127.0.0.1:$PROC_PORT and REFUSES this install's CC_SANDBOX_RUNNER_TOKEN (401), so every sandbox request the API makes would fail — stop the runner you started by hand (boot starts it now), then run ./setup.sh"
      return 1
    else
      fail "$PROC_CHECK" "port $PROC_PORT is held by something this install did not start, and it does not answer as $PROC_WHAT — stop it, or change the port in .env"
      return 1
    fi
  fi
  if [[ -n "$unit" ]]; then
    note "--> systemctl --user restart $unit (log: $PROC_LOG · stop: ./setup.sh stop)"
    if ! systemctl --user restart "$unit" >&2; then
      fail "$PROC_CHECK" "systemctl --user restart $unit failed — read: systemctl --user status $unit, and $PROC_LOG"
      return 1
    fi
    how="under $unit"
  else
    note "--> starting $PROC_WHAT detached (log: $PROC_LOG · stop: ./setup.sh stop)"
    proc_start_detached
    how="detached, pid file $PROC_PIDFILE"
  fi
  if ! poll_until "$PROC_WAIT" 2 proc_ready "$kind"; then
    if [[ "$kind" == sandbox && "$(sandbox_auth_code)" == 401 ]]; then
      fail "$PROC_CHECK" "$PROC_WHAT answers on 127.0.0.1:$PROC_PORT but refuses this install's CC_SANDBOX_RUNNER_TOKEN — read $PROC_LOG"
    else
      fail "$PROC_CHECK" "$PROC_WHAT never answered at $PROC_URL within ${PROC_WAIT}s — read $PROC_LOG${unit:+ and: systemctl --user status $unit}"
    fi
    return 1
  fi
  proc_record_pid "$unit"
  case "$kind" in
    api) pass "$PROC_CHECK" "API answering at $PROC_URL ($how; first boot hires the roster)" ;;
    *)   pass "$PROC_CHECK" "$PROC_WHAT answering at $PROC_URL ($how)" ;;
  esac
}

# Stop what this install started on <port>, and PROVE the port is free — the
# port is the proof, never the signal: under Git Bash `kill` reported success
# against a native Windows process it never signalled (2026-09-17: "sent
# TERM", the API still answering), and `stop` left node/uvicorn/python
# listening (2026-09-18, 2026-09-25). In order:
#   1. this install's UNIT, through systemd — otherwise Restart= revives what a
#      signal killed (a TERM is a clean exit, so on-failure would not, but the
#      unit's own stop is the honest one);
#   2. the PID FILE: its whole process group where it leads one (a detached
#      start is `setsid`'d — a wrapper's children go with it), and on Windows
#      the native process TREE (`taskkill /T`: uvicorn.exe is a shim that
#      spawns the python that actually listens);
#   3. wait for the port, then on Windows the listener netstat names.
# A pid file whose pid is now some unrelated program (pid reuse) is not
# signalled. Nothing is ever killed by PORT on Linux: what holds a port this
# install has no record of starting is reported, not shot.
# Returns 0 when nothing listens on the port; HALT_HOW says what was done.
proc_halt() { # proc_halt <pidfile> <port> <unit|''>
  local pidf="$1" port="$2" unit="$3" pid="" winpid="" i
  HALT_HOW=""
  if [[ -n "$unit" && -f "$STATE_DIR/systemd/$unit" ]] && command -v systemctl >/dev/null 2>&1; then
    if systemctl --user stop "$unit" >&2; then
      HALT_HOW="systemctl --user stop $unit"
    else
      note "systemctl --user stop $unit failed — falling back to the pid file"
    fi
  fi
  if [[ -f "$pidf" ]]; then
    pid="$(tr -cd '0-9' <"$pidf" 2>/dev/null)"
    [[ -n "$pid" ]] && winpid="$(tr -cd '0-9' <"/proc/$pid/winpid" 2>/dev/null)"
    if [[ -n "$pid" && -z "$HALT_HOW" ]] && proc_pid_is_ours "$pid"; then
      proc_signal "$pid"
      HALT_HOW="TERM to pid $pid"
    fi
    rm -f "$pidf"
  fi
  if [[ -z "$HALT_HOW" ]]; then
    port_listener "$port" || return 0
  else
    for i in $(seq 1 20); do
      port_listener "$port" || return 0
      sleep 1
    done
  fi
  if command -v taskkill >/dev/null 2>&1; then
    if [[ -n "$winpid" ]]; then
      taskkill //T //F //PID "$winpid" >/dev/null 2>&1
      HALT_HOW="${HALT_HOW:+$HALT_HOW, then }taskkill /T of Windows pid $winpid"
      sleep 1
      port_listener "$port" || return 0
    fi
    pid="$(netstat -ano 2>/dev/null | grep LISTENING | grep ":${port} " | awk '{print $NF}' | head -1)"
    if [[ -n "$pid" ]]; then
      taskkill //T //F //PID "$pid" >/dev/null 2>&1
      HALT_HOW="${HALT_HOW:+$HALT_HOW, then }taskkill /T of the listener, Windows pid $pid"
      sleep 1
    fi
    port_listener "$port" || return 0
  fi
  return 1
}

# A recorded pid is signalled only while it is still one of OUR programs —
# after a reboot a pid file can name anything. Where /proc cannot say (the
# process is gone, or this is not Linux), the kill is harmless or the only
# evidence there is.
proc_pid_is_ours() { # proc_pid_is_ours <pid>
  local cl
  [[ -r "/proc/$1/cmdline" ]] || return 0
  cl="$(tr '\0' ' ' <"/proc/$1/cmdline" 2>/dev/null)"
  [[ -z "$cl" || "$cl" == *uvicorn* || "$cl" == *node* || "$cl" == *central_command* ]]
}

# TERM to the pid's whole process group when it leads one (a detached start
# runs under setsid), else to the pid alone.
proc_signal() { # proc_signal <pid>
  local pg
  pg="$(ps -o pgid= -p "$1" 2>/dev/null | tr -d ' ')"
  if [[ "$pg" == "$1" ]] && kill -TERM -- "-$1" 2>/dev/null; then
    return 0
  fi
  kill -TERM "$1" 2>/dev/null || true
}

# ── the bundled skills (D7) ─────────────────────────────────────────────────
# Every skills/<id>/SKILL.md folder this release ships: "<id>\t<abs dir>" per
# line. The id is the FOLDER name, and it is passed to the importer explicitly:
# tests/test_single_boot_supervision.py proves each folder name equals the id
# the importer would derive from its SKILL.md, so a skill an operator imported
# by hand from the same folder is recognised as the same skill.
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
# is still "held", and re-importing it would be undoing their decision.
# 1 = the API did not answer, which is not the same as an empty library.
skills_library_ids() {
  local out
  out="$(curl -fsS -m 15 "$(api_url)/api/skills" 2>/dev/null)" || return 1
  printf '%s' "$out" | $PY -c 'import json,sys; [print(s.get("id","")) for s in json.load(sys.stdin).get("skills",[])]' 2>/dev/null
}

# boot/skills-imported (2026-10-01 record, D7). The importer is the API's own
# `POST /api/skills/import` — body {"path": <a SKILL.md + references/ folder on
# this host>, "skill_id": <id>}, 200 with {"skill_id", "guidance",
# "references"}, 422 with {"detail"} for a folder it cannot read — which reads
# the folder from disk, so it needs neither the cockpit nor node. CREATE-ONLY,
# like register-models.py: a skill the library already holds is never
# re-imported, even when this release changed the bundled copy — the
# operator's library is theirs once a skill is in it. The row's fingerprint
# reads `@skills`, so a release that ADDS a bundled skill re-runs the row and
# imports exactly that one.
boot_skills_import() {
  local have id d body resp code why="" added=() kept=() failed=()
  if ! have="$(skills_library_ids)"; then
    fail "skills-imported" "GET $(api_url)/api/skills did not answer, so the bundled skills were not imported — read $STATE_DIR/uvicorn.log"
    return 1
  fi
  while IFS=$'\t' read -r id d; do
    [[ -n "$id" ]] || continue
    if [[ $'\n'"$have"$'\n' == *$'\n'"$id"$'\n'* ]]; then
      kept+=("$id")
      continue
    fi
    # A path the API's Python can open: on Git Bash that is C:/… (cc_norm_path),
    # never /c/….
    body="$($PY -c 'import json,sys; print(json.dumps({"path": sys.argv[1], "skill_id": sys.argv[2]}))' "$(cc_norm_path "$d")" "$id")"
    note "--> POST $(api_url)/api/skills/import  {skill_id: $id}"
    resp="$(printf '%s' "$body" | curl -sS -m 120 -X POST -H 'content-type: application/json' \
      -w '\n%{http_code}' -d @- "$(api_url)/api/skills/import" 2>&1)"
    code="${resp##*$'\n'}"
    if [[ "$code" == 200 ]]; then
      added+=("$id")
    else
      failed+=("$id")
      why="${resp%$'\n'*}"; why="${why//$'\n'/ }"; why="${why:0:240}"
    fi
  done < <(bundled_skill_dirs)
  if (( ${#failed[@]} )); then
    fail "skills-imported" "could not import ${failed[*]} through POST $(api_url)/api/skills/import: ${why:-no answer} — read $STATE_DIR/uvicorn.log"
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

phase_boot() {
  load_env || return 1
  SUP_MODE="$(boot_supervisor)"
  if ! api_up; then
    # CC_OPERATOR_NAME is the one value only a human can supply. On a
    # terminal, ask it here (elicitation IS allowed to be a prompt — it is
    # the script asking, deterministically); headless, the cockpit asks.
    local opname; opname="$(q_unquote "$(get_kv "$ENV_FILE" CC_OPERATOR_NAME)")"
    if is_placeholder "$opname"; then
      if is_tty; then
        note ""
        note "== one question before first boot =="
        read -rp "What should the agents call you? " opname
        [[ -n "$opname" ]] || { fail "operator-name" "no name given — first boot needs one"; return 1; }
        # Quoted: the answer file is SOURCED, and a two-word name written bare
        # makes every later `set -a; . .env` run the surname as a command.
        set_kv "$ENV_FILE" CC_OPERATOR_NAME "$(q_quote "$opname")"
        pass "operator-name" "CC_OPERATOR_NAME recorded in the root .env"
      else
        # Headless: the cockpit asks on first run (v2.37.0) — gating here made
        # that prompt unreachable on this profile.
        pass "operator-name" "not set — the cockpit asks on first run (agents say 'the operator' until then)"
      fi
    else
      pass "operator-name" "CC_OPERATOR_NAME already set — left alone"
    fi
  fi

  # Which of the three this install runs. The runner only when
  # CC_ENABLE_SANDBOX=1 — off, its row is not applicable (done) and nothing is
  # started. The cockpit only when its server was built: node absent at build
  # time is a WARN, and the API runs without it.
  local kinds=(api) sandbox_on=0 cockpit_on=0
  if [[ "$(p_flag CC_ENABLE_SANDBOX 1)" == 1 ]]; then sandbox_on=1; kinds+=(sandbox); fi
  if [[ -f "$REPO_ROOT/web/server-dist/index.js" ]]; then cockpit_on=1; kinds+=(cockpit); fi

  case "$SUP_MODE" in
    systemd)
      sup_install_units "${kinds[@]}" || return 1
      (( sandbox_on )) || sup_retire_unit sandbox
      ;;
    detached)
      warn "boot-supervisor" "no systemd user manager answers here (a container, or WSL without systemd), so the API, the cockpit and the sandbox runner are started DETACHED and nothing restarts them after a crash or a reboot — run ./setup.sh again after a reboot: it starts whatever is not running"
      ;;
  esac

  proc_boot api || return 1

  if (( sandbox_on )); then
    if is_placeholder "$(get_kv "$ENV_FILE" CC_SANDBOX_RUNNER_TOKEN)"; then
      warn "boot-sandbox" "CC_SANDBOX_RUNNER_TOKEN is blank, so the runner accepts ANY local caller — deploy/single/make-secrets.sh generates it (the llm phase runs it)"
    fi
    proc_boot sandbox || return 1
  else
    pass "boot-sandbox" "CC_ENABLE_SANDBOX is not 1 — the sandbox runner is not applicable on this install, and nothing was started"
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

  boot_skills_import || return 1

  # The cockpit is the Node server in web/ (server-dist), NOT the SPA uvicorn
  # serves from web/dist: every panel is a route or a WebSocket proxy that
  # server owns, so the SPA alone sits at CONNECTING with 404s (2026-09-17
  # Windows run). On k3s it is the cc-nerve unit; here it is the third host
  # process (proc_spec says how it is started).
  local cport; cport="$(cockpit_port)"
  if (( ! cockpit_on )); then
    warn "boot-cockpit" "web/server-dist is missing (node absent at build time?) — the API runs, the cockpit does not"
  else
    proc_boot cockpit || return 1
  fi

  # Windows: the three are host processes, not containers — podman-restart
  # brings the containers back after a reboot, nothing brings these. A
  # logon-triggered scheduled task runs the RESUME command (D6: `./setup.sh`,
  # not `boot` — so an install that never completed leaves a ledger row in
  # boot-at-logon.log rather than silence), retrying while the podman machine
  # starts. The wrapper is tiny; the loop is a bash script beside it
  # (supervise-lib.sh renders both). Linux has the units above instead.
  if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]] && command -v schtasks >/dev/null 2>&1; then
    local wrapper="$STATE_DIR/cc-boot.cmd" retry="$STATE_DIR/boot-at-logon.sh" bashw
    bashw="$(cygpath -w "$(command -v bash)")"
    # The wrapper, the loop and their log live in the state dir with everything
    # else generated; the loop's `cd` is still the checkout, because that is
    # where setup.sh is.
    if ! cc_render_logon_retry "$HERE" "$STATE_DIR/boot-at-logon.log" >"$retry" \
       || ! cc_render_logon_cmd "$bashw" "$retry" >"$wrapper"; then
      warn "boot-at-logon" "could not write $wrapper / $retry — after a reboot, run: ./setup.sh"
    else
      # An onlogon task needs an elevated shell ("Access is denied" otherwise,
      # 2026-09-18); the user's Startup folder needs nothing — same moment, a
      # console window while the run goes. Task first, Startup folder as the
      # fallback.
      local startup="$APPDATA/Microsoft/Windows/Start Menu/Programs/Startup"
      if schtasks //create //f //tn cc-boot //sc onlogon //tr "$(cygpath -w "$wrapper")" >/dev/null 2>&1; then
        pass "boot-at-logon" "scheduled task cc-boot runs ./setup.sh (the resume command) at every logon, retrying while the podman machine starts (log: $STATE_DIR/boot-at-logon.log)"
      elif [[ -d "$startup" ]] && cp "$wrapper" "$startup/cc-boot.cmd" 2>/dev/null; then
        pass "boot-at-logon" "Startup-folder entry cc-boot.cmd runs ./setup.sh (the resume command) at every logon, retrying while the podman machine starts (no elevation; an elevated shell can instead: schtasks /create /f /tn cc-boot /sc onlogon /tr \"$(cygpath -w "$wrapper")\")"
      else
        warn "boot-at-logon" "could not register a logon entry — after a reboot, run: ./setup.sh"
      fi
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
    # pipefail INSIDE the body (D5): a `bash -c` child does not inherit this
    # script's, so a python that failed to read the fixture handed curl an empty
    # body and the step's status was curl's alone.
    step "demo-feed" "fixture email enrolled (a repeat Message-ID is a no-op by design)" \
      bash -c "set -o pipefail; $PY -c'import json,sys,pathlib;print(json.dumps({\"text\":pathlib.Path(sys.argv[1]).read_text()}))' '$eml' \
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

# Stop one of the three and PROVE its port is free (proc_halt above says how).
stop_listener() { # stop_listener <name> <pidfile> <port> <kind>
  local name="$1" pidf="$2" port="$3" kind="$4"
  if proc_halt "$pidf" "$port" "$(sup_unit "$kind")"; then
    pass "stop-$name" "nothing listens on 127.0.0.1:${port}${HALT_HOW:+ ($HALT_HOW)}"
    return 0
  fi
  if [[ -z "$HALT_HOW" ]]; then
    fail "stop-$name" "something listens on 127.0.0.1:${port} that this install has no record of starting (no pid file, no unit) — stop it where it was started; stop proves the port, and it is not free"
  else
    fail "stop-$name" "$name still LISTENS on 127.0.0.1:${port} after $HALT_HOW — stop proves the port, not the signal (a Windows kill can report a success it never delivered). Stop it where it was started, then re-run ./setup.sh stop"
  fi
  return 1
}

# The counterpart of `boot`: all three, through their units where boot wrote
# them. The cockpit first and the API last — the API is what the cockpit
# proxies to, and it is the process a cockpit-driven update is running under.
cmd_stop() {
  CURPHASE=stop
  # D5: `stop` used to run `load_env >/dev/null 2>&1 || true` and then probe
  # the DEFAULT ports, so a stop that could not read .env reported green about
  # ports this install may not even use. It says so now, and fails.
  if ! load_env; then
    fail "stop" "could not load $ENV_FILE (the line above says why), so this install's ports are unknown — nothing was stopped, and nothing is proven down"
    return 1
  fi
  stop_listener cockpit "$STATE_DIR/cockpit.pid" "$(cockpit_port)" cockpit
  if [[ "$(p_flag CC_ENABLE_SANDBOX 1)" == 1 || -f "$STATE_DIR/sandbox.pid" || -f "$(sup_unit_file sandbox)" ]]; then
    stop_listener sandbox "$STATE_DIR/sandbox.pid" "$(sandbox_port)" sandbox
  else
    pass "stop-sandbox" "CC_ENABLE_SANDBOX is not 1 and this install never started a runner — nothing to stop"
  fi
  stop_listener uvicorn "$STATE_DIR/uvicorn.pid" "$(boot_api_port)" api
}

# ─────────────────────────────────────────────────────────────────────────────
# status — postconditions only. Mutates nothing.
# ─────────────────────────────────────────────────────────────────────────────
phase_status() {
  init_state
  # THE LEDGER FIRST (D3): where this install stands is the question `status`
  # is asked, and the table is the answer. The six-key check below stays — it
  # is the cheap cross-check that the ledger's `done` rows are still true.
  ledger_table
  phase_validate || true
  load_env || return 1
  pass "state-dir" "$STATE_DIR (logs, diagnostics, installed.manifest, pids — nothing inside the checkout)"

  if venv_python >/dev/null; then pass "venv" ".venv present"; else fail "venv" ".venv missing — run: ./setup.sh app"; fi
  local k
  for k in CC_LLM_BASE_URL CC_DEFAULT_MODEL CC_LLM_API_KEY CC_EMBED_DIM CC_NEO4J_PASSWORD CC_LITELLM_SALT_KEY; do
    if is_placeholder "$(get_kv "$ENV_FILE" "$k")"; then
      fail "app-${k}" "$k is unset in the app's .env — run: ./setup.sh app"
    else
      pass "app-${k}" "$k is set in the app's .env"
    fi
  done
  step "verify-deployed" "every deployment/configuration assertion passed" "$HERE/verify.sh" || true
  # "status prints the ledger AND the self-check" (D3). WITHOUT --pre-boot:
  # after boot the cockpit and the sandbox runner are part of what the agents
  # rely on, so they are checked too. Read-only, but NOT free — it spends two
  # small model requests (one max_tokens=1 completion, one embedding), which is
  # exactly why the Systems page serves a CACHED result instead of running it
  # on every page load.
  selfcheck_emit || true
}

# ═════════════════════════════════════════════════════════════════════════════
# report — HOW A DEFECT TRAVELS (2026-10-01 design record, D10.3)
# ═════════════════════════════════════════════════════════════════════════════
# One file, built to be pasted into a development session. The rule it serves:
# a deployment never fixes Central Command on site — it reports. So the skill's
# whole instruction on a repository defect is one line (run ./setup.sh report,
# hand over the path, end the turn), and this is the file that makes that
# enough.
#
# NAMES of keys, never values — and since that is a claim rather than a
# mechanism, TWO mechanisms hold it (P2, D11's `report` row, from Replicated
# troubleshoot.sh's redactors):
#   1. IN PROCESS: every section is piped through report_redact AS IT IS
#      COLLECTED, which replaces what deploy/single/redact.tsv declares — the
#      value of every matching .env key, and credential SHAPES .env never knew.
#      A value that turns up in a log is replaced, rather than costing the
#      operator the whole report;
#   2. THE GUARD: the result is still written to a temp file, SCANNED for every
#      credential value .env actually holds, and REFUSED (FAIL, nothing
#      written) if one appears. Unchanged, and kept as the last line of
#      defence — tests/test_single_report_redacts.py keeps its refusal path
#      alive. Both read the SAME list (report_secret_values), so the redactor
#      and the guard cannot disagree about what a secret is.
#
# `diagnose` is an alias for it: the bundle it used to write had a `tail -40`
# window that cut off above wherever the run stopped, which is the one thing a
# support bundle may not do.
env_key_names() { # env_key_names <file>
  local line k v
  [[ -f "$1" ]] || { echo "  (file not present: $1)"; return 0; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
    k="${line%%=*}"; v="${line#*=}"
    if [[ -z "$v" ]]; then echo "  $k = (empty)"; else echo "  $k = (set)"; fi
  done <"$1"
}

# The tool versions, as their own section (the report prints them where D10.3
# asks for them, and diagnose_sections no longer repeats them).
report_tool_versions() {
  local t
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
  return 0
}

# THE WHOLE LOG OF THE LAST RUN — from its last `run start:` line, not a
# `tail -40` window. The 40-line window is what cut off above wherever a run
# stopped, which is the one thing a support bundle may not do. LOG_BEFORE is
# the log's length BEFORE this very run appended its own `run start:`, so
# "the last run" means the one being reported on, never the report itself.
LOG_BEFORE=0
last_run_log() {
  local n total="${LOG_BEFORE:-0}"
  [[ -f "$LOGFILE" ]] || { echo "  (no log yet)"; return 0; }
  [[ "$total" =~ ^[0-9]+$ ]] || total=0
  (( total > 0 )) || { echo "  (no earlier run in the log)"; return 0; }
  n="$(head -n "$total" "$LOGFILE" | grep -n 'run start:' | tail -1 | cut -d: -f1)"
  [[ "$n" =~ ^[0-9]+$ ]] || n=1
  head -n "$total" "$LOGFILE" | tail -n +"$n"
  return 0
}

# The declared default redaction list (D11): data, read by the filter AND by
# the guard below. Same conventions as questions.tsv/steps.tsv.
REDACT_FILE="$HERE/redact.tsv"

# The `key` rows' globs, one per line. q_rows is questions.tsv's reader
# (comments and blank lines dropped, CRLF-tolerant), reused rather than copied.
report_redact_globs() {
  local row
  [[ -f "$REDACT_FILE" ]] || return 0
  while IFS= read -r row; do
    [[ "$(q_field "$row" 1)" == key ]] || continue
    printf '%s\n' "$(q_field "$row" 2)"
  done < <(q_rows "$REDACT_FILE")
  return 0
}

# Every credential VALUE this .env actually holds, as `KEY<TAB>VALUE` — THE
# list, read by both the in-process filter (report_redact) and the scan that
# refuses a leaking report (report_leaking_keys), so the two cannot disagree
# about what a secret is. The sources are redact.tsv's `key` rows (globs over
# key NAMES: the shapes a credential takes in this file, which catch what
# make-secrets.sh and the app phase generate) plus questions.tsv's own
# `secret` column (what configure asks for without echo).
#
# Values shorter than eight characters are skipped on purpose: `none`, `0` and
# `1` are legitimate answers and a substring of half the English language, and
# a refusal on one would make `report` unusable exactly when it is needed.
report_secret_values() {
  local row k v line g matched globs=()
  while IFS= read -r g; do
    [[ -n "$g" && "$g" != "-" ]] && globs+=("$g")
  done < <(report_redact_globs)
  if [[ -f "$QUESTIONS" ]]; then
    while IFS= read -r row; do
      [[ "$(q_field "$row" 8)" == y ]] || continue
      globs+=("$(q_field "$row" 1)")
    done < <(q_rows "$QUESTIONS")
  fi
  [[ -f "$ENV_FILE" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
    k="${line%%=*}"; v="$(q_unquote "${line#*=}")"
    matched=0
    for g in ${globs[@]+"${globs[@]}"}; do
      # Unquoted on the right on purpose: a GLOB match, not a string compare.
      # shellcheck disable=SC2053
      [[ "$k" == $g ]] && { matched=1; break; }
    done
    (( matched )) || continue
    (( ${#v} >= 8 )) || continue
    printf '%s\t%s\n' "$k" "$v"
  done <"$ENV_FILE"
  return 0
}

# ── the in-process redactor (D11, Replicated's redactors) ───────────────────
# Loaded ONCE per report: the values (longest first, so a secret that contains
# another is replaced whole rather than leaving a remnant), and the `shape`
# rows as sed -E expressions. Returns 1 — and the report REFUSES — when
# redact.tsv is missing or declares no `key` row: it is release content, and a
# report that cannot redact is not one to write (steps.tsv's rule, same reason).
REDACT_K=(); REDACT_V=(); REDACT_SED=(); REDACT_WHY=""
report_redact_load() {
  local row kind pat rep nkeys=0 len k v d=$'\001'
  REDACT_K=(); REDACT_V=(); REDACT_SED=(); REDACT_WHY=""
  if [[ ! -f "$REDACT_FILE" ]]; then
    REDACT_WHY="$REDACT_FILE is missing"
    return 1
  fi
  while IFS= read -r row; do
    kind="$(q_field "$row" 1)"; pat="$(q_field "$row" 2)"; rep="$(q_field "$row" 3)"
    case "$kind" in
      key)   nkeys=$((nkeys+1)) ;;
      shape) [[ "$rep" == "-" ]] && rep=""
             REDACT_SED+=(-e "s${d}${pat}${d}${rep}${d}g") ;;
    esac
  done < <(q_rows "$REDACT_FILE")
  if (( nkeys == 0 )); then
    REDACT_WHY="$REDACT_FILE declares no \`key\` row"
    return 1
  fi
  # The length prefix travels through sort's STDIN, never its argv.
  while IFS=$'\t' read -r len k v; do
    [[ -n "$v" ]] || continue
    REDACT_K+=("$k"); REDACT_V+=("$v")
  done < <(report_secret_values | while IFS= read -r row; do
             v="${row#*$'\t'}"
             printf '%s\t%s\n' "${#v}" "$row"
           done | sort -t$'\t' -k1,1nr)
  return 0
}

# THE FILTER every report section is piped through as it is collected. A known
# value becomes [REDACTED:<KEY NAME>] by bash's own substitution — builtins
# only, so a value never reaches an argv (no `sed "s/$value/…"`, no `grep
# "$value"`: `ps` shows argv). The replacement is QUOTED: bash 5.2's
# patsub_replacement would otherwise read an `&` in it as the match. Then the
# shapes, through sed -E — static release content, safe in an argv — applied
# AFTER the values, so a value .env knows keeps its key-name label (the shape
# patterns stop at the `[` of a label already there).
report_redact() {
  local line i
  while IFS= read -r line || [[ -n "$line" ]]; do
    for (( i = 0; i < ${#REDACT_V[@]}; i++ )); do
      [[ "$line" == *"${REDACT_V[i]}"* ]] || continue
      line="${line//"${REDACT_V[i]}"/"[REDACTED:${REDACT_K[i]}]"}"
    done
    printf '%s\n' "$line"
  done | if (( ${#REDACT_SED[@]} )); then LC_ALL=C sed -E "${REDACT_SED[@]}"; else cat; fi
}

# One report section: its heading, then whatever <cmd...> prints — stderr too,
# a section's errors are part of the evidence — THROUGH the filter.
report_section() { # report_section <heading> <cmd...>
  printf '%s\n' "$1"; shift
  "$@" 2>&1 | report_redact
  echo
}

# The self-check's lines (D10.3), without --pre-boot: a report is about the
# install as it stands. A SHORT timeout, because a report is collected FROM a
# broken install and must not hang on it; and a module that is absent, cannot
# import, or fails is PRINTED — it never aborts the report.
report_selfcheck() {
  local py rc=0
  if ! py="$(venv_python)"; then
    echo "  (no .venv python on this install — the self-check cannot run yet; the fetch and app phases create it)"
    return 0
  fi
  ( cd "$REPO_ROOT" && "$py" -m central_command.selfcheck --timeout 10 ) 2>&1 || rc=$?
  echo "  (exit $rc — 0 all passed · 1 a check failed · 2 warnings only; anything else, or no check line above, means the module could not run)"
  return 0
}

report_manifest() {
  cat "$STATE_DIR/installed.manifest" 2>/dev/null || echo "  (no manifest — the fetch phase writes it)"
  return 0
}

report_process_logs() {
  local t
  for t in uvicorn cockpit sandbox; do
    if [[ -f "$STATE_DIR/$t.log" ]]; then
      echo "---- $t.log"
      tail -100 "$STATE_DIR/$t.log" 2>&1
    else
      echo "---- $t.log (not present)"
    fi
  done
  return 0
}

# The KEY NAMES whose value appears in <file>. A bash substring test, never a
# `grep <value>`: a value in an argv is a value in `ps`.
report_leaking_keys() { # report_leaking_keys <file>
  local blob row k v out=""
  blob="$(cat "$1" 2>/dev/null)" || return 0
  while IFS= read -r row; do
    k="${row%%$'\t'*}"; v="${row#*$'\t'}"
    [[ -n "$v" ]] || continue
    [[ "$blob" == *"$v"* ]] && out="${out:+$out }$k"
  done < <(report_secret_values)
  printf '%s' "$out"
  return 0
}

# Every `failed`, `gate` or `started` row, with its reason and the NAMES of the
# .env keys that shaped it — which is what turns "a row is red" into "change
# one of these keys, or report the row". `started` (D11) belongs here: a run
# that was interrupted inside a phase is exactly what a report is asked about.
report_open_rows() {
  local line step st reason qual phase name reads any=0
  while IFS= read -r line; do
    IFS=$'\t' read -r step st _ _ _ reason <<<"$line"
    [[ "$st" == failed || "$st" == gate || "$st" == started ]] || continue
    any=1
    qual="$step"; phase="${qual%%/*}"; name="${qual#*/}"
    reads="$(cc_step_field "$phase" "$name" reads)" || reads=""
    echo "  $qual [$st]"
    echo "      reason: ${reason:-(none recorded)}"
    echo "      inputs: ${reads:-(none)}"
  done < <(cc_ledger_read "$LEDGER")
  (( any )) || echo "  (no failed or waiting row)"
  return 0
}

diagnose_sections() {
  local ctrs c
    echo "== host"
    uname -a 2>&1
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
    compose_detect && MSYS2_ENV_CONV_EXCL="${MSYS2_ENV_CONV_EXCL:+${MSYS2_ENV_CONV_EXCL};}${COMPOSE_ENV_CONV_EXCL}" \
      "${COMPOSE_BIN[@]}" --env-file "$ENV_FILE" -f "$HERE/compose.yaml" ps -a 2>&1 || echo "  (no compose provider)"
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
    # A DEFAULT, not an override: 15s is the right budget for a bundle, and a
    # caller that has already said how long it is willing to wait meant it.
    CC_VERIFY_MAX_WAIT="${CC_VERIFY_MAX_WAIT:-15}" "$HERE/verify.sh" 2>&1
  return 0
}

cmd_report() {
  CURPHASE=report
  init_state
  # Quietly: the report's own protocol line is the one this command prints.
  load_env >/dev/null 2>&1 || true
  : "${CC_POD_PREFIX:=cc-}"
  local out tmp
  out="$STATE_DIR/report-$(date -u +%Y%m%dT%H%M%SZ).txt"
  tmp="$out.partial"
  # The redaction list FIRST: without it nothing below can be redacted, and a
  # report that cannot redact is not written at all.
  if ! report_redact_load; then
    fail "report" "REFUSING to write the report: $REDACT_WHY — it is release content (re-extract the release), and without it nothing in the report can be redacted. Nothing was written"
    return 1
  fi
  : >"$tmp" && chmod 600 "$tmp" 2>/dev/null
  # Every section through report_redact AS IT IS COLLECTED (D11) — the
  # headings are this script's own text and carry nothing to redact.
  {
    echo "Central Command single-node install report — $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "Built to be pasted into a DEVELOPMENT session. Values were REDACTED IN PROCESS"
    echo "(deploy/single/redact.tsv); a scan for every .env credential is the second check."
    echo "The log section below is the WHOLE last run, not a window."
    echo
    report_section "== state directory (everything this install GENERATES is in here;
   nothing is written inside the checkout)" echo "  $STATE_DIR"
    report_section "== the ledger — what completed, at which release, with which inputs" \
      ledger_table
    report_section "== every failed or waiting row, with its reason and its inputs" \
      report_open_rows
    report_section "== the self-check (python -m central_command.selfcheck, short timeout)" \
      report_selfcheck
    report_section "== the whole log of the last run" last_run_log
    report_section "== .env — the one answer file (names only)" env_key_names "$ENV_FILE"
    report_section "== VERSION (what the updaters read as the installed version)" \
      cat "$REPO_ROOT/VERSION"
    report_section "== tool versions" report_tool_versions
    report_section "== installed.manifest (what resolve-images.sh actually wrote)" \
      report_manifest
    report_section "== the processes this install starts (last 100 lines each)" \
      report_process_logs
    # Container logs are the likeliest place for a credential of all.
    diagnose_sections 2>&1 | report_redact
  } >>"$tmp"
  # Belt and braces (D10.3): the redactor above replaced what redact.tsv
  # declares, and this is the check that it did — the SAME list, scanned for
  # verbatim. A leak is a FAIL and nothing is written.
  local leaked; leaked="$(report_leaking_keys "$tmp")"
  if [[ -n "$leaked" ]]; then
    rm -f "$tmp"
    fail "report" "REFUSING to write the report: the VALUE of $leaked appeared in it, and this file is meant to be pasted into a chat. Nothing was written. That is a defect in the report itself — report it with the ledger from ./setup.sh status instead"
    return 1
  fi
  mv -f "$tmp" "$out" || { rm -f "$tmp"; fail "report" "could not write $out — check the permissions on $STATE_DIR"; return 1; }
  chmod 600 "$out" 2>/dev/null
  pass "report" "state dir is $STATE_DIR; wrote $out — hand that path to a development session (key NAMES only, never a value)"
  return 0
}

# ═════════════════════════════════════════════════════════════════════════════
# THE PROBES — one per steps.tsv row: "is this step's effect present NOW?"
# ═════════════════════════════════════════════════════════════════════════════
# 2026-10-01 design record, D1/D2. Three rules every function below lives
# under, because a probe is the one thing the driver trusts:
#
#   * it MUTATES NOTHING. tests/test_single_steps_schema.py runs the same
#     source walk over every probe that keeps `check` dry — a pull, a build, a
#     `compose up`, an install or a make-secrets call fails the suite;
#   * it is CHEAP. No probe spends a token and none waits on a deadline: the
#     llm round-trip rows read the proxy's catalog rather than asking for
#     another completion, and what records that the real round trip was made
#     is the ledger row's VERSION + FINGERPRINT. (The two requests that do
#     prove what an agent will experience are P2's self-check, by design — see
#     D4.);
#   * "NOT APPLICABLE is DONE". A step a flag turns off returns 0, because a
#     component this install does not have is not an unfinished step. That is
#     what keeps a speech-less or n8n-less deployment from blocking on rows it
#     will never perform.
#
# A credential is read with get_kv and, where one must travel, goes through
# `-H @-` (stdin) so it is never in an argv for `ps` to show.

# A flag's effective value: this shell's (load_env has sourced .env), else the
# answer file's, else the default load_env would have applied.
p_flag() { # p_flag <key> <default>
  local v="${!1:-}"
  [[ -n "$v" ]] || v="$(q_unquote "$(get_kv "$ENV_FILE" "$1")")"
  printf '%s' "${v:-$2}"
}

# 0 = this step does NOT apply (the flag is not at its on-value), so the caller
# returns done. Reads as `p_off CC_ENABLE_N8N 0 1 && return 0`.
p_off() { # p_off <flag-key> <default> <on-value>
  [[ "$(p_flag "$1" "$2")" == "$3" ]] && return 1
  return 0
}

# A derived, minted or generated .env key carries a real value.
p_kv_set() { # p_kv_set <key>
  is_placeholder "$(get_kv "$ENV_FILE" "$1")" && return 1
  return 0
}

p_always() {
  # For a step with no artifact to read — the suite's green, the catalog line
  # that is a PASS either way. The ledger's version+fingerprint is the whole
  # record, which is exactly how `test` becomes "once per release" without the
  # api_up proxy it used to skip on.
  return 0
}

p_env_file() {
  [[ -f "$ENV_FILE" ]]
}

p_venv() {
  venv_python >/dev/null
}

p_install() {
  local py
  py="$(venv_python)" || return 1
  ( cd "$REPO_ROOT" && "$py" -c 'import central_command' ) >/dev/null 2>&1
}

# node >= 22, i.e. is the cockpit in scope on this host at all? The phases WARN
# and carry on without it, so every cockpit row probes 0 when it is absent.
p_node_ok() {
  command -v node >/dev/null 2>&1 || return 1
  local nv
  nv="$(node -v 2>/dev/null)"; nv="${nv#v}"
  [[ "${nv%%.*}" =~ ^[0-9]+$ ]] || return 1
  (( ${nv%%.*} >= 22 ))
}

p_cockpit_npm() {
  p_node_ok || return 0
  [[ -d "$REPO_ROOT/web/node_modules" ]]
}

p_cockpit_build() {
  p_node_ok || return 0
  [[ -f "$REPO_ROOT/web/server-dist/index.js" ]] || return 1
  [[ -d "$REPO_ROOT/web/dist" ]]
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

# ── machine ─────────────────────────────────────────────────────────────────
p_machine() {
  [[ -n "$(machine_name)" ]] || return 0
  machine_sh 'true' >/dev/null 2>&1
}

p_machine_native_ca() {
  [[ -n "$(machine_name)" ]] || return 0
  podman machine set --help 2>/dev/null | grep -q -- '--import-native-ca' || return 0
  [[ -f "$STATE_DIR/machine.import-native-ca" ]]
}

p_machine_ca() {
  [[ -n "$(machine_name)" ]] || return 0
  local ca want cur
  ca="$(q_unquote "$(get_kv "$ENV_FILE" CC_CA_BUNDLE)")"
  [[ -n "$ca" ]] || return 0
  [[ -r "$ca" ]] || return 1
  want="$(cat "$ca")"
  cur="$(machine_sh "cat '$CC_MACHINE_CA_PEM' 2>/dev/null")"
  [[ "$cur" == "$want" ]]
}

p_machine_ca_probe() {
  [[ -n "$(machine_name)" ]] || return 0
  [[ -n "$(q_unquote "$(get_kv "$ENV_FILE" CC_CA_BUNDLE)")" ]] || return 0
  local host rc=0
  host="$(cc__mhost "$(q_unquote "$(get_kv "$ENV_FILE" CC_REGISTRY_DOCKERIO)")")"
  [[ -n "$host" ]] || host="registry-1.docker.io"
  machine_sh "curl -fsSI --max-time 15 https://${host}/v2/ >/dev/null" || rc=$?
  # Only curl 60 is a TRUST verdict. Anything else is reachability, which the
  # phase itself reports as a WARN — a probe that read it as failure would turn
  # an air-gapped machine into a permanently blocked install.
  (( rc != 60 ))
}

p_machine_registries() {
  [[ -n "$(machine_name)" ]] || return 0
  local want cur
  want="$(cc_render_registries_conf)"
  [[ -n "$want" ]] || return 0
  cur="$(machine_sh "cat '$CC_MACHINE_REGISTRIES_CONF' 2>/dev/null")"
  [[ "$cur" == "$want" ]]
}

p_machine_proxy() {
  [[ -n "$(machine_name)" ]] || return 0
  local want cur
  want="$(cc_render_proxy_conf)"
  [[ -n "$want" ]] || return 0
  cur="$(machine_sh "cat '$CC_MACHINE_PROXY_CONF' 2>/dev/null")"
  [[ "$cur" == "$want" ]]
}

# ── fetch ───────────────────────────────────────────────────────────────────
# The CC_IMG_* names images.txt implies FOR THESE FLAGS — the resolver's own
# derivation (its `img_var`), mirrored here so a probe needs no second list of
# images and a disabled component's image is never demanded.
p_image_vars() {
  local line path comp n
  [[ -f "$HERE/images.txt" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    line="${line%%#*}"
    [[ -n "${line//[[:space:]]/}" ]] || continue
    # shellcheck disable=SC2086
    set -- $line
    (( $# == 6 )) || continue
    path="$2"; comp="$6"
    case "$comp" in
      n8n)          p_off CC_ENABLE_N8N 0 1 && continue ;;
      speech)       p_off CC_ENABLE_SPEECH 1 1 && continue ;;
      sandbox-base) p_off CC_ENABLE_SANDBOX 1 1 && continue ;;
      crawler-base) p_off CC_ENABLE_CRAWLER 1 1 && continue ;;
    esac
    n="${path^^}"; n="${n//[\/-]/_}"
    printf 'CC_IMG_%s\n' "${n#LIBRARY_}"
  done <"$HERE/images.txt"
  return 0
}

p_resolve_images() {
  [[ -f "$STATE_DIR/installed.manifest" ]] || return 1
  # The manifest records what the resolver DID; .env is what the deploy READS,
  # and compose interpolates from there. A manifest without the keys is the
  # v2.48.0 shape of defect, so both halves are probed.
  local var
  while IFS= read -r var; do
    [[ -n "$var" ]] || continue
    [[ -n "$(get_kv "$ENV_FILE" "$var")" ]] || return 1
  done < <(p_image_vars)
  return 0
}

p_img() { # p_img <CC_IMG_var>
  local ref
  ref="$(get_kv "$ENV_FILE" "$1")"
  [[ -n "$ref" ]] || return 1
  have_image "$ref"
}

p_image_postgres() {
  p_img CC_IMG_POSTGRES
}

p_image_neo4j() {
  p_img CC_IMG_NEO4J
}

p_image_redis() {
  p_img CC_IMG_REDIS
}

p_image_berriai_litellm_database() {
  p_img CC_IMG_BERRIAI_LITELLM_DATABASE
}

p_image_n8nio_n8n() {
  p_off CC_ENABLE_N8N 0 1 && return 0
  p_img CC_IMG_N8NIO_N8N
}

p_image_zepai_knowledge_graph_mcp() {
  p_img CC_IMG_ZEPAI_KNOWLEDGE_GRAPH_MCP
}

p_image_python() {
  p_off CC_ENABLE_SANDBOX 1 1 && return 0
  p_img CC_IMG_PYTHON
}

p_image_playwright_python() {
  p_off CC_ENABLE_CRAWLER 1 1 && return 0
  p_img CC_IMG_PLAYWRIGHT_PYTHON
}

p_image_speaches_ai_speaches() {
  p_off CC_ENABLE_SPEECH 1 1 && return 0
  p_img CC_IMG_SPEACHES_AI_SPEACHES
}

# The three LOCAL images: done means the tag is present AND its build-inputs
# label equals this tree's (v2.57.0, local_image_state). That comparison is
# what re-runs these rows when a release changes a Dockerfile or its context —
# their `reads` name only .env keys, and a file is not one.
p_image_graphiti() {
  [[ "$(local_image_state "localhost/cc-graphiti:$(p_flag CC_GRAPHITI_TAG 1.0.2-anthropic)" "$HERE/build-graphiti-image.sh")" == current ]]
}

p_image_sandbox() {
  p_off CC_ENABLE_SANDBOX 1 1 && return 0
  [[ "$(local_image_state "localhost/cc-sandbox:1" "$HERE/build-sandbox-image.sh")" == current ]]
}

p_image_crawler() {
  p_off CC_ENABLE_CRAWLER 1 1 && return 0
  [[ "$(local_image_state "localhost/cc-crawler:1" "$HERE/build-crawler-image.sh")" == current ]]
}

# ── llm ─────────────────────────────────────────────────────────────────────
p_secrets() {
  local k
  for k in "${CC_GENERATED_KEYS[@]}"; do
    is_placeholder "$(get_kv "$ENV_FILE" "$k")" && return 1
  done
  return 0
}

p_litellm_live() {
  curl -fsS -m 5 -o /dev/null \
    "http://127.0.0.1:$(p_flag CC_LITELLM_PORT 4000)/health/liveliness" 2>/dev/null
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

# The proxy's catalog, read under the ADMIN key. One GET, no tokens.
p_models_json() {
  local key
  key="$(get_kv "$ENV_FILE" CC_LLM_PROXY_ADMIN_KEY)"
  [[ -n "$key" ]] || return 1
  printf 'Authorization: Bearer %s\n' "$key" \
    | curl -fsS -m 15 -H @- "http://127.0.0.1:$(p_flag CC_LITELLM_PORT 4000)/v1/models" 2>/dev/null
}

p_alias() { # p_alias <alias>
  local listed
  listed="$(p_models_json)" || return 1
  [[ "$listed" == *"\"$1\""* ]]
}

# The gate's probe (llm/catalog-filled): every alias THIS deployment requires
# answers /v1/models through the admin key. cc_required_aliases is the ONE list.
p_catalog_aliases() {
  local listed a
  listed="$(p_models_json)" || return 1
  for a in $(cc_required_aliases); do
    [[ "$listed" == *"\"$a\""* ]] || return 1
  done
  return 0
}

p_alias_cc_default() {
  p_alias cc-default
}

p_alias_graphiti_llm() {
  p_alias graphiti-llm
}

p_alias_gpt_4_1_nano() {
  p_alias gpt-4.1-nano
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

p_embed_dim() {
  p_kv_set CC_EMBED_DIM
}

# ── stack ───────────────────────────────────────────────────────────────────
p_up_stack() {
  local gp
  gp="$(p_flag CC_GRAPHITI_PORT 8000)"
  p_litellm_live || return 1
  # The spine's Postgres speaks no HTTP, and Graphiti's MCP root is not a
  # health page — a listener is the honest evidence for both.
  port_listener "$(p_flag CC_PG_PORT 5442)" || return 1
  port_listener "$gp" || return 1
  # Up is not enough: every enabled service's container must run the image its
  # ref resolves to NOW (v2.57.0), or the row is not done — a rebuilt local
  # image or a re-pulled tag otherwise hides behind a healthy old container.
  # Two read-only podman calls (image_drift).
  image_drift || return 1
  return 0
}

p_restart_on_boot() {
  local out
  [[ -n "$(podman machine list --format '{{.Name}}' 2>/dev/null)" ]] || return 0
  out="$(podman machine ssh -- 'systemctl --global is-enabled podman-restart.service 2>/dev/null || true' </dev/null 2>/dev/null | tr -d ' \r')"
  # An answer this cannot read is not evidence of absence: the phase WARNs when
  # the enable fails, and a probe inventing a FAIL out of silence would stop an
  # install over a unit query.
  [[ -z "$out" ]] && return 0
  [[ "$out" == enabled* ]]
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

# ── boot ────────────────────────────────────────────────────────────────────
p_operator_name() {
  is_placeholder "$(q_unquote "$(get_kv "$ENV_FILE" CC_OPERATOR_NAME)")" || return 0
  # Headless, the cockpit asks on first run (v2.37.0) — gating here is what
  # made that prompt unreachable on this profile, so a nameless headless
  # install is complete and the row is done.
  is_tty && return 1
  return 0
}

p_boot_api() {
  api_up
}

p_boot_roster() {
  local n
  n="$(api_json "$(api_url)/api/agents" 'len(d.get("agents", d if isinstance(d, list) else []))')"
  [[ "$n" =~ ^[0-9]+$ ]] || return 1
  (( n > 0 ))
}

p_boot_cockpit() {
  # No server build means no cockpit on this host; the phase WARNs and the API
  # runs without it.
  [[ -f "$REPO_ROOT/web/server-dist/index.js" ]] || return 0
  curl -fsS -m 5 -o /dev/null \
    "http://127.0.0.1:$(p_flag CC_COCKPIT_PORT 3080)/" 2>/dev/null
}

# boot/boot-sandbox: off is done; on, the runner answers on its port AND takes
# this install's token (a 401 is a runner every API request would fail
# against, which is not "running" in any sense that matters).
p_boot_sandbox() {
  p_off CC_ENABLE_SANDBOX 1 1 && return 0
  proc_ready sandbox
}

# boot/skills-imported: every bundled skill id is in GET /api/skills (one GET;
# the API is up by the time this row runs).
p_skills_imported() {
  local have id d
  have="$(skills_library_ids)" || return 1
  while IFS=$'\t' read -r id d; do
    [[ -n "$id" ]] || continue
    [[ $'\n'"$have"$'\n' == *$'\n'"$id"$'\n'* ]] || return 1
  done < <(bundled_skill_dirs)
  return 0
}

p_boot_at_logon() {
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*) ;;
    *) return 0 ;;
  esac
  [[ -f "$STATE_DIR/cc-boot.cmd" && -f "$STATE_DIR/boot-at-logon.sh" ]]
}

# ── demo ────────────────────────────────────────────────────────────────────
p_demo_fed() {
  demo_decided && return 0
  demo_awaiting
}

p_demo_decided() {
  demo_decided
}

# ═════════════════════════════════════════════════════════════════════════════
# THE LEDGER — what completed, at which version, with which inputs (D2)
# ═════════════════════════════════════════════════════════════════════════════
# The updaters read VERSION as the installed version — not git, not the tag.
installed_version() {
  local v
  v="$(sed -n 's/^version=//p' "$REPO_ROOT/VERSION" 2>/dev/null | head -1 | tr -d ' \r')"
  printf '%s' "${v:-unknown}"
}

# The whole ledger, as a table. On stdout, because it is the answer to
# `./setup.sh status` and the first section of a report — the one thing an
# operator (or a reviewing agent) reads to know where an install stands.
ledger_table() {
  local line step st ver at fp reason
  printf 'LEDGER %s\n' "$LEDGER"
  # A run in progress is part of "where does this install stand" — `status`
  # and `report` never take the lock, so they are how somebody waiting on one
  # learns whose it is. Not printed for this run's own lock, nor for the
  # update.sh this run is nested under.
  if cc__lock_read "$STATE_DIR" && [[ -z "$RUN_LOCK_HOLDER_PID" \
     || ( "$RUN_LOCK_HOLDER_PID" != "$$" && "$RUN_LOCK_HOLDER_PID" != "${CC_RUN_LOCK_PID:-}" ) ]]; then
    printf 'RUN-LOCK held by pid %s running "%s" since %s (%s)\n' \
      "${RUN_LOCK_HOLDER_PID:--}" "${RUN_LOCK_HOLDER_CMD:--}" "${RUN_LOCK_HOLDER_AT:--}" \
      "$(cc_lock_pid_is_run "$RUN_LOCK_HOLDER_PID" && printf 'alive' || printf 'gone — the next ./setup.sh reclaims it')"
  fi
  printf '%-34s %-7s %-9s %-21s %s\n' "step" "status" "version" "at" "reason"
  while IFS= read -r line; do
    IFS=$'\t' read -r step st ver at fp reason <<<"$line"
    printf '%-34s %-7s %-9s %-21s %s\n' "$step" "$st" "$ver" "$at" "${reason:--}"
  done < <(cc_ledger_read "$LEDGER")
  return 0
}

# D11's `started` (dpkg's half-configured): every row of the phase, in ONE
# atomic rewrite, immediately before the phase function runs. Called only
# once the phase has passed its ledger gate — a REFUSED phase did not start,
# and a phase skipped as done is not run at all.
ledger_mark_started() { # ledger_mark_started <phase>
  cc_ledger_mark_started "$LEDGER" "$1" "$(installed_version)" "$(date -u +%FT%TZ)" "$ENV_FILE" \
    || warn "ledger" "could not mark $1's rows started in $LEDGER — if this run is interrupted the ledger will not say where"
  return 0
}

# After the phase function returns: record EVERY row of the phase from the
# reality its probe reads, never from what the phase said it did — except that
# a row that SAID it failed failed (STEP_SAID above).
#
# `<phase-reported-success>` is "no FAIL and no USERACTION" — and a row probing
# false after that is the defect this whole mechanism exists for: the step after
# the one that failed never ran, and nothing said so. It becomes a line.
#
# All of the phase's rows are written in ONE rewrite, overwriting the
# `started` mark ledger_mark_started left on each.
ledger_record() { # ledger_record <phase> <phase-reported-success:0|1>
  local phase="$1" ok="$2" step kind probe qual fp st now ver reason said
  local rows=()
  now="$(date -u +%FT%TZ)"
  ver="$(installed_version)"
  while IFS= read -r step; do
    [[ -n "$step" ]] || continue
    cc__step_split "$phase" "$step" || continue
    kind="$ROWDEF_KIND"; probe="$ROWDEF_PROBE"
    qual="$phase/$step"
    fp="$(cc_fingerprint "$ENV_FILE" "$ROWDEF_READS")"
    reason="${STEP_MSG[$step]:-}"
    said="${STEP_SAID[$step]:-}"
    if [[ "$said" == FAIL ]]; then
      # It printed FAIL <step> in this run: failed, with that message, however
      # true its probe reads. The probe proves an effect EXISTS; it cannot
      # unsay a step that reported it did not do its job.
      st=failed
    elif [[ "$said" == USERACTION ]]; then
      # ...and a USERACTION is the operator's move: waiting, not broken.
      st=gate
    elif "$probe" >/dev/null 2>&1; then
      st=done; reason=""
      # A row whose ONLY evidence is the phase's own verdict — `p_always`: the
      # suite's green, the catalog line that is a PASS either way — may not be
      # recorded done on a phase that did not report success. Otherwise a RED
      # suite, or a phase refused by the tree-pristine guard before it did
      # anything, would write itself into the ledger as finished, which is the
      # exact class of defect the ledger exists to end.
      if [[ "$probe" == "p_always" ]] && (( ! ok )); then
        st=failed
        reason="${STEP_MSG[$step]:-the phase did not report success, and this step has no artifact of its own to read}"
      fi
    elif [[ "$kind" == gate || "$kind" == human ]]; then
      # The llm catalog pause and the demo approval: not broken, waiting.
      st=gate
    else
      st=failed
    fi
    if [[ "$st" != done ]] && (( ok )); then
      fail "$step" "phase reported success but $qual's effect is absent ($probe returned non-zero) — this is the step nobody told you about"
      st=failed
      reason="${STEP_MSG[$step]:-}"
    fi
    rows+=("$qual" "$st" "$ver" "$now" "$fp" "$reason")
  done < <(cc_steps_for_phase "$phase")
  (( ${#rows[@]} )) || return 0
  cc_ledger_write_batch "$LEDGER" "${rows[@]}" \
    || warn "ledger" "could not write $LEDGER — this phase will simply run again"
  return 0
}

# Is this phase already DONE — every row `done`, at THIS version, with the same
# input fingerprint, and every probe still true (D2's rule 2)? Prints one
# "<step>\t<at>" line per row when it is, so the caller can report what it
# skipped rather than skipping silently.
#
# The judgement is cc_phase_decide's (ledger-lib.sh) — the SAME function the
# plan prints from, so the plan and this skip cannot disagree about a ledger
# (only about a world a phase that ran in between has changed).
phase_is_done() { # phase_is_done <phase>
  cc_phase_decide "$LEDGER" "$1" "$(installed_version)" "$ENV_FILE"
  [[ "$PHASE_VERDICT" == skip ]] || return 1
  printf '%s' "$PHASE_DONE_LINES"
  return 0
}

# The DEVELOPER bypass (D3), documented only in .claude/rules/deploy-single.md
# and never in an operator document. There is no --force: the operator decided
# on 2026-10-01 that the escape hatch is the defect. It is REFUSED outright on
# an installation whose Executor is live, because running a phase ahead of its
# prerequisites there means proposing against half a deployment.
#
# unledgered_permitted is the silent half, which the plan asks; the gate asks
# unledgered_allowed, which also says why when it refuses.
unledgered_permitted() {
  [[ "${CC_SETUP_UNLEDGERED:-0}" == "1" ]] || return 1
  [[ "$(get_kv "$ENV_FILE" CC_EXECUTOR_MODE)" != "live" ]]
}

unledgered_allowed() {
  [[ "${CC_SETUP_UNLEDGERED:-0}" == "1" ]] || return 1
  if ! unledgered_permitted; then
    fail "unledgered" "CC_SETUP_UNLEDGERED=1 is REFUSED while .env carries CC_EXECUTOR_MODE=live — out-of-order phases on a live deployment is the failure mode the ledger exists to end. Run ./setup.sh (it resumes in order)"
    return 1
  fi
  return 0
}

# What a `requires` row's status MEANS, in a refusal. `started` is the one that
# needs saying: the ledger is not merely incomplete there, a run was killed or
# lost inside that phase.
ledger_status_words() { # ledger_status_words <status>
  case "$1" in
    started) printf 'started — an earlier run was interrupted inside that phase before it finished' ;;
    *)       printf '%s' "$1" ;;
  esac
}

# ── the plan (D11, Terraform's `plan`) ──────────────────────────────────────
# Printed at the start of every run that will run phases — after the lock is
# taken and the manifest loaded, before anything executes: which phases WILL
# RUN and which WILL SKIP, and why, in terms of their rows. It changes nothing
# and counts nothing (not a PASS/WARN/FAIL line: `PLAN ` on stdout, and in the
# log). Every verdict comes from cc_phase_decide, the function the full run's
# skip asks too; a probe is evaluated only for a row the ledger would otherwise
# skip, so a fresh install's plan reads no probe at all. Key NAMES only.
plan_line() { # plan_line <text>
  printf 'PLAN %s\n' "$1"
  logline "PLAN $1"
}

ledger_plan() { # ledger_plan all | ledger_plan <phase>
  local ver p blocked text
  ver="$(installed_version)"
  plan_line "./setup.sh ${1} at ${ver} — a PREDICTION made before anything runs: a phase that runs can change what a later phase finds (fetch rewrites image refs, llm measures CC_EMBED_DIM), so each phase is judged again when the run reaches it"
  if [[ "$1" == all ]]; then
    for p in $(cc_steps_phases); do
      if [[ "$p" == check ]]; then
        plan_line "check: WILL RUN — always: the dry gate proves every input before anything changes"
        continue
      fi
      cc_phase_decide "$LEDGER" "$p" "$ver" "$ENV_FILE"
      plan_line "$(cc_phase_plan_text "$p" "$ver")"
    done
    return 0
  fi
  # A phase named on the command line (the DEVELOPER form) is never skipped as
  # done — it is the ledger GATE that decides whether it runs at all.
  p="$1"
  if blocked="$(cc_ledger_blocked "$LEDGER" "$p")" && ! unledgered_permitted; then
    plan_line "$p: WILL NOT RUN — it requires ${blocked%% *}, which is $(ledger_status_words "${blocked#* }"); the ledger refuses a phase whose prerequisites are not done"
    return 0
  fi
  if [[ "$p" == check ]]; then
    text="check: WILL RUN — always: it is the dry gate"
  else
    cc_phase_decide "$LEDGER" "$p" "$ver" "$ENV_FILE"
    if [[ "$PHASE_VERDICT" == skip ]]; then
      text="$p: WILL RUN — asked for by name, though all $PHASE_NROWS of its rows are done at $ver with the same inputs and every effect still reads present (last done ${PHASE_LAST_AT:--}): a named phase runs regardless"
    else
      text="$(cc_phase_plan_text "$p" "$ver")"
    fi
  fi
  [[ -n "$blocked" ]] && text="$text — OUT OF ORDER under CC_SETUP_UNLEDGERED=1 (${blocked%% *} is ${blocked#* })"
  plan_line "$text"
  return 0
}

# ── one run at a time (D11, Kamal's lock directory) ─────────────────────────
# The lock functions are deploy/env-lib.sh's (update.sh takes the same lock).
# Returns 0 to go on, 1 when another LIVE run holds it — after a FAIL line and
# before anything else has happened: no plan, no `started` row, no phase.
#
# A STALE lock (its holder provably gone) is reclaimed with a WARN that is
# printed and logged but NOT counted: a reclaim is the recovery working, and
# counted it would turn check's verdict into a WARN-only gate — a logon-time
# ./setup.sh after a power cut, with no terminal to say `y`, would then stop
# behind the very lock it just cleared.
run_lock_take() { # run_lock_take <command-text>
  if cc_lock_acquire "$STATE_DIR" "$1"; then
    case "$RUN_LOCK_RESULT" in
      acquired)  cc_lock_trap "$STATE_DIR" ;;
      reclaimed) cc_lock_trap "$STATE_DIR"
                 printf 'WARN run-lock: %s\n' "$(cc_lock_reclaim_text "$STATE_DIR")"
                 logline "WARN run-lock: $(cc_lock_reclaim_text "$STATE_DIR")" ;;
      nested)    : ;;   # our parent's lock (update.sh): neither take nor release it
    esac
    # setup.sh never runs a nested setup.sh or update.sh, but `boot` starts
    # LONG-LIVED processes (the API, the cockpit) and they would inherit the
    # nesting marker. The API spawns update-run.sh -> update.sh apply, which,
    # if this run were still alive, would read that marker as "my parent holds
    # the lock" and run beside it. So the marker stops at this process.
    export -n CC_RUN_LOCK_PID 2>/dev/null || true
    return 0
  fi
  fail "run-lock" "$(cc_lock_refusal_text "$STATE_DIR")"
  return 1
}

# THE LEDGER'S FIRST RULE (D2): never run ahead. A phase whose requires are
# not all `done` is refused WITHOUT running — which is what `./setup.sh boot`
# after a failed `app` used to be allowed to do, and is exactly the 2026-10-01
# work-site state (an empty CC_LLM_API_KEY, eight blank Systems links, a green
# `verify`). Returns 0 to continue, 1 to refuse (the FAIL is already printed).
#
# Shared with the `machine` branch of main(), which takes a flag of its own and
# so cannot go through run_phase: one gate, or `./setup.sh machine` would be
# the hole in a rule with no other hole in it.
phase_ledger_gate() { # phase_ledger_gate <phase>
  local blocked
  if blocked="$(cc_ledger_blocked "$LEDGER" "$1")"; then
    if unledgered_allowed; then
      warn "$1" "CC_SETUP_UNLEDGERED=1 — running out of order on purpose (${blocked%% *} is ${blocked#* })"
      return 0
    fi
    # THE PRE-LEDGER INSTALL, driven by an OLDER release's updater. A
    # deployment installed before v2.55.0 has no ledger rows at all, and the
    # update that brings the ledger in is run by the updater it already has —
    # which merges first and then calls THIS script's phases. Under the
    # cockpit's runner (CC_UPDATE_DRIVEN=1) a FAIL here is exit 1, and every
    # runner before v2.57.0 answers exit 1 with a ROLLBACK to a tree that can
    # never write a ledger: such an install could not be updated at all. So,
    # for exactly that caller and exactly an EMPTY ledger, the refusal is the
    # operator's move (exit 3, which every runner treats as a pause): the
    # update is merged, and ./setup.sh adopts the running deployment. From
    # v2.57.0 on update.sh asks this itself before it calls a phase
    # (ledger_adoption_gate); this is the same answer for the releases that
    # cannot.
    if [[ "${CC_UPDATE_DRIVEN:-0}" == "1" && -z "$(cc_ledger_read "$LEDGER")" ]]; then
      useraction "ledger-adopt" "the update is merged and this deployment predates the install ledger: run ./setup.sh once — it adopts the running deployment phase by phase (every phase is idempotent) and records it"
      return 1
    fi
    fail "$1" "requires ${blocked%% *}, which is $(ledger_status_words "${blocked#* }") — run ./setup.sh (it resumes in order)"
    return 1
  fi
  return 0
}

# ─────────────────────────────────────────────────────────────────────────────
run_phase() { # run_phase <name>  -> 0 clean / 1 hard fail / 2 warnings / 3 user action
  FAILS=0; WARNS=0; ACTIONS=0; PASSES=0
  STEP_MSG=(); STEP_SAID=()
  CURPHASE="$1"
  MUTATING=0
  [[ "$MUTATING_PHASES" == *" $1 "* ]] && MUTATING=1
  note ""
  note "======== phase: $1"
  # A STAGED run (update.sh apply's acquisition, D5) is neither held to the
  # ledger nor recorded in it: it runs the NEW release's fetch from a worktree
  # that is not the install, deploys nothing, and its rows would carry the new
  # version before anything was merged. The deployment's ledger is left
  # exactly as it was; the post-merge `./setup.sh fetch` records the real rows.
  if (( STAGED )); then
    "phase_$1"
    return "$(cc_exit_code "$FAILS" "$WARNS" "$ACTIONS")"
  fi
  phase_ledger_gate "$1" || return "$(cc_exit_code "$FAILS" "$WARNS" "$ACTIONS")"
  # Past the gate and about to run: every row of the phase reads `started`
  # until ledger_record overwrites it with the outcome (D11).
  ledger_mark_started "$1"
  "phase_$1"
  # The phase's own verdict, BEFORE the probes add to it: "reported success" is
  # no FAIL and no USERACTION.
  local ok=0
  (( FAILS == 0 && ACTIONS == 0 )) && ok=1
  ledger_record "$1" "$ok"
  # ONE exit-code rule (D5): FAIL > USERACTION > WARN. A gate no longer
  # outranks a FAIL — that ranking is how phase_fetch became a phase that
  # could never return 1.
  return "$(cc_exit_code "$FAILS" "$WARNS" "$ACTIONS")"
}

# ── the STAGED ACQUISITION: `./setup.sh acquire` (2026-10-01 record, D5) ────
# INTERNAL to `update.sh apply`, which runs it from a sparse worktree of
# `upstream` — this file as the NEW release ships it — with CC_STAGED_FOR
# naming the deployment, BEFORE the merge. It answers the two questions whose
# "no" used to arrive after the merge, when the tree, the venv and the
# containers had already moved: can every artifact the new release needs be
# acquired from here (the `fetch` phase, run for real — images pulled, builds
# built, the Python graph resolved, the npm tree installed, so the image store
# and the package caches are WARM for the post-merge fetch), and does the
# running proxy's catalog answer every alias the new release requires?
#
# It is an interface BETWEEN RELEASES: the update.sh that calls it is the one
# already installed, the setup.sh that answers is the one being installed. So
# its contract is small and stays put — `acquire`, CC_STAGED_FOR, the output
# protocol, exit 0/1/2/3 — and update.sh reads nothing else from it.

# The catalog half. The VERDICT is p_catalog_aliases — the very probe the llm
# phase's `catalog-filled` gate asks, over the new release's
# cc_required_aliases — so this cannot disagree with the pause the merge would
# have run into. The loop after it only NAMES what is missing, and separates
# the aliases .env DECLARES (CC_LLM_UPSTREAM_BASE_URL + _API_KEY + the alias's
# own CC_LLM_UPSTREAM_MODEL_<A>): register-models.py creates those rows itself
# in the post-merge llm phase, so a release that ADDS an alias can still be
# applied by a declared-catalog install. Counting them missing would have made
# such an update impossible — the row only appears after the merge this probe
# would be refusing. A UI-catalog install adds the new alias's row in the
# LiteLLM UI, which works on the running proxy before the update.
acquire_catalog() {
  local listed a key missing="" declared=""
  load_env || return 1
  if p_catalog_aliases; then
    pass "catalog-probe" "the running proxy's catalog answers every alias this release requires ($(cc_required_aliases | tr -d '\n'))"
    return 0
  fi
  if ! listed="$(p_models_json)"; then
    fail "catalog-probe" "the running proxy did not answer GET /v1/models on 127.0.0.1:$(p_flag CC_LITELLM_PORT 4000) under CC_LLM_PROXY_ADMIN_KEY, so whether its catalog holds every alias this release requires cannot be proven — is the stack up? (./setup.sh status). Nothing has been merged"
    return 1
  fi
  for a in $(cc_required_aliases); do
    [[ "$listed" == *"\"$a\""* ]] && continue
    key="$(cc_alias_env_key "$a")"
    if [[ -n "${CC_LLM_UPSTREAM_BASE_URL:-}" && -n "${CC_LLM_UPSTREAM_API_KEY:-}" && -n "${!key:-}" ]]; then
      declared="${declared:+$declared }$a"
    else
      missing="${missing:+$missing }$a"
    fi
  done
  if [[ -n "$missing" ]]; then
    useraction "catalog-probe" "the running proxy's catalog has no row for ${missing}, which this release requires — add each in the LiteLLM UI at http://127.0.0.1:$(p_flag CC_LITELLM_PORT 4000)/ui (Models), or declare CC_LLM_UPSTREAM_BASE_URL, CC_LLM_UPSTREAM_API_KEY and $(for a in $missing; do printf '%s ' "$(cc_alias_env_key "$a")"; done)in .env so the update registers it, then re-run ./update.sh apply. Nothing has been merged"
    return 3
  fi
  pass "catalog-probe" "every alias this release requires answers in the running proxy's catalog, except ${declared}, which .env declares — the post-merge llm phase registers it"
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
usage: ./setup.sh                      # THE command: it resumes from the ledger
       ./setup.sh configure [--all] [--non-interactive]   # ask what is missing
       ./setup.sh check [--list]       # everything dry; --list names the sections
       ./setup.sh status               # the ledger + the postconditions. Writes nothing
       ./setup.sh report               # one file to hand a development session
       ./setup.sh stop                 # stop what boot started
       ./setup.sh machine --dry-run    # report the diff, write nothing
       ./setup.sh [all] --accept-warnings   # let a WARN-only check through
       ./setup.sh <phase>              # DEVELOPER form, refused out of order

  THE LOOP      configure -> check -> (triage: edit .env) -> check -> ... -> all
  THE LEDGER    deploy/single/steps.tsv declares every step of the install
                once, in run order; <state>/ledger.tsv records which ones are
                done, at which release, with which .env inputs. So there is one
                recovery from anything: change the .env key the FAIL line names
                and run ./setup.sh again — it skips what is still true and
                resumes at the first step that is not. A phase whose
                prerequisites are not done is REFUSED and names the first one;
                there is no --force, because the escape hatch is the defect.
                A row reads done, failed, pending, gate (waiting on you) or
                started — a run began that phase and never finished (it was
                interrupted); ./setup.sh runs it again
  THE PLAN      every run that runs phases first prints PLAN lines: which
                phases WILL RUN and which WILL SKIP, and why — a prediction
                made before the run, from the same rule the run skips by
  ONE AT A TIME every command that changes something takes <state>/run.lock;
                a second one while it is held is refused and names the pid,
                the command and when it started. A lock whose process is gone
                (a power cut, a killed terminal) is reclaimed by the next
                ./setup.sh with a WARN. status and report never wait on it
  no argument   runs check -> machine -> fetch -> llm -> stack -> app -> verify
                -> test -> boot -> demo: zero to a working, human-approved demo
                in one command, stopping at the first phase that hard-fails or
                needs you. ONE stop is EXPECTED rather than a fault: the llm
                phase exits 3 so you enter the provider details in the LiteLLM
                UI (the catalog lives in LiteLLM's database). Re-run to go on —
                or declare CC_LLM_UPSTREAM_* in .env to skip that pause
  configure     ASKS the questions in deploy/single/questions.tsv that this
                .env does not answer yet (creating .env from .env.example if it
                is absent — the one command that does), prints the diff it will
                write, writes ONLY .env, then generates the credentials.
                --all re-asks every question including the ports; with no
                terminal (or --non-interactive) it asks NOTHING rather than
                guessing, and exits 3 listing any REQUIRED key still blank
                (no question is required today — every one has a working
                default or a documented blank meaning)
  check         the pre-deployment gate: nine dry sections (answers, host,
                machine, images, indexes, llm, integrations, compose, models)
                in one table,
                ending in a CHECK: summary line. It changes nothing but
                CC_STATE_DIR / CC_EMBED_DIM in .env, so the loop is: edit
                .env -> check -> triage -> check -> ... -> ./setup.sh
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
  status        prints the ledger table and re-checks the postconditions.
                Mutates nothing
  report        writes <state>/report-<stamp>.txt — the ledger, every failed or
                waiting row with its reason and its inputs, the WHOLE log of
                the last run, .env key NAMES, versions, the image manifest and
                the process logs. Never a secret VALUE: each section is
                redacted as it is collected, from the list in redact.tsv, and
                the finished file is then scanned and refused if a value still
                got in. This is how a REPOSITORY
                DEFECT travels — an install configures through .env and never
                rewrites any part of Central Command, so anything else it needs
                is a finding. \`diagnose\` is an alias for it
  <phase>       the DEVELOPER form. It is refused (exit 1) when the phase's
                prerequisites are not done in the ledger, and names the first
                one. There is no --force
  exit codes    0 clean · 1 hard failure · 2 completed with warnings
                3 stopped for USER ACTION (see the last USERACTION line)
                precedence is FAIL > USERACTION > WARN, everywhere
  status log    every check is appended to <state>/setup-log.txt, outside the
                checkout (./setup.sh report prints the state dir first)
USAGE
}

# The WARN gate's answer, from the command line. Scanned before anything else so
# it may appear anywhere: `./setup.sh --accept-warnings`, `./setup.sh all
# --accept-warnings`, `./setup.sh check --accept-warnings`.
ACCEPT_WARNINGS=0
for arg in "$@"; do [[ "$arg" == "--accept-warnings" ]] && ACCEPT_WARNINGS=1; done

# The KOTS gate, rustup's rule (design record D5): a check that FAILED or
# stopped for the operator never continues into a mutating phase, and a
# WARN-only check continues only if somebody said so. No TTY and no flag = stop,
# naming the flag — an installer that cannot ask does not guess.
check_gate() { # check_gate <check-exit-code>  -> 0 continue, else the exit code
  local rc="$1" reply
  case "$rc" in
    0) return 0 ;;
    1) note ""
       note "check FAILED. Nothing has been changed. Fix the FAIL lines above (they name"
       note "the .env key or the mirror seam), then re-run:  ./setup.sh check"
       return 1 ;;
    3) note ""
       note "check stopped for YOUR action — see the USERACTION line(s) above. Nothing"
       note "has been changed. An answers-* line means a key nobody has answered:"
       note "    ./setup.sh configure      (it asks exactly those, and writes only .env)"
       note "When done, re-run:  ./setup.sh check"
       return 3 ;;
  esac
  # WARN only.
  if (( ACCEPT_WARNINGS )); then
    note ""
    note "check completed with warnings; --accept-warnings was given — continuing."
    return 0
  fi
  if [[ -t 0 ]]; then
    note ""
    printf 'check completed with WARNINGS (see above). Continue with the install? [y/N] ' >&2
    read -r reply
    case "$reply" in
      y|Y|yes|YES) return 0 ;;
    esac
    note "stopped at the check gate — nothing has been changed."
    return 2
  fi
  note ""
  note "check completed with WARNINGS and this is not an interactive terminal, so"
  note "setup will not decide for you. Re-run with:  ./setup.sh --accept-warnings"
  return 2
}

main() {
  local cmd="${1:-all}"
  [[ "$cmd" == --accept-warnings ]] && cmd=all
  # --list must work on a machine with no .env at all: it is documentation.
  if [[ "$cmd" == check && "${2:-}" == --list ]]; then check_list; exit 0; fi
  # Usage creates nothing either: a bare --help from a dev checkout wrote an
  # empty ledger into the state dir and armed the install-tree hook (2026-10-01).
  case "$cmd" in -h|--help|help) usage; exit 0 ;; esac
  init_state           # the log file (and the ledger) live in there
  # A STAGED tree is not an install: under CC_STAGED_FOR the one command is
  # `acquire`, and without it `acquire` is refused (it belongs to update.sh). A
  # staged `llm` or `stack` would deploy the NEW release's compose file from a
  # worktree that is about to be deleted, beside the old release's containers.
  if (( STAGED )) && [[ "$cmd" != acquire ]]; then
    CURPHASE=staged
    fail "staged" "CC_STAGED_FOR is set, which makes this a STAGED run of a release that is not installed — the only command it runs is \`acquire\` (update.sh apply's); refusing \`$cmd\`"
    exit 1
  fi
  if (( ! STAGED )) && [[ "$cmd" == acquire ]]; then
    CURPHASE=acquire
    fail "acquire" "\`acquire\` is internal to ./update.sh apply, which runs it from a staged copy of the release it is about to merge — on an install, ./setup.sh fetch is the acquisition"
    exit 1
  fi
  # THE MANIFEST, before anything can consult the ledger. A manifest this
  # cannot parse is release content that failed to ship, so it is a hard stop
  # rather than something to run around.
  if ! cc_steps_load "$STEPS"; then
    fail "steps" "$STEPS could not be read (the reason is on stderr) — it is release content, so re-extract the release"
    exit 1
  fi
  # How long the log was BEFORE this run appended to it, so `report` can print
  # the whole of the LAST run rather than the report's own.
  LOG_BEFORE=0
  [[ -f "$LOGFILE" ]] && LOG_BEFORE="$(wc -l <"$LOGFILE" 2>/dev/null | tr -d ' ')"
  logline "run start: ./setup.sh $cmd"
  # ONE RUN AT A TIME (D11, Kamal's lock directory): every command that writes
  # the ledger or changes the host takes <state>/run.lock first; the read-only
  # ones (status, report/diagnose, validate, preflight, check --list, machine
  # --dry-run, --help) never do, so they answer while a run is in progress —
  # which is when somebody is most likely to ask. A refusal happens HERE, before
  # the plan, before any `started` row, before any phase.
  local lockcmd=""
  case "$cmd" in
    all|configure|check|fetch|llm|stack|app|verify|test|boot|demo|stop|acquire)
      lockcmd="./setup.sh ${*:-all}" ;;
    machine)
      [[ "${2:-}" == "--dry-run" ]] || lockcmd="./setup.sh $*" ;;
  esac
  if [[ -n "$lockcmd" ]] && ! run_lock_take "$lockcmd"; then
    logline "run end: ./setup.sh $cmd -> exit 1 (run lock)"
    exit 1
  fi
  case "$cmd" in
    configure)
      # Not a phase: it is the command BEFORE the gate, it takes its own flags,
      # and its exit codes are its own (0 answered · 3 something required is
      # still missing · 1 a write failed) rather than run_phase's worst-of.
      FAILS=0; WARNS=0; ACTIONS=0; PASSES=0; CURPHASE=configure
      note ""; note "======== configure${2:+ ${*:2}}"
      shift || true
      cmd_configure "$@"; local qrc=$?
      logline "run end: ./setup.sh configure -> exit $qrc"
      exit $qrc
      ;;
    machine)
      # The one phase that takes a flag: --dry-run reports the diff and writes
      # nothing (which is what preflight calls it as).
      FAILS=0; WARNS=0; ACTIONS=0; CURPHASE=machine; MUTATING=1
      STEP_MSG=(); STEP_SAID=()
      # --dry-run writes nothing, so it is neither held to the ledger's order
      # nor recorded in it: it is a REPORT. (preflight reaches it through
      # phase_machine directly, not through this branch.)
      local mdry=0
      [[ "${2:-}" == "--dry-run" ]] && { mdry=1; MUTATING=0; }
      (( mdry )) || ledger_plan machine
      note ""; note "======== phase: machine${2:+ $2}"
      if (( ! mdry )) && ! phase_ledger_gate machine; then
        local grc0; grc0="$(cc_exit_code "$FAILS" "$WARNS" "$ACTIONS")"
        ledger_table
        logline "run end: ./setup.sh machine ${2:-} -> exit $grc0"
        exit "$grc0"
      fi
      (( mdry )) || ledger_mark_started machine
      phase_machine "${2:-}"
      local mok=0
      (( FAILS == 0 && ACTIONS == 0 )) && mok=1
      (( mdry )) || ledger_record machine "$mok"
      # The ONE exit-code rule (D5), here too: this branch used to carry its
      # own third copy of the precedence, and it ranked a gate above a FAIL.
      local mrc; mrc="$(cc_exit_code "$FAILS" "$WARNS" "$ACTIONS")"
      logline "run end: ./setup.sh machine ${2:-} -> exit $mrc"
      exit "$mrc"
      ;;
    report|diagnose)
      # `diagnose` is an alias (D10.3): one bundle, one shape, and no `tail -40`
      # window cutting off above wherever the run stopped.
      FAILS=0; WARNS=0; ACTIONS=0; PASSES=0
      cmd_report
      local rrc; rrc="$(cc_exit_code "$FAILS" "$WARNS" "$ACTIONS")"
      logline "run end: ./setup.sh $cmd -> exit $rrc"
      exit "$rrc"
      ;;
    check|validate|preflight|fetch|llm|stack|app|verify|test|boot|demo|status)
      # The plan, for the phases the manifest declares (validate, preflight and
      # status have no rows and change nothing — there is nothing to predict).
      case "$cmd" in validate|preflight|status) ;; *) ledger_plan "$cmd" ;; esac
      run_phase "$cmd"; local prc=$?
      # The ledger is the answer to "where did this stop?", so it is printed on
      # any stop — not only at the end of a full run. (`status` leads with it
      # already; printing it twice would just be noise.)
      (( prc )) && [[ "$cmd" != status ]] && ledger_table
      logline "run end: ./setup.sh $cmd -> exit $prc"
      exit $prc
      ;;
    acquire)
      # STAGED only (refused above otherwise), and NESTED under update.sh's run
      # lock. No plan and no ledger table: this tree has no ledger of its own,
      # and the deployment's is not what this run is about. Both halves always
      # run, so one pass names every seam the merge would have hit; the exit
      # code is the ONE rule over both (FAIL > USERACTION > WARN).
      note ""
      note "======== staged acquisition: release $(installed_version), for the deployment at $INSTALL_ROOT"
      run_phase fetch
      CURPHASE=catalog-probe
      note ""
      note "======== catalog probe: the running proxy, against this release's aliases"
      acquire_catalog
      local arc; arc="$(cc_exit_code "$FAILS" "$WARNS" "$ACTIONS")"
      logline "run end: ./setup.sh acquire (staged) -> exit $arc"
      exit "$arc"
      ;;
    stop)
      cmd_stop
      local src; src="$(cc_exit_code "$FAILS" "$WARNS" "$ACTIONS")"
      logline "run end: ./setup.sh stop -> exit $src"
      exit "$src"
      ;;
    all)
      if pending_update; then
        CURPHASE="dispatch"
        useraction "existing-install" "this tree is an existing deployment with an unapplied update — use ./update.sh plan (then apply), not a fresh setup run"
        logline "run end: ./setup.sh all -> exit 3"
        exit 3
      fi
      # The PLAN (D11): every phase in run order, WILL RUN or WILL SKIP and why
      # — from the same cc_phase_decide the loop below skips on.
      ledger_plan all
      # THE GATE comes first (design record D5): every input is proven before
      # anything is changed, so a mutating phase never discovers a
      # configuration problem the check could have named.
      run_phase check; local crc=$?
      # NOT `if ! check_gate`: the negation would make $? the INVERTED status
      # and the run would exit 0 on a refused gate.
      local grc=0
      check_gate "$crc" || grc=$?
      if (( grc )); then
        ledger_table
        logline "run end: ./setup.sh all -> exit $grc (check gate)"
        exit $grc
      fi
      local worst=0 rc p donelines s a
      (( crc == 2 )) && worst=2
      for p in machine fetch llm stack app verify test boot demo; do
        # D2's rule 2: a phase every one of whose rows is `done` AT THIS
        # RELEASE, with the same input fingerprint, and whose probes are all
        # still true, is SKIPPED — and says which rows it skipped and when.
        # Anything else runs: a release bump re-runs everything (the inner
        # idempotency — have_image, api_up, set_kv_if_unset — keeps that
        # cheap), and an .env edit re-runs exactly the rows it changed.
        if donelines="$(phase_is_done "$p")"; then
          FAILS=0; WARNS=0; ACTIONS=0; PASSES=0; CURPHASE="$p"
          note ""
          note "======== phase: $p — already done at $(installed_version), skipping"
          while IFS=$'\t' read -r s a; do
            [[ -n "$s" ]] && pass "$s" "done ($a)"
          done <<<"$donelines"
          continue
        fi
        run_phase "$p"; rc=$?
        if (( rc == 1 )); then
          note ""
          note "phase '$p' failed. Fix the FAIL line above — it names the .env key or the"
          note "seam — then re-run:  ./setup.sh   (it resumes at the first step that is"
          note "not done; the ledger below says which that is)"
          note "If the cause is not obvious: ./setup.sh report  (hand that file over)"
          ledger_table
          logline "run end: ./setup.sh all -> exit 1 (phase $p)"
          exit 1
        fi
        if (( rc == 3 )); then
          note ""
          note "phase '$p' stopped for YOUR action — see the USERACTION line above."
          note "When done, re-run:  ./setup.sh   (idempotent — it fast-forwards to here)"
          ledger_table
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
      ledger_table
      logline "run end: ./setup.sh all -> exit $worst"
      exit "$worst"
      ;;
    -h|--help|help) usage; exit 0 ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"
