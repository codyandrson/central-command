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
#     cc_alias_env_key <alias>            the CC_LLM_UPSTREAM_MODEL_* key name
#     cc_required_aliases                 the LiteLLM aliases THESE flags need
#     cc_stage_build_context <dir> <ca> <src>...  a build context outside the tree
#     cc_build_inputs_hash <ca> <args> <src>...   what a local image is built FROM
#     cc_build_arg_values <podman-argv>...        the --build-arg values in an argv
#     cc_build_inputs_label               the image LABEL that carries the hash
#     cc_staged_image_ref <live-ref>      where a STAGED build tags its image
#     cc_sha256_stdin                     sha256 hex of stdin, three-way fallback
#     cc_exit_code <fails> <warns> <actions>      the ONE exit-code rule
#     cc_tree_diff <repo-root>            is this tree still the release it claims
#   The run lock (2026-10-01 design record, D11 — one run at a time):
#     cc_lock_acquire <state-dir> <command-text>  take it, nest under it, or
#                                         reclaim a stale one (RUN_LOCK_* globals)
#     cc_lock_release <state-dir>         drop it — only the process that took it
#     cc_lock_holder <state-dir>          "<pid>\t<command>\t<started>" of the lock
#     cc_lock_pid_is_run <pid>            is that pid alive AND still one of ours
#     cc_lock_trap <state-dir>            release on EXIT/INT/TERM, composed with
#                                         whatever traps the caller already has
#     cc_lock_refusal_text / cc_lock_reclaim_text   the FAIL/WARN sentences
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

# ── the two trust knobs, fanned out (D4) ────────────────────────────────────
# ONE place, called by every command that makes a TLS connection or drives a
# tool that does: deploy/single/{setup,update,verify,make-secrets,resolve-images,
# discover-llm,build-*}.sh and deploy/discover.sh. Per-tool knobs are not
# exposed — per-tool granularity is exactly what drifted before.
#
#   CC_CA_BUNDLE      a PEM the whole toolchain should trust
#   CC_TLS_INSECURE   1 = verification OFF for everything this fan-out reaches
#
# The operator DROPPED the "never disable verification" rule on 2026-09-23:
# the site assumes security through isolation. The trade is stated once, by the
# caller, as a WARN naming the consumers it drives — never a PASS, never
# silent. This function does not print: the consumer LIST differs per command,
# and the output protocol belongs to the caller.
#
# Every name below is the one the tool actually reads (checked 2026-09-23):
# uv reads UV_SYSTEM_CERTS (UV_NATIVE_TLS is DEPRECATED) and UV_INSECURE_HOST;
# pip reads PIP_CERT / PIP_TRUSTED_HOST; npm reads any npm_config_* case-
# insensitively; node reads NODE_EXTRA_CA_CERTS / NODE_TLS_REJECT_UNAUTHORIZED;
# git reads GIT_SSL_CAINFO / GIT_SSL_NO_VERIFY.
#
# podman is NOT here. Its pulls and builds verify inside the podman MACHINE (or
# against the host trust store on bare Linux), which no exported variable
# reaches — that is the `machine` phase's job and `--tls-verify=false`'s.
cc_export_tls_env() { # cc_export_tls_env <state-dir>
  local state="${1:-}"
  # Lines for the .curlrc this install owns (see cc__write_curlrc). Collected
  # here because BOTH knobs can need one at the same time, and the file is
  # rewritten in one go rather than twice.
  local curlrc_extra=()

  if [[ -n "${CC_CA_BUNDLE:-}" ]]; then
    export CURL_CA_BUNDLE="$CC_CA_BUNDLE"      # curl (host) — OpenSSL builds only
    export SSL_CERT_FILE="$CC_CA_BUNDLE"       # openssl, uv, python
    export REQUESTS_CA_BUNDLE="$CC_CA_BUNDLE"  # requests/httpx-based tools
    export PIP_CERT="$CC_CA_BUNDLE"            # pip
    export NODE_EXTRA_CA_CERTS="$CC_CA_BUNDLE" # node
    export NPM_CONFIG_CAFILE="$CC_CA_BUNDLE"   # npm
    export GIT_SSL_CAINFO="$CC_CA_BUNDLE"      # git over https
    # ...and the same CA through the .curlrc, because a SCHANNEL curl — which is
    # what Git for Windows ships, and Windows is this profile's target — ignores
    # CURL_CA_BUNDLE entirely. MEASURED on the 2026-09-24 Windows run against a
    # private CA and curl 8.21.0 (Schannel):
    #   CURL_CA_BUNDLE=<pem>  -> 000, certificate failure
    #   --cacert <pem>        -> 200, and `curl -v` says
    #                            "schannel: added 1 certificate(s) from CA file"
    # So the CA does NOT have to be in the Windows Root store, which is what the
    # note below this function used to claim: it only has to reach curl as an
    # OPTION rather than an environment variable. Without this line every
    # host-side probe in `check` (the indexes, the registry manifest HEADs, the
    # upstream LLM) failed on a private CA on Windows while CC_CA_BUNDLE was set.
    curlrc_extra+=("cacert = $CC_CA_BUNDLE")
  fi

  # The hosts an index knob points at — pip and uv take HOSTS, not URLs.
  local ihosts="" u h
  for u in "${CC_PYPI_INDEX_URL:-}" "${CC_PYTHON_MIRROR:-}"; do
    [[ -n "$u" ]] || continue
    h="$(cc__host_only "$u")"
    [[ -n "$h" ]] || continue
    [[ " $ihosts " == *" $h "* ]] || ihosts="${ihosts:+$ihosts }$h"
  done

  if [[ "${CC_TLS_INSECURE:-0}" == "1" ]]; then
    # curl has no environment variable for -k, so it travels in the same config
    # file as the CA above.
    curlrc_extra+=(insecure)
    # Space-separated: pip documents multi-value environment options that way,
    # and uv's list-valued environment variables follow the same convention
    # (UV_INSECURE_HOST is the documented env form of --allow-insecure-host —
    # verified in `uv pip install --help` on 2026-09-23; the SEPARATOR is uv's
    # documented convention, not something this host could prove).
    if [[ -n "$ihosts" ]]; then
      export PIP_TRUSTED_HOST="$ihosts"
      export UV_INSECURE_HOST="$ihosts"
    fi
    export NPM_CONFIG_STRICT_SSL=false
    export NODE_TLS_REJECT_UNAUTHORIZED=0
    export GIT_SSL_NO_VERIFY=1
  fi

  # ONE write, whatever the knobs said. CURL_HOME is how curl finds a .curlrc
  # that is not in $HOME, and the file is REWRITTEN each run rather than appended
  # to, so a knob turned back off does not leave a stale `insecure` (or a stale
  # `cacert` pointing at a file that has moved) behind.
  if [[ -n "$state" ]] && mkdir -p "$state/curl" 2>/dev/null; then
    cc__write_curlrc "$state/curl/.curlrc" ${curlrc_extra[@]+"${curlrc_extra[@]}"}
    [[ -f "$state/curl/.curlrc" ]] && export CURL_HOME="$state/curl"
  fi
  return 0
}

