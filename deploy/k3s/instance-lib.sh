#!/usr/bin/env bash
# ============================================================================
# instance-lib.sh — where THIS install lives, derived from the machine.
#
#   No k3s script names a checkout, an account or a home: each asks this file,
#   from its own location. A NEW file on purpose, never part of env-path.sh:
#   the updater applying a release sources the MERGED env-path.sh after it has
#   stopped the services, and that file promises to set nothing.
#
#   SOURCE this file; it defines functions and sets nothing. Each takes a script
#   path inside deploy/k3s/ (default: the calling script), prints one value, and
#   on failure prints what it could not resolve to stderr and returns non-zero —
#   never a guess. Resolve at the TOP of a script: a relative path means nothing
#   after a `cd`, and a merge may replace this file while the script runs.
#
#     cc_k3s_checkout [path]    the checkout holding that script (physical path)
#     cc_k3s_owner [path]       the account that owns the checkout
#     cc_k3s_group [path]       the checkout's group, never assumed to be the owner's name
#     cc_k3s_home [path]        the owner's home, from the account database
#     cc_k3s_backup_dir [path]  CC_BACKUP_DIR when set, else <owner home>/cc-backups
#
#   The one function that takes values instead of a path, so a read-only check
#   can render for the live install from a worktree:
#
#     cc_k3s_render_unit <template> <checkout> <owner> <group> <home>
#
#   No git here: cc-update.sh and backup.sh run as root on a checkout another
#   user owns, and git's safe.directory check refuses that. No $USER or $HOME
#   either, for the same reason. From a worktree's script the checkout IS that
#   worktree, which is what a build script wants; cc-update.sh resolves REPO from
#   the live script the unit ran, before any worktree exists.
# ============================================================================

cc_k3s_checkout() { # [path]
  local path="${1:-${BASH_SOURCE[1]:-}}"
  if [[ -z "$path" ]]; then
    echo "instance-lib: cannot resolve the checkout: no script path given" >&2
    return 1
  fi
  (CDPATH='' cd -- "$(dirname -- "$path")/../.." 2>/dev/null && pwd -P) || {
    echo "instance-lib: cannot resolve the checkout from $path" >&2
    return 1
  }
}

# GNU stat prints UNKNOWN for an id with no name and still exits 0.
_cc_k3s_stat() { # <format> <role> <path>
  local checkout value
  checkout="$(cc_k3s_checkout "$3")" || return 1
  value="$(stat -c "$1" -- "$checkout" 2>/dev/null)" || value=""
  if [[ -z "$value" || "$value" == UNKNOWN ]]; then
    echo "instance-lib: cannot resolve the $2 of $checkout" >&2
    return 1
  fi
  printf '%s\n' "$value"
}

cc_k3s_owner() { # [path]
  _cc_k3s_stat %U owner "${1:-${BASH_SOURCE[1]:-}}"
}

cc_k3s_group() { # [path]
  _cc_k3s_stat %G group "${1:-${BASH_SOURCE[1]:-}}"
}

cc_k3s_home() { # [path]
  local owner entry home
  owner="$(_cc_k3s_stat %U owner "${1:-${BASH_SOURCE[1]:-}}")" || return 1
  entry="$(getent passwd "$owner" 2>/dev/null)" || entry=""
  home="$(printf '%s\n' "$entry" | cut -d: -f6)"
  if [[ -z "$home" ]]; then
    echo "instance-lib: cannot resolve the home of account $owner" >&2
    return 1
  fi
  printf '%s\n' "$home"
}

cc_k3s_backup_dir() { # [path]
  local home
  if [[ -n "${CC_BACKUP_DIR:-}" ]]; then
    printf '%s\n' "$CC_BACKUP_DIR"
    return 0
  fi
  home="$(cc_k3s_home "${1:-${BASH_SOURCE[1]:-}}")" || return 1
  printf '%s/cc-backups\n' "$home"
}

# The tokens a deploy/k3s/*.in template may hold are these four and no other. A
# release that adds one ships it in the updater a release AHEAD: the update INTO
# release N runs N-1's cc-update.sh, whose bash holds N-1's copy of this function.
# No sed: a value holding `&`, `/` or `\` is literal. Prints nothing on a refusal.
cc_k3s_render_unit() { # <template> <checkout> <owner> <group> <home>
  if (( $# != 5 )) || [[ -z "$2" || -z "$3" || -z "$4" || -z "$5" ]]; then
    echo "instance-lib: render wants a template, a checkout, an owner, a group and a home, none empty" >&2
    return 1
  fi
  local text rest token
  # The sentinel keeps the trailing newlines $(...) would strip.
  text="$(cat -- "$1" 2>/dev/null && printf x)" || {
    echo "instance-lib: cannot read the unit template $1" >&2
    return 1
  }
  text="${text%x}"
  rest="$text"
  while [[ "$rest" =~ @CC_[A-Za-z0-9_]*@ ]]; do
    token="${BASH_REMATCH[0]}"
    case "$token" in
      @CC_CHECKOUT@|@CC_OWNER@|@CC_OWNER_GROUP@|@CC_OWNER_HOME@) ;;
      *) echo "instance-lib: $1 holds the unknown token $token" >&2; return 1 ;;
    esac
    rest="${rest#*"$token"}"
  done
  text="${text//@CC_CHECKOUT@/"$2"}"
  text="${text//@CC_OWNER_GROUP@/"$4"}"
  text="${text//@CC_OWNER_HOME@/"$5"}"
  text="${text//@CC_OWNER@/"$3"}"
  if [[ "$text" == *@CC_* ]]; then
    echo "instance-lib: $1 still holds a token after rendering (a malformed @CC_ token, or a value holding one)" >&2
    return 1
  fi
  printf '%s' "$text"
}
