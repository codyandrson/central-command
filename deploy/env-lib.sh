#!/usr/bin/env bash
# ============================================================================
# env-lib.sh — the ONE answer file, and the ONE place generated files live.
#
#   Design record: docs/superpowers/specs/2026-09-23-airgap-check-configure-setup-design.md
#   (D1 "one operator-edited file: the repo-root .env" and D7 "generated files
#   live outside the checkout"). SOURCE this file; it defines functions and
#   sets nothing global but its own guard.
#
#   Why it exists: the operator's stated failure mode was DRIFT between files
#   holding the same fact — deploy/single/.env, web/.env, deploy/discovery.conf
#   and the root .env each carried a slice, and finding where a value was
#   defined meant chasing four files. There is now one: `$REPO_ROOT/.env`.
#   Everything a command GENERATES (logs, diagnostics, the installed manifest,
#   pid files, discovery evidence, the Windows logon wrapper) lives in the
#   STATE DIRECTORY, outside the checkout, so `git status` is clean after every
#   command and an update never merges around a log file.
#
#   Consumed by: deploy/single/{setup,update,resolve-images,verify,make-secrets,
#   discover-llm,build-*}.sh and deploy/discover.sh. The k3s profile is NOT in
#   scope for this record and keeps its own deploy/pi/.env and web/.env.
#
#   Functions (all cc_-prefixed so a caller's own helpers never collide):
#     cc_get_kv <file> <key>              read one dotenv value, no sourcing
#     cc_set_kv <file> <key> <value>      write one, in place, mode preserved
#     cc_is_placeholder <value>           empty or a *CHANGEME* marker
#     cc_set_kv_if_unset <file> <k> <v>   write only over a placeholder
#     cc_env_unquoted_keys <file>         KEY NAMES whose value would RUN
#     cc_norm_path <abs-path>             the one spelling bash AND python agree
#     cc_state_dir <env-file> <repo-root> resolve/create/persist the state dir
#     cc_migrate_legacy_env <env-file> <repo-root> <state-dir>
#                                         fold the retired files into .env
# ============================================================================

[[ -n "${CC_ENV_LIB_LOADED:-}" ]] && return 0
CC_ENV_LIB_LOADED=1

