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

# The consumer list a WARN names, per command. Kept here so the phrasing is
# one fact: a command passes what it actually drives.
cc_tls_insecure_warn_text() { # cc_tls_insecure_warn_text <consumers>
  printf 'CC_TLS_INSECURE=1 — TLS verification is OFF for %s' "$1"
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
