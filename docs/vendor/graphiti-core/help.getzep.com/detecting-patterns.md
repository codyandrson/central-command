> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Detecting Patterns

#### Experimental API

Pattern detection is an experimental feature. The API may change in future releases.

## Introduction

Zep's pattern detection analyzes the structure of your knowledge graph to discover recurring patterns: frequent relationship types, multi-hop paths, co-occurring entities, highly connected hubs, and tightly interconnected clusters. Unlike graph search, which retrieves content matching a query, pattern detection reveals the *shape* of your data — surfacing structural insights that aren't visible from individual nodes or edges.

Pattern detection supports two modes:

* **Seed mode**: Provide explicit seed nodes, labels, or edge types to analyze patterns around specific parts of the graph
* **Query mode**: Provide a natural-language query to automatically discover relevant nodes and return relationship patterns with relevance-scored edges

### What It Finds

| Pattern Type      | What It Detects                                          | Example                                                     |
| ----------------- | -------------------------------------------------------- | ----------------------------------------------------------- |
| **Relationship**  | Common `(source_label, edge_type, target_label)` triples | `Person -[WORKS_AT]-> Company` appears 47 times             |
| **Path**          | Frequent multi-hop connection chains                     | `Person -> Project -> Technology` is a recurring 2-hop path |
| **Co-occurrence** | Node types that appear together within k hops            | `Decision` and `Stakeholder` nodes consistently co-occur    |
| **Hub**           | Highly connected nodes (star topology)                   | A `Project` node with 12 `ASSIGNED_TO` edges                |
| **Cluster**       | Tightly interconnected groups (triangle topology)        | Three `Person` nodes all connected to each other            |

Query mode only detects **relationship** patterns. Use seed mode for path, co-occurrence, hub, and cluster detection.

### Use Cases

* **Knowledge graph auditing**: Understand what types of information your graph captures most frequently
* **Schema discovery**: Identify dominant relationship patterns to inform ontology design
* **Anomaly context**: Establish baselines of normal graph structure to help detect anomalies
* **Data quality**: Find unexpected patterns that may indicate ingestion issues
* **Agent-driven Q\&A**: Use query mode to find relevant graph context for answering questions (e.g., "clothing purchases" returns scored edge facts about purchase patterns)

## Basic Usage

### Seed mode

Provide `seeds` to focus analysis around specific nodes, node labels, or edge types. At least one seed field (`node_uuids`, `node_labels`, or `edge_types`) is required.

```python Python
from zep_cloud import Zep

client = Zep(
    api_key=API_KEY,
)

result = client.graph.detect_patterns(
    user_id="alice",
    seeds={
        "node_labels": ["Decision"],
    },
)

for pattern in result.patterns:
    print(f"{pattern.type}: {pattern.description} ({pattern.occurrences}x)")
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const result = await client.graph.detectPatterns({
  userId: "alice",
  seeds: {
    nodeLabels: ["Decision"],
  },
});

for (const pattern of result.patterns) {
  console.log(`${pattern.type}: ${pattern.description} (${pattern.occurrences}x)`);
}
```

```go Go
import (
    "context"
    "fmt"
    v3 "github.com/getzep/zep-go/v3"
    v3client "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := v3client.NewClient(
    option.WithAPIKey(API_KEY),
)

result, err := client.Graph.DetectPatterns(context.TODO(), &v3.DetectPatternsRequest{
    UserID: v3.String("alice"),
    Seeds: &v3.PatternSeeds{
        NodeLabels: []string{"Decision"},
    },
})

for _, pattern := range result.Patterns {
    fmt.Printf("%s: %s (%dx)\n", pattern.Type, pattern.Description, pattern.Occurrences)
}
```

### Query mode

Provide a `query` string to let Zep automatically discover relevant nodes and detect relationship patterns. The response includes relevance-scored edges per pattern and a deduplicated nodes array.

