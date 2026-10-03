#!/usr/bin/env bash
# ============================================================================
# supervise-lib.sh — what `boot` writes to keep its host processes running,
#                    rendered as TEXT by pure functions.
#
#   Design record: docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md
#   D6, "everything the install starts, it supervises". `boot` starts exactly
#   three HOST processes — the API, the cockpit server and the sandbox runner —
#   and until v2.57.0 nothing brought any of them back after a crash or a
#   reboot on Linux, and on Windows the logon entry re-ran `./setup.sh boot`,
#   which on an install that never completed fails in silence.
#
#   WHY A SEPARATE FILE OF PURE FUNCTIONS — machine-lib.sh's precedent. What
#   these render is executed by something the developer's box must never be
#   asked to run: a systemd user manager (this repository is developed on a
#   host whose systemd runs a LIVE deployment), and cmd.exe at a Windows logon.
#   The TEXT is what decides whether either works, so the text is what is
#   tested (tests/test_single_boot_supervision.py), and `systemd-analyze
#   --user verify` — read-only — checks the rendered units where it exists.
#   Nothing in this file runs systemctl, loginctl, schtasks or a process; it
#   prints, and the caller writes.
#
#   Functions:
#     cc_sup_unit_name <install-id> <kind>   the unit's file name, unique per install
#     cc_sup_systemd_word <arg>              one ExecStart= word, escaped
#     cc_sup_systemd_path <path>             a path setting's value, escaped
#     cc_sup_env_line <KEY=VALUE>            one Environment= line
#     cc_sup_passthrough_names               the derived variables a unit must carry
#     cc_render_unit ...                     a whole systemd --user unit
#     cc_render_logon_cmd <bash.exe> <script> the Windows logon wrapper (CRLF)
#     cc_render_logon_retry <setup-dir> <log> [attempts] [delay]
#                                            the bash retry loop the wrapper runs
#     cc_linger_verdict <rc> <output> <user> PASS|FAIL|NA <message>
# ============================================================================

[[ -n "${CC_SUPERVISE_LIB_LOADED:-}" ]] && return 0
CC_SUPERVISE_LIB_LOADED=1

# The unit's FILE NAME: `cc-<install-id>-<kind>.service`. The install id is
# deploy/env-lib.sh's cc_install_id (`<checkout basename>-<8 hex of the
# path>`) — the same identity the state directory is named by — so two
# installs on one host never collide, and a unit name says which checkout it
# runs. A basename may carry characters a unit name may not (a space, an `@`,
# which means "template instance" to systemd), so everything outside
# [A-Za-z0-9_.-] becomes `_`; the 8 hex keep it unique regardless.
cc_sup_unit_name() { # cc_sup_unit_name <install-id> <kind>
  local id="$1"
  id="${id//[^A-Za-z0-9_.-]/_}"
  printf 'cc-%s-%s.service' "$id" "$2"
}

# One ExecStart= word. systemd splits ExecStart on whitespace, honours double
# quotes with C-style backslash escapes, expands `%` specifiers and `$VAR`
# references — so `%` is written `%%` and `$` is written `$$` (both mean the
# literal character), and anything that is not a plain path-ish word is
# double-quoted with `\` and `"` escaped. A bare `;` would be a command
# separator, which is one more reason everything unusual is quoted.
cc_sup_systemd_word() { # cc_sup_systemd_word <arg>
  local a="$1"
  a="${a//%/%%}"
  a="${a//\$/\$\$}"
  if [[ -n "$a" && "$a" != *[!A-Za-z0-9_./:@+=,%\$-]* ]]; then
    printf '%s' "$a"
    return 0
  fi
  a="${a//\\/\\\\}"
  a="${a//\"/\\\"}"
  printf '"%s"' "$a"
}

