---
paths:
  - "central_command/integrations/graphiti*"
  - "central_command/integrations/graph_ontology.py"
  - "central_command/integrations/neo4j_*"
  - "deploy/graphiti-patches/**"
  - "scripts/apply_graphiti_patches.py"
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
  `source_node_uuid`/`target_node_uuid` properties. Since v2.61.0 every READ
  takes the relationship's real endpoints (`startNode`/`endNode`) and real
  labels as truth, as upstream's own reads do; the properties stay WRITTEN
  (they are the bulk shape) and only the audit's mismatch check reads them.
  Neo4j cannot repoint a relationship in place, so a repoint is
  copy-then-delete (`integrations/neo4j_writer.py`) — and **a copy that keeps
  its uuid must delete its original IN THE SAME STATEMENT**, by the matched
  variable: a second statement matching by uuid matches the copy too and
  deletes both (rescope_episode lost every moved fact whose endpoint was
  split, found on a scratch graph 2026-10-05). And **a node with no
  `name_embedding` is invisible to the semantic half of hybrid search** while
  still turning up in keyword hits — every write re-embeds through the
  `cc-embedding` alias at exactly 1024 dimensions; a mis-sized vector is
  dropped rather than stored (nothing schema-side enforces the width). Neo4j
  5.x has no parameterized labels, hence the closed `ENTITY_TYPES` allowlist
  mirroring `integrations/graph_ontology.py` (a test pins the two equal).
- **The bulk path's shape is canonical; a model's `save()` writes a different
  one.** `add_episode` stores entities through `add_nodes_and_edges_bulk`: a
  `labels` property, and facts with `source_node_uuid`/`target_node_uuid`.
  `EntityNode.save()` / `EntityEdge.save()` store no `labels` property and
  name the endpoints `source_uuid`/`target_uuid`. Every extracted fact in the
  graph has the bulk shape, so moving curation onto `save()` would add a second
  shape, not remove one. Hand-made writes stay our own Cypher
  (`neo4j_writer`), which writes the bulk shape and has no upstream
  equivalent to lean on for update, merge or move. `tests/test_graph_write_shape.py`
  derives the bulk path's property sets from the INSTALLED package (the dict
  builders in `utils/bulk_utils.py`, the Neo4j bulk queries) and fails on any
  difference not in its WHY-annotated allowlist — so a fact we create carries
  `expired_at` (null) and `reference_time` like an extracted one, and vectors
  go through `db.create.setNodeVectorProperty` / `setRelationshipVectorProperty`
  like the bulk path's (the procedure REFUSES a null vector, so a degraded
  write clears the property instead). No `embedding_model`/`embedding_dimensions`
  stamp: upstream's readers turn any unknown property into an entity
  ATTRIBUTE, which reached agents' fact results; old stamped rows are stripped
  in `graphiti.fact_result`/`node_result`. Embeddings stay OUR request, not the
  library's embedder: `OpenAIEmbedder.create` slices the vector to
  `embedding_dim`, so a wrong-width model would be truncated into a
  meaningless vector instead of being dropped.
- **Deleting an episode is upstream's rule, in ONE transaction of ours,
  through the group's queue, against the set the operator approved**
  (`graph.delete_episode`, v2.61.0). The rule is `Graphiti.remove_episode`'s:
  the facts among its `entity_edges` whose `episodes[0]` is this episode, the
  entities exactly one MENTIONS reaches, and — because the node delete is
  DETACH — every fact still attached to those entities (COLLATERAL, shown
  separately). Plus the cleanup upstream omits: the dead uuid leaves every
  surviving fact's `episodes`, deleted facts leave other episodes'
  `entity_edges`. `tests/test_graph_delete_episode.py` pins the installed
  `remove_episode` source by hash: if it fails after an upgrade, READ the new
  rule and match it before re-pinning. Upstream runs three separate
  auto-commit deletes (the node one in `CALL … IN TRANSACTIONS` batches), so a
  crash between them leaves half a deletion whose re-run reads a different
  set — ours computes, compares and deletes in one `execute_write`, so a
  RUNNING `remove_episode` job is simply re-run (a gone episode finishes DONE
  and still cleans up) and is never settled by the marker. The set is
  computed THREE times — at propose (embedded in the proposal, the cockpit's
  digest), at execute (the Executor refuses a change) and inside the deleting
  transaction (the job FAILS rather than delete a different set). The patch
  gate never holds a deletion back; a deletion still waits behind the
  extraction ahead of it in its group. `remove_episode` trusts the ORDER of
  `edge.episodes`: the graph audit lists facts whose list is empty or names a
  missing episode — curate those before deleting near them.
