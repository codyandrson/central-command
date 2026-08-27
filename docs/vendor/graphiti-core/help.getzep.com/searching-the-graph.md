> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Searching the Graph

#### Custom Context Blocks

Graph search results should be used in conjunction with [Advanced Context Block Construction](/cookbook/advanced-context-block-construction) to create contextual prompts for AI models. Custom context blocks allow you to format and structure the retrieved graph information, combining search results with conversation history and other relevant data to provide context for your AI applications.

Learn how to integrate graph search results into your context generation workflow for grounded responses.

## Introduction

Zep's graph search combines semantic similarity with BM25 full-text search to find relevant information across your knowledge graph. It uses semantic understanding for conceptual matches and full-text search for exact terms. Additionally, you can optionally enable breadth-first search to bias results toward information connected to specific starting points in your graph.

### How It Works

* **Semantic similarity**: Converts queries into embeddings to find conceptually similar content
* **BM25 full-text search**: Performs traditional keyword-based search for exact matches
* **Breadth-first search** (optional): Biases results toward information connected to specified starting nodes, useful for contextual relevance
* **Hybrid results**: Combines and reranks results using reciprocal rank fusion (RRF)

### Graph Concepts

* **Nodes**: Connection points representing entities (people, places, concepts) discussed in conversations or added via the Graph API
* **Edges**: Relationships between nodes containing specific facts and interactions

The example below demonstrates a simple search:

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

