> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Create an Ingestion Pipeline

> Learn how to use zep-ingest to prepare, preview, submit, and monitor entities, relationships, documents, conversations, and structured records.

## What is an ingestion pipeline?

An ingestion pipeline prepares and loads existing data into Zep in a defined order. It canonicalizes identities and timestamps, validates records, submits them to a graph, and monitors processing.

`zep-ingest` is a Python package for building these pipelines. Use it to import documents, conversations, structured records, known entities, and relationships.

## When to use `zep-ingest`

`zep-ingest` is the recommended path for backfills and other imports where the data is already on disk. Every built-in loader takes a path or glob.

| What you are adding                                                                                             | Use                                       |
| --------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| A one-time backfill of your existing history (documents, transcripts, email, Slack exports, past conversations) | `zep-ingest`                              |
| A recurring export that lands files on disk — an hourly or nightly dump, or ETL output written to a folder      | `zep-ingest`                              |
| An individual document, API response, or webhook payload your application already holds in memory               | [`graph.add`](/adding-business-data)      |
| A message in a live conversation, as your agent sends and receives it                                           | [`thread.add_messages`](/adding-messages) |

A recurring on-disk export is the same work as a first backfill — normalize identities, preserve original timestamps, validate, submit in order, monitor — so the package handles both. You supply the trigger (a cron schedule or a step in an existing ETL job) that writes the files and then calls `zep-ingest`.

