# Episodes, entities and facts

Three different things. Confusing them is the most common way an agent talks
nonsense about the graph.

## Episode — a single data-ingestion event

An episode is **one act of telling the graph something**. It is itself a node.
Every entity identified during ingestion is linked back to the episode by a
`MENTIONS` edge, so the graph can always answer "where did this come from" and
"what did we believe at time T".

You create episodes. `add_episode(name, episode_body, source_description)`
submits one to the `central_command` group with `source: "text"`.

**Ingestion is queued and asynchronous.** The return value is Graphiti's
acknowledgement, **not the finished graph state**. An episode you just
committed will not be searchable as facts immediately — on this deployment
extraction takes **~56 seconds per episode**. Do not conclude the write failed
because a search a moment later found nothing.

## Entity (node) — a thing the graph believes exists

People, systems, issues, projects. Extracted from episodes by an LLM, carrying
a generated summary. `search_nodes` returns these. You never create nodes
directly through Central Command's surface.

## Fact (edge) — a relationship between two entities

A typed, **temporally-scoped** relationship, carrying the natural-language
`fact` string. `search_facts` returns these, and they are what
`search_knowledge_graph` renders for you.

## The bi-temporal model — read the markers

Facts carry validity timestamps, and the read tool surfaces them:

- `- <fact> [valid from <valid_at>]` — the graph believes this held from that
  time.
- `- <fact> [SUPERSEDED as of <invalid_at>]` — **this edge was invalidated**. It
  is history, not current belief.

Graphiti's own contradiction handling is **bi-temporal edge invalidation**: a
new edge that contradicts a semantically-related old one causes the old edge to
be expired and the fact regenerated in the past tense.

**But that invalidation decision is itself an LLM judgment with no published
precision numbers** — exactly the class of claim this system treats as
untrusted. So:

- A `[SUPERSEDED]` marker tells you the graph *decided* something was
  superseded. It is evidence, not proof.
- Never quietly propose the new version of a contradicted fact. Contradictions
  surface as **proposals** ("X supersedes Y — confirm?"), never as silent
  mutations. See `steward-duties`.

## Group tenanting

`group_id` namespaces every node and edge. Nodes and edges sharing a `group_id`
form an isolated graph that can be queried independently.

| | value |
|---|---|
| **read groups** | `main, central_command` (`CC_GRAPH_READ_GROUPS`) |
| **write group** | `central_command` (`CC_GRAPH_WRITE_GROUP`) |

`main` is the **preserved homelab graph** inherited from before this system
existed. Reads span both so agents get the full picture; writes land only in
`central_command`, so the inherited graph is never polluted by Central Command's
proposals. Do not propose writing to `main`, and do not treat a `main` fact as
something this system produced.

## The rule that keeps the graph small

**The graph never ingests raw email or document text — only distilled claims**
(D13: store refs, not bodies). An episode body is 1–3 self-contained sentences.
If you find yourself pasting a paragraph from the source, you are writing the
wrong thing: quote the source in your *evidence*, and distil it in the episode.