# The setup-owned .curlrc. Git for Windows' curl is schannel-ONLY, with two
# consequences, and this file is where both are answered:
#   * it IGNORES CURL_CA_BUNDLE. The old note here concluded "the CA must be in
#     the Windows Root store" — MEASURED FALSE on the 2026-09-24 Windows run:
#     `--cacert <pem>` works (curl -v: "schannel: added 1 certificate(s) from CA
#     file"), it is only the ENVIRONMENT VARIABLE that is ignored. So the CA
#     travels as a `cacert` line from cc_export_tls_env, not via the Root store;
#   * it checks revocation, which an intercepting proxy cannot answer
#     (CRYPT_E_REVOCATION_OFFLINE, 2026-09-18 Windows run) — so that line is
#     written on Windows unconditionally.
# Extra lines come from the caller.
# Rewritten, never appended: the file is generated state, not a record.
cc__write_curlrc() { # cc__write_curlrc <path> [extra-line ...]
  local f="$1"; shift
  local lines="" l
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) lines="ssl-revoke-best-effort"$'\n' ;;
  esac
  for l in "$@"; do lines="${lines}${l}"$'\n'; done
  if [[ -z "$lines" ]]; then
    rm -f "$f" 2>/dev/null
    return 0
  fi
  mkdir -p "$(dirname "$f")" 2>/dev/null || return 1
  printf '%s' "$lines" >"$f"
}

# ── the CA reaches a BUILD through a STAGED CONTEXT (D4, amended 2026-09-24) ─
# It used to travel as `podman build --secret id=cc_ca,src=$CC_CA_BUNDLE`. That
# is BROKEN on Windows against a podman machine: podman joins a Windows path
# separator into the Linux-side temp path and the build dies before the first
# instruction —
#   open /mnt/c/.../tmp.X\podman-build-secret-N: The system cannot find the path
#   specified
# reproduced on the 2026-09-24 Windows Podman Desktop run with a trivial
# Dockerfile and every spelling of the context path; the same build without
# --secret succeeds. So with CC_CA_BUNDLE set, NONE of the three local images
# could build on Windows — the one platform this profile targets.
#
# The amendment: a CA certificate is PUBLIC material. It is the private key that
# is secret, and we never had one. The secret mechanism bought nothing but that
# failure, so the CA is simply a file in the build context now — a context this
# function STAGES outside the checkout, because nothing in the profile may write
# inside it (D7).
#
# WHY THE STAGED CONTEXT ALWAYS CONTAINS cc-ca.crt, empty when there is no CA:
# `COPY cc-ca.cr[t] <dir>/` with ZERO matches is a silent no-op under BuildKit
# (verified here with docker 29.8.1) but an ERROR under buildah, which is what
# `podman build` is (containers/podman#25229, containers/buildah#3284: "COPY
# with wildcard fails when no matching files found"). The Dockerfiles keep the
# glob so a bare `docker build` from the repo context still works, and every
# podman build gets a file to match — empty, which the Dockerfile's `-s` test
# reads exactly as "no CA", the same semantics `[ -s /run/secrets/cc_ca ]` had.
#
# The staged tree is REGENERABLE: it is deleted and rebuilt on every build, so a
# CA from a previous run can never linger and the directory is safe to delete.
# Contexts are small by design (deploy/pi/graphiti 56K including patches/ and
# config.yaml, central_command/crawler 32K, the sandbox Dockerfile alone), so
# `cp -R` is enough and rsync is not a dependency.
#
# Prints the staged directory on stdout.
cc_stage_build_context() { # cc_stage_build_context <staged-dir> <ca-bundle|''> <source>...
  local staged="$1" ca="$2"; shift 2
  local src
  rm -rf "$staged" || return 1
  mkdir -p "$staged" || return 1
  for src in "$@"; do
    if [[ -d "$src" ]]; then
      cp -R "$src/." "$staged/" || return 1
    elif [[ -f "$src" ]]; then
      cp "$src" "$staged/" || return 1
    else
      printf 'FATAL: build-context source not found: %s\n' "$src" >&2
      return 1
    fi
  done
  if [[ -n "$ca" ]]; then
    cp "$ca" "$staged/cc-ca.crt" || return 1
  else
    : >"$staged/cc-ca.crt" || return 1
  fi
  chmod 644 "$staged/cc-ca.crt" 2>/dev/null || true
  printf '%s' "$staged"
}

