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
#   cc_get_kv, and that is the one reader this profile has.
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
#   status is done | failed | pending | gate. NO VALUE EVER LANDS IN IT: the
#   inputs are recorded as a sha256 FINGERPRINT, and `reason` is the last
#   FAIL/USERACTION message text, which never carries a value by the existing
#   output protocol (keys are referred to by NAME). That is what makes the
#   ledger safe to print, to paste, and to put at the top of a report.
#
#   Functions:
#     cc_steps_load <tsv>                  parse + validate the manifest
#     cc_steps_phases                      the phases, in manifest order
#     cc_steps_for_phase <phase>           its step names, in manifest order
#     cc_steps_all                         every `<phase>/<step>`, in order
#     cc_step_field <phase> <step> <name>  one column of one row
#     cc_steps_requires <phase> <step>     its requires, space-separated
#     cc_fingerprint <env-file> <reads>    sha256 over the step's input VALUES
#     cc_ledger_write <ledger> <step> <status> <version> <at> <fp> <reason>
#     cc_ledger_read <ledger>              every row, comments stripped
#     cc_ledger_status <ledger> <step>     done|failed|pending|gate, or empty
#     cc_ledger_field <ledger> <step> <n>  one column of one ledger row
#     cc_ledger_blocked <ledger> <phase>   the first require that is not done
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

# ── the fingerprint (D2) ────────────────────────────────────────────────────
# sha256 over "KEY=VALUE\n" for each key in the step's `reads`, IN THE ORDER
# THE MANIFEST LISTS THEM — so the digest is a property of the row, not of a
# shell's hash ordering. An .env edit then re-runs exactly the steps whose
# inputs it changed, which is the whole mechanism behind "resume is one
# command".
#
# The three-way fallback mirrors cc_install_id's: sha256sum (coreutils),
# shasum (macOS), openssl. A host with none of them gets `nohash`, which
# compares equal to itself and simply makes the fingerprint gate a no-op
# rather than a crash.
cc__sha256() { # cc__sha256 <text>
  local h=""
  if command -v sha256sum >/dev/null 2>&1; then
    h="$(printf '%s' "$1" | sha256sum 2>/dev/null | cut -d' ' -f1)"
  elif command -v shasum >/dev/null 2>&1; then
    h="$(printf '%s' "$1" | shasum -a 256 2>/dev/null | cut -d' ' -f1)"
  elif command -v openssl >/dev/null 2>&1; then
    h="$(printf '%s' "$1" | openssl dgst -sha256 2>/dev/null | sed 's/.*= *//')"
  fi
  printf '%s' "${h:-nohash}"
}

cc_fingerprint() { # cc_fingerprint <env-file> <reads-csv>
  local f="$1" csv="$2" payload="" key
  [[ -z "$csv" || "$csv" == "-" ]] && { printf 'none'; return 0; }
  # Comma-split without touching the caller's IFS for anything else.
  local oldifs="$IFS"
  IFS=','
  local keys=($csv)
  IFS="$oldifs"
  for key in ${keys[@]+"${keys[@]}"}; do
    [[ -n "$key" ]] || continue
    payload="${payload}${key}=$(cc_get_kv "$f" "$key")"$'\n'
  done
  cc__sha256 "$payload"
}

# ── the ledger ──────────────────────────────────────────────────────────────
# One line, tabs and newlines flattened: the ledger is read back by field
# position, so a reason carrying a tab would shift the columns.
cc__one_line() { local s="${1//$'\t'/ }"; s="${s//$'\n'/ }"; s="${s//$'\r'/ }"; printf '%s' "$s"; }

LEDGER_HEADER="# ledger.tsv — one row per <phase>/<step>. Columns, tab-separated:"$'\n'"#   step	status	version	at	fingerprint	reason"$'\n'"# Written by deploy/single/setup.sh. Fingerprints only — never a value."

# ATOMIC: a temp file beside the ledger (same filesystem, so `mv` is a rename)
# and one `mv -f`. A reader never sees a half-rewritten ledger, which matters
# because the thing reading it is usually the next run of the same script.
cc_ledger_write() { # cc_ledger_write <ledger> <step> <status> <version> <at> <fingerprint> <reason>
  local f="$1" step="$2" tmp="$1.tmp" line found=0 row
  row="$(printf '%s\t%s\t%s\t%s\t%s\t%s' \
    "$step" "$3" "${4:--}" "${5:--}" "${6:--}" "$(cc__one_line "${7:-}")")"
  : >"$tmp" || return 1
  chmod 600 "$tmp" 2>/dev/null || true
  if [[ -f "$f" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%$'\r'}"
      [[ -z "$line" ]] && continue
      if [[ "$line" == "$step"$'\t'* ]]; then
        printf '%s\n' "$row" >>"$tmp"; found=1
      else
        printf '%s\n' "$line" >>"$tmp"
      fi
    done <"$f"
  else
    printf '%s\n' "$LEDGER_HEADER" >>"$tmp"
  fi
  (( found )) || printf '%s\n' "$row" >>"$tmp"
  mv -f "$tmp" "$f" || { rm -f "$tmp"; return 1; }
  chmod 600 "$f" 2>/dev/null || true
  return 0
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
