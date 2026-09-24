#!/usr/bin/env bash
# ============================================================================
# questions-lib.sh — the validators and the reader for questions.tsv.
#
#   Design record: docs/superpowers/specs/2026-09-23-airgap-check-configure-setup-design.md
#   (D6 "configure: one question schema, plain prompts, fail-closed"). SOURCE
#   this file; it defines functions and sets nothing global but its own guard.
#
#   Why it is separate from setup.sh: the schema drives TWO commands —
#   `configure` ASKS and `check` VALIDATES — and a validator that lived inside
#   one of them would be copied into the other. It is also the reason the
#   schema is a data FILE: adding a dependency is then a row, not a prompt in
#   one place and a check in another (debconf-preseed / Zarf's
#   InteractiveVariable, the two patterns the research pass said to copy).
#
#   Every v_* validator here obeys three rules:
#     * it prints ONE line of REASON on failure, on stdout, and nothing on
#       success — the caller decides whether that becomes a FAIL line or a
#       re-ask;
#     * it WRITES NOTHING and creates nothing. `check` reaches these, and
#       check executes nothing (tests/test_single_check_is_dry.py);
#     * it is called only for a NON-EMPTY value. Blank is a valid answer for a
#       non-required key, and "required" is the schema's column, not a
#       validator's business.
#
#   Functions:
#     v_nonempty v_bool01 v_url v_host v_path_readable
#     v_path_dir_or_creatable v_port v_model_id   the validators
#     q_rows <file>                 the schema's rows, one per line, tabs kept
#     q_field <row> <n>             one column out of a row
#     q_groups <file>               the group names, in first-appearance order
#     q_group_blurb <group>         the one-line explanation a header prints
#     q_when_holds <expr> <getter>  the tiny `when` expression language
# ============================================================================

[[ -n "${CC_QUESTIONS_LIB_LOADED:-}" ]] && return 0
CC_QUESTIONS_LIB_LOADED=1

# ── validators ──────────────────────────────────────────────────────────────

v_nonempty() { # v_nonempty <value>
  [[ -n "$1" ]] && return 0
  printf 'a value is required here\n'; return 1
}

v_bool01() { # v_bool01 <value>
  [[ "$1" == 0 || "$1" == 1 ]] && return 0
  printf 'must be exactly 0 or 1 (got %s) — "true"/"yes" read as FALSE everywhere this value is used\n' "$1"
  return 1
}