# ── what a LOCAL image was built from (v2.57.0) ─────────────────────────────
# The three locally built images carry FIXED tags (localhost/cc-sandbox:1,
# cc-crawler:1, cc-graphiti:<CC_GRAPHITI_TAG>), and the fetch phase used to
# read "tag present" as done — so a release that changed a Dockerfile, a patch
# under deploy/pi/graphiti/patches/ or the crawler's service.py never rebuilt
# on update, and the OLD image kept running under the new release. "Our own
# images stay exact — the release is one tested unit" (deploy-single.md) was
# false the moment a tag outlived its inputs. So every build records a HASH of
# its inputs on the image as a LABEL, and fetch and the probes compare it with
# the hash of the inputs the tree holds NOW: a different (or missing) label is
# a rebuild, which is also what makes a rollback rebuild the old image.
#
# What goes in is what can change the result, read off the build scripts:
#   * every file of the build context exactly as cc_stage_build_context lays it
#     out — a source DIRECTORY contributes each file under it by its path
#     relative to that directory, a source FILE by its basename — keyed by
#     that staged name, so enumeration order and source order are irrelevant
#     (the listing is sorted, C locale);
#   * the CA as the staged cc-ca.crt's CONTENT: no CA and an empty one stage the
#     same empty file and hash the same, a CA with content (or a renewed one)
#     does not;
#   * every --build-arg VALUE the build passes (the resolved base ref CC_IMG_*,
#     the registry/apt/pypi/npm seams, CC_TLS_INSECURE=1), sorted.
# What deliberately does NOT: timestamps, modes, the staged directory's path
# (it differs per state dir), the CA's PATH, --tls-verify (it changes how the
# base is fetched, not what is built), and Python bytecode (__pycache__/,
# *.pyc) — a deployment that imports central_command.crawler grows a
# __pycache__ inside the crawler context, which no Dockerfile COPYs and which
# would otherwise rebuild that image on every run.
#
# Line endings are NORMALISED (every CR dropped) before a file is hashed: a
# Windows checkout or a re-extracted zip may hold the same release in CRLF, and
# the same release must be the same hash on both sides of an update.
#
# Pure: reads files, writes nothing, calls no podman. The first payload line
# names the recipe version, so a future change to WHAT is hashed retires every
# old label deliberately (bump it) rather than by accident.
# Returns 1 when a source or the CA cannot be read — the caller treats an
# unknowable hash as "rebuild", never as "current".
cc_build_inputs_hash() { # cc_build_inputs_hash <ca-file|''> <build-arg-values, one per line> <source>...
  local ca="$1" args="$2"; shift 2
  local src f rel listing="" payload line
  for src in "$@"; do
    src="${src%/}"
    if [[ -d "$src" ]]; then
      while IFS= read -r f; do
        [[ -n "$f" ]] || continue
        rel="${f#"$src"/}"
        case "/$rel/" in */__pycache__/*) continue ;; esac
        # cc_stage_build_context writes the CA over a context file of this
        # name, so the context's own copy never reaches the build.
        [[ "$rel" == *.pyc || "$rel" == cc-ca.crt ]] && continue
        [[ -r "$f" ]] || return 1
        listing+="file ${rel} $(tr -d '\r' <"$f" | cc_sha256_stdin)"$'\n'
      done < <(find "$src" -type f 2>/dev/null)
    elif [[ -f "$src" && -r "$src" ]]; then
      listing+="file ${src##*/} $(tr -d '\r' <"$src" | cc_sha256_stdin)"$'\n'
    else
      return 1
    fi
  done
  if [[ -n "$ca" ]]; then
    [[ -r "$ca" ]] || return 1
    listing+="file cc-ca.crt $(tr -d '\r' <"$ca" | cc_sha256_stdin)"$'\n'
  else
    listing+="file cc-ca.crt $(printf '' | cc_sha256_stdin)"$'\n'
  fi
  while IFS= read -r line; do
    line="${line%$'\r'}"
    [[ -n "$line" ]] && listing+="arg ${line}"$'\n'
  done <<<"$args"
  payload="cc-build-inputs/1"$'\n'"$(LC_ALL=C sort <<<"${listing%$'\n'}")"
  printf '%s' "$payload" | cc_sha256_stdin
}

