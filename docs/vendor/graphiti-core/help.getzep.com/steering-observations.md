> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Steering observations

#### Experimental API

Observation steering is an experimental feature. The API may change in future releases.

Available to [Flex Plus and Enterprise](https://www.getzep.com/pricing) customers — the same plans that generate [observations](/observations).

## Why use observation steering

Zep automatically derives [observations](/observations) — durable, evidence-backed patterns — from a graph. By default, Zep decides how each observation is worded and labels every observation with the type `generic`. Observation steering lets you guide that generation without changing the underlying evidence.

Steering influences generation along two axes. You can set either one on its own, or both together:

* **Instruction**: free-text guidance that shapes the wording and relevance of the observations Zep writes. The instruction cannot introduce facts, choose which evidence is used, or change how many observations are produced — evidence always comes from the graph.
* **Observation types**: up to ten named categories Zep may assign to an observation. Zep writes the selected type to the `observation_type` property on the observation node, which you can [filter on](/searching-the-graph) during graph search. When none of your types fit, Zep falls back to `generic`.

Steering applies only to observations generated *after* you configure it. It does not enqueue a job or regenerate existing observations; those update on their own as new evidence arrives.

### Use cases

* **Domain vocabulary**: Label observations with categories that match your product — `commitment`, `risk`, `preference`, `churn_signal` — then filter graph search by type.
* **Relevance tuning**: Steer wording and emphasis toward what your agent needs, without post-processing.
* **Per-user or per-graph control**: Apply distinct guidance to a specific user or graph while keeping a project-wide default.

## Basic usage

Use `set_observation_steering` to replace the configuration at a scope, and `get_observation_steering` to read it back. Both return the resulting configuration. With no `user_id` or `graph_id`, the request addresses the project-wide default.

```python Python
from zep_cloud import Zep, ObservationType

client = Zep(
    api_key=API_KEY,
)

# Set the project-wide default.
config = client.project.set_observation_steering(
    instruction="Prioritize durable commitments and stated preferences. Keep observations concise.",
    types=[
        ObservationType(name="commitment", description="A promise or planned action the user has committed to."),
        ObservationType(name="preference", description="A durable preference the user has expressed."),
    ],
)

# Read the current project configuration.
config = client.project.get_observation_steering()
print(config.instruction)
for t in config.types or []:
    print(f"{t.name}: {t.description}")
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Set the project-wide default.
let config = await client.project.setObservationSteering({
  body: {
    instruction: "Prioritize durable commitments and stated preferences. Keep observations concise.",
    types: [
      { name: "commitment", description: "A promise or planned action the user has committed to." },
      { name: "preference", description: "A durable preference the user has expressed." },
    ],
  },
});

// Read the current project configuration.
config = await client.project.getObservationSteering();
console.log(config.instruction);
for (const t of config.types ?? []) {
  console.log(`${t.name}: ${t.description}`);
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

// Set the project-wide default.
config, err := client.Project.SetObservationSteering(context.TODO(), &v3.SetObservationSteeringRequest{
    Body: &v3.ObservationSteeringConfig{
        Instruction: v3.String("Prioritize durable commitments and stated preferences. Keep observations concise."),
        Types: []*v3.ObservationType{
            {Name: "commitment", Description: "A promise or planned action the user has committed to."},
            {Name: "preference", Description: "A durable preference the user has expressed."},
        },
    },
})

// Read the current project configuration.
config, err = client.Project.GetObservationSteering(context.TODO(), &v3.GetObservationSteeringRequest{})
fmt.Println(*config.Instruction)
for _, t := range config.Types {
    fmt.Printf("%s: %s\n", t.Name, t.Description)
}
```

## Scoping to a user or graph

Steering resolves at one of three scopes:

* **Project**: the default configuration, applied to every user and graph in the project. Addressed when you pass neither `user_id` nor `graph_id`.
* **User**: configuration for a single user's graph, addressed with `user_id`.
* **Graph**: configuration for a single named graph, addressed with `graph_id`.

Pass at most one of `user_id` or `graph_id`; passing both returns a `400`. A user or graph configuration is a **complete override**, not a merge — when a user or graph has its own configuration, Zep uses it in full and ignores the project default. Reading a user or graph scope returns its effective configuration, falling back to the project default when that scope has none of its own.

```python Python
# Steer observations for one user only.
client.project.set_observation_steering(
    user_id="alice",
    instruction="Focus on support escalations and unresolved issues.",
    types=[
        ObservationType(name="escalation", description="An issue the user escalated to support."),
    ],
)

# Read the effective configuration for the user (falls back to the project default).
config = client.project.get_observation_steering(user_id="alice")
```

```typescript TypeScript
// Steer observations for one user only.
await client.project.setObservationSteering({
  userId: "alice",
  body: {
    instruction: "Focus on support escalations and unresolved issues.",
    types: [
      { name: "escalation", description: "An issue the user escalated to support." },
    ],
  },
});

// Read the effective configuration for the user (falls back to the project default).
const config = await client.project.getObservationSteering({ userId: "alice" });
```

```go Go
// Steer observations for one user only.
_, err := client.Project.SetObservationSteering(context.TODO(), &v3.SetObservationSteeringRequest{
    UserID: v3.String("alice"),
    Body: &v3.ObservationSteeringConfig{
        Instruction: v3.String("Focus on support escalations and unresolved issues."),
        Types: []*v3.ObservationType{
            {Name: "escalation", Description: "An issue the user escalated to support."},
        },
    },
})

// Read the effective configuration for the user (falls back to the project default).
config, err := client.Project.GetObservationSteering(context.TODO(), &v3.GetObservationSteeringRequest{
    UserID: v3.String("alice"),
})
```

## Observation types

Each entry in `types` defines a category Zep may assign to an observation. When Zep generates an observation, it selects the best-fitting type and writes the name to the `observation_type` property on the observation node. If none of your configured types fit — or you configure no types — Zep uses `generic`.

| Field         | Rules                                                                                                                                                                                                        |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `name`        | Must match `^[a-z][a-z0-9_]*$` (start with a lowercase letter, then lowercase letters, digits, or underscores); up to 50 characters; unique within the list. `generic` is reserved and cannot be configured. |
| `description` | Required; 1–500 characters. Describes when the type applies, which guides Zep's selection.                                                                                                                   |

You can configure up to 10 types.

Because the selected type is stored as the `observation_type` node property, you can retrieve observations of a given type with [graph search](/searching-the-graph) property filters:

```python Python
from zep_cloud.types import SearchFilters, PropertyFilter

results = client.graph.search(
    user_id="alice",
    query="commitments to the customer",
    scope="observations",
    search_filters=SearchFilters(
        property_filters=[
            PropertyFilter(
                comparison_operator="=",
                property_name="observation_type",
                property_value="commitment",
            )
        ]
    ),
)

for obs in results.observations or []:
    print(obs.name, obs.score)
```

```typescript TypeScript
const results = await client.graph.search({
  userId: "alice",
  query: "commitments to the customer",
  scope: "observations",
  searchFilters: {
    propertyFilters: [
      {
        comparisonOperator: "=",
        propertyName: "observation_type",
        propertyValue: "commitment",
      },
    ],
  },
});

for (const obs of results.observations ?? []) {
  console.log(obs.name, obs.score);
}
```

```go Go
results, err := client.Graph.Search(context.TODO(), &v3.GraphSearchQuery{
    UserID: v3.String("alice"),
    Query:  "commitments to the customer",
    Scope:  v3.GraphSearchScopeObservations.Ptr(),
    SearchFilters: &v3.SearchFilters{
        PropertyFilters: []*v3.PropertyFilter{
            {
                ComparisonOperator: v3.ComparisonOperatorEquals,
                PropertyName:       "observation_type",
                PropertyValue:      "commitment",
            },
        },
    },
})

for _, obs := range results.Observations {
    fmt.Println(obs.Name, obs.Score)
}
```

## Clearing steering

Sending an empty configuration — a null instruction and an empty `types` list — clears steering at the addressed scope. Clearing the project scope removes the default; clearing a user or graph scope removes the override, so that scope falls back to the project default.

```python Python
# Remove the override for a user (falls back to the project default).
client.project.set_observation_steering(
    user_id="alice",
    instruction=None,
    types=[],
)
```

```typescript TypeScript
// Remove the override for a user (falls back to the project default).
await client.project.setObservationSteering({
  userId: "alice",
  body: { types: [] },
});
```

```go Go
// Remove the override for a user (falls back to the project default).
_, err := client.Project.SetObservationSteering(context.TODO(), &v3.SetObservationSteeringRequest{
    UserID: v3.String("alice"),
    Body:   &v3.ObservationSteeringConfig{Types: []*v3.ObservationType{}},
})
```

## Configurable parameters

| Parameter     | Type           | Description                                                                                                                         | Default | Required |
| ------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ------- | -------- |
| `user_id`     | string         | Address the steering configuration for a single user's graph.                                                                       | -       | No\*     |
| `graph_id`    | string         | Address the steering configuration for a named graph.                                                                               | -       | No\*     |
| `instruction` | string \| null | Guidance for the wording and relevance of generated observations. Up to 2000 characters. An empty or omitted instruction clears it. | `null`  | No       |
| `types`       | array          | Up to 10 custom observation types, each with a `name` and `description`.                                                            | `[]`    | No       |

\*Pass at most one of `user_id` or `graph_id`. Passing both returns a `400`; passing neither addresses the project scope. In the TypeScript and Go SDKs, `instruction` and `types` are set on the request's `body` field.

### Observation type fields

| Field         | Type   | Description                                                                                                        |
| ------------- | ------ | ------------------------------------------------------------------------------------------------------------------ |
| `name`        | string | Category name. Must match `^[a-z][a-z0-9_]*$`, up to 50 characters, unique within the list. `generic` is reserved. |
| `description` | string | When the type applies. Required, 1–500 characters.                                                                 |

## Response

Both `get_observation_steering` and `set_observation_steering` return the resulting configuration at the addressed scope:

| Field         | Type           | Description                                                             |
| ------------- | -------------- | ----------------------------------------------------------------------- |
| `instruction` | string \| null | The configured instruction, or `null` if none is set.                   |
| `types`       | array          | The configured observation types, each with a `name` and `description`. |

Reading a user or graph scope returns its effective configuration, falling back to the project default when that scope has no configuration of its own.

## Related

* [Observations](/observations) — the durable, evidence-backed context that steering shapes.
* [Searching the Graph](/searching-the-graph) — filter observations by the `observation_type` property.
* [Shape the Graph](/customizing-context) — the other ways to tailor how Zep builds the Context Graph.