# Graph inspection & curation — the rung ladder (2026-08-09)

**Status when written:** approved by the operator 2026-08-09 ("let's also immediately
start working on all of these rungs (except the actual graphistry
implementation, that should stay as a documented enhancement, deferred until
we need it)"). Rungs 1–2 and the rung-3 explore panel are being built in the
same change-set as this spec; Graphistry integration and graph curation writes
are the two recorded next increments.

## Why (what the research found, 2026-08-08)

Live-web research (three sonnet research runs; findings condensed here, full
citations in the session that produced this spec):

- **Graphiti ships no UI.** Upstream getzep/graphiti's visualization story is
  "use your backend's stock browser" (Neo4j Browser for us). Zep's graph
  explorer is a Zep-Cloud dashboard feature. The one community curation UI
  (`Brusdeylins/graphiti-ui`) requires a forked Graphiti on FalkorDB — a
  design reference, not an install.
- **Neo4j's premium tooling is closed to a self-hosted Community deployment.**
  Bloom requires Enterprise; the NVL JS library's LICENSE.txt (verified
  directly) restricts it to Aura / Neo4j commercial products; NeoDash is
  deprecated upstream as of 2026. What we can use freely: the bundled Neo4j
  Browser (:7474) and the official `neo4j-viz` Python package (production
  stable, 2026-07 release).
- **No packaged product exists for human review of machine-extracted temporal
  knowledge.** Bitemporal timeline views and HITL curation queues are
  research-stage in 2026 (CleanGraph, arXiv 2405.03932; ExtracTable, 2026).
  That is exactly Central Command's existing shape — proposal → Decisions Inbox →
  Executor — which no shipping tool has. Building the curation UI on our own
  spine is therefore the plan, not a fallback.
- **Established interaction patterns to adopt:** faceted search + incremental
  node expansion (never render the whole graph); a temporal filter as a
  first-class control ("the graph as of time T" via `valid_at`/`invalid_at` —
  Graphiti invalidates superseded facts rather than deleting them, so history
  is already in the data); provenance drill-down (Entity ← `MENTIONS` ←
  Episodic traces every fact to its source episode).
- **Our deployed cc-graphiti MCP server already exposes the curation surface**
  (verified live 2026-08-09 via `tools/list`): `delete_entity_edge`,
  `delete_episode`, `get_entity_edge`, `get_episodes`, `clear_graph`,
  alongside the reads and `add_memory`. Curation writes need no image work —
  only gating.

## The rungs

### Rung 1 — Neo4j Browser over an ephemeral forward (operator recipe) ✅

`deploy/k3s/graph-browse.sh`: a `kubectl port-forward` of svc/neo4j
7474 + 7687 bound to the Pi's **tailscale address**, foreground, Ctrl-C to
stop. Ephemeral by design — no manifest changes, the permanent
**ClusterIP-only posture of cc-neo4j is untouched**. Credentials: user
`neo4j`, password `NEO4J_PASSWORD` in `deploy/pi/.env`.

### Rung 2 — `scripts/graph_inspect.py` (operator script) ✅

A standalone PEP 723 (`uv run`) script using the official `neo4j-viz`
package + bolt driver against `127.0.0.1:7687` (see the loopback unit below).
Renders an interactive HTML file (group filter, optional as-of time filter).
Operator tooling, not app code: it deliberately carries its own inline deps so
the app's dependency tree gains nothing.

### Rung 3 — the cockpit Graph panel (explore-only first) ✅ this change-set

**Transport decision.** The graph panel needs Cypher-shaped reads
(neighborhood expansion, temporal filters) that Graphiti's MCP search tools
cannot express. So the **API tier** (the credentialed side of the trust
boundary, same side as the Executor) gains a **read-only bolt client**:
`integrations/neo4j_reader.py`, sessions opened with read access mode, only
canned parameterized queries — **no raw-Cypher endpoint; the browser never
talks to Neo4j**. `runtime/` still has zero Neo4j references; agents keep
reaching the graph only through Graphiti's MCP. This deliberately amends the
"app holds zero direct Neo4j references" fact recorded at the k3s cutover —
the ClusterIP-only Service stays, and reachability comes from:

**`deploy/k3s/cc-graph-bolt.service`** — a systemd unit running
`k3s kubectl port-forward` of svc/neo4j **7687 on 127.0.0.1 only**,
`Restart=always`, `Requires=k3s.service`. This keeps the standing rule that
every `CC_*` endpoint resolves to loopback, adds no tailnet/LAN exposure, and
survives pod moves by reconnecting (outage-equivalence: a drop costs latency,
never work — reads retry). The rung-1 browse script binds the tailscale
address precisely so the two can coexist on 7687.

