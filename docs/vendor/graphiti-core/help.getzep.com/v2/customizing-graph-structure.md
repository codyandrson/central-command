> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Customizing Graph Structure

Zep enables the use of rich, domain-specific data structures in graphs through Entity Types and Edge Types, replacing generic graph nodes and edges with detailed models.

Zep classifies newly created nodes/edges as one of the default or custom types or leaves them unclassified. For example, a node representing a preference is classified as a Preference node, and attributes specific to that type are automatically populated. You may restrict graph queries to nodes/edges of a specific type, such as Preference.

The default entity types are applied to all graphs by default, but you may define additional custom types as needed.

Each node/edge is classified as a single type only. Multiple classifications are not supported.

## Default Entity Types

### Definition

The default entity types are:

* **User**: A human that is part of the current chat thread.
* **Preference**: One of the User's preferences.
* **Procedure**: A multi-step instruction informing the agent how to behave (e.g. 'When the user asks for code, respond only with code snippets followed by a bullet point explanation')

Default entity types only apply to user graphs (not group graphs). All nodes in any user graph will be classified into one of these types or none.

### Adding Data

When we add data to the graph, default entity types are automatically created:

```python
from zep_cloud.types import Message

message = {"role": "John Doe", "role_type": "user", "content": "I really like pop music, and I don't like metal"}

client.memory.add(session_id=session_id, messages=[Message(**message)])
```

```typescript
import { RoleType } from "@getzep/zep-cloud/api/types";

const messages = [{ role: "John Doe", roleType: RoleType.UserRole, content: "I really like pop music, and I don't like metal" }];

await client.memory.add(sessionId, {messages: messages});
```

```go
userRole := "John Doe"
messages := []*zep.Message{
	{
		Role:     &userRole,
		Content:  "I really like pop music, and I don't like metal",
		RoleType: "user",
	},
}

// Add the messages to the graph
_, err = client.Memory.Add(
	context.TODO(),
	sessionID,
	&zep.AddMemoryRequest{
		Messages: messages,
	},
)
if err != nil {
	log.Fatal("Error adding messages:", err)
}
```

### Searching

When searching nodes in the graph, you may provide a list of types to filter the search by. The provided types are ORed together. Search results will only include nodes that satisfy one of the provided types:

```python
search_results = client.graph.search(
    user_id=user_id,
    query="the user's music preferences",
    scope="nodes",
    search_filters={
        "node_labels": ["Preference"]
    }
)
for i, node in enumerate(search_results.nodes):
    preference = node.attributes
    print(f"Preference {i+1}:{preference}")
```

```typescript
const searchResults = await client.graph.search({
  userId: userId,
  query: "the user's music preferences",
  scope: "nodes",
  searchFilters: {
    nodeLabels: ["Preference"],
  },
});

if (searchResults.nodes && searchResults.nodes.length > 0) {
  for (let i = 0; i < searchResults.nodes.length; i++) {
    const node = searchResults.nodes[i];
    const preference = node.attributes;
    console.log(`Preference ${i + 1}: ${JSON.stringify(preference)}`);
  }
}
```

```go
searchFilters := zep.SearchFilters{NodeLabels: []string{"Preference"}}
searchResults, err := client.Graph.Search(
	ctx,
	&zep.GraphSearchQuery{
		UserID:        zep.String(userID),
		Query:         "the user's music preferences",
		Scope:         zep.GraphSearchScopeNodes.Ptr(),
		SearchFilters: &searchFilters,
	},
)
if err != nil {
	log.Fatal("Error searching graph:", err)
}

for i, node := range searchResults.Nodes {
	// Convert attributes map to JSON for pretty printing
	attributesJSON, err := json.MarshalIndent(node.Attributes, "", "  ")
	if err != nil {
		log.Fatal("Error marshaling attributes:", err)
	}
	
	fmt.Printf("Preference %d:\n%s\n\n", i+1, string(attributesJSON))
}
```

