# shellcheck shell=bash
# ============================================================================
# phases/machine.sh — the `machine` phase of the single-node install (deploy/single).
#
#   MACHINE: write the podman MACHINE from .env — the CA into its trust store,
#   the registries mirror/insecure drop-in, the proxy drop-in. A no-op
#   where there is no machine (bare Linux); `--dry-run` reports the diff.
#
#   SOURCED by deploy/single/setup.sh, never executed: setup.sh sources the
#   ten phase files in steps.tsv's phase order before main runs, and this
#   file defines functions and constants and does nothing else, so sourcing it
#   twice is harmless. What more than one phase uses — the output protocol,
#   load_env and the .env helpers, compose, the image catch-up, the process
#   helpers `stop` shares, the probes two phases read — is setup.sh's.
#
#   Design record: docs/superpowers/specs/2026-10-01-setup-ledger-selfcheck-design.md
#   D11 — "a thin orchestrator sourcing step files in order" (Sentry's
#   installer), one file per manifest phase (v2.58.0). The functions below
#   MOVED here from setup.sh unchanged, comments and all; the records they
#   cite are the ones that shaped them.
#
#   ROWS (steps.tsv, phase `machine`) — step, kind, probe; a probe marked
#   (setup.sh) is shared with another phase or the driver and lives there:
#     machine                            run    p_machine
#     machine-native-ca                  run    p_machine_native_ca
#     machine-ca                         run    p_machine_ca
#     machine-ca-probe                   run    p_machine_ca_probe
#     machine-registries                 run    p_machine_registries
#     machine-proxy                      run    p_machine_proxy
# ============================================================================

[[ -n "${CC_PHASE_MACHINE_LOADED:-}" ]] && return 0
CC_PHASE_MACHINE_LOADED=1

