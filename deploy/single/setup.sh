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

# `dirname`'s answer by parameter expansion: no process for it (P5 — on Git
# Bash a fork is the cost of every line before the first output).
_cc_src="${BASH_SOURCE[0]}"
case "$_cc_src" in */*) _cc_src="${_cc_src%/*}"; _cc_src="${_cc_src:-/}" ;; *) _cc_src="." ;; esac
HERE="$(cd "$_cc_src" && pwd)"
unset _cc_src
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

# THE PHASES, one file each (2026-10-01 design record, D11 — Sentry's
# installer, "a thin orchestrator sourcing step files in order"). Every phase
# steps.tsv declares has exactly one file, deploy/single/phases/<phase>.sh,
# holding phase_<phase>, the helpers only that phase uses and the probes of its
# rows (check's file also holds `validate` and `preflight`, which it composes).
# They are SOURCED here, in the manifest's phase order, before anything runs;
# they define functions and constants and nothing else.
# tests/test_single_phase_files.py pins the set, the order and that no function
# is defined twice. What stays in THIS file:
#   the header and usage, the output protocol (pass/warn/fail/useraction, step),
#   init_state and load_env, the .env helpers and the question schema reader,
#   every helper more than one phase (or a command below) uses — the podman
#   machine's shell, compose, the image catch-up, the venv, the self-check, the
#   API and process helpers `stop` shares — `configure`, `status`, `stop`,
#   `report`, the probes more than one phase reads, the ledger, the plan, the
#   run lock, `acquire` and main.
# Every path is quoted: a Windows checkout lives under a folder with a space.
for _cc_phase in check machine fetch llm stack app verify test boot demo; do
  # shellcheck source=/dev/null
  . "$HERE/phases/$_cc_phase.sh" \
    || { printf 'FAIL phases: %s\n' "$HERE/phases/$_cc_phase.sh could not be sourced — it is release content, so re-extract the release"; exit 1; }
done
unset _cc_phase

# Python on Windows encodes a PIPED stdout in the ANSI code page, so the em
# dashes in register-models.py's operator banner reached the log as cp1252
# bytes inside otherwise-UTF-8 output (2026-09-03 Windows run: `�`).
export PYTHONUTF8=1

# The interpreter: a real python3/python probed by RUNNING it (the Windows
# stub exits 49), else `uv run --no-project --python 3.12 python` — the
# reasons for both live with the ONE definition, deploy/env-lib.sh's
# cc_resolve_py, which update.sh's importer uses too.
PY="$(cc_resolve_py)"
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
#
# A PASS or a WARN is recorded too (P5, the first laptop acceptance run): it
# says the step RAN, which is the one thing a probe cannot tell the ledger
# about a row the phase never reached. Never over a FAIL or a USERACTION — the
# word that stopped the step is the one that stands — and with no STEP_MSG,
# because `reason` is the stop's message and a step that passed has none.
declare -A STEP_MSG=()
declare -A STEP_SAID=()
# The stamp is cc_now_utc's (env-lib.sh): `date -u +%FT%TZ`'s text, with no
# fork — every PASS/WARN/FAIL line is logged, and on Git Bash a `date` per line
# was a visible share of a run's fixed cost (P5).
logline() { cc_now_utc; printf '%s %s %s\n' "$NOW_UTC" "${CURPHASE:-run}" "$*" >>"$LOGFILE" 2>/dev/null || true; }
pass() { printf 'PASS %s: %s\n' "$1" "$2"; PASSES=$((PASSES+1)); [[ -n "${STEP_SAID[$1]:-}" ]] || STEP_SAID["$1"]=PASS; logline "PASS $1: $2"; }
warn() { printf 'WARN %s: %s\n' "$1" "$2"; WARNS=$((WARNS+1)); [[ -n "${STEP_SAID[$1]:-}" ]] || STEP_SAID["$1"]=WARN; logline "WARN $1: $2"; }
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
  if cc__state_dir_into "$ENV_FILE" "$INSTALL_ROOT"; then
    STATE_DIR="$STATE_DIR_OUT"
  else
    STATE_DIR="${TMPDIR:-/tmp}/central-command-state"
  fi
  [[ -d "$STATE_DIR" ]] || mkdir -p "$STATE_DIR" 2>/dev/null || true
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
#
# A PASS is remembered for the rest of the run (P5): a full run asks this at
# the top of every mutating phase and again as check's row's probe, and each
# ask is a `git status` over the whole checkout — seconds on Git Bash. Nothing
# the run does writes inside the checkout (D7, tests/test_single_no_tree_writes.py),
# so the answer cannot change under it; a FAIL is never remembered, so the
# refusal and its TREE_DIFF_PATHS are always taken fresh.
TREE_PRISTINE_HELD=0
p_tree_pristine() {
  (( TREE_PRISTINE_HELD )) && return 0
  local rc=0
  cc_tree_diff "$REPO_ROOT" || rc=$?
  (( rc == 1 )) && return 1
  TREE_PRISTINE_HELD=1
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
  # The checklist's next move is the one command: ./setup.sh runs the dry check
  # first and stops there on anything it finds, so naming `check` here sent the
  # operator (and an agent) to a second command for no gain (P5, F1).
  note "next: ./setup.sh   (it runs the dry check first, then installs)"
  return $rc
}
# The credentials make-secrets.sh generates. check never generates one (that is
# a side effect, and the `llm` phase owns it) — a blank one is a WARN naming
# the command that fills it — and since v2.45.0 `./setup.sh configure` runs that
# command itself, so the WARN is what a run that SKIPPED configure looks like.
CC_GENERATED_KEYS=(CC_LLM_PROXY_ADMIN_KEY CC_LITELLM_SALT_KEY LITELLM_POSTGRES_PASSWORD
                   CC_NEO4J_PASSWORD N8N_ENCRYPTION_KEY N8N_DB_PASSWORD
                   CC_EMAIL_FACADE_TOKEN CC_CALENDAR_FACADE_TOKEN
                   CC_SANDBOX_RUNNER_TOKEN)

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
        fail "$check" "$svc's image ${DRIFT_REF[$svc]} is not in local storage, so its container cannot be brought onto it — run: ./setup.sh (it resumes at fetch, which acquires it)" ;;
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

# The API's server, from the same venv. Here rather than in phases/boot.sh
# (v2.58.0) because two phases read it: boot starts the API with it, and
# app/install's probe requires it — an install whose venv lacks uvicorn is not
# installed, or `./setup.sh` skips app as done and boot fails on it forever.
venv_uvicorn() {
  [[ -x "$REPO_ROOT/.venv/bin/uvicorn" ]] && { printf '%s' "$REPO_ROOT/.venv/bin/uvicorn"; return 0; }
  [[ -x "$REPO_ROOT/.venv/Scripts/uvicorn.exe" ]] && { printf '%s' "$REPO_ROOT/.venv/Scripts/uvicorn.exe"; return 0; }
  return 1
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
SUP_ID=""

# A unit's name for <kind> (api | cockpit | sandbox), from the install id —
# the identity the state dir is named by — so two installs on one host never
# collide (supervise-lib.sh's cc_sup_unit_name).
sup_unit() { # sup_unit <kind>
  sup_id_load
  cc_sup_unit_name "$SUP_ID" "$1"
}
# SUP_ID in THIS shell. sup_unit is mostly called inside a `$(…)`, where the
# value it caches dies with the subshell — so a command that names several
# units (stop names three) loads it once first, and the subshells inherit it.
sup_id_load() {
  [[ -n "$SUP_ID" ]] || SUP_ID="$(cc_install_id "$(cc_norm_path "$REPO_ROOT")")"
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
    # The listener netstat names is killed ONLY when this install has a record
    # of having started something on this port (a unit or a pid file, i.e.
    # HALT_HOW is set). With no record, whatever holds the port is somebody
    # else's — another install on the default ports, an unrelated program —
    # and is reported, not shot: a temp-tree `stop` on a box with a live API
    # on 8080 would otherwise have killed it (2026-10-02 review of the
    # Windows run). Same rule as "nothing is ever killed by PORT on Linux".
    if [[ -n "$HALT_HOW" ]]; then
      pid="$(netstat -ano 2>/dev/null | grep LISTENING | grep ":${port} " | awk '{print $NF}' | head -1)"
      if [[ -n "$pid" ]]; then
        taskkill //T //F //PID "$pid" >/dev/null 2>&1
        HALT_HOW="${HALT_HOW:+$HALT_HOW, then }taskkill /T of the listener, Windows pid $pid"
        sleep 1
      fi
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
  sup_id_load
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

  if venv_python >/dev/null; then pass "venv" ".venv present"; else fail "venv" ".venv missing — run: ./setup.sh (it resumes at fetch, which creates it)"; fi
  local k
  for k in CC_LLM_BASE_URL CC_DEFAULT_MODEL CC_LLM_API_KEY CC_EMBED_DIM CC_NEO4J_PASSWORD CC_LITELLM_SALT_KEY; do
    if is_placeholder "$(get_kv "$ENV_FILE" "$k")"; then
      fail "app-${k}" "$k is unset in the app's .env — run: ./setup.sh (it resumes at the phase that writes it)"
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
# Where that run BEGINS: its `run start:` line's number in the log as it stood
# before this command (1 when no line says so). 1 = no earlier run. Shared by
# the section and by the report's header, which states the bound (D8) — so
# the two cannot name different runs.
last_run_first_line() {
  local n total="${LOG_BEFORE:-0}"
  [[ -f "$LOGFILE" ]] || return 1
  [[ "$total" =~ ^[0-9]+$ ]] || total=0
  (( total > 0 )) || return 1
  n="$(head -n "$total" "$LOGFILE" | grep -n 'run start:' | tail -1 | cut -d: -f1)"
  [[ "$n" =~ ^[0-9]+$ ]] || n=1
  printf '%s' "$n"
}

last_run_log() {
  local n
  [[ -f "$LOGFILE" ]] || { echo "  (no log yet)"; return 0; }
  n="$(last_run_first_line)" || { echo "  (no earlier run in the log)"; return 0; }
  head -n "$LOG_BEFORE" "$LOGFILE" | tail -n +"$n"
  return 0
}

# The bound, in words, for the report's first lines: which line of which log
# the section starts at, and that line itself — the run's `run start:` stamp
# and command.
last_run_bound() {
  local n
  if n="$(last_run_first_line)"; then
    printf 'it starts at line %s of %s, "%s"' "$n" "$LOGFILE" "$(sed -n "${n}p" "$LOGFILE")"
  else
    printf 'there is no earlier run in %s' "$LOGFILE"
  fi
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
    echo "The log section below is the WHOLE last run, not a window, and it is CAPPED AT THE LAST RUN:"
    echo "nothing from any run before it — $(last_run_bound | report_redact)."
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
#
# WHERE THEY LIVE (v2.58.0, D11): a probe one phase's rows read is defined in
# that phase's file, deploy/single/phases/<phase>.sh. Defined HERE are the
# helpers every probe uses and the probes more than one phase reads (or the
# driver itself does: p_tree_pristine above, p_catalog_filled for `acquire`).

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

p_venv() {
  venv_python >/dev/null
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
# The three LOCAL images: done means the tag is present AND its build-inputs
# label equals this tree's (v2.57.0, local_image_state). That comparison is
# what re-runs these rows when a release changes a Dockerfile or its context —
# their `reads` name only .env keys, and a file is not one.

p_image_sandbox() {
  p_off CC_ENABLE_SANDBOX 1 1 && return 0
  [[ "$(local_image_state "localhost/cc-sandbox:1" "$HERE/build-sandbox-image.sh")" == current ]]
}

p_image_crawler() {
  p_off CC_ENABLE_CRAWLER 1 1 && return 0
  [[ "$(local_image_state "localhost/cc-crawler:1" "$HERE/build-crawler-image.sh")" == current ]]
}

p_litellm_live() {
  curl -fsS -m 5 -o /dev/null \
    "http://127.0.0.1:$(p_flag CC_LITELLM_PORT 4000)/health/liveliness" 2>/dev/null
}

# The proxy's catalog, read under the ADMIN key. One GET, no tokens.
p_models_json() {
  local key
  key="$(get_kv "$ENV_FILE" CC_LLM_PROXY_ADMIN_KEY)"
  [[ -n "$key" ]] || return 1
  printf 'Authorization: Bearer %s\n' "$key" \
    | curl -fsS -m 15 -H @- "http://127.0.0.1:$(p_flag CC_LITELLM_PORT 4000)/v1/models" 2>/dev/null
}

# cc_required_aliases (the ONE list) with CC_ENABLE_SPEECH read the way every
# probe reads a flag (p_flag): the process's value, else .env's. A probe is
# asked by the PLAN before load_env has exported .env, and cc_required_aliases
# reads only the environment, whose default is speech ON: a deployment with
# CC_ENABLE_SPEECH=0 and the cc-tts/cc-stt skeletons left unfilled (the normal
# case) was judged on six aliases there and on four by the phase — `llm` ran
# again on every ./setup.sh as "catalog-filled ... reads false now (drift)",
# and the probe memo carried the plan's false into the loop (the 2026-10-02
# testbed run's second pass, F18). Once exported, no .env read and no fork.
catalog_required_aliases() {
  local speech="${CC_ENABLE_SPEECH:-}"
  [[ -n "$speech" ]] || speech="$(p_flag CC_ENABLE_SPEECH 1)"
  CC_ENABLE_SPEECH="$speech" cc_required_aliases
}

# llm/catalog's probe: every alias THIS deployment requires answers /v1/models
# through the admin key — registered, as a real row or as a skeleton (a
# skeleton IS the catalog step's effect; whether it is filled in is the next
# row's question, p_catalog_filled). cc_required_aliases is the ONE list.
p_catalog_aliases() {
  local listed a
  listed="$(p_models_json)" || return 1
  for a in $(catalog_required_aliases); do
    [[ "$listed" == *"\"$a\""* ]] || return 1
  done
  return 0
}

# The required aliases the catalog does NOT hold FILLED IN, one per line —
# absent, or still carrying register-models.py's PLACEHOLDER token in a
# litellm_param it owns (model, api_base, timeout, mode: its OWNED tuple, and
# the convention .claude/rules/deploy-single.md and models.json describe). Read
# from GET /model/info under the ADMIN key — the call register-models.py itself
# judges rows by; /v1/models lists a skeleton exactly like a real row, which is
# why `catalog` (skeletons registered) and `catalog-filled` (the operator has
# filled them) need two different questions. Every row of an alias counts: one
# skeleton beside a real deployment still routes requests to PLACEHOLDER.
# -> 0 and the list (empty = every required alias is filled) · 1 the proxy
# could not be asked, or did not answer JSON.
#
# The list is CR-free on every host: Windows Python writes "\r\n" to a pipe,
# and Git Bash's $(...) strips only the LAST line's — so a two-alias answer
# read "cc-tts\r" and every consumer that derives a key from a name, or
# compares one, got it wrong. Stripped here, in-process.
catalog_unfilled() {
  local key info out
  key="$(get_kv "$ENV_FILE" CC_LLM_PROXY_ADMIN_KEY)"
  [[ -n "$key" ]] || return 1
  info="$(printf 'Authorization: Bearer %s\n' "$key" \
    | curl -fsS -m 15 -H @- "http://127.0.0.1:$(p_flag CC_LITELLM_PORT 4000)/model/info" 2>/dev/null)" || return 1
  # JSON on STDIN, alias NAMES in the argv — never the key.
  out="$($PY -c '
import json, sys
try:
    rows = json.load(sys.stdin).get("data") or []
except Exception:
    sys.exit(1)
OWNED = ("model", "api_base", "timeout", "mode")
for alias in sys.argv[1:]:
    mine = [r for r in rows if r.get("model_name") == alias]
    if not mine or any(isinstance((r.get("litellm_params") or {}).get(k), str)
                       and "PLACEHOLDER" in r["litellm_params"][k]
                       for r in mine for k in OWNED):
        print(alias)
' $(catalog_required_aliases) <<<"$info" 2>/dev/null)" || return 1
  [[ -z "$out" ]] || printf '%s\n' "${out//$'\r'/}"
}

# The gate's probe (llm/catalog-filled): every alias THIS deployment requires
# is in the catalog AND filled in. False while any of them is still a skeleton
# — which is the pause, so the plan and `status` say "waiting on you" there
# instead of `done` (the first laptop run, P5, read `catalog-filled done` at
# the very stop, because the probe only asked that each alias be LISTED).
p_catalog_filled() {
  local unfilled
  unfilled="$(catalog_unfilled)" || return 1
  [[ -z "$unfilled" ]]
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

# ═════════════════════════════════════════════════════════════════════════════
# THE LEDGER — what completed, at which version, with which inputs (D2)
# ═════════════════════════════════════════════════════════════════════════════
# The updaters read VERSION as the installed version — not git, not the tag.
#
# Read ONCE per run (a release's VERSION does not change under the run that is
# installing it) and without a pipeline: it was `sed | head | tr` per call, and
# the plan, the `started` mark, every record and every skip ask for it. The
# answer is the same: the first line starting `version=`, minus that prefix,
# with every space and CR deleted; `unknown` when there is none.
# installed_version_load sets INSTALLED_VERSION in this shell (the ledger's
# callers use it, so even the `$(…)` is gone); installed_version prints it.
INSTALLED_VERSION=""
installed_version_load() {
  [[ -n "$INSTALLED_VERSION" ]] && return 0
  local line v=""
  if [[ -f "$REPO_ROOT/VERSION" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$line" == version=* ]] || continue
      v="${line#version=}"; v="${v//[ $'\r']/}"
      break
    done <"$REPO_ROOT/VERSION"
  fi
  INSTALLED_VERSION="${v:-unknown}"
}
installed_version() {
  installed_version_load
  printf '%s' "$INSTALLED_VERSION"
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
  # cc_ledger_read's lines (CR dropped, blanks and comments skipped), read here
  # rather than through its process substitution.
  if [[ -f "$LEDGER" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%$'\r'}"
      [[ -z "$line" || "$line" == '#'* ]] && continue
      IFS=$'\t' read -r step st ver at fp reason <<<"$line"
      printf '%-34s %-7s %-9s %-21s %s\n' "$step" "$st" "$ver" "$at" "${reason:--}"
    done <"$LEDGER"
  fi
  return 0
}

# D11's `started` (dpkg's half-configured): every row of the phase, in ONE
# atomic rewrite, immediately before the phase function runs. Called only
# once the phase has passed its ledger gate — a REFUSED phase did not start,
# and a phase skipped as done is not run at all.
ledger_mark_started() { # ledger_mark_started <phase>
  cc_now_utc; installed_version_load
  cc_ledger_mark_started "$LEDGER" "$1" "$INSTALLED_VERSION" "$NOW_UTC" "$ENV_FILE" \
    || warn "ledger" "could not mark $1's rows started in $LEDGER — if this run is interrupted the ledger will not say where"
  return 0
}

# After the phase function returns: record EVERY row of the phase, in manifest
# order, from what the step SAID this run and — only where it said nothing and
# nothing before it stopped — from the reality its probe reads:
#
#   printed FAIL        -> failed, reason = that message. The probe proves an
#                          effect EXISTS; it cannot unsay a step that reported
#                          it did not do its job (v2.56.0).
#   printed USERACTION  -> gate, reason = that message: the operator's move.
#   printed PASS/WARN   -> done if its probe holds; if not, a FAIL naming the
#                          row — it said it did its job and the effect is not
#                          there.
#   printed NOTHING     -> if an EARLIER row of this phase is failed or gate
#                          this run (and the phase did not report success), the
#                          phase never reached it: `pending`, with no reason,
#                          and its probe is never asked. A probe reads an
#                          effect, and an effect left by an earlier run is not
#                          this run's — the first laptop acceptance run (P5)
#                          recorded the rows after a FAILed app/mint-key as
#                          `done` where the configure-born .env already held
#                          their key, and `failed` with an EMPTY reason where it
#                          did not, and D9's own sentence is "every later app
#                          row pending".
#                          Otherwise (nothing earlier stopped; the row simply
#                          prints no line of its own) the probe decides, as it
#                          always has: done, or failed — or `gate` for a
#                          `gate`/`human` row, which is waiting, not broken.
#
# `<phase-reported-success>` is "no FAIL and no USERACTION" — and a row probing
# false after that is the defect this whole mechanism exists for: the step after
# the one that failed never ran, and nothing said so. It becomes a line.
#
# All of the phase's rows are written in ONE rewrite, overwriting the
# `started` mark ledger_mark_started left on each.
ledger_record() { # ledger_record <phase> <phase-reported-success:0|1>
  local phase="$1" ok="$2" step kind probe qual fp st now ver reason said stopped=0
  local rows=()
  cc_now_utc; now="$NOW_UTC"
  installed_version_load; ver="$INSTALLED_VERSION"
  # Every row's fingerprint in one hasher process (ledger-lib.sh).
  cc_fingerprint_prime_rows "$ENV_FILE" "$phase"
  # Every probe below runs with stdin from /dev/null: this loop used to READ
  # the phase's step names from its stdin (a probe that read stdin swallowed
  # the rest, and those rows kept their `started` mark), and a probe has no
  # business with the caller's stdin either way.
  cc__phase_steps "$phase"
  for step in ${PHASE_STEPS[@]+"${PHASE_STEPS[@]}"}; do
    [[ -n "$step" ]] || continue
    cc__step_split "$phase" "$step" || continue
    kind="$ROWDEF_KIND"; probe="$ROWDEF_PROBE"
    qual="$phase/$step"
    cc__fingerprint_into "$ENV_FILE" "$ROWDEF_READS"; fp="$FP_OUT"
    reason="${STEP_MSG[$step]:-}"
    said="${STEP_SAID[$step]:-}"
    case "$said" in
      FAIL)
        st=failed ;;
      USERACTION)
        st=gate ;;
      PASS|WARN)
        if "$probe" </dev/null >/dev/null 2>&1; then
          st=done; reason=""
        else
          if (( ok )); then
            fail "$step" "phase reported success but $qual's effect is absent ($probe returned non-zero) — this is the step nobody told you about"
          else
            fail "$step" "$qual printed $said but its effect is absent ($probe returned non-zero) — the step said it did its job and the system does not show it"
          fi
          st=failed
          reason="${STEP_MSG[$step]:-}"
        fi ;;
      *)
        if (( stopped )); then
          # Never reached: an earlier row of this phase stopped it this run.
          st=pending; reason=""
        elif "$probe" </dev/null >/dev/null 2>&1; then
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
        fi ;;
    esac
    # Only a STOP makes the rows after it unreached. On a phase that reported
    # success nothing stopped it; a row failed here by its probe is a line of
    # its own, and the rows after it did run.
    if (( ! ok )) && [[ "$st" == failed || "$st" == gate ]]; then stopped=1; fi
    rows+=("$qual" "$st" "$ver" "$now" "$fp" "$reason")
  done
  (( ${#rows[@]} )) || return 0
  cc_ledger_write_batch "$LEDGER" "${rows[@]}" \
    || warn "ledger" "could not write $LEDGER — this phase will simply run again"
  return 0
}

# Is this phase already DONE — every row `done`, at THIS version, with the same
# input fingerprint, and every probe still true (D2's rule 2)? Leaves one
# "<step>\t<at>" line per row in PHASE_DONE_LINES when it is, so the caller can
# report what it skipped rather than skipping silently. Called in THIS shell,
# not a `$(…)`: the fingerprints and probe verdicts it takes are cached for the
# rest of the run (ledger-lib.sh), and a subshell would throw them away.
#
# The judgement is cc_phase_decide's (ledger-lib.sh) — the SAME function the
# plan prints from, so the plan and this skip cannot disagree about a ledger
# (only about a world a phase that ran in between has changed).
phase_is_done() { # phase_is_done <phase>
  installed_version_load
  cc_phase_decide "$LEDGER" "$1" "$INSTALLED_VERSION" "$ENV_FILE"
  [[ "$PHASE_VERDICT" == skip ]]
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
  installed_version_load; ver="$INSTALLED_VERSION"
  plan_line "./setup.sh ${1} at ${ver} — a PREDICTION made before anything runs: a phase that runs can change what a later phase finds (fetch rewrites image refs, llm measures CC_EMBED_DIM), so each phase is judged again when the run reaches it"
  if [[ "$1" == all ]]; then
    # Every row's fingerprint in ONE hasher process, before the phases are
    # judged (each would otherwise start one of its own).
    cc_ledger_load "$LEDGER"
    if [[ "${LEDGER_ROWS[*]-}" == *$'\t'done$'\t'"$ver"$'\t'* ]]; then
      cc_fingerprint_prime_rows "$ENV_FILE"
    fi
    for p in $STEPS_PHASE_LIST; do
      if [[ "$p" == check ]]; then
        plan_line "check: WILL RUN — always: the dry gate proves every input before anything changes"
        continue
      fi
      cc_phase_decide "$LEDGER" "$p" "$ver" "$ENV_FILE"
      cc__phase_plan_text_into "$p" "$ver"
      plan_line "$PLAN_TEXT"
    done
    return 0
  fi
  # A phase named on the command line (the DEVELOPER form) is never skipped as
  # done — it is the ledger GATE that decides whether it runs at all.
  p="$1"
  blocked=""
  cc__ledger_blocked_into "$LEDGER" "$p" && blocked="$LEDGER_BLOCKED"
  if [[ -n "$blocked" ]] && ! unledgered_permitted; then
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
      cc__phase_plan_text_into "$p" "$ver"
      text="$PLAN_TEXT"
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
  if cc__ledger_blocked_into "$LEDGER" "$1"; then
    blocked="$LEDGER_BLOCKED"
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
    cc_ledger_cache_drop
    return "$(cc_exit_code "$FAILS" "$WARNS" "$ACTIONS")"
  fi
  phase_ledger_gate "$1" || return "$(cc_exit_code "$FAILS" "$WARNS" "$ACTIONS")"
  # Past the gate and about to run: every row of the phase reads `started`
  # until ledger_record overwrites it with the outcome (D11).
  ledger_mark_started "$1"
  "phase_$1"
  # A phase that ran may have changed what a probe reads, so the run's cached
  # probe verdicts, fingerprints and tree hashes go (ledger-lib.sh) — except
  # after `check`, the dry gate: it changes nothing a probe reads but .env, and
  # every cached verdict is already keyed on .env's content.
  [[ "$1" == check ]] || cc_ledger_cache_drop
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

# The catalog half. The VERDICT is p_catalog_filled — the very probe the llm
# phase's `catalog-filled` gate asks, over the new release's
# cc_required_aliases — so this cannot disagree with the pause the merge would
# have run into: an alias that is absent, OR still a PLACEHOLDER skeleton, is
# what that gate stops on. The loop after it only NAMES what is not filled, and
# separates the aliases .env DECLARES (CC_LLM_UPSTREAM_BASE_URL + _API_KEY +
# the alias's own CC_LLM_UPSTREAM_MODEL_<A>): register-models.py creates those
# rows itself in the post-merge llm phase — and UPDATES a skeleton it finds —
# so a release that ADDS an alias can still be applied by a declared-catalog
# install. Counting them missing would have made such an update impossible —
# the row only appears after the merge this probe would be refusing. A
# UI-catalog install adds (or fills) the alias's row in the LiteLLM UI, which
# works on the running proxy before the update.
acquire_catalog() {
  local unfilled a key missing="" declared=""
  load_env || return 1
  if ! unfilled="$(catalog_unfilled)"; then
    fail "catalog-probe" "the running proxy did not answer GET /model/info on 127.0.0.1:$(p_flag CC_LITELLM_PORT 4000) under CC_LLM_PROXY_ADMIN_KEY, so whether its catalog holds every alias this release requires, filled in, cannot be proven — is the stack up? (./setup.sh status). Nothing has been merged"
    return 1
  fi
  if [[ -z "$unfilled" ]]; then
    pass "catalog-probe" "the running proxy's catalog answers every alias this release requires, filled in ($(cc_required_aliases | tr -d '\n'))"
    return 0
  fi
  for a in $unfilled; do
    key="$(cc_alias_env_key "$a")"
    if [[ -n "${CC_LLM_UPSTREAM_BASE_URL:-}" && -n "${CC_LLM_UPSTREAM_API_KEY:-}" && -n "${!key:-}" ]]; then
      declared="${declared:+$declared }$a"
    else
      missing="${missing:+$missing }$a"
    fi
  done
  if [[ -n "$missing" ]]; then
    useraction "catalog-probe" "the running proxy's catalog has no filled-in row for ${missing}, which this release requires (absent, or still a PLACEHOLDER skeleton) — add or fill each in the LiteLLM UI at http://127.0.0.1:$(p_flag CC_LITELLM_PORT 4000)/ui (Models), or declare CC_LLM_UPSTREAM_BASE_URL, CC_LLM_UPSTREAM_API_KEY and $(for a in $missing; do printf '%s ' "$(cc_alias_env_key "$a")"; done)in .env so the update registers it, then re-run ./update.sh apply. Nothing has been merged"
    return 3
  fi
  pass "catalog-probe" "every alias this release requires is filled in on the running proxy, except ${declared}, which .env declares — the post-merge llm phase registers it"
  return 0
}

# An initialized update.sh repo with an import `local` does not contain yet:
# an update is WAITING. That leaves the tree exactly the installed release —
# update.sh's apply merges only after the new release has been acquired — so
# running it is safe, and `all` proceeds on it and says so in one line. It
# used to REFUSE ("use ./update.sh plan"), and after an update whose
# acquisition stopped (the checklist's flow had already stopped the API) that
# kept the deployment DOWN until an acquirable release arrived (the
# 2026-10-02 testbed run's second pass, F24).
pending_update() {
  command -v git >/dev/null 2>&1 || return 1
  git -C "$REPO_ROOT" rev-parse --verify -q upstream >/dev/null 2>&1 || return 1
  ! git -C "$REPO_ROOT" merge-base --is-ancestor upstream local 2>/dev/null
}

# What IS unsafe to run: a merge left half-way (conflicts, or resolved and not
# committed) — the tree is neither release. update.sh's apply refuses the same
# state (`merge-in-progress`); an EDITED tree is check/tree-pristine's (D10).
merge_in_progress() {
  command -v git >/dev/null 2>&1 || return 1
  git -C "$REPO_ROOT" rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1
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
    fail "acquire" "\`acquire\` is internal to ./update.sh apply, which runs it from a staged copy of the release it is about to merge — on an install, the fetch phase of ./setup.sh is the acquisition"
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
  if [[ -f "$LOGFILE" ]]; then
    LOG_BEFORE="$(wc -l <"$LOGFILE" 2>/dev/null)"; LOG_BEFORE="${LOG_BEFORE// /}"
  fi
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
      cc_ledger_cache_drop
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
      if merge_in_progress; then
        CURPHASE="dispatch"
        fail "merge-in-progress" "this tree is in the middle of a git merge, so it is neither the installed release nor the new one — resolve it (fix conflicts, git add, git commit) and run ./update.sh apply, or back out with: git merge --abort"
        logline "run end: ./setup.sh all -> exit 1 (merge in progress)"
        exit 1
      fi
      if pending_update; then
        # A note, not a protocol line: it moves no counter, so it never turns
        # the run into a stop.
        note "an imported update is waiting (upstream $(git -C "$REPO_ROOT" show upstream:VERSION 2>/dev/null | sed -n 's/^version=//p' | head -1)): this run keeps the INSTALLED release running; ./update.sh apply installs the update when its acquisition can succeed"
        logline "note: an imported update is waiting on upstream — running the installed release"
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
        if phase_is_done "$p"; then
          donelines="$PHASE_DONE_LINES"
          FAILS=0; WARNS=0; ACTIONS=0; PASSES=0; CURPHASE="$p"
          note ""
          note "======== phase: $p — already done at $INSTALLED_VERSION, skipping"
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