# A path-valued setting (WorkingDirectory=, EnvironmentFile=, the append:
# log target): the whole remainder of the line is the path, so whitespace is
# fine, but `%` is still a specifier.
cc_sup_systemd_path() { # cc_sup_systemd_path <path>
  printf '%s' "${1//%/%%}"
}

# One Environment= line. Quoted, so a value with a space survives; `$` has no
# meaning there (systemd does no variable expansion in Environment=), `%` does.
cc_sup_env_line() { # cc_sup_env_line <KEY=VALUE>
  local a="$1"
  a="${a//\\/\\\\}"
  a="${a//\"/\\\"}"
  a="${a//%/%%}"
  printf 'Environment="%s"\n' "$a"
}

# The variables a process started by HAND inherits from setup.sh that are NOT
# in .env — derived by load_env and deploy/env-lib.sh's cc_export_tls_env from
# CC_CA_BUNDLE, CC_TLS_INSECURE and CC_PROXY (the 2026-09-23 record's D4
# fan-out), plus PYTHONUTF8. A unit's EnvironmentFile= gives it .env and
# nothing else, so these are carried as Environment= lines, or the API under
# systemd would dial Jira through a TLS-intercepting proxy without the CA the
# same API trusted when it was started by hand. Names only — the caller reads
# the values out of its own environment.
cc_sup_passthrough_names() {
  printf '%s\n' PYTHONUTF8 \
    CURL_CA_BUNDLE SSL_CERT_FILE REQUESTS_CA_BUNDLE PIP_CERT \
    NODE_EXTRA_CA_CERTS NPM_CONFIG_CAFILE GIT_SSL_CAINFO \
    PIP_TRUSTED_HOST UV_INSECURE_HOST NPM_CONFIG_STRICT_SSL \
    NODE_TLS_REJECT_UNAUTHORIZED GIT_SSL_NO_VERIFY CURL_HOME \
    http_proxy https_proxy HTTP_PROXY HTTPS_PROXY no_proxy NO_PROXY
}

