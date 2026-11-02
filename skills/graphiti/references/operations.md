# The graph in operation

## Your surface

Every holder of the `graph-read` pack has two ungated reads. Every line they
render leads with the item's **uuid** — what `graph.create_edge` endpoints,
`graph.update_node` / `update_edge` / `delete_*` and `graph.merge_nodes` take.
Never guess a uuid; read it here.

- `search_knowledge_graph(query)` — relationships. Up to 25 hits, rendered
  `- <uuid> | <fact> (<RELATION_TYPE>) [valid from …]` /
  `- <uuid> | <fact> (<RELATION_TYPE>) [SUPERSEDED as of …, was valid from … —
  historical, no longer current]`, each with a trailing `[group: …]` (or
  `[domain: … — steward: …]`) note naming its partition. The endpoints are
  not named, so the relation type is all the naming you get.
- `search_knowledge_graph_entities(query)` — entities. Up to 15, rendered
  `- <uuid> | <name>: <summary>`. Ask this when the question is about a *thing*
  ("what do you know about X"); a fact search alone returns fragments.

Both take OPTIONAL narrowing (since v2.61.0); an ordinary search needs none:

- **When** (facts): `as_of=<instant>` — only facts true at that instant; or
  `after=` / `before=` — facts true at some point in that window (either
  bound alone works). Not both kinds at once. Each is an ISO-8601 date or
  date-time (`2025-03-01`, `2025-03-01T09:00:00Z`); a word like "today" is
  refused — resolve it with `current_time` first. A fact with no recorded
  start (or end) counts as open on that side, so a still-current fact is
  found by `as_of=today`.
- **What kind** (entities): `entity_types=[…]` from exactly the ten ontology
  types — Person, Preference, Requirement, Procedure, Location, Event,
  Organization, Document, Topic, Object. A wrong name is handed back with the
  list.
- **Around whom** (both): `center_entity_uuid=<uuid>` ranks results by graph
  distance from one entity — a uuid a search already returned, never an
  invented one. (A centred search does not use the cross-encoder reranker.)

Holders of `graph-curate` (the curator) have three more, which see EVERY
partition: `list_graph_groups`, `list_graph_group_episodes(group_id)` (episode
uuids, for `graph.rescope_episode` and `propose_delete_episode`) and
`search_graph_group(group_id, query, …)` (facts and entities inside one group,
uuids leading; the same optional narrowing as above). The curator also holds
`propose_delete_episode(episode_uuid, rationale)`, which reads exactly what a
deletion removes and embeds it in the proposal (below).

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

**Deleting and reworking an episode.** `graph.delete_episode` (v2.61.0)
deletes an episode, the facts it was the FIRST to create, the entities no
other episode mentions, and — because entities are detach-deleted — any other
fact still attached to those entities (the "collateral", listed separately);
surviving facts stop citing it. That is graphiti-core's own `remove_episode`
rule plus one cleanup it omits, run in one transaction on the episode's group
queue, so it never races an extraction. The proposal carries the preview;
the Executor and the queue each recompute it and refuse if the set has
changed — re-propose then. It is IRREVERSIBLE: restoring means proposing the
episode again. An episode is never edited in place — its text is what the
operator approved — so REWORKING one whose text was wrong is this deletion
plus a fresh `graph.add_episode` with the corrected text. A wrong EXTRACTION
of a right text is not a deletion; it is the curator's surgical edits.

## Degradation — a down graph must not wedge you

`search_knowledge_graph` is **best-effort by necessity**: one bounded retry,
and any failure to reach the graph (Neo4j or the model proxy) returns

```
knowledge graph unavailable (<ErrorType>); proceed without it
```

An empty result returns `no relevant facts in the knowledge graph`.

This is the behaviour of the code today. The outage doctrine (PRD FR-40; build
list §6.1 item 4) replaces it: a DOWN graph read will **park the session**
rather than hand the agent "proceed without it". Until that ships the string
stands: say the check could not be made and draw no conclusion from it.

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

So "queued" is a state on a record, not an acknowledgement. Never
re-propose an episode because a search came back empty; look at the job, not at
the graph, to learn whether it ran.

## Latency you should expect