```python Python
result = client.graph.detect_patterns(
    user_id="alice",
    query="clothing purchases",
)

for pattern in result.patterns:
    print(f"{pattern.description} ({pattern.occurrences}x)")
    for edge in pattern.edges:
        print(f"  {edge.fact} (score: {edge.score:.2f})")

# Deduplicated nodes referenced by pattern edges
for node in result.nodes:
    print(f"  Node: {node.name} ({node.labels})")
```

```typescript TypeScript
const result = await client.graph.detectPatterns({
  userId: "alice",
  query: "clothing purchases",
});

for (const pattern of result.patterns) {
  console.log(`${pattern.description} (${pattern.occurrences}x)`);
  for (const edge of pattern.edges) {
    console.log(`  ${edge.fact} (score: ${edge.score.toFixed(2)})`);
  }
}

// Deduplicated nodes referenced by pattern edges
for (const node of result.nodes) {
  console.log(`  Node: ${node.name} (${node.labels})`);
}
```

```go Go
result, err := client.Graph.DetectPatterns(context.TODO(), &v3.DetectPatternsRequest{
    UserID: v3.String("alice"),
    Query:  v3.String("clothing purchases"),
})

for _, p := range result.Patterns {
    fmt.Printf("%s (%dx)\n", p.Description, p.Occurrences)
    for _, e := range p.Edges {
        fmt.Printf("  %s (score: %.2f)\n", e.Fact, e.Score)
    }
}

// Deduplicated nodes referenced by pattern edges
for _, n := range result.Nodes {
    fmt.Printf("  Node: %s (%v)\n", n.Name, n.Labels)
}
```

## Configurable Parameters

| Parameter         | Type    | Description                                                                                                            | Default   | Required |
| ----------------- | ------- | ---------------------------------------------------------------------------------------------------------------------- | --------- | -------- |
| `user_id`         | string  | Detect patterns in a user's graph                                                                                      | -         | Yes\*    |
| `graph_id`        | string  | Detect patterns in a named graph                                                                                       | -         | Yes\*    |
| `query`           | string  | Natural-language search query for discovering relevant nodes automatically. Only detects relationship patterns.        | -         | Yes\*\*  |
| `query_limit`     | integer | Maximum nodes to discover from the query (1-50). Only used with `query`.                                               | `10`      | No       |
| `edge_limit`      | integer | Maximum resolved edges per pattern (1-100). Only used with `query`.                                                    | `10`      | No       |
| `seeds`           | object  | Seed nodes to focus analysis around (at least one of `node_uuids`, `node_labels`, or `edge_types` must be provided).   | -         | Yes\*\*  |
| `detect`          | object  | Which pattern types to detect and their configuration. Ignored when `query` is set.                                    | all types | No       |
| `limit`           | integer | Maximum patterns to return (1-200)                                                                                     | `50`      | No       |
| `min_occurrences` | integer | Minimum occurrence count to report a pattern                                                                           | `2`       | No       |
| `recency_weight`  | string  | Temporal decay half-life: `"none"`, `"7_days"`, `"30_days"`, `"90_days"`                                               | `"none"`  | No       |
| `search_filters`  | object  | Filter which edges/nodes participate (same format as graph search). Also applied to seed node discovery in query mode. | -         | No       |

\*Either `user_id` or `graph_id` is required

Either `query` or `seeds` is required, but not both

## Selecting Pattern Types

Use the `detect` parameter to choose which pattern types to find. Each key enables that type; its value provides type-specific configuration. Omit `detect` entirely to run all types with defaults.

```python Python
result = client.graph.detect_patterns(
    user_id="alice",
    seeds={
        "node_labels": ["Person"],
    },
    detect={
        "relationships": {},
        "paths": {"max_hops": 4},
        "hubs": {"min_degree": 5},
    },
)
```

