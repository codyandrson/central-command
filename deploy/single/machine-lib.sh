#!/usr/bin/env bash
# ============================================================================
# machine-lib.sh — what the podman MACHINE has to be told, rendered as text.
#
#   Design record: docs/superpowers/specs/2026-09-23-airgap-check-configure-setup-design.md
#   D4 ("setup may write the podman machine"). SOURCE this file; it defines
#   functions and sets nothing global.
#
#   WHY IT IS A SEPARATE FILE OF PURE FUNCTIONS. A podman machine exists only
#   on Windows and macOS. The developer's box is Linux, so the WRITER can only
#   be exercised on the Windows testbed — but the DECISIONS (which mirror, which
#   host is insecure, what the drop-in should say, whether a restart is needed)
#   are pure text transformations of `.env`, and those are unit-tested here on
#   Linux by `tests/test_single_machine_lib.py` invoking bash directly. Nothing
#   in this file runs podman, reads a file, or writes one.
#
#   Syntax sources, all doc-verified 2026-09-23 (containers/image and
#   containers/common man pages):
#     * containers-registries.conf(5): a `[[registry]]` table takes `prefix`,
#       `location`, `insecure`, `blocked`; `[[registry.mirror]]` nests under it
#       and takes `location`, `insecure`, `pull-from-mirror`.
#     * containers-registries.conf.d(5): drop-ins live in
#       /etc/containers/registries.conf.d/, are loaded in alpha-numerical order
#       AFTER the main file, must be in the version-2 format, and `[[registry]]`
#       tables merge by overwriting an identical `prefix` and appending new
#       ones. So a drop-in is additive and the main file is never touched.
#     * containers.conf(5): `[engine] env = ["http_proxy=…"]` is the engine
#       process's environment — podman's and buildah's own, which is what pulls
#       and builds travel through. (`[containers] env` would be the CONTAINER's,
#       a different fact.) Drop-ins: /etc/containers/containers.conf.d/*.conf.
#
#   Functions:
#     cc_render_registries_conf            the registries drop-in, on stdout
#     cc_render_proxy_conf                 the containers.conf drop-in, on stdout
#     cc_redact_proxy <text>               same text with proxy VALUES removed
#     cc_machine_restart_needed <files...> what a changed file requires
# ============================================================================

[[ -n "${CC_MACHINE_LIB_LOADED:-}" ]] && return 0
CC_MACHINE_LIB_LOADED=1

# The paths this library describes. The machine phase writes exactly these two
# and never the main configuration files beside them.
CC_MACHINE_REGISTRIES_CONF="/etc/containers/registries.conf.d/cc-central-command.conf"
CC_MACHINE_PROXY_CONF="/etc/containers/containers.conf.d/cc-proxy.conf"
# Fedora CoreOS is the machine image (podman-machine-init(1): "a custom Fedora
# CoreOS based image"; WSL is a custom Fedora too), so the anchors directory and
# `update-ca-trust` are the right pair. The Debian/Ubuntu fallback is in
# setup.sh, which is where the probing belongs.
CC_MACHINE_CA_PEM="/etc/pki/ca-trust/source/anchors/cc-ca.pem"

# host only, from a URL or a bare host[:port][/path]
cc__mhost() { local h="${1#*://}"; printf '%s' "${h%%/*}"; }

# canonical-name<TAB>mirror-host, one per configured CC_REGISTRY_* seam whose
# value actually differs from the canonical name (a seam set to docker.io is
# not a mirror, and a drop-in mirroring a host onto itself is a loop).
cc_registry_pairs() {
  local pair canon var host
  for pair in "docker.io:CC_REGISTRY_DOCKERIO" \
              "ghcr.io:CC_REGISTRY_GHCR" \
              "mcr.microsoft.com:CC_REGISTRY_MCR"; do
    canon="${pair%%:*}"; var="${pair#*:}"
    host="$(cc__mhost "${!var:-}")"
    [[ -n "$host" && "$host" != "$canon" ]] || continue
    printf '%s\t%s\n' "$canon" "$host"
  done
}