- **A Graphiti date filter takes at most ONE parameter-carrying OR group per
  field.** `edge_search_filter_query_constructor` names a date parameter by
  its position INSIDE its AND group (`valid_at_0`), so a second OR group with
  a compared date silently overwrites the first one's value. The agent
  filters (`graphiti.fact_filters`) build `[[IS NULL], [<op> instant]]` — the
  parameterless `IS NULL` is what an open bound means (a fact with no
  `invalid_at` is still true) — and `tests/test_graph_search_filters.py` runs
  the library's own constructor over every window we build. A centre node
  selects the node-distance recipes whatever the reranker; with no filter the
  call is byte-identical to v2.60.0's.
- **The suite may not write to the live graph** — through the library
  (`no_live_graph_writes` wraps the ONE builder,
  `graphiti_client.get_graphiti()`, so a real client's write methods refuse)
  or over bolt (`neo4j_writer._write` refuses). Opt
  in with `CC_LIVE_GRAPH_TESTS=1` when you mean it: one leftover scratch
  entity is enough to fail an unrelated live read test. And **an async bolt
  driver cached at module scope is loop-bound**; `neo4j_reader._get_driver`
  keys its cache on the running loop, and the writer shares that driver
  because access mode is a property of the SESSION, never of the driver. The
  Graphiti object `graphiti_client` builds is cached per loop for the same
  reason.
- **The LLM client is graphiti-core's `OpenAIGenericClient`, built by us, and
  `graphiti-llm` is a PLAIN `openai/<model>` alias.** `graphiti_client` passes
  every client in (a missing one makes `Graphiti.__init__` quietly build an
  OpenAI client that wants an OpenAI key); the generic client sends
  `response_format: json_schema` to `/v1/chat/completions`, LiteLLM forwards
  it, llama.cpp enforces it as a grammar, and `max_tokens` is the constructor
  argument (verified 2026-09-21). From 2026-08-01 to v2.39.0 the alias carried
  an `openai/chat_completions/` prefix to bridge a Responses-API client we then
  pinned with a patch; on the chat client that prefix sends
  `chat_completions/<model>` upstream and 404s. The setup probes assert the
  prefix is ABSENT. Do not reintroduce the prefix or a Responses client; if a
  future graphiti-core release changes which client suits a non-OpenAI
  backend, change the alias and the probe together.
- **A REQUIRED string attribute on a Graphiti entity type is an unbounded
  one.** graphiti-core re-extracts a typed entity's attributes on EVERY
  episode with the prior value in the prompt, and its 250-char cap exempts
  required fields (`attribute_length_cap_skipped_required` — dropping one
  would fail validation), so the value is rewritten longer each time. The
  stock MCP server made this the default (its built-in models each carry a
  required `description`): on the hub Person (502 edges) that reached 4378
  chars and generations of 41-52k chars into the output cap: ~700 GPU-minutes
  a week, discarded (2026-09-20). `integrations/graph_ontology.py` is the
  answer: ten FIELD-LESS models, in the declaration order of the ontology, whose
  docstrings are the stock built-ins' verbatim. A model with no fields skips
  the per-entity attribute call. **The docstrings are the guidance**: the
  extraction prompt's entity-types block is built from `__doc__` alone, and the
  first cut of the fix (v2.38.4) swapped them for one-line descriptions — with
  thinking off, the local model then answered `{"extracted_entities": []}` for
  short episodes (0/4 vs 4/4 with the docstrings, logprobs 2026-09-21). They
  are assigned as explicit strings and pinned by SHA-256 in
  `tests/test_graph_ontology.py`; changing one changes what the extraction
  model is told, and the same alias serves another workload, so measure
  against both. If you ever add a real attribute, make it Optional or give it
  `Field(max_length=…)`. Two things ride along: thinking is switched OFF on
  the `graphiti-llm` ALIAS (`chat_template_kwargs: {"enable_thinking": false}`
  in its litellm_params — graphiti-core sends reasoning controls only for
  gpt-5/o1/o3 names, and llama.cpp does not enforce a json_schema grammar
  while the model thinks), and a failed job's `last_error` (and the Verify
  row it parks) is where the symptom shows — read it before theorising about
  the model.
