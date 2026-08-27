> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# AutoGen integration

The `zep-autogen` package integrates Zep with [Microsoft AutoGen](https://github.com/microsoft/autogen) agents, backing them with long-term memory and a temporal knowledge graph. It provides memory classes that plug into AutoGen's native `Memory` interface for automatic context injection, plus function tools the agent can call to search and add data on demand. Choose between [user-specific conversation memory](/users) or structured [knowledge graph memory](/graph-overview).

## Core benefits

* **Native `Memory` interface**: `ZepUserMemory` and `ZepGraphMemory` implement AutoGen's `Memory` interface, so they drop straight into an agent's `memory` list
* **Automatic context injection**: Relevant memory is retrieved and prepended to the model context before each turn via `update_context()`
* **User and knowledge graphs**: Persist a user's conversation history or maintain a shared knowledge graph with custom entity models
* **On-demand function tools**: Pre-built tools let the agent explicitly search and add graph data when it chooses
* **Graceful degradation**: A Zep failure is logged but does not crash the agent run

## How it works

The integration exposes two complementary retrieval paths:

* **Memory classes** (`ZepUserMemory`, `ZepGraphMemory`) attach to an agent's `memory` list. AutoGen calls `update_context()` before each turn, and the class retrieves memory from Zep and injects it as a system message — transparent, automatic context on every interaction.
* **Function tools** (`create_search_graph_tool`, `create_add_graph_data_tool`) attach to an agent's `tools` list. The model decides when to call them, giving explicit, observable search and add operations that work with AutoGen's tool reflection.

Both approaches can be combined on the same agent: memory for consistent background context, tools for targeted lookups.

Context injection is automatic, but persistence is not: AutoGen's `Memory` protocol has no hook that fires after the model responds, so your application calls `memory.add()` explicitly — typically once per user turn and once per assistant turn. This is AutoGen's design, not a limitation of the integration.

## Installation

```bash pip
pip install zep-autogen zep-cloud autogen-core autogen-agentchat
```

```bash uv
uv add zep-autogen zep-cloud autogen-core autogen-agentchat
```

```bash poetry
poetry add zep-autogen zep-cloud autogen-core autogen-agentchat
```

Requires Python 3.11+, `zep-cloud>=3.23.0`, `autogen-agentchat>=0.7.0`, and a Zep Cloud API key. Get your API key from [app.getzep.com](https://app.getzep.com).

Set up your environment variables:

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

#### Upgrading from zep-autogen 1.1.x

Two changes affect existing code.

`ZepUserMemory` now creates the Zep user and thread lazily on first use instead of requiring pre-provisioning. If your code relied on a 404 from `update_context()` to detect an unprovisioned user, that signal is gone — call `ensure_user`/`ensure_thread` explicitly instead and check their return value.

Search tools also expose `scope`, `reranker`, `limit`, `mmr_lambda`, and `center_node_uuid` to the model by default — pass `pinned_params` (or the legacy `scope`/`limit` arguments, which pin) to restore fixed values. See the [changelog](https://github.com/getzep/zep/blob/main/integrations/autogen/python/CHANGELOG.md) for the full release history.

## Memory types

* **User memory**: Stores conversation history in [user threads](/users) with automatic context injection
* **Knowledge graph memory**: Maintains structured knowledge with [custom entity models](/customizing-graph-structure)

## User memory

`ZepUserMemory` persists messages to a user's thread and injects the context block into the agent before each turn. Set up the imports, initialize the memory, attach it to an agent, then store messages as the conversation proceeds.

### Import dependencies

```python
import os
import uuid
import asyncio
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_core.memory import MemoryContent, MemoryMimeType
from zep_cloud.client import AsyncZep
from zep_autogen import ZepUserMemory
```

### Initialize the client and memory

`ZepUserMemory` binds the client, user, and thread into a memory object that AutoGen can attach to an agent. The Zep user and thread are created lazily on first use by whichever of `add()` or `update_context()` runs first — no pre-creation step is required. Creation is idempotent and cached per instance.

```python
zep_client = AsyncZep(api_key=os.environ.get("ZEP_API_KEY"))
user_id = f"user_{uuid.uuid4().hex[:16]}"
thread_id = f"thread_{uuid.uuid4().hex[:16]}"

memory = ZepUserMemory(
    client=zep_client,
    user_id=user_id,
    thread_id=thread_id,
    first_name="Alice",
    email="alice@example.com",
)
```

| Parameter                          | Description                                                                                                                              |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `client`                           | An initialized `AsyncZep` instance (required)                                                                                            |
| `user_id`                          | Zep user ID for memory isolation (required)                                                                                              |
| `thread_id`                        | Thread identifier; generated automatically if omitted                                                                                    |
| `context_template_id`              | Zep context template used to render the retrieved context block; ignored when `context_builder` is set                                   |
| `first_name`, `last_name`, `email` | Passed to `user.add` during lazy provisioning; helps Zep anchor the user's identity node in the graph                                    |
| `on_created`                       | Async hook run exactly once, only when the user is newly created — use it for per-user ontology or custom instructions                   |
| `context_builder`                  | Async callable replacing the default context retrieval in `update_context()` — see [custom context retrieval](#custom-context-retrieval) |
| `context_template`                 | Template wrapping injected context; defaults to `DEFAULT_CONTEXT_TEMPLATE`                                                               |

The lazy path never raises into `add()` or `update_context()`: a provisioning failure (including an `on_created` failure) is logged and swallowed. To surface provisioning failures loudly — for example during account onboarding, before the first turn — call `ensure_user` and `ensure_thread` out-of-band:

```python
from zep_autogen import ensure_user, ensure_thread

await ensure_user(zep_client, user_id=user_id, first_name="Alice", email="alice@example.com")
await ensure_thread(zep_client, thread_id=thread_id, user_id=user_id)
```

Both helpers are idempotent and return `True` only when the resource is newly created.

### Attach the memory to an agent

Pass the memory in the agent's `memory` list so context is injected before each turn.

```python
# Create agent with Zep memory
agent = AssistantAgent(
    name="MemoryAwareAssistant",
    model_client=OpenAIChatCompletionClient(
        model="gpt-4.1-mini",
        api_key=os.environ.get("OPENAI_API_KEY")
    ),
    memory=[memory],
    system_message="You are a helpful assistant with persistent memory."
)
```

### Store messages and run

Persistence is manual: AutoGen never calls `memory.add()` for you, so persist each turn explicitly — once for the user message and once for the assistant reply. The agent automatically retrieves context via `update_context()` before responding; skipping the `add()` calls means the agent still sees Zep's existing context, but that turn's messages are never written to Zep and cannot be recalled later.

```python
# Helper function to store messages with proper metadata
async def add_message(message: str, role: str, name: str = None):
    """Store a message in Zep memory following AutoGen standards."""
    metadata = {"type": "message", "role": role}
    if name:
        metadata["name"] = name

    await memory.add(MemoryContent(
        content=message,
        mime_type=MemoryMimeType.TEXT,
        metadata=metadata
    ))

# Example conversation with memory persistence
user_message = "My name is Alice and I love hiking in the mountains."
print(f"User: {user_message}")

# Store user message
await add_message(user_message, "user", "Alice")

# Run agent - it will automatically retrieve context via update_context()
response = await agent.run(task=user_message)
agent_response = response.messages[-1].content
print(f"Agent: {agent_response}")

# Store agent response
await add_message(agent_response, "assistant")
```

**Automatic context injection**: `ZepUserMemory` injects relevant memory via the `update_context()` method before each turn. On the default retrieval path it injects the context block and, when one is available, also appends up to 10 recent thread messages. When a `context_builder` is set, only the builder's output is injected.

**Allow time for indexing** — Zep extracts knowledge asynchronously, so facts from a turn are not instantly searchable. Allow time for indexing before querying for newly added content.

## Custom context retrieval

By default, `update_context()` retrieves context via `thread.get_user_context(...)`. Pass `context_builder` to replace this with custom logic — for example a filtered graph search, or a different graph entirely:

```python
from zep_autogen.memory import ContextInput

async def my_builder(ctx: ContextInput) -> str | None:
    results = await ctx.zep.graph.search(
        user_id=ctx.user_id,
        query=ctx.user_message,
        scope="edges",
    )
    if not results.edges:
        return None
    return "\n".join(edge.fact for edge in results.edges)

memory = ZepUserMemory(
    client=zep_client,
    user_id=user_id,
    thread_id=thread_id,
    context_builder=my_builder,
)
```

The builder receives a single frozen `ContextInput`:

| Field           | Description                                                                    |
| --------------- | ------------------------------------------------------------------------------ |
| `zep`           | The `AsyncZep` client in use by this memory instance                           |
| `user_id`       | The Zep user ID the memory is scoped to                                        |
| `thread_id`     | The Zep thread ID the memory records the conversation in                       |
| `user_message`  | The last user-role message's text from the model context (`""` if none)        |
| `model_context` | The AutoGen `ChatCompletionContext` passed to `update_context()` for this call |

If the builder raises, a warning is logged and context injection is skipped for that turn — `update_context()` never raises. The builder is retrieval-only and never runs concurrently with message persistence: AutoGen's `Memory` protocol calls `update_context()` (injection) and `add()` (persistence) as two separate, caller-controlled steps, so persist turns explicitly via `add()`.

### Customizing the injected context template

Retrieved context (from the default retrieval or a `context_builder`) is wrapped in `context_template` before being added to the model context as a system message. The default `DEFAULT_CONTEXT_TEMPLATE` wraps the context in `<ZEP_CONTEXT>` tags with a short preamble. Override it with your own wording, as long as it contains a literal `{context}` placeholder:

```python
memory = ZepUserMemory(
    client=zep_client,
    user_id=user_id,
    thread_id=thread_id,
    context_template="Relevant background:\n{context}",
)
```

The template is rendered via plain string replacement (`template.replace("{context}", ...)`), never `str.format`, so context text containing `{`, `}`, or `%` is always safe to inject.

## Knowledge graph memory

`ZepGraphMemory` maintains a standalone knowledge graph with custom entity models. Define an ontology, create the graph, initialize the memory with search filters, add data, then attach the memory to an agent.

`ZepGraphMemory` is scoped to a standalone `graph_id`, not a Zep user, so it has no `on_created` hook and no lazy user provisioning — create the graph out-of-band via `graph.create` as shown below.

### Define entity models

Custom entity models shape how Zep extracts structured knowledge from the data you add.

```python
from zep_autogen.graph_memory import ZepGraphMemory
from zep_cloud.external_clients.ontology import EntityModel, EntityText
from pydantic import Field

# Define entity models using Pydantic
class ProgrammingLanguage(EntityModel):
    """A programming language entity."""
    paradigm: EntityText = Field(
        description="programming paradigm (e.g., object-oriented, functional)",
        default=None
    )
    use_case: EntityText = Field(
        description="primary use cases for this language",
        default=None
    )

class Framework(EntityModel):
    """A software framework or library."""
    language: EntityText = Field(
        description="the programming language this framework is built for",
        default=None
    )
    purpose: EntityText = Field(
        description="primary purpose of this framework",
        default=None
    )
```

### Set the ontology and create the graph

Register the entity models as the graph's ontology, then create the graph that will hold the extracted knowledge.

```python
from zep_cloud import SearchFilters

# Set ontology first
await zep_client.graph.set_ontology(
    entities={
        "ProgrammingLanguage": ProgrammingLanguage,
        "Framework": Framework,
    }
)

# Create graph
graph_id = f"graph_{uuid.uuid4().hex[:16]}"
try:
    await zep_client.graph.create(
        graph_id=graph_id,
        name="Programming Knowledge Graph"
    )
    print(f"Created graph: {graph_id}")
except Exception as e:
    print(f"Graph creation failed: {e}")
```

### Initialize the graph memory

Configure search filters and context limits to control what `ZepGraphMemory` injects on each turn.

```python
# Create graph memory with search configuration
graph_memory = ZepGraphMemory(
    client=zep_client,
    graph_id=graph_id,
    search_filters=SearchFilters(
        node_labels=["ProgrammingLanguage", "Framework"]
    ),
    facts_limit=20,  # Max facts in context injection (default: 20)
    entity_limit=5   # Max entities in context injection (default: 5)
)
```

### Add data and wait for indexing

Knowledge extraction is asynchronous, so allow time for indexing before the data is searchable.

```python
# Add structured knowledge
await graph_memory.add(MemoryContent(
    content="Python is excellent for data science and AI development",
    mime_type=MemoryMimeType.TEXT,
    metadata={"type": "data"}  # "data" stores in graph, "message" stores as episode
))

# Wait for graph processing (required)
print("Waiting for graph indexing...")
await asyncio.sleep(30)  # Allow time for knowledge extraction
```

### Attach the memory to an agent

Pass the graph memory in the agent's `memory` list so relevant facts and entities are injected before each turn.

```python
# Create agent with graph memory
agent = AssistantAgent(
    name="GraphMemoryAssistant",
    model_client=OpenAIChatCompletionClient(model="gpt-4.1-mini"),
    memory=[graph_memory],
    system_message="You are a technical assistant with programming knowledge."
)
```

**Graph memory context injection**: `ZepGraphMemory` automatically retrieves the last 2 episodes from the graph and uses their content to query for relevant facts (up to `facts_limit`) and entities (up to `entity_limit`). This context is injected as a system message during agent interactions.

## Tools integration

Zep tools let agents search and add data directly to memory storage with manual control and structured responses.

**Important**: Tools must be bound to either `graph_id` OR `user_id`, not both. This determines whether they operate on knowledge graphs or user graphs.

### Search tool parameters

`create_search_graph_tool` follows a **pin-or-expose** pattern: every `graph.search` parameter is exposed to the model by default, each with a typed schema and documented default. Letting the model choose the scope and reranker per query produces better retrieval than a single fixed configuration; pin parameters when you need deterministic behavior instead. `query` is always exposed and required.

| Parameter          | Default   | Description                                                                     |
| ------------------ | --------- | ------------------------------------------------------------------------------- |
| `scope`            | `"edges"` | One of `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto` |
| `reranker`         | `"rrf"`   | One of `rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder`       |
| `limit`            | `10`      | Maximum number of results                                                       |
| `mmr_lambda`       | `None`    | Diversity (0.0) vs. relevance (1.0) balance; only used when `reranker="mmr"`    |
| `center_node_uuid` | `None`    | Center node for `reranker="node_distance"`                                      |

Use `pinned_params` to fix a parameter to a constant (hidden from the model), or `hidden_params` to remove it from the schema without pinning (Zep's server-side default applies):

```python
# Model chooses scope/reranker/limit/mmr_lambda/center_node_uuid freely (default)
tool = create_search_graph_tool(zep_client, user_id=user_id)

# Pin scope to "nodes" and limit to 5 — hidden from the model, always sent as given
tool = create_search_graph_tool(
    zep_client, user_id=user_id, pinned_params={"scope": "nodes", "limit": 5}
)

# Hide mmr_lambda from the schema without pinning it — Zep's own default applies
tool = create_search_graph_tool(zep_client, user_id=user_id, hidden_params={"mmr_lambda"})
```

The legacy `scope` and `limit` arguments pin (and hide) the corresponding parameter — equivalent to passing them via `pinned_params`. `search_filters` and `bfs_origin_node_uuids` are constructor-only and never exposed to the model.

AutoGen's `FunctionTool` derives its JSON schema strictly from the wrapped function's typed signature. `create_search_graph_tool` implements pin-or-expose by building that signature dynamically: exposed parameters become real, typed parameters of the function AutoGen introspects, while pinned and hidden parameters are never part of the signature at all.

### Add tool parameters

`create_add_graph_data_tool` exposes:

* `data`: str (required) - Content to store
* `data_type`: str (optional, default "text") - Data type: "text", "json", "message"

### User graph tools

```python
from zep_autogen import create_search_graph_tool, create_add_graph_data_tool

# Create tools bound to user graph
search_tool = create_search_graph_tool(zep_client, user_id=user_id)
add_tool = create_add_graph_data_tool(zep_client, user_id=user_id)

# Agent with user graph tools
agent = AssistantAgent(
    name="UserKnowledgeAssistant",
    model_client=OpenAIChatCompletionClient(model="gpt-4.1-mini"),
    tools=[search_tool, add_tool],
    system_message="You can search and add data to the user's knowledge graph.",
    reflect_on_tool_use=True  # Enables tool usage reflection
)
```

### Knowledge graph tools

```python
# Create tools bound to knowledge graph
search_tool = create_search_graph_tool(zep_client, graph_id=graph_id)
add_tool = create_add_graph_data_tool(zep_client, graph_id=graph_id)

# Agent with knowledge graph tools
agent = AssistantAgent(
    name="KnowledgeGraphAssistant",
    model_client=OpenAIChatCompletionClient(model="gpt-4.1-mini"),
    tools=[search_tool, add_tool],
    system_message="You can search and add data to the knowledge graph.",
    reflect_on_tool_use=True
)
```

## Size limits

Zep rejects over-long direct SDK payloads with an HTTP 400. The AutoGen integration truncates before calling Zep, logging only the before and after lengths (never the content):

* **Thread messages** (`ZepUserMemory.add` with `type="message"`): truncated to 4,000 characters, a safety margin under Zep's 4,096-character thread-message limit
* **Graph data** (`ZepGraphMemory.add`, `ZepUserMemory.add` with `type="data"`, and `create_add_graph_data_tool`): truncated to 9,900 characters, a safety margin under Zep's 10,000-character `graph.add` limit

## Query memory

Both memory types support direct querying with different scope parameters.

### User memory queries

```python
# Query user conversation history
results = await memory.query("What does Alice like?", limit=5)

# Process different result types
for result in results.results:
    content = result.content
    metadata = result.metadata

    if 'edge_name' in metadata:
        # Fact/relationship result
        print(f"Fact: {content}")
        print(f"Relationship: {metadata['edge_name']}")
        print(f"Valid: {metadata.get('valid_at', 'N/A')} - {metadata.get('invalid_at', 'present')}")
    elif 'node_name' in metadata:
        # Entity result
        print(f"Entity: {metadata['node_name']}")
        print(f"Summary: {content}")
    else:
        # Episode/message result
        print(f"Message: {content}")
        print(f"Role: {metadata.get('episode_role', 'unknown')}")

    print(f"Source: {metadata.get('source')}\n")
```

### Graph memory queries

```python
# Query knowledge graph with scope control
facts_results = await graph_memory.query(
    "Python frameworks",
    limit=10,
    scope="edges"  # "edges" (facts), "nodes" (entities), "episodes" (messages)
)

print(f"Found {len(facts_results.results)} facts about Python frameworks:")
for result in facts_results.results:
    print(f"- {result.content}")

entities_results = await graph_memory.query(
    "programming languages",
    limit=5,
    scope="nodes"
)

print(f"\nFound {len(entities_results.results)} programming language entities:")
for result in entities_results.results:
    entity_name = result.metadata.get('node_name', 'Unknown')
    print(f"- {entity_name}: {result.content}")
```

### Search result structure

#### Edge results (facts)

```json
{
    "content": "fact text",
    "metadata": {
        "source": "graph" | "user_graph",
        "edge_name": "relationship_name",
        "edge_attributes": {...},
        "created_at": "timestamp",
        "valid_at": "timestamp",
        "invalid_at": "timestamp",
        "expired_at": "timestamp"
    }
}
```

#### Node results (entities)

```json
{
    "content": "entity_name:\n entity_summary",
    "metadata": {
        "source": "graph" | "user_graph",
        "node_name": "entity_name",
        "node_attributes": {...},
        "created_at": "timestamp"
    }
}
```

#### Episode results (messages)

```json
{
    "content": "episode_content",
    "metadata": {
        "source": "graph" | "user_graph",
        "episode_type": "source_type",
        "episode_role": "role_type",
        "episode_name": "role_name",
        "created_at": "timestamp"
    }
}
```

## Memory vs tools comparison

**Memory objects** (`ZepUserMemory` / `ZepGraphMemory`):

* Automatic context injection via `update_context()`; persistence stays manual via `add()`
* Attached to the agent's `memory` list
* Transparent operation — happens automatically
* Better for consistent memory across interactions

**Function tools** (search/add tools):

* Manual control — the agent decides when to use them
* More explicit and observable operations
* Better for specific search/add operations
* Works with AutoGen's tool reflection features
* Provides structured return values

**Note**: Both approaches can be combined — use memory for automatic context and tools for explicit operations.

## Best practices

* **Pick the right memory type** — use `ZepUserMemory` for per-user conversation history and `ZepGraphMemory` for a shared knowledge graph
* **Persist every turn explicitly** — call `memory.add()` once per user turn and once per assistant turn; injection is the only automatic half of the loop
* **Bind tools to exactly one scope** — a search or add tool targets either a `graph_id` or a `user_id`, never both
* **Combine memory and tools** — attach a memory class for automatic context and add function tools for targeted lookups
* **Allow time for indexing** — Zep extracts knowledge asynchronously, so facts from a turn are not instantly searchable

## Next steps

* Explore [customizing graph structure](/customizing-graph-structure) for advanced knowledge organization
* Learn about [searching the graph](/searching-the-graph) and how to tune search
* See [code examples](https://github.com/getzep/zep/tree/main/integrations/autogen/python/examples) for additional patterns