# Same, with stdin piped in — how a file gets INTO the machine without a share.
machine_sh_stdin() { # machine_sh_stdin <shell-command> < file
  local m err rc=0; m="$(machine_name)"
  [[ -n "$m" ]] || return 1
  { err="$(podman machine ssh "$m" -- "$1" 2>&1 1>&3 3>&-)" || rc=$?; } 3>&1
  machine_sh_log "$1" "$rc" "$err"
  return "$rc"
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE: machine — tell the podman MACHINE what .env says. Idempotent.
# ─────────────────────────────────────────────────────────────────────────────
# Windows and macOS run podman inside a VM, and that VM is a SECOND HOST: the
# operator's CC_CA_BUNDLE, CC_REGISTRY_* and CC_PROXY do not reach a pull or a
# build unless the machine itself carries them, and the Windows-side
# registries.conf is parsed but NOT honoured for a machine-backed connection
# (podman#16532). Until v2.43.0 preflight printed instructions and the operator
# typed them; the operator authorised setup writing the machine on 2026-09-23.
#
# THE RULES THIS PHASE FOLLOWS, because it writes another host:
#   * a no-op where there is no machine (bare Linux) — never a FAIL;
#   * DROP-INS only, never the main registries.conf / containers.conf
#     (containers-registries.conf.d(5): drop-ins load after the main file, in
#     alpha-numerical order, and merge rather than replace);
#   * the DIFF is printed BEFORE each write, and a proxy VALUE is never printed;
#   * `--dry-run` reports and writes nothing — that is what preflight calls;
#   * one PASS/FAIL per item, and a live PROBE for the CA rather than a
#     settings read (Podman Desktop's CA propagation is a known rough edge,
#     podman-desktop#3821).
MACHINE_CHANGED=()

# Show what a write would change, without ever printing a proxy value.
machine_diff() { # machine_diff <label> <remote-path> <desired-text>
  local label="$1" path="$2" desired="$3" current
  current="$(machine_sh "cat '$path' 2>/dev/null")"
  if [[ "$current" == "$desired" ]]; then
    return 1   # nothing to do
  fi
  note ""
  note "---- $label: $path"
  if [[ -z "$current" ]]; then
    note "     (absent in the machine; would be created)"
  else
    note "     (present and DIFFERENT; would be replaced)"
  fi
  note "$(cc_redact_proxy "$desired" | sed 's/^/     + /')"
  return 0
}

# Write a file inside the machine, as root, via stdin — the content never
# appears in an argv and needs no share between host and VM.
machine_put() { # machine_put <remote-path> <text>
  local path="$1"
  printf '%s' "$2" | machine_sh_stdin "sudo mkdir -p '$(dirname "$path")' && sudo tee '$path' >/dev/null" >/dev/null
}

phase_machine() {
  local dry=0
  [[ "${1:-}" == "--dry-run" ]] && dry=1
  MACHINE_CHANGED=()
  load_env || return 1

  local m; m="$(machine_name)"
  if [[ -z "$m" ]]; then
    pass "machine" "no podman machine on this host — nothing to configure (bare Linux runs containers directly)"
    return 0
  fi
  if ! machine_sh 'true' >/dev/null; then
    fail "machine" "podman machine '$m' exists but does not answer 'podman machine ssh' — start it: podman machine start"
    return 1
  fi
  pass "machine" "podman machine '$m' answers ssh$( (( dry )) && echo ' (--dry-run: reporting only, nothing written)')"

  # ── the CA ────────────────────────────────────────────────────────────────
  # Two paths, both wanted where available. --import-native-ca brings the
  # WINDOWS trust store in (which is where an enterprise CA usually already is,
  # by policy) and is podman >= 6.0 — so it is probed, not assumed. Installing
  # CC_CA_BUNDLE as an anchor covers the case the host store does not (a
  # self-signed mirror CA the operator holds as a file).
  if podman machine set --help 2>/dev/null | grep -q -- '--import-native-ca'; then
    if (( dry )); then
      note "     machine phase will apply: podman machine set --import-native-ca=true"
      pass "machine-native-ca" "podman supports --import-native-ca (the host trust store would be imported at the machine's next start)"
    elif [[ -f "$STATE_DIR/machine.import-native-ca" ]]; then
      pass "machine-native-ca" "podman machine set --import-native-ca=true was applied on $(cat "$STATE_DIR/machine.import-native-ca") (the host trust store is imported at every start)"
    elif podman machine set --import-native-ca=true >/dev/null 2>&1; then
      # Recorded in the state dir so a re-run neither re-applies it nor demands
      # another restart: `podman machine inspect` has no documented field for
      # it, and the setting only takes effect at the machine's next start.
      date -u +%Y-%m-%dT%H:%M:%SZ >"$STATE_DIR/machine.import-native-ca"
      pass "machine-native-ca" "podman machine set --import-native-ca=true applied (the host trust store is imported at every start)"
      MACHINE_CHANGED+=(import-native-ca)
    else
      warn "machine-native-ca" "podman machine set --import-native-ca=true was rejected — the CA anchor below is then the only path"
    fi
  else
    pass "machine-native-ca" "this podman has no --import-native-ca (it arrived in podman 6.0) — the CA anchor below is the path"
  fi

  if [[ -n "${CC_CA_BUNDLE:-}" ]]; then
    if [[ ! -r "$CC_CA_BUNDLE" ]]; then
      fail "machine-ca" "CC_CA_BUNDLE is set to $CC_CA_BUNDLE, which this host cannot read"
    else
      local want cur
      want="$(cat "$CC_CA_BUNDLE")"
      cur="$(machine_sh "cat '$CC_MACHINE_CA_PEM' 2>/dev/null")"
      if [[ "$cur" == "$want" ]]; then
        pass "machine-ca" "CC_CA_BUNDLE is already installed in the machine at $CC_MACHINE_CA_PEM"
      elif (( dry )); then
        note ""
        note "---- CA anchor: $CC_MACHINE_CA_PEM"
        note "     ($( [[ -z "$cur" ]] && echo absent || echo 'present and DIFFERENT' ); would install CC_CA_BUNDLE, then update-ca-trust)"
        warn "machine-ca" "the machine does not trust CC_CA_BUNDLE — machine phase will apply: install CC_CA_BUNDLE at $CC_MACHINE_CA_PEM, then update-ca-trust (./setup.sh does this in its next phase, machine)"
      else
        # The machine image is Fedora CoreOS (podman-machine-init(1)), so
        # ca-trust is the framework; the Debian/Ubuntu pair is the fallback for
        # a custom machine image.
        local rc=0
        if machine_sh 'command -v update-ca-trust >/dev/null 2>&1'; then
          machine_put "$CC_MACHINE_CA_PEM" "$want" || rc=1
          machine_sh 'sudo update-ca-trust' >/dev/null || rc=1
        else
          machine_put "/usr/local/share/ca-certificates/cc-ca.crt" "$want" || rc=1
          machine_sh 'sudo update-ca-certificates' >/dev/null || rc=1
        fi
        if (( rc )); then
          fail "machine-ca" "could not install CC_CA_BUNDLE into the machine — check that 'podman machine ssh $m -- sudo true' works"
        else
          pass "machine-ca" "CC_CA_BUNDLE installed in the machine and the trust store rebuilt"
          MACHINE_CHANGED+=(ca)
        fi
      fi
    fi
    # The VERIFY is a live probe from INSIDE the machine, not a settings read:
    # a present anchor file proves nothing about what a pull will accept. exit
    # 60 is curl's certificate failure, which is the answer this asks for.
    if (( ! dry )); then
      local probe_host rc2=0
      probe_host="$(cc__mhost "${CC_REGISTRY_DOCKERIO:-}")"
      [[ -n "$probe_host" ]] || probe_host="registry-1.docker.io"
      machine_sh "curl -fsSI --max-time 15 https://${probe_host}/v2/ >/dev/null" || rc2=$?
      if (( rc2 == 60 )); then
        fail "machine-ca-probe" "from inside the machine, https://${probe_host}/v2/ fails with a CERTIFICATE error (curl exit 60) — the CA is still not trusted there"
      elif (( rc2 == 0 )); then
        pass "machine-ca-probe" "from inside the machine, https://${probe_host}/v2/ answers with no certificate error"
      else
        warn "machine-ca-probe" "from inside the machine, https://${probe_host}/v2/ did not answer (curl exit $rc2) — not a certificate failure (60), so this is reachability, not trust"
      fi
    fi
  fi

  # ── the registries drop-in ────────────────────────────────────────────────
  local regconf; regconf="$(cc_render_registries_conf)"
  if [[ -z "$regconf" ]]; then
    pass "machine-registries" "no mirror or insecure host to declare (CC_REGISTRY_* unset and CC_TLS_INSECURE=0)"
  elif machine_diff "registries drop-in" "$CC_MACHINE_REGISTRIES_CONF" "$regconf"; then
    if (( dry )); then
      warn "machine-registries" "the machine's registries drop-in does not match .env — machine phase will apply: write $CC_MACHINE_REGISTRIES_CONF (diff above; the full run does this in the next phase)"
    elif machine_put "$CC_MACHINE_REGISTRIES_CONF" "$regconf"; then
      pass "machine-registries" "wrote $CC_MACHINE_REGISTRIES_CONF in the machine (podman re-reads its configuration per invocation — no restart)"
      MACHINE_CHANGED+=(registries)
    else
      fail "machine-registries" "could not write $CC_MACHINE_REGISTRIES_CONF in the machine"
    fi
  else
    pass "machine-registries" "$CC_MACHINE_REGISTRIES_CONF already matches .env"
  fi

  # ── the proxy drop-in ─────────────────────────────────────────────────────
  # containers.conf(5) `[engine] env` is the podman/buildah PROCESS
  # environment, which is what pulls and builds travel through. The value is
  # never printed — only the key name.
  local proxyconf; proxyconf="$(cc_render_proxy_conf)"
  if [[ -z "$proxyconf" ]]; then
    pass "machine-proxy" "CC_PROXY is unset — no proxy drop-in to write"
  elif machine_diff "proxy drop-in (values redacted)" "$CC_MACHINE_PROXY_CONF" "$proxyconf"; then
    if (( dry )); then
      warn "machine-proxy" "the machine's containers.conf proxy drop-in does not match CC_PROXY — machine phase will apply: write $CC_MACHINE_PROXY_CONF (the value is never printed; the full run does this in the next phase)"
    elif machine_put "$CC_MACHINE_PROXY_CONF" "$proxyconf"; then
      pass "machine-proxy" "wrote $CC_MACHINE_PROXY_CONF in the machine from CC_PROXY (key names only — the value is not printed)"
      MACHINE_CHANGED+=(proxy)
    else
      fail "machine-proxy" "could not write $CC_MACHINE_PROXY_CONF in the machine"
    fi
  else
    pass "machine-proxy" "$CC_MACHINE_PROXY_CONF already matches CC_PROXY"
  fi

  # Restarting: only --import-native-ca needs one (it imports at START). The
  # pause is that ROW's (machine-native-ca), so the ledger records it as the
  # gate it is rather than as nothing — a check-name that is no row marks none
  # (P5; tests/test_single_reporter_rows.py).
  if (( ! dry )) && (( ${#MACHINE_CHANGED[@]} )); then
    local r; r="$(cc_machine_restart_needed "${MACHINE_CHANGED[@]}")" \
      && useraction "machine-native-ca" "$r"
  fi
  return 0
}

# ── machine ─────────────────────────────────────────────────────────────────
p_machine() {
  [[ -n "$(machine_name)" ]] || return 0
  machine_sh 'true' >/dev/null 2>&1
}

p_machine_native_ca() {
  [[ -n "$(machine_name)" ]] || return 0
  podman machine set --help 2>/dev/null | grep -q -- '--import-native-ca' || return 0
  [[ -f "$STATE_DIR/machine.import-native-ca" ]]
}

p_machine_ca() {
  [[ -n "$(machine_name)" ]] || return 0
  local ca want cur
  ca="$(q_unquote "$(get_kv "$ENV_FILE" CC_CA_BUNDLE)")"
  [[ -n "$ca" ]] || return 0
  [[ -r "$ca" ]] || return 1
  want="$(cat "$ca")"
  cur="$(machine_sh "cat '$CC_MACHINE_CA_PEM' 2>/dev/null")"
  [[ "$cur" == "$want" ]]
}

p_machine_ca_probe() {
  [[ -n "$(machine_name)" ]] || return 0
  [[ -n "$(q_unquote "$(get_kv "$ENV_FILE" CC_CA_BUNDLE)")" ]] || return 0
  local host rc=0
  host="$(cc__mhost "$(q_unquote "$(get_kv "$ENV_FILE" CC_REGISTRY_DOCKERIO)")")"
  [[ -n "$host" ]] || host="registry-1.docker.io"
  machine_sh "curl -fsSI --max-time 15 https://${host}/v2/ >/dev/null" || rc=$?
  # Only curl 60 is a TRUST verdict. Anything else is reachability, which the
  # phase itself reports as a WARN — a probe that read it as failure would turn
  # an air-gapped machine into a permanently blocked install.
  (( rc != 60 ))
}

p_machine_registries() {
  [[ -n "$(machine_name)" ]] || return 0
  local want cur
  want="$(cc_render_registries_conf)"
  [[ -n "$want" ]] || return 0
  cur="$(machine_sh "cat '$CC_MACHINE_REGISTRIES_CONF' 2>/dev/null")"
  [[ "$cur" == "$want" ]]
}

p_machine_proxy() {
  [[ -n "$(machine_name)" ]] || return 0
  local want cur
  want="$(cc_render_proxy_conf)"
  [[ -n "$want" ]] || return 0
  cur="$(machine_sh "cat '$CC_MACHINE_PROXY_CONF' 2>/dev/null")"
  [[ "$cur" == "$want" ]]
}