search_results = client.graph.search(
    user_id=user_id,
    query=query,
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const searchResults = await client.graph.search({
  userId: userId,
  query: query,
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    UserID: zep.String(userID),
    Query:  query,
})
```

#### Best Practices

Keep queries short: they are truncated at 400 characters. Long queries may increase latency without improving search quality.
Break down complex searches into smaller, targeted queries. Use precise, contextual queries rather than generic ones

#### Looking for one-call context retrieval?

For most assistant use cases, set `scope="auto"` and let Zep dynamically compose the most relevant context across edges, nodes, episodes, observations, and thread summaries into a single ready-to-use block. See [Auto Search](#auto-search) below.

## Auto Search

Auto search is the recommended entry point to graph retrieval. Instead of asking you to pre-commit to a single result type — facts, entity summaries, raw messages, observations, or thread summaries — auto search retrieves across all of them in parallel, applies a cross-scope rerank, and dynamically composes the most relevant results into a single context block sized to a character budget you control.

The output is a drop-in string you can paste straight into your LLM prompt. There is no client-side stitching, no scope-picking heuristic to maintain, and no need to make multiple search calls to cover different data shapes.

### What auto search does

* **Composes across all data shapes in one call.** A single query returns the most relevant material whether it lives in graph facts, entity summaries, raw episodes, derived observations, or per-thread summaries.
* **Ranks globally, not per-scope.** Auto search applies its own internal cross-scope rerank so results are ordered by overall relevance to the query — a strong observation can outrank a weaker edge, and vice versa.
* **Packs to a character budget.** The returned context block is materialized to fit within `max_characters`, giving you predictable, prompt-window-friendly output.
* **Returns a ready-to-use context block.** The `context` field is the primary output: a formatted string designed to be inserted directly into a system prompt or message.
* **Optionally exposes the underlying results.** Set `return_raw_results=true` to also receive the selected items as typed arrays — useful for inspection, citation, or building custom context blocks on top of auto's selection.

### Relative time in auto search

Auto search can interpret relative calendar language as a retrieval constraint. For example:

* `What happened yesterday?`
* `Updates from last week`
* `What changed this month?`

For supported relative requests, Zep uses a machine learning model to determine whether the phrase expresses a temporal retrieval constraint in the context of the query. When it does, Zep resolves the corresponding calendar window and restricts the retrieved facts and episodes automatically. You do not need to calculate timestamps or construct explicit date filters.

The window uses the time zone stored for the target user or graph. If that value is unavailable or invalid, Zep uses the project's default time zone, then UTC.

If Zep is not sufficiently confident that the phrase is a temporal constraint, it runs normal auto search without forcing a time window. When temporal filtering is applied, the returned `context` block states that evidence was restricted and includes the UTC interval and the time zone used to calculate it.

This interpretation applies only to `scope="auto"`. It is separate from [explicit datetime filters](#datetime-filtering), which let you choose timestamp fields and boundaries for edge searches. A caller-supplied temporal filter takes precedence, so Zep does not also infer a window from the query.

### How to use it

Set `scope="auto"` and (optionally) `max_characters` to bound the size of the returned context block. `max_characters` defaults to `2500` and is capped at `50000`. Zep selects results across scopes, applies its internal cross-scope rerank, and packs the top-ranked results into the context block until the character budget is reached.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

search_results = client.graph.search(
    user_id=user_id,
    query="What did we decide about the pricing rollout?",
    scope="auto",
    max_characters=2500,
)

# The materialized context block, ready to drop into a prompt
print(search_results.context)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const searchResults = await client.graph.search({
  userId: userId,
  query: "What did we decide about the pricing rollout?",
  scope: "auto",
  maxCharacters: 2500,
});

// The materialized context block, ready to drop into a prompt
console.log(searchResults.context);
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    UserID:        zep.String(userID),
    Query:         "What did we decide about the pricing rollout?",
    Scope:         zep.GraphSearchScopeAuto.Ptr(),
    MaxCharacters: zep.Int(2500),
})
```

### When to use auto vs. a specific scope

| Use auto when...                                                | Use a specific scope when...                                                |
| --------------------------------------------------------------- | --------------------------------------------------------------------------- |
| You want the best available context for an arbitrary user query | You know exactly which data shape you need (e.g. just facts, just entities) |
| The right result type varies query-by-query                     | You're driving a UI that renders one result type (e.g. an entity browser)   |
| You want a ready-to-prompt context block                        | You need to programmatically merge results with other data                  |
| You want Zep to manage cross-scope ranking for you              | You need fine-grained control over reranker, filters, or BFS per scope      |

### Response format

Auto search returns a `GraphSearchResults` object with two parts:

* **`context`** *(string)* — A single materialized context block composed from the highest-ranked results across scopes, packed up to `max_characters`. This is the primary output of auto search and is intended to be passed directly to your LLM. With other scopes, `context` is empty; with `scope="auto"` it is always populated.
* **Raw selected results** *(arrays)* — When `return_raw_results=true`, the response also includes the underlying selected results as typed arrays: `edges`, `nodes`, `episodes`, `observations`, and `thread_summaries`. Only the result types Zep chose for this query will be non-empty. Each item carries a `selection_rank` field — the global cross-scope rank assigned by auto selection — which you can use to reconstruct the order Zep used when building the context block. By default `return_raw_results=false` and only `context` is returned.

```json Example response (return_raw_results=true)
{
  "context": "Pricing rollout decisions:\n- Approved tiered pricing for Q3, with grandfathering for existing enterprise contracts...\n\nRelated discussion:\n- 2026-04-22: Eng and Finance agreed to delay the enterprise tier by two weeks...\n",
  "edges": [
    {
      "uuid": "...",
      "fact": "Engineering and Finance agreed to delay the enterprise tier by two weeks",
      "selection_rank": 2,
      "score": 0.81
    }
  ],
  "observations": [
    {
      "uuid": "...",
      "summary": "Pricing rollout decisions",
      "selection_rank": 1
    }
  ],
  "episodes": [],
  "nodes": [],
  "thread_summaries": []
}
```

When `scope="auto"`, the `reranker` parameter is ignored entirely. Auto search applies its own internal cross-scope rerank to order results before packing the context block, so any value you pass for `reranker` has no effect.

## Configurable Parameters

Zep provides extensive configuration options to fine-tune search behavior and optimize results for your specific use case:

| Parameter               | Type    | Description                                                                                                                                                                                                                                                                                                                                                                                                                                     | Default   | Required |
| ----------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- | -------- |
| `graph_id`              | string  | Search within a graph                                                                                                                                                                                                                                                                                                                                                                                                                           | -         | Yes\*    |
| `user_id`               | string  | Search within a user graph                                                                                                                                                                                                                                                                                                                                                                                                                      | -         | Yes\*    |
| `query`                 | string  | Search text (max 400 characters)                                                                                                                                                                                                                                                                                                                                                                                                                | -         | Yes      |
| `scope`                 | string  | Search target: `"auto"`, `"edges"`, `"nodes"`, `"episodes"`, `"observations"`, or `"thread_summaries"`                                                                                                                                                                                                                                                                                                                                          | `"edges"` | No       |
| `reranker`              | string  | Reranking method: `"rrf"`, `"mmr"`, `"node_distance"`, `"episode_mentions"`, or `"cross_encoder"`                                                                                                                                                                                                                                                                                                                                               | `"rrf"`   | No       |
| `limit`                 | integer | Maximum number of results to return                                                                                                                                                                                                                                                                                                                                                                                                             | `10`      | No       |
| `max_characters`        | integer | Maximum total characters across all selected results when `scope="auto"`. Limited to `50000`.                                                                                                                                                                                                                                                                                                                                                   | `2500`    | No¶      |
| `return_raw_results`    | boolean | When `scope="auto"`, also return the selected raw results alongside the materialized context block.                                                                                                                                                                                                                                                                                                                                             | `false`   | No¶      |
| `mmr_lambda`            | float   | MMR diversity vs relevance balance (0.0-1.0)                                                                                                                                                                                                                                                                                                                                                                                                    | -         | No†      |
| `center_node_uuid`      | string  | Center node for distance-based reranking                                                                                                                                                                                                                                                                                                                                                                                                        | -         | No‡      |
| `search_filters`        | object  | Filter by entity types (`node_labels`), edge types (`edge_types`), exclude entity types (`exclude_node_labels`), exclude edge types (`exclude_edge_types`), custom properties (`property_filters`), episode metadata (`episode_metadata_filters`), connected nodes (`connected_node_uuids`, `source_node_uuids`, `target_node_uuids`), source episodes (`episode_uuids`), or timestamps (`created_at`, `expired_at`, `invalid_at`, `valid_at`§) | -         | No       |
| `bfs_origin_node_uuids` | array   | Node UUIDs to seed breadth-first searches from                                                                                                                                                                                                                                                                                                                                                                                                  | -         | No       |

\*Either `user_id` OR `graph_id` is required
†Required when using `mmr` reranker\
‡Required when using `node_distance` reranker\
§Timestamp filtering only applies to edge scope searches\
¶Only applies when `scope="auto"`

## Search Scopes

If you need fine-grained control over what is retrieved, you can target a specific scope directly instead of using [Auto Search](#auto-search). Zep supports five scopes, each optimized for a different shape of information:

### Edges (Default)

Edges represent individual relationships and facts between entities in your graph. They contain specific interactions, conversations, and detailed information. Edge search is ideal for:

* Finding specific details or conversations
* Retrieving precise facts about relationships
* Getting granular information about interactions

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

# Edge search (default scope)
search_results = client.graph.search(
    user_id=user_id,
    query="What did John say about the project?",
    scope="edges",  # Optional - this is the default
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Edge search (default scope)
const searchResults = await client.graph.search({
  userId: userId,
  query: "What did John say about the project?",
  scope: "edges", // Optional - this is the default
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

// Edge search (default scope)
searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    UserID: zep.String(userID),
    Query:  "What did John say about the project?",
    Scope:  zep.GraphSearchScopeEdges.Ptr(), // Optional - this is the default
})
```

### Nodes

Nodes are connection points in the graph that represent entities. Each node maintains a summary of facts from its connections (edges), providing a comprehensive overview. Node search is useful for:

* Understanding broader context around entities
* Getting entity summaries and overviews
* Finding all information related to a specific person, place, or concept

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

search_results = client.graph.search(
    graph_id=graph_id,
    query="John Smith",
    scope="nodes",
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const searchResults = await client.graph.search({
  graphId: graphId,
  query: "John Smith",
  scope: "nodes",
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    GraphID: zep.String(graphID),
    Query:   "John Smith",
    Scope:   zep.GraphSearchScopeNodes.Ptr(),
})
```

### Episodes

Episodes represent individual messages or chunks of data sent to Zep. Episode search allows you to find relevant episodes based on their content, making it ideal for:

* Finding specific messages or data chunks related to your query
* Discovering when certain topics were mentioned
* Retrieving relevant individual interactions
* Understanding the context of specific messages

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

search_results = client.graph.search(
    user_id=user_id,
    query="project discussion",
    scope="episodes",
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const searchResults = await client.graph.search({
  userId: userId,
  query: "project discussion",
  scope: "episodes",
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    UserID: zep.String(userID),
    Query:  "project discussion",
    Scope:  zep.GraphSearchScopeEpisodes.Ptr(),
})
```

### Observations

Observations are durable, evidence-backed memories Zep automatically derives from a graph's recent activity, capturing meaningful changes, decisions, commitments, preferences, and recurring patterns across one or more entities. Observation search is useful for:

* Surfacing cross-entity context that spans many facts
* Retrieving persistent behavioral patterns or stable relationships
* Grounding responses in higher-level memories rather than granular edges

See [Observations](/observations) for more details on how observations are produced and retrieved.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

search_results = client.graph.search(
    user_id=user_id,
    query="account suspension and recovery",
    scope="observations",
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const searchResults = await client.graph.search({
  userId: userId,
  query: "account suspension and recovery",
  scope: "observations",
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    UserID: zep.String(userID),
    Query:  "account suspension and recovery",
    Scope:  zep.GraphSearchScopeObservations.Ptr(),
})
```

### Thread Summaries

Thread summaries are per-thread, incremental summaries of the messages in a single conversation. Thread summary search is useful for:

* Surfacing the most relevant past conversations across a user's threads
* Pulling thread-level recaps into a custom context block
* Building features that need a different view of a user's history at the conversation level

See [Thread summaries](/thread-summaries) for more details on how thread summaries are produced and retrieved.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

search_results = client.graph.search(
    user_id=user_id,
    query="payment failures and account recovery",
    scope="thread_summaries",
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const searchResults = await client.graph.search({
  userId: userId,
  query: "payment failures and account recovery",
  scope: "thread_summaries",
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    UserID: zep.String(userID),
    Query:  "payment failures and account recovery",
    Scope:  zep.GraphSearchScopeThreadSummaries.Ptr(),
})
```

## Rerankers

Zep provides multiple reranking algorithms to optimize search results for different use cases. Each reranker applies a different strategy to prioritize and order results:

### RRF (Reciprocal Rank Fusion)

<a name="reciprocal-rank-fusion" />

Reciprocal Rank Fusion is the default reranker that combines results by each result's rank position in both the semantic similarity and BM25 full-text searches. It merges the two result sets by considering the rank position of each result in both searches, creating a unified ranking that leverages the strengths of both approaches.

**When to use**: RRF is ideal for most general-purpose search scenarios where you want balanced results combining conceptual understanding with exact keyword matching.

**Score interpretation**: RRF scores combine semantic similarity and keyword matching by summing reciprocal ranks (1/rank) from both search methods, resulting in higher scores for results that perform well in both approaches. Scores don't follow a fixed 0-1 scale but rather reflect the combined strength across both search types, with higher values indicating better overall relevance.

### MMR (Maximal Marginal Relevance)

<a name="maximal-marginal-re-ranking" />

Maximal Marginal Relevance addresses a common issue in similarity searches: highly similar top results that don't add diverse information to your context. MMR reranks results to balance relevance with diversity, promoting varied but still relevant results over redundant similar ones.

**When to use**: Use MMR when you need diverse information for comprehensive context, such as generating summaries, answering complex questions, or avoiding repetitive results.

**Required parameter**: `mmr_lambda` (0.0-1.0) - Controls the balance between relevance (1.0) and diversity (0.0). A value of 0.5 provides balanced results.

**Score interpretation**: MMR scores balance relevance with diversity based on your mmr\_lambda setting, meaning a moderately relevant but diverse result may score higher than a highly relevant but similar result. Interpret scores relative to your lambda value: with lambda=0.5, moderate scores may indicate valuable diversity rather than poor relevance.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

search_results = client.graph.search(
    user_id=user_id,
    query="project status",
    reranker="mmr",
    mmr_lambda=0.5, # Balance diversity vs relevance
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const searchResults = await client.graph.search({
  userId: userId,
  query: "project status",
  reranker: "mmr",
  mmrLambda: 0.5, // Balance diversity vs relevance
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    UserID:    zep.String(userID),
    Query:     "project status",
    Reranker:  zep.GraphSearchQueryRerankerMmr.Ptr(),
    MmrLambda: zep.Float64(0.5), // Balance diversity vs relevance
})
```

### Cross Encoder

`cross_encoder` uses a specialized neural model that jointly analyzes the query and each search result together, rather than analyzing them separately. This provides more accurate relevance scoring by understanding the relationship between the query and potential results in a single model pass.

**When to use**: Use cross encoder when you need the highest accuracy in relevance scoring and are willing to trade some performance for better results. Ideal for critical searches where precision is paramount.

**Trade-offs**: Higher accuracy but slower performance compared to other rerankers.

**Score interpretation**: Cross encoder scores follow a sigmoid curve (`0-1` range) where highly relevant results cluster near the top with scores that decay rapidly as relevance decreases. You'll typically see a sharp drop-off between truly relevant results (higher scores) and less relevant ones, making it easy to set meaningful relevance thresholds.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

search_results = client.graph.search(
    user_id=user_id,
    query="critical project decision",
    reranker="cross_encoder",
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const searchResults = await client.graph.search({
  userId: userId,
  query: "critical project decision",
  reranker: "cross_encoder",
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    UserID:   zep.String(userID),
    Query:    "critical project decision",
    Reranker: zep.GraphSearchQueryRerankerCrossEncoder.Ptr(),
})
```

### Episode Mentions

`episode_mentions` reranks edge candidates by how many of the episodes listed in `search_filters.episode_uuids` mention them, from most to least mentioned. Node results are filtered by mention but not reranked by this reranker.

**Required parameter**: `search_filters.episode_uuids` - the episode UUIDs to count mentions against. Without it, `episode_mentions` has no effect and results are ranked as if no reranker were specified.

**When to use**: Use episode mentions when you already have a set of episode UUIDs (for example, from an episode search or a specific conversation) and want to prioritize graph results that those episodes reference most.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

search_results = client.graph.search(
    user_id=user_id,
    query="team feedback",
    reranker="episode_mentions",
    search_filters={
        "episode_uuids": [episode_uuid_1, episode_uuid_2]
    },
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const searchResults = await client.graph.search({
  userId: userId,
  query: "team feedback",
  reranker: "episode_mentions",
  searchFilters: {
    episodeUuids: [episodeUuid1, episodeUuid2]
  },
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchFilters := zep.SearchFilters{EpisodeUUIDs: []string{episodeUUID1, episodeUUID2}}
searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    UserID:        zep.String(userID),
    Query:         "team feedback",
    Reranker:      zep.GraphSearchQueryRerankerEpisodeMentions.Ptr(),
    SearchFilters: &searchFilters,
})
```

### Node Distance

`node_distance` reranks search results based on graph proximity, prioritizing results that are closer (fewer hops) to a specified center node. This spatial approach to relevance is useful for finding information contextually related to a specific entity or concept.

**When to use**: Use node distance when you want to find information specifically related to a particular entity, person, or concept in your graph. Ideal for exploring the immediate context around a known entity.

**Required parameter**: `center_node_uuid` - The UUID of the node to use as the center point for distance calculations.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

search_results = client.graph.search(
    user_id=user_id,
    query="recent activities",
    reranker="node_distance",
    center_node_uuid=center_node_uuid,
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const searchResults = await client.graph.search({
  userId: userId,
  query: "recent activities",
  reranker: "node_distance",
  centerNodeUuid: centerNodeUuid,
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    UserID:         zep.String(userID),
    Query:          "recent activities",
    Reranker:       zep.GraphSearchQueryRerankerNodeDistance.Ptr(),
    CenterNodeUuid: zep.String(centerNodeUuid),
})
```

### Reranker Score

Graph search results include a reranker score that provides a measure of relevance for each returned result. This score is available when using any reranker and is returned on any node, edge, or episode from `graph.search`. The reranker score can be used to manually filter results to only include those above a certain relevance threshold, allowing for more precise control over search result quality.

The interpretation of the score depends on which reranker is used. For example, when using the `cross_encoder` reranker, the score follows a sigmoid curve with the score decaying rapidly as relevance decreases. For more information about the score field in the response, see the [SDK reference](https://help.getzep.com/sdk-reference/graph/search#response.body.edges.score).

#### Relevance Score

When using the `cross_encoder` reranker, search results include an additional `relevance` field alongside the `score` field. The `relevance` field is a rank-aligned score in the range \[0, 1] derived from the existing sigmoid-distributed `score` to improve interpretability and thresholding.

**Key characteristics:**

* Range: \[0, 1]
* Only populated when using the `cross_encoder` reranker
* Preserves the ranking order produced by Zep's reranker
* Not a probability; it is a monotonic transform of `score` to reduce saturation near 1
* Use `relevance` for sorting, filtering, and analytics

The `relevance` field provides a more intuitive metric for evaluating search result quality compared to the raw `score`, making it easier to set meaningful thresholds and analyze results.

## Search Filters

Zep allows you to filter search results by specific entity types or edge types, enabling more targeted searches within your graph.

### Entity Type Filtering

Filter search results to only include nodes of specific entity types. This is useful when you want to focus on particular kinds of entities (e.g., only people, only companies, only locations).

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

search_results = client.graph.search(
    user_id=user_id,
    query="software engineers",
    scope="nodes",
    search_filters={
        "node_labels": ["Person", "Company"]
    }
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const searchResults = await client.graph.search({
  userId: userId,
  query: "software engineers",
  scope: "nodes",
  searchFilters: {
    nodeLabels: ["Person", "Company"]
  }
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchFilters := zep.SearchFilters{NodeLabels: []string{"Person", "Company"}}
searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    UserID:        zep.String(userID),
    Query:         "software engineers",
    Scope:         zep.GraphSearchScopeNodes.Ptr(),
    SearchFilters: &searchFilters,
})
```

### Edge Type Filtering

Filter search results to only include edges of specific relationship types. This helps you find particular kinds of relationships or interactions between entities.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

search_results = client.graph.search(
    user_id=user_id,
    query="project collaboration",
    scope="edges",
    search_filters={
        "edge_types": ["WORKS_WITH", "COLLABORATES_ON"]
    }
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const searchResults = await client.graph.search({
  userId: userId,
  query: "project collaboration",
  scope: "edges",
  searchFilters: {
    edgeTypes: ["WORKS_WITH", "COLLABORATES_ON"]
  }
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchFilters := zep.SearchFilters{EdgeTypes: []string{"WORKS_WITH", "COLLABORATES_ON"}}
searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    UserID:        zep.String(userID),
    Query:         "project collaboration",
    Scope:         zep.GraphSearchScopeEdges.Ptr(),
    SearchFilters: &searchFilters,
})
```

### Exclusion Filters

Exclusion filters allow you to exclude specific entity types or edge types from your search results. This is useful when you want to filter out certain types of information while keeping all others.

#### Excluding Node Labels

Exclude specific entity types from node or edge search results. When searching edges, nodes connected to the edges are also checked against exclusion filters.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

# Exclude certain entity types from results
search_results = client.graph.search(
    user_id=user_id,
    query="project information",
    scope="nodes",
    search_filters={
        "exclude_node_labels": ["Assistant", "Document"]
    }
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Exclude certain entity types from results
const searchResults = await client.graph.search({
  userId: userId,
  query: "project information",
  scope: "nodes",
  searchFilters: {
    excludeNodeLabels: ["Assistant", "Document"]
  }
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

// Exclude certain entity types from results
searchFilters := zep.SearchFilters{ExcludeNodeLabels: []string{"Assistant", "Document"}}
searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    UserID:        zep.String(userID),
    Query:         "project information",
    Scope:         zep.GraphSearchScopeNodes.Ptr(),
    SearchFilters: &searchFilters,
})
```

#### Excluding Edge Types

Exclude specific edge types from search results. This helps you filter out certain kinds of relationships while keeping all others.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

# Exclude certain edge types from results
search_results = client.graph.search(
    user_id=user_id,
    query="user activities",
    scope="edges",
    search_filters={
        "exclude_edge_types": ["LOCATED_AT", "OCCURRED_AT"]
    }
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Exclude certain edge types from results
const searchResults = await client.graph.search({
  userId: userId,
  query: "user activities",
  scope: "edges",
  searchFilters: {
    excludeEdgeTypes: ["LOCATED_AT", "OCCURRED_AT"]
  }
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

// Exclude certain edge types from results
searchFilters := zep.SearchFilters{ExcludeEdgeTypes: []string{"LOCATED_AT", "OCCURRED_AT"}}
searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    UserID:        zep.String(userID),
    Query:         "user activities",
    Scope:         zep.GraphSearchScopeEdges.Ptr(),
    SearchFilters: &searchFilters,
})
```

Exclusion filters can be combined with inclusion filters (`node_labels` and `edge_types`). When both are specified, results must match the inclusion criteria AND not match any exclusion criteria.

### Node and Episode Filtering

Anchor results to graph structure instead of to text. These four filters restrict results by which nodes an edge connects, or by which episode a fact or entity came from — so you can ask "the facts on this node" or "the entities this episode mentioned" without fetching everything and discarding the rest client-side.

| Filter                 | Applies to      | Semantics                                                                                           |
| ---------------------- | --------------- | --------------------------------------------------------------------------------------------------- |
| `connected_node_uuids` | Edges           | Edge matches if its source **or** target node is in the list.                                       |
| `source_node_uuids`    | Edges           | Edge matches if its source node is in the list.                                                     |
| `target_node_uuids`    | Edges           | Edge matches if its target node is in the list.                                                     |
| `episode_uuids`        | Edges and nodes | Edge matches if it was derived from a listed episode; node matches if a listed episode mentions it. |

UUIDs within one list are combined with OR, and the filters are combined with each other — and with every other filter in the same `search_filters` object — using AND. So `source_node_uuids: [A]` together with `target_node_uuids: [B]` selects the edges directed from A to B. For the undirected set between two nodes, use `connected_node_uuids: [A]` and read each result's `source_node_uuid` and `target_node_uuid`.

Each list accepts at most 256 UUIDs, and every entry must be a valid UUID. A UUID that does not exist in the target graph matches nothing rather than erroring.

The three node-anchoring filters constrain edges only. Supplying one on a request that returns no edges — node listing, or a search `scope` with no edge results — is rejected with a validation error naming the field, rather than silently ignored.

`episode_uuids` applies when results include nodes or edges. On `graph.search`, use it with `scope="nodes"`, `scope="edges"`, or `scope="auto"`; the API rejects it with `episodes`, `observations`, and `thread_summaries`. [Edge listing](/reading-data-from-the-graph#listing-edges-with-filters) accepts all four filters, while [node listing](/reading-data-from-the-graph#listing-nodes) accepts only `episode_uuids`. Observation and thread-summary listing reject all four.

The [neighbors and subgraph endpoints](/reading-data-from-the-graph#navigating-the-graph) also accept all four filters. The example below returns the facts on one node, newest first.

```python Python
from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters

client = Zep(
    api_key=API_KEY,
)

edges = client.graph.edge.get_by_user_id(
    user_id=user_id,
    filters=SearchFilters(connected_node_uuids=[node_uuid]),
    order_by="created_at",
    direction="desc",
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const edges = await client.graph.edge.getByUserId(userId, {
  filters: { connectedNodeUuids: [nodeUuid] },
  orderBy: "created_at",
  direction: "desc",
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

edges, err := client.Graph.Edge.GetByUserID(context.TODO(), userID, &zep.GraphEdgesRequest{
    Filters: &zep.SearchFilters{
        ConnectedNodeUUIDs: []string{nodeUUID},
    },
    OrderBy:   zep.String("created_at"),
    Direction: zep.String("desc"),
})
```

`episode_uuids` also drives the [`episode_mentions` reranker](#episode-mentions), which orders edge results by how many of the listed episodes mention them.

### Property Filtering

Filter search results based on custom attributes stored on nodes and edges. Property filters apply to both node attributes and edge attributes, enabling flexible querying across your graph.

**Supported Comparison Operators:**

| Operator      | Description                            | Requires Value |
| ------------- | -------------------------------------- | -------------- |
| `=`           | Equal to                               | Yes            |
| `<>`          | Not equal to                           | Yes            |
| `>`           | Greater than                           | Yes            |
| `<`           | Less than                              | Yes            |
| `>=`          | Greater than or equal                  | Yes            |
| `<=`          | Less than or equal                     | Yes            |
| `CONTAINS`    | Value is one of a comma-separated list | Yes            |
| `IS NULL`     | Property is null or not set            | No             |
| `IS NOT NULL` | Property exists and is not null        | No             |

```python Python
from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters, PropertyFilter

client = Zep(
    api_key=API_KEY,
)

# Filter by property values
search_results = client.graph.search(
    user_id=user_id,
    query="team members",
    scope="edges",
    search_filters=SearchFilters(
        property_filters=[
            PropertyFilter(
                comparison_operator="=",
                property_name="department",
                property_value="Engineering"
            ),
            PropertyFilter(
                comparison_operator=">",
                property_name="level",
                property_value=3
            )
        ]
    )
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Filter by property values
const searchResults = await client.graph.search({
  userId: userId,
  query: "team members",
  scope: "edges",
  searchFilters: {
    propertyFilters: [
      {
        comparisonOperator: "=",
        propertyName: "department",
        propertyValue: "Engineering"
      },
      {
        comparisonOperator: ">",
        propertyName: "level",
        propertyValue: 3
      }
    ]
  }
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchResults, err := client.Graph.Search(ctx, &zep.GraphSearchQuery{
    UserID: zep.String(userID),
    Query:  "team members",
    Scope:  zep.GraphSearchScopeEdges.Ptr(),
    SearchFilters: &zep.SearchFilters{
        PropertyFilters: []*zep.PropertyFilter{
            {
                ComparisonOperator: zep.ComparisonOperatorEquals,
                PropertyName:       "department",
                PropertyValue:      "Engineering",
            },
            {
                ComparisonOperator: zep.ComparisonOperatorGreaterThan,
                PropertyName:       "level",
                PropertyValue:      3,
            },
        },
    },
})
```

#### Checking for Null Values

The `IS NULL` and `IS NOT NULL` operators allow you to filter based on whether a property exists. When using these operators, omit the `property_value` parameter.

```python Python
from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters, PropertyFilter

client = Zep(
    api_key=API_KEY,
)

# Find edges where a property is NOT set
search_results = client.graph.search(
    user_id=user_id,
    query="incomplete records",
    scope="edges",
    search_filters=SearchFilters(
        property_filters=[
            PropertyFilter(
                comparison_operator="IS NULL",
                property_name="end_date"
                # property_value is omitted for IS NULL
            )
        ]
    )
)

# Find edges where a property IS set
search_results = client.graph.search(
    user_id=user_id,
    query="active employees",
    scope="edges",
    search_filters=SearchFilters(
        property_filters=[
            PropertyFilter(
                comparison_operator="IS NOT NULL",
                property_name="manager_id"
                # property_value is omitted for IS NOT NULL
            )
        ]
    )
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Find edges where a property is NOT set
const incompleteResults = await client.graph.search({
  userId: userId,
  query: "incomplete records",
  scope: "edges",
  searchFilters: {
    propertyFilters: [
      {
        comparisonOperator: "IS NULL",
        propertyName: "endDate"
        // propertyValue is omitted for IS NULL
      }
    ]
  }
});

// Find edges where a property IS set
const activeResults = await client.graph.search({
  userId: userId,
  query: "active employees",
  scope: "edges",
  searchFilters: {
    propertyFilters: [
      {
        comparisonOperator: "IS NOT NULL",
        propertyName: "managerId"
        // propertyValue is omitted for IS NOT NULL
      }
    ]
  }
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

// Find edges where a property is NOT set
incompleteResults, err := client.Graph.Search(ctx, &zep.GraphSearchQuery{
    UserID: zep.String(userID),
    Query:  "incomplete records",
    Scope:  zep.GraphSearchScopeEdges.Ptr(),
    SearchFilters: &zep.SearchFilters{
        PropertyFilters: []*zep.PropertyFilter{
            {
                ComparisonOperator: zep.ComparisonOperatorIsNull,
                PropertyName:       "end_date",
                // PropertyValue is omitted for IS NULL
            },
        },
    },
})

// Find edges where a property IS set
activeResults, err := client.Graph.Search(ctx, &zep.GraphSearchQuery{
    UserID: zep.String(userID),
    Query:  "active employees",
    Scope:  zep.GraphSearchScopeEdges.Ptr(),
    SearchFilters: &zep.SearchFilters{
        PropertyFilters: []*zep.PropertyFilter{
            {
                ComparisonOperator: zep.ComparisonOperatorIsNotNull,
                PropertyName:       "manager_id",
                // PropertyValue is omitted for IS NOT NULL
            },
        },
    },
})
```

For standard comparison operators (`=`, `<>`, `>`, `<`, `>=`, `<=`), the `property_value` parameter is required. For `IS NULL` and `IS NOT NULL` operators, the `property_value` should be omitted since the value is implicitly known (null).

### Datetime Filtering

Filter search results based on timestamps, enabling temporal-based queries to find information from specific time periods. This feature allows you to search for content based on four different timestamp types, each serving a distinct purpose in tracking the lifecycle of facts in your knowledge graph. For supported relative requests with `scope="auto"`, Zep can instead [infer the calendar window from the query](#relative-time-in-auto-search).

#### Edge Scope Only

Datetime filtering only applies to edge scope searches. When using `scope="nodes"` or `scope="episodes"`, datetime filter values are ignored and have no effect on search results.

**Available Timestamp Types:**

| Timestamp    | Description                                          | Example Use Case                                       |
| ------------ | ---------------------------------------------------- | ------------------------------------------------------ |
| `created_at` | The time when Zep learned the fact was true          | Finding when information was first added to the system |
| `valid_at`   | The real world time that the fact started being true | Identifying when a relationship or state began         |
| `invalid_at` | The real world time that the fact stopped being true | Finding when a relationship or state ended             |
| `expired_at` | The time that Zep learned that the fact was false    | Tracking when information was marked as outdated       |

For example, for the fact "Alice is married to Bob":

* `valid_at`: The time they got married
* `invalid_at`: The time they got divorced
* `created_at`: The time Zep learned they were married
* `expired_at`: The time Zep learned they were divorced

**Logic Behavior:**

* **Outer array/list**: Uses OR logic - any condition graph can match
* **Inner array/list**: Uses AND logic - all conditions within a graph must match

In the example below, results are returned if they match:

* (created >= 2025-07-01 AND created \< 2025-08-01) OR (created \< 2025-05-01)

**Date Format**: All dates must be in ISO 8601 format with timezone (e.g., "2025-07-01T20:57:56Z")

**Comparison Operators:**

| Operator      | Description          | Requires Date |
| ------------- | -------------------- | ------------- |
| `=`           | Equal to             | Yes           |
| `<>`          | Not equal to         | Yes           |
| `>`           | After                | Yes           |
| `<`           | Before               | Yes           |
| `>=`          | On or after          | Yes           |
| `<=`          | On or before         | Yes           |
| `IS NULL`     | Timestamp is not set | No            |
| `IS NOT NULL` | Timestamp is set     | No            |

```python Python
from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters, DateFilter

client = Zep(
    api_key=API_KEY,
)

# Search for edges created in July 2025 OR before May 2025
search_results = client.graph.search(
    user_id=user_id,
    query="project discussions",
    scope="edges",
    search_filters=SearchFilters(
        created_at=[
            # First condition graph (AND logic within)
            [DateFilter(comparison_operator=">=", date="2025-07-01T20:57:56Z"), 
             DateFilter(comparison_operator="<", date="2025-08-01T20:57:56Z")],
            # Second condition graph (OR logic with first graph)
            [DateFilter(comparison_operator="<", date="2025-05-01T20:57:56Z")],
        ]
    )
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Search for edges created in July 2025 OR before May 2025
const searchResults = await client.graph.search({
  userId: userId,
  query: "project discussions",
  scope: "edges",
  searchFilters: {
    createdAt: [
      // First condition graph (AND logic within)
      [
        {comparisonOperator: ">=", date: "2025-07-01T20:57:56Z"},
        {comparisonOperator: "<", date: "2025-08-01T20:57:56Z"}
      ],
      // Second condition graph (OR logic with first graph)
      [
        {comparisonOperator: "<", date: "2025-05-01T20:57:56Z"}
      ]
    ]
  }
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

// Search for edges created in July 2025 OR before May 2025
searchResults, err := client.Graph.Search(ctx, &zep.GraphSearchQuery{
    UserID: zep.String(userID),
    Query:  "project discussions",
    Scope:  zep.GraphSearchScopeEdges.Ptr(),
    SearchFilters: &zep.SearchFilters{
        CreatedAt: [][]*zep.DateFilter{
            // First condition graph (AND logic within)
            {
                {
                    ComparisonOperator: zep.ComparisonOperatorGreaterThanEqual,
                    Date:              "2025-07-01T20:57:56Z",
                },
                {
                    ComparisonOperator: zep.ComparisonOperatorLessThan,
                    Date:              "2025-08-01T20:57:56Z",
                },
            },
            // Second condition graph (OR logic with first graph)
            {
                {
                    ComparisonOperator: zep.ComparisonOperatorLessThan,
                    Date:              "2025-05-01T20:57:56Z",
                },
            },
        },
    },
})
```

#### Checking for Null Timestamps

The `IS NULL` and `IS NOT NULL` operators allow you to filter edges based on whether a timestamp field is set. When using these operators, omit the `date` parameter.

```python Python
from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters, DateFilter

client = Zep(
    api_key=API_KEY,
)

# Find edges that have never been invalidated (invalid_at is not set)
search_results = client.graph.search(
    user_id=user_id,
    query="current facts",
    scope="edges",
    search_filters=SearchFilters(
        invalid_at=[
            [DateFilter(comparison_operator="IS NULL")]
            # date is omitted for IS NULL
        ]
    )
)

# Find edges that have an expiration date set
search_results = client.graph.search(
    user_id=user_id,
    query="temporary facts",
    scope="edges",
    search_filters=SearchFilters(
        expired_at=[
            [DateFilter(comparison_operator="IS NOT NULL")]
            # date is omitted for IS NOT NULL
        ]
    )
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Find edges that have never been invalidated (invalid_at is not set)
const currentFacts = await client.graph.search({
  userId: userId,
  query: "current facts",
  scope: "edges",
  searchFilters: {
    invalidAt: [
      [{ comparisonOperator: "IS NULL" }]
      // date is omitted for IS NULL
    ]
  }
});

// Find edges that have an expiration date set
const temporaryFacts = await client.graph.search({
  userId: userId,
  query: "temporary facts",
  scope: "edges",
  searchFilters: {
    expiredAt: [
      [{ comparisonOperator: "IS NOT NULL" }]
      // date is omitted for IS NOT NULL
    ]
  }
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

// Find edges that have never been invalidated (invalid_at is not set)
currentFacts, err := client.Graph.Search(ctx, &zep.GraphSearchQuery{
    UserID: zep.String(userID),
    Query:  "current facts",
    Scope:  zep.GraphSearchScopeEdges.Ptr(),
    SearchFilters: &zep.SearchFilters{
        InvalidAt: [][]*zep.DateFilter{
            {
                {ComparisonOperator: zep.ComparisonOperatorIsNull},
                // Date is omitted for IS NULL
            },
        },
    },
})

// Find edges that have an expiration date set
temporaryFacts, err := client.Graph.Search(ctx, &zep.GraphSearchQuery{
    UserID: zep.String(userID),
    Query:  "temporary facts",
    Scope:  zep.GraphSearchScopeEdges.Ptr(),
    SearchFilters: &zep.SearchFilters{
        ExpiredAt: [][]*zep.DateFilter{
            {
                {ComparisonOperator: zep.ComparisonOperatorIsNotNull},
                // Date is omitted for IS NOT NULL
            },
        },
    },
})
```

For standard comparison operators (`=`, `<>`, `>`, `<`, `>=`, `<=`), the `date` parameter is required. For `IS NULL` and `IS NOT NULL` operators, the `date` should be omitted since the value is implicitly known (null).

**Common Use Cases:**

* **Date Range Filtering**: Find facts from specific time periods using any timestamp type
* **Recent Activity**: Search for edges created or expired after a certain date using `>=` operator
* **Historical Data**: Find older information using `<` or `<=` operators on any timestamp
* **Validity Period Analysis**: Use `valid_at` and `invalid_at` together to find facts that were true during specific periods
* **Audit Trail**: Use `created_at` and `expired_at` to track when your system learned about changes
* **Complex Temporal Queries**: Combine multiple date conditions across different timestamp types
* **Find current/valid facts**: Filter for edges where `invalid_at` IS NULL to find facts that are still valid
* **Find temporary facts**: Filter for edges where `expired_at` IS NOT NULL to find facts with expiration dates
* **Find facts without validity periods**: Filter for edges where `valid_at` IS NULL to find facts without explicit start dates

### Episode Metadata Filtering

Filter search results based on [metadata attached to episodes](/adding-business-data#episode-metadata). Because that metadata is [projected onto every artifact derived from the episode](/episode-metadata-projection), the filter restricts results to edges, nodes, or episodes whose associated episodes' metadata matches the given predicates. For edge and node scopes, a result matches if at least one of its associated episodes satisfies the filter.

Episode metadata filters use explicit AND/OR groups via the `episode_metadata_filters` field in `search_filters`. A filter group contains a `type` (`"and"` or `"or"`), a `filters` array of leaf predicates (each with `property_name`, `comparison_operator`, and optionally `property_value`), and an optional `groups` array for nested sub-expressions.

**Supported comparison operators:**

| Operator      | Description                            | Requires Value |
| ------------- | -------------------------------------- | -------------- |
| `=`           | Equal to                               | Yes            |
| `<>`          | Not equal to                           | Yes            |
| `>`           | Greater than                           | Yes            |
| `<`           | Less than                              | Yes            |
| `>=`          | Greater than or equal                  | Yes            |
| `<=`          | Less than or equal                     | Yes            |
| `CONTAINS`    | Value is one of a comma-separated list | Yes            |
| `IS NULL`     | Key is null or absent                  | No             |
| `IS NOT NULL` | Key exists and is not null             | No             |

**Metadata value types:** filter values must be scalars — string, number (int/float), or boolean. You cannot pass a nested object or an array as a filter value. Metadata keys whose stored value is an [array of scalars](/adding-business-data#episode-metadata) are matched element-wise: `=` matches when any element equals the value, and `CONTAINS` matches when any element appears in the comma-separated list.

Numeric operators (`>`, `<`, `>=`, `<=`) compare values lexicographically because episode metadata is stored as strings — for example, `"9" > "10"` evaluates to `true`. Zero-pad numeric values (such as `"009"`) if you need correct numeric ordering.

#### Simple filter

```python Python
from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters, MetadataFilterGroup, EpisodeMetadataFilter

client = Zep(
    api_key=API_KEY,
)

search_results = client.graph.search(
    user_id=user_id,
    query="lab results",
    scope="edges",
    search_filters=SearchFilters(
        episode_metadata_filters=MetadataFilterGroup(
            type="and",
            filters=[
                EpisodeMetadataFilter(
                    property_name="source",
                    property_value="lab_report",
                    comparison_operator="=",
                )
            ],
        )
    ),
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const searchResults = await client.graph.search({
  userId: userId,
  query: "lab results",
  scope: "edges",
  searchFilters: {
    episodeMetadataFilters: {
      type: "and",
      filters: [
        {
          propertyName: "source",
          propertyValue: "lab_report",
          comparisonOperator: "=",
        },
      ],
    },
  },
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchResults, err := client.Graph.Search(ctx, &zep.GraphSearchQuery{
    UserID: zep.String(userID),
    Query:  "lab results",
    Scope:  zep.GraphSearchScopeEdges.Ptr(),
    SearchFilters: &zep.SearchFilters{
        EpisodeMetadataFilters: &zep.MetadataFilterGroup{
            Type: "and",
            Filters: []*zep.EpisodeMetadataFilter{
                {
                    PropertyName:       "source",
                    PropertyValue:      "lab_report",
                    ComparisonOperator: zep.ComparisonOperatorEquals,
                },
            },
        },
    },
})
```

#### Nested filter groups

Groups can be nested to express complex logic. A `MetadataFilterGroup` contains `filters` for leaf predicates and `groups` for nested sub-expressions. The example below finds results from episodes where `source` is `"lab_report"` AND the `department` is either `"endocrinology"` or `"general"`.

```python Python
from zep_cloud.client import Zep
from zep_cloud.types import SearchFilters, MetadataFilterGroup, EpisodeMetadataFilter

client = Zep(
    api_key=API_KEY,
)

search_results = client.graph.search(
    user_id=user_id,
    query="lab results",
    scope="edges",
    search_filters=SearchFilters(
        episode_metadata_filters=MetadataFilterGroup(
            type="and",
            filters=[
                EpisodeMetadataFilter(
                    property_name="source",
                    property_value="lab_report",
                    comparison_operator="=",
                ),
            ],
            groups=[
                MetadataFilterGroup(
                    type="or",
                    filters=[
                        EpisodeMetadataFilter(
                            property_name="department",
                            property_value="endocrinology",
                            comparison_operator="=",
                        ),
                        EpisodeMetadataFilter(
                            property_name="department",
                            property_value="general",
                            comparison_operator="=",
                        ),
                    ],
                ),
            ],
        )
    ),
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const searchResults = await client.graph.search({
  userId: userId,
  query: "lab results",
  scope: "edges",
  searchFilters: {
    episodeMetadataFilters: {
      type: "and",
      filters: [
        {
          propertyName: "source",
          propertyValue: "lab_report",
          comparisonOperator: "=",
        },
      ],
      groups: [
        {
          type: "or",
          filters: [
            {
              propertyName: "department",
              propertyValue: "endocrinology",
              comparisonOperator: "=",
            },
            {
              propertyName: "department",
              propertyValue: "general",
              comparisonOperator: "=",
            },
          ],
        },
      ],
    },
  },
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchResults, err := client.Graph.Search(ctx, &zep.GraphSearchQuery{
    UserID: zep.String(userID),
    Query:  "lab results",
    Scope:  zep.GraphSearchScopeEdges.Ptr(),
    SearchFilters: &zep.SearchFilters{
        EpisodeMetadataFilters: &zep.MetadataFilterGroup{
            Type: "and",
            Filters: []*zep.EpisodeMetadataFilter{
                {
                    PropertyName:       "source",
                    PropertyValue:      "lab_report",
                    ComparisonOperator: zep.ComparisonOperatorEquals,
                },
            },
            Groups: []*zep.MetadataFilterGroup{
                {
                    Type: "or",
                    Filters: []*zep.EpisodeMetadataFilter{
                        {
                            PropertyName:       "department",
                            PropertyValue:      "endocrinology",
                            ComparisonOperator: zep.ComparisonOperatorEquals,
                        },
                        {
                            PropertyName:       "department",
                            PropertyValue:      "general",
                            ComparisonOperator: zep.ComparisonOperatorEquals,
                        },
                    },
                },
            },
        },
    },
})
```

Episode metadata filters can be combined with other search filters such as `node_labels`, `edge_types`, `property_filters`, and datetime filters. When multiple filter types are specified, results must satisfy all of them.

## Breadth-First Search (BFS)

The `bfs_origin_node_uuids` parameter enables breadth-first searches starting from specified nodes, which helps make search results more relevant to recent context. This is particularly useful when combined with recent episode IDs to bias search results toward information connected to recent conversations. You can pass episode IDs as BFS node IDs because episodes are represented as nodes under the hood.

**When to use**: Use BFS when you want to find information that's contextually connected to specific starting points in your graph, such as recent episodes or important entities.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

# Get recent episodes to use as BFS origin points
episodes = client.graph.episode.get_by_user_id(
    user_id=user_id,
    lastn=10
).episodes

episode_uuids = [episode.uuid_ for episode in episodes if episode.role == 'user']

# Search with BFS starting from recent episodes
search_results = client.graph.search(
    user_id=user_id,
    query="project updates",
    scope="edges",
    bfs_origin_node_uuids=episode_uuids,
    limit=10
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Get recent episodes to use as BFS origin points
const episodeResponse = await client.graph.episode.getByUserId(userId, { lastn: 10 });
const episodeUuids = (episodeResponse.episodes || [])
    .filter((episode) => episode.role === "user")
    .map((episode) => episode.uuid);

// Search with BFS starting from recent episodes
const searchResults = await client.graph.search({
  userId: userId,
  query: "project updates",
  scope: "edges",
  bfsOriginNodeUuids: episodeUuids,
  limit: 10,
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

// Get recent episodes to use as BFS origin points
response, err := client.Graph.Episode.GetByUserID(
    ctx,
    userID,
    &zep.EpisodeGetByUserIDRequest{
        Lastn: zep.Int(10),
    },
)

var episodeUUIDs []string
for _, episode := range response.Episodes {
    if episode.Role != nil && *episode.Role == zep.RoleTypeUserRole {
        episodeUUIDs = append(episodeUUIDs, episode.UUID)
    }
}

// Search with BFS starting from recent episodes
searchResults, err := client.Graph.Search(ctx, &zep.GraphSearchQuery{
    UserID:             zep.String(userID),
    Query:              "project updates",
    Scope:              zep.GraphSearchScopeEdges.Ptr(),
    BfsOriginNodeUUIDs: episodeUUIDs,
    Limit:              zep.Int(10),
})
```