```typescript TypeScript
const result = await client.graph.detectPatterns({
  userId: "alice",
  seeds: {
    nodeLabels: ["Person"],
  },
  detect: {
    relationships: {},
    paths: { maxHops: 4 },
    hubs: { minDegree: 5 },
  },
});
```

```go Go
maxHops := 4
minDegree := 5

result, err := client.Graph.DetectPatterns(context.TODO(), &v3.DetectPatternsRequest{
    UserID: v3.String("alice"),
    Seeds: &v3.PatternSeeds{
        NodeLabels: []string{"Person"},
    },
    Detect: &v3.DetectConfig{
        Relationships: &v3.RelationshipDetectConfig{},
        Paths:         &v3.PathDetectConfig{MaxHops: &maxHops},
        Hubs:          &v3.HubDetectConfig{MinDegree: &minDegree},
    },
})
```

### Type-Specific Configuration

| Pattern Type     | Config Field | Type          | Description                             | Default |
| ---------------- | ------------ | ------------- | --------------------------------------- | ------- |
| `paths`          | `max_hops`   | integer (1-5) | Maximum path length to search           | 3       |
| `co_occurrences` | `max_hops`   | integer (1-5) | Proximity window for co-occurrence      | 3       |
| `hubs`           | `min_degree` | integer (2+)  | Minimum connections to qualify as a hub | 3       |
| `relationships`  | *(none)*     | -             | -                                       | -       |
| `clusters`       | *(none)*     | -             | -                                       | -       |

## Seed Nodes

Use `seeds` to specify the starting points for pattern detection in seed mode. At least one seed field is required. When multiple seed fields are provided, seeds are combined (union). Seeds cannot be used together with `query`.

```python Python
# Focus on patterns around Decision nodes
result = client.graph.detect_patterns(
    user_id="alice",
    seeds={
        "node_labels": ["Decision"],
    },
    detect={
        "relationships": {},
        "paths": {"max_hops": 3},
    },
)
```

```typescript TypeScript
const result = await client.graph.detectPatterns({
  userId: "alice",
  seeds: {
    nodeLabels: ["Decision"],
  },
  detect: {
    relationships: {},
    paths: { maxHops: 3 },
  },
});
```

```go Go
result, err := client.Graph.DetectPatterns(context.TODO(), &v3.DetectPatternsRequest{
    UserID: v3.String("alice"),
    Seeds: &v3.PatternSeeds{
        NodeLabels: []string{"Decision"},
    },
    Detect: &v3.DetectConfig{
        Relationships: &v3.RelationshipDetectConfig{},
        Paths:         &v3.PathDetectConfig{MaxHops: v3.Int(3)},
    },
})
```

### Seed Options

| Field         | Type             | Description                                                                    |
| ------------- | ---------------- | ------------------------------------------------------------------------------ |
| `node_uuids`  | array of strings | Specific node UUIDs to analyze around                                          |
| `node_labels` | array of strings | All nodes with these labels become seeds (e.g., `["Decision", "Person"]`)      |
| `edge_types`  | array of strings | All endpoints of these edge types become seeds (e.g., `["CHOSE", "REJECTED"]`) |

## Recency Weighting

Apply temporal decay to favor recently created edges. The `recency_weight` value sets the exponential decay half-life applied to each edge's `created_at` timestamp.

| Value       | Effect                                     |
| ----------- | ------------------------------------------ |
| `"none"`    | All edges weighted equally (default)       |
| `"7_days"`  | Edges lose half their weight every 7 days  |
| `"30_days"` | Edges lose half their weight every 30 days |
| `"90_days"` | Edges lose half their weight every 90 days |

When recency weighting is enabled, the `weighted_score` in each result reflects the decayed sum, while `occurrences` always reports the raw unweighted count.

```python Python
result = client.graph.detect_patterns(
    user_id="alice",
    seeds={
        "node_labels": ["Decision"],
    },
    recency_weight="30_days",
)

for pattern in result.patterns:
    print(f"{pattern.description}: {pattern.occurrences} occurrences, weighted score {pattern.weighted_score:.1f}")
```