# The VALUE of every `--build-arg <K=V>` (or `--build-arg=<K=V>`) in a podman
# build argv, one per line — so a build script hashes exactly the arguments it
# then passes, out of the one array it passes them in.
cc_build_arg_values() { # cc_build_arg_values <argv>...
  while (( $# )); do
    case "$1" in
      --build-arg) (( $# >= 2 )) && { printf '%s\n' "$2"; shift; } ;;
      --build-arg=*) printf '%s\n' "${1#--build-arg=}" ;;
    esac
    shift
  done
  return 0
}

# The label's NAME — one definition for the build scripts (which write it) and
# setup.sh (which reads it).
cc_build_inputs_label() { printf 'cc.build-inputs'; }

# WHERE A STAGED BUILD PUTS ITS IMAGE (v2.57.0, 2026-10-01 design record D5).
# `update.sh apply` runs the NEW release's fetch from a staged tree BEFORE the
# merge, and promises that a stop there leaves the tree, the branch, the
# database and the containers as they were. The three local images have FIXED
# tags, so a staged build under the live tag would break that promise the moment
# it finished: new sandbox sessions on the OLD code would start from the NEW
# image, and any recreation of graphiti or the crawler would pick it up. So a
# staged build is tagged ASIDE — the live ref with `-staged` appended to its
# tag. It proves the build and warms the layer cache; the post-merge fetch then
# builds under the live tag (fast: every layer is cached) and that image's label
# is the one local_image_state reads.
# The form: still a valid reference on podman and docker (a tag is
# [A-Za-z0-9_][A-Za-z0-9_.-]{0,127}; the longest live tag, CC_GRAPHITI_TAG's
# default, stays far below 128), still under `localhost/` so podman never asks a
# registry for it, and it cannot be a release tag — ours are `1` and the
# Graphiti tag, and no release names one `-staged`.
# ONE definition: the build scripts tag with it and setup.sh reads and later
# untags with it, and both decide "staged" from CC_STAGED_FOR.
cc_staged_image_ref() { # cc_staged_image_ref <live-ref>
  printf '%s-staged' "$1"
}

# sha256 hex of stdin. The same three-way fallback cc_install_id (above) uses —
# sha256sum (coreutils, Git Bash), shasum (macOS), openssl — and the ONE hasher
# of a text: ledger-lib.sh's fingerprints pipe through it too (it is sourced
# after this file). Defined HERE because the build scripts source this library
# and nothing else (ledger-lib.sh is setup.sh's). A host with none of the three
# gets `nohash`, which compares equal to itself: the label check then degrades
# to the old "tag present" rule rather than rebuilding on every run.
cc_sha256_stdin() {
  local h=""
  if command -v sha256sum >/dev/null 2>&1; then
    h="$(sha256sum 2>/dev/null | cut -d' ' -f1)"
  elif command -v shasum >/dev/null 2>&1; then
    h="$(shasum -a 256 2>/dev/null | cut -d' ' -f1)"
  elif command -v openssl >/dev/null 2>&1; then
    h="$(openssl dgst -sha256 2>/dev/null | sed 's/.*= *//')"
  else
    cat >/dev/null
  fi
  printf '%s' "${h:-nohash}"
}

# The consumer list a WARN names, per command. Kept here so the phrasing is
# one fact: a command passes what it actually drives.
cc_tls_insecure_warn_text() { # cc_tls_insecure_warn_text <consumers>
  printf 'CC_TLS_INSECURE=1 — TLS verification is OFF for %s' "$1"
}

# The ONE gate a tls-insecure WARN passes through (F25, 2026-09-24 Windows
# testbed run — a single `./setup.sh check` calls `load_env` several times and
# execs resolve-images.sh, the three build scripts' dry runs and
# discover-llm.sh, each printing its own WARN: up to five identical lines for
# one fact). CC_TLS_INSECURE_WARNED is EXPORTED once printed, so it is
# inherited by every child process this run execs — the fix reaches across
# process boundaries for free — while a script run STANDALONE (nothing has
# warned yet) still gets its one warning.
#
# Prints "WARN tls-insecure: <text>" on stdout — the same shape every caller
# already produced via its own `warn "tls-insecure" "$(cc_tls_insecure_warn_text
# ...)"` or `echo "WARN tls-insecure: ..."` — so a caller that greps its own
# output or a log file for that line sees it unchanged. Returns 0 when it
# printed (a caller that keeps its own WARNS counter for the exit-code
# protocol should bump it then), 1 when a WARN already fired this run (nothing
# printed, counter left alone).
cc_tls_insecure_warn_once() { # cc_tls_insecure_warn_once <consumers>
  [[ "${CC_TLS_INSECURE_WARNED:-0}" == "1" ]] && return 1
  printf 'WARN tls-insecure: %s\n' "$(cc_tls_insecure_warn_text "$1")"
  export CC_TLS_INSECURE_WARNED=1
  return 0
}

# ── the LLM catalog, declared in .env (D3) ──────────────────────────────────
# Until v2.44.0 every LiteLLM alias was created as a PLACEHOLDER row and the
# operator filled provider, model id and key in the proxy's web UI while setup
# waited at an exit-3 gate. That pause is now the FALLBACK: the upstream can be
# declared in the answer file instead, which is what lets `check` prove the LLM
# from the host before a container exists.
#
#   CC_LLM_UPSTREAM_BASE_URL   the /v1 base THIS HOST can reach
#   CC_LLM_UPSTREAM_API_KEY    its key (never printed, never logged)
#   CC_LLM_UPSTREAM_MODEL_<A>  the UPSTREAM model id for alias <A>
#
# The key NAME is the alias upper-cased with every non-alphanumeric turned into
# `_`: cc-default -> CC_LLM_UPSTREAM_MODEL_CC_DEFAULT, gpt-4.1-nano ->
# CC_LLM_UPSTREAM_MODEL_GPT_4_1_NANO. Mirrored in
# deploy/pi/litellm/register-models.py (`alias_env_key`) — that script is what
# turns these into rows, and it runs as Python, so the derivation exists twice
# on purpose; `tests/test_register_models_upstream.py` pins the pair.
cc_alias_env_key() { # cc_alias_env_key <alias>
  local a="${1^^}"
  printf 'CC_LLM_UPSTREAM_MODEL_%s' "${a//[!A-Z0-9]/_}"
}

# WHICH aliases a given deployment requires. ONE list, read by the `check`
# command (a missing key is a USERACTION naming it) and by setup's `llm` phase
# (all declared = no UI pause). The four core aliases are always required; the
# speech pair only with the bundled engine — with CC_ENABLE_SPEECH=0 the
# operator points cc-tts/cc-stt at engines of their own, which is a UI job, not
# an upstream this file can name.
#
# Reads CC_ENABLE_SPEECH from the environment (the caller has sourced .env).
cc_required_aliases() {
  printf 'cc-default graphiti-llm cc-embedding gpt-4.1-nano'
  [[ "${CC_ENABLE_SPEECH:-1}" == "1" ]] && printf ' cc-tts cc-stt'
  printf '\n'
}

# ── ONE exit-code rule (2026-10-01 design record, D5) ───────────────────────
# Prints the code; the caller exits with it. Precedence is
# FAIL > USERACTION > WARN, everywhere: setup.sh's run_phase, its `machine`
# branch, update.sh's main and update-run.sh all call this one function.
#
# The rule it REPLACES ranked a gate above a FAIL ("the same event often prints
# both"), and that is how `phase_fetch` became a phase that could never return
# 1: its only FAIL path was followed by a USERACTION summary, so an install
# whose image build had failed reported "stopped for your action" and
# update.sh's apply merged straight past it. A FAIL is never reported as the
# operator's move. The one case where a USERACTION should win — the llm
# catalog gate — prints no FAIL at all, so nothing is lost.
cc_exit_code() { # cc_exit_code <fails> <warns> <actions>
  local f="${1:-0}" w="${2:-0}" a="${3:-0}"
  [[ "$f" =~ ^[0-9]+$ ]] || f=0
  [[ "$w" =~ ^[0-9]+$ ]] || w=0
  [[ "$a" =~ ^[0-9]+$ ]] || a=0
  if   (( f )); then printf '1'
  elif (( a )); then printf '3'
  elif (( w )); then printf '2'
  else               printf '0'
  fi
}

# ── a deployment carries no local patches (2026-10-01 design record, D10) ───
# The operator's rule: an install configures through .env and the environment,
# and never rewrites any part of Central Command; anything else it needs is a
# FINDING, carried back to a development session and released. It was prose
# until now, and the 2026-09-24 work-site session regenerated the npm lock,
# hand-edited images.txt and commented out lock pins anyway — each a defect
# later blamed on something else.
#
# So: is this tree still the release it claims to be?
#   0  yes — no difference outside gitignored paths
#   1  no  — TREE_DIFF_PATHS names what differs (NAMES only; a diff carries
#            content, and content can carry a secret)
#   2  there is no git baseline here to compare against, so nothing can be
#      proven either way (a fresh zip install, before ./update.sh init)
#
# `$root/.git` is tested DIRECTLY rather than asking git: `git -C` walks UP the
# directory tree, so a checkout unpacked inside somebody else's repository
# would otherwise be judged against that repository's index.
#
# TREE_DIFF_PATHS is not CC_-prefixed: every CC_* name a deploy script reads
# must be declared in .env.example, and this is an output variable, not an
# answer.
TREE_DIFF_PATHS=""
cc_tree_diff() { # cc_tree_diff <repo-root>
  local root="$1" dirty
  TREE_DIFF_PATHS=""
  command -v git >/dev/null 2>&1 || return 2
  [[ -d "$root/.git" ]] || return 2
  dirty="$(git -C "$root" status --porcelain --untracked-files=no 2>/dev/null)"
  if [[ -n "$dirty" ]]; then
    # $NF is the path a porcelain line ends with, including a rename's
    # destination (`R  old -> new`).
    TREE_DIFF_PATHS="$(printf '%s\n' "$dirty" | awk '{ print $NF }' | tr '\n' ' ')"
    return 1
  fi
  # An updated install carries `upstream` (the pristine imports). After a
  # fast-forward apply, HEAD IS upstream — and an equal commit is its own
  # ancestor, so this passes. A commit of its own on top is a local patch.
  git -C "$root" rev-parse --verify -q upstream >/dev/null 2>&1 || return 0
  git -C "$root" merge-base --is-ancestor HEAD upstream 2>/dev/null && return 0
  TREE_DIFF_PATHS="HEAD ($(git -C "$root" rev-parse --short HEAD 2>/dev/null)) carries commits that are not on \`upstream\`"
  return 1
}

# ── the STAGED ACQUISITION's scratch (2026-10-01 design record, D5) ─────────
# `update.sh apply` proves the NEW release's artifacts available BEFORE it
# merges, by running that release's own `setup.sh acquire` from a git worktree
# of `upstream`. Everything that run needs lives under this one directory of
# the STATE dir — never inside the checkout (2026-09-23 D7) — and the whole
# directory is removed when the apply finishes, on every exit path:
#   <stage>/tree/      update.sh's sparse worktree of `upstream`
#   <stage>/acquire/   the staged run's own copies: the CC_IMG_* answers and the
#                      image manifest the staged resolver rewrites, so the
#                      deployment's .env and installed.manifest are untouched
# ONE definition, because two scripts of two different releases agree on it:
# the update.sh that is running and the setup.sh it staged.
cc_stage_dir() { # cc_stage_dir <state-dir>
  printf '%s/stage' "$1"
}

# ── one run at a time: the run lock (2026-10-01 design record, D11) ─────────
# Kamal's lock directory, for the duration of a run. `mkdir` is the lock
# because it is ATOMIC on every filesystem this profile runs on, NTFS under Git
# Bash included — `flock` is not available there, and a lock FILE written with
# `>` is a check-then-act race.
#
#   <state>/run.lock/          the lock itself: whoever's mkdir succeeded holds it
#     command                  what the holder is running ("./setup.sh all")
#     started                  when it took the lock, UTC
#     pid                      the holder's pid — written LAST, via a rename, so
#                              a lock WITH a pid file is a complete lock
#
# It lives in the STATE directory, never in the checkout (2026-09-23 D7;
# tests/test_single_no_tree_writes.py).
#
# NESTING. update.sh runs `setup.sh fetch|llm|app|verify|stop` while it holds
# the lock, so a child must neither deadlock on its parent's lock nor release
# it on its way out. The holder EXPORTS CC_RUN_LOCK_PID=<its pid>; a child that
# inherits it, finds the lock's recorded pid equal to it AND that pid alive and
# one of ours, proceeds as `nested` and touches nothing. Anything else — the
# variable naming some other pid, a long-lived process (the API `boot` started)
# that inherited a pid which has since exited — is "not mine" and goes through
# the ordinary rules below. It is internal plumbing, never an answer, so it is
# in tests/test_single_airgap_seams.py's RUNTIME_ONLY and not in .env.example.
#
# STALE. The holder is PROVABLY GONE when `kill -0 <pid>` fails, or when the
# pid is alive but /proc/<pid>/cmdline is readable and names none of setup.sh,
# update.sh, update-run.sh — a pid reused after a reboot. Where /proc cannot be
# read, `kill -0` alone decides. A stale lock is RECLAIMED by the run that
# finds it, with a WARN naming the dead holder, rather than refused. That is
# deliberate: the operator-side agent may run exactly three commands
# (`./setup.sh`, `./setup.sh status`, `./setup.sh report`) and the design
# promises ONE recovery command, so the command that clears a stale lock has
# to be `./setup.sh` itself; and a logon-time `./setup.sh` after a power cut
# must not sit behind a lock no process holds. A lock with NO pid file is a
# run caught between its mkdir and its pid write: younger than
# RUN_LOCK_YOUNG_SECONDS it is "another run is starting", older it is stale.
#
# The two callers own their output protocol, so these functions PRINT nothing
# (cc_lock_holder aside, which is a reader); cc_lock_refusal_text and
# cc_lock_reclaim_text are the shared sentences.
#
# Globals (not CC_-prefixed — they are results, not answers):
#   RUN_LOCK_RESULT       acquired | nested | reclaimed | held | starting | error
#   RUN_LOCK_HOLDER_PID / _CMD / _AT   the holder found (for `reclaimed`, the
#                         DEAD one this run replaced)
#   RUN_LOCK_AGE          seconds, for a pid-less lock
RUN_LOCK_YOUNG_SECONDS=10
RUN_LOCK_RESULT=""; RUN_LOCK_HOLDER_PID=""; RUN_LOCK_HOLDER_CMD=""; RUN_LOCK_HOLDER_AT=""; RUN_LOCK_AGE=""

# Read the lock's three files into RUN_LOCK_HOLDER_*. Returns 1 when there is
# no lock. A half-written lock reads with empty fields — never an error.
cc__lock_read() { # cc__lock_read <state-dir>
  local lock="$1/run.lock" v
  RUN_LOCK_HOLDER_PID=""; RUN_LOCK_HOLDER_CMD=""; RUN_LOCK_HOLDER_AT=""
  [[ -d "$lock" ]] || return 1
  if [[ -f "$lock/pid" ]]; then
    IFS= read -r v <"$lock/pid" 2>/dev/null || true
    v="${v%$'\r'}"
    [[ "$v" =~ ^[0-9]+$ ]] && RUN_LOCK_HOLDER_PID="$v"
  fi
  if [[ -f "$lock/command" ]]; then
    IFS= read -r v <"$lock/command" 2>/dev/null || true
    RUN_LOCK_HOLDER_CMD="${v%$'\r'}"
  fi
  if [[ -f "$lock/started" ]]; then
    IFS= read -r v <"$lock/started" 2>/dev/null || true
    RUN_LOCK_HOLDER_AT="${v%$'\r'}"
  fi
  return 0
}

cc_lock_holder() { # cc_lock_holder <state-dir>
  cc__lock_read "$1" || return 1
  printf '%s\t%s\t%s\n' "$RUN_LOCK_HOLDER_PID" "$RUN_LOCK_HOLDER_CMD" "$RUN_LOCK_HOLDER_AT"
}

# Is <pid> a live run of OURS? 0 yes, 1 provably not (see STALE above).
cc_lock_pid_is_run() { # cc_lock_pid_is_run <pid>
  local pid="$1" f cmd=""
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  f="/proc/$pid/cmdline"
  [[ -r "$f" ]] || return 0
  cmd="$(tr '\0' ' ' <"$f" 2>/dev/null)" || return 0
  # Empty: a zombie or a process /proc will not describe — nothing provable
  # beyond kill -0, which said alive.
  [[ -n "$cmd" ]] || return 0
  case "$cmd" in
    *setup.sh*|*update.sh*|*update-run.sh*) return 0 ;;
  esac
  return 1
}

# The lock directory's age in seconds, or empty when this host cannot say
# (GNU stat, then BSD stat; no third fallback — an unknown age is judged OLD,
# because "a run is starting" is only ever true for a few seconds).
cc__lock_age() { # cc__lock_age <lock-dir>
  local m now
  m="$(stat -c %Y "$1" 2>/dev/null)" || m="$(stat -f %m "$1" 2>/dev/null)" || m=""
  [[ "$m" =~ ^[0-9]+$ ]] || { printf ''; return 0; }
  now="$(date +%s)"
  printf '%s' "$(( now - m ))"
}

# Fill a lock this process just created. pid LAST, by rename, so a reader that
# sees a pid sees the whole lock.
cc__lock_fill() { # cc__lock_fill <lock-dir> <command-text>
  local lock="$1"
  printf '%s\n' "$2" >"$lock/command" || return 1
  printf '%s\n' "$(date -u +%FT%TZ)" >"$lock/started" || return 1
  printf '%s\n' "$$" >"$lock/pid.tmp" || return 1
  mv -f "$lock/pid.tmp" "$lock/pid"
}

cc_lock_acquire() { # cc_lock_acquire <state-dir> <command-text>
  local state="$1" what="$2" lock="$1/run.lock" attempt aside age
  local reclaimed=0 dead_pid="" dead_cmd="" dead_at="" seen_pid
  RUN_LOCK_RESULT=""; RUN_LOCK_AGE=""
  mkdir -p "$state" 2>/dev/null || true
  for attempt in 1 2 3; do
    if mkdir "$lock" 2>/dev/null; then
      if ! cc__lock_fill "$lock" "$what"; then
        rm -rf "$lock" 2>/dev/null
        RUN_LOCK_RESULT=error
        return 1
      fi
      export CC_RUN_LOCK_PID="$$"
      if (( reclaimed )); then
        RUN_LOCK_RESULT=reclaimed
        RUN_LOCK_HOLDER_PID="$dead_pid"; RUN_LOCK_HOLDER_CMD="$dead_cmd"; RUN_LOCK_HOLDER_AT="$dead_at"
      else
        RUN_LOCK_RESULT=acquired
        RUN_LOCK_HOLDER_PID="$$"; RUN_LOCK_HOLDER_CMD="$what"; RUN_LOCK_HOLDER_AT=""
      fi
      return 0
    fi
    # It exists. Whose is it?
    if ! cc__lock_read "$state"; then
      [[ -e "$lock" ]] || continue        # released between our mkdir and read
      RUN_LOCK_RESULT=error               # a FILE named run.lock: not ours to touch
      return 1
    fi
    if [[ -n "$RUN_LOCK_HOLDER_PID" ]]; then
      if cc_lock_pid_is_run "$RUN_LOCK_HOLDER_PID"; then
        if [[ "${CC_RUN_LOCK_PID:-}" == "$RUN_LOCK_HOLDER_PID" ]]; then
          RUN_LOCK_RESULT=nested
          return 0
        fi
        RUN_LOCK_RESULT=held
        return 1
      fi
    else
      age="$(cc__lock_age "$lock")"
      RUN_LOCK_AGE="$age"
      if [[ -n "$age" ]] && (( age < RUN_LOCK_YOUNG_SECONDS )); then
        RUN_LOCK_RESULT=starting
        return 1
      fi
    fi
    # STALE. Rename it aside (atomic — of two runs reclaiming at once, one
    # rename wins and the other fails), then re-check that what moved is the
    # lock that was judged: if another reclaimer got there first and has
    # already taken a FRESH lock, put that one back and judge again.
    dead_pid="$RUN_LOCK_HOLDER_PID"; dead_cmd="$RUN_LOCK_HOLDER_CMD"; dead_at="$RUN_LOCK_HOLDER_AT"
    aside="$state/run.lock.stale.$$"
    rm -rf "$aside" 2>/dev/null
    if mv "$lock" "$aside" 2>/dev/null; then
      seen_pid=""
      [[ -f "$aside/pid" ]] && { IFS= read -r seen_pid <"$aside/pid" || true; seen_pid="${seen_pid%$'\r'}"; }
      if [[ "$seen_pid" != "$dead_pid" ]]; then
        # Never delete what moved here: it is somebody's live lock. Put it
        # back if its place is still free; otherwise it stays aside as
        # harmless debris (a race of THREE runs in the same instant).
        [[ -e "$lock" ]] || mv "$aside" "$lock" 2>/dev/null
        continue
      fi
      rm -rf "$aside" 2>/dev/null
      reclaimed=1
    fi
  done
  RUN_LOCK_RESULT=error
  return 1
}

# Only the process that TOOK the lock releases it: the recorded pid must be
# this shell's, and this must be that shell itself rather than a subshell of
# it ($BASHPID differs from $$ in a subshell) — so a nested child, or a `$(…)`
# that somehow ran an EXIT trap, can never drop its parent's lock.
cc_lock_release() { # cc_lock_release <state-dir>
  local lock="$1/run.lock" pid=""
  [[ "${BASHPID:-$$}" == "$$" ]] || return 0
  [[ -f "$lock/pid" ]] || return 0
  IFS= read -r pid <"$lock/pid" 2>/dev/null || true
  [[ "${pid%$'\r'}" == "$$" ]] || return 0
  rm -rf "$lock" 2>/dev/null
  return 0
}

# Append <action> to <signal>'s existing trap rather than replacing it. `trap
# -p` prints the action in re-readable quoting, and `eval set --` is the one
# way to read it back verbatim. INT/TERM put the release FIRST, then whatever
# was there, then the conventional exit status — a caller's own INT trap may
# `exit` before an appended release would run.
cc__trap_add() { # cc__trap_add <signal> <action> [first]
  local sig="$1" add="$2" first="${3:-}" prev
  prev="$(trap -p "$sig")"
  if [[ -z "$prev" ]]; then
    trap -- "$add" "$sig"
    return 0
  fi
  eval "set -- ${prev#trap -- }"
  if [[ -n "$first" ]]; then
    trap -- "$add"$'\n'"$1" "$sig"
  else
    trap -- "$1"$'\n'"$add" "$sig"
  fi
}

cc_lock_trap() { # cc_lock_trap <state-dir>
  local q; q="$(printf '%q' "$1")"
  local had_int had_term
  had_int="$(trap -p INT)"; had_term="$(trap -p TERM)"
  cc__trap_add EXIT "cc_lock_release $q"
  if [[ -n "$had_int" ]]; then cc__trap_add INT "cc_lock_release $q" first
  else trap -- "cc_lock_release $q; exit 130" INT; fi
  if [[ -n "$had_term" ]]; then cc__trap_add TERM "cc_lock_release $q" first
  else trap -- "cc_lock_release $q; exit 143" TERM; fi
  return 0
}

# The sentences, so setup.sh and update.sh say the same thing. Called right
# after a failed / reclaiming cc_lock_acquire, from RUN_LOCK_*.
cc_lock_refusal_text() { # cc_lock_refusal_text <state-dir>
  local lock="$1/run.lock"
  case "$RUN_LOCK_RESULT" in
    held)
      printf 'another run holds %s: pid %s is running "%s", started %s. That process is alive, so the lock is NOT stale — wait for it to finish (./setup.sh status shows where it stands), then run this again' \
        "$lock" "$RUN_LOCK_HOLDER_PID" "${RUN_LOCK_HOLDER_CMD:-an unrecorded command}" "${RUN_LOCK_HOLDER_AT:-at an unrecorded time}" ;;
    starting)
      printf 'another run is starting: %s was created %ss ago and has not recorded its pid yet — wait a moment, then run this again' \
        "$lock" "${RUN_LOCK_AGE:-?}" ;;
    *)
      printf 'could not take %s (it exists and is not a lock directory, or %s is not writable) — check that path and the permissions on the state directory' \
        "$lock" "$1" ;;
  esac
}

cc_lock_reclaim_text() { # cc_lock_reclaim_text <state-dir>
  if [[ -n "$RUN_LOCK_HOLDER_PID" ]]; then
    printf 'reclaimed a STALE lock in %s: pid %s, which was running "%s" (started %s), is gone — it did not finish, so the rows it was running read `started` in the ledger, and this run picks them up' \
      "$1" "$RUN_LOCK_HOLDER_PID" "${RUN_LOCK_HOLDER_CMD:-an unrecorded command}" "${RUN_LOCK_HOLDER_AT:-at an unrecorded time}"
  else
    printf 'reclaimed a STALE lock in %s: a half-written lock with no pid (%s, %s old) — the run that created it died before it recorded itself, so any rows it reached read `started` in the ledger, and this run picks them up' \
      "$1" "${RUN_LOCK_HOLDER_CMD:-no command recorded}" "${RUN_LOCK_AGE:+${RUN_LOCK_AGE}s}"
  fi
}
