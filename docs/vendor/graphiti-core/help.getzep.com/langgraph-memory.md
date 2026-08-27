> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# LangGraph integration

[LangGraph](https://github.com/langchain-ai/langgraph) agents using Zep gain durable, cross-session memory backed by a temporal knowledge graph. The `zep-langgraph` package wires Zep into your graph nodes: it provisions the user and thread, injects the user's context block into the system prompt, persists each turn, and exposes a graph-search tool the model can call on demand.

A complete notebook example is available in the [Zep repository](https://github.com/getzep/zep/blob/main/examples/python/langgraph-agent/agent.ipynb).

## Core benefits

* **Context injection**: Fold the user's context block into the system prompt via a `prompt` callable, or guarantee it with a `pre_model_hook`
* **Per-turn persistence**: Write each conversation turn back to Zep with a single helper
* **On-demand graph search**: Expose a LangChain tool the model calls to search the knowledge graph, with pin-or-expose control over its parameters
* **Idempotent provisioning**: Create the Zep user and thread out-of-band with `ensure_user` and `ensure_thread`
* **Custom context building**: Replace the default context retrieval with your own `context_builder`
* **`BaseStore` support**: Use `ZepStore` for `create_react_agent(store=...)` and langmem's memory tools
* **Async and sync clients**: Every helper has both an async and a synchronous variant
* **Graceful degradation**: A Zep failure is logged but never crashes the host agent

## How it works

The package ships two layers. The **node and tool helpers** (the primary path) call Zep directly inside your graph nodes; this matches Zep's recommended LangGraph pattern. `ZepStore` (secondary) is a `BaseStore` implementation for callers who need one — e.g. `create_react_agent(store=...)` or langmem's memory tools.

The Zep loop is the same everywhere — create user, create thread, add messages, retrieve context — and each step is wrapped as a helper you call from a graph node:

* **`ensure_user` / `ensure_thread`** — idempotently provision the Zep user and thread before the first turn. See [provisioning users and threads](#provisioning-users-and-threads).
* **`build_system_message`** — fetches the context block (`thread.get_user_context`) assembled from the entire user graph and folds it into a `SystemMessage` with your base instructions, ready to prepend to the model's message list. `get_zep_context` returns just the raw block. Both accept a [`context_builder`](#custom-context-building) that replaces the default retrieval.
* **`create_zep_pre_model_hook`** — builds a `pre_model_hook` for `create_react_agent` that injects context on every model call without relying on a `prompt` callable. See [guaranteed context injection](#guaranteed-context-injection-with-a-pre-model-hook).
* **`persist_messages`** — wraps `thread.add_messages`. Accepts LangChain `BaseMessage` objects (converted automatically) or native Zep `Message` objects, flattens multimodal content to text, and maps names so Zep can resolve identity. Zep rejects direct thread-message payloads over 4,096 characters or 30 messages per call; this helper truncates over-long content and splits larger turns across multiple calls. Pass `return_context=True` to fold persist and retrieve into one round-trip.
* **`create_graph_search_tool`** — returns a LangChain `StructuredTool` over `graph.search`. Pass it to `create_react_agent(tools=[...])` and the model decides when to search. Exactly one of `user_id` (the user's personal graph) or `graph_id` (a shared standalone graph) is required and fixed at construction; the remaining search parameters are pin-or-expose. See [controlling the search tool](#controlling-the-search-tool).

Identity is yours to manage, and the package never provisions lazily — create the Zep user and thread out-of-band before the first turn, with `ensure_user` / `ensure_thread` or your own SDK calls.

## Installation

```bash
pip install zep-langgraph langchain-openai
```

Requires Python 3.11+, `langgraph>=1.2.5`, `zep-cloud>=3.23.0`, and a Zep Cloud API key. Get your API key from [app.getzep.com](https://app.getzep.com).

Set up your environment variables:

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

#### Upgrading from zep-langgraph 0.1.x

Two breaking changes affect existing code:

* **Default context template**: the context block is wrapped in `<ZEP_CONTEXT>...</ZEP_CONTEXT>` instead of `<MEMORY>...</MEMORY>`. To keep the old wording, pass `template="<MEMORY>\n{context}\n</MEMORY>"` to `build_system_message` or `get_zep_context`.
* **Search tool schema**: the model can set `scope`, `reranker`, `limit`, `mmr_lambda`, and `center_node_uuid`, which 0.1.x fixed at construction. Existing `scope=` / `limit=` constructor arguments keep their runtime behavior by pinning those parameters; use `pinned_params` to fix any parameter the model shouldn't control.

See the [package changelog](https://github.com/getzep/zep/blob/main/integrations/langgraph/python/CHANGELOG.md) for the full list of changes.

## Usage

Provision the user and thread, inject context with a `prompt` callable, expose the graph-search tool, and persist each turn. See the [runnable examples](https://github.com/getzep/zep/tree/main/integrations/langgraph/python/examples) for additional patterns.

```python Python
import asyncio
import os
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from zep_cloud import Message
from zep_cloud.client import AsyncZep
from zep_langgraph import (
    build_system_message,
    create_graph_search_tool,
    ensure_thread,
    ensure_user,
    persist_messages,
)

zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])


async def main():
    # Provision the Zep user and thread out-of-band before the first turn.
    await ensure_user(zep, user_id="user-1", first_name="Alice", last_name="Smith")
    await ensure_thread(zep, thread_id="thread-1", user_id="user-1")

    # Inject the Zep context block into the system prompt on every turn.
    async def prompt(state):
        system = await build_system_message(
            zep, thread_id="thread-1", base_instructions="You are a helpful assistant."
        )
        return [system, *state["messages"]]

    agent = create_react_agent(
        model=ChatOpenAI(model="gpt-5-mini"),
        tools=[create_graph_search_tool(zep, user_id="user-1")],
        prompt=prompt,
    )

    result = await agent.ainvoke({"messages": [HumanMessage(content="Where do I work?")]})
    reply = result["messages"][-1]

    # Persist the turn back to Zep.
    await persist_messages(
        zep,
        thread_id="thread-1",
        messages=[Message(role="user", content="Where do I work?", name="Alice Smith"), reply],
    )


asyncio.run(main())
```

## Guaranteed context injection with a pre-model hook

The `prompt` callable above shapes the model's input, but nothing enforces that a caller wires one. `create_zep_pre_model_hook` builds a [`pre_model_hook`](https://langchain-ai.github.io/langgraph/reference/agents/#langgraph.prebuilt.chat_agent_executor.create_react_agent) for `create_react_agent` that injects context on every model call:

```python Python
from langgraph.prebuilt import create_react_agent
from zep_langgraph import create_zep_pre_model_hook

agent = create_react_agent(
    model=model,
    tools=[create_graph_search_tool(zep, user_id="user-1")],
    pre_model_hook=create_zep_pre_model_hook(
        zep, user_id="user-1", thread_id="thread-1",
        base_instructions="You are a helpful assistant.",
    ),
)
```

The hook (a `ZepPreModelHook`) fetches the context block — or runs a custom `context_builder` — and returns it via the hook's `llm_input_messages` key. Per `create_react_agent`'s `pre_model_hook` contract, this shapes the model's input for that step without overwriting the persisted `messages` state, so injected context is re-fetched fresh every turn rather than baked into thread history. The hook supports `context_builder`, `template`, `template_id`, and `base_instructions`, using the same retrieval path as `build_system_message`.

Choose the `prompt` callable when your node already assembles the message list and you want full control over it; choose the hook when you want injection guaranteed regardless of how the agent is wired, or to keep injected context out of persisted state. The hook only injects context — call `persist_messages` separately after the model responds to save the turn.

## Provisioning users and threads

The package never creates users or threads lazily — provision both out-of-band before the first turn. `ensure_user` and `ensure_thread` are idempotent create-then-catch-conflict helpers: they call the Zep SDK's create method, treat an "already exists" conflict as success (returning `False`), and let genuine failures (auth, network, 5xx) raise loudly rather than degrade silently — useful for onboarding flows that should stop on real errors.

```python Python
from zep_langgraph import ensure_thread, ensure_user

async def setup_user(zep_client, user_id: str) -> None:
    ...  # e.g. configure per-user ontology

created = await ensure_user(
    zep,
    user_id="user-1",
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",
    on_created=setup_user,  # fires exactly once, only on real creation
)
await ensure_thread(zep, thread_id="thread-1", user_id="user-1")
```

The `on_created` hook (a `UserSetupHook`) fires only when the user is genuinely new — use it for one-time per-user setup. If the hook raises, the exception propagates even though the user was created, so make the hook idempotent. The synchronous twins `ensure_user_sync` / `ensure_thread_sync` take a synchronous `Zep` client and a synchronous hook (`UserSetupHookSync`). These are plain module-level functions with no instance caching — cache the "already provisioned" result yourself to skip redundant calls.

## Custom context building

Pass `context_builder` to `get_zep_context` or `build_system_message` (or their `_sync` twins) to replace the default `thread.get_user_context` retrieval with custom logic — a filtered graph search, a different graph, or multiple combined sources:

```python Python
from zep_langgraph import ContextInput, build_system_message

async def my_builder(ctx: ContextInput) -> str | None:
    results = await ctx.zep.graph.search(
        user_id=ctx.user_id,
        query=ctx.user_message,
        scope="edges",
    )
    if not results.edges:
        return None
    return "\n".join(edge.fact for edge in results.edges)

system = await build_system_message(
    zep, thread_id="thread-1",
    context_builder=my_builder,
    user_id="user-1",
    user_message=state["messages"][-1].content,
)
```

`ContextInput` is a frozen dataclass bundling `zep`, `user_id`, `thread_id`, and `user_message`; the `user_id` and `user_message` keyword arguments populate it. A builder that raises is logged and treated as returning `None` — these helpers never raise.

Because the helpers are plain functions rather than a single framework-owned turn hook, they don't run persistence and context building concurrently for you. To overlap the two, gather them yourself:

```python Python
import asyncio
from zep_langgraph import build_system_message, persist_messages

async def agent_node(state):
    system, _ = await asyncio.gather(
        build_system_message(
            zep, thread_id="thread-1", context_builder=my_builder,
            user_id="user-1", user_message=state["messages"][-1].content,
        ),
        persist_messages(zep, thread_id="thread-1", messages=[state["messages"][-1]]),
    )
    response = await llm.ainvoke([system, *state["messages"]])
    await persist_messages(zep, thread_id="thread-1", messages=[response])
    return {"messages": [response]}
```

## Customizing the context template

The context block is wrapped using `DEFAULT_CONTEXT_TEMPLATE` — an explicit `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block, canonical across Zep's framework integrations. Pass `template=` to customize the wording; it must contain a literal `{context}` placeholder. Pass `template_id=` instead to render a server-side [context template](/context-templates).

`format_context_block` renders via plain string replacement (`template.replace("{context}", context)`, never `str.format`), so context text or a custom template containing `{`, `}`, or `%` is always safe to inject.

## Controlling the search tool

The search target is fixed when the tool is constructed — exactly one of `user_id` or `graph_id`. Every other `graph.search` parameter is **pin-or-expose**: exposed to the model in the tool's schema by default (with documented defaults), pinnable to a constant, or hideable so Zep's server-side default applies.

Model-exposed parameters:

| Parameter          | Values                                                                   | Notes                                              |
| ------------------ | ------------------------------------------------------------------------ | -------------------------------------------------- |
| `scope`            | `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto` | What to search                                     |
| `reranker`         | `rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder`       | How to rank results                                |
| `limit`            | integer                                                                  | Default 10; clamped to 50                          |
| `mmr_lambda`       | float                                                                    | Relevance–diversity balance for the `mmr` reranker |
| `center_node_uuid` | string                                                                   | Center node for the `node_distance` reranker       |

```python Python
from zep_langgraph import create_graph_search_tool

# Model chooses scope/reranker/limit/mmr_lambda/center_node_uuid freely.
tool = create_graph_search_tool(zep, user_id="user-1")

# Pin scope to "nodes" and limit to 5 — hidden from the model, always sent.
tool = create_graph_search_tool(
    zep, user_id="user-1", pinned_params={"scope": "nodes", "limit": 5}
)

# Hide mmr_lambda from the schema; Zep applies its own default when omitted.
tool = create_graph_search_tool(zep, user_id="user-1", hidden_params={"mmr_lambda"})
```

* `pinned_params` fixes a parameter to a constant value: hidden from the model's schema, always sent.
* `hidden_params` hides a parameter without pinning it, so Zep's server-side default applies.
* A parameter neither pinned nor supplied by the model is omitted from the `graph.search` call entirely — never forwarded as an explicit `None`.
* The legacy `scope`, `reranker`, and `limit` constructor keywords pin the corresponding parameter, equivalent to `pinned_params`.
* `search_filters` and `bfs_origin_node_uuids` are constructor-only; their complex shapes are not exposed to the model.

The schema is built dynamically with `pydantic.create_model` and passed as the `StructuredTool`'s `args_schema`. See [searching the graph](/searching-the-graph) for what each parameter does.

## Long-term memory with ZepStore

`BaseStore` is LangGraph's cross-thread long-term-memory interface; `create_react_agent(store=...)` and langmem's memory tools require one. Zep is a temporal knowledge graph, not a key-value store, so it can't faithfully serve exact-key reads or read-after-write on its own. `ZepStore` bridges this with a hybrid-delegate design: a backing key-value store (default `InMemoryStore`) serves exact-key `get` / `put` / `delete` synchronously, while every `put` is also ingested into Zep and `search` is routed to Zep's semantic `graph.search`.

```python Python
from zep_langgraph import ZepStore

store = ZepStore(zep)  # default backing store: InMemoryStore
await store.aput(("memories", "user-1"), "m1", {"text": "Alice works at Acme."})
item = await store.aget(("memories", "user-1"), "m1")  # exact-key, synchronous
hits = await store.asearch(("memories", "user-1"), query="where does Alice work?")
```

Zep ingestion is asynchronous. A value written with `put` is available immediately for exact-key `get` (served by the backing store), but its extracted facts are not instantly returned by `search`. `ZepStore` is the long-term memory layer, not the checkpointer, so graph execution and short-term state are unaffected.

## Public API

| Symbol                                                       | Kind                   | Purpose                                                                           |
| ------------------------------------------------------------ | ---------------------- | --------------------------------------------------------------------------------- |
| `get_zep_context` / `get_zep_context_sync`                   | async / sync fn        | Fetch the context block for a thread (or run a `context_builder`)                 |
| `build_system_message` / `build_system_message_sync`         | async / sync fn        | Build a `SystemMessage` with the context block                                    |
| `format_context_block`                                       | fn                     | Combine base instructions with a context block                                    |
| `ContextInput` / `ContextBuilder` / `ContextBuilderSync`     | dataclass / type alias | Custom context-builder contract                                                   |
| `DEFAULT_CONTEXT_TEMPLATE`                                   | constant               | Canonical `<ZEP_CONTEXT>...</ZEP_CONTEXT>` wrapper                                |
| `persist_messages` / `persist_messages_sync`                 | async / sync fn        | Persist a turn (LangChain or Zep messages)                                        |
| `to_zep_message` / `to_zep_messages`                         | fn                     | Convert LangChain messages to Zep messages                                        |
| `MAX_MESSAGE_CHARS` / `MAX_MESSAGES_PER_CALL`                | constants              | Per-message truncation length (4096) and per-call message cap (30)                |
| `ensure_user` / `ensure_user_sync`                           | async / sync fn        | Idempotently provision a Zep user, out-of-band                                    |
| `ensure_thread` / `ensure_thread_sync`                       | async / sync fn        | Idempotently provision a Zep thread, out-of-band                                  |
| `UserSetupHook` / `UserSetupHookSync`                        | type alias             | `on_created` hook signatures for `ensure_user` / `ensure_user_sync`               |
| `create_zep_pre_model_hook`                                  | fn                     | Build a `create_react_agent(pre_model_hook=...)` for guaranteed context injection |
| `ZepPreModelHook`                                            | class                  | Hook returned by `create_zep_pre_model_hook`                                      |
| `create_graph_search_tool` / `create_graph_search_tool_sync` | fn                     | Build a pin-or-expose `graph.search` `StructuredTool`                             |
| `GraphSearchScope` / `GraphSearchReranker`                   | type alias             | Valid `scope` and `reranker` values for the search tool                           |
| `DEFAULT_TOOL_NAME` / `DEFAULT_TOOL_DESCRIPTION`             | constants              | Default name and description of the search tool                                   |
| `ZepStore`                                                   | class                  | Hybrid-delegate `BaseStore`                                                       |
| `NamespaceTargetResolver`                                    | type alias             | Maps a `ZepStore` namespace to its Zep ingestion target                           |
| `ZepDependencyError`                                         | exception              | Raised when required LangChain/LangGraph dependencies are missing                 |

`MAX_MESSAGE_CHARS` and `MAX_MESSAGES_PER_CALL` are useful when writing custom batching around `persist_messages` or `persist_messages_sync`.

Both an `AsyncZep` (async helpers, recommended) and a synchronous `Zep` client are supported. Reuse a single client instance.

## Best practices

* **Provision the user and thread out-of-band** before the first turn with `ensure_user` / `ensure_thread` — the package never creates them lazily
* **Pass real names** to `persist_messages` so Zep can resolve the user's identity node
* **Pin search parameters the model shouldn't control** with `pinned_params` — e.g. a fixed `scope` or `limit`
* **Use the async helpers** with `AsyncZep` for non-blocking nodes; the `_sync` variants exist for synchronous graphs
* **Allow time for indexing** — Zep extracts knowledge asynchronously, so facts from a turn are not instantly searchable

## Next steps

* Explore [customizing graph structure](/customizing-graph-structure) for advanced knowledge organization
* Learn about [searching the graph](/searching-the-graph) and how to tune search
* See [code examples](https://github.com/getzep/zep/tree/main/integrations/langgraph/python/examples) for the `create_react_agent` and `ZepStore` patterns