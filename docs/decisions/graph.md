# Graphiti / Neo4j — decisions

Governs graph identity, embedding, live-write gating, temporal facts, and
the graph ontology. See `docs/decisions/README.md` for the entry format and
how to add one.

### DL-051 — A Graphiti entity and edge each store their identity twice

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**A Graphiti entity and edge each store their identity TWICE, and a hand-made write must set both halves.**"
- **Why:** Not recorded beyond the stated mechanism: types live in real Neo4j
  labels AND an `n.labels` property; edge endpoints live in the relationship
  AND in `source_node_uuid`/`target_node_uuid` properties, and the cockpit
  draws from the PROPERTIES. Neo4j cannot repoint a relationship in place,
  so a repoint is copy-then-delete.
- **Enforced:** discipline only — no guard test located this pass
- **Source:** .claude/rules/graph.md

### DL-052 — Every write re-embeds at exactly 1024 dimensions; a mis-sized vector is dropped

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**a node with no `name_embedding` is invisible to the semantic half of hybrid search**"
- **Why:** Not recorded beyond the stated mechanism: still turning up in
  keyword hits, a node with no embedding is invisible to semantic search, so
  every write re-embeds through the `cc-embedding` alias at exactly 1024
  dimensions; nothing schema-side enforces the width, so a mis-sized vector
  is dropped rather than stored.
- **Enforced:** discipline only — no guard test located this pass (an embedding-dimension mismatch would surface as a runtime error, not a named test)
- **Source:** .claude/rules/graph.md

### DL-053 — The suite may not write to the live graph without CC_LIVE_GRAPH_TESTS=1

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**The suite may not write to the live graph**"
- **Why:** Not recorded beyond the stated invariant: one leftover scratch
  entity is enough to fail an unrelated live read test.
- **Enforced:** test: `tests/conftest.py::no_live_graph_writes` (checks `os.getenv("CC_LIVE_GRAPH_TESTS") != "1"` before allowing `add_memory`), used across `tests/test_graph_curation.py`, `tests/test_graph_delta_live.py`
- **Source:** .claude/rules/graph.md

### DL-054 — An async bolt driver cached at module scope is loop-bound

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**an async bolt driver cached at module scope is loop-bound**"
- **Why:** Not recorded beyond the stated mechanism:
  `neo4j_reader._get_driver` keys its cache on the running loop, and the
  writer shares that driver because access mode is a property of the
  SESSION, never of the driver.
- **Enforced:** discipline only — no guard test located this pass
- **Source:** .claude/rules/graph.md

### DL-055 — Graphiti's extraction needs the bridged LiteLLM alias

- **Status:** active
- **Date:** 2026-09-19
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**Graphiti's extraction needs the BRIDGED LiteLLM alias.**"
- **Why:** graphiti_core drives extraction through the Responses API with no
  chat-completions fallback; an OpenAI-compatible backend without
  `/v1/responses` silently drops `text.format` json_schema and every
  extraction fails. Registering the model as
  `openai/chat_completions/<model>` forces LiteLLM's Responses→chat bridge.
  The bridge only answers Responses calls — MCP server 1.1.0 picks the
  chat-completions client for any non-OpenAI LLM URL, and a plain chat call
  to the bridged alias 404s (2026-09-19).
- **Enforced:** discipline only — `GRAPHITI_OPENAI_CLIENT=responses` (`cc-openai-client-switch.patch`) keeps the Responses client; no guard test located this pass
- **Source:** .claude/rules/graph.md; MEMORY.md v2.38.0 entry

### DL-056 — Graphiti will retire a fact it merely recognises; the guard is the invalidation scope, not the model

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**Graphiti will retire a fact it merely RECOGNISES, and the guard is not the model.**"
- **Why:** Upstream's `resolve_extracted_edges` offers a whole-group semantic
  search as invalidation candidates (empty `SearchFilters()`); upstream
  patch #1729 is carried under `deploy/pi/graphiti/patches/` (with #1666,
  reasoning-first dedupe — drop both when they merge upstream).
- **Enforced:** test: `tests/test_graph_verification.py` exercises related invalidation logic; not pinned to this exact upstream-patch invariant
- **Source:** .claude/rules/graph.md; docs/superpowers/specs/2026-08-19-graph-verification-auditor-design.md (adjacent)

### DL-057 — graph.add_episode requires reference_time; no defaults, anywhere

