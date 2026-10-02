# shellcheck shell=bash
# ============================================================================
# phases/boot.sh — the `boot` phase of the single-node install (deploy/single).
#
#   BOOT: the operator's name (once), then the API, the sandbox runner and
#   the cockpit server — through systemd --user units where a user manager
#   answers — the roster, and the bundled skills.
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
#   ROWS (steps.tsv, phase `boot`) — step, kind, probe; a probe marked
#   (setup.sh) is shared with another phase or the driver and lives there:
#     operator-name                      human  p_operator_name
#     boot-api                           run    p_boot_api
#     boot-sandbox                       run    p_boot_sandbox
#     boot-roster                        run    p_boot_roster
#     skills-imported                    run    p_skills_imported
#     boot-cockpit                       run    p_boot_cockpit
#     boot-at-logon                      run    p_boot_at_logon
# ============================================================================

[[ -n "${CC_PHASE_BOOT_LOADED:-}" ]] && return 0
CC_PHASE_BOOT_LOADED=1

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
      uv="$(venv_uvicorn)" || { PROC_WHY="uvicorn not in .venv — run: ./setup.sh (it resumes at app, which installs it)"; return 1; }
      PROC_CMD=("$uv" central_command.api.app:app --host 127.0.0.1 --port "$PROC_PORT")
      ;;
    sandbox)
      PROC_CHECK=boot-sandbox; PROC_WHAT="the sandbox runner"; PROC_TITLE="sandbox runner"
      PROC_PIDFILE="$STATE_DIR/sandbox.pid"; PROC_LOG="$STATE_DIR/sandbox.log"
      PROC_PORT="$(sandbox_port)"; PROC_URL="http://127.0.0.1:$PROC_PORT"; PROC_DIR="$REPO_ROOT"; PROC_WAIT=60
      # This profile's backend is rootless podman (README, "Starting the
      # sandbox runner"); kubectl is the k3s profile's.
      PROC_ENV+=("CC_SANDBOX_BACKEND=podman")
      uv="$(venv_uvicorn)" || { PROC_WHY="uvicorn not in .venv — run: ./setup.sh (it resumes at app, which installs it)"; return 1; }
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
