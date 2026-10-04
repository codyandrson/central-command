# The graph in operation

## Your surface

Every holder of the `graph-read` pack has two ungated reads. Every line they
render leads with the item's **uuid** — what `graph.create_edge` endpoints,
`graph.update_node` / `update_edge` / `delete_*` and `graph.merge_nodes` take.
Never guess a uuid; read it here.

- `search_knowledge_graph(query)` — relationships. Up to 25 hits, rendered
  `- <uuid> | <fact> (<RELATION_TYPE>) [valid from …]` /
  `- <uuid> | <fact> (<RELATION_TYPE>) [SUPERSEDED as of …]`. The endpoints are
  not named, so the relation type is all the naming you get.
- `search_knowledge_graph_entities(query)` — entities. Up to 15, rendered
  `- <uuid> | <name>: <summary>`. Ask this when the question is about a *thing*
  ("what do you know about X"); a fact search alone returns fragments.

Holders of `graph-curate` (the curator) have three more, which see EVERY
partition: `list_graph_groups`, `list_graph_group_episodes(group_id)` (episode
uuids, for `graph.rescope_episode`) and `search_graph_group(group_id, query)`
(facts and entities inside one group, uuids leading).

`search_tools` searches only skill reference material — a miss there never
means a tool above is missing. Nothing else in this document is callable by
you — the rest are internal client functions.

There is **no** tool to list episodes and **no** tool to check graph health —
`get_episodes` feeds the cockpit's memory panel and `get_status` (a Neo4j ping)
is infrastructure; neither is reachable from a turn. If you need either, declare
the gap.

**Writes are not a tool.** Committing knowledge is a `graph.add_episode`
proposal you draft, which the Executor performs after the operator approves.
The runtime tier **cannot** write even if approved by mistake — that is the
trust boundary, not a convention.

## Degradation — a down graph must not wedge you

`search_knowledge_graph` is **best-effort by necessity**: one bounded retry,
and any failure to reach the graph (Neo4j or the model proxy) returns

```
knowledge graph unavailable (<ErrorType>); proceed without it
```

An empty result returns `no relevant facts in the knowledge graph`.

**These two are different answers and you must not conflate them.**
"Unavailable" is *no information*; "no relevant facts" is *evidence of
absence*, weakly. Never report "the team has no knowledge of X" on the back of
an unavailable graph — declare that the check could not be made.

## Transport

There is no separate graph service, port or health endpoint. `graphiti-core`
runs inside the application process: reads (search, episode listing) go over
bolt to Neo4j (`CC_NEO4J_URL`), with the model and embedder reached through the
LiteLLM proxy. "Status" is a Neo4j ping. The runtime tier holds the read path
only; no write path is importable from an agent turn.

## Ingestion after approval — the durable queue

The Executor's `graph.add_episode` writes the verification row and a job in the
`graph_ingest_job` table together, then returns. A background worker extracts
each job with graphiti-core's `add_episode`:

- **Serial per group, groups side by side.** Episodes of one group run strictly
  in order (the library requires it); different groups do not wait for each
  other.
- **Transient failures retry.** Neo4j down (the nightly dump included), the
  proxy unreachable, a 429 or 5xx: the job goes back to `QUEUED` with backoff.
- **Permanent failures are parked, not dropped.** A truncated generation, a
  validation error or an invalid group id marks the job `FAILED` and parks its
  verification row for the operator with the error text, visible in Verify.
- **Nothing is acknowledged and then lost.** The queue is in Postgres, so a
  restart does not drop it; a job interrupted mid-run is re-queued unless its
  episode (found by the `proposal=<id>` marker in `source_description`) already
  landed.

So "queued" is a state on a record, not a promise from a server. Never
re-propose an episode because a search came back empty; look at the job, not at
the graph, to learn whether it ran.

## Latency you should expect

- **~56 seconds per episode** for extraction on this deployment (background
  work), since the extraction LLM moved to the local Qwen on 2026-08-01. On the
  previous cloud model it was a few seconds. This is acceptable for queue
  processing and worth knowing before you assert a write "did not land".

## The one configuration that must not be "simplified"

Graphiti's entity extraction runs against the LiteLLM alias
**`graphiti-llm`**, not `cc-default`. The application's own graphiti-core
client (`OpenAIGenericClient`, structured output as `json_schema`) calls it
over **chat-completions**; it is that alias's only caller.

The alias must therefore be registered as a plain `openai/<model>` — the same
shape as `cc-default`. The old `openai/chat_completions/<model>` Responses→chat
bridge prefix is **wrong now**: a chat-completions call to the bridged alias
sends `chat_completions/<model>` upstream, which the backend 404s, and every
extraction fails.

If extraction starts failing outright, this alias is the first thing to check.
The `json_schema` response format also has to be enforced by the model backend;
a model or gateway that silently drops it makes the model answer in prose, and
those jobs fail as permanent (a decode or validation error) rather than
retrying.

## Every episode carries its reference time

`graph.add_episode` requires `reference_time` (the instant the source material
is from), the Executor refuses a proposal without a real ISO-8601 instant, and
the job row stores it — there is no default anywhere. Graphiti anchors every
present-tense fact to it; its own fallback ("when I processed this") is what
produced ingestion-dated facts under the old server and is never relied on.

## The embedder is not optional

Graphiti has **no embedding fallback** — OpenAI cannot substitute because the
dimensions differ. So an outage of the local embedder
(role alias `cc-embedding`, over `qwen3-embedding-local`) is a graph
**write outage**, not a degraded
capability: ingest jobs cannot complete until it is back. The LLM being down is the degradable case; the embedder being down
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
  dimension — the enforcement is two hand-synced declarations that MUST
  change together: the app's `CC_EMBED_ALIAS`/`CC_EMBED_DIM` (config.py,
  which the in-process graphiti-core embedder is built from), and
  `EMBEDDER_MODEL`/`EMBEDDER_DIMENSIONS` for the migration script.

**Same-dimension swap (new model, still 1024d):**
1. Re-point `cc-embedding` in the LiteLLM UI (or register the new model and
   re-point the role at it). The application sends only the alias name, so no
   name or dimension declaration changes.
2. Re-embed everything — NOT optional:
   `python3 scripts/oneoff/reembed_graph.py` (dry run) → `--apply` →
   `--verify`. On the host the app runs on; needs `NEO4J_PASSWORD` and
   `EMBEDDER_API_KEY` set in the shell — the latter is any LiteLLM key that
   reaches `cc-embedding` (the app's `CC_LLM_API_KEY` does; the retired
   Graphiti server's key of that name is gone). Neo4j HTTP is loopback-only.

**Different-dimension swap — all of the above, plus:**
- Change `CC_EMBED_DIM` in the app's `.env` (setup.sh REFUSES a drifted
  value until you clear it deliberately — that refusal is the guard, respect
  it) and `EMBEDDER_DIMENSIONS` for the script, in the same change; the
  application builds its embedder once per process, so it picks the new width
  up on its next restart.
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
→ the Executor enqueues → the ingest worker extracts. One path, the existing one, widened: the ledger's
`unique(message_id)` + `on conflict do nothing` + `for update skip locked` is
the same idempotent-consumer shape whether the work item is an email, a
document, or a change event. Don't imagine a second ingestion route.

## The graph's content is disposable during build

Judge the **mechanism**, not the facts currently in it — graph content gets
wiped before real use, scoped to the `central_command` group. Do not build
reasoning that depends on a specific stored fact still being there.