- **Roughly a minute per episode** for extraction when the extraction LLM is a
  local model (background work); a hosted model takes a few seconds. This is
  acceptable for queue processing and worth knowing before you assert a write
  "did not land".
  <!-- overlay: graph_extraction_latency -->
  (The measured figure for this deployment is an instance fact.)

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
present-tense fact to it; its own fallback ("when I processed this") produces
ingestion-dated facts and is never relied on.

## The embedder is not optional

Graphiti has **no embedding fallback** — another provider cannot substitute
because the dimensions differ (PRD FR-34). So an outage of the embedder (role
alias `cc-embedding`, over whatever upstream the deployment runs
<!-- overlay: embedder_upstream -->)
is a graph **write outage**, not a degraded capability: ingest jobs cannot
complete until it is back. The LLM being down is the degradable case; the
embedder being down is not. Report them differently.

## Changing the embedding model (a migration, an accepted outage)

`cc-embedding` is the one alias Central Command ever addresses, and the model
behind it changes in LiteLLM only — never in Central Command, never as a second
alias. Vectors from two models are not comparable, even at the same width, so
the graph does not trust the alias: it records its own embedder and refuses to
be read or written when the two disagree.

**The record.** One node, `(:CCGraphMeta {key: 'embedder'})`, written by the
module and by nothing else (the one exception is the first write into an EMPTY
graph, which declares the configured embedder). It holds `alias`, `dimensions`,
`epoch` (+1 per completed migration), `text_recipe` (names how an entity and a
fact are turned into the text that is embedded), `state` (`stable`,
`migrating` or `awaiting_config`), three `canary_texts` with their
`canary_vectors`, `upstream_model` (LiteLLM's name for the model, as
information), `updated_at`, `history` (one line per completed run) and
`committed_at`. `python -m central_command.graph_reembed status` prints it.

**The check, and what closes the graph.** One cached check (30 seconds) runs at
the ingest worker's claim, the four curation writes, every graph search and the
`graph-embedder` self-check row. It closes the graph, with the reason on the
outage banner and the remedy `run: python -m central_command.graph_reembed
status`, when: (i) the graph has vectors and no record, (ii) the record's alias
or width differs from the settings, (iii) the vectors in the graph are not the
record's width, (iv) the model behind the alias has changed — the three canary
texts, embedded now, no longer match the stored canary vectors (cosine below
0.999), which is how a same-width swap is caught — or (v) a migration is in
progress. A closed graph is the `embedder` dependency DOWN: reads and writes
park, nothing fails open, no keyword-only mode, and the work resumes by itself
once the record and the configuration agree again. An empty graph is open.
Bolt-only reads and deletions keep working while it is closed.

**A migration is an outage the operator accepts.** The app does not need to
work while it runs. `run` closes the graph, re-embeds every entity, fact and
community in place through the alias as it now points, rewrites the record and
opens the graph again. There is no staging, no second graph and no rollback
window; the rollback is the dump taken first.

The sequence:

1. **A fresh backup.** `run` refuses without a `neo4j_<stamp>.dump.gz` in the
   backup directory that passes `gzip -t`, is over 1024 bytes, is not a
   `pre-update` file and is under an hour old (the age is read from the stamp in
   the file name, not the file time). The module never takes the backup. On the
   multi-node profile run `deploy/k3s/backup.sh`. The single-node profile has no
   Neo4j backup, so take one by hand (the `neo4j-admin` dump runs against a
   stopped database; do it in the operator's maintenance window):
   `neo4j-admin database dump neo4j --to-stdout | gzip > <dir>/neo4j_<stamp>.dump.gz`
   where the stamp is `%Y%m%dT%H%M%SZ`. The backup directory is
   `CC_BACKUP_DIR`, else the running user's `~/cc-backups`; `--backup-dir`
   overrides both. `run` also refuses within 15 minutes either side of the
   03:00 nightly backup (`--ignore-backup-window` if it is disabled).
2. **Re-point `cc-embedding` in the LiteLLM UI.** The next check closes the graph
   with reason (iv), and the banner shows `embedder` down with the remedy. That
   is expected, and it is the proof the detector works. For a hosted embedder
   the cost is one embedding per entity, fact and community; `check` reports the
   counts and the tokens (from the proxy's counter, or marked estimated).
3. **`python -m central_command.graph_reembed check`.** Read-only. Run it from
   the checkout, as the user that owns it: the settings come from the checkout's
   `.env`. It counts the graph by kind and width, embeds through the alias with
   the app's key and reports the width it measured, runs the alignment probe
   (one batch of repeated and different texts, each position equal to its own
   single call, which reproduces the 2026-09-01 cache incident), reads LiteLLM's
   cache configuration (`GET /cache/ping`, with `CC_LLM_PROXY_BASE_URL` and
   `CC_LLM_PROXY_ADMIN_KEY`) and refuses if the cache covers embeddings, and
   checks the dump. It exits 0, or 3 naming the fix. On a copy of the live graph
   it took 3 seconds.
4. **`python -m central_command.graph_reembed run`.** It requires the same
   checks to pass, writes `state=migrating` (every gate is closed within the
   check's 30-second cache; `run` waits that out plus 5 seconds, 35 in all),
   waits for an extraction job that is still running, then re-embeds in
   batches of 32 and verifies: every vector the measured width, none lost,
   the canaries agree, no identical vectors for different texts, three
   searches through the real read path, and a sample compared against a fresh
   single embedding. On success it writes the record (`epoch` + 1, a history
   line), sets `state=stable`, and the gates reopen within the cache's
   30 seconds; the report is saved under `<backup-dir>/reembed/`. Rehearsed on
   a copy of a small graph: 209 vectors in 47 seconds (about 4.5 vectors a
   second on a local 1024-wide embedder), plus the wait.
   A second run is idempotent: the epoch goes up once more and the history gains
   a line.
5. **Confirm.** `status` reads `epoch`, `stable`, the alias and width. The
   banner clears within one sweep (a `dependency.up` for `embedder`), and a
   search answers.

**If the width changes** (a model with a different number of dimensions): `run`
still re-embeds everything and records the width it measured, but it ends
`awaiting_config`, closed, exit 3, with the line
`the graph is re-embedded at width <m> and CC_EMBED_DIM is <d>: set CC_EMBED_DIM=<m> in .env, restart the units (the setting is bound at import), then run: python -m central_command.graph_reembed run --finish`.
The setup drivers refuse to change `CC_EMBED_DIM` themselves, which is the guard;
this is the one path that sets it, by the operator. Set the key, restart the
units yourself (the module restarts nothing), then `run --finish`: it checks that
the record and the configuration agree, verifies, and sets `stable`. A same-width
change needs no edit and no restart.

**If a run is killed** (or exits 2 or 3 mid-pass) the record stays `migrating`
and the graph stays closed. Run `run` again: it redoes the whole pass, and the
epoch is counted once. There is no lock against two runs at once, so do not
start a second while one is going. A refusal before the first write
(`run refused: nothing was changed`) leaves the graph exactly as it was.

**Rollback.** Either restore the dump taken in step 1 by the procedure in the
k3s README (it brings back the graph and its record as they were, so the alias
must point at the old model again too, or the check closes the graph at once), or re-point `cc-embedding` back
in LiteLLM and run `run` again: the graph is re-embedded by the old model and the
epoch goes up. Nothing else is retained.

**The first use after the release that introduced this.** The graph already
holds vectors and has no record, so that release's update closes it (reason (i))
until the operator runs `check` and `run` once, with a fresh dump, under the
same alias and width. That one run also re-embeds every entity under the single
text recipe (an entity is its name, a newline and its summary; a fact is its
text), so no vector is left in the older name-only form. Do it straight after the
update.

**Text recipe.** One function, `contract/embedding_text.py`, is the recipe for
the curation writer, the module and the third carried graphiti-core patch (see
`deploy/graphiti-patches/`), which makes the extractor embed an entity's name and
summary rather than the name alone. The record's `text_recipe` names it.

## Where knowledge comes from

Sources → the **work ledger** → dispatch → a steward agent proposes → the gate
→ the Executor enqueues → the ingest worker extracts. One path, the existing one, widened: the ledger's
`unique(message_id)` + `on conflict do nothing` + `for update skip locked` is
the same idempotent-consumer shape whether the work item is an email, a
document, or a change event. Don't imagine a second ingestion route.

## The graph's content is disposable — for now

Judge the **mechanism**, not the facts currently in it. During the current
shake-out phase graph content may be wiped, scoped to the `central_command`
group, so do not build reasoning that depends on a specific stored fact still
being there. Once the deployment is in long-term use, disposing becomes
non-viable (PRD FR-32).
