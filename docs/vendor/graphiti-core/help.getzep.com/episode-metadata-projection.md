> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Episode metadata projection

> How episodes associate with the facts, entities, observations, thread summaries, and user summary derived from them, and how episode metadata projects onto those artifacts for filtering.

## Overview

Every piece of context Zep produces — a fact on an edge, an entity, an observation — is derived from one or more [episodes](/episodes), the raw messages, text, or JSON you ingested. Zep records these links as **episode associations**.

Associations provide **provenance**: you can trace any artifact back to the episodes it was created from. That provenance is the foundation for several of Zep's other capabilities. Metadata you attach to an episode is *projected* along its associations onto every artifact derived from it, which powers [agent access](/attribute-based-access-control) and [episode metadata filtering](/searching-the-graph#episode-metadata-filtering) during search. Associations also preserve **graph integrity** when you [delete an episode](/deleting-data-from-the-graph#delete-an-episode): an edge or node is only removed once no associated episodes remain.

## How episodes associate with each context type

Each context type accumulates episode associations in its own way:

* **Edges (facts).** [Facts](/facts) live on edges. An edge is associated with the episode that first extracted the relationship, plus any later episode that either reaffirms the same fact (recorded as a duplicate) or invalidates it.
* **Entities (nodes).** An [entity](/entities) is associated with every episode that mentions it. If a user mentions "Paris" in three separate conversations, the "Paris" node is associated with all three episodes.
* **Observations.** An [observation](/observations) is associated with the episodes that serve as its supporting evidence — the episodes behind the facts it was synthesized from.
* **Thread summaries.** A [thread summary](/thread-summaries) is associated with the message episodes it summarizes, meaning every message in that thread.
* **User summary.** The [user summary](/user-summary) lives on the user's central entity node, which is associated with episodes exactly as any other entity node is: every episode that mentions the user. The user node is created before any episodes exist and is never deleted when an episode is deleted, but it accumulates episode associations in the same way as any other node.

You can inspect these associations using the API:

* **Edge → episodes**: The `episodes` field on an [edge object](/sdk-reference/graph/edge/get) is a list of episode UUIDs associated with that edge.
* **Node → episodes**: Use [episode listing](/reading-data-from-the-graph#listing-episodes) with `mentioned_node_uuids` to page through every episode that mentions a given node.
* **Episode → nodes and edges**: Use node and edge listing with the [`episode_uuids` filter](/searching-the-graph#node-and-episode-filtering) to retrieve the entities and facts an episode contributed to.

## How episode metadata projects onto artifacts

When you [attach metadata to an episode](/adding-business-data#episode-metadata), that metadata is projected onto every artifact [associated with](#how-episodes-associate-with-each-context-type) the episode. An edge or node does not carry metadata of its own — its **effective metadata** is derived from the metadata of the episodes associated with it.

Because an artifact can be associated with more than one episode, its effective metadata is the combined, deduplicated union of those episodes' metadata. Each key maps to a list of the values contributed across the associated episodes:

* A key present in only one associated episode maps to a single-value list.
* A key present in several associated episodes maps to the list of their distinct values.

For example, suppose an edge is associated with two episodes:

```json
// Episode 1 metadata
{ "source": "crm", "priority": 5 }

// Episode 2 metadata
{ "source": "support_ticket", "reviewed": true }
```

`source` appears in both episodes, while `priority` and `reviewed` each appear in only one. The edge's effective metadata is:

```json
{
  "source": ["crm", "support_ticket"],
  "priority": [5],
  "reviewed": [true]
}
```

The overlapping `source` key holds both values, and the keys unique to a single episode each hold one value.

Effective metadata is also what [agent access](/attribute-based-access-control) evaluates: source-based policies match a rule's required values against an object's effective metadata to decide which objects an API key may read. This is why the metadata you attach at ingestion governs not only what you can filter for, but what an access-controlled key is allowed to see.

## Filtering by episode metadata

The same episode associations that carry projected metadata also power [episode metadata filtering](/searching-the-graph#episode-metadata-filtering) in graph search: an edge, node, or episode result matches when at least one of its associated episodes satisfies the filter. Matching is evaluated per episode, not against the merged effective metadata above — a set of `AND`ed conditions matches only when a single associated episode satisfies all of them.

## Related

* [Episodes](/episodes) — the raw artifacts that metadata is attached to and associations point back to.
* [Adding business data](/adding-business-data#episode-metadata) — how to attach metadata to an episode.
* [Searching the graph](/searching-the-graph#episode-metadata-filtering) — filtering search results by episode metadata.
* [Agent access](/attribute-based-access-control) — source-based policies use an object's effective metadata to decide which objects an API key can read.
* [Deleting data from the graph](/deleting-data-from-the-graph#delete-an-episode) — how deletion follows episode associations.
* [Context types](/context-types) — overview of the context Zep produces.