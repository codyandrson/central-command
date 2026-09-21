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
- **Graphiti's LLM client is upstream's, and `graphiti-llm` is a PLAIN
  `openai/<model>` alias.** MCP 1.1.0 picks graphiti_core's chat-completions
  client for any non-OpenAI LLM URL; it sends `response_format: json_schema`,
  LiteLLM forwards it, llama.cpp enforces it as a grammar, and the client
  passes `llm.max_tokens` itself (verified 2026-09-21). From 2026-08-01 to
  v2.39.0 the alias carried an `openai/chat_completions/` prefix to bridge
  the Responses-API client we then pinned with a patch; on the chat client
  that prefix sends `chat_completions/<model>` upstream and 404s. The setup
  probes now assert the prefix is ABSENT. Do not reintroduce the pin or the
  prefix; if a future graphiti_core release changes the client choice, change
  the alias and the probe together.
- **A REQUIRED string attribute on a Graphiti entity type is an unbounded
  one.** graphiti-core re-extracts a typed entity's attributes on EVERY
  episode with the prior value in the prompt, and its 250-char cap exempts
  required fields (`attribute_length_cap_skipped_required` — dropping one
  would fail validation), so the value is rewritten longer each time. MCP
  1.1.0 made this the default: it substitutes its own built-in model — each
  with a required `description` — for any configured type whose NAME it
  knows, silently discarding the description in `config.yaml`. On the hub
  Person (502 edges) that reached 4378 chars and generations of 41-52k chars
  into the output cap: ~700 GPU-minutes a week, discarded (2026-09-20).
  `GRAPHITI_ENTITY_TYPE_FIELDS=none` (our `cc-entity-type-source.patch`)
  keeps each built-in model's DOCSTRING and drops its fields, which skips the
  per-entity attribute call. **The docstrings are the guidance**: the
  extraction prompt's entity-types block is built from `__doc__` alone, and
  the first cut of this fix (v2.38.4) swapped them for `config.yaml`'s
  one-line descriptions — with thinking off, the local model then answered
  `{"extracted_entities": []}` for short episodes (0/4 vs 4/4 with the
  docstrings, logprobs 2026-09-21). config.yaml's descriptions reach the
  prompt only for a name upstream has no model for. If you ever add a real
  attribute, make it Optional or give it `Field(max_length=…)`. Two things
  ride along: thinking is switched OFF on the `graphiti-llm` ALIAS
  (`chat_template_kwargs: {"enable_thinking": false}` in its litellm_params —
  graphiti-core sends reasoning controls only for gpt-5/o1/o3 names, and
  llama.cpp does not enforce a json_schema grammar while the model thinks),
  and a log line is the only place the symptom shows — read the Graphiti
  pod's log before theorising about the model.
- **Graphiti never assumes an episode's time.** Every present-tense fact's
  `valid_at` is anchored to the episode's `reference_time`, and Graphiti's
  own fallback is the moment it PROCESSED the episode — under MCP 1.0.2 that
  fallback was the only behaviour, and 44% of the live graph's edges read
  "became true when ingested" (2026-09-19). `graph.add_episode` therefore
  REQUIRES `reference_time` (the instant the SOURCE material is from, derived
  by the agent, approved by the operator): the shared `ARG_SPECS` refuses a
  proposal without it in both tiers, the Executor refuses anything that is
  not an ISO-8601 instant ("today" included), and `graphiti.add_episode` has
  no default for it. Don't add one anywhere — a default IS the assumption.
  The same law covers the operator's hand: `graph.create_edge` requires
  `valid_at`, spelled `unbounded` when the text gives no start (the Executor
  maps it to null), and `neo4j_writer.create_edge` stores a None start as
  null rather than the creation instant. Windows live on FACTS and are read
  from words — Graphiti has no per-fact date input — so the pack tells agents
  the phrasings the extractor keys on, and the auditor's judgment prompt
  compares every fact's window with the text's dates.
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
