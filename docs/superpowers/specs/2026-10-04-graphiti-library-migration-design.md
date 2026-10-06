# Graphiti as a library: the MCP server leaves, ingestion becomes durable, and the graph's shapes get one owner

> **Status:** partial — release 1 (D1–D7, D10) shipped in v2.60.0 and release 2 (D8, D9, agent search filters, the reader's canonical switch, D10's deferred deletions) in v2.61.0 (both 2026-10-05); D3 rebuilt as the reranker decision in v2.62.0; the refreshed vendored graphiti documentation (D11) remains
> **As-built:** `central_command/integrations/graphiti_client.py`, `central_command/integrations/graphiti.py`, `central_command/integrations/graphiti_ingest.py`, `central_command/integrations/graph_ontology.py`, `central_command/integrations/graphiti_patches.py`, `central_command/integrations/neo4j_reader.py`, `central_command/integrations/neo4j_writer.py`, `scripts/apply_graphiti_patches.py`, `deploy/graphiti-patches/`, `central_command/gateway/graph_auditor.py`, `central_command/gateway/executor.py`, `central_command/runtime/tools.py`, `central_command/db/schema.sql`, `deploy/k3s/cc-update.sh`, `deploy/k3s/mint-keys.sh`, `deploy/k3s/removed.txt`, `deploy/single/steps.tsv`, `web/src/features/graph/EpisodeDeleteDialog.tsx`, `tests/test_graph_ingest.py`, `tests/test_graphiti_client.py`, `tests/test_graphiti_server_boundary.py`, `tests/test_k3s_update_reconcile.py`, `tests/test_graph_delete_episode.py`, `tests/test_graph_write_shape.py`, `tests/test_graph_search_filters.py`, `tests/test_graph_rerank.py`, `tests/test_single_rerank_probe.py`, `scripts/graph_rerank_bench.py`

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
- **Cross-encoder:** ours — `RerankClient`, `ChatRerankClient` or `NoReranker` (D3).
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

### D3. The reranker: one optional alias, the best kind the environment has, and a failure raises

*Rewritten in v2.62.0 to the decision as built. The release-1 text (a
`RerankClient` for `/rerank`, upstream's `OpenAIRerankerClient` on a
`gpt-4.1-nano` alias otherwise, "parity decides") is superseded; what it
found is kept below because it is why the decision was needed.*

**What release 1 found.** The stock server's two search tools never called a
reranker without our patch — they ran the RRF recipes. So on the single-node
profile the client built upstream's `OpenAIRerankerClient` on the
`gpt-4.1-nano` alias because the constructor requires a cross-encoder, and
search never asked it: **single node had no reranking at all**, while
requiring the operator to map, and setup to probe, an alias nothing called.
k3s reranked through `CC_GRAPH_RERANK_ALIAS=cc-rerank`, a dedicated model,
and its client swallowed every error into the input order.

**The measurement (2026-10-05).** A synthetic, deliberately confusable corpus
(150 episodes → 90 entities, ~345 facts), 72 known-answer fact questions, 8
results per search:

