> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Searching the Graph

#### Custom Context Strings

Graph search results should be used in conjunction with [Custom Context Strings](/cookbook/customize-your-memory-context-string) to create rich, contextual prompts for AI models. Custom context strings allow you to format and structure the retrieved graph information, combining search results with conversation history and other relevant data to provide comprehensive context for your AI applications.

Learn how to integrate graph search results into your context generation workflow for more effective AI interactions.

## Introduction

Zep's graph search provides powerful hybrid search capabilities that combine semantic similarity with BM25 full-text search to find relevant information across your knowledge graph. This approach leverages the best of both worlds: semantic understanding for conceptual matches and full-text search for exact term matching. Additionally, you can optionally enable breadth-first search to bias results toward information connected to specific starting points in your graph.

### How It Works

* **Semantic similarity**: Converts queries into embeddings to find conceptually similar content
* **BM25 full-text search**: Performs traditional keyword-based search for exact matches
* **Breadth-first search** (optional): Biases results toward information connected to specified starting nodes, useful for contextual relevance
* **Hybrid results**: Combines and reranks results using sophisticated algorithms

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
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
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

Keep queries short: they are truncated at 256 characters. Long queries may increase latency without improving search quality.
Break down complex searches into smaller, targeted queries. Use precise, contextual queries rather than generic ones

## Configurable Parameters

Zep provides extensive configuration options to fine-tune search behavior and optimize results for your specific use case:

| Parameter               | Type    | Description                                                                                                                              | Default   | Required |
| ----------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------- | --------- | -------- |
| `user_id`               | string  | Search within a specific user's graph                                                                                                    | -         | Yes\*    |
| `group_id`              | string  | Search within a group graph                                                                                                              | -         | Yes\*    |
| `query`                 | string  | Search text (max 256 characters)                                                                                                         | -         | Yes      |
| `scope`                 | string  | Search target: `"edges"`, `"nodes"`, or `"episodes"`                                                                                     | `"edges"` | No       |
| `reranker`              | string  | Reranking method: `"rrf"`, `"mmr"`, `"node_distance"`, `"episode_mentions"`, or `"cross_encoder"`                                        | `"rrf"`   | No       |
| `limit`                 | integer | Maximum number of results to return                                                                                                      | `10`      | No       |
| `mmr_lambda`            | float   | MMR diversity vs relevance balance (0.0-1.0)                                                                                             | -         | No†      |
| `center_node_uuid`      | string  | Center node for distance-based reranking                                                                                                 | -         | No‡      |
| `search_filters`        | object  | Filter by entity types (`node_labels`), edge types (`edge_types`), or timestamps (`created_at`, `expired_at`, `invalid_at`, `valid_at`§) | -         | No       |
| `bfs_origin_node_uuids` | array   | Node UUIDs to seed breadth-first searches from                                                                                           | -         | No       |
| `min_fact_rating`       | double  | The minimum rating by which to filter relevant facts (range: 0.0-1.0). Can only be used when using `scope="edges"`                       | -         | No       |

\*Either `user_id` OR `group_id` is required\
†Required when using `mmr` reranker\
‡Required when using `node_distance` reranker\
§Timestamp filtering only applies to edge scope searches

## Search Scopes

Zep supports three different search scopes, each optimized for different types of information retrieval:

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
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
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
    group_id=group_id,
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
  groupId: groupId,
  query: "John Smith",
  scope: "nodes",
});
```

```go Go
import (
    "context"
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(API_KEY),
)

searchResults, err := client.Graph.Search(context.TODO(), &zep.GraphSearchQuery{
    GroupID: zep.String(groupID),
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
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
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

## Rerankers

Zep provides multiple reranking algorithms to optimize search results for different use cases. Each reranker applies a different strategy to prioritize and order results:

### RRF (Reciprocal Rank Fusion)

<a name="reciprocal-rank-fusion" />

Reciprocal Rank Fusion is the default reranker that intelligently combines results from both semantic similarity and BM25 full-text search. It merges the two result sets by considering the rank position of each result in both searches, creating a unified ranking that leverages the strengths of both approaches.

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
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
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
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
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
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
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
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
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
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
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
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
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

### Datetime Filtering

Filter search results based on timestamps, enabling temporal-based queries to find information from specific time periods. This feature allows you to search for content based on four different timestamp types, each serving a distinct purpose in tracking the lifecycle of facts in your knowledge graph.

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

* **Outer array/list**: Uses OR logic - any condition group can match
* **Inner array/list**: Uses AND logic - all conditions within a group must match

In the example below, results are returned if they match:

* (created >= 2025-07-01 AND created \< 2025-08-01) OR (created \< 2025-05-01)

**Date Format**: All dates must be in ISO 8601 format with timezone (e.g., "2025-07-01T20:57:56Z")

**Comparison Operators**: Supports `>=`, `<=`, `<`, and `>` for flexible date range queries

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
            # First condition group (AND logic within)
            [DateFilter(comparison_operator=">=", date="2025-07-01T20:57:56Z"), 
             DateFilter(comparison_operator="<", date="2025-08-01T20:57:56Z")],
            # Second condition group (OR logic with first group)
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
      // First condition group (AND logic within)
      [
        {comparisonOperator: ">=", date: "2025-07-01T20:57:56Z"},
        {comparisonOperator: "<", date: "2025-08-01T20:57:56Z"}
      ],
      // Second condition group (OR logic with first group)
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
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
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
            // First condition group (AND logic within)
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
            // Second condition group (OR logic with first group)
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

**Common Use Cases:**

* **Date Range Filtering**: Find facts from specific time periods using any timestamp type
* **Recent Activity**: Search for edges created or expired after a certain date using `>=` operator
* **Historical Data**: Find older information using `<` or `<=` operators on any timestamp
* **Validity Period Analysis**: Use `valid_at` and `invalid_at` together to find facts that were true during specific periods
* **Audit Trail**: Use `created_at` and `expired_at` to track when your system learned about changes
* **Complex Temporal Queries**: Combine multiple date conditions across different timestamp types

## Filtering by Fact Rating

The `min_fact_rating` parameter allows you to filter search results based on the relevancy rating of facts stored in your graph edges. When specified, all facts returned will have at least the minimum rating value you set.

This parameter accepts values between 0.0 and 1.0, where higher values indicate more relevant facts. By setting a minimum threshold, you can ensure that only highly relevant facts are included in your search results.

**Important**: The `min_fact_rating` parameter can only be used when searching with `scope="edges"` as fact ratings are associated with edge relationships.

Read more about [fact ratings and how they work](/facts#rating-facts-for-relevancy).

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

episode_uuids = [episode.uuid_ for episode in episodes if episode.role_type == 'user']

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
    .filter((episode) => episode.roleType === "user")
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
    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
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
    if episode.RoleType != nil && *episode.RoleType == zep.RoleTypeUserRole {
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