# Read one key's value out of a dotenv file WITHOUT sourcing it. Last
# assignment wins, matching dotenv readers.
cc_get_kv() { # cc_get_kv <file> <key>
  local f="$1" k="$2" line out=""
  [[ -f "$f" ]] || { printf ''; return 0; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"   # a CRLF file (Windows checkout) must not turn "" into "\r"
    [[ "$line" == "$k="* ]] && out="${line#*=}"
  done <"$f"
  printf '%s' "$out"
}

# Set one key, in place, preserving the file's mode and every comment.
# printf/read are BUILTINS, so unlike `sed -i s|..|VALUE|` the value never
# appears in an argv and never shows up in `ps`.
cc_set_kv() { # cc_set_kv <file> <key> <value>
  local f="$1" k="$2" v="$3" tmp line found=0
  tmp="$(mktemp)" || return 1
  chmod 600 "$tmp" 2>/dev/null
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
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

# A value is "unset" for our purposes if it is empty or still a template
# placeholder. CHANGEME is .env.example's marker.
cc_is_placeholder() { [[ -z "$1" || "$1" == *CHANGEME* ]]; }

# Write only when the current value is a placeholder — the operator's own
# edits are never overwritten. Returns 0 when it wrote, 1 when it left the
# existing value alone, 2 when the incoming value was itself empty.
cc_set_kv_if_unset() { # cc_set_kv_if_unset <file> <key> <value>
  local cur; cur="$(cc_get_kv "$1" "$2")"
  cc_is_placeholder "$cur" || return 1
  [[ -n "$3" ]] || return 2
  cc_set_kv "$1" "$2" "$3"
}

# ── the answer file is SOURCED, so it has to be sourceable ──────────────────
# `set -a; . .env` is how every deploy script reads it, and an unquoted value
# containing a space is a COMMAND: `CC_FEED_QUERY=in:inbox newer_than:1d` runs
# `newer_than:1d` and leaves the variable unset. Harmless-looking under
# `set -uo pipefail` (setup.sh), fatal under `set -euo pipefail`
# (make-secrets.sh, verify.sh, the build scripts) — so it is checked ONCE,
# before the first source, and named.
#
# Prints the offending KEY NAMES (never values), space-separated; empty when
# the file is clean.
cc_env_unquoted_keys() { # cc_env_unquoted_keys <file>
  local f="$1" line k v out=""
  [[ -f "$f" ]] || { printf ''; return 0; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
    k="${line%%=*}"; v="${line#*=}"
    case "$v" in
      '"'*|"'"*) continue ;;                    # already quoted
      *[[:space:]]*|*'`'*|*'$('*) out="${out:+$out }$k" ;;
    esac
  done <"$f"
  printf '%s' "$out"
}

# ── the state directory (D7) ────────────────────────────────────────────────
# bash-on-MSYS and Python disagree on how to SPELL a Windows path (/c/Users/x
# vs C:\Users\x), so a hash of the path would differ between the two sides of
# the same install. `cygpath -m` produces the C:/Users/x form, which is what
# Python's Path.resolve().as_posix() produces and which BOTH bash and Python
# accept as a path. On Linux this is the identity.
cc_norm_path() { # cc_norm_path <path>
  local p="$1" out
  if command -v cygpath >/dev/null 2>&1; then
    out="$(cygpath -m "$p" 2>/dev/null)" && [[ -n "$out" ]] && { printf '%s' "$out"; return 0; }
  fi
  printf '%s' "$p"
}

# <basename>-<first 8 hex of sha256 of the normalized absolute path>. Mirrored
# EXACTLY in central_command/config.py (Settings.resolved_state_dir) — a second
# checkout of the same release on one machine must not share a state dir, and
# the two sides must agree on which one it is.
cc_install_id() { # cc_install_id <normalized-repo-root>
  local norm="$1" h=""
  if command -v sha256sum >/dev/null 2>&1; then
    h="$(printf '%s' "$norm" | sha256sum 2>/dev/null | cut -c1-8)"
  elif command -v shasum >/dev/null 2>&1; then
    h="$(printf '%s' "$norm" | shasum -a 256 2>/dev/null | cut -c1-8)"
  elif command -v openssl >/dev/null 2>&1; then
    h="$(printf '%s' "$norm" | openssl dgst -sha256 2>/dev/null | sed 's/.*= *//' | cut -c1-8)"
  fi
  printf '%s-%s' "$(basename "$norm")" "${h:-nohash}"
}

# Echo the state dir, creating it 0700 (best effort — chmod is a silent no-op
# on NTFS). The bash side RESOLVES and PERSISTS the value into .env on first
# run: that is the one .env write a read-only command is allowed to make, and
# it is what keeps the Python side from having to guess a path spelling.
cc_state_dir() { # cc_state_dir <env-file> <repo-root>
  local envf="$1" root="$2" sd base
  sd="${CC_STATE_DIR:-}"
  [[ -n "$sd" ]] || sd="$(cc_get_kv "$envf" CC_STATE_DIR)"
  if [[ -z "$sd" ]]; then
    base="${XDG_STATE_HOME:-$HOME/.local/state}"
    sd="$(cc_norm_path "$base")/central-command/$(cc_install_id "$(cc_norm_path "$root")")"
    mkdir -p "$sd" || return 1
    chmod 700 "$sd" 2>/dev/null || true
    [[ -f "$envf" ]] && cc_set_kv "$envf" CC_STATE_DIR "$sd"
  else
    mkdir -p "$sd" || return 1
    chmod 700 "$sd" 2>/dev/null || true
  fi
  printf '%s' "$sd"
}

# ── migration off the retired files (D1, the release path) ──────────────────
# An existing install has its answers in deploy/single/.env, its cockpit
# settings in web/.env and its probe answers in deploy/discovery.conf. Each is
# folded into the root .env — under the CC_ name where the same fact already
# had one — and then MOVED aside (never deleted, never printed). Idempotent:
# once the old file is gone this is a no-op, which is why it can run at the
# top of every phase.
#
# The CC_-prefixed app name wins for a duplicate fact, because the app cannot
# be asked to rename its own configuration:
cc__rename() { # cc__rename <legacy-key> -> the key it lands under
  case "$1" in
    LITELLM_MASTER_KEY) printf 'CC_LLM_PROXY_ADMIN_KEY' ;;
    LITELLM_SALT_KEY)   printf 'CC_LITELLM_SALT_KEY' ;;
    NEO4J_PASSWORD)     printf 'CC_NEO4J_PASSWORD' ;;
    *)                  printf '%s' "$1" ;;
  esac
}

# deploy/discovery.conf's DISCO_* keys map one-to-one onto seams that already
# exist under CC_ names (the design record's D1). A mirror with no CC seam is
# kept verbatim as CC_DISCO_MIRROR_<KEY> so nothing an operator measured is
# silently dropped.
cc__disco_rename() { # cc__disco_rename <DISCO_ key> -> the key it lands under
  case "$1" in
    DISCO_CA_BUNDLE)             printf 'CC_CA_BUNDLE' ;;
    DISCO_PROXY)                 printf 'CC_PROXY' ;;
    DISCO_NETRC)                 printf 'CC_NETRC' ;;
    DISCO_INSECURE)              printf 'CC_TLS_INSECURE' ;;
    DISCO_MIRROR_PYPI)           printf 'CC_PYPI_INDEX_URL' ;;
    DISCO_MIRROR_NPM)            printf 'CC_NPM_REGISTRY' ;;
    DISCO_MIRROR_DOCKERIO)       printf 'CC_REGISTRY_DOCKERIO' ;;
    DISCO_MIRROR_GHCR)           printf 'CC_REGISTRY_GHCR' ;;
    DISCO_MIRROR_MCR)            printf 'CC_REGISTRY_MCR' ;;
    DISCO_MIRROR_DEB_DEBIAN)     printf 'CC_APT_MIRROR' ;;
    DISCO_MIRROR_DEB_SECURITY)   printf 'CC_APT_SECURITY_MIRROR' ;;
    DISCO_MIRROR_HUGGINGFACE)    printf 'CC_HF_ENDPOINT' ;;
    DISCO_MIRROR_*)              printf 'CC_%s' "$1" ;;
    *)                           printf '' ;;
  esac
}

