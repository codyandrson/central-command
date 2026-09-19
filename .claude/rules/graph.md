---
paths:
  - "central_command/integrations/graphiti*"
  - "central_command/integrations/neo4j_*"
  - "deploy/pi/graphiti/**"
  - "deploy/k3s/*graph*"
  - "scripts/*graph*"
---

# Graphiti / Neo4j bite marks

Rules below exist because a real failure produced them. Trust the rule even
where the story is gone. Moved verbatim from the root instructions; they load
when a matching file is read.

- **A Graphiti entity and edge each store their identity TWICE, and a
  hand-made write must set both halves.** Types live in real Neo4j labels AND
  an `n.labels` property; edge endpoints live in the relationship AND in
  `source_node_uuid`/`target_node_uuid` properties — the cockpit draws from
  the PROPERTIES. Neo4j cannot repoint a relationship in place, so a repoint
  is copy-then-delete (`integrations/neo4j_writer.py`). And **a node with no
  `name_embedding` is invisible to the semantic half of hybrid search** while
  still turning up in keyword hits — every write re-embeds through the
  `cc-embedding` alias at exactly 1024 dimensions; a mis-sized vector is
  dropped rather than stored (nothing schema-side enforces the width). Neo4j
  5.x has no parameterized labels, hence the closed `ENTITY_TYPES` allowlist
  mirroring `graphiti/config.yaml`.
- **The suite may not write to the live graph** — over MCP
  (`no_live_graph_writes`) or over bolt (`neo4j_writer._write` refuses). Opt
  in with `CC_LIVE_GRAPH_TESTS=1` when you mean it: one leftover scratch
  entity is enough to fail an unrelated live read test. And **an async bolt
  driver cached at module scope is loop-bound**; `neo4j_reader._get_driver`
  keys its cache on the running loop, and the writer shares that driver
  because access mode is a property of the SESSION, never of the driver.
- **Graphiti's extraction needs the BRIDGED LiteLLM alias.** graphiti_core
  drives extraction through the **Responses API** with no chat-completions
  fallback; an OpenAI-compatible backend without `/v1/responses` silently
  drops the `text.format` json_schema and every extraction fails. Registering
  the model as **`openai/chat_completions/<model>`** forces LiteLLM's
  Responses→chat bridge, which converts it into a real `response_format:
  json_schema`. That prefix is the whole fix.
- **Graphiti will retire a fact it merely RECOGNISES, and the guard is not
  the model.** Upstream's `resolve_extracted_edges` offers a whole-group
  semantic search as invalidation candidates (empty `SearchFilters()`); we
  carry upstream #1729 under `deploy/pi/graphiti/patches/` (with #1666,
  reasoning-first dedupe — drop both when they merge). Before blaming
  extraction quality on the model, check the invalidation scope, the sampling
  (unset temperature means the backend default, not deterministic), and the
  field ORDER of the schema (with schema-constrained decoding, index arrays
  before a `reasoning` field means the model answers before it thinks).
- **Episodes name the operator; "the operator" is not a graph subject.** The
  Person ontology refuses a role as a name, so an episode written "the
  operator is subscribed to X" lands with no subscriber or spawns a bare
  `operator` entity. The graph-propose pack renders the configured name into
  its rule; keep any new episode-writing pack on the same rule.
- **An edge with `expired_at` is not necessarily a retired fact.** Graphiti
  expires a NEW edge at birth whenever the extractor supplied an `invalid_at`
  — a deadline, a "through <date>" range, even a future date — and that is
  upstream intent (its own test asserts it). A retirement is an edge that
  PRE-DATES the episode; `episode_delta` guards on `r.created_at <
  e.created_at`, and its attribution window closes when the next episode in
  the group begins, because a fixed window credits one retirement to every
  neighbour while a backlog drains (2026-09-15: 89 of 90 entries were
  artifacts). Graphiti's MCP queue is in memory, serial per group, and
  exposes no depth — an "absent" episode may just be queued, so the
  re-submit deadline stretches with the PENDING rows ahead of it.
- **The ontology needs somewhere for every category to go.** Graphiti's
  upstream default entity types had no `Person`, so every person landed as a
  bare `Entity` while orgs typed correctly. Declare high-priority types FIRST
  so they beat the "use as last resort" types.