# Every host an operator CC_IMG_* PIN points at. A re-namespacing mirror's pin
# may name a host no CC_REGISTRY_* seam mentions, and with verification off that
# host needs `insecure = true` too or the pull fails with a certificate error
# the operator has already said they do not care about.
cc_pin_hosts() {
  local v ref host out=""
  while IFS= read -r v; do
    [[ -n "$v" ]] || continue
    ref="${!v:-}"; [[ -n "$ref" ]] || continue
    # A ref with no registry host at all has nothing to mark, and the test for
    # that is the SLASH, not the colon: `postgres:16` would otherwise read as
    # the host "postgres:16" because of its tag separator.
    [[ "$ref" == */* ]] || continue
    host="${ref%%/*}"
    [[ "$host" == *.* || "$host" == *:* ]] || continue
    [[ "$host" == localhost* ]] && continue
    [[ " $out " == *" $host "* ]] || out="${out:+$out }$host"
  done < <(compgen -A variable CC_IMG_ 2>/dev/null | sort)
  printf '%s' "$out"
}

# The registries drop-in. Empty output means "nothing to configure" — which the
# caller must treat as "remove the file if it exists", not as "write nothing".
cc_render_registries_conf() {
  local insecure=0 body="" canon host hosts_done="" hosts_direct="" h
  [[ "${CC_TLS_INSECURE:-0}" == "1" ]] && insecure=1

  while IFS=$'\t' read -r canon host; do
    [[ -n "$canon" ]] || continue
    body="${body}[[registry]]"$'\n'
    body="${body}prefix = \"${canon}\""$'\n'
    # `location` is REQUIRED beside a non-wildcard `prefix`. Without it podman
    # refuses to load the WHOLE drop-in:
    #   Error: getting registries: loading drop-in registries configuration
    #   "…/cc-central-command.conf": invalid condition: location is unset and
    #   prefix is not in the format: *.example.com
    # and then every registry operation in the machine fails — `podman info`
    # included. So `./setup.sh machine` with any CC_REGISTRY_* set used to leave
    # the machine unable to resolve ANY registry, which is the one mechanism the
    # whole air-gapped design rests on. Found on the 2026-09-24 Windows run
    # (podman 5.8.3); it could not be found on Linux because there is no machine
    # there and the unit tests assert the rendered TEXT, never that podman
    # accepts it. For a mirror, location == prefix: the canonical name is where
    # the ref would have gone, and [[registry.mirror]] below is where it goes
    # instead.
    #
    # `insecure` does NOT belong on this block (F27, 2026-09-24 Windows
    # testbed run): this is the CANONICAL registry (docker.io etc), which
    # still gets pulled from directly whenever the mirror is unreachable or a
    # ref bypasses it — marking it insecure turns verification off for the
    # PUBLIC registry too, not just the mirror this operator actually chose to
    # trust unverified. Only the `[[registry.mirror]]` sub-table below (and the
    # direct mirror-host / pin-host blocks further down) carry it.
    body="${body}location = \"${canon}\""$'\n'
    body="${body}"$'\n'
    body="${body}[[registry.mirror]]"$'\n'
    body="${body}location = \"${host}\""$'\n'
    (( insecure )) && body="${body}insecure = true"$'\n'
    body="${body}"$'\n'
    hosts_done="${hosts_done:+$hosts_done }$host"
  done < <(cc_registry_pairs)

  # With verification off, the mirror host is also addressed DIRECTLY: the
  # CC_REGISTRY_* seams rewrite every ref in images.txt to <mirror>/<path>, so
  # the pull never goes through the canonical name above. That block is what
  # actually makes those pulls work.
  if (( insecure )); then
    for h in $hosts_done $(cc_pin_hosts); do
      [[ -n "$h" ]] || continue
      case " $hosts_direct " in *" $h "*) continue ;; esac
      hosts_direct="${hosts_direct} $h"
      body="${body}[[registry]]"$'\n'
      body="${body}location = \"${h}\""$'\n'
      body="${body}insecure = true"$'\n'
      body="${body}"$'\n'
    done
  fi

  [[ -n "$body" ]] || return 0
  printf '%s\n' "# GENERATED by deploy/single/setup.sh machine — Central Command."
  printf '%s\n' "# A DROP-IN: the main /etc/containers/registries.conf is never touched."
  printf '%s\n' "# Seams: CC_REGISTRY_DOCKERIO / _GHCR / _MCR, CC_TLS_INSECURE in the"
  printf '%s\n' "# repo-root .env. Re-render with: ./deploy/single/setup.sh machine --dry-run"
  printf '%s\n' ""
  printf '%s' "$body"
}

# The proxy drop-in. `[engine] env` is the podman/buildah PROCESS environment —
# the one pulls and builds travel through (containers.conf(5)).
cc_render_proxy_conf() {
  local p="${CC_PROXY:-}" np
  [[ -n "$p" ]] || return 0
  # Loopback and the container-host alias must bypass the proxy, same rule the
  # host side follows: every phase curls 127.0.0.1.
  np="127.0.0.1,localhost,host.containers.internal"
  printf '%s\n' "# GENERATED by deploy/single/setup.sh machine — Central Command."
  printf '%s\n' "# A DROP-IN: the main /etc/containers/containers.conf is never touched."
  printf '%s\n' "# Seam: CC_PROXY in the repo-root .env. This is the ENGINE's environment"
  printf '%s\n' "# (podman's and buildah's own), which is what pulls and builds use."
  printf '%s\n' ""
  printf '%s\n' "[engine]"
  printf 'env = ["http_proxy=%s", "https_proxy=%s", "no_proxy=%s"]\n' "$p" "$p" "$np"
}

# A proxy URL may carry credentials, so no command in this profile ever PRINTS
# one. The diff the machine phase shows goes through here first: the KEY stays,
# the value becomes the name of the seam it came from.
cc_redact_proxy() { # cc_redact_proxy <text>
  local t="$1"
  if [[ -n "${CC_PROXY:-}" ]]; then
    # Substring replacement, not sed: the value never reaches an argv.
    t="${t//"$CC_PROXY"/<value of CC_PROXY, not printed>}"
  fi
  printf '%s' "$t"
}

# What a changed file requires of the operator. Doc-verified 2026-09-23:
#   * registries.conf.d — podman re-reads its configuration per invocation, so
#     the next pull already sees it. No restart.
#   * containers.conf.d — same: read per invocation by the engine.
#   * the CA anchors — `update-ca-trust` rebuilds the bundle in place; a pull
#     started afterwards trusts it. No restart.
#   * --import-native-ca — podman-machine-set(1): "the certificates from the
#     host system are imported during machine startup". So it takes effect at
#     the NEXT start, and only that one needs a stop/start.
# Prints the sentence to put in a USERACTION, or nothing when no restart is due.
cc_machine_restart_needed() { # cc_machine_restart_needed <changed-item ...>
  local it need=0
  for it in "$@"; do
    case "$it" in
      import-native-ca) need=1 ;;
    esac
  done
  (( need )) || return 1
  printf '%s' "podman machine set --import-native-ca takes effect at the machine's NEXT START (podman-machine-set(1): the host certificates are imported during machine startup) — run: podman machine stop && podman machine start"
}

# ── how much memory the STACK will actually get (ledger F6) ─────────────────
# `check`'s memory probe read /proc/meminfo, which on a machine-backed podman
# is the HOST's RAM — a fact about the wrong computer. Every container runs
# inside the podman machine, so a 16 GB laptop with the default 2 GiB machine
# passed a check it should have failed (Windows testbed, 2026-09-24).
#
# The machine figure comes from `podman machine inspect --format
# '{{.Resources.Memory}}'` (podman-machine-inspect(1), verified 2026-09-25:
# `Resources` holds `CPUs`, `DiskSize`, `Memory`, `USBs`). Its UNIT is MiB, not
# bytes: podman-machine-set(1) documents `--memory` as "Memory (in MB)" and the
# inspect example prints `Memory: 6144` for a 6 GiB machine — the word "bytes"
# in that example's own format string is a documentation slip, and 6144 bytes is
# not a machine anyone ran.
#
# Pure text, so it is testable where no podman machine can exist
# (tests/test_single_machine_lib.py). Prints "<PASS|WARN> <message>".
CC_MEM_WANT_GB=3        # what the stack wants for itself
CC_MEM_BAR_MIB=4096     # ...and the bar, leaving room for the operator's own work

cc_memory_verdict() { # cc_memory_verdict <host-gb|""> <machine-mib|"">
  local host_gb="${1:-}" mach_mib="${2:-}" host_short="" host_long=""
  if [[ "$host_gb" =~ ^[0-9]+$ ]]; then
    host_short=" (host: ${host_gb} GB)"
    host_long=" — note: the host itself has ${host_gb} GB, which is not what the containers get"
  fi

  if [[ "$mach_mib" =~ ^[0-9]+$ ]] && (( mach_mib > 0 )); then
    local gib; gib="$(awk -v m="$mach_mib" 'BEGIN{g=m/1024; printf (g==int(g) ? "%d" : "%.1f"), g}')"
    if (( mach_mib >= CC_MEM_BAR_MIB )); then
      printf 'PASS the podman machine has %s GiB%s\n' "$gib" "$host_short"
    else
      printf 'WARN the podman machine has %s GiB — the stack wants ~%s GB; raise it with: podman machine set --memory %s (needs a machine restart)%s\n' \
        "$gib" "$CC_MEM_WANT_GB" "$CC_MEM_BAR_MIB" "$host_long"
    fi
    return 0
  fi

  # No machine (bare Linux): the host IS the substrate, so /proc/meminfo is the
  # right number — today's rule, unchanged.
  if [[ "$host_gb" =~ ^[0-9]+$ ]]; then
    if (( host_gb >= 4 )); then
      printf 'PASS %s GB total\n' "$host_gb"
    else
      printf 'WARN %s GB total — the stack wants ~%s GB plus your own workload\n' "$host_gb" "$CC_MEM_WANT_GB"
    fi
    return 0
  fi

  printf 'WARN cannot read total memory here — need ~%s GB for the stack\n' "$CC_MEM_WANT_GB"
}