- **Graphiti never assumes an episode's time.** Every present-tense fact's
  `valid_at` is anchored to the episode's `reference_time`, and the fallback
  is the moment the episode was PROCESSED — when nothing states a time, 44% of
  the live graph's edges read "became true when ingested" (2026-09-19).
  `graph.add_episode` therefore REQUIRES `reference_time` (the instant the
  SOURCE material is from, derived by the agent, approved by the operator):
  the shared `ARG_SPECS` refuses a proposal without it in both tiers, the
  Executor refuses anything that is not an ISO-8601 instant ("today"
  included), and neither `graphiti_ingest.enqueue` nor the worker has a
  default for it. Don't add one anywhere — a default IS the assumption.
  The same law covers the operator's hand: `graph.create_edge` requires
  `valid_at`, spelled `unbounded` when the text gives no start (the Executor
  maps it to null), and `neo4j_writer.create_edge` stores a None start as
  null rather than the creation instant. Windows live on FACTS and are read
  from words — Graphiti has no per-fact date input — so the pack tells agents
  the phrasings the extractor keys on, and the auditor's judgment prompt
  compares every fact's window with the text's dates.
- **Graphiti will retire a fact it merely RECOGNISES, and the guard is not
  the model.** Upstream's `resolve_extracted_edges` offers a whole-group
  semantic search as invalidation candidates (empty `SearchFilters()`). We
  carry upstream #1729 (invalidation scope) and #1666 (reasoning-first dedupe)
  as patch files in `deploy/graphiti-patches/`, applied to the INSTALLED
  package by `scripts/apply_graphiti_patches.py` after every dependency
  install (setup, the updater, and by hand in a development environment); the
  ingest worker refuses to extract without them and the self-check's
  `graph-patches` row says so — drop both when they merge upstream and the pin
  moves. Before blaming extraction quality on the model, check the
  invalidation scope, the sampling (unset temperature means the backend
  default, not deterministic — `graph_llm_temperature` is 0), and the field
  ORDER of the schema (with schema-constrained decoding, index arrays before a
  `reasoning` field means the model answers before it thinks).
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
  artifacts). An episode that is not in the graph yet is a `graph_ingest_job`
  row, not an absence to infer: jobs run in strict order per group (groups run
  side by side), a crash is recovered by the `proposal=<id>` marker on the
  Episodic node (landed → DONE, else QUEUED again), and nothing is ever
  re-submitted. A transient failure re-queues with backoff; a permanent one
  FAILS the job and parks its Verify row with the error.
- **`add_episode(uuid=…)` LOADS an existing episode; it does not name a new
  one.** It raises when there is none, so a new episode's uuid cannot be
  chosen in advance — read it from the result (`AddEpisodeResults.episode.uuid`,
  recorded on the job row).
- **`EpisodicNode.get_by_group_ids` orders by `uuid`, not by time**
  (upstream #1724), so past the limit "the latest N" is an arbitrary slice.
  "The latest episodes" is our own Cypher ordered by `created_at`
  (`neo4j_reader.latest_episodes`); don't reach for the library's list methods
  to mean "recent".
- **The ontology needs somewhere for every category to go.** Graphiti's
  upstream default entity types had no `Person`, so every person landed as a
  bare `Entity` while orgs typed correctly. Declare high-priority types FIRST
  (the order of `graph_ontology.ENTITY_TYPES`) so they beat the "use as last
  resort" types.
