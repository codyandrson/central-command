#!/usr/bin/env bash
# ============================================================================
# ledger-lib.sh — the step MANIFEST and the install's MEMORY, as pure functions.
#
#   Design record: docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md
#   (D1 "the process is one data file" and D2 "the ledger, and resume reads
#   it"). SOURCE this file; it defines functions and sets nothing global but
#   its own guard and the manifest's (empty) arrays.
#
#   WHY IT IS A SEPARATE FILE OF PURE FUNCTIONS — machine-lib.sh's precedent.
#   The decisions this file makes are the ones the 2026-10-01 investigation
#   found nothing was making: which step comes first, whether a step's inputs
#   changed, whether a phase may run at all. None of them needs podman, a
#   network or a database, so all of them are unit-tested on Linux by
#   tests/test_single_ledger_lib.py invoking bash directly. Nothing in this
#   file runs a container, probes an endpoint, or writes anything but the
#   ledger it is handed.
#
#   SOURCE deploy/env-lib.sh FIRST: cc_fingerprint reads .env values through
#   cc_get_kv, and that is the one reader this profile has; it and
#   cc_tree_hash hash through cc_sha256_stdin, the one hasher.
#
#   THE MANIFEST (deploy/single/steps.tsv) — eight tab-separated columns,
#   `-` where empty: phase, step, kind, requires, reads, writes, probe, doc.
#   The file itself documents them; this library only enforces the shape.
#
#   THE LEDGER (<state>/ledger.tsv) — six tab-separated columns, one row per
#   `<phase>/<step>`:
#
#     step  status  version  at  fingerprint  reason
#
#   status is done | failed | pending | gate | started. NO VALUE EVER LANDS IN
#   IT: the inputs are recorded as a sha256 FINGERPRINT, and `reason` is the
#   last FAIL/USERACTION message text, which never carries a value by the
#   existing output protocol (keys are referred to by NAME). That is what makes
#   the ledger safe to print, to paste, and to put at the top of a report.
#
#   `started` (v2.56.0, D11 — dpkg's `half-configured`, a state of its own) is
#   written for EVERY row of a phase, in one atomic rewrite, immediately BEFORE
#   the phase function runs; the outcome then overwrites each row. So a run
#   that is killed mid-phase (a power cut, a closed terminal, SIGKILL) leaves
#   that phase reading "started, not finished" — never `pending`, which would
#   claim it never began, and never the `done` it carried from the run before,
#   which would claim the half-run phase is still whole. `started` is not
#   `done`: it blocks what requires it, and the next `./setup.sh` re-runs it.
#
#   THE DECISION (D2 rule 2 + D11's drift rule) lives HERE, once:
#   `cc_row_decide` judges one row and `cc_phase_decide` a phase. The real skip
#   in the full run and the PLAN printed before it both call them, so the plan
#   cannot disagree with what the loop then does — only with a world the run
#   itself changes between the two (which the plan's heading says).
#
#   Functions:
#     cc_steps_load <tsv>                  parse + validate the manifest
#     cc_steps_phases                      the phases, in manifest order
#     cc_steps_for_phase <phase>           its step names, in manifest order
#     cc_steps_all                         every `<phase>/<step>`, in order
#     cc_step_field <phase> <step> <name>  one column of one row
#     cc_steps_requires <phase> <step>     its requires, space-separated
#     cc_fingerprint <env-file> <reads>    sha256 over the step's input VALUES
#                                          (and a tree input's content, `@dir`)
#     cc_tree_hash <root> <dir>            a repo-relative directory's content hash
#     cc_ledger_write <ledger> <step> <status> <version> <at> <fp> <reason>
#     cc_ledger_write_batch <ledger> [<step> <status> <version> <at> <fp> <reason>]...
#                                          many rows, ONE atomic rewrite
#     cc_ledger_mark_started <ledger> <phase> <version> <at> <env-file>
#                                          every row of a phase -> `started`
#     cc_ledger_read <ledger>              every row, comments stripped
#     cc_ledger_load <ledger>              every row into LEDGER_ROWS[<step>]
#     cc_ledger_status <ledger> <step>     done|failed|pending|gate|started, or empty
#     cc_ledger_field <ledger> <step> <n>  one column of one ledger row
#     cc_ledger_blocked <ledger> <phase>   the first require that is not done
#     cc_row_decide <row> <version> <env-file> <reads> <probe|->
#                                          skip or run, and why (ROW_* globals)
#     cc_phase_decide <ledger> <phase> <version> <env-file>
#                                          the same for a phase (PHASE_* globals)
#     cc_phase_plan_text <phase> <version> one plan sentence from PHASE_*
# ============================================================================

