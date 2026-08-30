# The graph in operation

## Your surface

You hold exactly **two** graph tools, both ungated reads, both from the
`graph-read` pack. Nothing else in this document is callable by you — the rest
are internal client functions.

- `search_knowledge_graph(query)` — relationships. Up to 25 hits, rendered
  `- <fact> (<RELATION_TYPE>) [valid from …]` /
  `- <fact> (<RELATION_TYPE>) [SUPERSEDED as of …]`. The endpoints come back as
  UUIDs, not names, so the relation type is all the naming you get.
- `search_knowledge_graph_entities(query)` — entities. Up to 15, rendered
  `- <name>: <summary>`. Ask this when the question is about a *thing* ("what do
  you know about X"); a fact search alone returns fragments.

There is **no** tool to list episodes and **no** tool to check graph health —
`get_episodes` feeds the cockpit's memory panel and `get_status` is
infrastructure; neither is reachable from a turn. If you need either, declare
the gap.

**Writes are not a tool.** Committing knowledge is a `graph.add_episode`
proposal you draft, which the Executor performs after the operator approves.
The runtime tier **cannot** write even if approved by mistake — that is the
trust boundary, not a convention.

## Degradation — a down graph must not wedge you

`search_knowledge_graph` is **best-effort by necessity**: one bounded retry,
and any transport failure returns

```
knowledge graph unavailable (<ErrorType>); proceed without it
```

An empty result returns `no relevant facts in the knowledge graph`.

**These two are different answers and you must not conflate them.**
"Unavailable" is *no information*; "no relevant facts" is *evidence of
absence*, weakly. Never report "the team has no knowledge of X" on the back of
an unavailable graph — declare that the check could not be made.

## Transport

The client speaks MCP streamable HTTP to `CC_GRAPHITI_MCP_URL`
(`127.0.0.1:8000/mcp`), a fresh session per call (initialize → initialized →
tools/call). Reads time out at 30s, `add_episode` at 60s. Central Command has
**zero direct Neo4j references** — the graph is reached solely through
Graphiti's MCP endpoint, and `cc-neo4j` is ClusterIP-only.

## Latency you should expect

- **~56 seconds per episode** for extraction on this deployment (async
  background work), since Graphiti's LLM moved to the local Qwen on 2026-08-01.
  On the previous cloud model it was a few seconds. This is acceptable for
  queue processing and worth knowing before you assert a write "did not land".

## The one configuration that must not be "simplified"

Graphiti's entity extraction runs against the LiteLLM alias
**`graphiti-llm` = `openai/chat_completions/qwen3.8-27b`**, not
`cc-default`.

The `openai/chat_completions/` prefix is **the whole fix, and it is not
cosmetic**: graphiti_core's `OpenAIClient` drives extraction through the
**Responses API** (`client.responses.parse`) whenever a `response_model` is
present — which is always, for extraction — with **no chat-completions
fallback**. llama.cpp does not implement `/v1/responses`, and LiteLLM treats a
plain `openai/…` model as *natively* supporting it, so the request passes
through and the `text.format` json_schema is **silently dropped**. The model
then answers with prose and **every extraction fails**. The
`openai/chat_completions/` prefix forces LiteLLM's Responses→chat bridge, which
converts `text.format` into a real `response_format: json_schema` that
llama.cpp enforces via GBNF.

If extraction starts producing prose or nothing at all, this alias is the first
thing to check — and re-pointing it at a bare `openai/…` model is how it
breaks.

## The embedder is not optional

Graphiti has **no embedding fallback** — OpenAI cannot substitute because the
dimensions differ. So an outage of the local embedder
(role alias `cc-embedding`, over `qwen3-embedding-local`) is a graph
**write outage**, not a degraded
capability. The LLM being down is the degradable case; the embedder being down
is not. Report them differently.

## Changing the embedding model (a migration, never a swap)

`cc-embedding` being re-pointable in the LiteLLM UI does NOT make an embedder
change a config change. Vectors from two models are not comparable — even at
the same width — so a re-point without a re-embed leaves hybrid search
returning plausible-looking nonsense with no error anywhere. Investigated
2026-08-30 against the live graph and graphiti_core source:

- Embeddings live as plain float-array properties: `Entity.name_embedding`
  and `RELATES_TO.fact_embedding` (community nodes carry `name_embedding`
  too, when any exist).
- **There is no Neo4j vector index.** graphiti_core builds only
  RANGE/FULLTEXT/LOOKUP indexes and scores similarity per-row with the
  scalar `vector.similarity.cosine()` in Cypher. So there is no
  index-drop/rebuild step, and nothing at the database level enforces the
  dimension — the enforcement is three hand-synced declarations that MUST
  change together: `deploy/pi/graphiti/config.yaml` (`embedder.model` +
  `dimensions`), the app's `CC_EMBED_ALIAS`/`CC_EMBED_DIM` (config.py), and
  `EMBEDDER_MODEL`/`EMBEDDER_DIMENSIONS` for the migration script.

**Same-dimension swap (new model, still 1024d):**
1. Re-point `cc-embedding` in the LiteLLM UI (or register the new model and
   re-point the role at it).
2. Restart/redeploy `cc-graphiti` so extraction picks it up. No name or
   dimension declarations change.
3. Re-embed everything — NOT optional:
   `python3 scripts/oneoff/reembed_graph.py` (dry run) → `--apply` →
   `--verify`. On the Pi; needs `NEO4J_PASSWORD` and `EMBEDDER_API_KEY` set
   (Neo4j HTTP is loopback-only there).

**Different-dimension swap — all of the above, plus:**
- Change `dimensions:` in `graphiti/config.yaml`, `CC_EMBED_DIM` in the
  app's `.env` (setup.sh REFUSES a drifted value until you clear it
  deliberately — that refusal is the guard, respect it), and
  `EMBEDDER_DIMENSIONS` for the script, all in the same change.
- Mixed-width vectors coexist on the same property mid-migration and
  `vector.similarity.cosine()` behavior on a width mismatch is unverified —
  prefer a stop-writes-then-migrate window (dispatch off, no curation)
  rather than migrating under load.

**Stamps:** every embedding write — the reembed script AND the curation
writer (`neo4j_writer`) since v2.8.1 — records `embedding_model` +
`embedding_dimensions` beside the vector; the script keys its resumability
and `--verify` on them. Rows written before v2.8.1 are unstamped, so the
first `--apply` after this release re-embeds them once and stamps them;
from then on `--verify` is meaningful as a standing consistency check.

## Where knowledge comes from

Sources → the **work ledger** → dispatch → a steward agent proposes → the gate
→ the Executor writes. One path, the existing one, widened: the ledger's
`unique(message_id)` + `on conflict do nothing` + `for update skip locked` is
the same idempotent-consumer shape whether the work item is an email, a
document, or a change event. Don't imagine a second ingestion route.

## The graph's content is disposable during build

Judge the **mechanism**, not the facts currently in it — graph content gets
wiped before real use, scoped to the `central_command` group. Do not build
reasoning that depends on a specific stored fact still being there.
