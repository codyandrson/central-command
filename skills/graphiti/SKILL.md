---
name: Graphiti
description: The team's shared knowledge graph — episodes vs entities vs facts, the bi-temporal model and what SUPERSEDED means, group tenanting (read main + central_command, write central_command only), what makes an episode worth committing, and the confidence and citation duties that ride every extraction. Load before searching the graph, judging whether a fact is durable, or proposing graph.add_episode.
---

# Graphiti

Central Command's shared memory is a **Graphiti** temporal knowledge graph over
Neo4j, reached through Graphiti's MCP server (`central_command/integrations/
graphiti.py`). There is **no private agent memory** (D11) — every agent reads
the same store, so what you commit becomes what the whole team believes.

**Reading is layered and ungated. Writing is gated.** You search freely; a graph
write is a `graph.add_episode` action inside a proposal the operator approves
before the Executor commits it.

## Load this skill when

- You are deciding **whether something belongs in the graph at all** —
  `what-belongs` (and the layer table: not everything true is a graph fact).
- You are about to propose **`graph.add_episode`** — `episodes-entities-facts`
  for what an episode actually becomes, `what-belongs` for the shape.
- A search returned something **contradicting** what you observed, or marked
  `SUPERSEDED` — `episodes-entities-facts` and `steward-duties`.
- You are stating **confidence** or citing the document — `steward-duties`.
- Something is **failing** — `operations`. The extraction pipeline here has one
  configuration that is load-bearing and non-obvious.

## Four things to hold even without loading a reference

1. **An episode is an INGESTION EVENT, not a fact.** You submit an episode;
   Graphiti extracts entities and relationships from it asynchronously. You are
   writing the source, and the graph derives the rest.
2. **Never paste source text.** The episode body is a distilled claim of 1–3
   self-contained sentences naming people, keys and dates. The graph stores
   refs, not bodies (D13).
3. **Reads span `main` + `central_command`; writes land only in `central_command`.**
   The inherited homelab graph is never polluted by this system's proposals.
4. **Be stricter about relationships than about things.** Naming an entity the
   document names is usually safe. Asserting that A blocks B, C owns D, or X
   caused Y is where extraction breaks — a relationship you inferred rather
   than read is `low` confidence at best.

## References

- `episodes-entities-facts` — the three layers, the bi-temporal model,
  `valid_at` / `invalid_at`, and group tenanting.
- `what-belongs` — which knowledge layer a given fact belongs in, and what
  makes an episode worth committing.
- `steward-duties` — confidence, citation, contradictions, and when the right
  answer is plain text or a question.
- `operations` — the tool surface, degradation behaviour, and the extraction
  configuration that must not be "simplified".