[[ -n "${CC_LEDGER_LIB_LOADED:-}" ]] && return 0
CC_LEDGER_LIB_LOADED=1

# The manifest, in memory. NOT CC_-prefixed on purpose: every CC_* name a
# deploy script reads has to be declared in .env.example
# (tests/test_single_airgap_seams.py), and a parsed-manifest array is not an
# operator answer.
declare -A STEPS_ROW=()
STEPS_ORDER=()
STEPS_PHASE_LIST=""
# The ledger, in memory, keyed `<phase>/<step>` -> its whole line. Loaded ONCE
# per decision (cc_ledger_load) rather than re-read per field: cc_ledger_field
# reads the whole file and forks a `cut` per call, and the plan judges every
# row of the install — Git Bash on Windows forks slowly enough for that to show.
declare -A LEDGER_ROWS=()

# Column order, by name. One list, so a new column is one edit here and one in
# the file's header.
cc__step_col() { # cc__step_col <name>
  case "$1" in
    phase)    printf '1' ;;
    step)     printf '2' ;;
    kind)     printf '3' ;;
    requires) printf '4' ;;
    reads)    printf '5' ;;
    writes)   printf '6' ;;
    probe)    printf '7' ;;
    doc)      printf '8' ;;
    *)        printf '0' ;;
  esac
}

# One column out of one row. `cut`, not an IFS split: a `doc` sentence contains
# spaces and IFS-splitting on a tab COLLAPSES consecutive tabs, which is
# exactly why the schema writes `-` for an empty field (questions.tsv's rule,
# learned the same way).
cc__step_cut() { # cc__step_cut <row> <n>
  printf '%s' "$1" | cut -d$'\t' -f"$2"
}

