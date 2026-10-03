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
#     cc_fingerprint_prime <env-file> <reads>...
#                                          many rows' digests, ONE hasher process
#     cc_fingerprint_prime_rows <env-file> [<phase>]
#                                          the same for a phase, or every row
#     cc_ledger_cache_drop                 forget the run's cached digests,
#                                          tree hashes and probe verdicts
#     cc_probe_memo <probe> <env-file>     a probe's verdict, once per run
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
# reads the whole file per call, and the plan judges every row of the install.
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

# Every column of one row, into the global array TSV_F (TSV_F[0] is column 1).
# NOT an IFS split: a `doc` sentence contains spaces, and IFS-splitting on a
# tab COLLAPSES consecutive tabs, which is exactly why the schema writes `-`
# for an empty field (questions.tsv's rule, learned the same way). This split
# keeps an empty field empty, as `cut -f` does, so a hand edit that left one
# empty still reaches cc_steps_load's "a row with no phase" refusal rather than
# shifting every column after it. Pure parameter expansion and no `$(...)`: it
# used to be `printf | cut` per column, and cc_steps_load reads five columns of
# every row on every command — ~1,000 forks before the first line on Git Bash,
# where a fork costs tens of milliseconds (P5, the first laptop acceptance run).
TSV_F=()
cc__tsv_split() { # cc__tsv_split <row>  -> TSV_F
  local rest="$1"
  TSV_F=()
  while [[ "$rest" == *$'\t'* ]]; do
    TSV_F+=("${rest%%$'\t'*}")
    rest="${rest#*$'\t'}"
  done
  TSV_F+=("$rest")
}

# One column out of one row, printed — `cut -d<tab> -f<n>`'s answer for a row
# that has a tab (every manifest and ledger row has several): an empty string
# past the last column.
cc__step_cut() { # cc__step_cut <row> <n>
  cc__tsv_split "$1"
  printf '%s' "${TSV_F[$(( $2 - 1 ))]:-}"
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
    cc__tsv_split "$line"
    phase="${TSV_F[0]}"; step="${TSV_F[1]}"; kind="${TSV_F[2]}"
    reads="${TSV_F[4]}"; probe="${TSV_F[6]}"
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
  cc__tsv_split "$row"
  local v="${TSV_F[$(( col - 1 ))]:-}"
  [[ "$v" == "-" ]] && v=""
  printf '%s' "$v"
}

# The requires list, space-separated and already `<phase>/<step>`-qualified.
cc_steps_requires() { # cc_steps_requires <phase> <step>
  local v; v="$(cc_step_field "$1" "$2" requires)" || return 1
  printf '%s' "${v//,/ }"
}

# The four columns a DECISION needs, into ROWDEF_KIND / ROWDEF_READS /
# ROWDEF_PROBE / ROWDEF_REQUIRES (`-` read as empty, like cc_step_field),
# with no fork: cc_step_field costs a subshell per column, and the plan asks for
# several columns of every row. Split by cc__tsv_split, which keeps an empty
# field empty — an IFS split on a tab would collapse two adjacent tabs (the
# reason every empty field is written `-`), so this does not lean on the
# convention to read the right column.
cc__step_split() { # cc__step_split <phase> <step>
  local row="${STEPS_ROW[$1/$2]:-}"
  ROWDEF_KIND=""; ROWDEF_READS=""; ROWDEF_PROBE=""; ROWDEF_REQUIRES=""
  [[ -n "$row" ]] || return 1
  cc__tsv_split "$row"
  ROWDEF_KIND="${TSV_F[2]:-}"; ROWDEF_REQUIRES="${TSV_F[3]:-}"
  ROWDEF_READS="${TSV_F[4]:-}"; ROWDEF_PROBE="${TSV_F[6]:-}"
  [[ "$ROWDEF_READS" == "-" ]] && ROWDEF_READS=""
  [[ "$ROWDEF_REQUIRES" == "-" ]] && ROWDEF_REQUIRES=""
  return 0
}

