#!/usr/bin/env bash
# Rung 1 of the graph-inspection ladder (spec: docs/superpowers/specs/
# 2026-08-09-graph-inspection-design.md): Neo4j Browser over an EPHEMERAL
# port-forward. Foreground on purpose — Ctrl-C ends the exposure; cc-neo4j's
# permanent ClusterIP-only posture is untouched.
#
# Binds the TAILSCALE address, not 0.0.0.0, so it can coexist with
# cc-graph-bolt.service, which holds 127.0.0.1:7687 for the cockpit backend.
#
# Open http://<this-host's tailscale name>:7474, connect to
# bolt://<same host>:7687, user neo4j, password NEO4J_PASSWORD in
# deploy/pi/.env.
set -euo pipefail

TSIP=$(tailscale ip -4 | head -1)
echo "Neo4j Browser: http://${TSIP}:7474  (bolt://${TSIP}:7687) — Ctrl-C to stop"
exec sudo k3s kubectl -n central-command port-forward --address="${TSIP}" \
  svc/neo4j 7474:7474 7687:7687
