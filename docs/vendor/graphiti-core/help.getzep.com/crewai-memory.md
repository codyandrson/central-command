> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# CrewAI integration

The `zep-crewai` package gives CrewAI agents persistent memory backed by Zep's temporal knowledge graph. You persist conversation turns and business data with Zep storage adapters, and give your agents Zep tools so they can retrieve relevant context when they need it. This lets agents carry context across executions, share a common knowledge base, and ground their decisions in what was learned before.

## Core benefits

* **Persistent memory** — Conversations and knowledge persist across sessions and crew runs.
* **On-demand retrieval** — Agents search Zep through tools and pull in context exactly when a task calls for it.
* **Dual storage** — User-specific memory for individuals and shared knowledge graphs for organizational data.
* **Tool integration** — Search and add-data tools let agents read from and write to Zep during execution.

## How it works

Memory in this integration is explicit and tool-driven. There are two distinct steps, and you control both.

**Persisting context.** Create a `ZepUserStorage` or `ZepGraphStorage` adapter and call `storage.save(value, metadata={"type": ...})`. The adapter routes each item by its `type`:

| Metadata type | Routes to       | Use for                         |
| ------------- | --------------- | ------------------------------- |
| `message`     | Thread API      | Conversation turns (role-based) |
| `json`        | Knowledge graph | Structured data                 |
| `text`        | Knowledge graph | Facts, preferences, free text   |

**Retrieving context.** Attach `create_search_tool` (and optionally `create_add_data_tool`) to an `Agent(tools=[...])`. The agent searches Zep when it decides the task needs it. You can also call `ZepUserStorage.get_context()` directly to fetch the prompt-ready context block that Zep auto-assembles for a thread.