# A phase's step names, in manifest order, into PHASE_STEPS — what
# cc_steps_for_phase prints, without the process substitution a
# `while read … < <(cc_steps_for_phase …)` costs (a fork per call).
PHASE_STEPS=()
cc__phase_steps() { # cc__phase_steps <phase>  -> PHASE_STEPS
  local key
  PHASE_STEPS=()
  for key in ${STEPS_ORDER[@]+"${STEPS_ORDER[@]}"}; do
    [[ "${key%%/*}" == "$1" ]] && PHASE_STEPS+=("${key#*/}")
  done
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
  cc__fingerprint_into "$1" "$2"
  printf '%s' "$FP_OUT"
}

# ── the fingerprint, without the forks (P5) ─────────────────────────────────
# The value above, computed the way the driver's hot path needs it. Each digest
# is the SAME sha256 over the SAME payload — the fingerprints already recorded
# in installed ledgers stay equal, so nothing re-runs after this update — but:
#
#   * cc__fingerprint_into sets FP_OUT instead of printing (a `$(…)` is a fork);
#   * the values come from env-lib.sh's answer-file cache (the one
#     cc_get_kv_cached reads), one read of .env per row or per batch instead
#     of a `$(cc_get_kv …)` subshell per key;
#   * a digest is CACHED per `reads` list (FP_CACHE) for as long as .env reads
#     the same — KVCACHE_GEN moves whenever the file's content does, whoever
#     wrote it — and the tree it was taken under;
#   * cc_fingerprint_prime hashes MANY rows in ONE hasher process: each
#     payload is handed to it as a process substitution, so it is a pipe and
#     never a file (a payload carries .env VALUES, and a value never lands on
#     disk outside .env). One fork per payload, no exec — where the old path
#     cost a `$(…)`, a pipeline, a `sha256sum` and a `cut` per row.
#
# FP_CACHE / TREE_CACHE are not CC_-prefixed: results, not answers.
declare -A FP_CACHE=()
declare -A TREE_CACHE=()
FP_CACHE_TAG=""
FP_OUT=""
FP_PAYLOAD=""

# Drop every per-run cache this file keeps (the fingerprints, the tree
# hashes, the probe verdicts). The digests also invalidate themselves when
# .env changes; a phase that RAN can change what a probe reads or (in
# principle) a tree, so setup.sh calls this after every phase function returns.
cc_ledger_cache_drop() {
  FP_CACHE=(); TREE_CACHE=(); FP_CACHE_TAG=""; PROBE_MEMO=()
}

# The cache is about ONE answer file at ONE content generation, under ONE tree
# root; anything else empties it. Returns 1 when the file is not there (the
# caller then computes without caching, as cc_get_kv would read it: empty).
cc__fp_cache_sync() { # cc__fp_cache_sync <env-file>
  cc__kv_cache_sync "$1" || { FP_CACHE=(); FP_CACHE_TAG=""; return 1; }
  local tag="$1|$KVCACHE_GEN|${LEDGER_TREE_ROOT:-}"
  [[ "$FP_CACHE_TAG" == "$tag" ]] || { FP_CACHE=(); FP_CACHE_TAG="$tag"; }
  return 0
}

