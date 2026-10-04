# Graphiti as a library: the MCP server leaves, ingestion becomes durable, and the graph's shapes get one owner

> **Status:** open — decided 2026-10-04, not yet built
> **As-built:** `docs/superpowers/specs/2026-10-04-graphiti-library-migration-design.md`

## The problem

Central Command reaches its knowledge graph two ways at once. Extraction and
search go through the stock Graphiti MCP server (image
`zepai/knowledge-graph-mcp:1.1.0-standalone`, graphiti-core 0.30.1) over HTTP
from `integrations/graphiti.py`; curation, provenance and the cockpit go
straight to Neo4j over bolt from `integrations/neo4j_writer.py` and
`neo4j_reader.py`. An audit on 2026-10-04 (upstream source at `mcp-v1.1.0`,
`v0.30.2` and `main`; the GitHub issue tracker; our own tree) found that the
server is the weaker of the two halves and the source of most of what we
carry:

- **Ingestion is acknowledged before it happens, and failures after the
  acknowledgement are logged and dropped.** `add_memory` puts the episode on
  an in-memory `asyncio.Queue` per group and returns "queued"; the worker
  catches every exception and only logs it; a restart loses the queue. There
  is no completion signal and no status tool. Upstream's tracker records the
  same loss others see (#1707: an episode dropped at the output cap after the
  client was told success; #1911: 242 of 686 episodes stored with no edges on
  our image version; #1574: a garbage-collected worker). The verification
  sweep, its absence deadline, the queue-depth estimate and the one-shot
  re-submission in `gateway/graph_auditor.py` all exist to infer, from the
  outside, what the server never reports.
- **Thirteen tools, no curation.** There is no update, merge, move or
  node-delete tool, so every one of those is ours over bolt. The one stock
  tool we lack and want, `delete_episode`, returns only "success".
- **`get_episodes` returns an arbitrary slice.** `EpisodicNode.get_by_group_ids`
  orders by `uuid DESC` (upstream #1724); past the limit, "the latest N" is
  not the latest.
- **Six image deviations, none fixed upstream.** Two are core fixes we
  submitted (#1729 invalidation scope, #1666 reasoning-first dedupe; both
  still open). Two patch the SERVER to do what the library takes as an
  argument (a `/rerank` client; entity types without fields). One patches the
  server's Host allowlist and will stop applying when the server moves to MCP
  SDK 2.x, already on `main`. One patches a client we no longer construct.
- **A locally built image per architecture**, with its base image, apt mirror
  and build step in every install path, air-gapped ones included.

The library removes the server's half of that list. `Graphiti.add_episode` is
awaitable, raises on failure, and returns the episode with the nodes and
edges it produced; the reranker, embedder, LLM client and entity types are
constructor and call arguments; its required dependencies are `neo4j`,
`numpy`, `openai`, `posthog`, `pydantic`, `python-dotenv` and `tenacity`, on
Python 3.10 or later.

## Decisions

### D1. graphiti-core runs in the application process; the MCP server is removed

`graphiti-core==0.30.2` (exact pin) joins the core dependencies and
`requirements.lock`. The `cc-graphiti` deployment, its service, configmap,
secret, image, build scripts and `config.yaml` files are deleted, on both
substrates. Nothing new is invested in the MCP path between now and the
cutover.

The seam keeps its name. `integrations/graphiti.py` stays the module callers
import, with the same read functions and the same result keys, so the agent
tools, the routes and their tests change at the edges only. It splits three
ways:

- `integrations/graphiti_client.py` — builds the one `Graphiti` object.
- `integrations/graphiti.py` — reads (search, episodes, groups, status).
- `integrations/graphiti_ingest.py` — writes (the ingest queue's worker
  body, and later episode removal).

### D2. The client is built explicitly and fails loudly

Every client is passed in; a missing one would make `Graphiti.__init__`
silently build an OpenAI client that wants an OpenAI key.

- **LLM:** `OpenAIGenericClient` — the client the stock server already
  selects for a non-OpenAI URL — with `model=graphiti-llm`, `temperature=0`,
  `max_tokens=4096` passed as the constructor argument (it overrides the
  config field), `structured_output_mode='json_schema'`. `small_model` is not
  set: this client ignores it.
- **Embedder:** `OpenAIEmbedder` with the `cc-embedding` alias at
  `CC_EMBED_DIM`.
- **Cross-encoder:** our own `RerankClient` (D3).
- **Driver:** `Neo4jDriver` on `CC_NEO4J_URL`, built lazily and keyed on the
  running loop, as `neo4j_reader._get_driver` already is. Its constructor
  schedules index DDL when a loop is running; startup also awaits
  `build_indices_and_constraints()` once (idempotent, `IF NOT EXISTS`).
- **Process environment, set before `graphiti_core` is imported:**
  `GRAPHITI_TELEMETRY_ENABLED=false` (the library otherwise posts one
  PostHog event and writes an id file under `~/.cache`), and
  `SEMAPHORE_LIMIT` from a new `CC_GRAPH_SEMAPHORE_LIMIT` (default 3 — the
  value the k3s manifest set). No `OPENAI_*` variable is ever exported into
  the application environment; base URL and key are passed as arguments.
- **One LiteLLM key.** The application's own virtual key gains the
  `graphiti-llm`, `cc-embedding` and reranker aliases. The three
  Graphiti-only keys and their minting are retired. An existing k3s install
  re-mints once.

Never call `Graphiti.search()`: it assigns `limit` on a module-level recipe
object that `add_episode` also reads for its dedupe and invalidation
candidates, so one search call changes extraction for the life of the
process. Every search goes through `search_()` with
`RECIPE.model_copy(deep=True)`.

### D3. The reranker is a class of ours, and parity decides which one

`RerankClient(base_url, api_key, model)` implements
`CrossEncoderClient.rank`: POST `{model, query, documents}` to LiteLLM's
`/rerank`, sort by `relevance_score`, and on any error return the input order
with descending placeholder scores — fail-soft, exactly as the retired
server patch did. It is used when `CC_GRAPH_RERANK_ALIAS` is set (`cc-rerank`
on k3s today).

When it is unset — the single-node substrate today — the client is
upstream's `OpenAIRerankerClient` pointed at LiteLLM with the
`gpt-4.1-nano` alias, which is what the stock factory builds there now.
Release 1 is a parity release; collapsing the two paths into one is a later
decision with its own measurements.

`add_episode` never calls the cross-encoder. Only agent and cockpit search
do.

### D4. The ontology is ten field-less models in our tree

`integrations/graph_ontology.py` defines `Person`, `Preference`,
`Requirement`, `Procedure`, `Location`, `Event`, `Organization`, `Document`,
`Topic`, `Object` — in that order — as Pydantic models with **no fields** and
the docstrings of MCP 1.1.0's built-in models, verbatim. That is what the
running deployment effectively passes today (`GRAPHITI_ENTITY_TYPE_FIELDS=none`):
the extraction prompt's type block is built from `__doc__` alone, and a model
with no fields skips attribute extraction, which is what stops a required
attribute growing without bound.

Docstrings are assigned as explicit strings, not written as class
docstrings, so the prompt bytes do not depend on the interpreter's docstring
handling. A guard test pins the SHA-256 of each. `neo4j_writer.ENTITY_TYPES`
is derived from this module — one list, not two mirrored ones.

### D5. Ingestion is a durable, serial-per-group queue in Postgres

A new table, `graph_ingest_job`, holds one row per approved episode: the
exact payload the Executor composed (name, body, provenance-stamped
`source_description`, group, `reference_time`), a status
(`QUEUED`/`RUNNING`/`DONE`/`FAILED`), attempts, the last error, and on
success the episode uuid and the uuids of the nodes and edges
`AddEpisodeResults` returned.

- **The Executor's contract does not change.** `graph.add_episode` writes the
  verification row and the job row together and returns; approval still
  gates the EPISODE, and extraction still runs afterwards. What changes is
  that "afterwards" is now on the record.
- **The worker** is a background task started in the application lifespan.
  It claims the oldest `QUEUED` job whose group has no `RUNNING` job —
  upstream requires episodes in a group to be added sequentially — and
  awaits `add_episode`. Cross-group concurrency is
  `CC_GRAPH_INGEST_CONCURRENCY`, default 1: one extraction at a time, where
  the stock server ran groups in parallel. One local model slot serves
  everything; parallel extraction only moves the queue into the model's.
- **Failures are classified with the existing taxonomy**
  (`contract/failures.py`). Transient — Neo4j unavailable (the nightly dump
  included), LLM connection errors, 429/5xx — returns the job to `QUEUED`
  with backoff. Permanent — the library's `JSONDecodeError` after its own
  four attempts (a truncated generation), a `ValidationError`, an invalid
  group id — marks it `FAILED` and parks the verification row for the
  operator with the error text. Nothing is acknowledged and then dropped.
- **Crash recovery uses the marker.** `add_episode` writes the episode, its
  entities, mentions and facts in ONE managed transaction, so an episode
  either exists whole or not at all. At startup a `RUNNING` job whose
  `proposal=<id>` marker is found on an Episodic node is `DONE`; otherwise
  it is `QUEUED` again. The marker stays in `source_description` — it is
  provenance, and now also the idempotency key.
- **Verification stops guessing.** The sweep audits a row when its job is
  `DONE`, by the recorded episode uuid. The absence deadline, the
  queue-depth estimate, `_resubmit` and `resubmitted_at` are retired. The
  mechanical checks, the judgment and the three-way operator verdict are
  unchanged.
- **Cutover.** Verification rows still `PENDING` with no episode at the
  first start on the new release are enqueued once from their approved
  proposals — the same reconstruction `_resubmit` performed.

A new episode's uuid cannot be chosen in advance: `add_episode(uuid=...)`
LOADS an existing episode and raises if there is none. The id is read from
the result.

### D6. Reads move in-process, and the import graph still draws the boundary

- Search uses `search_()` with a deep-copied recipe and the limit set on the
  copy (the server sliced a ten-result recipe, so asking for more than ten
  returned ten): the cross-encoder recipes when a reranker is configured and
  no centre node is given, node-distance recipes with one. Results keep the
  keys the server returned.
- "The latest N episodes" is our own Cypher ordered by `created_at`.
- Status is a Neo4j ping; the Systems page and the selfcheck row read that.
- **Trust boundary.** `runtime/` may import `integrations/graphiti` (reads)
  and nothing that can write: not `graphiti_ingest`, not `graphiti_client`,
  not `neo4j_writer`. The existing import-graph guard gains those names. The
  statement "the runtime tier holds no bolt" is retired in the documents
  that make it — agents' reads now run over bolt in-process — and replaced
  by the rule that is actually enforced: the runtime tier holds no write
  path.

Search filters (validity dates, entity types, centre node) are exposed to
agents in release 2. Date filters use a single OR group: the library names
its filter parameters by position inside the AND group only, so several OR
groups overwrite each other.

### D7. The two core fixes are patch files applied at install

`deploy/graphiti-patches/` holds `1729-invalidation-scope.patch` and
`1666-reasoning-first-dedupe.patch` (both apply cleanly to 0.30.2; the two
target files are byte-identical between 0.30.1 and 0.30.2). A fork was
rejected: an air-gapped site cannot be assumed to reach it.

`scripts/apply_graphiti_patches.py` applies them with the environment's own
Python — `patch` is not guaranteed on every substrate:

- exact context matching, no fuzz: the version is pinned, so a hunk that
  does not match is a real finding;
- idempotent: an already-patched file is recognised and skipped;
- atomic: write a temporary file and `os.replace` it, never write in place —
  `uv` hardlinks installed files from its cache, and an in-place write would
  patch the cache and every other environment sharing it;
- loud: a file that is neither pristine nor patched fails the step.

It runs after every dependency install: the k3s setup and updater
`rebuild()` (rollback included), a `steps.tsv` row after `app/install` on
the single-node substrate, and by hand in a development environment
(documented beside `pip install`). Because a hand step can be forgotten, the
running system checks too: the selfcheck gains a row, and the ingest worker
refuses to start extraction when the patch sentinels are absent from the
installed package. Reads are unaffected.

### D8. Hand-made writes keep our Cypher, pinned to the shape extraction writes

graphiti-core has two write paths that disagree. The bulk path
(`add_nodes_and_edges_bulk`, what `add_episode` uses) stores a `labels`
property and `source_node_uuid`/`target_node_uuid`; the model `save()`
methods store no `labels` property and name the endpoints
`source_uuid`/`target_uuid`. Every extracted fact in a graph has the bulk
shape. Moving curation onto `save()` would therefore introduce a second
shape, not remove one.

So: the canonical shape is the bulk path's. `neo4j_writer` keeps its own
Cypher for create, update, delete, merge and rescope — upstream has no
update, merge or move at all — and a guard test compares the property sets
it writes against the installed package's bulk queries, so an upgrade that
changes them fails the suite instead of the graph. The reader takes real
labels and relationship endpoints as truth, as upstream's own reads do,
leaving the duplicated properties write-only compatibility. Embeddings are
generated through the library's embedder and stored with
`db.create.setNodeVectorProperty` / `setRelationshipVectorProperty`, as the
bulk path does. Whether `embedding_model`/`embedding_dimensions` — which
upstream's reads surface as entity attributes — stay on nodes is settled in
release 2 against what `reembed_graph.py` needs.

### D9. `graph.delete_episode`, through the same queue

A new gated capability. The proposal carries, captured at propose time, what
the deletion will remove: the episode, the facts it was FIRST to create
(`edge.episodes[0]`), and the entities no other episode mentions — upstream's
rule in `Graphiti.remove_episode`, computed by a read. The Executor
recomputes at execute time and refuses if the set has changed since the
operator saw it.

The removal itself is `remove_episode`, run as a job on the group's ingest
queue so it can never race an extraction in the same group, followed by one
cleanup upstream omits: the dead uuid is removed from the `episodes` list of
every surviving fact. "Rework an episode" is this deletion plus an ordinary
`graph.add_episode` proposal with the corrected text; an episode is never
edited in place, because its text is what the operator approved.

`remove_episode` trusts the ORDER of `edge.episodes`. Before this capability
ships, the live graph is checked for facts whose list is empty or was
reordered by a past one-off script, and the cockpit's graph audit gains that
check.

### D10. Removing the server from both substrates

- **k3s:** delete the deployment, service, configmap and secret from the
  manifests and add them to `removed.txt` (`kubectl apply` never prunes);
  drop the image row, the build step and the health checks; remove the
  scale-down choreography from `backup.sh`; widen the application key's
  scope in `mint-keys.sh`.
- **The first update across the boundary** is run by the OLD updater, which
  sees the image's inputs changed and calls a build script the new tag no
  longer has. A stub `deploy/k3s/build-graphiti-image.sh` that exits 0 ships
  for one release and is deleted in the next.
- **Single node:** delete the compose service, the base-image row, the
  build script and the `fetch`/`up-stack` rows; regenerate the checklist.
  One fewer image to mirror, build and carry.
- **Air-gap:** graphiti-core and its dependencies join the lock and the
  PyPI-mirror completeness story in `deploy/AIRGAP.md`.
- **Rollback** re-applies the previous manifests; the previous image is left
  on the nodes for that reason and is not garbage-collected by this release.

### D11. Two releases

1. **Parity cutover** — D1 to D7 and D10. Same behaviour, a durable queue,
   no server. Acceptance: the suite; a prompt-parity test (the entity-types
   block rendered from our models equals the bytes the server's models
   produced); and one live episode through approval, extraction, verify and
   a search on a non-production graph.
2. **What the library makes possible** — D8, D9, search filters for agents,
   the reader's canonical switch, the refreshed vendored documentation
   (fetched from the graphiti repository at our pinned tag; the present
   snapshot is mostly Zep Cloud pages).

## Not adopted

- **Communities.** No evidence of value (the Zep paper has no ablation), no
  search path that returns them outside the library's combined recipes, one
  LLM call per member, and a clustering step with a long-open
  non-termination bug on hub-and-spoke graphs (upstream #402, #1400).
- **Sagas.** New in 2026, summaries "not yet exercised end-to-end" by
  upstream's own account when exposed, a pointer-corruption bug fixed only
  recently, and no effect on extraction. A rolling thread summary is also
  close to the state the graph is ruled not to hold. Revisit with a concrete
  thread to track.
- **`add_triplet`.** It creates bare nodes — no type, summary, validity or
  provenance — and runs the unpatched resolve path.
- **Message and JSON sources, bulk ingest.** Episodes are prose an agent
  wrote and the operator approved; nothing here ingests transcripts or
  records, and one proposal is one episode by decision.
- **Per-episode extraction instructions and excluded entity types.**
  Reachable after the cutover; a benchmarked experiment, not part of it.
- **A fork of graphiti-core.** See D7.
- **Any further work on the MCP path**, the server's `delete_episode` tool
  included.