# Parse and VALIDATE. Returns 1 with the reason on stderr — a manifest this
# library cannot trust is release content that failed to ship, not something to
# work around at install time.
cc_steps_load() { # cc_steps_load <steps.tsv>
  local f="$1" line n phase step kind probe reads key
  [[ -f "$f" ]] || { printf 'steps: %s is missing — it is release content, so re-extract the release\n' "$f" >&2; return 1; }
  STEPS_ROW=(); STEPS_ORDER=(); STEPS_PHASE_LIST=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"          # a CRLF checkout must not append \r to `doc`
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "$line" == '#'* ]] && continue
    # Field count by counting the separators: a row with seven tabs has eight
    # fields, and anything else is a hand edit that lost one.
    n="${line//[!$'\t']/}"
    if (( ${#n} != 7 )); then
      printf 'steps: a row needs 8 tab-separated fields, saw %s: %s\n' "$(( ${#n} + 1 ))" "$line" >&2
      return 1
    fi
    phase="$(cc__step_cut "$line" 1)"
    step="$(cc__step_cut "$line" 2)"
    kind="$(cc__step_cut "$line" 3)"
    reads="$(cc__step_cut "$line" 5)"
    probe="$(cc__step_cut "$line" 7)"
    [[ -n "$phase" && "$phase" != "-" ]] || { printf 'steps: a row with no phase: %s\n' "$line" >&2; return 1; }
    [[ -n "$step"  && "$step"  != "-" ]] || { printf 'steps: a row with no step name: %s\n' "$line" >&2; return 1; }
    case "$kind" in run|gate|human) ;; *) printf 'steps: %s/%s has kind %s — run, gate or human\n' "$phase" "$step" "$kind" >&2; return 1 ;; esac
    [[ -n "$probe" && "$probe" != "-" ]] || { printf 'steps: %s/%s has no probe — a step whose effect cannot be read is a step nothing can resume\n' "$phase" "$step" >&2; return 1; }
    # A `reads` glob cannot be fingerprinted (there is no stable order for the
    # keys it would match), so the manifest spells every input out. Globs are
    # for `writes`, where the family is the fact (CC_IMG_*).
    [[ "$reads" == *'*'* ]] && { printf 'steps: %s/%s reads a GLOB (%s) — a fingerprint needs named keys\n' "$phase" "$step" "$reads" >&2; return 1; }
    # A TREE input (`@<dir>`, v2.57.0) names a directory INSIDE the release, by
    # a repo-relative path: no leading `/`, no `..`, nothing that could make a
    # fingerprint read outside the checkout it describes.
    local _r
    local _oldifs="$IFS"; IFS=','
    for _r in $reads; do
      IFS="$_oldifs"
      [[ "$_r" == @* ]] || continue
      if [[ "$_r" == "@" || "$_r" == @/* || "$_r" == *..* ]]; then
        printf 'steps: %s/%s reads %s — a tree input is @<repo-relative directory>, with no leading / and no ..\n' "$phase" "$step" "$_r" >&2
        return 1
      fi
    done
    IFS="$_oldifs"
    key="$phase/$step"
    [[ -n "${STEPS_ROW[$key]:-}" ]] && { printf 'steps: %s appears twice — the ledger keys on it\n' "$key" >&2; return 1; }
    STEPS_ROW["$key"]="$line"
    STEPS_ORDER+=("$key")
    [[ " $STEPS_PHASE_LIST " == *" $phase "* ]] || STEPS_PHASE_LIST="${STEPS_PHASE_LIST:+$STEPS_PHASE_LIST }$phase"
  done <"$f"
  (( ${#STEPS_ORDER[@]} )) || { printf 'steps: %s declares no rows\n' "$f" >&2; return 1; }
  return 0
}

cc_steps_phases() { printf '%s\n' "$STEPS_PHASE_LIST"; }

cc_steps_all() {
  local key
  for key in ${STEPS_ORDER[@]+"${STEPS_ORDER[@]}"}; do printf '%s\n' "$key"; done
}

cc_steps_for_phase() { # cc_steps_for_phase <phase>
  local key
  for key in ${STEPS_ORDER[@]+"${STEPS_ORDER[@]}"}; do
    [[ "${key%%/*}" == "$1" ]] && printf '%s\n' "${key#*/}"
  done
  return 0
}

cc_step_field() { # cc_step_field <phase> <step> <column-name>
  local row="${STEPS_ROW[$1/$2]:-}" col
  [[ -n "$row" ]] || return 1
  col="$(cc__step_col "$3")"
  [[ "$col" != 0 ]] || return 1
  local v; v="$(cc__step_cut "$row" "$col")"
  [[ "$v" == "-" ]] && v=""
  printf '%s' "$v"
}

# The requires list, space-separated and already `<phase>/<step>`-qualified.
cc_steps_requires() { # cc_steps_requires <phase> <step>
  local v; v="$(cc_step_field "$1" "$2" requires)" || return 1
  printf '%s' "${v//,/ }"
}

# The three columns a DECISION needs, into ROWDEF_KIND / ROWDEF_READS /
# ROWDEF_PROBE,
# with no fork: cc_step_field costs a subshell and a `cut` per column, and the
# plan asks for two columns of every row. An IFS split on a tab is safe HERE
# (and only because cc_steps_load refused any row with an empty field — every
# empty one is written `-`, so no two tabs are adjacent to collapse).
cc__step_split() { # cc__step_split <phase> <step>
  local row="${STEPS_ROW[$1/$2]:-}" _p _s req writes doc
  ROWDEF_KIND=""; ROWDEF_READS=""; ROWDEF_PROBE=""
  [[ -n "$row" ]] || return 1
  IFS=$'\t' read -r _p _s ROWDEF_KIND req ROWDEF_READS writes ROWDEF_PROBE doc <<<"$row"
  [[ "$ROWDEF_READS" == "-" ]] && ROWDEF_READS=""
  return 0
}

# ── the fingerprint (D2) ────────────────────────────────────────────────────
# sha256 over "KEY=VALUE\n" for each key in the step's `reads`, IN THE ORDER
# THE MANIFEST LISTS THEM — so the digest is a property of the row, not of a
# shell's hash ordering. An .env edit then re-runs exactly the steps whose
# inputs it changed, which is the whole mechanism behind "resume is one
# command".
#
# Hashed by deploy/env-lib.sh's cc_sha256_stdin — the ONE text hasher of this
# profile (sha256sum, shasum or openssl; a host with none of them gets
# `nohash`, which compares equal to itself and simply makes the fingerprint gate
# a no-op rather than a crash). This file carried a second copy of that
# fallback until v2.57.0; env-lib.sh is sourced first, as the header says.
cc_fingerprint() { # cc_fingerprint <env-file> <reads-csv>
  local f="$1" csv="$2" payload="" key root
  [[ -z "$csv" || "$csv" == "-" ]] && { printf 'none'; return 0; }
  # Comma-split without touching the caller's IFS for anything else.
  local oldifs="$IFS"
  IFS=','
  local keys=($csv)
  IFS="$oldifs"
  for key in ${keys[@]+"${keys[@]}"}; do
    [[ -n "$key" ]] || continue
    if [[ "$key" == @* ]]; then
      # A TREE input: the directory's content hash stands where a value would.
      # The tree is the release beside the answer file — the checkout whose
      # .env this is — unless LEDGER_TREE_ROOT says otherwise (a test, or a
      # caller fingerprinting a staged tree). Not CC_-prefixed: it is not an
      # operator answer (see STEPS_ROW above).
      root="${LEDGER_TREE_ROOT:-}"
      [[ -n "$root" ]] || root="$(cd "$(dirname "$f")" 2>/dev/null && pwd)"
      payload="${payload}${key}=$(cc_tree_hash "$root" "${key#@}")"$'\n'
      continue
    fi
    payload="${payload}${key}=$(cc_get_kv "$f" "$key")"$'\n'
  done
  printf '%s' "$payload" | cc_sha256_stdin
}

# ── a TREE input (v2.57.0) ──────────────────────────────────────────────────
# `reads` was .env keys only, and one step's input is not an answer at all:
# boot/skills-imported imports the bundled skills/*/ folders, so the release's
# skills/ directory is what shaped it (2026-10-01 record, D7: "the ledger row's
# fingerprint includes the folder's tree hash"). `@skills` in a `reads` column
# means "the content of the repo-relative directory skills/", and this is its
# digest:
#
#   sha256 over, for every regular file under the directory in byte order of
#   its repo-relative path:  "<path>\001\n" + its content with every line's
#   trailing CR dropped + "\001\n"   (\001, not NUL: bash strings hold no NUL)
#
# No git: a zip install has none until `./update.sh init`, and the fingerprint
# must mean the same thing on both. CRLF-INSENSITIVE, because a Windows
# checkout with core.autocrlf rewrites every line ending and that is not the
# release changing. The read is bash's own `read` — no fork per file, which on
# Git Bash is the difference between instant and seconds, and this digest is
# taken several times per run (the plan, `started`, the record). Its one
# blindness: a final line with or without its newline hashes the same, which
# is a difference no importer can see either. A directory that is not there
# hashes to `absent`, so it still compares equal to itself.
cc_tree_hash() { # cc_tree_hash <root> <repo-relative dir>
  local root="$1" rel="${2%/}" payload
  [[ -d "$root/$rel" ]] || { printf 'absent'; return 0; }
  payload="$(
    cd "$root" || exit 1
    # Byte order, not the locale's collation: the digest is a property of the
    # tree, never of the machine reading it.
    LC_ALL=C
    shopt -s globstar nullglob dotglob
    for p in "$rel"/**; do
      [[ -f "$p" ]] || continue
      printf '%s\001\n' "$p"
      while IFS= read -r line || [[ -n "$line" ]]; do
        printf '%s\n' "${line%$'\r'}"
      done <"$p"
      printf '\001\n'
    done
  )" || { printf 'unreadable'; return 0; }
  printf '%s' "$payload" | cc_sha256_stdin
}

# ── the ledger ──────────────────────────────────────────────────────────────
# One line, tabs and newlines flattened: the ledger is read back by field
# position, so a reason carrying a tab would shift the columns.
cc__one_line() { local s="${1//$'\t'/ }"; s="${s//$'\n'/ }"; s="${s//$'\r'/ }"; printf '%s' "$s"; }

LEDGER_HEADER="# ledger.tsv — one row per <phase>/<step>. Columns, tab-separated:"$'\n'"#   step	status	version	at	fingerprint	reason"$'\n'"# status: done | failed | pending | gate | started (written before a phase runs — still there means that run was interrupted)"$'\n'"# Written by deploy/single/setup.sh. Fingerprints only — never a value."

# ATOMIC: a temp file beside the ledger (same filesystem, so `mv` is a rename)
# and one `mv -f`. A reader never sees a half-rewritten ledger, which matters
# because the thing reading it is usually the next run of the same script.
cc_ledger_write() { # cc_ledger_write <ledger> <step> <status> <version> <at> <fingerprint> <reason>
  cc_ledger_write_batch "$1" "$2" "$3" "${4:-}" "${5:-}" "${6:-}" "${7:-}"
}

# MANY rows, ONE rewrite (v2.56.0). Six arguments per row, in the column order
# above. A row already in the file is replaced in place; a new one is appended,
# in the order given. It exists for the `started` mark (every row of a phase,
# before the phase runs) and for recording a phase's outcome: one rewrite per
# ROW re-reads and re-renames the whole file for each, and costs the forks of
# a `mv` and a `chmod` per row on Git Bash, where a fork is slow. It is also
# the stronger promise — a phase's rows change together or not at all.
# Idempotent: the same batch twice leaves the same file.
cc_ledger_write_batch() { # cc_ledger_write_batch <ledger> [<step> <status> <version> <at> <fp> <reason>]...
  local f="$1"; shift
  (( $# % 6 == 0 )) || { printf 'ledger: a batch write takes six fields per row, got %s\n' "$#" >&2; return 1; }
  (( $# )) || return 0
  local tmp="$f.tmp" line key r reason
  local -A new=() written=()
  local order=()
  while (( $# )); do
    # One line, tabs and newlines flattened (cc__one_line, inlined to save the
    # subshell): the ledger is read back by field position.
    reason="${6//$'\t'/ }"; reason="${reason//$'\n'/ }"; reason="${reason//$'\r'/ }"
    r="$1"$'\t'"$2"$'\t'"${3:--}"$'\t'"${4:--}"$'\t'"${5:--}"$'\t'"$reason"
    [[ -n "${new[$1]+x}" ]] || order+=("$1")
    new["$1"]="$r"
    shift 6
  done
  {
    if [[ -f "$f" ]]; then
      while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ -z "$line" ]] && continue
        key="${line%%$'\t'*}"
        if [[ "$line" != '#'* && -n "${new[$key]+x}" ]]; then
          printf '%s\n' "${new[$key]}"; written["$key"]=1
        else
          printf '%s\n' "$line"
        fi
      done <"$f"
    else
      printf '%s\n' "$LEDGER_HEADER"
    fi
    for key in "${order[@]}"; do
      [[ -n "${written[$key]+x}" ]] || printf '%s\n' "${new[$key]}"
    done
  } >"$tmp" || { rm -f "$tmp"; return 1; }
  chmod 600 "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$f" || { rm -f "$tmp"; return 1; }
  return 0
}

# D11's `started`: EVERY row of <phase>, at this version, now, with the
# fingerprint of the inputs it is about to run with, and an empty reason — in
# one rewrite, BEFORE the phase function runs. The outcome overwrites each row
# afterwards (setup.sh's ledger_record); a run that never gets there leaves
# "started, not finished" behind, which is the truth about it. A phase with no
# manifest rows (status, validate, preflight) writes nothing.
cc_ledger_mark_started() { # cc_ledger_mark_started <ledger> <phase> <version> <at> <env-file>
  local ledger="$1" phase="$2" ver="$3" at="$4" envf="$5" step
  local args=()
  while IFS= read -r step; do
    [[ -n "$step" ]] || continue
    cc__step_split "$phase" "$step" || continue
    args+=("$phase/$step" started "$ver" "$at" "$(cc_fingerprint "$envf" "$ROWDEF_READS")" "")
  done < <(cc_steps_for_phase "$phase")
  (( ${#args[@]} )) || return 0
  cc_ledger_write_batch "$ledger" "${args[@]}"
}

cc_ledger_read() { # cc_ledger_read <ledger>
  local line
  [[ -f "$1" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == '#'* ]] && continue
    printf '%s\n' "$line"
  done <"$1"
  return 0
}

# Every row into LEDGER_ROWS, keyed on its step — one pass, no fork. A step
# written twice (a hand edit) keeps its LAST row, which is what
# cc_ledger_field's loop returns too.
cc_ledger_load() { # cc_ledger_load <ledger>
  local line
  LEDGER_ROWS=()
  [[ -f "$1" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == '#'* ]] && continue
    LEDGER_ROWS["${line%%$'\t'*}"]="$line"
  done <"$1"
  return 0
}

cc_ledger_field() { # cc_ledger_field <ledger> <step> <column-number>
  local line out=""
  while IFS= read -r line; do
    [[ "$line" == "$2"$'\t'* ]] || continue
    out="$(printf '%s' "$line" | cut -d$'\t' -f"$3")"
  done < <(cc_ledger_read "$1")
  printf '%s' "$out"
}

cc_ledger_status() { # cc_ledger_status <ledger> <step>
  cc_ledger_field "$1" "$2" 2
}

# THE DRIVER'S FIRST RULE (D2): never run ahead of a step that is not done.
# Prints "<step> <status>" for the FIRST require of this phase that is not
# `done` and returns 0 (blocked); returns 1 when every require is satisfied.
# An absent row reads as `pending`, which is what an empty ledger is made of.
# A `started` row blocks like any other non-`done` one (D11): it is the mark of
# a run that was interrupted inside that phase, and what it left behind is not
# known to be whole. The caller's refusal says so in words.
#
# ONLY requires that point at ANOTHER PHASE count here, and that is not a
# loophole — it is what P1 is. The phase FUNCTIONS stay (D1: this is not a step
# interpreter), so a row's requires inside its own phase are the order the
# phase's own linear bash already runs them in, and they can only be `done`
# AFTER the phase has run. Checking them beforehand would mean `boot` is
# blocked by `boot/boot-api` on every install that has not booted yet — i.e.
# forever. What the manifest's intra-phase requires buy is the ORDER the
# generated checklist reads in (D8) and the guard that it is acyclic.
cc_ledger_blocked() { # cc_ledger_blocked <ledger> <phase>
  local ledger="$1" phase="$2" step req r st
  while IFS= read -r step; do
    req="$(cc_steps_requires "$phase" "$step")"
    for r in $req; do
      [[ "${r%%/*}" == "$phase" ]] && continue
      st="$(cc_ledger_status "$ledger" "$r")"
      [[ "$st" == done ]] && continue
      printf '%s %s' "$r" "${st:-pending}"
      return 0
    done
  done < <(cc_steps_for_phase "$phase")
  return 1
}

# ── the decision: skip or run, and WHY (D2 rule 2, D11) ─────────────────────
# ONE row. Sets ROW_VERDICT (skip|run), ROW_CODE and ROW_DETAIL; ROW_AT is the
# row's own timestamp. Globals rather than stdout because the plan judges every
# row of the install and a `$(...)` per row is a fork per row on Git Bash.
#
#   ROW_CODE   meaning                                   ROW_DETAIL
#   done       skip: done, same version, same inputs,    the row's `at`
#              and the probe still reads the effect
#   pending    never run (or no row at all)              -
#   failed     the last run failed it                    the recorded reason
#   started    a run began it and never finished (D11)   the row's `at`
#   gate       waiting on the operator                   the recorded reason
#   version    done, but at another release              "<old> -> <new>"
#   inputs     done, but a `reads` value changed         -
#   drift      done and current, but the probe is false  the probe's name
#   unprobed   done and current, probe NOT asked (`-`)   -
#
# THE ORDER IS THE RULE: status first, then version, then the fingerprint,
# and the probe LAST — evaluated only when everything the ledger records says
# skip (DSC's Test-before-Set: a `done` row whose effect is gone is drift and
# runs again, never a skip). A fresh install's plan therefore costs no probe at
# all. `-` as the probe asks for no probe — the plan's "the verdict is already
# known, just classify the rest" case — and a row that would have needed one
# reads `unprobed`, which is never a skip.
cc_row_decide() { # cc_row_decide <ledger-row|''> <version> <env-file> <reads> <probe|->
  local row="$1" ver="$2" envf="$3" reads="$4" probe="$5" _s st rver at rfp reason
  ROW_VERDICT=run; ROW_CODE=pending; ROW_DETAIL=""; ROW_AT=""
  [[ -n "$row" ]] || return 0
  IFS=$'\t' read -r _s st rver at rfp reason <<<"$row"
  ROW_AT="$at"
  case "$st" in
    done)    ;;
    failed)  ROW_CODE=failed;  ROW_DETAIL="$reason"; return 0 ;;
    started) ROW_CODE=started; ROW_DETAIL="$at";     return 0 ;;
    gate)    ROW_CODE=gate;    ROW_DETAIL="$reason"; return 0 ;;
    # `pending`, and any status this release does not know: never a skip.
    *)       ROW_CODE=pending; return 0 ;;
  esac
  if [[ "$rver" != "$ver" ]]; then ROW_CODE=version; ROW_DETAIL="$rver -> $ver"; return 0; fi
  if [[ "$rfp" != "$(cc_fingerprint "$envf" "$reads")" ]]; then ROW_CODE=inputs; return 0; fi
  if [[ "$probe" == "-" || -z "$probe" ]]; then ROW_CODE=unprobed; return 0; fi
  if ! "$probe" >/dev/null 2>&1; then ROW_CODE=drift; ROW_DETAIL="$probe"; return 0; fi
  ROW_VERDICT=skip; ROW_CODE=done; ROW_DETAIL="$at"
  return 0
}

# ONE phase: it is skipped only when EVERY row is (D2 rule 2). Sets
#   PHASE_VERDICT    skip | run
#   PHASE_CODE       the FIRST non-skip row's ROW_CODE (done when skipping;
#                    `norows` for a phase the manifest declares nothing for)
#   PHASE_STEP       that row, `<phase>/<step>`
#   PHASE_DETAIL     its ROW_DETAIL
#   PHASE_READS      its `reads` KEY NAMES (never a value)
#   PHASE_PROBE      its probe's name
#   PHASE_SAME       how many LATER rows share PHASE_CODE
#   PHASE_NROWS      the phase's row count
#   PHASE_LAST_AT    the newest `at` of a skipped phase's rows
#   PHASE_DONE_LINES "<step>\t<at>\n" per row when skipping, else empty
# Once the verdict is `run`, later rows are classified WITHOUT their probes —
# the verdict cannot change, and a probe is a read of the live system.
cc_phase_decide() { # cc_phase_decide <ledger> <phase> <version> <env-file>
  local ledger="$1" phase="$2" ver="$3" envf="$4" step probe
  PHASE_VERDICT=skip; PHASE_CODE=done; PHASE_STEP=""; PHASE_DETAIL=""
  PHASE_READS=""; PHASE_PROBE=""; PHASE_SAME=0; PHASE_NROWS=0
  PHASE_LAST_AT=""; PHASE_DONE_LINES=""
  cc_ledger_load "$ledger"
  while IFS= read -r step; do
    [[ -n "$step" ]] || continue
    cc__step_split "$phase" "$step" || continue
    PHASE_NROWS=$((PHASE_NROWS + 1))
    probe="$ROWDEF_PROBE"
    [[ "$PHASE_VERDICT" == skip ]] || probe="-"
    cc_row_decide "${LEDGER_ROWS[$phase/$step]:-}" "$ver" "$envf" "$ROWDEF_READS" "$probe"
    if [[ "$PHASE_VERDICT" == skip ]]; then
      if [[ "$ROW_VERDICT" == skip ]]; then
        PHASE_DONE_LINES="${PHASE_DONE_LINES}${step}"$'\t'"${ROW_AT}"$'\n'
        [[ "$ROW_AT" > "$PHASE_LAST_AT" ]] && PHASE_LAST_AT="$ROW_AT"
      else
        PHASE_VERDICT=run; PHASE_CODE="$ROW_CODE"; PHASE_STEP="$phase/$step"
        PHASE_DETAIL="$ROW_DETAIL"; PHASE_READS="$ROWDEF_READS"; PHASE_PROBE="$ROWDEF_PROBE"
      fi
    elif [[ "$ROW_CODE" == "$PHASE_CODE" ]]; then
      PHASE_SAME=$((PHASE_SAME + 1))
    fi
  done < <(cc_steps_for_phase "$phase")
  if (( PHASE_NROWS == 0 )); then PHASE_VERDICT=run; PHASE_CODE=norows; fi
  [[ "$PHASE_VERDICT" == run ]] && PHASE_DONE_LINES=""
  return 0
}

# A `reads` column in words, for the plan: .env key NAMES as they are, and a
# tree input `@skills` as "the files under skills/" — which is what changed
# when a release (or a hand edit the tree-pristine row will refuse) touched it.
cc__reads_words() { # cc__reads_words <reads-csv>
  local out="" r
  local oldifs="$IFS"; IFS=','
  for r in $1; do
    IFS="$oldifs"
    [[ -n "$r" ]] || continue
    [[ "$r" == @* ]] && r="the files under ${r#@}/"
    out="${out:+$out, }$r"
  done
  IFS="$oldifs"
  printf '%s' "$out"
}

# One plan sentence for the phase cc_phase_decide just judged, WITHOUT the
# `PLAN ` prefix (the caller owns the output protocol). Key NAMES only: an
# `inputs` line names the row's `reads` keys and says "one of", because a
# fingerprint is a digest over all of them and cannot say which one moved.
cc_phase_plan_text() { # cc_phase_plan_text <phase> <version>
  local phase="$1" ver="$2" more="" what=""
  if [[ "$PHASE_VERDICT" == skip ]]; then
    if (( PHASE_NROWS == 1 )); then what="its 1 row is"; else what="all $PHASE_NROWS rows are"; fi
    printf '%s: WILL SKIP — %s done at %s with the same inputs, and every effect still reads present (last done %s)' \
      "$phase" "$what" "$ver" "${PHASE_LAST_AT:--}"
    return 0
  fi
  if (( PHASE_SAME > 0 )); then
    case "$PHASE_CODE" in
      pending) what="pending" ;;
      failed)  what="failed" ;;
      started) what="started and never finished" ;;
      gate)    what="waiting on you" ;;
      version) what="recorded at another release" ;;
      inputs)  what="with changed inputs" ;;
      *)       what="like it" ;;
    esac
    more=" (and $PHASE_SAME more row$( (( PHASE_SAME == 1 )) || printf s) $what)"
  fi
  printf '%s: WILL RUN — ' "$phase"
  case "$PHASE_CODE" in
    pending) printf 'never run: %s is pending%s' "$PHASE_STEP" "$more" ;;
    failed)  printf 'failed last time: %s — "%s"%s' "$PHASE_STEP" "${PHASE_DETAIL:-no reason was recorded}" "$more" ;;
    started) printf 'the last run was interrupted here: %s was started at %s and never finished%s' "$PHASE_STEP" "$PHASE_DETAIL" "$more" ;;
    gate)    printf 'waiting on you: %s%s%s' "$PHASE_STEP" "${PHASE_DETAIL:+ — \"$PHASE_DETAIL\"}" "$more" ;;
    version) printf 'version changed, %s: %s%s' "$PHASE_DETAIL" "$PHASE_STEP" "$more" ;;
    inputs)  printf 'inputs changed: %s reads one of %s%s' "$PHASE_STEP" "$(cc__reads_words "$PHASE_READS")" "$more" ;;
    drift)   printf 'effect absent: %s is recorded done but its probe %s reads false now (drift)' "$PHASE_STEP" "$PHASE_PROBE" ;;
    norows)  printf 'the manifest declares no rows for it, so there is nothing to skip on' ;;
    *)       printf '%s: %s' "$PHASE_CODE" "$PHASE_STEP" ;;
  esac
  return 0
}