Live chat turns are deliberately out of scope. The Zep SDK already runs inside the service handling the conversation, a chat turn needs no preparation beyond the message itself, and `thread.add_messages` is one call. Use `zep-ingest` for conversations only when [backfilling historical threads](#backfill-thread-messages).

In-memory and webhook payloads are also out of scope for the built-in loaders. Prefer [`graph.add`](/adding-business-data) for those today. If you specifically want Pipeline transforms, preview, and Batch submission for an in-memory payload, you can write a custom loader — see [Ingest from an event rather than a folder](#ingest-from-an-event-rather-than-a-folder) — but that is an advanced path, not the recommended one.

You do not have to use `zep-ingest`. It is a convenience layer over the Zep SDK, so calling [`graph.add`](/adding-business-data) or the [Batch API](/adding-batch-data) directly is fully supported.

This guide explains the package through a company knowledge graph example. The same workflow applies to customer data, research collections, product catalogs, support archives, and other domains.

Start with [How Graph Creation Works](/how-graph-creation-works) for how episodes become graph state, then [Prepare Data for Ingestion](/prepare-data-for-ingestion) for the identity, context, timestamp, and rerun decisions this walkthrough applies.

`zep-ingest` 0.1 is an alpha Python package and requires Python 3.11 or later. Pin the package version and review its [changelog](https://github.com/getzep/zep/blob/main/ingestion/CHANGELOG.md) before upgrading.

The package ships runnable scripts and sample input data in its [examples directory](https://github.com/getzep/zep/tree/main/ingestion/examples). Each script creates its own graph or user, ingests the bundled data, and ends with a search, so you can run a complete import before adapting one to your own sources.

## Install and authenticate

`zep-ingest` depends on the Zep Cloud SDK, so one install brings in both packages:

```bash
pip install zep-ingest
```

LLM contextualization is optional. You only need an additional provider dependency when using `LLMContextualizer` to add document-level context to each chunk, covered at the end of the walkthrough:

```bash
pip install "zep-ingest[anthropic]"
# or
pip install "zep-ingest[openai]"
```

Every ingestion helper takes a Zep client as its first argument. Create one from your project API key before running any of the code below. The rest of this guide assumes this `client`:

```python
import os

from zep_cloud.client import Zep

client = Zep(api_key=os.environ["ZEP_API_KEY"])
```

## Example: Build a company knowledge graph

Imagine you are building an internal assistant that must answer questions such as:

* Who owns Customer Success?
* Which team maintains a product?
* What did the company decide about a launch?
* What do the handbook, Slack history, and meeting transcripts say about a policy?

The employee directory and org chart contain known entities and relationships. The handbook, Slack messages, meeting transcripts, and email contain source material for Zep to interpret.

Ingest them in this order:

1. Create the destination and set its ontology.
2. Seed people and teams from the employee directory.
3. Add reporting and ownership relationships from the org chart.
4. Canonicalize known aliases when sources use shorthand names for entities, you need reliably unique hubs, and you can normalize those names (see [Help entity deduplication](/prepare-data-for-ingestion#help-entity-deduplication)).
5. Prepare and preview the employee handbook.
6. Ingest the handbook and then the remaining documents and conversations in chronological order.

Seeding known entities first gives later extraction anchors to resolve against.

## Choose how to ingest each source

Choose a feature according to what the source represents, not its file format alone. When you have known structured data and already know which entities or relationships belong in the graph, write them directly. When the source contains material Zep should interpret, ingest it as episodes or messages.

| Ingestion path                  | Use it when                                                                                                                                                                                                           | Running example                                           |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `ingest_nodes`                  | You already know the canonical entities to seed                                                                                                                                                                       | People and teams from the employee directory              |
| `ingest_fact_triples`           | You already know a relationship between two entities and want to assert it exactly                                                                                                                                    | Leadership and ownership relationships from the org chart |
| `Pipeline` and `ingest`         | You need custom loading, transforms, preview, or chunking before creating episodes                                                                                                                                    | The employee handbook                                     |
| `ingest_slack_export`           | You have a Slack export and want speaker, channel, thread, and timestamp context preserved                                                                                                                            | Company public channels                                   |
| `ingest_documents`              | You have documents that should become chunked text episodes                                                                                                                                                           | Handbooks, policies, and internal documentation           |
| `ingest_transcripts`            | You have meeting transcripts that should be split at speaker-turn boundaries                                                                                                                                          | Recorded company meetings                                 |
| `ingest_emails`                 | You have `.eml` files and want sender, recipient, subject, date, and body context                                                                                                                                     | Internal correspondence                                   |
| `ingest_json_records`           | You have CSV, JSONL, or JSON records and want identity fields and timestamps mapped onto them                                                                                                                         | CRM, product, or operational records                      |
| `ingest_thread_messages`        | You are backfilling historical application conversations into user threads                                                                                                                                            | A support assistant's previous conversations              |
| A custom `Loader` with `ingest` | Your source needs application-specific parsing of files on disk, or you are deliberately routing an in-memory payload through Pipeline (advanced; prefer [`graph.add`](/adding-business-data) for most webhook cases) | A proprietary export                                      |

The directory and org chart should not go through extraction when they already contain the correct identities and relationships. Documents and conversations become episodes from which Zep extracts additional entities and facts.

## Choose where the data lands

Every import targets either a named graph or a user graph, and that choice sets the data model for everything after it.

Use a user graph when the data belongs to one person's memory. User graphs integrate directly with Zep's context retrieval, so their data reaches an agent through that user's context. Use a named graph for knowledge shared across users, such as a handbook, a product catalog, or an internal directory, which callers search explicitly. [Users and user graphs](/users-and-user-graphs) and [Create graph](/create-graph) cover the difference in full.

Pass `graph_id` for a named graph or `user_id` for a user graph. Every path in the table above accepts either one, except `ingest_thread_messages`, which writes messages into threads and therefore takes `user_id` only.

This example uses a named graph, because company knowledge is not one user's memory.

## Build the pipeline step by step

### 1. Create the graph and set its ontology

The Zep ontology defines the entity and relationship types Zep should extract. This is a Zep Cloud SDK operation rather than a `zep-ingest` transform, but it must happen before the package submits data because ontology changes are not retroactive.

Prescribed types are prescriptive, not restrictive. They tell Zep which types to look for; they do not filter what is ingested. Entities that do not match a prescribed type are still ingested, typed under the ontology Zep learns from the data. Prescribing three types therefore does not reduce the graph to three types, and setting an ontology at all is optional.

This company graph needs people, teams, and leadership relationships. Create the graph and set that ontology before adding nodes, facts, or episodes.

`zep-ingest` writes only into a destination that already exists; it never creates one for you. Create a named graph with `client.graph.create(graph_id=...)`, or a user graph with `client.user.add(user_id=...)`, before any ingestion call targets it. See [Create graph](/create-graph) and [Users and user graphs](/users-and-user-graphs). This holds for `ingest_thread_messages` too: it raises `ConfigurationError` when the user does not exist, rather than creating a bare one that skips the profile Zep relies on to anchor that user's identity. It does create the threads a backfill references, because those are containers for the import rather than a destination you provision.

```python
from zep_cloud import EntityEdgeSourceTarget
from zep_cloud.external_clients.ontology import EdgeModel, EntityModel


class Person(EntityModel):
    """A named individual, such as an employee or customer contact."""


class Team(EntityModel):
    """A named organizational team or department."""


class Leads(EdgeModel):
    """A person leads, manages, or is responsible for a team."""


ONTOLOGY = {
    "entities": {
        "Person": Person,
        "Team": Team,
    },
    "edges": {
        "LEADS": (
            Leads,
            [EntityEdgeSourceTarget(source="Person", target="Team")],
        ),
    },
}

graph_id = "company-kb"

client.graph.create(graph_id=graph_id)
client.graph.set_ontology(
    entities=ONTOLOGY["entities"],
    edges=ONTOLOGY["edges"],
    graph_ids=[graph_id],
)
```

Ontology changes are not retroactive. Data that Zep processed before `set_ontology` is not re-extracted or retyped automatically.

### 2. Add known entities with `ingest_nodes`

`ingest_nodes` creates entities through `graph.add_nodes`. Use it for canonical entities your application already knows, such as customers, employees, products, accounts, or teams. It bypasses entity extraction: the caller supplies the node's name, type, and attributes.

The directory already identifies Sarah Brown and the Customer Success team. Add them directly.

```python
from zep_ingest import NodeItem, ingest_nodes

nodes = ingest_nodes(
    client,
    [
        NodeItem(
            name="Sarah Brown",
            label="Person",
            summary="Customer success lead",
        ),
        NodeItem(
            name="Customer Success",
            label="Team",
            summary="The customer success organization",
        ),
    ],
    graph_id=graph_id,
    require_uuids=False,
)

nodes.wait(timeout=600)
nodes.raise_for_status()
```

`wait()` polls the completion handles the operation returned. When Zep accepts a write but returns no handle for it, the result counts those items as untracked and `wait()` raises `IngestUntrackedError` instead of blocking. Catch it and confirm the data another way, as described under monitoring below.

Waiting is always a call on the result, never an argument to the helper: bind what the helper returns, then call `wait()` on it with an optional `poll_interval` and `timeout`. That also leaves the result in hand to inspect when `IngestUntrackedError` is raised.

Zep assigns each node's UUID. You cannot supply one: a request that includes a node `uuid` is rejected with a `400`. Every accepted node is a new node, and nodes are not deduplicated by name or content, so rerunning the same direct-node import creates another set of nodes. `ingest_nodes` still defaults to requiring a client UUID; pass `require_uuids=False` because the API rejects caller-supplied node UUIDs. `IngestResult` tracks task IDs for monitoring and does not return the assigned node UUIDs — call `graph.add_nodes` directly when you need those UUIDs for `graph.node.update` or to pin fact-triple endpoints. This does not disable normal entity-name resolution elsewhere: entities extracted later from documents, messages, or episodes can still resolve to an existing node by name.

### 3. Add known relationships with `ingest_fact_triples`

`ingest_fact_triples` writes a fact you already know between two entities through `graph.add_fact_triple`. Each triple contains a source entity, a target entity, the fact statement, the relationship type, and optionally when the fact became valid.

Use these fields to describe the relationship:

* `source_node_name` and `target_node_name` identify the two entities.
* `fact` is the human-readable assertion, such as `Sarah Brown leads Customer Success`.
* `fact_name` is the relationship type in `SCREAMING_SNAKE_CASE`, such as `LEADS`.
* `source_node_uuid` and `target_node_uuid` pin the relationship to existing nodes when their UUIDs are known from a prior `add_nodes` response.
* `valid_at` records when the relationship became true.

The org chart states that Sarah leads Customer Success. Add that relationship directly:

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
    graph_id=graph_id,
)

facts.wait(timeout=600)
facts.raise_for_status()
```

As in step 2, `wait()` raises `IngestUntrackedError` if Zep returned no completion handle for a submitted triple.

**Why this matters:** raw dialogue does not guarantee extraction of a specific decision, action item, or relationship. Direct facts make known assertions explicit. When endpoint UUIDs are available from a prior seed, pinning them prevents a similar name from selecting the wrong node.

See [Manually updating the graph](/adding-fact-triplets) for the underlying node and fact-triple behavior.

### 4. Normalize aliases with `AliasCanonicalizer`

`AliasCanonicalizer` rewrites known aliases to one canonical entity name before Zep extracts entities. Use it when sources often use shorthand names for the same entity (ambiguous to [LLM extraction](/how-graph-creation-works) without more context), you need reliably unique hubs, and you can normalize those names — for example people in your organization across Slack and email. See [Help entity deduplication](/prepare-data-for-ingestion#help-entity-deduplication).

Skip this step if you already canonicalize entity names while exporting or preprocessing the source data, or if those conditions do not apply. In those cases, omit `AliasCanonicalizer` from the pipeline.

The employee directory says the person's full name is `Sarah Brown` and the official team name is `Customer Success`. Historical documents use several shorter forms. Define those mappings before loading documents and conversations.

```python
ALIASES = {
    "Sarah Brown": ["Sarah B.", "S. Brown"],
    "Customer Success": ["Customer Success Team", "CS Team"],
}
```

Only include an alias when the mapping is valid across the source you are processing. The bare first name `Sarah` is deliberately absent: add it only when the source contains exactly one Sarah. The default safety guard does not catch that case. It rejects aliases shorter than three characters and a list of common words and word-like given names, but an ordinary first name passes.

### 5. Build and preview an episode `Pipeline`

`Pipeline` connects a source `Loader` to ordered transforms, validates the complete output, and selects a submitter. Use `preview()` to inspect the transformed episodes and warnings without making Zep API calls.

The episode pipeline is:

```text
Loader → transforms → LimitGuard → Submitter
```

The handbook is source material Zep should interpret, so ingest it as episodes. Apply the company alias map before chunking. `Pipeline.preview()` runs the loader, transforms, and validation without making any Zep API calls.

`TextFileLoader` raises when its glob matches no files, so create the input before building the pipeline. A minimal `handbook/customer-success.md` that exercises the alias map:

```text
# Customer success

The Customer Success Team owns renewals and escalations. Sarah B. leads the
team. Escalations that involve a billing dispute are routed to Finance within
one business day.
```

For a larger ready-made input, the package's [examples/data](https://github.com/getzep/zep/tree/main/ingestion/examples/data) directory contains a sample handbook, Slack export, transcripts, email, and chat history.

```python
from zep_ingest import (
    AliasCanonicalizer,
    Pipeline,
    TextChunker,
    TextFileLoader,
)

pipeline = Pipeline(
    TextFileLoader("handbook/**/*.md", created_at="2025-06-01T00:00:00Z"),
    transforms=[
        AliasCanonicalizer(ALIASES),
        TextChunker(chunk_size=500, overlap=50),
    ],
)

preview = pipeline.preview(limit=20)

for episode in preview.episodes:
    print(episode.data_type, episode.created_at, episode.data[:120])

for warning in preview.warnings:
    print(warning)
```

A sampled preview validates at most `limit` episodes, and its warning counts cover only that sample. Before a production import, run `pipeline.preview(limit=None)` for an exhaustive pass over the whole stream, so a problem that first appears in episode 5,000 is not missed by a 20-episode check.

**Why this matters:** preview surfaces missing timestamps, automatic size-limit splits, and alias replacement counts before the first write. A transformation that is safe for ten sample records may be too broad across a complete export.

### 6. Ingest documents and conversations

The source-specific helpers package common loaders and transforms into one-line ingestion functions. Use them for material Zep should interpret, such as documents, conversations, transcripts, email, and structured business records.

When the preview is correct, run the same pipeline against the graph that already contains the ontology, canonical nodes, and known relationships:

```python
result = pipeline.run(
    client,
    graph_id=graph_id,
)
result.wait(timeout=3600)

print(result.method, result.status)
for warning in result.warnings:
    print(warning)
```

The pipeline validates and spools the full transformed stream before it submits the first episode. Large imports can spill to a temporary file instead of remaining in memory.

Once the handbook import behaves as expected, ingest the remaining company sources. Reuse the same alias map and preserve each source's original timestamps:

```python
from zep_ingest import ingest_emails, ingest_slack_export, ingest_transcripts

slack = ingest_slack_export(
    client,
    "exports/slack.zip",
    graph_id=graph_id,
    aliases=ALIASES,
)
slack.wait()

meetings = ingest_transcripts(
    client,
    "exports/meetings/**/*.vtt",
    graph_id=graph_id,
    aliases=ALIASES,
)
meetings.wait()

email = ingest_emails(
    client,
    "exports/email/**/*.eml",
    graph_id=graph_id,
    aliases=ALIASES,
)
email.wait()
```

The Slack call above ingests public channels only. A Slack export also indexes private channels, direct messages, and group direct messages, and none of them are read unless you name them in `conversation_types`, which accepts `public_channel`, `private_channel`, `dm`, and `group_dm`. That default depends on the export carrying those indexes. An export with none of `channels.json`, `groups.json`, `dms.json`, and `mpims.json` is typed by folder name instead, and folder names only go so far: direct-message and group-direct-message folders are still recognized and still excluded, but nothing distinguishes a private channel's folder from a public one, so a private channel is read as a public channel, ingested under the default, and recorded in the graph with a `conversation_type` of `public_channel`.

`result.warnings` carries two separate signals, so read both. Coverage: what the export held that `conversation_types` did not select. Fidelity, for an index-less export only: that conversation types could not be determined, how many folders were read as public channels, and a request to confirm none of them are private before trusting what lands in the graph. A private channel read as a public one is ingested, so it raises the fidelity warning and never appears in the coverage one.

Start with the oldest historical source and move forward. Within each source, keep events in their original order and retain their real `created_at` values.

### Optional: add document context with an LLM

`LLMContextualizer` prepends document-level context to each chunk so that a chunk read on its own still identifies what it is about. It costs one LLM call per chunk, so weigh it against the size of your import.

`LLMContextualizer` is provider-independent. The package includes:

* `AnthropicLLM` for the Anthropic API;
* `OpenAILLM` for the OpenAI API;
* `OpenAICompatibleLLM` for any provider, proxy, or local model that exposes an OpenAI-compatible `/chat/completions` endpoint.

The `openai` extra installs the client library used by both `OpenAILLM` and `OpenAICompatibleLLM`. It does not restrict you to OpenAI models. For a compatible provider, supply its model name and full base URL. Include the API-version path required by that provider, such as `/v1` or `/v1beta/openai/`.

For example, configure a Gemini model through Gemini's OpenAI-compatible endpoint:

```python
from zep_ingest.llm.openai import OpenAICompatibleLLM

gemini_llm = OpenAICompatibleLLM(
    model=os.environ["GEMINI_MODEL"],
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key=os.environ["GEMINI_API_KEY"],
)
```

Pass the adapter to a document helper:

```python
from zep_ingest import ingest_documents

result = ingest_documents(
    client,
    "documents/**/*.md",
    graph_id=graph_id,
    llm=gemini_llm,
)
result.wait()
```

The same adapter works with OpenAI-compatible endpoints from providers and gateways such as LiteLLM, Ollama, vLLM, OpenRouter, Together, or Groq. If a provider does not offer that interface, pass a custom client that implements `complete(prompt: str) -> str`.

See [Gemini's OpenAI compatibility documentation](https://ai.google.dev/gemini-api/docs/openai) for its current endpoint and model support.

## Ingest from an event rather than a folder

This section is for callers who specifically want Pipeline transforms, preview, and Batch submission for an in-memory payload. For most event-driven paths, call [`graph.add`](/adding-business-data) directly — it is simpler and does not require a custom loader.

Every built-in loader reads from a path or glob. A webhook hands your service a transcript in an HTTP body, and there is no file. If you still want to use `zep-ingest`, you have two options.

**Write the payload to a temporary file and use the built-in loader.** Do this when the payload is unparsed source text, such as raw WebVTT or a `Speaker: text` transcript. You keep `TranscriptLoader`'s speaker-turn detection, cue parsing, and turn-boundary chunking. Write the file first, then construct the loader with that path — `TranscriptLoader` resolves the path at construction time. The file is a handoff buffer rather than storage, so a `tempfile` path is enough.

**Write your own loader and skip the file.** Do this when the payload is already structured, which is typical of meeting-recording webhooks that deliver speaker-segmented JSON. Serializing that structure to text so a parser can re-derive it with regular expressions loses fidelity for no gain. Chunk at speaker-turn boundaries in the loader, the same way `TranscriptLoader` does for files — do not pipe the whole meeting through `TextChunker`, which splits on paragraphs and sentences and can cut mid-turn.

`Loader` is a structural protocol: any object with a `load()` method that yields `Episode` objects qualifies, and there is no base class to inherit.

```python
from zep_ingest import AliasCanonicalizer, Episode, Pipeline

CHUNK_CHARS = 3500  # same default as TranscriptLoader


class WebhookTranscriptLoader:
    """Renders a parsed webhook payload as turn-boundary transcript episodes."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def load(self):
        turns = [
            f"{turn['speaker']}: {turn['text']}" for turn in self.payload["segments"]
        ]
        # Chunk at turn boundaries so a speaker attribution is never split mid-turn.
        chunks: list[list[str]] = []
        current: list[str] = []
        size = 0
        for turn in turns:
            turn_size = len(turn) + 1
            if current and size + turn_size > CHUNK_CHARS:
                chunks.append(current)
                current, size = [], 0
            current.append(turn)
            size += turn_size
        if current:
            chunks.append(current)

        started_at = self.payload["started_at"]  # RFC3339 with timezone offset
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            yield Episode(
                data="\n".join(chunk),
                data_type="text",
                created_at=started_at,
                metadata={
                    "source_type": "transcript",
                    "call_id": self.payload["call_id"],
                    "chunk": f"{index}/{total}",
                },
            )


pipeline = Pipeline(
    WebhookTranscriptLoader(payload),
    transforms=[AliasCanonicalizer(ALIASES)],
)
result = pipeline.run(client, graph_id=graph_id)
```

`created_at` must include a timezone offset (for example `2025-03-01T10:00:00Z`); the package rejects naive timestamps before any API call. Episodes that still exceed the size limit after your transforms are split by the pipeline's always-on `LimitGuard`.

Everything else behaves as it does for a backfill: preview, submission, and `IngestResult` monitoring.

### Run the ingestion outside the webhook request

Do not ingest inside the webhook handler. Webhook senders expect a prompt response and retry when they do not get one, so a handler that waits on ingestion invites duplicate deliveries. Acknowledge the delivery, enqueue the payload, and ingest from a worker.

`zep-ingest` does not deduplicate. Submitting the same transcript twice creates a second set of episodes, and Zep does not merge episodes by content. A backfill is run once under supervision, but an event-driven path retries automatically, so the caller has to enforce idempotency: record a key from the source event, such as a call or message ID, and skip any payload already recorded as ingested.

## Choose a submission method

The package submitter controls how episode pipelines reach Zep. `auto` chooses the best available path, while `batch` and `sequential` let you require a particular submission behavior.

Episode pipelines accept `method="auto"`, `method="batch"`, or `method="sequential"`.

| Method       | Behavior                                                                                                                               |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| `auto`       | Uses the Batch API when your deployment serves it, and falls back to sequential `graph.add` calls when it does not                     |
| `batch`      | Requires the Batch API and fails if your deployment does not serve it; submits up to 350 items per add call and 50,000 items per batch |
| `sequential` | Uses rate-limit-aware `graph.add` calls                                                                                                |

Use `auto` unless you need to require a specific path. Falling back is narrower than it sounds: it happens only when the deployment does not serve the Batch API at all. Any other refusal, such as a rejected key or an exhausted quota, is raised as it is, because sequential submission would meet the same refusal one call at a time. When the fallback does happen, the result reports it in `warnings`.

If Batch becomes unavailable after part of a multi-batch import has already been submitted, the package stops instead of resubmitting the complete source sequentially and creating duplicate episodes. See [Recover from a partial import](#recover-from-a-partial-import) for what to do next.

`ingest_thread_messages` takes the same `method` argument and resolves it the same way: `auto` submits through the Batch API and falls back to sequential `thread.add_messages` when your deployment does not serve it. The same anti-duplication rule applies — if Batch becomes unavailable after part of a multi-batch backfill has already been submitted, the package stops rather than resubmitting and duplicating messages.

## Backfill thread messages

`ingest_thread_messages` writes historical application conversations to threads on a user graph. It requires the user to already exist, creates any missing threads, preserves the order of messages within each thread, and splits messages that exceed the thread-message limit.

```python
from zep_ingest import ingest_thread_messages

result = ingest_thread_messages(
    client,
    "chat-history.jsonl",
    user_id="user-123",
    method="auto",
)
```

Each row must use exactly these fields, and no others:

```json
{"thread_id": "support-4417", "role": "user", "name": "Sarah Brown", "content": "The renewal quote has the wrong term length.", "created_at": "2025-03-01T10:00:00Z", "metadata": {"channel": "web"}}
```

`thread_id`, `role`, `name`, `content`, and `created_at` are all required; only `metadata` is optional. Carrying the speaker type, speaker name, and original timestamp on every row is what lets a backfill reconstruct both the conversation and its fact validity timeline. `role` must be one of `user`, `assistant`, `system`, `function`, `tool`, or `norole`; a source-system value such as `human` is rejected. `created_at` is RFC3339, for example `2025-03-01T10:00:00Z`.

Any other column is rejected outright rather than ignored, because silently dropping a misspelled field produces an import that looks complete and is not. Real chat exports usually carry extra columns such as an internal message ID or a channel name, so strip or rename them, and map source roles to the values above, before ingesting.

`created_at` is required on every thread message, and the package sends it on both the Batch and sequential paths.

Thread IDs are unique across a Zep project. Use `thread_id_suffix` when an imported ID might collide with an existing thread. A suffix namespaces the import; it does not make reruns idempotent.

On the sequential path, `messages_per_call` sets how many messages go in each `thread.add_messages` call. It defaults to 30, which is also the maximum; a larger value is rejected when the arguments are checked, before the user and threads are touched.

**Why this matters:** if historical messages are dated at ingestion time, newer-looking backfill facts can invalidate facts that occurred later in the real timeline.

## Monitor ingestion with `IngestResult`

`IngestResult` gives every helper a consistent interface for processing status, errors, and monitoring identifiers across Batch, episode, and task-backed operations.

```python
result.status
result.refresh()
result.wait(timeout=3600)
result.failed_items()
result.raise_for_status()

print(result.batch_ids)
print(result.episode_uuids)
print(result.task_ids)
print(result.untracked_items)
```

`status` aggregates every handle in the result and reports the least-complete one: `failed`, `partial`, `canceled`, `untracked`, `processing`, `queued`, or `succeeded`.

Persist the returned IDs if another process will monitor the import:

```python
from zep_ingest import IngestResult

result = IngestResult.from_batch_ids(client, saved_batch_ids)
result.wait(timeout=3600)

task_result = IngestResult.from_task_ids(client, saved_task_ids)
task_result.wait(timeout=3600)
```

`wait()` polls the Batch, episode, or task handles returned by the operation until they reach a terminal state.

Some accepted writes come back without a completion handle. When that happens the result counts them in `untracked_items`, `status` reports `untracked`, and `wait()` raises `IngestUntrackedError` immediately rather than polling forever. The submission itself succeeded; only server-side extraction is untrackable. Catch the error and confirm the data landed with your own read, such as `search_when_ready`:

```python
from zep_ingest import IngestUntrackedError, search_when_ready

try:
    result.wait(timeout=3600)
except IngestUntrackedError:
    search_when_ready(client, "Who leads customer success?", graph_id=graph_id)
```

Sequential thread ingestion tracks a task handle when `thread.add_messages` returns one, and counts the messages as untracked when it does not.

Search indexing can take additional time after processing succeeds. Use `search_when_ready` when a script must ingest and then immediately check retrieval:

```python
from zep_ingest import search_when_ready

search_results = search_when_ready(
    client,
    "Who leads customer success?",
    graph_id="company-kb",
    scope="edges",
    timeout=120,
)
```

`search_when_ready` retries until the query returns any result or reaches the timeout. It does not verify that a specific imported record produced the result. Use a query unique to the imported data when checking indexing readiness. An empty result after the timeout is returned normally.

**Why this matters:** a completed ingestion task and a searchable graph are separate states. Retrying the import during the indexing window creates duplicate episodes without making indexing complete sooner.

For lower-level polling and webhook options, see [Check data ingestion status](/check-data-ingestion-status).

## Recover from a partial import

The package stops on an unrecoverable submission error rather than resubmitting a source from the beginning, which would duplicate everything already accepted. It does not resume, so recovery is a procedure you run, not a flag you pass. It depends on the [import manifest](/prepare-data-for-ingestion#set-up-an-import-manifest-before-your-first-write) you recorded at submit time.

1. Establish what was accepted. Reconstruct the result from the identifiers in your manifest with `IngestResult.from_batch_ids` or `IngestResult.from_task_ids`, then call `refresh()` and read `status`.
2. List what failed. `failed_items()` returns the submission errors the package recorded and, for a Batch import, the per-item failures Zep reported. Each entry identifies the item, so you can map it back to a source record through your manifest.
3. Rebuild the input from the records your manifest does not show as accepted. Do not rerun the original source: reingesting an accepted record creates a second episode for it, because Zep does not deduplicate episodes by content.
4. Submit that remainder as a normal import, and record its identifiers in the manifest as well.

If the manifest is incomplete and you cannot tell which records landed, treat the destination as unusable rather than reingesting over it. Delete the affected data and start again. [Deleting data from the graph](/deleting-data-from-the-graph) describes what deleting an episode, node, or thread removes.

## Current limitations

* Rerunning the same export creates new episodes. `zep-ingest` does not yet provide a source manifest, content-hash deduplication, or resume checkpoint.
* `batch_ids`, `episode_uuids`, and `task_ids` let you resume status monitoring. They do not resume parsing or submission from the middle of a source.
* Accepted writes are sometimes returned without a completion handle. The result counts them in `untracked_items` and `wait()` raises `IngestUntrackedError`, so confirming those items requires your own read.
* `LLMContextualizer` sends document content to the LLM client you provide. Confirm that the provider and deployment meet your data-handling requirements.
* Retrieval ranking, authority weighting, and contradiction policy are outside the package's scope.

Review [Prepare Data for Ingestion](/prepare-data-for-ingestion) before running a production import.