```typescript TypeScript
const result = await client.graph.detectPatterns({
  userId: "alice",
  seeds: {
    nodeLabels: ["Decision"],
  },
  recencyWeight: "30_days",
});

for (const pattern of result.patterns) {
  console.log(
    `${pattern.description}: ${pattern.occurrences} occurrences, weighted score ${pattern.weightedScore.toFixed(1)}`
  );
}
```

```go Go
result, err := client.Graph.DetectPatterns(context.TODO(), &v3.DetectPatternsRequest{
    UserID:        v3.String("alice"),
    Seeds: &v3.PatternSeeds{
        NodeLabels: []string{"Decision"},
    },
    RecencyWeight: v3.String("30_days"),
})

for _, p := range result.Patterns {
    fmt.Printf("%s: %d occurrences, weighted score %.1f\n", p.Description, p.Occurrences, p.WeightedScore)
}
```

## Search Filters

Use `search_filters` to restrict which nodes and edges participate in pattern detection. This uses the same filter format as [graph search](/searching-the-graph).

```python Python
result = client.graph.detect_patterns(
    user_id="alice",
    seeds={
        "node_labels": ["Decision"],
    },
    search_filters={
        "node_labels": ["Decision", "Person"],
        "edge_types": ["CHOSE", "REJECTED"],
        "created_at": [[{">": "2025-01-01T00:00:00Z"}]],
    },
)
```

```typescript TypeScript
const result = await client.graph.detectPatterns({
  userId: "alice",
  seeds: {
    nodeLabels: ["Decision"],
  },
  searchFilters: {
    nodeLabels: ["Decision", "Person"],
    edgeTypes: ["CHOSE", "REJECTED"],
    createdAt: [[{ ">": "2025-01-01T00:00:00Z" }]],
  },
});
```

```go Go
result, err := client.Graph.DetectPatterns(context.TODO(), &v3.DetectPatternsRequest{
    UserID: v3.String("alice"),
    Seeds: &v3.PatternSeeds{
        NodeLabels: []string{"Decision"},
    },
    SearchFilters: &v3.SearchFilters{
        NodeLabels: []string{"Decision", "Person"},
        EdgeTypes:  []string{"CHOSE", "REJECTED"},
    },
})
```

## Response Structure

Each pattern in the response contains:

| Field            | Type    | Description                                                                               |
| ---------------- | ------- | ----------------------------------------------------------------------------------------- |
| `type`           | string  | Pattern type: `"relationship"`, `"path"`, `"co_occurrence"`, `"hub"`, or `"cluster"`      |
| `description`    | string  | Human-readable description (e.g., `"Person -[WORKS_AT]-> Company"`)                       |
| `occurrences`    | integer | Raw count of this pattern in the graph (always unweighted)                                |
| `weighted_score` | float   | Weighted sum after recency decay (equals `occurrences` when `recency_weight` is `"none"`) |
| `node_labels`    | array   | Node labels involved in the pattern structure                                             |
| `edge_types`     | array   | Edge types involved in the pattern structure                                              |
| `edges`          | array   | Resolved edges sorted by relevance score (only populated in query mode)                   |

The top-level response also includes:

| Field      | Type   | Description                                                                   |
| ---------- | ------ | ----------------------------------------------------------------------------- |
| `nodes`    | array  | Deduplicated nodes referenced by pattern edges (only populated in query mode) |
| `metadata` | object | Statistics about the detection run                                            |

The `metadata` object contains:

| Field            | Type    | Description                                 |
| ---------------- | ------- | ------------------------------------------- |
| `nodes_analyzed` | integer | Number of unique nodes analyzed             |
| `edges_analyzed` | integer | Number of edges analyzed                    |
| `elapsed_ms`     | integer | Server-side processing time in milliseconds |

Patterns are sorted by `weighted_score` in descending order.