**Wire contract** (backend and frontend built against this; a backend
wire-shape test pins each payload — the untyped-RPC lesson):

- `GET /api/graph/search?q=&group_id=&limit=` →
  `{nodes: [{uuid, name, labels, summary, group_id}]}` — fulltext/CONTAINS
  match on Entity nodes.
- `GET /api/graph/neighborhood?uuid=&at=&limit=` →
  `{nodes: [...], edges: [{uuid, source, target, name, fact, valid_at,
  invalid_at, created_at}]}` — one hop of `RELATES_TO` around a node;
  `at` (ISO time) filters edges to `valid_at <= at AND (invalid_at IS NULL OR
  invalid_at > at)`; omitted = everything, with invalidated edges flagged, so
  the UI can render history distinctly rather than hide it.
- `GET /api/graph/provenance?uuid=` → the Episodic nodes `MENTIONS`-linked to
  an entity (and for an edge, its source episode): `{episodes: [{uuid, name,
  source_description, valid_at, content_preview}]}`.
- `GET /api/graph/status` → `{available: bool, node_count, edge_count,
  group_ids: [...], graphistry: "disabled" | "unreachable" | "live",
  graphistry_enabled: bool, graphistry_url: string}`.
- `PUT /api/graph/settings` `{graphistry_enabled?, graphistry_url?}` → the
  status shape. Settings persist in a new `app_setting` key/value table
  (operator input is trusted — a settings flip is an operator action like a
  schedule toggle, no proposal gate; the cockpit's localStorage-only settings
  can't carry a flag the *backend* must act on, hence the table).

**Renderer:** cytoscape.js (MIT, active — v3.34 2026-06; chosen over
react-force-graph for its editing primitives, which the curation increment
will want). Explore-only UI: search box → results → click to expand
neighborhood, incremental (never load-the-whole-graph); an as-of time control;
a provenance drawer on node/edge select; invalidated edges rendered
struck/dimmed, never hidden.

### The Graphistry seam (built now) vs. Graphistry itself (deferred)

Graphistry's GPU requirement is **server-side** (RAPIDS on NVIDIA/x86); the
homelab Pi/chromebox can never host it, the operator's workstation and the work
deployment can. Decision (the operator, 2026-08-09): ship the **seam** now, defer the
integration until scale or the work deployment demands it.

- **Settings toggle** `graphistry_enabled` (default **off**) plus
  `graphistry_url` — governed data like every other setting. The toggle
  records *intent* only.
- **Three states, not two** — the flag can't know whether the workstation is
  on or its GPU is free (VRAM contention with the local LLM makes
  enabled-but-unreachable an *expected* state, not an error):
  1. `disabled` — quiet indicator on the Graph page: enable in Settings.
  2. `unreachable` — enabled, but the health probe to `graphistry_url`
     failed. Banner says so. The UI **believes the probe, not the flag**.
  3. `live` — probe succeeded; the enhanced view may be offered.
  The state rides on `graph.status` (backend-computed, wire-shape-tested);
  the cockpit never probes the workstation itself.
- **The Pi never starts/stops the Graphistry container.** Turning it on
  physically is an operator action at the workstation (someday, perhaps, a
  gated capability for an infra-manager agent — a deliberate decision, never
  a side effect of the settings flip).
- At work the same seam holds with the toggle simply left on; `unreachable`
  becomes a genuine health alarm there rather than an expected condition.

## Recorded next increments (deliberately not in this change-set)

1. ~~**Curation writes**~~ — **BUILT 2026-08-15, and NOT as specified here.**
   This entry proposed routing the operator's own corrections through a
   proposal and the Executor. That was wrong on inspection: a gate exists so
   the operator reviews an AGENT's claim before it changes the world, and asking him to
   approve his own click is ceremony wearing governance's clothes. The built
   shape is `integrations/neo4j_writer.py` — a closed, parameterized write set
   the API tier calls directly, no raw Cypher over the wire, no path from
   `runtime/` (guarded by a source walk in `tests/test_graph_curation.py`).
   The MCP `delete_entity_edge` / `delete_episode` tools went unused: they
   cover deletion only, and merge / repoint / rename / create need Cypher
   regardless, so a second write path would have bought nothing.
2. **Graphistry integration** — pygraphistry (BSD) server-side push behind the
   `live` state; blocked on: a reachable Graphistry server (workstation or
   work), 2026 licensing check for the self-hosted server, and an actual
   graph too big for cytoscape in the browser (~tens of thousands of
   elements; we are nowhere near).
3. **NL-to-graph querying** — an agent with the ungated read tools *is* the
   Text2Cypher interface; revisit once the panel exists and real questions
   accumulate.
