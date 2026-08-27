> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Prepare Data for Ingestion

> Prepare source data so Zep can resolve entities, preserve timelines, and extract useful facts into a Context Graph.

Good ingestion starts before the API call. Preserve source context and timestamps, respect import limits, and decide which entities or facts must be represented exactly. These choices improve extraction and retrieval. For how Zep turns episodes into a Context Graph, see [How Graph Creation Works](/how-graph-creation-works).

These practices apply whether you call the Zep SDK directly or use [`zep-ingest`](/zep-ingest), a Python package of loaders, transforms, and monitoring helpers built on the Zep SDK. When `zep-ingest` provides a built-in feature for a recommendation, this guide identifies the specific helper to use. `zep-ingest` 0.1 is alpha software, so pin its version, and use the SDK directly if your import cannot absorb interface changes.

## Include context in every episode

Put this context in the episode text itself. Zep extracts entities and facts from the text. Metadata is projected onto the artifacts derived from an episode and lets you [filter graph search results](/searching-the-graph#episode-metadata-filtering), but it is not a place to keep context you want extracted.

An episode should be understandable without relying on the previous record or a filename that Zep never receives.

Preserve context that identifies:

* who said or authored the content;
* the channel, thread, meeting, document, or email subject;
* the organization, account, project, or product involved;
* the source record ID and system;
* the original event time.

For a transcript, prefer:

```text
Meeting: Weekly launch review
Date: 2025-04-18

Sarah Brown (Product): The launch date moved to May 10.
Robert Chen (Support): I will update the customer notice by Friday.
```

over:

```text
The team discussed the launch. It moved, and someone will update customers.
```

The second version removes speaker attribution, the exact date, and the owner of the commitment.

The `zep-ingest` package's `SlackExportLoader`, `TranscriptLoader`, and `EmlLoader` preserve source-specific context inline. For a custom loader, add the equivalent context to the text of every episode.

## Preserve original event time

Supply `created_at` for episodes and messages using the original event time in RFC3339 format. If it is absent, Zep uses ingestion time.

This distinction matters for backfills. Data imported today may describe events from several years ago. Dating all of it today changes the order Zep uses when determining which facts were valid later.

Use the most authoritative timestamp for the source:

| Source                | Recommended timestamp                        |
| --------------------- | -------------------------------------------- |
| Chat or Slack message | The time the message was sent                |
| Email                 | The parsed `Date` header                     |
| Meeting transcript    | The meeting start or timestamp for each turn |
| CRM or business event | The time the event occurred                  |
| Published document    | The publication or effective date            |

Do not use filesystem modification time unless it represents the source event. The `zep-ingest` package's `TextFileLoader` uses it only when you explicitly set `use_file_mtime=True`.

If you use `zep-ingest`, `created_at` is required on every thread message, and the package sends it on both the Batch and sequential paths.

## Group related chunks with a document ID

When one source becomes several episodes, pass the same `document_id` on every `graph.add` call and every batch `graph_episode` item. Pages of a PDF, sections of a handbook, and messages from one Slack channel are typical cases.

Zep scopes prior-episode context to that `document_id`, so extraction can resolve pronouns and references across chunks. Zep also generates an incremental [document summary](/documents) from those episodes.

Choose a stable identifier from the source system, such as a file name and version, a channel ID, or an export ID. A `document_id` is 1 to 100 characters.

`zep-ingest` does not attach a `document_id` when it chunks and submits files. Pass `document_id` on `graph.add` or on batch `graph_episode` items when you submit chunks through the SDK.

## Choose episodes or manual graph updates

Use episodes when the source is material Zep should interpret. Use manual graph updates when you already know the structured entities or relationships that should exist in the graph.

| Path                                 | Use it when                                                                                         |
| ------------------------------------ | --------------------------------------------------------------------------------------------------- |
| Episodes, messages, and source files | The source is conversation, narrative, or other material Zep should extract from                    |
| Manual nodes and fact triples        | You have known structured data and already know which entities or relationships belong in the graph |

If you use `zep-ingest`, document, conversation, email, transcript, Slack, and JSON helpers create episodes or messages for Zep to interpret. Use `ingest_nodes` and `ingest_fact_triples` for the manual path.

When combining both paths, use this order:

1. Create the destination.
2. Set its ontology. An ontology applies only to data processed after it is set, and existing data is not re-extracted or retyped automatically, so a type you add later does not reach anything already ingested. Setting an ontology also replaces the whole type set for that scope, so send the complete list every time rather than only the types you are adding.
3. Seed known nodes or facts.
4. Ingest historical documents and conversations in chronological order.
5. Apply current known assertions with their real validity times.

## Seed the graph before the rest of the import

One of the most useful places for a manual update is right after you create a graph and set its ontology, before you ingest documents or conversations. Seed the entities and relationships you already trust so later extraction has anchors to resolve against, instead of inventing parallel nodes for the same people, teams, or accounts.

An employee directory and org chart are a typical case. Suppose the directory already identifies Sarah Brown and the Customer Success team, and the org chart says she leads that team. Write those into the graph first:

```python
import os

from zep_cloud.client import Zep
from zep_ingest import NodeItem, ingest_nodes

client = Zep(api_key=os.environ["ZEP_API_KEY"])

created = ingest_nodes(
    client,
    [
        NodeItem(
            name="Sarah Brown",
            label="Person",
            attributes={
                "employee_id": "EMP-1842",
                "title": "Product Manager",
            },
        ),
        NodeItem(
            name="Customer Success",
            label="Team",
            summary="The customer success organization",
        ),
    ],
    graph_id="company-kb",
    require_uuids=False,
)
created.wait(timeout=600)
```

Then assert the known relationship. Zep resolves endpoints by name when you omit endpoint UUIDs, and may create a node when no match is found. To pin a triple to nodes you just created, call `graph.add_nodes` instead of `ingest_nodes` so the response includes the assigned UUIDs, then pass those as `source_node_uuid` and `target_node_uuid`:

```python
from zep_ingest import FactTriple, ingest_fact_triples

facts = ingest_fact_triples(
    client,
    [
        FactTriple(
            fact="Sarah Brown leads customer success",
            fact_name="LEADS",
            source_node_name="Sarah Brown",
            target_node_name="Customer Success",
            valid_at="2025-01-15T00:00:00Z",
        ),
    ],
    graph_id="company-kb",
)
facts.wait(timeout=600)
```

If you use `zep-ingest`, those helpers are `ingest_nodes` and `ingest_fact_triples`. The underlying SDK methods are `graph.add_nodes` and `graph.add_fact_triple`. [Manually updating the graph](/adding-fact-triplets) covers the full behavior of those operations.

Zep assigns node UUIDs. You cannot supply one when creating a node: a request that includes a node `uuid` is rejected with a `400`. Every accepted node is a new node, and nodes are not deduplicated by name or content, so two identical requests create two sets of nodes. `ingest_nodes` still defaults to requiring a client UUID for idempotency planning; pass `require_uuids=False` as above because the API rejects caller-supplied node UUIDs. When you need the assigned UUIDs — for `graph.node.update`, or to pin fact-triple endpoints — call `graph.add_nodes` directly and keep the UUIDs from that response. `IngestResult` tracks task IDs for monitoring and does not surface assigned node UUIDs.

Store source-system IDs such as `EMP-1842` in node attributes when you need to join the graph back to your systems. Those attributes are application data; they are not a substitute for the UUID Zep assigns.

## Help entity deduplication

When Zep extracts entities from episodes, it deduplicates them primarily by name. It first checks exact names after normalizing case and whitespace, then may use fuzzy similarity and model-based resolution. These later checks are heuristic and do not guarantee a merge.

Whether stronger identity prep is necessary depends on your data and use case. Generally, Zep prefers under-merging entities rather than over-merging when it is unsure, so a given real-world entity might have multiple nodes in its Context Graph. With [high-recall retrieval](/retrieval-philosophy), that may not be an issue: information for that entity can still surface to your agent reliably. See [How entity resolution works](/how-graph-creation-works#how-entity-resolution-works).

Use the practices below — alias canonicalization, identity properties on your ontology, or both — when you need stronger uniqueness than default resolution provides.

### Use one canonical name per entity

Alias canonicalization is worth doing when all of the following are true:

1. Your data often uses shorthand or alternate names for the same entity — a person, project, team, account, and so on. Without more context, those shorthands are ambiguous to Zep's [LLM extraction process](/how-graph-creation-works).
2. You need entities to be reliably unique in the graph for your use case. See [How entity resolution works](/how-graph-creation-works#how-entity-resolution-works) for why reliably unique entities may not be necessary for end to end performance.
3. You can normalize those names in your data before or during ingest (you have an authoritative map, and the aliases are unambiguous in that source).

For example: the entities are people in your organization, and the data includes Slack and email, where shorthand names are often used. If you can map those nicknames and abbreviations to directory names, rewrite them before ingestion so extraction sees one name per person.

[`zep-ingest`](/zep-ingest) includes `AliasCanonicalizer`, a pipeline transform that applies that alias map for you before episodes are submitted. See [Canonicalize aliases with zep-ingest](#canonicalize-aliases-with-zep-ingest).

When those conditions hold, rewrite known aliases to one canonical name. If `Sarah` and `Sarah Brown` refer to the same person in that source, use `Sarah Brown` consistently.

Do not apply a global alias such as `Sarah` when more than one Sarah appears in the dataset. Restrict the mapping to the source in which it is unambiguous.

Choose a canonical name from an authoritative source such as your user directory or CRM. An alias map takes no scope argument, so the unit of scoping is the pipeline: build one map per source and process each source separately, as shown below.

#### Canonicalize aliases with zep-ingest

If you use `zep-ingest`, the package includes `AliasCanonicalizer` for replacing known aliases before Zep extracts entities. Add it as a pipeline transform when you have an authoritative alias map and names have not already been normalized upstream.

```python
from zep_ingest import AliasCanonicalizer

canonicalize_people = AliasCanonicalizer(
    {
        "Sarah Brown": ["Sarah B.", "S. Brown"],
        "Robert Chen": ["Bob Chen"],
    }
)
```

The default `rewrite` mode replaces aliases with their canonical value. Use `mode="annotate"` when preserving the original text is important:

```python
canonicalize_people = AliasCanonicalizer(
    {"Sarah Brown": ["Sarah B."]},
    mode="annotate",
)
```

##### `AliasCanonicalizer` behavior and safeguards

* Matching is case-sensitive and uses word boundaries by default.
* URLs, inline code, and existing canonical names are not rewritten.
* An alias cannot map to two different canonical names in the same transform.
* Text episodes are rewritten; structured JSON fields are left unchanged. Canonicalize those values upstream or with a custom transform.
* The default safety guard rejects aliases shorter than three characters and many common words or ambiguous given names.

For example, do not map the bare alias `Will` to `William Hughes`. `Will` can be a person's name, but it is also a common word in sentences such as “Will review the proposal.” Sentence-start capitalization makes that unsafe even with case-sensitive matching. Use an unambiguous alias such as `Will Hughes`:

```python
canonicalize_people = AliasCanonicalizer(
    {
        "William Hughes": ["Will Hughes", "W. Hughes"],
    }
)
```

The default risky-word list includes names and words such as `Will`, `May`, `Mark`, `Bill`, `Art`, and `Page`. You can extend it with terms that are ambiguous in your domain:

```python
from zep_ingest import AliasCanonicalizer, DEFAULT_RISKY_WORDS

canonicalize_people = AliasCanonicalizer(
    {"William Hughes": ["Will Hughes"]},
    risky_words=DEFAULT_RISKY_WORDS | {"lead", "support"},
)
```

Give each source its own alias map, then preview the complete import and review the per-alias replacement counts before submitting anything. `preview()` makes no Zep API calls, and `limit=None` validates the whole stream rather than a sample:

```python
from zep_ingest import AliasCanonicalizer, Pipeline, TextChunker, TextFileLoader

PER_SOURCE_ALIASES = {
    "exports/crm/**/*.md": {"Sarah Brown": ["Sarah B.", "S. Brown"]},
    "exports/support/**/*.md": {"Sarah Okafor": ["Sarah O."]},
}

for pattern, aliases in PER_SOURCE_ALIASES.items():
    pipeline = Pipeline(
        TextFileLoader(pattern),
        transforms=[
            AliasCanonicalizer(aliases),
            TextChunker(chunk_size=500, overlap=50),
        ],
    )

    for warning in pipeline.preview(limit=None).warnings:
        print(warning)
```

An exhaustive preview reads every source file and holds the transformed episodes in memory, so allow time and memory proportional to the size of the export.

This name-based behavior applies to entities extracted from episodes. Directly added nodes are not deduplicated by name: each `graph.add_nodes` call creates new nodes. Keep the UUIDs Zep returns if you need to update those nodes later.

### Configure identity properties for extracted entities

Identity properties let Zep match extracted entities using an exact, type-specific value before considering name similarity. They are useful when two sources use different display names but share a value such as a company domain.

Identity properties are part of the Zep ontology API, not a `zep-ingest` transform. Configure them on the destination ontology before using `zep-ingest` to submit episodes, messages, or documents.

For example, two episodes may describe the same company differently:

```json
{"name": "Acme", "domain": "acme.com"}
{"name": "Acme Incorporated", "domain": "acme.com"}
```

Define `domain` as the identity property for the `Company` entity type:

```python
from zep_cloud.types import EntityProperty, EntityType

company = EntityType(
    name="Company",
    description="A company or legal organization",
    properties=[
        EntityProperty(
            name="domain",
            type="Text",
            description="The company's primary domain",
        )
    ],
    identity_properties=["domain"],
)
```

A property's `type` is one of `Text`, `Int`, `Float`, or `Boolean`.

If Zep classifies both extracted entities as `Company` and extracts `domain` on both, the exact `acme.com` identity match resolves them to the same node before name similarity or model-based resolution runs. If a record omits the domain or supplies a different value such as `www.acme.com`, the identity property does not match. Keep identity values consistent upstream.

Every identity property must be declared on the entity type. Multiple properties use AND semantics: all values must be present and match. Missing, null, empty, object, and array values do not form a match. String comparisons ignore surrounding whitespace but are otherwise exact.

| Ingestion path                         | How Zep identifies an entity                                                                 |
| -------------------------------------- | -------------------------------------------------------------------------------------------- |
| Episodes, messages, and documents      | Configured identity properties first; otherwise exact names followed by heuristic resolution |
| Direct nodes through `graph.add_nodes` | Each call creates a new node; Zep assigns the UUID. Names are not deduplicated               |

Identity properties travel on the `EntityType` model shown above. They are not carried by the typed ontology helper that takes `EntityModel` subclasses: that path converts each class to a name, a description, and a property list, so an identity property has no field to travel in and is discarded without an error. Set identity properties through the ontology surface in your SDK version that accepts `EntityType` values directly, and read the ontology back to confirm they were stored before you rely on them.

[Customizing graph structure](/customizing-graph-structure) covers entity and edge type modelling through `EntityModel` subclasses, which do not carry identity properties.

## Chunk documents and transcripts with zep-ingest

Zep rejects an episode larger than 10,000 characters rather than truncating it, so any longer document has to be split. Split at meaningful boundaries. Keep headings or source context with each chunk. For transcripts, preserve complete speaker turns where possible. When you submit the chunks through `graph.add` or the Batch API, pass the same [`document_id`](/documents) on every chunk.

If you use `zep-ingest`, the built-in `TextChunker` uses paragraph and sentence boundaries with configurable overlap:

```python
from zep_ingest import TextChunker

chunker = TextChunker(chunk_size=500, overlap=50)
```

`chunk_size` and `overlap` are counted in characters, not tokens or words. Do not chunk records that are already one self-contained unit at a natural size, such as a short support ticket or a CRM note; splitting them only breaks context that was already correct.

The package's optional `LLMContextualizer` can prepend source context to each chunk. It sends the chunk and document context to the LLM client you configure. Review that provider's data-handling terms before enabling it.

## Name and contextualize JSON records

Zep does not invent a name for an entity identified only by a code. A JSON record whose subject is an ID, such as an order, invoice, or account number, gets no subject node, so give it a human-readable `name` field, or, with `ingest_json_records`, map one with `name_field`.

A JSON record should also make sense in isolation, without relying on context outside the record. Add a `description` field, or other descriptive attribute names, when the record's meaning is not otherwise obvious.

## Set up an import manifest before your first write

Reingesting the same file creates new episodes, even when its contents are unchanged. `zep-ingest` does not currently deduplicate episodes by source record or content, and it cannot resume a run from the middle of a source. The identifiers that make an import recoverable are only available at the moment you submit, so the manifest has to exist before the first write rather than after the first failure.

Maintain an import manifest outside Zep until `zep-ingest` provides resume and idempotency support. At minimum, record:

* destination graph or user ID;
* source-system record ID;
* content hash;
* source modification time or export version;
* import run ID;
* returned Batch IDs, episode UUIDs, and task IDs;
* node UUIDs from any `graph.add_nodes` responses you need for later updates;
* terminal status.

Before submitting a record, compare its source ID and content hash with the last successful import. If the record changed, decide whether to add new historical evidence, update a directly managed node, or delete and replace earlier data. [Deleting data from the graph](/deleting-data-from-the-graph) describes what removing an episode, node, or thread takes with it.

If an import stops partway through, the manifest is what tells you which records to resubmit; see [Recover from a partial import](/zep-ingest#recover-from-a-partial-import).

Do not solve an empty search result by immediately rerunning the import. Processing complete and search ready are separate checkpoints; retrying during the short indexing window creates duplicate episodes. See [Monitor ingestion with `IngestResult`](/zep-ingest#monitor-ingestion-with-ingestresult) or [Check data ingestion status](/check-data-ingestion-status).

## Know the import limits

Exceeding any of these is a rejection, not degraded quality. Two of them constrain how you model your data rather than how you make a single call, so check them before you write ingestion code.

| Limit                                   | Value                        |
| --------------------------------------- | ---------------------------- |
| Episode content in one `graph.add` call | 10,000 characters            |
| Thread message content                  | 4,096 characters per message |
| Messages per `thread.add_messages` call | 30                           |
| Nodes per `graph.add_nodes` call        | 100                          |
| Entity types per project or graph       | 10                           |
| Edge types per project or graph         | 10                           |

Exceeding a payload limit returns a 400 Bad Request error. Chunk documents that are larger than the episode limit before submitting them; see [Chunking large documents](/chunking-large-documents). Pass the same [`document_id`](/documents) on every chunk of one source.

The entity-type and edge-type caps are counted separately rather than as a combined total, so 10 entity types and 10 edge types together are accepted. Enterprise and Flex Plus accounts are not subject to the 10-type caps, but no account can set more than 20 entity types or 20 edge types.

Setting an ontology replaces the entire type set for that scope. Types you leave out of the call are deleted, and nothing is merged. Read the current types, add to them, and submit the complete list. Check that total against the 10-type caps first: a read-modify-write that grows the list from 9 types to 11 is rejected, and it fails partway through an import rather than before it.

#### [How Graph Creation Works](/how-graph-creation-works)

How Zep extracts entities and facts from episodes and writes them into the graph.

#### [Create an Ingestion Pipeline](/zep-ingest)

Walk through ontology, optional aliases, seeding, and loading documents and conversations.