# A CC_REGISTRY_* seam is a HOST PREFIX, never a URL — a discovery mirror is
# a URL, so it is reduced here rather than written in a shape podman cannot use.
cc__host_only() { local h="${1#*://}"; printf '%s' "${h%%/*}"; }

# Fold one legacy dotenv file's keys into the root .env, then move it aside.
# Prints, on stdout, a comma-separated list of the keys it wrote (NAMES only —
# never values). Returns 1 only when a move or a write actually failed.
cc__absorb() { # cc__absorb <legacy-file> <env-file> <archive-path> <renamer>
  local src="$1" envf="$2" arch="$3" renamer="$4" line k v target wrote=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
    k="${line%%=*}"; v="${line#*=}"
    target="$("$renamer" "$k")"
    [[ -n "$target" ]] || continue
    # A quoted value in the legacy file keeps its quotes: the root .env is
    # sourced the same way, so the shape that worked there works here.
    case "$target" in
      CC_REGISTRY_*) v="$(cc__host_only "$v")" ;;
    esac
    [[ -n "$v" ]] || continue
    if cc_set_kv_if_unset "$envf" "$target" "$v"; then
      wrote="${wrote:+$wrote, }$target"
    fi
  done <"$src"
  mkdir -p "$(dirname "$arch")" || return 1
  mv -f "$src" "$arch" || return 1
  chmod 600 "$arch" 2>/dev/null || true
  printf '%s' "$wrote"
}

# The whole migration. Prints ONE summary line's worth of text on stdout when
# it did something (the caller wraps it in its own `pass`), nothing when there
# was nothing to do.
cc_migrate_legacy_env() { # cc_migrate_legacy_env <env-file> <repo-root> <state-dir>
  local envf="$1" root="$2" state="$3" msg="" wrote
  local arch="$state/migrated"

  local old="$root/deploy/single/.env"
  if [[ -f "$old" ]]; then
    wrote="$(cc__absorb "$old" "$envf" "$arch/deploy-single.env" cc__rename)" || return 1
    msg="${msg:+$msg; }deploy/single/.env -> $arch/deploy-single.env (merged: ${wrote:-nothing new})"
  fi

  # web/.env is retired on THIS profile only, and only when it holds nothing
  # but the three lines this profile ever wrote — a shared checkout whose k3s
  # cockpit owns that file must not have it moved out from under cc-nerve.
  local web="$root/web/.env"
  if [[ -f "$web" ]]; then
    local foreign=0 line k
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%$'\r'}"
      [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
      k="${line%%=*}"
      case "$k" in PORT|GATEWAY_URL|CC_UPDATE_BACKEND) ;; *) foreign=1 ;; esac
    done <"$web"
    if (( foreign )); then
      msg="${msg:+$msg; }web/.env kept (it carries keys this profile never wrote — another profile's cockpit owns it)"
    else
      local wport; wport="$(cc_get_kv "$web" PORT)"
      [[ -n "$wport" ]] && cc_set_kv_if_unset "$envf" CC_COCKPIT_PORT "$wport"
      mkdir -p "$arch" && mv -f "$web" "$arch/web.env" || return 1
      chmod 600 "$arch/web.env" 2>/dev/null || true
      msg="${msg:+$msg; }web/.env -> $arch/web.env (the cockpit launcher exports PORT/GATEWAY_URL/CC_UPDATE_BACKEND now)"
    fi
  fi

  local conf="$root/deploy/discovery.conf"
  if [[ -f "$conf" ]]; then
    wrote="$(cc__absorb "$conf" "$envf" "$arch/discovery.conf" cc__disco_rename)" || return 1
    msg="${msg:+$msg; }deploy/discovery.conf -> $arch/discovery.conf (merged: ${wrote:-nothing new})"
  fi

  # The prober's OUTPUT tree is regenerable, but setup's discovery-crosscheck
  # reads it, so carry it over rather than silently losing the evidence.
  local dout="$root/deploy/discovery.out"
  if [[ -d "$dout" && ! -e "$state/discovery" ]]; then
    mkdir -p "$state" && mv -f "$dout" "$state/discovery" || return 1
    msg="${msg:+$msg; }deploy/discovery.out/ -> $state/discovery/"
  fi

  printf '%s' "$msg"
}