```text
Preference 1: {'category': 'Music', 'description': 'Pop Music is a genre of music characterized by its catchy melodies and widespread appeal.', 'labels': ['Entity', 'Preference']}
Preference 2: {'category': 'Music', 'description': 'Metal Music is a genre of music characterized by its heavy sound and complex compositions.', 'labels': ['Entity', 'Preference']}
```

## Custom Entity and Edge Types

Start with fewer, more generic custom types with minimal fields and simple definitions, then incrementally add complexity as needed. This functionality requires prompt engineering and iterative optimization of the class and field descriptions, so it's best to start simple.

### Definition

In addition to the default entity types, you may specify your own custom entity and custom edge types. You need to provide a description of the type and a description for each of the fields. The syntax for this is different for each language.

The number of custom entity types and custom edge types you may define on one ontology depends on your plan. The [pricing](https://www.getzep.com/pricing) page lists those limits.

The entity-type limit and the edge-type limit apply independently. The same limits apply whether you set the ontology for a specific graph or project-wide. The entity-type limit does not include Zep's default types.

Each model may have up to 10 fields.

When creating custom entity or edge types, you may not use the following attribute names (including in Go struct tags), as they conflict with default node attributes: `uuid`, `name`, `group_id`, `name_embedding`, `summary`, and `created_at`.

Including attributes on custom entity and edge types is an advanced feature designed for precision context engineering where you only want to utilize specific field values when constructing your context string. [See here for an example](/cookbooks/customizing-memory-context-string#example-2-using-custom-entity-and-edge-attributes). Many agent memory use cases can be solved with node summaries and facts alone. Custom attributes should only be added when you need structured field values for precise context retrieval rather than general conversational memory.

```python
from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel, EntityBoolean
from pydantic import Field

class Restaurant(EntityModel):
    """
    Represents a specific restaurant.
    """
    cuisine_type: EntityText = Field(description="The cuisine type of the restaurant, for example: American, Mexican, Indian, etc.", default=None)
    dietary_accommodation: EntityText = Field(description="The dietary accommodation of the restaurant, if any, for example: vegetarian, vegan, etc.", default=None)

class Audiobook(EntityModel):
    """
    Represents an audiobook entity.
    """
    genre: EntityText = Field(description="The genre of the audiobook, for example: self-help, fiction, nonfiction, etc.", default=None)

class RestaurantVisit(EdgeModel):
    """
    Represents the fact that the user visited a restaurant.
    """
    restaurant_name: EntityText = Field(description="The name of the restaurant the user visited", default=None)

class AudiobookListen(EdgeModel):
    """
    Represents the fact that the user listened to or played an audiobook.
    """
    audiobook_title: EntityText = Field(description="The title of the audiobook the user listened to or played", default=None)

class DietaryPreference(EdgeModel):
    """
    Represents the fact that the user has a dietary preference or dietary restriction.
    """
    preference_type: EntityText = Field(description="Preference type of the user: anything, vegetarian, vegan, peanut allergy, etc.", default=None)
    allergy: EntityBoolean = Field(description="Whether this dietary preference represents a user allergy: True or false", default=None)
```

```typescript
import { entityFields, EntityType, EdgeType } from "@getzep/zep-cloud";

const RestaurantSchema: EntityType = {
    description: "Represents a specific restaurant.",
    fields: {
        cuisine_type: entityFields.text("The cuisine type of the restaurant, for example: American, Mexican, Indian, etc."),
        dietary_accommodation: entityFields.text("The dietary accommodation of the restaurant, if any, for example: vegetarian, vegan, etc."),
    },
};

const AudiobookSchema: EntityType = {
    description: "Represents an audiobook entity.",
    fields: {
        genre: entityFields.text("The genre of the audiobook, for example: self-help, fiction, nonfiction, etc."),
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

const AudiobookListen: EdgeType = {
    description: "Represents the fact that the user listened to or played an audiobook.",
    fields: {
        audiobook_title: entityFields.text("The title of the audiobook the user listened to or played"),
    },
    sourceTargets: [
        { source: "User", target: "Audiobook" },
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
```

```go
type Restaurant struct {
    zep.BaseEntity `name:"Restaurant" description:"Represents a specific restaurant."`
    CuisineType           string `description:"The cuisine type of the restaurant, for example: American, Mexican, Indian, etc." json:"cuisine_type,omitempty"`
    DietaryAccommodation  string `description:"The dietary accommodation of the restaurant, if any, for example: vegetarian, vegan, etc." json:"dietary_accommodation,omitempty"`
}

type Audiobook struct {
    zep.BaseEntity `name:"Audiobook" description:"Represents an audiobook entity."`
    Genre string `description:"The genre of the audiobook, for example: self-help, fiction, nonfiction, etc." json:"genre,omitempty"`
}

type RestaurantVisit struct {
    zep.BaseEdge `name:"RESTAURANT_VISIT" description:"Represents the fact that the user visited a restaurant."`
    RestaurantName string `description:"The name of the restaurant the user visited" json:"restaurant_name,omitempty"`
}

type AudiobookListen struct {
    zep.BaseEdge `name:"AUDIOBOOK_LISTEN" description:"Represents the fact that the user listened to or played an audiobook."`
    AudiobookTitle string `description:"The title of the audiobook the user listened to or played" json:"audiobook_title,omitempty"`
}

type DietaryPreference struct {
    zep.BaseEdge `name:"DIETARY_PREFERENCE" description:"Represents the fact that the user has a dietary preference or dietary restriction."`
    PreferenceType string `description:"Preference type of the user: anything, vegetarian, vegan, peanut allergy, etc." json:"preference_type,omitempty"`
    Allergy        bool   `description:"Whether this dietary preference represents a user allergy: True or false" json:"allergy,omitempty"`
}
```

### Setting Entity and Edge Types

You can then set these custom entity and edge types as the graph ontology for your current [Zep project](/projects). Note that for custom edge types, you can require the source and destination nodes to be a certain type, or allow them to be any type:

```python
from zep_cloud import EntityEdgeSourceTarget

client.graph.set_ontology(
    entities={
        "Restaurant": Restaurant,
        "Audiobook": Audiobook,
    },
    edges={
        "RESTAURANT_VISIT": (
            RestaurantVisit,
            [EntityEdgeSourceTarget(source="User", target="Restaurant")]
        ),
        "AUDIOBOOK_LISTEN": (
            AudiobookListen,
            [EntityEdgeSourceTarget(source="User", target="Audiobook")]
        ),
        "DIETARY_PREFERENCE": (
            DietaryPreference,
            [EntityEdgeSourceTarget(source="User")]
        ),
    }
)
```

```typescript
await client.graph.setOntology(
    {
        Restaurant: RestaurantSchema,
        Audiobook: AudiobookSchema,
    },
    {
        RESTAURANT_VISIT: RestaurantVisit,
        AUDIOBOOK_LISTEN: AudiobookListen,
        DIETARY_PREFERENCE: DietaryPreference,
    }
);
```

```go
_, err = client.Graph.SetOntology(
    ctx,
    []zep.EntityDefinition{
        Restaurant{},
        Audiobook{},
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
            EdgeModel: AudiobookListen{},
            SourceTargets: []zep.EntityEdgeSourceTarget{
                {
                    Source: zep.String("User"),
                    Target: zep.String("Audiobook"),
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
```

### Adding Data

Now, when you add data to the graph, new nodes and edges are classified into exactly one of the overall set of entity or edge types respectively, or no type:

```python
from zep_cloud import Message
import uuid

messages_session1 = [
    Message(content="Take me to a lunch place", role_type="user", role="John Doe"),
    Message(content="How about Panera Bread, Chipotle, or Green Leaf Cafe, which are nearby?", role_type="assistant", role="Assistant"),
    Message(content="Do any of those have vegetarian options? I'm vegetarian", role_type="user", role="John Doe"),
    Message(content="Yes, Green Leaf Cafe has vegetarian options", role_type="assistant", role="Assistant"),
    Message(content="Let's go to Green Leaf Cafe", role_type="user", role="John Doe"),
    Message(content="Navigating to Green Leaf Cafe", role_type="assistant", role="Assistant"),
]

messages_session2 = [
    Message(content="Play the 7 habits of highly effective people", role_type="user", role="John Doe"),
    Message(content="Playing the 7 habits of highly effective people", role_type="assistant", role="Assistant"),
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
import { v4 as uuidv4 } from "uuid";
import type { Message } from "@getzep/zep-cloud/api";

const messagesSession1: Message[] = [
    { content: "Take me to a lunch place", roleType: "user", role: "John Doe" },
    { content: "How about Panera Bread, Chipotle, or Green Leaf Cafe, which are nearby?", roleType: "assistant", role: "Assistant" },
    { content: "Do any of those have vegetarian options? I'm vegetarian", roleType: "user", role: "John Doe" },
    { content: "Yes, Green Leaf Cafe has vegetarian options", roleType: "assistant", role: "Assistant" },
    { content: "Let's go to Green Leaf Cafe", roleType: "user", role: "John Doe" },
    { content: "Navigating to Green Leaf Cafe", roleType: "assistant", role: "Assistant" },
];

const messagesSession2: Message[] = [
    { content: "Play the 7 habits of highly effective people", roleType: "user", role: "John Doe" },
    { content: "Playing the 7 habits of highly effective people", roleType: "assistant", role: "Assistant" },
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
messagesSession1 := []zep.Message{
    {Content: "Take me to a lunch place", RoleType: "user", Role: zep.String("John Doe")},
    {Content: "How about Panera Bread, Chipotle, or Green Leaf Cafe, which are nearby?", RoleType: "assistant", Role: zep.String("Assistant")},
    {Content: "Do any of those have vegetarian options? I'm vegetarian", RoleType: "user", Role: zep.String("John Doe")},
    {Content: "Yes, Green Leaf Cafe has vegetarian options", RoleType: "assistant", Role: zep.String("Assistant")},
    {Content: "Let's go to Green Leaf Cafe", RoleType: "user", Role: zep.String("John Doe")},
    {Content: "Navigating to Green Leaf Cafe", RoleType: "assistant", Role: zep.String("Assistant")},
}
messagesSession2 := []zep.Message{
    {Content: "Play the 7 habits of highly effective people", RoleType: "user", Role: zep.String("John Doe")},
    {Content: "Playing the 7 habits of highly effective people", RoleType: "assistant", Role: zep.String("Assistant")},
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

### Searching/Retrieving

Now that a graph with custom entity and edge types has been created, you may filter node search results by entity type, or edge search results by edge type.

Below, you can see the examples that were created from our data of each of the entity and edge types that we defined:

```python
search_results_restaurants = client.graph.search(
    user_id=user_id,
    query="Take me to a restaurant",
    scope="nodes",
    search_filters={
        "node_labels": ["Restaurant"]
    },
    limit=1,
)
node = search_results_restaurants.nodes[0]
print(f"Node name: {node.name}")
print(f"Node labels: {node.labels}")
print(f"Cuisine type: {node.attributes.get('cuisine_type')}")
print(f"Dietary accommodation: {node.attributes.get('dietary_accommodation')}")
```

```typescript
let searchResults = await client.graph.search({
    userId: userId,
    query: "Take me to a restaurant",
    scope: "nodes",
    searchFilters: { nodeLabels: ["Restaurant"] },
    limit: 1,
});
if (searchResults.nodes && searchResults.nodes.length > 0) {
    const node = searchResults.nodes[0];
    console.log(`Node name: ${node.name}`);
    console.log(`Node labels: ${node.labels}`);
    console.log(`Cuisine type: ${node.attributes?.cuisine_type}`);
    console.log(`Dietary accommodation: ${node.attributes?.dietary_accommodation}`);
}
```

```go
searchFiltersRestaurants := zep.SearchFilters{NodeLabels: []string{"Restaurant"}}
searchResultsRestaurants, err := client.Graph.Search(
    ctx,
    &zep.GraphSearchQuery{
        UserID:        zep.String(userID),
        Query:         "Take me to a restaurant",
        Scope:         zep.GraphSearchScopeNodes.Ptr(),
        SearchFilters: &searchFiltersRestaurants,
        Limit:         zep.Int(1),
    },
)
if err != nil {
    fmt.Printf("Error searching graph (Restaurant node): %v\n", err)
    return
}
if len(searchResultsRestaurants.Nodes) > 0 {
    node := searchResultsRestaurants.Nodes[0]
    fmt.Printf("Node name: %s\n", node.Name)
    fmt.Printf("Node labels: %v\n", node.Labels)
    fmt.Printf("Cuisine type: %v\n", node.Attributes["cuisine_type"])
    fmt.Printf("Dietary accommodation: %v\n", node.Attributes["dietary_accommodation"])
}
```

```text
Node name: Green Leaf Cafe
Node labels: Entity,Restaurant
Cuisine type: undefined
Dietary accommodation: vegetarian
```

```python
search_results_audiobook_nodes = client.graph.search(
    user_id=user_id,
    query="Play an audiobook",
    scope="nodes",
    search_filters={
        "node_labels": ["Audiobook"]
    },
    limit=1,
)
node = search_results_audiobook_nodes.nodes[0]
print(f"Node name: {node.name}")
print(f"Node labels: {node.labels}")
print(f"Genre: {node.attributes.get('genre')}")
```

```typescript
searchResults = await client.graph.search({
    userId: userId,
    query: "Play an audiobook",
    scope: "nodes",
    searchFilters: { nodeLabels: ["Audiobook"] },
    limit: 1,
});
if (searchResults.nodes && searchResults.nodes.length > 0) {
    const node = searchResults.nodes[0];
    console.log(`Node name: ${node.name}`);
    console.log(`Node labels: ${node.labels}`);
    console.log(`Genre: ${node.attributes?.genre}`);
}
```

```go
searchFiltersAudiobook := zep.SearchFilters{NodeLabels: []string{"Audiobook"}}
searchResultsAudiobook, err := client.Graph.Search(
    ctx,
    &zep.GraphSearchQuery{
        UserID:        zep.String(userID),
        Query:         "Play an audiobook",
        Scope:         zep.GraphSearchScopeNodes.Ptr(),
        SearchFilters: &searchFiltersAudiobook,
        Limit:         zep.Int(1),
    },
)
if err != nil {
    fmt.Printf("Error searching graph (Audiobook node): %v\n", err)
    return
}
if len(searchResultsAudiobook.Nodes) > 0 {
    node := searchResultsAudiobook.Nodes[0]
    fmt.Printf("Node name: %s\n", node.Name)
    fmt.Printf("Node labels: %v\n", node.Labels)
    fmt.Printf("Genre: %v\n", node.Attributes["genre"])
}
```

```text
Node name: 7 habits of highly effective people
Node labels: Entity,Audiobook
Genre: undefined
```

```python
search_results_visits = client.graph.search(
    user_id=user_id,
    query="Take me to a restaurant",
    scope="edges",
    search_filters={
        "edge_types": ["RESTAURANT_VISIT"]
    },
    limit=1,
)
edge = search_results_visits.edges[0]
print(f"Edge fact: {edge.fact}")
print(f"Edge type: {edge.name}")
print(f"Restaurant name: {edge.attributes.get('restaurant_name')}")
```

```typescript
searchResults = await client.graph.search({
    userId: userId,
    query: "Take me to a restaurant",
    scope: "edges",
    searchFilters: { edgeTypes: ["RESTAURANT_VISIT"] },
    limit: 1,
});
if (searchResults.edges && searchResults.edges.length > 0) {
    const edge = searchResults.edges[0];
    console.log(`Edge fact: ${edge.fact}`);
    console.log(`Edge type: ${edge.name}`);
    console.log(`Restaurant name: ${edge.attributes?.restaurant_name}`);
}
```

```go
searchFiltersVisits := zep.SearchFilters{EdgeTypes: []string{"RESTAURANT_VISIT"}}
searchResultsVisits, err := client.Graph.Search(
    ctx,
    &zep.GraphSearchQuery{
        UserID:        zep.String(userID),
        Query:         "Take me to a restaurant",
        Scope:         zep.GraphSearchScopeEdges.Ptr(),
        SearchFilters: &searchFiltersVisits,
        Limit:         zep.Int(1),
    },
)
if err != nil {
    fmt.Printf("Error searching graph (RESTAURANT_VISIT): %v\n", err)
    return
}
if len(searchResultsVisits.Edges) > 0 {
    edge := searchResultsVisits.Edges[0]
    var visit RestaurantVisit
    err := zep.UnmarshalEdgeAttributes(edge.Attributes, &visit)
    if err != nil {
        fmt.Printf("\t\tError converting edge to RestaurantVisit struct: %v\n", err)
    } else {
        fmt.Printf("Edge fact: %s\n", edge.Fact)
        fmt.Printf("Edge type: %s\n", edge.Name)
        fmt.Printf("Restaurant name: %s\n", visit.RestaurantName)
    }
}
```

```text
Edge fact: User John Doe is going to Green Leaf Cafe
Edge type: RESTAURANT_VISIT
Restaurant name: Green Leaf Cafe
```

```python
search_results_audiobook_listens = client.graph.search(
    user_id=user_id,
    query="Play an audiobook",
    scope="edges",
    search_filters={
        "edge_types": ["AUDIOBOOK_LISTEN"]
    },
    limit=1,
)
edge = search_results_audiobook_listens.edges[0]
print(f"Edge fact: {edge.fact}")
print(f"Edge type: {edge.name}")
print(f"Audiobook title: {edge.attributes.get('audiobook_title')}")
```

```typescript
searchResults = await client.graph.search({
    userId: userId,
    query: "Play an audiobook",
    scope: "edges",
    searchFilters: { edgeTypes: ["AUDIOBOOK_LISTEN"] },
    limit: 1,
});
if (searchResults.edges && searchResults.edges.length > 0) {
    const edge = searchResults.edges[0];
    console.log(`Edge fact: ${edge.fact}`);
    console.log(`Edge type: ${edge.name}`);
    console.log(`Audiobook title: ${edge.attributes?.audiobook_title}`);
}
```

```go
searchFiltersAudiobookListen := zep.SearchFilters{EdgeTypes: []string{"AUDIOBOOK_LISTEN"}}
searchResultsAudiobookListen, err := client.Graph.Search(
    ctx,
    &zep.GraphSearchQuery{
        UserID:        zep.String(userID),
        Query:         "Play an audiobook",
        Scope:         zep.GraphSearchScopeEdges.Ptr(),
        SearchFilters: &searchFiltersAudiobookListen,
        Limit:         zep.Int(1),
    },
)
if err != nil {
    fmt.Printf("Error searching graph (AUDIOBOOK_LISTEN): %v\n", err)
    return
}
if len(searchResultsAudiobookListen.Edges) > 0 {
    edge := searchResultsAudiobookListen.Edges[0]
    var listen AudiobookListen
    err := zep.UnmarshalEdgeAttributes(edge.Attributes, &listen)
    if err != nil {
        fmt.Printf("Error converting edge to AudiobookListen struct: %v\n", err)
    } else {
        fmt.Printf("Edge fact: %s\n", edge.Fact)
        fmt.Printf("Edge type: %s\n", edge.Name)
        fmt.Printf("Audiobook title: %s\n", listen.AudiobookTitle)
    }
}
```

```text
Edge fact: John Doe requested to play the audiobook '7 habits of highly effective people'
Edge type: AUDIOBOOK_LISTEN
Audiobook title: 7 habits of highly effective people
```

```python
search_results_dietary_preference = client.graph.search(
    user_id=user_id,
    query="Find some food around here",
    scope="edges",
    search_filters={
        "edge_types": ["DIETARY_PREFERENCE"]
    },
    limit=1,
)
edge = search_results_dietary_preference.edges[0]
print(f"Edge fact: {edge.fact}")
print(f"Edge type: {edge.name}")
print(f"Preference type: {edge.attributes.get('preference_type')}")
print(f"Allergy: {edge.attributes.get('allergy')}")
```

```typescript
searchResults = await client.graph.search({
    userId: userId,
    query: "Find some food around here",
    scope: "edges",
    searchFilters: { edgeTypes: ["DIETARY_PREFERENCE"] },
    limit: 1,
});
if (searchResults.edges && searchResults.edges.length > 0) {
    const edge = searchResults.edges[0];
    console.log(`Edge fact: ${edge.fact}`);
    console.log(`Edge type: ${edge.name}`);
    console.log(`Preference type: ${edge.attributes?.preference_type}`);
    console.log(`Allergy: ${edge.attributes?.allergy}`);
}
```

```go
searchFiltersDietary := zep.SearchFilters{EdgeTypes: []string{"DIETARY_PREFERENCE"}}
searchResultsDietary, err := client.Graph.Search(
    ctx,
    &zep.GraphSearchQuery{
        UserID:        zep.String(userID),
        Query:         "Find some food around here",
        Scope:         zep.GraphSearchScopeEdges.Ptr(),
        SearchFilters: &searchFiltersDietary,
        Limit:         zep.Int(1),
    },
)
if err != nil {
    fmt.Printf("Error searching graph (DIETARY_PREFERENCE): %v\n", err)
    return
}
if len(searchResultsDietary.Edges) > 0 {
    edge := searchResultsDietary.Edges[0]
    var dietary DietaryPreference
    err := zep.UnmarshalEdgeAttributes(edge.Attributes, &dietary)
    if err != nil {
        fmt.Printf("Error converting edge to DietaryPreference struct: %v\n", err)
    } else {
        fmt.Printf("Edge fact: %s\n", edge.Fact)
        fmt.Printf("Edge type: %s\n", edge.Name)
        fmt.Printf("Preference type: %s\n", dietary.PreferenceType)
        fmt.Printf("Allergy: %v\n", dietary.Allergy)
    }
}
```

```text
Edge fact: User states 'I'm vegetarian' indicating a dietary preference.
Edge type: DIETARY_PREFERENCE
Preference type: vegetarian
Allergy: false
```

Additionally, you can provide multiple types in search filters, and the types will be ORed together:

```python
search_results_dietary_preference = client.graph.search(
    user_id=user_id,
    query="Find some food around here",
    scope="edges",
    search_filters={
        "edge_types": ["DIETARY_PREFERENCE", "RESTAURANT_VISIT"]
    },
    limit=2,
)
for edge in search_results_dietary_preference.edges:
    print(f"Edge fact: {edge.fact}")
    print(f"Edge type: {edge.name}")
    if edge.name == "DIETARY_PREFERENCE":
        print(f"Preference type: {edge.attributes.get('preference_type')}")
        print(f"Allergy: {edge.attributes.get('allergy')}")
    elif edge.name == "RESTAURANT_VISIT":
        print(f"Restaurant name: {edge.attributes.get('restaurant_name')}")
    print("\n")
```

```typescript
searchResults = await client.graph.search({
    userId: userId,
    query: "Find some food around here",
    scope: "edges",
    searchFilters: { edgeTypes: ["DIETARY_PREFERENCE", "RESTAURANT_VISIT"] },
    limit: 2,
});
if (searchResults.edges && searchResults.edges.length > 0) {
    for (const edge of searchResults.edges) {
        console.log(`Edge fact: ${edge.fact}`);
        console.log(`Edge type: ${edge.name}`);
        if (edge.name === "DIETARY_PREFERENCE") {
            console.log(`Preference type: ${edge.attributes?.preference_type}`);
            console.log(`Allergy: ${edge.attributes?.allergy}`);
        } else if (edge.name === "RESTAURANT_VISIT") {
            console.log(`Restaurant name: ${edge.attributes?.restaurant_name}`);
        }
        console.log("\n");
    }
}
```

```go
searchFiltersDietaryAndRestaurantVisit := zep.SearchFilters{EdgeTypes: []string{"DIETARY_PREFERENCE", "RESTAURANT_VISIT"}}
searchResultsDietaryAndRestaurantVisit, err := client.Graph.Search(
    ctx,
    &zep.GraphSearchQuery{
        UserID:        zep.String(userID),
        Query:         "Find some food around here",
        Scope:         zep.GraphSearchScopeEdges.Ptr(),
        SearchFilters: &searchFiltersDietaryAndRestaurantVisit,
        Limit:         zep.Int(2),
    },
)
if err != nil {
    fmt.Printf("Error searching graph (DIETARY_PREFERENCE and RESTAURANT_VISIT): %v\n", err)
    return
}
if len(searchResultsDietaryAndRestaurantVisit.Edges) > 0 {
    for _, edge := range searchResultsDietaryAndRestaurantVisit.Edges {
        switch edge.Name {
        case "DIETARY_PREFERENCE":
            var dietary DietaryPreference
            err := zep.UnmarshalEdgeAttributes(edge.Attributes, &dietary)
            if err != nil {
                fmt.Printf("Error converting edge to DietaryPreference struct: %v\n", err)
            } else {
                fmt.Printf("Edge fact: %s\n", edge.Fact)
                fmt.Printf("Edge type: %s\n", edge.Name)
                fmt.Printf("Preference type: %s\n", dietary.PreferenceType)
                fmt.Printf("Allergy: %v\n", dietary.Allergy)
            }
        case "RESTAURANT_VISIT":
            var visit RestaurantVisit
            err := zep.UnmarshalEdgeAttributes(edge.Attributes, &visit)
            if err != nil {
                fmt.Printf("Error converting edge to RestaurantVisit struct: %v\n", err)
            } else {
                fmt.Printf("Edge fact: %s\n", edge.Fact)
                fmt.Printf("Edge type: %s\n", edge.Name)
                fmt.Printf("Restaurant name: %s\n", visit.RestaurantName)
            }
        default:
            fmt.Printf("Unknown edge type: %s\n", edge.Name)
        }
        fmt.Println()
    }
}
```

```text
Edge fact: User John Doe is going to Green Leaf Cafe
Edge type: RESTAURANT_VISIT
Restaurant name: Green Leaf Cafe
```

```text
Edge fact: User states 'I'm vegetarian' indicating a dietary preference.
Edge type: DIETARY_PREFERENCE
Preference type: vegetarian
Allergy: false
```

### Important Notes/Tips

Some notes regarding custom entity and edge types:

* The `set_ontology` method overwrites any previously defined custom entity and edge types, so the set of custom entity and edge types is always the list of types provided in the last `set_ontology` method call
* The overall set of entity types for a project includes both the custom entity types you set and the default entity types
* There are no default edge types
* You can overwrite the default entity types by providing custom entity types with the same names
* Changing the custom entity or edge types will not update previously created nodes or edges. The classification and attributes of existing nodes and edges will stay the same. The only thing that can change existing classifications or attributes is adding data that provides new information.
* When creating custom entity or edge types, avoid using the following attribute names (including in Go struct tags), as they conflict with default attributes: `uuid`, `name`, `group_id`, `name_embedding`, `summary`, and `created_at`
* **Tip**: Design custom entity types to represent entities/nouns, and design custom edge types to represent relationships/verbs. Otherwise, your type might be represented in the graph as an edge more often than as a node or vice versa.
* **Tip**: Avoid defining entity or edge types whose definitions overlap — aim for types that are mutually exclusive, so any given fact has one clear home. During ingestion each fact is classified into a single best-fit type based on the type's description (and, for edge types, its source and target entity types), not its name, so give each type a clear, distinct description. Overlapping or ambiguous descriptions lead to inconsistent classification, because there is no single correct type for the fact. Overlapping names are fine as long as the descriptions make the types genuinely distinct.
* **Tip**: If you have overlapping entity or edge types (e.g. 'Hobby' and 'Hiking'), you can prioritize one type over another by mentioning which to prioritize in the entity or edge type descriptions