- **Status:** active
- **Date:** 2026-09-19
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**Graphiti never assumes an episode's time.**"
- **Why:** CHANGELOG `2026-09-19 — v2.38.0: an episode's time is stated,
  never assumed`: under MCP 1.0.2, Graphiti's own fallback (the moment it
  PROCESSED the episode) was the only behaviour, and 44% of the live graph's
  edges read "became true when ingested" (2026-09-19). A default IS the
  assumption, so `reference_time` is required in both tiers with no
  fallback anywhere in the code.
- **Enforced:** test: `tests/test_proposal_args.py::test_executor_normalises_the_reference_time_and_refuses_words` (and the surrounding `graph.add_episode` shape tests in the same file)
- **Source:** CHANGELOG v2.38.0

### DL-058 — Episodes name the operator; "the operator" is not a graph subject

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**Episodes name the operator; "the operator" is not a graph subject.**"
- **Why:** Not recorded beyond the stated mechanism: the Person ontology
  refuses a role as a name, so an episode written "the operator is
  subscribed to X" lands with no subscriber or spawns a bare `operator`
  entity; the graph-propose pack renders the configured name into its rule.
- **Enforced:** test: `tests/test_context_overflow.py::test_graph_propose_notes_tell_the_agent_to_name_the_operator`
- **Source:** .claude/rules/graph.md; CHANGELOG v2.36.0

### DL-059 — An edge with expired_at is not necessarily a retired fact

- **Status:** active
- **Date:** 2026-09-15
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**An edge with `expired_at` is not necessarily a retired fact.**"
- **Why:** CHANGELOG-adjacent incident (v2.35.0): Graphiti expires a NEW edge
  at birth whenever the extractor supplied an `invalid_at` — a deadline, a
  "through <date>" range, even a future date — which is upstream intent
  (its own test asserts it). A retirement is an edge that PRE-DATES the
  episode, guarded by `r.created_at < e.created_at`; 89 of 90 flagged
  entries were reader artifacts of a fixed attribution window, not real
  retirements (2026-09-15).
- **Enforced:** discipline only — not independently pinned to a named test this pass
- **Source:** .claude/rules/graph.md

### DL-060 — The ontology needs somewhere for every category; declare high-priority types first

- **Status:** active
- **Date:** undated
- **Rule:** [.claude/rules/graph.md](../../.claude/rules/graph.md) — "**The ontology needs somewhere for every category to go.**"
- **Why:** Graphiti's upstream default entity types had no `Person`, so every
  person landed as a bare `Entity` while orgs typed correctly.
  High-priority types must be declared FIRST so they beat the "use as last
  resort" types.
- **Enforced:** discipline only — no guard test located this pass
- **Source:** .claude/rules/graph.md

### DL-061 — The curator can see the patient: graph.add_episode takes for_agent

- **Status:** active
- **Date:** 2026-09-01
- **Rule:** (recorded here) `graph.add_episode` takes a `for_agent` argument
  (private scope only): a rule about how a teammate works lands in THAT
  teammate's partition rather than the proposer's own, and the target is
  visible on the proposal and must be an active roster member.
- **Why:** CHANGELOG `2026-09-01 — v2.20.0: the curator can see the patient`:
  onboarding recorded eight teammates' operating doctrines through the EA,
  and `graph.add_episode`'s `private` scope could only mean the proposer's
  own partition, so every one of them landed in `central_command_ea`; asked
  to fix it, the graph curator could neither read that partition nor
  propose a move. `graph.rescope_episode` was added alongside as the gated
  curation capability to move an episode between partitions.
- **Enforced:** code structure only — `for_agent` argument on `graph.add_episode`; no dedicated test confirmed by name this pass
- **Source:** CHANGELOG v2.20.0

### DL-062 — An embedding is never served from memory

- **Status:** active
- **Date:** 2026-09-01
- **Rule:** (recorded here) The graph embedder always re-fetches/recomputes
  an embedding rather than serving it from a cache that could return a stale
  vector.
- **Why:** CHANGELOG `2026-09-01 — v2.19.1: an embedding is never served from
  memory`: LiteLLM's redis response cache no longer covers `/v1/embeddings`
  in either direction.
- **Enforced:** code structure only — LiteLLM cache configuration; no dedicated test confirmed by name this pass
- **Source:** CHANGELOG v2.19.1
