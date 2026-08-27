> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Advanced Context Block construction

This guide covers building context blocks from scratch using graph search for maximum customization. See [Choosing a retrieval method](/retrieving-context#choosing-a-retrieval-method) for a comparison of all three context retrieval approaches.

When [searching the graph](/searching-the-graph) instead of [using Zep's Context Block](/retrieving-context#zeps-context-block), you need to use the search results to create a custom context block. In this recipe, we will demonstrate how to build a custom context block using the [graph search API](/searching-the-graph). We will also use the [custom entity and edge types feature](/customizing-graph-structure#custom-entity-and-edge-types), though using this feature is optional.

**Include fact validity.** When you build a custom context block, include each fact's `valid_at` and `invalid_at` dates, and clearly mark any fact with a non-null `invalid_at` as no longer valid. This lets the agent tell current facts from outdated ones instead of treating every fact as true now. The examples below format validity as a date range: a range ending in `present` is currently valid, and a past end date means the fact is no longer valid.

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

messages_thread1 = [
    Message(content="Take me to a lunch place", role="user", name="John Doe"),
    Message(content="How about Panera Bread, Chipotle, or Green Leaf Cafe, which are nearby?", role="assistant", name="Assistant"),
    Message(content="Do any of those have vegetarian options? I’m vegetarian", role="user", name="John Doe"),
    Message(content="Yes, Green Leaf Cafe has vegetarian options", role="assistant", name="Assistant"),
    Message(content="Let’s go to Green Leaf Cafe", role="user", name="John Doe"),
    Message(content="Navigating to Green Leaf Cafe", role="assistant", name="Assistant"),
]

messages_thread2 = [
    Message(content="Take me to dessert", role="user", name="John Doe"),
    Message(content="How about getting some ice cream?", role="assistant", name="Assistant"),
    Message(content="I can't have ice cream, I'm lactose intolerant, but I'm craving a chocolate chip cookie", role="user", name="John Doe"),
    Message(content="Sure, there's Insomnia Cookies nearby.", role="assistant", name="Assistant"),
    Message(content="Perfect, let's go to Insomnia Cookies", role="user", name="John Doe"),
    Message(content="Navigating to Insomnia Cookies.", role="assistant", name="Assistant"),
]

user_id = f"user-{uuid.uuid4()}"
client.user.add(user_id=user_id, first_name="John", last_name="Doe", email="john.doe@example.com")

thread1_id = f"thread-{uuid.uuid4()}"
thread2_id = f"thread-{uuid.uuid4()}"
client.thread.create(thread_id=thread1_id, user_id=user_id)
client.thread.create(thread_id=thread2_id, user_id=user_id)

client.thread.add_messages(thread_id=thread1_id, messages=messages_thread1, ignore_roles=["assistant"])
client.thread.add_messages(thread_id=thread2_id, messages=messages_thread2, ignore_roles=["assistant"])
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

const messagesthread1: Message[] = [
    { content: "Take me to a lunch place", role: "user", name: "John Doe" },
    { content: "How about Panera Bread, Chipotle, or Green Leaf Cafe, which are nearby?", role: "assistant", name: "Assistant" },
    { content: "Do any of those have vegetarian options? I’m vegetarian", role: "user", name: "John Doe" },
    { content: "Yes, Green Leaf Cafe has vegetarian options", role: "assistant", name: "Assistant" },
    { content: "Let’s go to Green Leaf Cafe", role: "user", name: "John Doe" },
    { content: "Navigating to Green Leaf Cafe", role: "assistant", name: "Assistant" },
];

const messagesthread2: Message[] = [
    { content: "Take me to dessert", role: "user", name: "John Doe" },
    { content: "How about getting some ice cream?", role: "assistant", name: "Assistant" },
    { content: "I can't have ice cream, I'm lactose intolerant, but I'm craving a chocolate chip cookie", role: "user", name: "John Doe" },
    { content: "Sure, there's Insomnia Cookies nearby.", role: "assistant", name: "Assistant" },
    { content: "Perfect, let's go to Insomnia Cookies", role: "user", name: "John Doe" },
    { content: "Navigating to Insomnia Cookies.", role: "assistant", name: "Assistant" },
];

let userId = `user-${uuidv4()}`;
await client.user.add({ userId, firstName: "John", lastName: "Doe", email: "john.doe@example.com" });

const thread1Id = `thread-${uuidv4()}`;
const thread2Id = `thread-${uuidv4()}`;
await client.thread.create({ threadId: thread1Id, userId });
await client.thread.create({ threadId: thread2Id, userId });

await client.thread.addMessages(thread1Id, { messages: messagesthread1, ignoreRoles: ["assistant"] });
await client.thread.addMessages(thread2Id, { messages: messagesthread2, ignoreRoles: ["assistant"] });
```

```go
import (
	"github.com/getzep/zep-go/v3"
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

messagesthread1 := []zep.Message{
	{Content: "Take me to a lunch place", Role: "user", Name: zep.String("John Doe")},
	{Content: "How about Panera Bread, Chipotle, or Green Leaf Cafe, which are nearby?", Role: "assistant", Name: zep.String("Assistant")},
	{Content: "Do any of those have vegetarian options? I'm vegetarian", Role: "user", Name: zep.String("John Doe")},
	{Content: "Yes, Green Leaf Cafe has vegetarian options", Role: "assistant", Name: zep.String("Assistant")},
	{Content: "Let's go to Green Leaf Cafe", Role: "user", Name: zep.String("John Doe")},
	{Content: "Navigating to Green Leaf Cafe", Role: "assistant", Name: zep.String("Assistant")},
}
messagesthread2 := []zep.Message{
	{Content: "Take me to dessert", Role: "user", Name: zep.String("John Doe")},
	{Content: "How about getting some ice cream?", Role: "assistant", Name: zep.String("Assistant")},
	{Content: "I can't have ice cream, I'm lactose intolerant, but I'm craving a chocolate chip cookie", Role: "user", Name: zep.String("John Doe")},
	{Content: "Sure, there's Insomnia Cookies nearby.", Role: "assistant", Name: zep.String("Assistant")},
	{Content: "Perfect, let's go to Insomnia Cookies", Role: "user", Name: zep.String("John Doe")},
	{Content: "Navigating to Insomnia Cookies.", Role: "assistant", Name: zep.String("Assistant")},
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

thread1ID := "thread-" + uuid.NewString()
thread2ID := "thread-" + uuid.NewString()

thread1Req := &zep.CreateThreadRequest{
	threadID: thread1ID,
	UserID:    userID,
}
thread2Req := &zep.CreateThreadRequest{
	threadID: thread2ID,
	UserID:    userID,
}
_, err = client.Thread.Create(ctx, thread1Req)
if err != nil {
	fmt.Printf("Error creating thread 1: %v\n", err)
	return
}
_, err = client.Thread.Create(ctx, thread2Req)
if err != nil {
	fmt.Printf("Error creating thread 2: %v\n", err)
	return
}

msgPtrs1 := make([]*zep.Message, len(messagesthread1))
for i := range messagesthread1 {
	msgPtrs1[i] = &messagesthread1[i]
}
addReq1 := &zep.AddThreadMessagesRequest{
	Messages: msgPtrs1,
	IgnoreRoles: []zep.RoleType{
        zep.RoleTypeAssistantRole,
	},
}
_, err = client.Thread.AddMessages(ctx, thread1ID, addReq1)
if err != nil {
	fmt.Printf("Error adding messages to thread 1: %v\n", err)
	return
}

msgPtrs2 := make([]*zep.Message, len(messagesthread2))
for i := range messagesthread2 {
	msgPtrs2[i] = &messagesthread2[i]
}
addReq2 := &zep.AddThreadMessagesRequest{
	Messages: msgPtrs2,
	IgnoreRoles: []zep.RoleType{
		zep.RoleTypeAssistantRole,
	},
}
_, err = client.Thread.AddMessages(ctx, thread2ID, addReq2)
if err != nil {
	fmt.Printf("Error adding messages to thread 2: %v\n", err)
	return
}
```

# Example 1: Basic custom context block

## Search

For a basic custom context block, we search the graph for edges and nodes relevant to our custom query string, which typically represents a user message. Note that the default [Context Block](/retrieving-context#zeps-context-block) returned by `thread.get_user_context` uses the past few messages as the query instead.

These searches can be performed in parallel to reduce latency, using our [async Python client](/quickstart#initialize-the-client), TypeScript promises, or goroutines.

```python
query = "Find some food around here"

search_results_nodes = client.graph.search(
    query=query,
    user_id=user_id,
    scope='nodes',
    reranker='cross_encoder',
    limit=10
)
search_results_edges = client.graph.search(
    query=query,
    user_id=user_id,
    scope='edges',
    reranker='cross_encoder',
    limit=10
)
```

```typescript
let query = "Find some food around here";

const searchResultsNodes = await client.graph.search({
    userId: userId,
    query: query,
    scope: "nodes",
    reranker: "cross_encoder",
    limit: 10,
});

const searchResultsEdges = await client.graph.search({
    userId: userId,
    query: query,
    scope: "edges",
    reranker: "cross_encoder",
    limit: 10,
});
```

```go
import (
	"github.com/getzep/zep-go/v2/graph"
)

query := "Find some food around here"

searchResultsNodes, err := client.Graph.Search(
	ctx,
	&zep.GraphSearchQuery{
		UserID:  zep.String(userID),
		Query:   query,
		Scope:   zep.GraphSearchScopeNodes.Ptr(),
		Reranker: zep.RerankerCrossEncoder.Ptr(),
		Limit:   zep.Int(10),
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
	},
)
if err != nil {
	fmt.Printf("Error searching graph (edges): %v\n", err)
	return
}
```

## Build the context block

Using the search results and a few helper functions, we can build the context block. Note that for nodes, we typically want to unpack the node name and node summary, and for edges we typically want to unpack the fact and the temporal validity information:

```python
from zep_cloud import EntityEdge, EntityNode

CONTEXT_STRING_TEMPLATE = """
FACTS and ENTITIES represent relevant context to the current conversation.
# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
# NOTE: Facts ending in "present" are currently valid (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - present)" means Jane currently prefers coffee with milk)
#       Facts with a past end date used to be valid but are NOT CURRENTLY VALID (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - 2024-06-20 14:00:00)" means Jane no longer prefers coffee with milk)
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

def compose_context_block(edges: list[EntityEdge], nodes: list[EntityNode]) -> str:
    facts = [format_fact(edge) for edge in edges]
    entities = [format_entity(node) for node in nodes]
    return CONTEXT_STRING_TEMPLATE.format(facts='\n'.join(facts), entities='\n'.join(entities))

edges = search_results_edges.edges
nodes = search_results_nodes.nodes

context_block = compose_context_block(edges, nodes)
print(context_block)
```

```typescript
import type { EntityEdge, EntityNode } from "@getzep/zep-cloud/api";

const CONTEXT_STRING_TEMPLATE_1 = `FACTS and ENTITIES represent relevant context to the current conversation.
# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
# NOTE: Facts ending in "present" are currently valid (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - present)" means Jane currently prefers coffee with milk)
#       Facts with a past end date used to be valid but are NOT CURRENTLY VALID (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - 2024-06-20 14:00:00)" means Jane no longer prefers coffee with milk)
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

function composeContextBlock1(edges: EntityEdge[], nodes: EntityNode[]): string {
    const facts = edges.map(formatFact).join('\n');
    const entities = nodes.map(formatEntity).join('\n');
    return CONTEXT_STRING_TEMPLATE_1
        .replace('{facts}', facts)
        .replace('{entities}', entities);
}

const edges: EntityEdge[] = searchResultsEdges.edges ?? [];
const nodes: EntityNode[] = searchResultsNodes.nodes ?? [];

const contextBlock1 = composeContextBlock1(edges, nodes);
console.log(contextBlock1);
```

```go
import (
	"strings"
)

const CONTEXT_STRING_TEMPLATE_1 = `FACTS and ENTITIES represent relevant context to the current conversation.
# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
# NOTE: Facts ending in "present" are currently valid (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - present)" means Jane currently prefers coffee with milk)
#       Facts with a past end date used to be valid but are NOT CURRENTLY VALID (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - 2024-06-20 14:00:00)" means Jane no longer prefers coffee with milk)
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

composeContextBlock1 := func(edges []*zep.EntityEdge, nodes []*zep.EntityNode) string {
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

contextBlock1 := composeContextBlock1(edges, nodes)
fmt.Println(contextBlock1)
```

```text
FACTS and ENTITIES represent relevant context to the current conversation.
# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
# NOTE: Facts ending in "present" are currently valid (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - present)" means Jane currently prefers coffee with milk)
#       Facts with a past end date used to be valid but are NOT CURRENTLY VALID (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - 2024-06-20 14:00:00)" means Jane no longer prefers coffee with milk)
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
  - John Doe: The user is John Doe, with user ID user-34c7a6c1-ded6-4797-9620-8b80a5e7820f, email john.doe@example.com, and role user. He inquired about nearby lunch options and vegetarian choices, and expressed a preference for a chocolate chip cookie due to lactose intolerance.
</ENTITIES>
```

# Example 2: Utilizing custom entity and edge types

## Search

For a custom context block that uses custom entity and edge types, we perform multiple searches (with our custom query string) filtering to the custom entity or edge type we want to include in the context block:

These searches can be performed in parallel to reduce latency, using our [async Python client](/quickstart#initialize-the-client), TypeScript promises, or goroutines.

```python
query = "Find some food around here"

search_results_restaurant_visits = client.graph.search(
    query=query,
    user_id=user_id,
    scope='edges',
    search_filters={
        "edge_types": ["RESTAURANT_VISIT"]
    },
    reranker='cross_encoder',
    limit=10
)
search_results_dietary_preferences = client.graph.search(
    query=query,
    user_id=user_id,
    scope='edges',
    search_filters={
        "edge_types": ["DIETARY_PREFERENCE"]
    },
    reranker='cross_encoder',
    limit=10
)
search_results_restaurants = client.graph.search(
    query=query,
    user_id=user_id,
    scope='nodes',
    search_filters={
        "node_labels": ["Restaurant"]
    },
    reranker='cross_encoder',
    limit=10
)
```

```typescript
query = "Find some food around here";

const searchResultsRestaurantVisits = await client.graph.search({
    query,
    userId: userId,
    scope: "edges",
    searchFilters: {
        edgeTypes: ["RESTAURANT_VISIT"]
    },
    reranker: "cross_encoder",
    limit: 10,
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
});
```

```go
query := "Find some food around here"

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
	},
)
if err != nil {
	fmt.Printf("Error searching graph (Restaurant nodes): %v\n", err)
	return
}
```

## Build the context block

Using the search results and a few helper functions, we can compose the context block. Note that in this example, we focus on unpacking the custom attributes of the nodes and edges, but this is a design choice that you can experiment with for your use case.

Note also that we designed the context block template around the custom entity and edge types that we are unpacking into the context block:

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

def compose_context_block(restaurant_visit_edges: list[EntityEdge], dietary_preference_edges: list[EntityEdge], restaurant_nodes: list[EntityNode]) -> str:
    restaurant_visits = [format_edge_with_attributes(edge, include_timestamps=False) for edge in restaurant_visit_edges]
    dietary_preferences = [format_edge_with_attributes(edge, include_timestamps=True) for edge in dietary_preference_edges]
    restaurant_nodes = [format_node_with_attributes(node) for node in restaurant_nodes]
    return CONTEXT_STRING_TEMPLATE.format(restaurant_visits='\n'.join(restaurant_visits), dietary_preferences='\n'.join(dietary_preferences), restaurants='\n'.join(restaurant_nodes))


restaurant_visit_edges = search_results_restaurant_visits.edges
dietary_preference_edges = search_results_dietary_preferences.edges
restaurant_nodes = search_results_restaurants.nodes

context_block = compose_context_block(restaurant_visit_edges, dietary_preference_edges, restaurant_nodes)
print(context_block)
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

function composeContextBlock2(
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

const contextBlock2 = composeContextBlock2(restaurantVisitEdges, dietaryPreferenceEdges, restaurantNodes);
console.log(contextBlock2);
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

composeContextBlock2 := func(restaurantVisitEdges []*zep.EntityEdge, dietaryPreferenceEdges []*zep.EntityEdge, restaurantNodes []*zep.EntityNode) string {
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

contextBlock2 := composeContextBlock2(restaurantVisitEdges, dietaryPreferenceEdges, restaurantNodes)
fmt.Println(contextBlock2)
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

# Example 3: Basic custom context block with BFS

## Search

For a more advanced custom context block, we can enhance the search results by using Breadth-First Search (BFS) to make them more relevant to the user's recent history. In this example, we retrieve the past several [episodes](/graphiti/graphiti/adding-episodes) and use those episode IDs as the BFS node IDs. We use BFS here to make the search results more relevant to the user's recent history. You can read more about how BFS works in the [Breadth-First Search section](/searching-the-graph#breadth-first-search-bfs) of our searching the graph documentation.

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

## Build the context block

Using the search results and a few helper functions, we can build the context block. Note that for nodes, we typically want to unpack the node name and node summary, and for edges we typically want to unpack the fact and the temporal validity information:

```python
from zep_cloud import EntityEdge, EntityNode

CONTEXT_STRING_TEMPLATE = """
FACTS and ENTITIES represent relevant context to the current conversation.
# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
# NOTE: Facts ending in "present" are currently valid (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - present)" means Jane currently prefers coffee with milk)
#       Facts with a past end date used to be valid but are NOT CURRENTLY VALID (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - 2024-06-20 14:00:00)" means Jane no longer prefers coffee with milk)
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

def compose_context_block(edges: list[EntityEdge], nodes: list[EntityNode]) -> str:
    facts = [format_fact(edge) for edge in edges]
    entities = [format_entity(node) for node in nodes]
    return CONTEXT_STRING_TEMPLATE.format(facts='\n'.join(facts), entities='\n'.join(entities))

edges = search_results_edges.edges
nodes = search_results_nodes.nodes

context_block = compose_context_block(edges, nodes)
print(context_block)
```

```typescript
import type { EntityEdge, EntityNode } from "@getzep/zep-cloud/api";

const CONTEXT_STRING_TEMPLATE_1 = `FACTS and ENTITIES represent relevant context to the current conversation.
# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
# NOTE: Facts ending in "present" are currently valid (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - present)" means Jane currently prefers coffee with milk)
#       Facts with a past end date used to be valid but are NOT CURRENTLY VALID (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - 2024-06-20 14:00:00)" means Jane no longer prefers coffee with milk)
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

function composeContextBlock1(edges: EntityEdge[], nodes: EntityNode[]): string {
    const facts = edges.map(formatFact).join('\n');
    const entities = nodes.map(formatEntity).join('\n');
    return CONTEXT_STRING_TEMPLATE_1
        .replace('{facts}', facts)
        .replace('{entities}', entities);
}

const edges: EntityEdge[] = searchResultsEdges.edges ?? [];
const nodes: EntityNode[] = searchResultsNodes.nodes ?? [];

const contextBlock1 = composeContextBlock1(edges, nodes);
console.log(contextBlock1);
```

```go
import (
	"strings"
)

const CONTEXT_STRING_TEMPLATE_1 = `FACTS and ENTITIES represent relevant context to the current conversation.
# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
# NOTE: Facts ending in "present" are currently valid (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - present)" means Jane currently prefers coffee with milk)
#       Facts with a past end date used to be valid but are NOT CURRENTLY VALID (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - 2024-06-20 14:00:00)" means Jane no longer prefers coffee with milk)
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

composeContextBlock1 := func(edges []*zep.EntityEdge, nodes []*zep.EntityNode) string {
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

contextBlock1 := composeContextBlock1(edges, nodes)
fmt.Println(contextBlock1)
```

```text
FACTS and ENTITIES represent relevant context to the current conversation.
# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
# NOTE: Facts ending in "present" are currently valid (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - present)" means Jane currently prefers coffee with milk)
#       Facts with a past end date used to be valid but are NOT CURRENTLY VALID (e.g., "Jane prefers her coffee with milk (2024-01-15 10:30:00 - 2024-06-20 14:00:00)" means Jane no longer prefers coffee with milk)
<FACTS>
  - User wants to go to dessert (Date range: 2025-06-16T02:17:25Z - present)
  - John Doe wants to go to a lunch place (Date range: 2025-06-16T02:17:25Z - present)
  - John Doe said 'Perfect, let's go to Insomnia Cookies' indicating he will visit Insomnia Cookies. (Date range: 2025-06-16T02:17:25Z - present)
  - John Doe said 'Let's go to Green Leaf Cafe' indicating intention to visit (Date range: 2025-06-16T02:17:25Z - present)
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
  - John Doe: The user is John Doe, with user ID user-34c7a6c1-ded6-4797-9620-8b80a5e7820f, email john.doe@example.com, and role user. He inquired about nearby lunch options and vegetarian choices, and expressed a preference for a chocolate chip cookie due to lactose intolerance.
</ENTITIES>
```

# Example 4: Using user summary in context block

## Get user node

You can retrieve the user node and use its summary to create a simple, personalized context block. This approach is particularly useful when you want to include high-level user information generated from [user summary instructions](/users#user-summary-instructions).

**About the user node**

Each user has a single unique user node in their graph representing the user themselves. The user summary generated from user summary instructions lives on this user node. When you call `client.user.get_node()`, you are retrieving this special node that contains the user's summary.

```python Python
from zep_cloud.client import Zep

client = Zep(api_key=API_KEY)

# Get the user node and extract the summary
user_node_response = client.user.get_node(user_id=user_id)
user_summary = user_node_response.node.summary if user_node_response.node else None
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Get the user node and extract the summary
const userNodeResponse = await client.user.getNode(userId);
const userSummary = userNodeResponse.node?.summary;
```

```go Go
import (
	"context"
	"log"

	"github.com/getzep/zep-go/v3"
	zepclient "github.com/getzep/zep-go/v3/client"
	"github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(option.WithAPIKey(apiKey))

// Get the user node and extract the summary
userNodeResponse, err := client.User.GetNode(context.TODO(), userID)
if err != nil {
	log.Fatalf("Failed to get user node: %v", err)
}

var userSummary string
if userNodeResponse.Node != nil && userNodeResponse.Node.Summary != nil {
	userSummary = *userNodeResponse.Node.Summary
}
```

## Build the context block

Using the user summary, you can create a simple context block that provides personalized user information:

```python Python
# Build a simple context block with user summary
context_block = f"""USER_SUMMARY represents relevant context about the user.
# This is a high-level summary of the user
<USER_SUMMARY>
{user_summary if user_summary else "No user summary available"}
</USER_SUMMARY>
"""

print(context_block)
```

```typescript TypeScript
// Build a simple context block with user summary
const contextBlock = `USER_SUMMARY represents relevant context about the user.
# This is a high-level summary of the user
<USER_SUMMARY>
${userSummary || "No user summary available"}
</USER_SUMMARY>
`;

console.log(contextBlock);
```

```go Go
import "fmt"

// Build a simple context block with user summary
summaryText := userSummary
if summaryText == "" {
	summaryText = "No user summary available"
}

contextBlock := fmt.Sprintf(`USER_SUMMARY represents relevant context about the user.
# This is a high-level summary of the user
<USER_SUMMARY>
%s
</USER_SUMMARY>
`, summaryText)

fmt.Println(contextBlock)
```

```text
USER_SUMMARY represents relevant context about the user.
# This is a high-level summary of the user
<USER_SUMMARY>
John Doe is a software engineer who enjoys hiking and photography. He is vegetarian and lactose intolerant. He prefers detailed technical discussions and values efficiency in communication. He has requested that the AI provide concise answers with code examples when discussing programming topics.
</USER_SUMMARY>
```