# A full URL: scheme, ://, and a host. file:// is deliberately admitted — it is
# a documented answer for CC_PYTHON_MIRROR (a directory of CPython archives).
v_url() { # v_url <value>
  [[ "$1" =~ ^[A-Za-z][A-Za-z0-9+.-]*://[^[:space:]/]+ ]] && return 0
  [[ "$1" =~ ^file://. ]] && return 0
  printf 'must be a full URL with a scheme, like https://mirror.corp.example/path (got %s)\n' "$1"
  return 1
}

# host[:port], no scheme and no path — the shape podman's registries want, and
# the one an operator most often gets wrong by pasting a browser URL.
v_host() { # v_host <value>
  if [[ "$1" == *://* ]]; then
    printf 'this is a HOST prefix, not a URL — drop the %s:// (podman takes registry.corp.example, optionally with :port)\n' "${1%%://*}"
    return 1
  fi
  if [[ "$1" == */* ]]; then
    printf 'this is a HOST prefix, not a path — a mirror that re-namespaces image PATHS is a CC_IMG_<NAME> pin instead\n'
    return 1
  fi
  [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?(:[0-9]{1,5})?$ ]] && return 0
  printf 'not a hostname or host:port (got %s)\n' "$1"
  return 1
}

v_path_readable() { # v_path_readable <value>
  [[ -f "$1" && -r "$1" ]] && return 0
  [[ -e "$1" ]] && { printf 'exists but is not a readable file: %s\n' "$1"; return 1; }
  printf 'no such file: %s\n' "$1"
  return 1
}

# An existing directory, or one whose PARENT exists and is writable. It never
# creates anything: the caller may be `check`, which executes nothing.
v_path_dir_or_creatable() { # v_path_dir_or_creatable <value>
  if [[ -d "$1" ]]; then
    [[ -w "$1" ]] && return 0
    printf 'directory exists but is not writable: %s\n' "$1"
    return 1
  fi
  [[ -e "$1" ]] && { printf 'exists and is not a directory: %s\n' "$1"; return 1; }
  local parent; parent="$(dirname "$1")"
  [[ -d "$parent" && -w "$parent" ]] && return 0
  printf 'cannot be created: %s does not exist or is not writable\n' "$parent"
  return 1
}

v_port() { # v_port <value>
  if [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 )); then return 0; fi
  printf 'must be a port number between 1 and 65535 (got %s)\n' "$1"
  return 1
}

# The UPSTREAM model id, as the server names it. Slashes are LEGAL and common
# (speaches-ai/Kokoro-82M-v1.0-ONNX), so only whitespace and a comma are
# rejected: whitespace would break the sourced answer file and a comma is the
# shape of someone answering two aliases at once.
v_model_id() { # v_model_id <value>
  [[ -n "$1" ]] || { printf 'a model id is required here\n'; return 1; }
  if [[ "$1" =~ [[:space:]] ]]; then
    printf 'a model id cannot contain a space — the answer file is SOURCED, so the value would be run as a command\n'
    return 1
  fi
  if [[ "$1" == *,* ]]; then
    printf 'one model id per key (got a comma — each alias has its own CC_LLM_UPSTREAM_MODEL_* key)\n'
    return 1
  fi
  return 0
}

# ── writing an answer into a file that gets SOURCED ─────────────────────────
# `set -a; . .env` is how every command in this profile reads the answer file,
# so an unquoted value containing a space is a COMMAND: `CC_OPERATOR_NAME=Jane
# Doe` runs `Doe` and leaves the variable unset (found while building v2.45.0 —
# the operator's NAME and a Windows CA path under `C:\Program Files` are the two
# answers that hit it). cc_env_unquoted_keys catches it at the next run; these
# two make sure it never gets written in the first place.
#
# Double quotes, not single: python-dotenv (the app's reader) and bash agree on
# them, and a value containing an apostrophe is likelier than one containing a
# dollar sign.
q_quote() { # q_quote <value>
  local v="$1"
  case "$v" in
    *[[:space:]\$\`\"\\\'\#\&\;\|\<\>\(\)\*\?\[\]\{\}]*)
      v="${v//\\/\\\\}"; v="${v//\"/\\\"}"; v="${v//\$/\\\$}"; v="${v//\`/\\\`}"
      printf '"%s"' "$v" ;;
    *) printf '%s' "$v" ;;
  esac
}

# The inverse, for reading a value back out of the file: what the operator
# ANSWERED, not how it is stored, is what a prompt's default and a validator see.
q_unquote() { # q_unquote <value>
  local v="$1"
  case "$v" in
    '"'*'"')
      v="${v:1:${#v}-2}"
      v="${v//\\\"/\"}"; v="${v//\\\$/\$}"; v="${v//\\\`/\`}"; v="${v//\\\\/\\}" ;;
    "'"*"'") v="${v:1:${#v}-2}" ;;
  esac
  printf '%s' "$v"
}

# ── the schema ──────────────────────────────────────────────────────────────

# Every data row of the schema, comments and blank lines dropped, tabs intact.
q_rows() { # q_rows <questions.tsv>
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "$line" == '#'* ]] && continue
    printf '%s\n' "$line"
  done <"$1"
}

# One column (1-based) out of one row. cut, not an array split: a prompt
# contains spaces and IFS-splitting on a tab would COLLAPSE consecutive tabs,
# which is exactly why the schema writes `-` instead of an empty field.
q_field() { # q_field <row> <n>
  printf '%s' "$1" | cut -d$'\t' -f"$2"
}

q_groups() { # q_groups <questions.tsv>
  local row g out=""
  while IFS= read -r row; do
    g="$(q_field "$row" 2)"
    [[ " $out " == *" $g "* ]] || out="${out:+$out }$g"
  done < <(q_rows "$1")
  printf '%s' "$out"
}

# One sentence per group, printed under its `== <group>` header. Kept here
# rather than in the schema so a group's explanation is one fact even though
# several rows carry the group name.
q_group_blurb() { # q_group_blurb <group>
  case "$1" in
    identity) printf 'how the agents address you' ;;
    features) printf 'which optional components this install brings up' ;;
    network)  printf 'how this host reaches the outside world: the proxy and the two trust knobs' ;;
    mirrors)  printf 'where each dependency comes from — blank everywhere means the public source' ;;
    llm)      printf 'the upstream LLM: one endpoint, one key, one model id per alias' ;;
    paths)    printf 'where this install keeps what it generates' ;;
    advanced) printf 'loopback ports — every default is fine unless something else already holds one' ;;
    *)        printf '' ;;
  esac
}

# The `when` guard: `KEY=value` or `KEY!=value`, comma-separated ANDs. A tiny
# expression language and NOT bash, on purpose — a schema row is data, and
# `eval`ing a data file would make every row a code path. `-` (or empty) always
# holds.
#
# The second argument is the name of a function that prints the CURRENT value
# of a key ("the answers so far"): configure passes its own answer map, check
# passes the answer file. Neither has to be the process environment.
q_when_holds() { # q_when_holds <expr> <value-getter>
  local expr="$1" getter="$2" clause key want cur
  [[ -z "$expr" || "$expr" == "-" ]] && return 0
  local IFS=,
  for clause in $expr; do
    [[ -n "$clause" ]] || continue
    if [[ "$clause" == *"!="* ]]; then
      key="${clause%%!=*}"; want="${clause#*!=}"
      cur="$("$getter" "$key")"
      [[ "$cur" != "$want" ]] || return 1
    elif [[ "$clause" == *"="* ]]; then
      key="${clause%%=*}"; want="${clause#*=}"
      cur="$("$getter" "$key")"
      [[ "$cur" == "$want" ]] || return 1
    else
      # An unparseable clause must never silently mean "ask it anyway": the
      # guard test parses every `when` in the schema, so reaching this is a
      # schema bug, and the safe reading of a broken guard is "does not hold".
      return 1
    fi
  done
  return 0
}