**Failure isolation.** `save()` on every storage adapter logs a Zep failure and returns normally instead of raising, so a Zep outage never crashes a crew run. When you want misconfiguration to fail loudly, provision with `ensure_user` and `ensure_thread` before the crew runs — see [provisioning users and threads](#provisioning-users-and-threads).

There is no automatic retrieval or storage, and no `external_memory=` Crew wiring. CrewAI 1.x removed the `ExternalMemory(storage=...)` wrapper and the storage interface it depended on, so context is never injected behind the scenes. You decide what to save with `save(...)`, and the agent decides what to search through its tools. The package is also sync-only: its adapters are built on the synchronous `Zep` client.

## Installation

```bash
pip install zep-crewai
```

Requires Python 3.11+, `zep-crewai>=1.2.0`, `crewai>=1.0.0`, `zep-cloud>=3.23.0`, and `pydantic>=2.0.0`, plus a Zep Cloud API key. Get your API key from [app.getzep.com](https://app.getzep.com).

Set your API key in the environment:

```bash
export ZEP_API_KEY="your-zep-api-key"
```

#### Upgrading from zep-crewai 1.1.x

Three changes affect existing code:

* The compound `all` search scope is removed — use `auto` to let Zep pick a scope.
* `save()` logs Zep failures instead of raising them.
* `search()` wraps its context string in a `<ZEP_CONTEXT>` template; pass `context_template="{context}"` for the raw block.

See the [package changelog](https://github.com/getzep/zep/blob/main/integrations/crewai/python/CHANGELOG.md) for the full list of changes.

## Provisioning users and threads

`ensure_user` and `ensure_thread` are idempotent create-then-catch-conflict helpers. Both return `True` when the resource is newly created and `False` when it already exists; genuine failures (auth, network, 5xx) raise. Call them before a crew runs so misconfiguration surfaces loudly rather than being swallowed by `save()`.

The optional `on_created` hook fires exactly once, only when the user is genuinely new — use it for one-time per-user setup such as ontology configuration.

```python Python
from zep_crewai import ensure_user, ensure_thread

def setup_new_user(client, user_id):
    client.graph.set_ontology(...)  # one-time per-user configuration

ensure_user(zep_client, user_id="alice_123", first_name="Alice", on_created=setup_new_user)
ensure_thread(zep_client, thread_id="project_456", user_id="alice_123")
```

`ZepUserStorage` and `ZepStorage` also provision lazily on the first `save()` or `search()` call; pass `first_name`, `last_name`, `email`, or `on_created` to their constructors to feed that path. The lazy path never raises — a provisioning failure is logged and the call becomes a no-op — so prefer the explicit helpers when you want failures to surface. `ZepGraphStorage` is scoped to a standalone graph rather than a Zep user, so it has no lazy provisioning; passing it `on_created` raises `TypeError`.

## Storage types

### User storage

Use `ZepUserStorage` for an individual user's conversation history and personal context. A `thread_id` is required and ties message storage to a conversation thread. CrewAI has no automatic persistence loop; sharing one thread across multiple agents is safe when your code or tools write each turn once.

```python Python
import os
from zep_cloud.client import Zep
from zep_crewai import ZepUserStorage, create_search_tool, ensure_user, ensure_thread
from crewai import Agent

zep_client = Zep(api_key=os.getenv("ZEP_API_KEY"))

# Provision the user and thread (idempotent; genuine failures raise)
ensure_user(zep_client, user_id="alice_123", first_name="Alice")
ensure_thread(zep_client, thread_id="project_456", user_id="alice_123")

# Create user storage
user_storage = ZepUserStorage(
    client=zep_client,
    user_id="alice_123",
    thread_id="project_456",
)

# Persist a conversation turn (routes to the thread)
user_storage.save(
    "How can I help you today?",
    metadata={"type": "message", "role": "assistant", "name": "Helper"},
)

# Persist a preference as graph data
user_storage.save(
    "Alice prefers morning meetings",
    metadata={"type": "text"},
)

# Give an agent a Zep search tool so it can retrieve this context on demand
assistant = Agent(
    role="Personal Assistant",
    goal="Help Alice using what you know about her",
    backstory="You know Alice's preferences and conversation history.",
    tools=[create_search_tool(zep_client, user_id="alice_123")],
)
```

To fetch the auto-assembled context block for the thread directly, call `get_context()`:

```python Python
# Returns a prompt-ready context block string (or None if empty)
context = user_storage.get_context()
print(context)
```

### Graph storage

Use `ZepGraphStorage` for shared organizational knowledge that multiple agents can read and write.

```python Python
from zep_cloud import SearchFilters
from zep_crewai import ZepGraphStorage, create_search_tool
from crewai import Agent

# Create the graph
zep_client.graph.create(
    graph_id="company_knowledge",
    name="Company Knowledge Graph",
    description="Shared organizational knowledge and insights.",
)

# Create graph storage for shared knowledge
graph_storage = ZepGraphStorage(
    client=zep_client,
    graph_id="company_knowledge",
    search_filters=SearchFilters(node_labels=["Technology", "Project"]),
)

# Persist knowledge
graph_storage.save(
    "Project Alpha uses Python and React",
    metadata={"type": "text"},
)

# Let agents search it through a tool
knowledge_agent = Agent(
    role="Knowledge Assistant",
    goal="Answer questions from the shared knowledge graph",
    backstory="You maintain and search the team's shared knowledge.",
    tools=[create_search_tool(zep_client, graph_id="company_knowledge")],
)
```

You can also search a graph directly. `search` returns a list whose entries include a composed context string wrapped in the storage's context template — a `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block by default:

```python Python
results = graph_storage.search("project status", limit=5)
for item in results:
    print(item.get("context", ""))  # <ZEP_CONTEXT> ... </ZEP_CONTEXT>
```

Pass `context_template="{context}"` to the storage constructor to get the bare context string instead.

## Customizing retrieved context

Both storage classes wrap the context string returned from `search()` in a template. `context_template` must contain a literal `{context}` placeholder and is rendered with plain string replacement (never `str.format`), so context containing `{`, `}`, or `%` is always safe. The default is the `DEFAULT_CONTEXT_TEMPLATE` export — the `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block shared across Zep integrations.

`ZepUserStorage` also accepts a `context_builder`: a synchronous callable that replaces the default graph composition in `search()` with your own retrieval logic. The builder receives a frozen `ContextInput` (`zep`, `user_id`, `thread_id`, `user_message`) and returns the context string, or `None` for no results. A builder exception is logged and degrades to empty results.

```python Python
from zep_crewai import ZepUserStorage, ContextInput

def my_builder(ctx: ContextInput) -> str | None:
    results = ctx.zep.graph.search(user_id=ctx.user_id, query=ctx.user_message, scope="edges")
    if not results.edges:
        return None
    return "\n".join(edge.fact for edge in results.edges)

storage = ZepUserStorage(
    client=zep_client,
    user_id="alice_123",
    thread_id="project_456",
    context_builder=my_builder,
)
```

## Tool integration

Tools are the supported extension point for exposing Zep to CrewAI agents. Bind a tool to a single user or a single graph at creation time, then add it to an agent's `tools` list.

```python Python
from zep_cloud import SearchFilters
from zep_crewai import create_search_tool, create_add_data_tool
from crewai import Agent

# Tools bound to user storage
user_search_tool = create_search_tool(zep_client, user_id="alice_123")
user_add_tool = create_add_data_tool(zep_client, user_id="alice_123")

# Tools bound to graph storage
graph_search_tool = create_search_tool(zep_client, graph_id="knowledge_base")
graph_add_tool = create_add_data_tool(zep_client, graph_id="knowledge_base")

curator = Agent(
    role="Knowledge Curator",
    goal="Search existing knowledge and record new findings",
    backstory="You maintain the organization's knowledge base.",
    tools=[graph_search_tool, graph_add_tool],
    llm="gpt-5-mini",
)
```

`create_search_tool` and `create_add_data_tool` return `ZepSearchTool` and `ZepAddDataTool` instances; both classes are also exported if you prefer to construct them directly. A Zep failure inside either tool returns an error string to the agent — the tool never raises into the crew.

### Search tool parameters

The search tool's `args_schema` exposes every `graph.search` parameter to the model by default:

| Parameter          | Values                                                                   | Default            |
| ------------------ | ------------------------------------------------------------------------ | ------------------ |
| `query`            | Natural language search query (required; truncated to 400 characters)    | —                  |
| `scope`            | `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto` | `edges`            |
| `reranker`         | `rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder`       | `rrf`              |
| `limit`            | Maximum results                                                          | `10`               |
| `mmr_lambda`       | Diversity/relevance balance for the `mmr` reranker                       | omitted when unset |
| `center_node_uuid` | Center node for `node_distance` reranking                                | omitted when unset |

Use `pinned_params` to fix a parameter to a constant and remove it from the model-facing schema, or `hidden_params` to remove it from the schema without pinning it (Zep's server-side default applies). The legacy `scope`, `reranker`, and `limit` keyword arguments each pin and hide their parameter, equivalent to putting them in `pinned_params`. `search_filters` and `bfs_origin_node_uuids` are constructor-only and never exposed to the model.

```python Python
# Pin scope and limit (hidden from the model, always sent); hide reranker entirely
search_tool = create_search_tool(
    zep_client,
    user_id="alice_123",
    pinned_params={"scope": "edges", "limit": 5},
    hidden_params={"reranker"},
)

# Constructor-only parameters are never exposed to the model
search_tool = create_search_tool(
    zep_client,
    graph_id="knowledge_base",
    search_filters=SearchFilters(node_labels=["Project"]),
    bfs_origin_node_uuids=["node-uuid-1"],
)
```

The tool returns results to the agent as plain `- fact` lines, one per result.

### Add-data tool parameters

* `data` — Content to store; payloads over Zep's `graph.add` ceiling are truncated to 9,900 characters instead of failing.
* `data_type` — Explicit type: `text` (default), `json`, or `message`.

### Structured data with ontologies

Define entity models so Zep organizes graph data into typed entities. The SDK requires a docstring on each entity class to describe it.

```python Python
from pydantic import Field
from zep_cloud import SearchFilters
from zep_cloud.external_clients.ontology import EntityModel, EntityText
from zep_crewai import ZepGraphStorage

class ProjectEntity(EntityModel):
    """A project tracked in the knowledge graph."""

    status: EntityText = Field(description="project status")
    priority: EntityText = Field(description="priority level")
    team_size: EntityText = Field(description="team size")

# Apply the ontology to one or more graphs
zep_client.graph.set_ontology(
    graph_ids=["projects"],
    entities={"Project": ProjectEntity},
    edges={},
)

# Use the graph with filtered search and context limits
graph_storage = ZepGraphStorage(
    client=zep_client,
    graph_id="projects",
    search_filters=SearchFilters(node_labels=["Project"]),
    facts_limit=20,
    entity_limit=5,
)
```

## Configuration options

### ZepUserStorage parameters

| Parameter                            | Description                                                                 |
| ------------------------------------ | --------------------------------------------------------------------------- |
| `client`                             | Zep client instance (required)                                              |
| `user_id`                            | User identifier (required)                                                  |
| `thread_id`                          | Thread identifier (required); ties message storage to a conversation thread |
| `search_filters`                     | Filter search results by node labels or attributes                          |
| `facts_limit`                        | Maximum facts (edges) for context (default: `20`)                           |
| `entity_limit`                       | Maximum entities (nodes) for context (default: `5`)                         |
| `first_name` / `last_name` / `email` | Optional identity fields applied during lazy provisioning                   |
| `on_created`                         | Hook fired once when the Zep user is newly created on the lazy path         |
| `context_builder`                    | Sync callable replacing the default `search()` composition                  |
| `context_template`                   | Template wrapping `search()` context (default: `DEFAULT_CONTEXT_TEMPLATE`)  |
| `mode`                               | Deprecated and ignored                                                      |

### ZepGraphStorage parameters

| Parameter          | Description                                                                    |
| ------------------ | ------------------------------------------------------------------------------ |
| `client`           | Zep client instance (required)                                                 |
| `graph_id`         | Graph identifier (required)                                                    |
| `search_filters`   | Filter by node labels, for example `SearchFilters(node_labels=["Technology"])` |
| `facts_limit`      | Maximum facts (edges) for context (default: `20`)                              |
| `entity_limit`     | Maximum entities (nodes) for context (default: `5`)                            |
| `context_template` | Template wrapping `search()` context (default: `DEFAULT_CONTEXT_TEMPLATE`)     |

`ZepGraphStorage` has no `on_created` parameter — it is graph-scoped, with no Zep user to provision. Passing `on_created` raises `TypeError`.

### ZepStorage parameters

`ZepStorage` is a standalone user-and-thread adapter that preserves the historical `save(value, metadata)` / `search(query, limit, score_threshold)` / `reset()` contract for existing callers. It takes `client`, `user_id`, and `thread_id` (all required) plus the same lazy-provisioning fields as `ZepUserStorage` (`first_name`, `last_name`, `email`, `on_created`). New code should prefer `ZepUserStorage`.

### Size limits

* Zep rejects direct thread-message payloads over 4,096 characters; the CrewAI storage paths truncate message content to 4,000 characters before `thread.add_messages`, logging lengths only and never content.
* Zep rejects direct `graph.add` payloads over 10,000 characters; CrewAI storage paths and `ZepAddDataTool` truncate graph payloads to 9,900 characters before calling `graph.add`.
* Search queries are truncated to 400 characters (Zep's query limit).

## Complete example

This example mirrors the `simple_example.py` from the integration repository. It persists conversation turns and business data to a user's memory, then runs an agent that searches that memory through a Zep tool before answering.

```python Python
import os
import sys
import time
import uuid

from crewai import Agent, Crew, Process, Task
from zep_cloud.client import Zep

from zep_crewai import ZepUserStorage, create_search_tool, ensure_thread, ensure_user


def main():
    api_key = os.environ.get("ZEP_API_KEY")
    if not api_key:
        print("Error: set your ZEP_API_KEY environment variable")
        print("Get your API key from: https://app.getzep.com")
        sys.exit(1)

    zep_client = Zep(api_key=api_key)

    # Set up a unique user and thread
    user_id = "demo_user_" + str(uuid.uuid4())
    thread_id = "demo_thread_" + str(uuid.uuid4())

    ensure_user(
        zep_client,
        user_id=user_id,
        first_name="John",
        last_name="Doe",
        email="john.doe@example.com",
    )
    ensure_thread(zep_client, thread_id=thread_id, user_id=user_id)

    # Initialize the Zep storage adapter
    user_storage = ZepUserStorage(client=zep_client, user_id=user_id, thread_id=thread_id)

    # Persist context with metadata-based routing
    # JSON data routes to the graph
    user_storage.save(
        '{"trip_type": "business", "destination": "New York", "duration": "3 days", '
        '"budget": 2000, "accommodation_preference": "mid-range hotels"}',
        metadata={"type": "json"},
    )

    # Messages route to the thread
    user_storage.save(
        "Hi, I need help planning a business trip to New York. I'll be there for 3 "
        "days and prefer mid-range hotels.",
        metadata={"type": "message", "role": "user", "name": "John Doe"},
    )
    user_storage.save(
        "I'd be happy to help you plan your New York business trip!",
        metadata={"type": "message", "role": "assistant", "name": "Travel Planning Assistant"},
    )

    # Text data routes to the graph
    user_storage.save(
        "John Doe prefers mid-range hotels with business amenities, enjoys local "
        "cuisine, and values convenient locations near business districts.",
        metadata={"type": "text"},
    )
    user_storage.save(
        "John Doe's budget constraint: around $2000 total for the trip including "
        "flights and accommodation. Looking for good value rather than luxury.",
        metadata={"type": "text"},
    )

    # Allow time for indexing before the agent searches
    time.sleep(20)

    # Give the agent a Zep search tool bound to this user
    search_tool = create_search_tool(zep_client, user_id=user_id)

    travel_agent = Agent(
        role="Travel Planning Assistant",
        goal="Help plan business trips efficiently and within budget",
        backstory="""You are an experienced travel planner who specializes in business
        trips. You always consider the user's preferences, budget, and trip context.
        Use the Zep memory search tool to recall what you know about the user before
        answering.""",
        tools=[search_tool],
        verbose=True,
        llm="gpt-4.1-mini",
    )

    planning_task = Task(
        description="""First, search Zep memory for the user's saved preferences and
        trip context. Then provide 3 specific hotel recommendations in New York that
        would be good for a business traveler. Include hotel names and locations, price
        range per night, why each fits the user's preferences, and any business
        amenities.""",
        expected_output="A list of 3 hotel recommendations with detailed explanations",
        agent=travel_agent,
    )

    crew = Crew(
        agents=[travel_agent],
        tasks=[planning_task],
        process=Process.sequential,
        verbose=True,
    )

    result = crew.kickoff()
    print(result)

    # Optionally persist the result for future runs
    user_storage.save(str(result), metadata={"type": "message", "role": "assistant"})


if __name__ == "__main__":
    main()
```

## Best practices

### Storage selection

* **Use `ZepUserStorage`** for personal preferences, conversation history, and user-specific context.
* **Use `ZepGraphStorage`** for shared knowledge, organizational data, and collaborative information.

### Memory management

* **Set up ontologies** for structured graph data, and give every entity class a docstring.
* **Use search filters** to target specific node types and improve relevance.
* **Combine storage types** for comprehensive memory coverage.

### Tool usage

* **Bind tools** to a specific user or graph at creation time.
* **Pin or hide search parameters** the model should not control with `pinned_params` and `hidden_params`.
* **Save data with the right `type`** (`message`, `json`, or `text`) so it routes correctly.
* **Allow time for indexing** — Zep extracts knowledge asynchronously, so facts from a turn are not instantly searchable.

## Next steps

* Explore [customizing graph structure](/customizing-graph-structure) for advanced knowledge organization
* Learn about [searching the graph](/searching-the-graph) and how to tune search
* See [code examples](https://github.com/getzep/zep/tree/main/integrations/crewai/python/examples) for additional patterns