> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# AG2 integration

[AG2](https://ag2.ai) agents using Zep maintain context across conversations and access a temporal knowledge graph built from prior turns. The `zep-ag2` package wires a fully automatic memory loop onto AG2's hook system, injects relevant context into an agent's system message, and exposes Zep search and data tools that AG2 calls during a conversation.

## Core benefits

* **Automatic memory loop**: `attach_to_agent` persists every message an agent receives and sends, and refreshes its system message with relevant context — no per-turn memory calls
* **Persistent memory**: Conversations and extracted knowledge persist across sessions
* **System message injection**: Relevant context is added to an agent's system message before it responds
* **Knowledge graph access**: Search and write to Zep's temporal knowledge graph from AG2 agents
* **Tool-based access**: Register Zep search and add operations as AG2 tools the agent invokes on demand

## How it works

AG2 has no native memory interface, so the integration provides three ways to give an agent memory:

* **Automatic memory loop** — `ZepMemoryManager.attach_to_agent(agent)` registers hooks on `ConversableAgent` that persist every message the agent receives and sends, and refresh its system message with relevant context on each turn. This is the recommended path.
* **System message injection** — `ZepMemoryManager` (conversation memory) and `ZepGraphMemoryManager` (knowledge graph) fetch a relevant context block from Zep and enrich an agent's system message when you call them.
* **Tools** — factory functions return AG2-compatible tools the model can call mid-conversation to search memory or write new data. Tools execute synchronously (AG2's execution model) while bridging to the async Zep SDK internally, so you pass an `AsyncZep` client.

The approaches combine: attach the automatic loop for consistent grounding and register tools so the agent can search or store explicitly when needed.

## Installation

```bash
pip install zep-ag2
```

Requires Python 3.11+, `ag2>=0.9.0`, `zep-cloud>=3.23.0`, and a Zep Cloud API key. Get your API key from [app.getzep.com](https://app.getzep.com).

Set up your environment variables:

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

#### Upgrading from zep-ag2 0.1.x

Two changes affect existing code. Search tools expose `scope`, `reranker`, `limit`, `mmr_lambda`, and `center_node_uuid` to the model by default — pass `pinned_params` to restore fixed values (the legacy `scope`/`limit` keyword arguments still work and pin). And the `ZepMemoryManager` configuration arguments (`first_name`, `last_name`, `email`, `on_created`, `context_builder`, `context_template`) are keyword-only. See the [changelog](https://github.com/getzep/zep/blob/main/integrations/ag2/python/CHANGELOG.md) for the full release history.

## Automatic memory loop

`attach_to_agent` gives an agent a complete memory loop in one call — you don't call `enrich_system_message` or `add_messages` on every turn:

```python Python
import os
from autogen import AssistantAgent, UserProxyAgent, LLMConfig
from zep_cloud.client import AsyncZep
from zep_ag2 import ZepMemoryManager

zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

llm_config = LLMConfig(
    {"model": "gpt-5-mini", "api_key": os.environ["OPENAI_API_KEY"]}
)

assistant = AssistantAgent(
    name="assistant",
    llm_config=llm_config,
    system_message="You are a helpful assistant with long-term memory.",
)
user_proxy = UserProxyAgent(
    name="user",
    human_input_mode="NEVER",
    code_execution_config=False,
)

manager = ZepMemoryManager(zep, user_id="user123", session_id="session456")
manager.attach_to_agent(assistant)

# Every message the assistant receives is persisted and used to refresh its
# system message; every reply it sends is persisted automatically too.
user_proxy.initiate_chat(assistant, message="My name is Alice.")
```

`attach_to_agent(agent)` registers two hooks on AG2's `ConversableAgent`:

* **`process_last_received_message`** fires for every message the agent receives. It persists the message and retrieves fresh context (via `process_user_message` internally), then replaces the agent's system message with its original text plus the rendered context template. The hook returns the message content unmodified — it is a side channel, not a message transform.
* **`process_message_before_send`** fires for every message the agent sends. It persists the outgoing message as an `assistant` message and returns it unchanged.

Both hooks wrap their entire body in error handling, so a Zep outage never breaks the agent's conversation loop — on failure, the incoming hook skips the system-message update and the outgoing hook skips persistence, in both cases still returning the message unchanged.

`attach_to_agent` is optional and additive: `enrich_system_message` and `add_messages` remain available for manual control, for example to persist only some turns or inject context at a different point than "on receive".

**Attach exactly one agent per Zep thread** — normally the user-facing agent. If two agents in a conversation each attach a manager pointing at the same `session_id`, every turn is persisted twice with conflicting roles: one agent's outgoing hook stores its reply as `assistant`, and the other agent's incoming hook stores the same content again as `user`. The package does not detect or deduplicate this. If both agents need their own automatic loop, give each a manager with a distinct `session_id`.

## System message injection

For manual control, use `ZepMemoryManager` to enrich an agent's system message with relevant conversation context before it responds:

```python Python
import asyncio
import os
from autogen import AssistantAgent, UserProxyAgent, LLMConfig
from zep_cloud.client import AsyncZep
from zep_ag2 import ZepMemoryManager, ensure_user, ensure_thread, register_all_tools

async def main():
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user_id = "user123"
    session_id = "session456"

    # Optional: provision out-of-band so failures surface before the first turn
    await ensure_user(zep, user_id=user_id, first_name="Jane")
    await ensure_thread(zep, thread_id=session_id, user_id=user_id)

    llm_config = LLMConfig(
        {"model": "gpt-5-mini", "api_key": os.environ["OPENAI_API_KEY"]}
    )

    assistant = AssistantAgent(
        name="assistant",
        llm_config=llm_config,
        system_message="You are a helpful assistant with long-term memory.",
    )
    user_proxy = UserProxyAgent(
        name="user",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
    )

    # Enrich the agent's system message with relevant memory
    memory_mgr = ZepMemoryManager(zep, user_id=user_id, session_id=session_id)
    await memory_mgr.enrich_system_message(assistant, query="conversation topic")

    # Register Zep memory tools — AG2 calls them automatically
    register_all_tools(assistant, user_proxy, zep, user_id=user_id, session_id=session_id)

    user_proxy.initiate_chat(assistant, message="What do you remember about me?")

asyncio.run(main())
```

`ZepMemoryManager` also exposes `process_user_message()` to persist a user turn and retrieve context in one call, `get_memory_context()` to retrieve the formatted context string directly, `add_messages()` to persist conversation turns, and `get_session_facts()` to read the thread's context block.

## Provisioning

The manager creates the Zep user and (when a `session_id` is set) thread lazily on the first memory-path call — `process_user_message`, `get_memory_context`, `enrich_system_message`, `add_messages`, or the `attach_to_agent` hooks. Creation is idempotent and cached per manager instance, so no pre-creation step is required.

Pass `first_name`, `last_name`, and `email` so Zep can anchor the user's identity node in the graph, and `on_created` to run one-time setup (ontology, custom instructions) only when the user is newly created:

```python
async def setup_new_user(zep, user_id: str) -> None:
    ...  # one-time setup: ontology, custom instructions

manager = ZepMemoryManager(
    zep,
    user_id="user123",
    session_id="session456",
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",
    on_created=setup_new_user,
)
```

The lazy path never raises into a memory-path method: a genuine provisioning failure (or an `on_created` hook failure) is logged and swallowed. To surface provisioning failures loudly — for example during account onboarding, before the first turn — call `ensure_user` and `ensure_thread` out-of-band:

```python
from zep_ag2 import ensure_user, ensure_thread

await ensure_user(zep, user_id="user123", first_name="Jane", on_created=setup_new_user)
await ensure_thread(zep, thread_id="session456", user_id="user123")
```

Both helpers are idempotent and return `True` only when the resource is newly created.

A `ZepMemoryManager` is scoped to one `(user_id, session_id)` pair for the lifetime of the instance — create one manager per user/thread rather than sharing an instance across users.

## Tool integration

Register Zep operations as AG2 tools so the agent can search memory or write new data during a conversation. `register_all_tools` wires up the full set in one call, or use the individual factories for finer control:

```python Python
from zep_ag2 import create_search_graph_tool, create_add_graph_data_tool

# Create tools bound to a user's knowledge graph
search_tool = create_search_graph_tool(zep, user_id="user123")
add_tool = create_add_graph_data_tool(zep, user_id="user123")

# Register with AG2's decorator pattern
assistant.register_for_llm(description="Search knowledge graph")(search_tool)
user_proxy.register_for_execution()(search_tool)

assistant.register_for_llm(description="Add to knowledge graph")(add_tool)
user_proxy.register_for_execution()(add_tool)
```

**Available tool factories:**

* `create_search_memory_tool(client, user_id, session_id=None, *, pinned_params=None, hidden_params=None, search_filters=None, bfs_origin_node_uuids=None, scope=None, limit=None)` — searches the user's graph
* `create_add_memory_tool(client, user_id, session_id=None)` — routes to the thread when a `session_id` is set, otherwise writes to the user's graph
* `create_search_graph_tool(client, user_id=None, graph_id=None, *, pinned_params=None, hidden_params=None, search_filters=None, bfs_origin_node_uuids=None, scope=None, limit=None)` — search the knowledge graph
* `create_add_graph_data_tool(client, user_id=None, graph_id=None)` — add data to the knowledge graph
* `register_all_tools(agent, executor, client, user_id, ...)` — register all tools at once

Graph tools are bound to either a `user_id` (the user's personal graph) or a `graph_id` (a shared standalone graph), not both.

### Search tool parameters

The search tool factories follow a **pin-or-expose** pattern: every `graph.search` parameter is exposed to the model by default, each with a typed schema and documented default. Letting the model choose the scope and reranker per query produces better retrieval than a single fixed configuration; pin parameters when you need deterministic behavior instead.

| Parameter          | Default   | Description                                                                     |
| ------------------ | --------- | ------------------------------------------------------------------------------- |
| `scope`            | `"edges"` | One of `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto` |
| `reranker`         | `"rrf"`   | One of `rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder`       |
| `limit`            | `10`      | Maximum number of results (capped at 50)                                        |
| `mmr_lambda`       | `None`    | Diversity (0.0) vs. relevance (1.0) balance; only used when `reranker="mmr"`    |
| `center_node_uuid` | `None`    | Center node for `reranker="node_distance"`                                      |

Use `pinned_params` to fix a parameter to a constant (hidden from the model), or `hidden_params` to remove it from the schema without pinning (Zep's server-side default applies):

```python Python
# Model chooses scope/reranker/limit/mmr_lambda/center_node_uuid freely (default)
tool = create_search_graph_tool(zep, user_id="user123")

# Pin scope to "nodes" and limit to 5 — hidden from the model, always sent as given
tool = create_search_graph_tool(
    zep, user_id="user123", pinned_params={"scope": "nodes", "limit": 5}
)

# Hide reranker entirely — omitted from the schema and the SDK call
tool = create_search_graph_tool(zep, user_id="user123", hidden_params={"reranker"})
```

The legacy `scope` and `limit` keyword arguments pin (and hide) the corresponding parameter — equivalent to passing them via `pinned_params`. `search_filters` and `bfs_origin_node_uuids` are constructor-only and never exposed to the model.

These parameters describe the model-facing tool schema. `ZepGraphMemoryManager.search()` is a separate programmatic method with its own signature and a three-value `scope` — see [Knowledge graph memory](#knowledge-graph-memory).

## Knowledge graph memory

Use `ZepGraphMemoryManager` to work with a shared knowledge graph that multiple agents can read and write:

```python Python
from zep_ag2 import ZepGraphMemoryManager

graph_mgr = ZepGraphMemoryManager(zep, graph_id="company_knowledge")

# Add data to the graph
await graph_mgr.add_data("Project Alpha uses Python and React.", data_type="text")

# Search the graph
results = await graph_mgr.search("What technologies does Project Alpha use?", limit=5, scope="edges")

# Inject graph context into an agent's system message
await graph_mgr.enrich_system_message(assistant, query="Project Alpha")
```

`ZepGraphMemoryManager.search()` accepts `scope` values `edges`, `nodes`, and `episodes` and returns structured result dicts for programmatic use. This is distinct from the search tool schema above, which exposes six scopes to the model and returns formatted strings.

## Custom context retrieval

By default, context is retrieved via `thread.get_user_context(...)` (or, inside `process_user_message`, via `thread.add_messages(..., return_context=True)`). Pass `context_builder` to replace this with custom logic — for example a filtered graph search, or a different graph entirely:

```python Python
from zep_ag2.memory import ContextInput

async def my_builder(ctx: ContextInput) -> str | None:
    results = await ctx.zep.graph.search(
        user_id=ctx.user_id,
        query=ctx.user_message,
        scope="edges",
    )
    if not results.edges:
        return None
    return "\n".join(edge.fact for edge in results.edges)

manager = ZepMemoryManager(
    zep, user_id="user123", session_id="session456", context_builder=my_builder,
)
```

The builder receives a single frozen `ContextInput`:

| Field          | Description                                                                         |
| -------------- | ----------------------------------------------------------------------------------- |
| `zep`          | The `AsyncZep` client in use by the manager                                         |
| `user_id`      | The Zep user ID the manager is scoped to                                            |
| `thread_id`    | The Zep thread ID the manager records the conversation in                           |
| `user_message` | The user message that triggered retrieval                                           |
| `agent`        | The AG2 agent in scope when invoked via the automatic loop; `None` for manual calls |

If the builder raises, a warning is logged and context injection is skipped for that call — the builder never raises into `process_user_message`, `get_memory_context`, or `enrich_system_message`. Inside `process_user_message`, persistence and the builder run concurrently with per-side isolation: a builder failure never blocks the message from being persisted, and a persistence failure never prevents the builder's context from being returned.

### Customizing the injected context template

Retrieved context (from the default retrieval or a `context_builder`) is wrapped in `context_template` before injection into the agent's system message. The default `DEFAULT_CONTEXT_TEMPLATE` wraps the context in `<ZEP_CONTEXT>` tags with a short preamble. Override it with your own wording, as long as it contains a literal `{context}` placeholder:

```python
manager = ZepMemoryManager(
    zep, user_id="user123", session_id="session456",
    context_template="Relevant background:\n{context}",
)
```

The template is rendered via plain string replacement (`template.replace("{context}", ...)`), never `str.format`, so context text containing `{`, `}`, or `%` is always safe to inject.

## Query memory

You can read memory directly, outside of agent tool calls:

```python Python
# Formatted context block for the user/thread (optionally biased by a query)
context = await memory_mgr.get_memory_context(query="project status", limit=5)

# Facts extracted from the current session
facts = await memory_mgr.get_session_facts()

# Structured search over a knowledge graph
results = await graph_mgr.search("Project Alpha", limit=5, scope="edges")
```

### Search result structure

The tool factories return human-readable strings formatted for the model, with formatting that adapts to the search scope. `ZepGraphMemoryManager.search()` returns a list of structured result dicts for programmatic use; the fields depend on the scope:

| Scope                 | Fields                                                                               |
| --------------------- | ------------------------------------------------------------------------------------ |
| `edges` (facts)       | `content` (the fact), `type` (`"edge"`), `name`, `attributes`, `created_at`          |
| `nodes` (entities)    | `content` (`"name: summary"`), `type` (`"node"`), `name`, `attributes`, `created_at` |
| `episodes` (messages) | `content`, `type` (`"episode"`), `source`, `role`, `created_at`                      |

## Memory vs tools

The integration supports three complementary patterns that work together on the same agent:

| Pattern                      | How                                                 | When to use                                                                  |
| ---------------------------- | --------------------------------------------------- | ---------------------------------------------------------------------------- |
| **Automatic memory loop**    | `attach_to_agent(agent)`                            | Persist and inject on every turn with no per-turn calls — the default choice |
| **System message injection** | `enrich_system_message(...)` on either manager      | Control exactly when context is injected and which turns are persisted       |
| **Tools**                    | `create_*_tool` factories registered with the agent | Let the agent decide when to search or store during the conversation         |

Use the automatic loop for consistent baseline context and tools for explicit, on-demand lookups and writes.

## Configuration options

### ZepMemoryManager

* `ZepMemoryManager(client, user_id, session_id=None, *, first_name=None, last_name=None, email=None, on_created=None, context_builder=None, context_template=DEFAULT_CONTEXT_TEMPLATE)` — initialize with a Zep client and user identity; the configuration arguments are keyword-only
* `attach_to_agent(agent)` — register the automatic inject and persist loop
* `process_user_message(user_message, *, agent=None)` — persist a user turn and retrieve context in one call
* `ensure_user_and_thread()` — lazily provision the user and thread; returns `False` on failure, never raises
* `enrich_system_message(agent, query=None, limit=5)` — inject memory context into an agent
* `get_memory_context(query=None, limit=5)` — return the formatted context string
* `add_messages(messages)` — store messages in the Zep thread
* `get_session_facts()` — read the thread's context block

### ZepGraphMemoryManager

* `ZepGraphMemoryManager(client, graph_id)` — initialize with a graph ID
* `search(query, limit=5, scope="edges")` — search the graph (`scope`: `edges`, `nodes`, `episodes`)
* `add_data(data, data_type="text")` — add data to the graph (`data_type`: `text`, `json`, `message`)
* `enrich_system_message(agent, query=None, limit=5)` — inject graph context into an agent

### Provisioning helpers

* `ensure_user(client, *, user_id, first_name=None, last_name=None, email=None, on_created=None)` — idempotently create a Zep user; returns `True` only when newly created
* `ensure_thread(client, *, thread_id, user_id)` — idempotently create a Zep thread; returns `True` only when newly created

## Size limits

Zep rejects over-long direct SDK payloads with an HTTP 400. The AG2 integration truncates before calling Zep, logging only the before and after lengths (never the content):

* **Thread messages**: truncated to 4,000 characters, a safety margin under Zep's 4,096-character thread-message limit
* **Graph data** (`add_data`, `create_add_graph_data_tool`): truncated to 9,900 characters, a safety margin under Zep's 10,000-character `graph.add` limit

## Best practices

* **Pass an `AsyncZep` client** — tools bridge to it on a shared background event loop, so reuse a single instance
* **Attach one agent per Zep thread** — attaching two managers with the same `session_id` double-persists every turn with conflicting roles
* **Bind tools to one target** — a `user_id` for personal memory or a `graph_id` for shared knowledge, never both
* **Combine the loop and tools** — attach the loop for consistent grounding, add tools for explicit lookups and writes
* **Allow time for indexing** — Zep extracts knowledge asynchronously, so data added during a turn is not instantly searchable

## Next steps

* Explore [customizing graph structure](/customizing-graph-structure) for advanced knowledge organization
* Learn about [searching the graph](/searching-the-graph) and how to tune search
* See [code examples](https://github.com/getzep/zep/tree/main/integrations/ag2/python/examples) for additional patterns