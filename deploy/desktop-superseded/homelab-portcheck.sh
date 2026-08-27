#!/usr/bin/env bash
# ============================================================================
# RETIRED 2026-07-26 — kept as history, not run. The timer is disabled.
#
#   Boot-time self-heal for rootless podman port forwarding (2026-07-21 reboot
#   incident): podman-restart.service brings restart-policy containers up at
#   boot, but their host port forwarders can fail to bind — containers report
#   healthy while every published port is unreachable. This script checked each
#   expected host port and restarted the owning container when its port was
#   missing.
#
#   It has nothing left to watch. Everything it healed has moved to the
#   Raspberry Pi, which runs Docker — a root daemon whose `restart: always`
#   has no port-forwarder-didn't-bind failure mode, which is why deploy/pi/
#   ships no equivalent. The port map below is empty on purpose: it is the
#   record of a workaround that stopped being needed, not a list to refill.
#
#     5442 cc-postgres      -> Pi (deploy/pi/docker-compose.yml)   2026-07-26
#     5678 nat-n8n          -> Pi (email facade only)              2026-07-26
#     8000 nat-graphiti     -> Pi                                  2026-07-26
#     7474 nat-neo4j        -> Pi                                  2026-07-26
#     8065 nat-mattermost   -> retired with n8n-agentic-team       2026-07-26
#     3010 nat-n8n-mcp      -> retired (tooling for a stopped n8n) 2026-07-26
#     4000 litellm          -> Pi (cc-litellm, single deployment)  2026-07-26
# ============================================================================

set -u

# DESKTOP-ONLY script. Central Command moved to the Raspberry Pi on 2026-07-26 and
# runs on Docker there, which has no boot-time port-forwarder failure mode —
# this file heals the DESKTOP's remaining podman stacks and is not deployed to
# the Pi. See deploy/pi/ for the real deployment.
#
# host port -> container that publishes it. EMPTY: see the retirement note at
# the top. Every former entry moved to the Pi or was retired with its project;
# healing any of them here would put a stale or retired service back on the
# network behind the operator's back.
declare -A PORT_CONTAINER=()

# container -> its compose file (for the rm+recreate fallback below).
compose_file_for() {
  case "$1" in
    nat-*)         echo /home/codys-lab/n8n-agentic-team/docker-compose.yml ;;
    cc-postgres)   echo /home/codys-lab/Central Command/docker-compose.yml ;;
    litellm-*)     echo /home/codys-lab/LiteLLM/docker-compose.yml ;;
  esac
}
service_for() {
  case "$1" in
    cc-postgres)   echo postgres ;;
    litellm-*)     echo litellm ;;
    nat-*)         echo "${1#nat-}" ;;
  esac
}

# Age of a RUNNING container in seconds; empty when not running.
running_age() {
  local status started
  status=$(podman inspect -f '{{.State.Status}}' "$1" 2>/dev/null) || return 0
  [ "$status" = "running" ] || return 0
  started=$(podman inspect -f '{{.State.StartedAt}}' "$1" 2>/dev/null)
  echo $(( $(date +%s) - $(date -d "$started" +%s 2>/dev/null || date +%s) ))
}

heal_round() {
  local healed=0
  declare -A recreate_files=()
  for port in "${!PORT_CONTAINER[@]}"; do
    ss -tln | grep -q ":${port} " && continue
    local c="${PORT_CONTAINER[$port]}"
    # A just-started container may simply not have bound yet — restarting it
    # mid-startup is exactly what wedged the 2026-07-21 boot. Give it time;
    # the next timer tick (or the second round) will catch a real failure.
    local age; age=$(running_age "$c")
    if [ -n "$age" ] && [ "$age" -lt 45 ]; then
      echo "port ${port} unbound but ${c} started ${age}s ago — waiting"
      continue
    fi
    echo "port ${port} unbound — restarting ${c}"
    local out out2
    if ! out=$(podman restart -t 10 "$c" 2>&1) && ! out2=$(podman start "$c" 2>&1); then
      echo "$out"; echo "$out2"
      # The phantom-state failure mode ("container state improper"): restart
      # and start both refuse. The proven remedy is rm -f (named volumes
      # survive) + a single compose up for the project afterwards. Grep BOTH
      # outputs: in one observed phantom variant (2026-07-21 boot, round 1)
      # restart fails with only "crun start ... exit status 1" and the
      # "state improper" text appears solely in podman start's error.
      if grep -q "state improper" <<<"$out$out2"; then
        echo "${c} is in the phantom state — removing for recreate"
        podman rm -f "$c" >/dev/null 2>&1
        local f; f=$(compose_file_for "$c")
        [ -n "$f" ] && recreate_files[$f]=1
      fi
    fi
    healed=1
  done
  # ONE compose up per project, after all removals — overlapping compose
  # operations on one project split DNS records across network generations
  # (the third 2026-07-21 failure mode). `up -d` only creates what's missing.
  for f in "${!recreate_files[@]}"; do
    echo "recreating removed containers via ${f}"
    docker compose -f "$f" up -d 2>&1 | tail -2
  done
  return $healed
}

# Second boot failure mode (same 2026-07-21 incident): a boot-era
# aardvark-dns process survives into the new session deaf — containers get
# answers to nothing (getaddrinfo EAI_AGAIN for sibling containers) and
# crash-loop, while ports and health checks can look fine. Probe DNS from
# inside a container; on failure, kill aardvark-dns and bounce a cheap
# canary container so netavark respawns it with the current registrations.
heal_dns() {
  podman ps --format '{{.Names}}' | grep -q '^nat-postgres$' || return 0
  if ! podman exec nat-postgres getent hosts redis >/dev/null 2>&1; then
    echo "in-container DNS dead — respawning aardvark-dns"
    pkill -f '/aardvark-dns --config' || true
    sleep 1
    podman restart -t 5 nat-redis >/dev/null 2>&1 || true
    sleep 3
    podman exec nat-postgres getent hosts redis >/dev/null 2>&1 \
      && echo "DNS healed" || echo "WARNING: DNS still dead after respawn" >&2
  fi
}

# Two rounds with a settle gap: the first heals, the second verifies (and
# catches containers that were still mid-start on round one).
heal_dns
heal_round && { echo "all ports bound"; exit 0; }
sleep 20
heal_dns
heal_round && { echo "all ports bound after heal"; exit 0; }
echo "WARNING: some ports still unbound after two rounds" >&2
ss -tln >&2
exit 1