| condition | top-1 | top-3 | top-8 | MRR | median search |
|---|---|---|---|---|---|
| no reranker (RRF recipe) | 37.5% | 75.0% | 97.2% | 0.577 | 0.19 s |
| chat model as reranker (upstream's client on a local 27B chat model, thinking off, logprobs on) | 66.7% | 97.2% | 100% | 0.815 | 6.6 s |
| dedicated reranker via LiteLLM `/rerank` | 75.0% | 94.4% | 100% | 0.850 | 0.50 s |

Dedicated against chat: MRR difference 0.035, 95% CI −0.035…0.10 — not
significant. Both far better than none (CI of the gain ≈ 0.14…0.37). Entity
search was at ceiling in every condition. Found while measuring: the chat
reranker asks ONE question per candidate, `max_tokens=1`, `logprobs`,
`top_logprobs=2`, scores P("True"), and sends a `logit_bias` for two
OpenAI-tokenizer ids; a model that THINKS first emits a reasoning token
("We") as its one token, so every score is garbage; the edge recipe reranks
the top 2 × limit fused candidates.

**The decision (the operator's).** Every deployment gets the best reranking
its environment can provide, through ONE alias the operator maps in LiteLLM:
a dedicated reranker if one exists; otherwise an ordinary chat model used as
a reranker (nearly the same quality, slower); otherwise none.

- **`CC_GRAPH_RERANK_ALIAS`** (`cc-rerank`, optional on both substrates) and
  **`CC_GRAPH_RERANK_KIND`**: `rerank` → `RerankClient` (LiteLLM `/rerank`);
  `chat` → `ChatRerankClient`; empty with an alias set → `rerank`, which is
  what every install meant before the setting existed (an existing k3s
  deployment behaves as before with no `.env` change). An unknown kind is
  `GraphitiNotConfigured` naming the setting. No alias: the RRF recipes, and
  the constructor gets `NoReranker`, which raises if anything calls it.
  `RERANK_FALLBACK_ALIAS` and the built-but-never-called upstream client are
  deleted.
- **`ChatRerankClient`** asks upstream's question VERBATIM (a test renders
  upstream's f-string from the installed source and compares, so the
  measurement keeps describing what we send), temperature 0, one token,
  `top_logprobs=2`, score = P(True) from the top token. It does NOT send
  `logit_bias`: those ids are unrelated tokens on every other tokenizer and
  some gateways refuse the parameter; with temperature 0 and the score read
  only from a True/False top token, a +1 nudge on two arbitrary tokens could
  only have mattered where it displaced the answer — which our parser now
  reports as an error rather than scoring. The answer is read robustly (case,
  a leading space, `▁`/`Ġ` word markers); a top token that is neither True nor
  False, or no logprobs, is a `RerankError` naming the alias and the usual
  causes. At most `CC_GRAPH_SEMAPHORE_LIMIT` requests at once; the first
  failure cancels the rest. Its AsyncOpenAI client is the graph's other
  clients' (`_openai_client`): the same trust settings and SDK defaults.
- **A failure raises — no fallback order, no time budget** (the operator,
  2026-10-05: "We are using LiteLLM for our routing and retry logic.
  Anything that fails should try again or throw an error."). Release 1's
  fail-soft (input order with placeholder scores) is retired from
  `RerankClient` too. A non-2xx `/rerank` answer is `RerankHTTPError`, an
  `openai.APIStatusError`, so `contract.classify_failure` judges it by the
  same status rule as every other LiteLLM call (403/408/429/5xx transient);
  a malformed answer is a semantic `RerankError`. It propagates out of
  `search_()`; the agent read path's bounded retry (`tools._read_with_retry`)
  tries a transient failure once more and then tells the agent the graph is
  unavailable. No retry loop in the clients; the per-request HTTP timeout
  raises. In-process counters (calls, failures, malformed, last error,
  recent median latency) feed the Systems row and the self-check.
- **Recipe choice is unchanged in shape** (D6): cross-encoder recipes when a
  reranker of either kind is configured and no centre node; RRF otherwise.
- **Health.** The self-check's `graph-rerank` row: no alias is a PASS line
  saying what it costs (a configured state, not a failure); with one, a live
  rank of a relevant and an irrelevant passage as the app — a failure is a
  FAIL saying fact search will error until it is fixed, naming for `chat` the
  three usual causes (no logprobs; thinking not disabled on the alias; the app
  key's scope lacks the alias). The Systems page's Graphiti row shows the
  reranker in use and its last error.
- **Installs decide the kind by PROBING** (`discover-llm.sh rerank` /
  `rerank-chat`): `/rerank` first, then the chat shape; the result is written
  only where `.env` has none, and a kind that is set is proven, never
  re-detected. Single node: `cc-rerank` is optional (`cc_optional_aliases`),
  its skeleton is created and never pauses the run, its question offers the
  `graphiti-llm` model as a one-line answer — never mapped automatically,
  because a chat reranker costs up to 2 × the limit calls per search on the
  extraction model (50 at the agent tools' 25-fact limit) — and the app key
  gains the alias once it is in use. Mapped but answering neither shape is a
  WARN and the alias stays unused; an alias the operator set that fails is a
  USERACTION. k3s: the declaration requires the alias, so "neither" is the
  same gate as its other probes. In both declarations the row is
  `judged_by_probe`: `register-models.py` creates the skeleton and does not
  hold a filled-in row to its patterns, because both shapes are right.
- **`gpt-4.1-nano` is retired** as a required alias, question, probe and
  declaration on both substrates — it was only ever the dead fallback. An
  existing row and `CC_LLM_UPSTREAM_MODEL_GPT_4_1_NANO` are left alone.
- **`scripts/graph_rerank_bench.py`** reproduces the measurement on an
  operator's own models (a scratch group, deleted afterwards), because the
  latency above is one model's.

`add_episode` never calls the cross-encoder. Only agent search does; the
cockpit's Graph panel search is `neo4j_reader.search_entities`, which has no
reranker.

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
  Jobs in one group run strictly in order — upstream requires episodes in a
  group to be added sequentially — and different groups run side by side, as
  the stock server's per-group queues did. There is no global cap and no
  setting for one: an extraction already issues several model calls at once,
  the model backend queues what it cannot serve, and a limit belongs to the
  model it protects, not to this queue. A call that times out waiting is a
  transient failure and the job runs again.
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

As built: there is one worker per DATABASE, not per process — it holds an
advisory-lock lease, and a second process that cannot take it says so and
does not run, so recovery's "a RUNNING job is an orphan" is true by
construction. A transient failure re-queues on the shared backoff curve with
no exhaustion (an outage is waited out, not converted into failures), and
only its FIRST transient failure emits `graph.ingest.deferred`, so a long
outage is one event and not a stream; the Systems page's graph row shows the
queue's counts, which is where a stuck backlog is seen.

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

As built (v2.61.0): `search_knowledge_graph` takes `as_of`, or
`after`/`before` (ISO-8601, validated in the tool); `search_knowledge_graph_entities`
takes `entity_types` (validated against the ontology, the valid names in the
retry message); both — and the curator's `search_graph_group` — take
`center_entity_uuid`, which selects the node-distance recipes whatever the
reranker. The "single OR group" rule is enforced in its precise form: per
date field, at most ONE OR group carries a compared date; the only other group
is the parameterless `IS NULL`, because an open bound is how a still-current
fact is stored (`invalid_at` null) and a window that excluded it would hide
every current fact. The test runs the library's own constructor over every
window built. With no filter the call is v2.60.0's exactly.

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

As built, a failure to patch is a WARNING in the k3s updater, never a stop —
a stop at `rebuild()` would leave a merged tree mid-update with no rollback —
and a REFUSAL in the worker, which keeps the jobs queued and re-checks every
minute, so running the script by hand releases them without a restart. The
installers (`setup.sh app` on k3s, the `app/graphiti-patches` row on single
node) fail the step.

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

As built (v2.61.0): `tests/test_graph_write_shape.py` derives the bulk path's
property sets for an entity, a fact, an episode and a MENTIONS edge from the
installed package and compares what `neo4j_writer` writes; the allowlist of
deliberate differences is empty. Facts gained `expired_at` (null) and
`reference_time` (the provenance episode's instant); vectors are stored with
the bulk path's procedures (a null vector clears the property instead — the
procedure refuses null). Embeddings are still our own request, NOT the
library's embedder: `OpenAIEmbedder.create` slices the vector to
`embedding_dim`, which would turn a wrong-width model's vector into a
truncated one instead of dropping it. The stamps are no longer written —
nothing reads them but the re-embed migration, which writes its own and treats
an unstamped row as pending, as it already did every extracted row — and
`fact_result` strips any key containing "embedding" from a fact's attributes
(the node result already did; the fact result leaked them). Every cockpit read
takes `startNode`/`endNode` and real labels as truth; the duplicated
properties are still written and only the audit's mismatch check reads them.
Found on a scratch graph while doing this: `rescope_episode` deleted the copy
of every moved fact whose endpoint was split (the copy kept the uuid and the
delete matched by uuid); fixed by deleting the original in the same
statement.

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

As built (v2.61.0): the removal is NOT a call to upstream's `remove_episode`.
That method runs three separate auto-commit deletes (the node one in
`CALL … IN TRANSACTIONS` batches), so a crash between them leaves half a
deletion whose re-run reads a different set. `neo4j_writer.delete_episode`
computes upstream's rule (`neo4j_reader.compute_delete_preview`), compares it
with the approved set, deletes and cleans up in ONE `execute_write`; a hash
pin on the installed `remove_episode` source keeps the hand-written rule in
step with upstream. Verified on a scratch Neo4j 5.26 against upstream's own
`remove_episode` on the same fixture: identical survivors. The preview also
lists COLLATERAL facts (not created by the episode, deleted because DETACH
takes everything attached to a deleted entity) and the surviving facts that
lose this provenance. The set is computed at propose time (embedded in the
proposal; the cockpit confirms its digest), at execute time (the Executor
refuses a change or a missing preview) and inside the deleting transaction
(the job FAILS with `graph.ingest.failed` rather than delete a different
set). The job is `kind='remove_episode'` on the group's queue: it waits behind
earlier work in the group, the patch gate does not hold it, recovery re-runs
a RUNNING one (a gone episode finishes DONE and still cleans up), and
completion writes `graph.episode.deleted`. The operator's direct path
(`DELETE /graph/episode`, from the episode walk or the drawer's source list)
records `graph.curated` before queueing and waits a bounded time (202 when
still queued). Verification rows are history: the sweep parks a row whose
episode was deleted with `episode_deleted` instead of reading an empty
delta, and the Verify tab marks such rows. The audit reports facts whose
`episodes` is empty or names a missing episode, and episodes whose
`entity_edges` names a missing fact. Not checked by this release: whether a
past one-off script REORDERED a list (nothing records the original order);
the live graph was not read for it here.

### D10. Removing the server from both substrates

- **k3s:** the deployment and service leave the manifests and join
  `removed.txt` (`kubectl apply` never prunes); the image row, the build step
  and the health checks are dropped; `backup.sh` loses its scale-down
  choreography; `mint-keys.sh` widens the application key's scope and gains
  `--scope-only`, which widens a kept key in place.
- **The first update across the boundary is run by the PREVIOUS release's
  updater,** whose image row and configmap-refresh row live in its memory and
  run against the new tree: a changed input there means a prebuild with the
  target's build script and then a rollout restart of a Deployment the
  tombstones have just deleted — a stop after the merge, with no rollback. So
  there is NO stub build script: the old image's files
  (`deploy/pi/graphiti/`, `deploy/k3s/build-graphiti-image.sh`) stay
  byte-identical and unreferenced for one release, and are deleted by the
  next one, which this release's updater applies. The deployment and service
  are tombstoned now; the configmap and Secret one release LATER, because the
  previous updater's rollback re-applies its manifests, which recreate the
  Deployment but not what it mounts.
- **The bridge.** What an existing install must gain — the patch step, the
  wider key scope, `CC_GRAPH_RERANK_ALIAS` — has to be done by that previous
  updater, so it ships one release earlier: v2.59.1 adds the patch step to
  `rebuild()` and a `reconcile` phase before the restart, both keyed on what
  the merged tree contains. v2.60.0's `min_upgrade_from=2.59.1` makes older
  installs refuse the jump at resolve, before anything changes; no update
  needs the operator's hands.
- **Single node:** the compose service, the base-image row, the build script
  and the `fetch`/`up-stack` rows are gone and the checklist regenerated; the
  `stack` phase removes an existing install's leftover container by name
  (`compose up` never removes a container whose service left the file). Its
  updater runs the new release's own `setup.sh`, so it needed no bridge.
- **Air-gap:** graphiti-core and its dependencies are in the lock and the
  PyPI-mirror completeness story in `deploy/AIRGAP.md`; the base image, its
  apt work and its mirror rows are gone.
- **Rollback** re-applies the previous manifests; the previous image is left
  on the nodes for that reason and is not garbage-collected by this release.

As built, the second half (v2.61.0): v2.60.0's `cc-update.sh` — the updater
that applies v2.61.0 — has no Graphiti image row and no Graphiti configmap
row (its IMAGES are sandbox and crawler; its configmap rows are the LiteLLM
config and the schema), so `deploy/pi/graphiti/` and
`deploy/k3s/build-graphiti-image.sh` were deleted and the configmap
`cc-graphiti-config` and Secret `cc-graphiti` joined `removed.txt`
(`make-secrets.sh` stopped creating both in v2.60.0). `min_upgrade_from` is
2.60.0: a 2.59.1 install still carries the image row in its updater's memory
and would die at prebuild on the deleted build script, so it refuses at
resolve instead — an install steps through each release tag in order. The
single-node updater runs the new release's own `setup.sh`, which never
referenced those paths.

### D11. Two releases, and the reranker decision

1. **Parity cutover** — D1 to D7 and D10. Same behaviour, a durable queue,
   no server. Acceptance: the suite; a prompt-parity test (the entity-types
   block rendered from our models equals the bytes the server's models
   produced); and one live episode through approval, extraction, verify and
   a search on a non-production graph. The live run was done on 2026-10-05:
   two episodes through enqueue, the worker, verification and search
   against a real graph and the local model, with the expected entity types,
   text-derived dates and exactly one scoped invalidation.
2. **What the library makes possible** — D8, D9, search filters for agents,
   the reader's canonical switch, the refreshed vendored documentation
   (fetched from the graphiti repository at our pinned tag; the present
   snapshot is mostly Zep Cloud pages). Shipped in v2.61.0 with D10's
   deferred deletions, EXCEPT the vendored documentation: `docs/vendor/` is
   only ever regenerated by `scripts/vendor_docs_fetch.sh` (which also
   regenerates its manifest), and that refetch was not part of this release.
   It is what keeps this record `partial`.
3. **The reranker decision** (v2.62.0) — D3 rewritten after the post-cutover
   benchmark: one optional alias with three tiers, the kind probed by both
   installers, `gpt-4.1-nano` retired, and the retirement of release 1's
   fail-soft (a reranker that fails now raises). It did not need a bridge:
   none of v2.61.0's updater inputs changed, so `min_upgrade_from` stays
   2.60.0.

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