# A whole systemd --user unit, on stdout.
#
#   cc_render_unit <unit-name> <description> <workdir> <env-file> <log>
#                  <after-unit|-> <killmode|-> -- [KEY=VALUE ...] -- <exe> [args...]
#
# What the unit is, and why each line:
#   * EnvironmentFile=<the install's .env> — the process reads the ONE answer
#     file exactly as it did when `boot` started it by hand with .env sourced
#     (`set -a`); CC_SANDBOX_RUNNER_TOKEN reaches the runner this way, the same
#     value the API sends. systemd's parser takes the quoting this profile
#     writes (`KEY="two words"`, q_quote's shape).
#   * Environment= lines AFTER it for what boot sets per process (the cockpit's
#     PORT/GATEWAY_URL, the runner's backend) and the derived trust/proxy
#     variables above. Note systemd's precedence: a key set in BOTH wins from
#     EnvironmentFile=, so nothing here may be a key .env is expected to hold.
#   * Restart=on-failure, not always: `./setup.sh stop` sends TERM, a clean
#     exit, and a unit that restarted itself after a stop would make stop a lie.
#     (stop goes through `systemctl --user stop` anyway — this is the backstop.)
#   * StandardOutput/StandardError=append:<state>/<name>.log — the same log
#     files a hand start writes, so report and diagnose read one place.
#   * WantedBy=default.target — a user unit's "at login", which with
#     lingering on (a `check` row since v2.57.0) is "at boot".
#   * KillMode= only when asked: the API spawns the cockpit-driven updater
#     (deploy/single/update-run.sh), which STOPS the API mid-run and must
#     outlive it. start_new_session does not leave a cgroup, and the default
#     control-group kill mode would take the updater down with the API it is
#     updating — so the API unit says KillMode=process.
cc_render_unit() {
  local name="$1" desc="$2" wd="$3" envf="$4" log="$5" after="$6" kill="$7"
  if (( $# < 7 )); then printf 'cc_render_unit: needs 7 fields, then --\n' >&2; return 1; fi
  shift 7
  [[ "${1:-}" == -- ]] || { printf 'cc_render_unit: expected -- before the environment\n' >&2; return 1; }
  shift
  local envs=()
  while (( $# )) && [[ "$1" != -- ]]; do envs+=("$1"); shift; done
  [[ "${1:-}" == -- ]] || { printf 'cc_render_unit: expected -- before the command\n' >&2; return 1; }
  shift
  (( $# )) || { printf 'cc_render_unit: no command\n' >&2; return 1; }
  [[ "$1" == /* ]] || { printf 'cc_render_unit: the executable must be an absolute path, got %s\n' "$1" >&2; return 1; }
  local exec="" w e
  for w in "$@"; do exec="${exec:+$exec }$(cc_sup_systemd_word "$w")"; done
  printf '# %s — written by deploy/single/setup.sh `boot`, which rewrites it on every\n' "$name"
  printf '# run: an edit here does not survive. 2026-10-01 design record, D6 (everything\n'
  printf '# the install starts, it supervises). Stop it with ./setup.sh stop.\n'
  printf '[Unit]\n'
  printf 'Description=%s\n' "${desc//%/%%}"
  [[ -n "$after" && "$after" != "-" ]] && printf 'After=%s\n' "$after"
  printf '\n[Service]\n'
  printf 'Type=simple\n'
  printf 'WorkingDirectory=%s\n' "$(cc_sup_systemd_path "$wd")"
  printf 'EnvironmentFile=%s\n' "$(cc_sup_systemd_path "$envf")"
  for e in ${envs[@]+"${envs[@]}"}; do cc_sup_env_line "$e"; done
  printf 'ExecStart=%s\n' "$exec"
  printf 'Restart=on-failure\n'
  printf 'RestartSec=5\n'
  [[ -n "$kill" && "$kill" != "-" ]] && printf 'KillMode=%s\n' "$kill"
  printf 'StandardOutput=append:%s\n' "$(cc_sup_systemd_path "$log")"
  printf 'StandardError=append:%s\n' "$(cc_sup_systemd_path "$log")"
  printf '\n[Install]\n'
  printf 'WantedBy=default.target\n'
}

# ── Windows: the logon entry ────────────────────────────────────────────────
# The wrapper cmd.exe runs at logon (a scheduled task, or a copy in the user's
# Startup folder). It is kept TINY on purpose: cmd.exe's quoting is hostile, so
# all it does is hand ONE script path to Git Bash, in the shape the previous
# wrapper proved on the Windows testbed (`"<bash.exe>" -lc "<command>"`, the
# path single-quoted for bash inside cmd's double quotes). `%` is doubled
# because a .cmd file expands `%NAME%` even inside quotes. CRLF line ends:
# it is a batch file.
cc_render_logon_cmd() { # cc_render_logon_cmd <bash.exe, Windows form> <retry-script path, as bash sees it>
  local bashw="${1//%/%%}" script="${2//%/%%}"
  printf '@echo off\r\n"%s" -lc "bash '\''%s'\''"\r\n' "$bashw" "$script"
}

# The Startup-folder entry, when that is the logon entry: ONE line calling the
# state dir's wrapper, never a copy of it — so `boot` refreshes the one
# wrapper in place and both kinds of entry run the same script. `call`, so a
# `%` in the path is doubled like the wrapper's own.
cc_render_logon_startup() { # cc_render_logon_startup <wrapper, Windows form>
  printf '@echo off\r\ncall "%s"\r\n' "${1//%/%%}"
}

# WHICH logon entry this install keeps — exactly ONE, whatever ran before
# (the 2026-10-02 testbed run's second pass, F21: an elevated first `boot`
# registered the scheduled task, a later non-elevated logon run could not
# re-create it and ALSO wrote the Startup entry, and both fired). The rule:
# the scheduled task wins whenever there is one — it already exists, or this
# run could create it — and then a Startup entry is REMOVED; only with no task
# and no way to create one is the Startup entry written (or refreshed). Pure:
# the caller asks schtasks and the filesystem and acts on the answer.
#   in:  <task exists 0|1> <startup entry exists 0|1> <task created now 0|1>
#        (the third is only consulted when the first is 0)
#   out: "<entry> <startup-action>": task-kept|task-created|startup  and
#        remove|leave|write
cc_logon_entry_plan() { # cc_logon_entry_plan <task> <startup> <created>
  local task="$1" startup="$2" created="$3" entry
  if (( task )); then entry=task-kept
  elif (( created )); then entry=task-created
  else printf 'startup write'; return 0
  fi
  if (( startup )); then printf '%s remove' "$entry"; else printf '%s leave' "$entry"; fi
}

# The retry loop the wrapper hands to bash — a bash script, so the loop is
# written in a language that has one. D6: the logon entry runs `./setup.sh`,
# THE resume command, not `boot` — so after a reboot "start whatever is not
# running" and "an install that never completed" are one code path, and the
# second leaves a ledger row in boot-at-logon.log instead of silence.
#
# Decided here, beyond the record:
#   * `--accept-warnings`: there is no terminal at logon, and with no terminal
#     a WARN-only `check` STOPS the run (rustup's rule) — so every logon would
#     end at the gate on, say, a node-version warning;
#   * stdin is /dev/null: a logon run is headless BY DEFINITION. The task runs
#     this in an interactive console, so stdin WAS a terminal, and every prompt
#     keyed on `[[ -t 0 ]]` asked a question nobody could answer in a locked
#     session — `boot/operator-name` waited forever on an install whose
#     operator had answered in the cockpit (the 2026-10-02 testbed run's
#     second pass, F19). With /dev/null each takes its headless branch (the
#     check gate's [y/N] is covered by --accept-warnings anyway);
#   * the RETRY: at logon the podman machine and its containers may still be
#     starting, and a run that starts too early fails (exit 1). So exit 1 is
#     retried, <delay> seconds apart, at most <attempts> times. Exit 3 is the
#     operator's move (the LiteLLM catalog, the demo approval) and retrying
#     cannot make it; any other code is not a failure this loop understands,
#     so it stops and says so. A stale run lock from a power cut is the
#     driver's to reclaim (D11), not this loop's;
#   * exit 0 and 2 are NOT trusted alone: a setup.sh killed from outside
#     reported exit 0 to its MSYS parent on Windows, and the loop logged
#     "done" with nothing started (F23). An attempt is finished only when the
#     DRIVER said so — its own log (<driver-log>, the state dir's
#     setup-log.txt, which logline appends to) gained a
#     `run end: ./setup.sh all -> exit <0|2>` line DURING this attempt. If it
#     did not, the attempt "ended without finishing" and is retried within the
#     same budget. The size check costs this loop a `wc`; the driver nothing.
# Every attempt appends a timestamped line, and the driver's own output, to
# the log. Paths are written with printf %q, so a space or a quote in either
# cannot break the script.
cc_render_logon_retry() { # cc_render_logon_retry <setup-dir> <log> <driver-log> [attempts=10] [delay=60]
  local dir log slog n="${4:-10}" d="${5:-60}"
  printf -v dir '%q' "$1"
  printf -v log '%q' "$2"
  printf -v slog '%q' "$3"
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    '# boot-at-logon.sh — written by deploy/single/setup.sh `boot`, which rewrites it on' \
    '# every run; cc-boot.cmd runs it at logon. 2026-10-01 design record, D6: run the' \
    '# RESUME command (./setup.sh), headless, retry while the podman machine is still' \
    '# starting, stop at once when the run is waiting on the operator.' \
    "log=$log" \
    "slog=$slog" \
    "attempts=$n" \
    "delay=$d" \
    'stamp() { date -u +%FT%TZ; }' \
    'size() { local s; s="$(wc -c <"$slog" 2>/dev/null)" || s=0; s="${s//[!0-9]/}"; printf %s "${s:-0}"; }' \
    "cd $dir || { printf '%s cannot cd to %s\\n' \"\$(stamp)\" $dir >>\"\$log\"; exit 1; }" \
    'n=1' \
    'while :; do' \
    '  printf '\''%s attempt %s/%s: ./setup.sh --accept-warnings\n'\'' "$(stamp)" "$n" "$attempts" >>"$log"' \
    '  before="$(size)"' \
    '  ./setup.sh --accept-warnings </dev/null >>"$log" 2>&1' \
    '  rc=$?' \
    '  case "$rc" in' \
    '    0|2)' \
    '      if tail -c +"$((before + 1))" "$slog" 2>/dev/null | grep -Eq "run end: \./setup\.sh all -> exit $rc( |\$)"; then' \
    '        printf '\''%s attempt %s/%s: exit %s — done\n'\'' "$(stamp)" "$n" "$attempts" "$rc" >>"$log"; exit "$rc"' \
    '      fi' \
    '      printf '\''%s attempt %s/%s: exit %s, but the driver logged no run end — it ended without finishing (killed?)\n'\'' "$(stamp)" "$n" "$attempts" "$rc" >>"$log"' \
    '      rc=1 ;;' \
    '    3)   printf '\''%s attempt %s/%s: exit 3 — waiting on the operator; not retrying (run ./setup.sh in a terminal)\n'\'' "$(stamp)" "$n" "$attempts" >>"$log"; exit 3 ;;' \
    '    1)   ;;' \
    '    *)   printf '\''%s attempt %s/%s: exit %s — not a failure this loop retries; stopping\n'\'' "$(stamp)" "$n" "$attempts" "$rc" >>"$log"; exit "$rc" ;;' \
    '  esac' \
    '  if (( n >= attempts )); then' \
    '    printf '\''%s attempt %s/%s: giving up; run ./setup.sh in a terminal, or ./setup.sh report\n'\'' "$(stamp)" "$n" "$attempts" >>"$log"' \
    '    exit 1' \
    '  fi' \
    '  printf '\''%s attempt %s/%s: retrying in %ss (the podman machine may still be starting)\n'\'' "$(stamp)" "$n" "$attempts" "$delay" >>"$log"' \
    '  n=$((n + 1))' \
    '  sleep "$delay"' \
    'done'
}

# ── lingering (D6: a `check` row) ───────────────────────────────────────────
# Without lingering, systemd tears a user's manager down — and every user unit
# and rootless container with it — when their last login session ends; the
# three units boot writes would then run only while somebody is logged in.
# One verdict from what `loginctl show-user <user> --property=Linger` said
# (its exit code, and stdout+stderr together):
#   PASS  Linger=yes
#   FAIL  Linger=no, or logind answered that the user is "not logged in or
#         lingering" (asked from outside a session) — naming the exact command
#   NA    anything else: no logind here (a container, WSL without systemd,
#         Git Bash), so lingering is not a thing this host has
cc_linger_verdict() { # cc_linger_verdict <rc> <output> <user>
  local rc="$1" out="$2" user="$3"
  if [[ "$out" == *"Linger=yes"* ]]; then
    printf 'PASS lingering is enabled for %s — its user units and containers outlive the login session' "$user"
  elif [[ "$out" == *"Linger=no"* || "$out" == *"not logged in or lingering"* ]]; then
    printf "FAIL lingering is OFF for %s, so systemd stops this install's user units and every rootless container when the last login session ends — run: loginctl enable-linger %s" "$user" "$user"
  else
    local why="${out%%$'\n'*}"
    [[ -n "$why" ]] || why="exit $rc, no output"
    printf 'NA no logind answers here (%s) — not applicable' "$why"
  fi
}
