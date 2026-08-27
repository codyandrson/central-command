> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Customize Your Context String

When using [`graph.search`](/concepts#using-graphsearch) instead of [`memory.get`](/concepts#using-memoryget), you need to use the search results to create a custom context string. In this recipe, we will demonstrate how to build a custom memory context string using the [graph search API](/searching-the-graph). We will also use the [custom entity and edge types feature](/customizing-graph-structure#custom-entity-and-edge-types), though using this feature is optional.

# Add data

First, we define our [custom entity and edge types](/customizing-graph-structure#definition-1), create a user, and add some example data:

```python
import uuid
from zep_cloud import Message
from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel, EntityBoolean
from zep_cloud import EntityEdgeSourceTarget
from pydantic import Field

class Restaurant(EntityModel):
    """
    Represents a specific restaurant.
    """
    cuisine_type: EntityText = Field(description="The cuisine type of the restaurant, for example: American, Mexican, Indian, etc.", default=None)
    dietary_accommodation: EntityText = Field(description="The dietary accommodation of the restaurant, if any, for example: vegetarian, vegan, etc.", default=None)

class RestaurantVisit(EdgeModel):
    """
    Represents the fact that the user visited a restaurant.
    """
    restaurant_name: EntityText = Field(description="The name of the restaurant the user visited", default=None)

class DietaryPreference(EdgeModel):
    """
    Represents the fact that the user has a dietary preference or dietary restriction.
    """
    preference_type: EntityText = Field(description="Preference type of the user: anything, vegetarian, vegan, peanut allergy, etc.", default=None)
    allergy: EntityBoolean = Field(description="Whether this dietary preference represents a user allergy: True or false", default=None)

client.graph.set_ontology(
    entities={
        "Restaurant": Restaurant,
    },
    edges={
        "RESTAURANT_VISIT": (
            RestaurantVisit,
            [EntityEdgeSourceTarget(source="User", target="Restaurant")]
        ),
        "DIETARY_PREFERENCE": (
            DietaryPreference,
            [EntityEdgeSourceTarget(source="User")]
        ),
    }
)

messages_session1 = [
    Message(content="Take me to a lunch place", role_type="user", role="John Doe"),
    Message(content="How about Panera Bread, Chipotle, or Green Leaf Cafe, which are nearby?", role_type="assistant", role="Assistant"),
    Message(content="Do any of those have vegetarian options? I’m vegetarian", role_type="user", role="John Doe"),
    Message(content="Yes, Green Leaf Cafe has vegetarian options", role_type="assistant", role="Assistant"),
    Message(content="Let’s go to Green Leaf Cafe", role_type="user", role="John Doe"),
    Message(content="Navigating to Green Leaf Cafe", role_type="assistant", role="Assistant"),
]

messages_session2 = [
    Message(content="Take me to dessert", role_type="user", role="John Doe"),
    Message(content="How about getting some ice cream?", role_type="assistant", role="Assistant"),
    Message(content="I can't have ice cream, I'm lactose intolerant, but I'm craving a chocolate chip cookie", role_type="user", role="John Doe"),
    Message(content="Sure, there's Insomnia Cookies nearby.", role_type="assistant", role="Assistant"),
    Message(content="Perfect, let's go to Insomnia Cookies", role_type="user", role="John Doe"),
    Message(content="Navigating to Insomnia Cookies.", role_type="assistant", role="Assistant"),
]

user_id = f"user-{uuid.uuid4()}"
client.user.add(user_id=user_id, first_name="John", last_name="Doe", email="john.doe@example.com")

session1_id = f"session-{uuid.uuid4()}"
session2_id = f"session-{uuid.uuid4()}"
client.memory.add_session(session_id=session1_id, user_id=user_id)
client.memory.add_session(session_id=session2_id, user_id=user_id)

client.memory.add(session_id=session1_id, messages=messages_session1, ignore_roles=["assistant"])
client.memory.add(session_id=session2_id, messages=messages_session2, ignore_roles=["assistant"])
```

```typescript
import { entityFields, EntityType, EdgeType } from "@getzep/zep-cloud/wrapper/ontology";
import { v4 as uuidv4 } from "uuid";
import type { Message } from "@getzep/zep-cloud/api";

const RestaurantSchema: EntityType = {
    description: "Represents a specific restaurant.",
    fields: {
        cuisine_type: entityFields.text("The cuisine type of the restaurant, for example: American, Mexican, Indian, etc."),
        dietary_accommodation: entityFields.text("The dietary accommodation of the restaurant, if any, for example: vegetarian, vegan, etc."),
    },
};

const RestaurantVisit: EdgeType = {
    description: "Represents the fact that the user visited a restaurant.",
    fields: {
        restaurant_name: entityFields.text("The name of the restaurant the user visited"),
    },
    sourceTargets: [
        { source: "User", target: "Restaurant" },
    ],
};

const DietaryPreference: EdgeType = {
    description: "Represents the fact that the user has a dietary preference or dietary restriction.",
    fields: {
        preference_type: entityFields.text("Preference type of the user: anything, vegetarian, vegan, peanut allergy, etc."),
        allergy: entityFields.boolean("Whether this dietary preference represents a user allergy: True or false"),
    },
    sourceTargets: [
        { source: "User" },
    ],
};

await client.graph.setOntology(
    {
        Restaurant: RestaurantSchema,
    },
    {
        RESTAURANT_VISIT: RestaurantVisit,
        DIETARY_PREFERENCE: DietaryPreference,
    }
);

const messagesSession1: Message[] = [
    { content: "Take me to a lunch place", roleType: "user", role: "John Doe" },
    { content: "How about Panera Bread, Chipotle, or Green Leaf Cafe, which are nearby?", roleType: "assistant", role: "Assistant" },
    { content: "Do any of those have vegetarian options? I’m vegetarian", roleType: "user", role: "John Doe" },
    { content: "Yes, Green Leaf Cafe has vegetarian options", roleType: "assistant", role: "Assistant" },
    { content: "Let’s go to Green Leaf Cafe", roleType: "user", role: "John Doe" },
    { content: "Navigating to Green Leaf Cafe", roleType: "assistant", role: "Assistant" },
];

const messagesSession2: Message[] = [
    { content: "Take me to dessert", roleType: "user", role: "John Doe" },
    { content: "How about getting some ice cream?", roleType: "assistant", role: "Assistant" },
    { content: "I can't have ice cream, I'm lactose intolerant, but I'm craving a chocolate chip cookie", roleType: "user", role: "John Doe" },
    { content: "Sure, there's Insomnia Cookies nearby.", roleType: "assistant", role: "Assistant" },
    { content: "Perfect, let's go to Insomnia Cookies", roleType: "user", role: "John Doe" },
    { content: "Navigating to Insomnia Cookies.", roleType: "assistant", role: "Assistant" },
];

let userId = `user-${uuidv4()}`;
await client.user.add({ userId, firstName: "John", lastName: "Doe", email: "john.doe@example.com" });

const session1Id = `session-${uuidv4()}`;
const session2Id = `session-${uuidv4()}`;
await client.memory.addSession({ sessionId: session1Id, userId });
await client.memory.addSession({ sessionId: session2Id, userId });

await client.memory.add(session1Id, { messages: messagesSession1, ignoreRoles: ["assistant"] });
await client.memory.add(session2Id, { messages: messagesSession2, ignoreRoles: ["assistant"] });
```

```go
import (
	"github.com/getzep/zep-go/v2"
	"github.com/google/uuid"
)

type Restaurant struct {
	zep.BaseEntity `name:"Restaurant" description:"Represents a specific restaurant."`
	CuisineType           string `description:"The cuisine type of the restaurant, for example: American, Mexican, Indian, etc." json:"cuisine_type,omitempty"`
	DietaryAccommodation  string `description:"The dietary accommodation of the restaurant, if any, for example: vegetarian, vegan, etc." json:"dietary_accommodation,omitempty"`
}

type RestaurantVisit struct {
	zep.BaseEdge `name:"RESTAURANT_VISIT" description:"Represents the fact that the user visited a restaurant."`
	RestaurantName string `description:"The name of the restaurant the user visited" json:"restaurant_name,omitempty"`
}

type DietaryPreference struct {
	zep.BaseEdge `name:"DIETARY_PREFERENCE" description:"Represents the fact that the user has a dietary preference or dietary restriction."`
	PreferenceType string `description:"Preference type of the user: anything, vegetarian, vegan, peanut allergy, etc." json:"preference_type,omitempty"`
	Allergy        bool   `description:"Whether this dietary preference represents a user allergy: True or false" json:"allergy,omitempty"`
}

_, err = client.Graph.SetOntology(
	ctx,
	[]zep.EntityDefinition{
		Restaurant{},
	},
	[]zep.EdgeDefinitionWithSourceTargets{
		{
			EdgeModel: RestaurantVisit{},
			SourceTargets: []zep.EntityEdgeSourceTarget{
				{
					Source: zep.String("User"),
					Target: zep.String("Restaurant"),
				},
			},
		},
		{
			EdgeModel: DietaryPreference{},
			SourceTargets: []zep.EntityEdgeSourceTarget{
				{
					Source: zep.String("User"),
				},
			},
		},
	},
)
if err != nil {
	fmt.Printf("Error setting ontology: %v\n", err)
	return
}

messagesSession1 := []zep.Message{
	{Content: "Take me to a lunch place", RoleType: "user", Role: zep.String("John Doe")},
	{Content: "How about Panera Bread, Chipotle, or Green Leaf Cafe, which are nearby?", RoleType: "assistant", Role: zep.String("Assistant")},
	{Content: "Do any of those have vegetarian options? I'm vegetarian", RoleType: "user", Role: zep.String("John Doe")},
	{Content: "Yes, Green Leaf Cafe has vegetarian options", RoleType: "assistant", Role: zep.String("Assistant")},
	{Content: "Let's go to Green Leaf Cafe", RoleType: "user", Role: zep.String("John Doe")},
	{Content: "Navigating to Green Leaf Cafe", RoleType: "assistant", Role: zep.String("Assistant")},
}
messagesSession2 := []zep.Message{
	{Content: "Take me to dessert", RoleType: "user", Role: zep.String("John Doe")},
	{Content: "How about getting some ice cream?", RoleType: "assistant", Role: zep.String("Assistant")},
	{Content: "I can't have ice cream, I'm lactose intolerant, but I'm craving a chocolate chip cookie", RoleType: "user", Role: zep.String("John Doe")},
	{Content: "Sure, there's Insomnia Cookies nearby.", RoleType: "assistant", Role: zep.String("Assistant")},
	{Content: "Perfect, let's go to Insomnia Cookies", RoleType: "user", Role: zep.String("John Doe")},
	{Content: "Navigating to Insomnia Cookies.", RoleType: "assistant", Role: zep.String("Assistant")},
}
userID := "user-" + uuid.NewString()
userReq := &zep.CreateUserRequest{
	UserID:    userID,
	FirstName: zep.String("John"),
	LastName:  zep.String("Doe"),
	Email:     zep.String("john.doe@example.com"),
}
_, err = client.User.Add(ctx, userReq)
if err != nil {
	fmt.Printf("Error creating user: %v\n", err)
	return
}

session1ID := "session-" + uuid.NewString()
session2ID := "session-" + uuid.NewString()

session1Req := &zep.CreateSessionRequest{
	SessionID: session1ID,
	UserID:    userID,
}
session2Req := &zep.CreateSessionRequest{
	SessionID: session2ID,
	UserID:    userID,
}
_, err = client.Memory.AddSession(ctx, session1Req)
if err != nil {
	fmt.Printf("Error creating session 1: %v\n", err)
	return
}
_, err = client.Memory.AddSession(ctx, session2Req)
if err != nil {
	fmt.Printf("Error creating session 2: %v\n", err)
	return
}

msgPtrs1 := make([]*zep.Message, len(messagesSession1))
for i := range messagesSession1 {
	msgPtrs1[i] = &messagesSession1[i]
}
addReq1 := &zep.AddMemoryRequest{
	Messages: msgPtrs1,
	IgnoreRoles: []zep.RoleType{
		zep.RoleTypeAssistantRole,
	},
}
_, err = client.Memory.Add(ctx, session1ID, addReq1)
if err != nil {
	fmt.Printf("Error adding messages to session 1: %v\n", err)
	return
}

msgPtrs2 := make([]*zep.Message, len(messagesSession2))
for i := range messagesSession2 {
	msgPtrs2[i] = &messagesSession2[i]
}
addReq2 := &zep.AddMemoryRequest{
	Messages: msgPtrs2,
	IgnoreRoles: []zep.RoleType{
		zep.RoleTypeAssistantRole,
	},
}
_, err = client.Memory.Add(ctx, session2ID, addReq2)
if err != nil {
	fmt.Printf("Error adding messages to session 2: %v\n", err)
	return
}
```

# Example 1: Basic custom context string

## Search

For a basic custom context string, we search the graph for edges and nodes relevant to our custom query string, which typically represents a user message. Note that the default [memory context string](/concepts#memory-context) returned by `memory.get` uses the past few messages as the query instead.

Additionally, note that below we retrieve the past several [episodes](/graphiti/graphiti/adding-episodes) and use those episode IDs as the BFS node IDs. We use BFS here to make the search results more relevant to the user's recent history. You can read more about how BFS works in the [Breadth-First Search section](/graph/searching-the-graph#breadth-first-search-bfs) of our searching the graph documentation.

These searches can be performed in parallel to reduce latency, using our [async Python client](/quickstart#initialize-the-client), TypeScript promises, or goroutines.

```python
query = "Find some food around here"

episodes = client.graph.episode.get_by_user_id(
    user_id=user_id,
    lastn=10
).episodes

episode_uuids = [episode.uuid_ for episode in episodes if episode.role_type == 'user']

search_results_nodes = client.graph.search(
    query=query,
    user_id=user_id,
    scope='nodes',
    reranker='cross_encoder',
    limit=10,
    bfs_origin_node_uuids=episode_uuids
)
search_results_edges = client.graph.search(
    query=query,
    user_id=user_id,
    scope='edges',
    reranker='cross_encoder',
    limit=10,
    bfs_origin_node_uuids=episode_uuids
)
```

```typescript
let query = "Find some food around here";

let episodeResponse = await client.graph.episode.getByUserId(userId, { lastn: 10 });
let episodeUuids = (episodeResponse.episodes || [])
    .filter((episode) => episode.roleType === "user")
    .map((episode) => episode.uuid);

const searchResultsNodes = await client.graph.search({
    userId: userId,
    query: query,
    scope: "nodes",
    reranker: "cross_encoder",
    limit: 10,
    bfsOriginNodeUuids: episodeUuids,
});

const searchResultsEdges = await client.graph.search({
    userId: userId,
    query: query,
    scope: "edges",
    reranker: "cross_encoder",
    limit: 10,
    bfsOriginNodeUuids: episodeUuids,
});
```

```go
import (
	"github.com/getzep/zep-go/v2/graph"
)

query := "Find some food around here"

response, err := client.Graph.Episode.GetByUserID(
	ctx,
	userID,
	&graph.EpisodeGetByUserIDRequest{
		Lastn: zep.Int(10),
	},
)
if err != nil {
	fmt.Printf("Error getting episodes: %v\n", err)
	return
}

var episodeUUIDs1 []string
for _, episode := range response.Episodes {
	if episode.RoleType != nil && *episode.RoleType == zep.RoleTypeUserRole {
		episodeUUIDs1 = append(episodeUUIDs1, episode.UUID)
	}
}

searchResultsNodes, err := client.Graph.Search(
	ctx,
	&zep.GraphSearchQuery{
		UserID:  zep.String(userID),
		Query:   query,
		Scope:   zep.GraphSearchScopeNodes.Ptr(),
		Reranker: zep.RerankerCrossEncoder.Ptr(),
		Limit:   zep.Int(10),
		BfsOriginNodeUUIDs: episodeUUIDs1,
	},
)
if err != nil {
	fmt.Printf("Error searching graph (nodes): %v\n", err)
	return
}

searchResultsEdges, err := client.Graph.Search(
	ctx,
	&zep.GraphSearchQuery{
		UserID:  zep.String(userID),
		Query:   query,
		Scope:   zep.GraphSearchScopeEdges.Ptr(),
		Reranker: zep.RerankerCrossEncoder.Ptr(),
		Limit:   zep.Int(10),
		BfsOriginNodeUUIDs: episodeUUIDs1,
	},
)
if err != nil {
	fmt.Printf("Error searching graph (edges): %v\n", err)
	return
}
```

## Build the context string

Using the search results and a few helper functions, we can build the context string. Note that for nodes, we typically want to unpack the node name and node summary, and for edges we typically want to unpack the fact and the temporal validity information:

```python
from zep_cloud import EntityEdge, EntityNode

CONTEXT_STRING_TEMPLATE = """
FACTS and ENTITIES represent relevant context to the current conversation.
# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
<FACTS>
{facts}
</FACTS>

# These are the most relevant entities
# ENTITY_NAME: entity summary
<ENTITIES>
{entities}
</ENTITIES>
"""


def format_fact(edge: EntityEdge) -> str:
    valid_at = edge.valid_at if edge.valid_at is not None else "date unknown"
    invalid_at = edge.invalid_at if edge.invalid_at is not None else "present"
    formatted_fact = f"  - {edge.fact} (Date range: {valid_at} - {invalid_at})"
    return formatted_fact

def format_entity(node: EntityNode) -> str:
    formatted_entity = f"  - {node.name}: {node.summary}"
    return formatted_entity

def compose_context_string(edges: list[EntityEdge], nodes: list[EntityNode]) -> str:
    facts = [format_fact(edge) for edge in edges]
    entities = [format_entity(node) for node in nodes]
    return CONTEXT_STRING_TEMPLATE.format(facts='\n'.join(facts), entities='\n'.join(entities))

edges = search_results_edges.edges
nodes = search_results_nodes.nodes

context_string = compose_context_string(edges, nodes)
print(context_string)
```

```typescript
import type { EntityEdge, EntityNode } from "@getzep/zep-cloud/api";

const CONTEXT_STRING_TEMPLATE_1 = `FACTS and ENTITIES represent relevant context to the current conversation.
# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
<FACTS>
{facts}
</FACTS>
# These are the most relevant entities
# ENTITY_NAME: entity summary
<ENTITIES>
{entities}
</ENTITIES>`;

function formatFact(edge: EntityEdge): string {
    const validAt = edge.validAt ?? "date unknown";
    const invalidAt = edge.invalidAt ?? "present";
    return `  - ${edge.fact} (Date range: ${validAt} - ${invalidAt})`;
}

function formatEntity(node: EntityNode): string {
    return `  - ${node.name}: ${node.summary}`;
}

function composeContextString1(edges: EntityEdge[], nodes: EntityNode[]): string {
    const facts = edges.map(formatFact).join('\n');
    const entities = nodes.map(formatEntity).join('\n');
    return CONTEXT_STRING_TEMPLATE_1
        .replace('{facts}', facts)
        .replace('{entities}', entities);
}

const edges: EntityEdge[] = searchResultsEdges.edges ?? [];
const nodes: EntityNode[] = searchResultsNodes.nodes ?? [];

const contextString1 = composeContextString1(edges, nodes);
console.log(contextString1);
```

```go
import (
	"strings"
)

const CONTEXT_STRING_TEMPLATE_1 = `FACTS and ENTITIES represent relevant context to the current conversation.
# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
<FACTS>
{facts}
</FACTS>

# These are the most relevant entities
# ENTITY_NAME: entity summary
<ENTITIES>
{entities}
</ENTITIES>
`

formatFact := func(edge *zep.EntityEdge) string {
	validAt := "date unknown"
	if edge.ValidAt != nil && *edge.ValidAt != "" {
		validAt = *edge.ValidAt
	}
	invalidAt := "present"
	if edge.InvalidAt != nil && *edge.InvalidAt != "" {
		invalidAt = *edge.InvalidAt
	}
	return fmt.Sprintf("  - %s (Date range: %s - %s)", edge.Fact, validAt, invalidAt)
}

formatEntity := func(node *zep.EntityNode) string {
	return fmt.Sprintf("  - %s: %s", node.Name, node.Summary)
}

composeContextString1 := func(edges []*zep.EntityEdge, nodes []*zep.EntityNode) string {
	var facts []string
	for _, edge := range edges {
		facts = append(facts, formatFact(edge))
	}
	var entities []string
	for _, node := range nodes {
		entities = append(entities, formatEntity(node))
	}
	result := strings.ReplaceAll(CONTEXT_STRING_TEMPLATE_1, "{facts}", strings.Join(facts, "\n"))
	result = strings.ReplaceAll(result, "{entities}", strings.Join(entities, "\n"))
	return result
}

edges := searchResultsEdges.Edges
nodes := searchResultsNodes.Nodes

contextString1 := composeContextString1(edges, nodes)
fmt.Println(contextString1)
```

```text
FACTS and ENTITIES represent relevant context to the current conversation.
# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
<FACTS>
  - User wants to go to dessert (Date range: 2025-06-16T02:17:25Z - present)
  - John Doe wants to go to a lunch place (Date range: 2025-06-16T02:17:25Z - present)
  - John Doe said 'Perfect, let's go to Insomnia Cookies' indicating he will visit Insomnia Cookies. (Date range: 2025-06-16T02:17:25Z - present)
  - John Doe said 'Let’s go to Green Leaf Cafe' indicating intention to visit (Date range: 2025-06-16T02:17:25Z - present)
  - John Doe is craving a chocolate chip cookie (Date range: 2025-06-16T02:17:25Z - present)
  - John Doe states that he is vegetarian. (Date range: 2025-06-16T02:17:25Z - present)
  - John Doe is lactose intolerant (Date range: 2025-06-16T02:17:25Z - present)
</FACTS>
 
# These are the most relevant entities
# ENTITY_NAME: entity summary
<ENTITIES>
  - lunch place: The entity is a lunch place, but no specific details about its cuisine or dietary accommodations are provided.
  - dessert: The entity 'dessert' refers to a preference related to sweet courses typically served at the end of a meal. The context indicates that the user has expressed an interest in going to a dessert place, but no specific dessert or place has been named. The entity is categorized as a Preference and Entity, but no additional attributes are provided or inferred from the messages.
  - Green Leaf Cafe: Green Leaf Cafe is a restaurant that offers vegetarian options, making it suitable for vegetarian diners.
  - user: The user is John Doe, with the email john.doe@example.com. He has shown interest in visiting Green Leaf Cafe, which offers vegetarian options, and has also expressed a preference for lactose-free options, craving a chocolate chip cookie. The user has decided to go to Insomnia Cookies.
  - vegetarian: The user is interested in lunch places such as Panera Bread, Chipotle, and Green Leaf Cafe. They are specifically looking for vegetarian options at these restaurants.
  - chocolate chip cookie: The entity is a chocolate chip cookie, which the user desires as a snack. The user is lactose intolerant and cannot have ice cream, but is craving a chocolate chip cookie.
  - Insomnia Cookies: Insomnia Cookies is a restaurant that offers cookies, including chocolate chip cookies. The user is interested in a dessert and has chosen to go to Insomnia Cookies. No specific cuisine type or dietary accommodations are mentioned in the messages.
  - lactose intolerant: The entity is a preference indicating lactose intolerance, which is a dietary restriction that prevents the individual from consuming lactose, a sugar found in milk and dairy products. The person is specifically craving a chocolate chip cookie but cannot have ice cream due to lactose intolerance.
  - John Doe: The user is John Doe, with user ID user-34c7a6c1-ded6-4797-9620-8b80a5e7820f, email john.doe@example.com, and role type user. He inquired about nearby lunch options and vegetarian choices, and expressed a preference for a chocolate chip cookie due to lactose intolerance.
</ENTITIES>
```

# Example 2: Utilizing custom entity and edge types

## Search

For a custom context string that uses custom entity and edge types, we perform multiple searches (with our custom query string) filtering to the custom entity or edge type we want to include in the context string:

As we do in [Example 1](/cookbook/customize-your-memory-context-string#search), we pass episode IDs as BFS node IDs:

These searches can be performed in parallel to reduce latency, using our [async Python client](/quickstart#initialize-the-client), TypeScript promises, or goroutines.

```python
query = "Find some food around here"

episodes = client.graph.episode.get_by_user_id(
    user_id=user_id,
    lastn=10
).episodes

episode_uuids = [episode.uuid_ for episode in episodes if episode.role_type == 'user']

search_results_restaurant_visits = client.graph.search(
    query=query,
    user_id=user_id,
    scope='edges',
    search_filters={
        "edge_types": ["RESTAURANT_VISIT"]
    },
    reranker='cross_encoder',
    limit=10,
    bfs_origin_node_uuids=episode_uuids
)
search_results_dietary_preferences = client.graph.search(
    query=query,
    user_id=user_id,
    scope='edges',
    search_filters={
        "edge_types": ["DIETARY_PREFERENCE"]
    },
    reranker='cross_encoder',
    limit=10,
    bfs_origin_node_uuids=episode_uuids
)
search_results_restaurants = client.graph.search(
    query=query,
    user_id=user_id,
    scope='nodes',
    search_filters={
        "node_labels": ["Restaurant"]
    },
    reranker='cross_encoder',
    limit=10,
    bfs_origin_node_uuids=episode_uuids
)
```

```typescript
query = "Find some food around here";

episodeResponse = await client.graph.episode.getByUserId(userId, { lastn: 10 });
episodeUuids = (episodeResponse.episodes || [])
    .filter((episode) => episode.roleType === "user")
    .map((episode) => episode.uuid);

const searchResultsRestaurantVisits = await client.graph.search({
    query,
    userId: userId,
    scope: "edges",
    searchFilters: {
        edgeTypes: ["RESTAURANT_VISIT"]
    },
    reranker: "cross_encoder",
    limit: 10,
    bfsOriginNodeUuids: episodeUuids,
});

const searchResultsDietaryPreferences = await client.graph.search({
    query,
    userId: userId,
    scope: "edges",
    searchFilters: {
        edgeTypes: ["DIETARY_PREFERENCE"]
    },
    reranker: "cross_encoder",
    limit: 10,
    bfsOriginNodeUuids: episodeUuids,
});

const searchResultsRestaurants = await client.graph.search({
    query,
    userId: userId,
    scope: "nodes",
    searchFilters: {
        nodeLabels: ["Restaurant"]
    },
    reranker: "cross_encoder",
    limit: 10,
    bfsOriginNodeUuids: episodeUuids,
});
```

```go
query = "Find some food around here"

response, err = client.Graph.Episode.GetByUserID(
	ctx,
	userID,
	&graph.EpisodeGetByUserIDRequest{
		Lastn: zep.Int(10),
	},
)
if err != nil {
	fmt.Printf("Error getting episodes: %v\n", err)
	return
}

var episodeUUIDs2 []string
for _, episode := range response.Episodes {
	if episode.RoleType != nil && *episode.RoleType == zep.RoleTypeUserRole {
		episodeUUIDs2 = append(episodeUUIDs2, episode.UUID)
	}
}

searchFiltersRestaurantVisits := zep.SearchFilters{EdgeTypes: []string{"RESTAURANT_VISIT"}}
searchResultsRestaurantVisits, err := client.Graph.Search(
	ctx,
	&zep.GraphSearchQuery{
		UserID:        zep.String(userID),
		Query:         query,
		Scope:         zep.GraphSearchScopeEdges.Ptr(),
		SearchFilters: &searchFiltersRestaurantVisits,
		Reranker:      zep.RerankerCrossEncoder.Ptr(),
		Limit:         zep.Int(10),
		BfsOriginNodeUUIDs: episodeUUIDs2,
},
)
if err != nil {
	fmt.Printf("Error searching graph (RESTAURANT_VISIT edges): %v\n", err)
	return
}

searchFiltersDietaryPreferences := zep.SearchFilters{EdgeTypes: []string{"DIETARY_PREFERENCE"}}
searchResultsDietaryPreferences, err := client.Graph.Search(
	ctx,
	&zep.GraphSearchQuery{
		UserID:        zep.String(userID),
		Query:         query,
		Scope:         zep.GraphSearchScopeEdges.Ptr(),
		SearchFilters: &searchFiltersDietaryPreferences,
		Reranker:      zep.RerankerCrossEncoder.Ptr(),
		Limit:         zep.Int(10),
		BfsOriginNodeUUIDs: episodeUUIDs2,
	},
)
if err != nil {
	fmt.Printf("Error searching graph (DIETARY_PREFERENCE edges): %v\n", err)
	return
}

searchFiltersRestaurants := zep.SearchFilters{NodeLabels: []string{"Restaurant"}}
searchResultsRestaurants, err := client.Graph.Search(
	ctx,
	&zep.GraphSearchQuery{
		UserID:        zep.String(userID),
		Query:         query,
		Scope:         zep.GraphSearchScopeNodes.Ptr(),
		SearchFilters: &searchFiltersRestaurants,
		Reranker:      zep.RerankerCrossEncoder.Ptr(),
		Limit:         zep.Int(10),
		BfsOriginNodeUUIDs: episodeUUIDs2,
},
)
if err != nil {
	fmt.Printf("Error searching graph (Restaurant nodes): %v\n", err)
	return
}
```

## Build the context string

Using the search results and a few helper functions, we can compose the context string. Note that in this example, we focus on unpacking the custom attributes of the nodes and edges, but this is a design choice that you can experiment with for your use case.

Note also that we designed the context string template around the custom entity and edge types that we are unpacking into the context string:

```python
from zep_cloud import EntityEdge, EntityNode

CONTEXT_STRING_TEMPLATE = """
PREVIOUS_RESTAURANT_VISITS, DIETARY_PREFERENCES, and RESTAURANTS represent relevant context to the current conversation.
# These are the most relevant restaurants the user has previously visited
# format: restaurant_name: RESTAURANT_NAME
<PREVIOUS_RESTAURANT_VISITS>
{restaurant_visits}
</PREVIOUS_RESTAURANT_VISITS>

# These are the most relevant dietary preferences of the user, whether they represent an allergy, and their valid date ranges
# format: allergy: True/False; preference_type: PREFERENCE_TYPE (Date range: from - to)
<DIETARY_PREFERENCES>
{dietary_preferences}
</DIETARY_PREFERENCES>

# These are the most relevant restaurants the user has discussed previously
# format: name: RESTAURANT_NAME; cuisine_type: CUISINE_TYPE; dietary_accommodation: DIETARY_ACCOMMODATION
<RESTAURANTS>
{restaurants}
</RESTAURANTS>
"""

def format_edge_with_attributes(edge: EntityEdge, include_timestamps: bool = True) -> str:
    attrs_str = '; '.join(f"{k}: {v}" for k, v in sorted(edge.attributes.items()))
    if include_timestamps:
        valid_at = edge.valid_at if edge.valid_at is not None else "date unknown"
        invalid_at = edge.invalid_at if edge.invalid_at is not None else "present"
        return f"  - {attrs_str} (Date range: {valid_at} - {invalid_at})"
    return f"  - {attrs_str}"

def format_node_with_attributes(node: EntityNode) -> str:
    attributes = {k: v for k, v in node.attributes.items() if k != "labels"}
    attrs_str = '; '.join(f"{k}: {v}" for k, v in sorted(attributes.items()))
    base = f"  - name: {node.name}; {attrs_str}"
    return base

def compose_context_string(restaurant_visit_edges: list[EntityEdge], dietary_preference_edges: list[EntityEdge], restaurant_nodes: list[EntityNode]) -> str:
    restaurant_visits = [format_edge_with_attributes(edge, include_timestamps=False) for edge in restaurant_visit_edges]
    dietary_preferences = [format_edge_with_attributes(edge, include_timestamps=True) for edge in dietary_preference_edges]
    restaurant_nodes = [format_node_with_attributes(node) for node in restaurant_nodes]
    return CONTEXT_STRING_TEMPLATE.format(restaurant_visits='\n'.join(restaurant_visits), dietary_preferences='\n'.join(dietary_preferences), restaurants='\n'.join(restaurant_nodes))


restaurant_visit_edges = search_results_restaurant_visits.edges
dietary_preference_edges = search_results_dietary_preferences.edges
restaurant_nodes = search_results_restaurants.nodes

context_string = compose_context_string(restaurant_visit_edges, dietary_preference_edges, restaurant_nodes)
print(context_string)
```

```typescript
import type { EntityEdge, EntityNode } from "@getzep/zep-cloud/api";

const CONTEXT_STRING_TEMPLATE_2 = `PREVIOUS_RESTAURANT_VISITS, DIETARY_PREFERENCES, and RESTAURANTS represent relevant context to the current conversation.
# These are the most relevant restaurants the user has previously visited
# format: restaurant_name: RESTAURANT_NAME
<PREVIOUS_RESTAURANT_VISITS>
{restaurant_visits}
</PREVIOUS_RESTAURANT_VISITS>

# These are the most relevant dietary preferences of the user, whether they represent an allergy, and their valid date ranges
# format: allergy: True/False; preference_type: PREFERENCE_TYPE (Date range: from - to)
<DIETARY_PREFERENCES>
{dietary_preferences}
</DIETARY_PREFERENCES>

# These are the most relevant restaurants the user has discussed previously
# format: name: RESTAURANT_NAME; cuisine_type: CUISINE_TYPE; dietary_accommodation: DIETARY_ACCOMMODATION
<RESTAURANTS>
{restaurants}
</RESTAURANTS>`;

function formatEdgeWithAttributes(edge: EntityEdge, includeTimestamps = true): string {
    const attrs = Object.entries(edge.attributes ?? {})
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => `${k}: ${v}`)
        .join('; ');
    if (includeTimestamps) {
        const validAt = edge.validAt ?? "date unknown";
        const invalidAt = edge.invalidAt ?? "present";
        return `  - ${attrs} (Date range: ${validAt} - ${invalidAt})`;
    }
    return `  - ${attrs}`;
}

function formatNodeWithAttributes(node: EntityNode): string {
    const attributes = Object.entries(node.attributes ?? {})
        .filter(([k]) => k !== "labels")
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => `${k}: ${v}`)
        .join('; ');
    return `  - name: ${node.name}; ${attributes}`;
}

function composeContextString2(
    restaurantVisitEdges: EntityEdge[],
    dietaryPreferenceEdges: EntityEdge[],
    restaurantNodes: EntityNode[]
): string {
    const restaurantVisits = restaurantVisitEdges.map(e => formatEdgeWithAttributes(e, false)).join('\n');
    const dietaryPreferences = dietaryPreferenceEdges.map(e => formatEdgeWithAttributes(e, true)).join('\n');
    const restaurants = restaurantNodes.map(n => formatNodeWithAttributes(n)).join('\n');
    return CONTEXT_STRING_TEMPLATE_2
        .replace('{restaurant_visits}', restaurantVisits)
        .replace('{dietary_preferences}', dietaryPreferences)
        .replace('{restaurants}', restaurants);
}

const restaurantVisitEdges: EntityEdge[] = searchResultsRestaurantVisits.edges ?? [];
const dietaryPreferenceEdges: EntityEdge[] = searchResultsDietaryPreferences.edges ?? [];
const restaurantNodes: EntityNode[] = searchResultsRestaurants.nodes ?? [];

const contextString2 = composeContextString2(restaurantVisitEdges, dietaryPreferenceEdges, restaurantNodes);
console.log(contextString2);
```

```go
import (
	"strings"
)
	
const CONTEXT_STRING_TEMPLATE_2 = `PREVIOUS_RESTAURANT_VISITS, DIETARY_PREFERENCES, and RESTAURANTS represent relevant context to the current conversation.
# These are the most relevant restaurants the user has previously visited
# format: restaurant_name: RESTAURANT_NAME
<PREVIOUS_RESTAURANT_VISITS>
{restaurant_visits}
</PREVIOUS_RESTAURANT_VISITS>

# These are the most relevant dietary preferences of the user, whether they represent an allergy, and their valid date ranges
# format: allergy: True/False; preference_type: PREFERENCE_TYPE (Date range: from - to)
<DIETARY_PREFERENCES>
{dietary_preferences}
</DIETARY_PREFERENCES>

# These are the most relevant restaurants the user has discussed previously
# format: name: RESTAURANT_NAME; cuisine_type: CUISINE_TYPE; dietary_accommodation: DIETARY_ACCOMMODATION
<RESTAURANTS>
{restaurants}
</RESTAURANTS>`

formatEdgeWithAttributes := func(edge *zep.EntityEdge, includeTimestamps bool) string {
	attrs := make([]string, 0)
	for _, k := range []string{"allergy", "preference_type", "restaurant_name"} {
		if v, ok := edge.Attributes[k]; ok {
			attrs = append(attrs, fmt.Sprintf("%s: %v", k, v))
		}
	}
	attrsStr := strings.Join(attrs, "; ")
	if includeTimestamps {
		validAt := "date unknown"
		if edge.ValidAt != nil && *edge.ValidAt != "" {
			validAt = *edge.ValidAt
		}
		invalidAt := "present"
		if edge.InvalidAt != nil && *edge.InvalidAt != "" {
			invalidAt = *edge.InvalidAt
		}
		return fmt.Sprintf("  - %s (Date range: %s - %s)", attrsStr, validAt, invalidAt)
	}
	return fmt.Sprintf("  - %s", attrsStr)
}

formatNodeWithAttributes := func(node *zep.EntityNode) string {
	attrs := make([]string, 0)
	for k, v := range node.Attributes {
		if k == "labels" {
			continue
		}
		attrs = append(attrs, fmt.Sprintf("%s: %v", k, v))
	}
	attrsStr := strings.Join(attrs, "; ")
	return fmt.Sprintf("  - name: %s; %s", node.Name, attrsStr)
}

composeContextString2 := func(restaurantVisitEdges []*zep.EntityEdge, dietaryPreferenceEdges []*zep.EntityEdge, restaurantNodes []*zep.EntityNode) string {
	restaurantVisits := make([]string, 0)
	for _, edge := range restaurantVisitEdges {
		restaurantVisits = append(restaurantVisits, formatEdgeWithAttributes(edge, false))
	}
	dietaryPreferences := make([]string, 0)
	for _, edge := range dietaryPreferenceEdges {
		dietaryPreferences = append(dietaryPreferences, formatEdgeWithAttributes(edge, true))
	}
	restaurants := make([]string, 0)
	for _, node := range restaurantNodes {
		restaurants = append(restaurants, formatNodeWithAttributes(node))
	}
	result := strings.ReplaceAll(CONTEXT_STRING_TEMPLATE_2, "{restaurant_visits}", strings.Join(restaurantVisits, "\n"))
	result = strings.ReplaceAll(result, "{dietary_preferences}", strings.Join(dietaryPreferences, "\n"))
	result = strings.ReplaceAll(result, "{restaurants}", strings.Join(restaurants, "\n"))
	return result
}

restaurantVisitEdges := searchResultsRestaurantVisits.Edges
dietaryPreferenceEdges := searchResultsDietaryPreferences.Edges
restaurantNodes := searchResultsRestaurants.Nodes

contextString2 := composeContextString2(restaurantVisitEdges, dietaryPreferenceEdges, restaurantNodes)
fmt.Println(contextString2)
```

```text
PREVIOUS_RESTAURANT_VISITS, DIETARY_PREFERENCES, and RESTAURANTS represent relevant context to the current conversation.
# These are the most relevant restaurants the user has previously visited
# format: restaurant_name: RESTAURANT_NAME
<PREVIOUS_RESTAURANT_VISITS>
  - restaurant_name: Insomnia Cookies
  - restaurant_name: Green Leaf Cafe
</PREVIOUS_RESTAURANT_VISITS>
 
# These are the most relevant dietary preferences of the user, whether they represent an allergy, and their valid date ranges
# format: allergy: True/False; preference_type: PREFERENCE_TYPE (Date range: from - to)
<DIETARY_PREFERENCES>
  - allergy: False; preference_type: vegetarian (Date range: 2025-06-16T02:17:25Z - present)
  - allergy: False; preference_type: lactose intolerance (Date range: 2025-06-16T02:17:25Z - present)
</DIETARY_PREFERENCES>
 
# These are the most relevant restaurants the user has discussed previously
# format: name: RESTAURANT_NAME; cuisine_type: CUISINE_TYPE; dietary_accommodation: DIETARY_ACCOMMODATION
<RESTAURANTS>
  - name: Green Leaf Cafe; dietary_accommodation: vegetarian
  - name: Insomnia Cookies; 
</RESTAURANTS>
```