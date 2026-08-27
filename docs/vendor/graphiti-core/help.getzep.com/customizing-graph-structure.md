> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Customizing Graph Structure

> Define custom entity and edge types so Zep extracts the entities and relationships that matter for your domain — a domain ontology for precise context recall.

Zep enables the use of rich, domain-specific data structures in graphs through Entity Types and Edge Types, replacing generic graph nodes and edges with detailed models.

Zep classifies newly created nodes/edges as one of the default or custom types or leaves them unclassified. For example, a node representing a preference is classified as a Preference node, and attributes specific to that type are automatically populated. You may restrict graph queries to nodes/edges of a specific type, such as Preference. To stop Zep from extracting entities and edges outside your configured types altogether, see [Strict ontology](#strict-ontology) below.

The default entity and edge types are applied to user graphs (not all graphs) by default, but you may define additional custom types as needed.

Each node/edge is classified as a single type only. Multiple classifications are not supported.

Set the ontology before ingestion. Ontologies affect only data processed after they are set; existing data is not re-extracted automatically. See [Prepare Data for Ingestion](/prepare-data-for-ingestion#configure-identity-properties-for-extracted-entities) for identity and ordering guidance.

## Why use custom ontology

Custom entity and edge types improve graph construction by focusing extraction on the most important entities and relationships for your domain. When a custom ontology is defined, Zep's extraction process prioritizes these types, resulting in more relevant and structured data for your use case.

Even if Zep's default types cover your basic needs, defining a custom ontology tailored to your domain often produces better results because:

* Extraction focuses on entities and relationships that matter most to your application
* Graph structure aligns more closely with your domain model
* Retrieved context becomes more relevant and actionable

## Default Entity and Edge Types

### Definition

Zep provides default entity and edge types that are automatically applied to user graphs (not all graphs). These types help classify and structure the information extracted from conversations.

You can view the exact definition for the default ontology [here](https://github.com/getzep/zep/blob/main/ontology/default_ontology.py).

#### Default Entity Types

The default entity types are:

* **User**: A Zep user specified by role in chat messages. There can only be a single User entity.
* **Assistant**: Represents the AI assistant in the conversation. This entity is a singleton.
* **Preference**: Entities mentioned in contexts expressing user preferences, choices, opinions, or selections. This classification is prioritized over most other classifications.
* **Location**: A physical or virtual place where activities occur or entities exist. Use this classification only after checking if the entity fits other more specific types.
* **Event**: A time-bound activity, occurrence, or experience.
* **Object**: A physical item, tool, device, or possession. Use this classification only as a last resort after checking other types.
* **Topic**: A subject of conversation, interest, or knowledge domain. Use this classification only as a last resort after checking other types.
* **Organization**: A company, institution, group, or formal entity.
* **Document**: Information content in various forms.

#### Default Edge Types

The default edge types are:

* **LOCATED\_AT**: Represents that an entity exists or occurs at a specific location. Connects any entity to a Location.
* **OCCURRED\_AT**: Represents that an event happened at a specific time or location. Connects an Event to any entity.

Default entity and edge types apply to user graphs. All nodes and edges in any user graph will be classified into one of these types or none.

### Adding Data

When we add data to the graph, default entity and edge types are automatically created:

```python
from zep_cloud.types import Message

message = {
    "name": "John Doe",
    "role": "user",
    "content": "I really like pop music, and I don't like metal",
}

client.thread.add_messages(thread_id=thread_id, messages=[Message(**message)])
```

```typescript
const messages = [{
    name: "John Doe",
    role: "user",
    content: "I really like pop music, and I don't like metal",
}];

await client.thread.addMessages(threadId, {messages: messages});
```

```go
userName := "John Doe"
messages := []*v3.Message{
    {
        Name:    &userName,
        Content: "I really like pop music, and I don't like metal",
        Role:    "user",
    },
}

// Add the messages to the graph
_, err = zepClient.Thread.AddMessages(
    context.TODO(),
    threadId,
    &v3.AddThreadMessagesRequest{
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
searchFilters := v3.SearchFilters{NodeLabels: []string{"Preference"}}
searchResults, err := client.Graph.Search(
	ctx,
	&v3.GraphSearchQuery{
		UserID:        v3.String(userID),
		Query:         "the user's music preferences",
		Scope:         v3.GraphSearchScopeNodes.Ptr(),
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

### Disabling Default Ontology

In some cases, you may want to disable the default entity and edge types for specific users and only use custom types you define. You can do this by setting the `disable_default_ontology` flag when creating or updating a user.

When `disable_default_ontology` is set to `true`:

* Only custom entity and edge types you define will be used for classification
* The default entity and edge types (User, Assistant, Preference, Location, etc.) will not be applied
* Nodes and edges will only be classified as your custom types or remain unclassified

This is useful when you need precise control over your graph structure and want to ensure only domain-specific types are used.

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

# Create a user with default ontology disabled
user = client.user.add(
    user_id=user_id,
    first_name="John",
    last_name="Doe",
    email="john.doe@example.com",
    disable_default_ontology=True
)

# Or update an existing user to disable default ontology
client.user.update(
    user_id=user_id,
    disable_default_ontology=True
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Create a user with default ontology disabled
const user = await client.user.add({
  userId: userId,
  firstName: "John",
  lastName: "Doe",
  email: "john.doe@example.com",
  disableDefaultOntology: true
});

// Or update an existing user to disable default ontology
await client.user.update(userId, {
  disableDefaultOntology: true
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

// Create a user with default ontology disabled
user, err := client.User.Add(
    context.TODO(),
    &zep.CreateUserRequest{
        UserID:                  userID,
        FirstName:              zep.String("John"),
        LastName:               zep.String("Doe"),
        Email:                  zep.String("john.doe@example.com"),
        DisableDefaultOntology: zep.Bool(true),
    },
)

// Or update an existing user to disable default ontology
_, err = client.User.Update(
    context.TODO(),
    userID,
    &zep.UpdateUserRequest{
        DisableDefaultOntology: zep.Bool(true),
    },
)
```

## Custom Entity and Edge Types

Start with fewer, more generic custom types with minimal fields and simple definitions, then incrementally add complexity as needed. This functionality requires prompt engineering and iterative optimization of the class and field descriptions, so it's best to start simple.

### Definition

In addition to the default entity and edge types, you may specify your own custom entity and custom edge types. You need to provide a description of the type and a description for each of the fields. The syntax for this is different for each language.

The number of custom entity types and custom edge types you may define on one ontology depends on your plan. The [pricing](https://www.getzep.com/pricing) page lists those limits.

The entity-type limit and the edge-type limit apply independently. The same limits apply whether you set the ontology for a specific graph or project-wide. The entity-type limit does not include Zep's default types.

Each model may have up to 10 fields.

When creating custom entity or edge types, you may not use the following attribute names (including in Go struct tags), as they conflict with default node attributes: `uuid`, `name`, `graph_id`, `name_embedding`, `summary`, and `created_at`.

Including attributes on custom entity and edge types is an advanced feature designed for precision context engineering where you only want to utilize specific field values when constructing your context block. [See here for an example](cookbook/advanced-context-block-construction#example-2-utilizing-custom-entity-and-edge-types). Many agent memory use cases can be solved with node summaries and facts alone. Custom attributes should only be added when you need structured field values for precise context retrieval rather than general conversational context.

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

You can set these custom entity and edge types as the graph ontology for your current Zep project. The ontology can be applied either project-wide to all users and graphs, or targeted to specific users and graphs only.

#### Setting Types Project Wide

When no user IDs or graph IDs are provided, the ontology is set for the entire project. All users and graphs within the project will use this ontology. Note that for custom edge types, you can require the source and destination nodes to be a certain type, or allow them to be any type:

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

#### Setting Types For Specific Graphs

You can also set the ontology for specific users and/or graphs by providing user IDs and graph IDs. When these parameters are provided, the ontology will only apply to the specified users and graphs, while other users and graphs in the project will continue using the previously set ontology (whether that was due to a project-wide setting of ontology or due to a graph-specific setting of ontology):

```python
from zep_cloud import EntityEdgeSourceTarget

await client.graph.set_ontology(
    user_ids=["user_1234", "user_5678"],
    graph_ids=["graph_1234", "graph_5678"],
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
    },
    {
        userIds: ["user_1234", "user_5678"],
        graphIds: ["graph_1234", "graph_5678"],
    }
);
```

```go
_, err := client.Graph.SetOntology(
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
    zep.ForUsers([]string{"user_1234", "user_5678"}),
    zep.ForGraphs([]string{"graph_1234", "graph_5678"}),
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

messages_thread1 = [
    Message(content="Take me to a lunch place", role="user", name="John Doe"),
    Message(content="How about Panera Bread, Chipotle, or Green Leaf Cafe, which are nearby?", role="assistant", name="Assistant"),
    Message(content="Do any of those have vegetarian options? I'm vegetarian", role="user", name="John Doe"),
    Message(content="Yes, Green Leaf Cafe has vegetarian options", role="assistant", name="Assistant"),
    Message(content="Let's go to Green Leaf Cafe", role="user", name="John Doe"),
    Message(content="Navigating to Green Leaf Cafe", role="assistant", name="Assistant"),
]

messages_thread2 = [
    Message(content="Play the 7 habits of highly effective people", role="user", name="John Doe"),
    Message(content="Playing the 7 habits of highly effective people", role="assistant", name="Assistant"),
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
import { v4 as uuidv4 } from "uuid";
import type { Message } from "@getzep/zep-cloud/api";

const messagesThread1: Message[] = [
    { content: "Take me to a lunch place", role: "user", name: "John Doe" },
    { content: "How about Panera Bread, Chipotle, or Green Leaf Cafe, which are nearby?", role: "assistant", name: "Assistant" },
    { content: "Do any of those have vegetarian options? I'm vegetarian", role: "user", name: "John Doe" },
    { content: "Yes, Green Leaf Cafe has vegetarian options", role: "assistant", name: "Assistant" },
    { content: "Let's go to Green Leaf Cafe", role: "user", name: "John Doe" },
    { content: "Navigating to Green Leaf Cafe", role: "assistant", name: "Assistant" },
];

const messagesThread2: Message[] = [
    { content: "Play the 7 habits of highly effective people", role: "user", name: "John Doe" },
    { content: "Playing the 7 habits of highly effective people", role: "assistant", name: "Assistant" },
];

let userId = `user-${uuidv4()}`;
await client.user.add({ userId, firstName: "John", lastName: "Doe", email: "john.doe@example.com" });

const thread1Id = `thread-${uuidv4()}`;
const thread2Id = `thread-${uuidv4()}`;
await client.thread.create({ threadId: thread1Id, userId });
await client.thread.create({ threadId: thread2Id, userId });

await client.thread.addMessages(thread1Id, { messages: messagesThread1, ignoreRoles: ["assistant"] });
await client.thread.addMessages(thread2Id, { messages: messagesThread2, ignoreRoles: ["assistant"] });
```

```go
messagesThread1 := []zep.Message{
    {Content: "Take me to a lunch place", Role: "user", Name: zep.String("John Doe")},
    {Content: "How about Panera Bread, Chipotle, or Green Leaf Cafe, which are nearby?", Role: "assistant", Name: zep.String("Assistant")},
    {Content: "Do any of those have vegetarian options? I'm vegetarian", Role: "user", Name: zep.String("John Doe")},
    {Content: "Yes, Green Leaf Cafe has vegetarian options", Role: "assistant", Name: zep.String("Assistant")},
    {Content: "Let's go to Green Leaf Cafe", Role: "user", Name: zep.String("John Doe")},
    {Content: "Navigating to Green Leaf Cafe", Role: "assistant", Name: zep.String("Assistant")},
}
messagesThread2 := []zep.Message{
    {Content: "Play the 7 habits of highly effective people", Role: "user", Name: zep.String("John Doe")},
    {Content: "Playing the 7 habits of highly effective people", Role: "assistant", Name: zep.String("Assistant")},
}
userID := "user-" + uuid.NewString()
userReq := &zep.CreateUserRequest{
    UserID:    userID,
    FirstName: zep.String("John"),
    LastName:  zep.String("Doe"),
    Email:     zep.String("john.doe@example.com"),
}
_, err := client.User.Add(ctx, userReq)
if err != nil {
    fmt.Printf("Error creating user: %v\n", err)
    return
}

thread1ID := "thread-" + uuid.NewString()
thread2ID := "thread-" + uuid.NewString()

thread1Req := &zep.CreateThreadRequest{
    ThreadID: thread1ID,
    UserID:    userID,
}
thread2Req := &zep.CreateThreadRequest{
    ThreadID: thread2ID,
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

msgPtrs1 := make([]*zep.Message, len(messagesThread1))
for i := range messagesThread1 {
    msgPtrs1[i] = &messagesThread1[i]
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

msgPtrs2 := make([]*zep.Message, len(messagesThread2))
for i := range messagesThread2 {
    msgPtrs2[i] = &messagesThread2[i]
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

### Retrieving Custom Types

Once you've created custom entity and edge types in your graph, you'll typically want to retrieve information filtered by these specific types. There are two primary approaches:

1. **Context Templates** (Recommended): Use context templates to create reusable context blocks that automatically filter by type. This is ideal when you want consistent formatting with automatic relevance detection.
2. **Graph Search**: Use direct graph search for more granular control over queries and when you need dynamic, query-based retrieval.

#### Using Context Templates

Context templates provide a convenient way to create context blocks that filter by your custom entity and edge types. You define a template once and Zep automatically retrieves and formats the relevant information.

Context templates support the `types` parameter on `%{entities}` and `%{edges}` variables to filter by your custom types:

```python
# Create a template that filters by custom types
client.context.create_context_template(
    template_id="restaurant-audiobook-context",
    template="""# USER PREFERENCES
%{edges types=[DIETARY_PREFERENCE] limit=5}

# RESTAURANT VISITS
%{edges types=[RESTAURANT_VISIT] limit=10}

# RESTAURANTS
%{entities types=[Restaurant] limit=5}

# AUDIOBOOKS
%{entities types=[Audiobook] limit=5}

# AUDIOBOOK LISTENING HISTORY
%{edges types=[AUDIOBOOK_LISTEN] limit=10}"""
)

# Use the template to get context
results = client.thread.get_user_context(
    thread_id=thread1_id,
    template_id="restaurant-audiobook-context"
)
print(results.context)
```

```typescript
// Create a template that filters by custom types
await client.context.createContextTemplate({
    templateId: "restaurant-audiobook-context",
    template: `# USER PREFERENCES
%{edges types=[DIETARY_PREFERENCE] limit=5}

# RESTAURANT VISITS
%{edges types=[RESTAURANT_VISIT] limit=10}

# RESTAURANTS
%{entities types=[Restaurant] limit=5}

# AUDIOBOOKS
%{entities types=[Audiobook] limit=5}

# AUDIOBOOK LISTENING HISTORY
%{edges types=[AUDIOBOOK_LISTEN] limit=10}`
});

// Use the template to get context
const results = await client.thread.getUserContext(thread1Id, {
    templateId: "restaurant-audiobook-context"
});
console.log(results.context);
```

```go
// Create a template that filters by custom types
_, err = client.Context.CreateContextTemplate(
    ctx,
    &zep.CreateContextTemplateRequest{
        TemplateID: "restaurant-audiobook-context",
        Template: `# USER PREFERENCES
%{edges types=[DIETARY_PREFERENCE] limit=5}

# RESTAURANT VISITS
%{edges types=[RESTAURANT_VISIT] limit=10}

# RESTAURANTS
%{entities types=[Restaurant] limit=5}

# AUDIOBOOKS
%{entities types=[Audiobook] limit=5}

# AUDIOBOOK LISTENING HISTORY
%{edges types=[AUDIOBOOK_LISTEN] limit=10}`,
    },
)
if err != nil {
    fmt.Printf("Error creating context template: %v\n", err)
    return
}

// Use the template to get context
templateID := "restaurant-audiobook-context"
results, err := client.Thread.GetUserContext(
    ctx,
    thread1ID,
    &zep.ThreadGetUserContextRequest{
        TemplateID: &templateID,
    },
)
if err != nil {
    fmt.Printf("Error getting user context: %v\n", err)
    return
}
fmt.Println(results.Context)
```

The resulting context block will automatically include only the specified types, formatted according to your template:

```text
# USER PREFERENCES
- (2024-11-20 10:30:00 - present) [DIETARY_PREFERENCE] <User states 'I'm vegetarian' indicating a dietary preference> { preference_type: vegetarian, allergy: false }

# RESTAURANT VISITS
- (2024-11-20 10:35:00 - present) [RESTAURANT_VISIT] <User John Doe is going to Green Leaf Cafe> { restaurant_name: Green Leaf Cafe }

# RESTAURANTS
- { name: Green Leaf Cafe, types: [Entity,Restaurant], summary: Green Leaf Cafe is a restaurant that offers vegetarian options, attributes: { dietary_accommodation: vegetarian } }

# AUDIOBOOKS
- { name: 7 habits of highly effective people, types: [Entity,Audiobook], summary: '7 habits of highly effective people' is an audiobook }

# AUDIOBOOK LISTENING HISTORY
- (2024-11-20 10:40:00 - present) [AUDIOBOOK_LISTEN] <John Doe requested to play the audiobook '7 habits of highly effective people'> { audiobook_title: 7 habits of highly effective people }
```

Learn more about context templates in the [Context Templates](/context-templates) documentation.

#### Using Graph Search

For more granular control or dynamic queries, you can use graph search to filter by entity or edge types. Graph search is ideal when you need to specify custom search queries, want direct access to individual nodes or edges, or are building dynamic filtering based on runtime conditions.

#### Searching Entities

Search for entities (nodes) by filtering on custom entity types using the `node_labels` parameter:

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
    fmt.Printf("Error searching graph: %v\n", err)
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

#### Searching Edges

Search for edges by filtering on custom edge types using the `edge_types` parameter:

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
    fmt.Printf("Error searching graph: %v\n", err)
    return
}
if len(searchResultsVisits.Edges) > 0 {
    edge := searchResultsVisits.Edges[0]
    var visit RestaurantVisit
    err := zep.UnmarshalEdgeAttributes(edge.Attributes, &visit)
    if err != nil {
        fmt.Printf("Error converting edge to RestaurantVisit struct: %v\n", err)
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

You can provide multiple types in search filters, and the types will be ORed together. For example, searching with `edge_types: ["DIETARY_PREFERENCE", "RESTAURANT_VISIT"]` will return edges matching either type.

### Important Notes/Tips

Some notes regarding custom entity and edge types:

* The `set_ontology` method overwrites any previously defined custom entity and edge types, so the set of custom entity and edge types is always the list of types provided in the last `set_ontology` method call
* The overall set of entity and edge types for a project includes both the custom entity and edge types you set and the default entity and edge types
* You can overwrite the default entity and edge types by providing custom types with the same names
* Changing the custom entity or edge types will not update previously created nodes or edges. The classification and attributes of existing nodes and edges will stay the same. The only thing that can change existing classifications or attributes is adding data that provides new information.
* When creating custom entity or edge types, avoid using the following attribute names (including in Go struct tags), as they conflict with default attributes: `uuid`, `name`, `graph_id`, `name_embedding`, `summary`, and `created_at`
* **Any custom entity or edge is required to have at least one custom property defined**
* **Tip**: Design custom entity types to represent entities/nouns, and design custom edge types to represent relationships/verbs. Otherwise, your type might be represented in the graph as an edge more often than as a node or vice versa.
* **Tip**: Avoid defining entity or edge types whose definitions overlap — aim for types that are mutually exclusive, so any given fact has one clear home. During ingestion each fact is classified into a single best-fit type based on the type's description (and, for edge types, its source and target entity types), not its name, so give each type a clear, distinct description. Overlapping or ambiguous descriptions lead to inconsistent classification, because there is no single correct type for the fact. Overlapping names are fine as long as the descriptions make the types genuinely distinct.
* **Tip**: If you have overlapping entity or edge types (e.g. 'Hobby' and 'Hiking'), you can prioritize one type over another by mentioning which to prioritize in the entity or edge type descriptions

## Strict Ontology

By default, Zep's extraction favors recall over strict adherence to your schema: entities and edges that don't clearly match one of your configured types are still added to the graph, classified generically rather than dropped. Set `strict_ontology: true` when you need the graph to conform strictly to your declared types — for example, when downstream code or [context templates](/context-templates) assume every node and edge belongs to a type you defined.

### What Strict Ontology Does

When `strict_ontology` is `true` on an add-data request:

* **Entities** are limited to your configured entity types — the [custom entity types](#custom-entity-and-edge-types) you've defined, plus Zep's default entity types unless you've also [disabled the default ontology](#disabling-default-ontology). The generic `Entity` type is excluded from the candidates, so an entity that doesn't match a declared type is dropped instead of added as an untyped node.
* **Edges** are limited to your configured edge types, when you have any configured. An edge type the extraction proposes that isn't in your configured set is discarded instead of saved under a derived relation name.

### Setting Strict Ontology

The `strict_ontology` field defaults to `false`. Set it to `true` on [`graph.add`](/adding-business-data), on [`thread.add_messages`](/adding-messages), on [`batch.create`](/adding-batch-data), or on the deprecated `/memory` endpoint:

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

new_episode = client.graph.add(
    user_id="user123",
    type="text",
    data="The user is an avid fan of Eric Clapton",
    strict_ontology=True,
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const newEpisode = await client.graph.add({
    userId: "user123",
    type: "text",
    data: "The user is an avid fan of Eric Clapton",
    strictOntology: true,
});
```

```go Go
import (
    "context"
    "log"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

userID := "user123"

newEpisode, err := client.Graph.Add(context.TODO(), &zep.AddDataRequest{
    UserID:         &userID,
    Type:           zep.GraphDataTypeText,
    Data:           "The user is an avid fan of Eric Clapton",
    StrictOntology: zep.Bool(true),
})
if err != nil {
    log.Fatalf("Failed to add data: %v", err)
}
```

The same field is available on `thread.add_messages`:

```python Python
from zep_cloud.types import Message

message = Message(
    name="John Doe",
    role="user",
    content="I really like pop music, and I don't like metal",
)

client.thread.add_messages(
    thread_id="thread-abc",
    messages=[message],
    strict_ontology=True,
)
```

```typescript TypeScript
const messages = [{
    name: "John Doe",
    role: "user",
    content: "I really like pop music, and I don't like metal",
}];

await client.thread.addMessages("thread-abc", {
    messages: messages,
    strictOntology: true,
});
```

```go Go
import (
    "context"
    "log"

    "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey(apiKey),
)

threadID := "thread-abc"
userName := "John Doe"
messages := []*zep.Message{
    {
        Name:    &userName,
        Content: "I really like pop music, and I don't like metal",
        Role:    "user",
    },
}

_, err := client.Thread.AddMessages(
    context.TODO(),
    threadID,
    &zep.AddThreadMessagesRequest{
        Messages:       messages,
        StrictOntology: zep.Bool(true),
    },
)
if err != nil {
    log.Fatal("Error adding messages:", err)
}
```

The same field is available on `batch.create`. Processing applies it to every graph episode and every thread message in that batch:

```python Python
batch = client.batch.create(
    strict_ontology=True,
    metadata={"description": "Customer support backfill"},
)
```

```typescript TypeScript
const batch = await client.batch.create({
    strictOntology: true,
    metadata: { description: "Customer support backfill" },
});
```

```go Go
batch, err := client.Batch.Create(ctx, &v3.ApidataCreateBatchRequest{
    StrictOntology: v3.Bool(true),
    Metadata: map[string]interface{}{
        "description": "Customer support backfill",
    },
})
if err != nil {
    log.Fatal("Error creating batch:", err)
}
```

Strict ontology has two edge cases worth checking before you rely on it:

* **No edge types configured**: edge enforcement only takes effect if you have edge types configured at all — custom or default. If you have none, strict ontology has no effect on edges, and edge extraction stays open-vocabulary.
* **No entity types available**: if `strict_ontology` is `true` and you have no custom entity types *and* the default ontology is disabled, there are no entity types left to match against. Extraction returns zero entities rather than an error, so this misconfiguration fails silently.

Strict ontology restricts extraction to your project's configured ontology, which includes Zep's default entity and edge types unless you've separately [disabled the default ontology](#disabling-default-ontology) — turning on `strict_ontology` doesn't disable the default ontology by itself.