# The bytes the digest is taken over, into FP_PAYLOAD: "KEY=VALUE\n" per key in
# manifest order, a tree input's content hash where its value would be. The
# CALLER syncs the answer-file cache first (cc__fp_cache_sync — one read of .env
# per row, or per batch, rather than one per key); <synced> says whether that
# found the file. Unsynced, or a key the cache does not hold by construction
# (not an identifier), reads through cc_get_kv itself.
cc__fingerprint_payload() { # cc__fingerprint_payload <env-file> <reads-csv> <synced:0|1>
  local f="$1" csv="$2" synced="${3:-0}" key root dir
  FP_PAYLOAD=""
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
      if [[ -z "$root" ]]; then
        # `dirname`'s answer, without its exec.
        case "$f" in */*) dir="${f%/*}"; dir="${dir:-/}" ;; *) dir="." ;; esac
        root="${TREE_CACHE[root:$dir]:-}"
        if [[ -z "$root" ]]; then
          root="$(cd "$dir" 2>/dev/null && pwd)"
          [[ -n "$root" ]] && TREE_CACHE["root:$dir"]="$root"
        fi
      fi
      cc__tree_hash_into "$root" "${key#@}"
      FP_PAYLOAD="${FP_PAYLOAD}${key}=${TREE_OUT}"$'\n'
      continue
    fi
    if (( synced )) && [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      FP_PAYLOAD="${FP_PAYLOAD}${key}=${KVCACHE[$key]:-}"$'\n'
    else
      FP_PAYLOAD="${FP_PAYLOAD}${key}=$(cc_get_kv "$f" "$key")"$'\n'
    fi
  done
}

cc__fingerprint_into() { # cc__fingerprint_into <env-file> <reads-csv>  -> FP_OUT
  local f="$1" csv="$2" cached=0
  [[ -z "$csv" || "$csv" == "-" ]] && { FP_OUT='none'; return 0; }
  if cc__fp_cache_sync "$f"; then
    cached=1
    [[ -n "${FP_CACHE[$csv]+x}" ]] && { FP_OUT="${FP_CACHE[$csv]}"; return 0; }
  fi
  cc__fingerprint_payload "$f" "$csv" "$cached"
  FP_OUT="$(printf '%s' "$FP_PAYLOAD" | cc_sha256_stdin)"
  (( cached )) && FP_CACHE["$csv"]="$FP_OUT"
  return 0
}

# Fill FP_CACHE for every given `reads` list not already in it, in ONE hasher
# process. Same three-way fallback as cc_sha256_stdin, read back per input in
# argument order; any surprise (an unparseable line, a count that does not
# match) caches nothing, and cc__fingerprint_into then hashes each row alone —
# slower, never different.
cc_fingerprint_prime() { # cc_fingerprint_prime <env-file> <reads-csv>...
  local f="$1"; shift
  cc__fp_cache_sync "$f" || return 0
  local -A seen=()
  local csvs=() payloads=() csv
  for csv in "$@"; do
    [[ -z "$csv" || "$csv" == "-" ]] && continue
    [[ -n "${FP_CACHE[$csv]+x}" || -n "${seen[$csv]+x}" ]] && continue
    seen["$csv"]=1
    cc__fingerprint_payload "$f" "$csv" 1
    csvs+=("$csv"); payloads+=("$FP_PAYLOAD")
  done
  (( ${#csvs[@]} )) || return 0
  if (( ${#csvs[@]} == 1 )); then
    cc__fingerprint_into "$f" "${csvs[0]}"
    return 0
  fi
  local tool=() out line i=0 h
  if command -v sha256sum >/dev/null 2>&1; then tool=(sha256sum)
  elif command -v shasum >/dev/null 2>&1; then tool=(shasum -a 256)
  elif command -v openssl >/dev/null 2>&1; then tool=(openssl dgst -sha256)
  else
    for csv in "${csvs[@]}"; do FP_CACHE["$csv"]=nohash; done
    return 0
  fi
  # One process substitution per payload, built as words for `eval` — the
  # payloads themselves are never part of the evaluated text, only their
  # array indices are.
  local cmd='"${tool[@]}"'
  for i in "${!payloads[@]}"; do cmd+=" <(printf '%s' \"\${payloads[$i]}\")"; done
  out="$(eval "$cmd" 2>/dev/null)" || return 0
  local digests=()
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    case "${tool[0]}" in
      openssl) h="${line##*= }" ;;
      *)       h="${line%% *}" ;;
    esac
    [[ "$h" =~ ^[0-9a-f]{64}$ ]] || return 0
    digests+=("$h")
  done <<<"$out"
  (( ${#digests[@]} == ${#csvs[@]} )) || return 0
  for i in "${!csvs[@]}"; do FP_CACHE["${csvs[$i]}"]="${digests[$i]}"; done
  return 0
}

# Prime every row of one phase (or, with no phase, of the whole manifest).
cc_fingerprint_prime_rows() { # cc_fingerprint_prime_rows <env-file> [<phase>]
  local f="$1" phase="${2:-}" key reads=()
  for key in ${STEPS_ORDER[@]+"${STEPS_ORDER[@]}"}; do
    [[ -z "$phase" || "${key%%/*}" == "$phase" ]] || continue
    cc__step_split "${key%%/*}" "${key#*/}" || continue
    reads+=("$ROWDEF_READS")
  done
  (( ${#reads[@]} )) || return 0
  cc_fingerprint_prime "$f" "${reads[@]}"
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
#
# cc__tree_hash_into is the same digest into TREE_OUT, cached per run in
# TREE_CACHE (keyed root and directory; cc_ledger_cache_drop empties it after
# every phase): the plan, the `started` mark and the record each fingerprint the
# same tree, and a release's tree does not change under a run.
TREE_OUT=""
cc__tree_hash_into() { # cc__tree_hash_into <root> <repo-relative dir>  -> TREE_OUT
  local k="tree:$1|${2%/}"
  if [[ -n "${TREE_CACHE[$k]+x}" ]]; then TREE_OUT="${TREE_CACHE[$k]}"; return 0; fi
  TREE_OUT="$(cc_tree_hash "$1" "$2")"
  TREE_CACHE["$k"]="$TREE_OUT"
}

# The payload, as a FUNCTION run inside the $(...) below — never as text
# written inside it: Git for Windows' bash (5.3, MSYS) re-reads a command
# substitution's text and drops the carriage return a `$'\r'` there produces
# (`$( x=$'\r'; echo ${#x} )` prints 0), so the CR strip below did nothing on
# Windows and a CRLF-only difference changed the digest (the 2026-10-02
# testbed run's second pass, measured; tests/test_single_cockpit_inputs.py's
# CRLF case). Same subshell, same output, no extra fork.
cc__tree_payload() { # cc__tree_payload <root> <repo-relative dir>  — run in a subshell
  local rel="$2" p line
  cd "$1" || return 1
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
}

cc_tree_hash() { # cc_tree_hash <root> <repo-relative dir>
  local root="$1" rel="${2%/}" payload
  [[ -d "$root/$rel" ]] || { printf 'absent'; return 0; }
  payload="$(cc__tree_payload "$root" "$rel")" || { printf 'unreadable'; return 0; }
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
  cc__phase_steps "$phase"
  (( ${#PHASE_STEPS[@]} )) && cc_fingerprint_prime_rows "$envf" "$phase"
  for step in ${PHASE_STEPS[@]+"${PHASE_STEPS[@]}"}; do
    [[ -n "$step" ]] || continue
    cc__step_split "$phase" "$step" || continue
    cc__fingerprint_into "$envf" "$ROWDEF_READS"
    args+=("$phase/$step" started "$ver" "$at" "$FP_OUT" "")
  done
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

# The LAST row for <step> (a hand edit can leave two), read straight from the
# file — cc_ledger_read's lines, without its process substitution, and the
# column split by cc__tsv_split (`cut -f`'s answer, empty fields kept) rather
# than a `cut` per call.
cc__ledger_row() { # cc__ledger_row <ledger> <step>  -> LEDGER_ROW_OUT
  local line
  LEDGER_ROW_OUT=""
  [[ -f "$1" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == '#'* ]] && continue
    [[ "$line" == "$2"$'\t'* ]] && LEDGER_ROW_OUT="$line"
  done <"$1"
  return 0
}

cc_ledger_field() { # cc_ledger_field <ledger> <step> <column-number>
  cc__ledger_row "$1" "$2"
  [[ -n "$LEDGER_ROW_OUT" ]] || return 0
  cc__tsv_split "$LEDGER_ROW_OUT"
  printf '%s' "${TSV_F[$(( $3 - 1 ))]:-}"
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
#
# One pass over the ledger, no fork: the status of every row into a local map
# (the LAST row per step, and only lines carrying a tab — exactly what
# cc_ledger_status's `<step><tab>` match reads), then the requires walked in
# manifest order.
cc_ledger_blocked() { # cc_ledger_blocked <ledger> <phase>
  cc__ledger_blocked_into "$1" "$2" || return 1
  printf '%s' "$LEDGER_BLOCKED"
}

# The same answer into LEDGER_BLOCKED ("<step> <status>"; empty and status 1
# when nothing blocks) — the driver asks it twice per phase command, and the
# `$(…)` around each was a fork.
LEDGER_BLOCKED=""
cc__ledger_blocked_into() { # cc__ledger_blocked_into <ledger> <phase>  -> LEDGER_BLOCKED
  local ledger="$1" phase="$2" step req r st line
  local -A stat=()
  LEDGER_BLOCKED=""
  if [[ -f "$ledger" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%$'\r'}"
      [[ -z "$line" || "$line" == '#'* || "$line" != *$'\t'* ]] && continue
      cc__tsv_split "$line"
      [[ -n "${TSV_F[0]}" ]] && stat["${TSV_F[0]}"]="${TSV_F[1]:-}"
    done <"$ledger"
  fi
  cc__phase_steps "$phase"
  for step in ${PHASE_STEPS[@]+"${PHASE_STEPS[@]}"}; do
    cc__step_split "$phase" "$step" || continue
    req="${ROWDEF_REQUIRES//,/ }"
    for r in $req; do
      [[ "${r%%/*}" == "$phase" ]] && continue
      st="${stat[$r]:-}"
      [[ "$st" == done ]] && continue
      LEDGER_BLOCKED="$r ${st:-pending}"
      return 0
    done
  done
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
  cc__fingerprint_into "$envf" "$reads"
  if [[ "$rfp" != "$FP_OUT" ]]; then ROW_CODE=inputs; return 0; fi
  if [[ "$probe" == "-" || -z "$probe" ]]; then ROW_CODE=unprobed; return 0; fi
  # A row that reads nothing took no fingerprint, so nothing synced the cache
  # the probe memo is keyed on; its probe may read .env all the same.
  [[ -z "$reads" || "$reads" == "-" ]] && { cc__kv_cache_sync "$envf" || true; }
  if ! cc_probe_memo "$probe" "$envf"; then ROW_CODE=drift; ROW_DETAIL="$probe"; return 0; fi
  ROW_VERDICT=skip; ROW_CODE=done; ROW_DETAIL="$at"
  return 0
}

# A probe's verdict, asked at most ONCE per run while nothing it could read has
# changed (P5). The plan probes every row of a phase the ledger would skip, and
# the full run's skip (phase_is_done) asks the same probes again a few seconds
# later, after `check`; on Git Bash one probe is several forks, and an
# all-done install walked six phases in ~60 s that way. A verdict is kept
# while .env reads the same (KVCACHE_GEN) and until
# setup.sh drops the caches after a phase
# function returns (cc_ledger_cache_drop) — the one exception it makes is
# `check`, the dry gate, which changes nothing a probe reads but .env, and .env
# is watched. A probe that READS THE NETWORK (an API's /health) is memoized
# too: the plan was already a prediction, and the loop's verdict is now the
# same one, taken seconds earlier.
#
# stdin from /dev/null: a caller may be READING a list from its stdin, and a
# probe that read stdin would swallow the rest of it.
declare -A PROBE_MEMO=()
#
# THE CALLER has synced env-lib.sh's answer-file cache (cc__kv_cache_sync) for
# <env-file> since the last thing that could have written it, so KVCACHE_GEN is
# current: cc_row_decide does, by fingerprinting the row first (and with an
# explicit sync for a row that reads nothing). Not synced again here — that is
# one more whole read of .env per probe.
cc_probe_memo() { # cc_probe_memo <probe> <env-file>  -> the probe's status
  local k="$1|$2|$KVCACHE_GEN" rc=0
  if [[ -n "${PROBE_MEMO[$k]+x}" ]]; then return "${PROBE_MEMO[$k]}"; fi
  "$1" </dev/null >/dev/null 2>&1 || rc=$?
  (( rc == 0 )) || rc=1
  PROBE_MEMO["$k"]="$rc"
  return "$rc"
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
  cc__phase_steps "$phase"
  # Every row's fingerprint in one hasher process, but only once a row is
  # going to need one — a phase with no `done` row (a fresh install) hashes
  # nothing at all, as before.
  for step in ${PHASE_STEPS[@]+"${PHASE_STEPS[@]}"}; do
    if [[ "${LEDGER_ROWS[$phase/$step]:-}" == "$phase/$step"$'\t'done$'\t'"$ver"$'\t'* ]]; then
      cc_fingerprint_prime_rows "$envf" "$phase"
      break
    fi
  done
  for step in ${PHASE_STEPS[@]+"${PHASE_STEPS[@]}"}; do
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
  done
  if (( PHASE_NROWS == 0 )); then PHASE_VERDICT=run; PHASE_CODE=norows; fi
  [[ "$PHASE_VERDICT" == run ]] && PHASE_DONE_LINES=""
  return 0
}

# A `reads` column in words, for the plan: .env key NAMES as they are, and a
# tree input `@skills` as "the files under skills/" — which is what changed
# when a release (or a hand edit the tree-pristine row will refuse) touched it.
cc__reads_words() { # cc__reads_words <reads-csv>
  cc__reads_words_into "$1"
  printf '%s' "$READS_WORDS"
}

READS_WORDS=""
cc__reads_words_into() { # cc__reads_words_into <reads-csv>  -> READS_WORDS
  local out="" r
  local oldifs="$IFS"; IFS=','
  for r in $1; do
    IFS="$oldifs"
    [[ -n "$r" ]] || continue
    [[ "$r" == @* ]] && r="the files under ${r#@}/"
    out="${out:+$out, }$r"
  done
  IFS="$oldifs"
  READS_WORDS="$out"
}

# One plan sentence for the phase cc_phase_decide just judged, WITHOUT the
# `PLAN ` prefix (the caller owns the output protocol). Key NAMES only: an
# `inputs` line names the row's `reads` keys and says "one of", because a
# fingerprint is a digest over all of them and cannot say which one moved.
cc_phase_plan_text() { # cc_phase_plan_text <phase> <version>
  cc__phase_plan_text_into "$1" "$2"
  printf '%s' "$PLAN_TEXT"
}

# The same sentence into PLAN_TEXT, built with `printf -v` — the plan prints
# one per phase, and a `$(cc_phase_plan_text …)` per phase was a fork per phase.
PLAN_TEXT=""
cc__phase_plan_text_into() { # cc__phase_plan_text_into <phase> <version>  -> PLAN_TEXT
  local phase="$1" ver="$2" more="" what="" plural="s" body=""
  PLAN_TEXT=""
  if [[ "$PHASE_VERDICT" == skip ]]; then
    if (( PHASE_NROWS == 1 )); then what="its 1 row is"; else what="all $PHASE_NROWS rows are"; fi
    printf -v PLAN_TEXT '%s: WILL SKIP — %s done at %s with the same inputs, and every effect still reads present (last done %s)' \
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
    (( PHASE_SAME == 1 )) && plural=""
    more=" (and $PHASE_SAME more row$plural $what)"
  fi
  case "$PHASE_CODE" in
    pending) printf -v body 'never run: %s is pending%s' "$PHASE_STEP" "$more" ;;
    failed)  printf -v body 'failed last time: %s — "%s"%s' "$PHASE_STEP" "${PHASE_DETAIL:-no reason was recorded}" "$more" ;;
    started) printf -v body 'the last run was interrupted here: %s was started at %s and never finished%s' "$PHASE_STEP" "$PHASE_DETAIL" "$more" ;;
    gate)    printf -v body 'waiting on you: %s%s%s' "$PHASE_STEP" "${PHASE_DETAIL:+ — \"$PHASE_DETAIL\"}" "$more" ;;
    version) printf -v body 'version changed, %s: %s%s' "$PHASE_DETAIL" "$PHASE_STEP" "$more" ;;
    inputs)  cc__reads_words_into "$PHASE_READS"
             printf -v body 'inputs changed: %s reads one of %s%s' "$PHASE_STEP" "$READS_WORDS" "$more" ;;
    drift)   printf -v body 'effect absent: %s is recorded done but its probe %s reads false now (drift)' "$PHASE_STEP" "$PHASE_PROBE" ;;
    norows)  printf -v body 'the manifest declares no rows for it, so there is nothing to skip on' ;;
    *)       printf -v body '%s: %s' "$PHASE_CODE" "$PHASE_STEP" ;;
  esac
  printf -v PLAN_TEXT '%s: WILL RUN — %s' "$phase" "$body"
  return 0
}
