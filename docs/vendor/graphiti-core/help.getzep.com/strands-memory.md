> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Strands integration

[Strands](https://strandsagents.com/) agents using Zep gain long-term memory backed by a temporal knowledge graph. The `zep-strands` package implements Strands' [`MemoryStore`](https://strandsagents.com/docs/user-guide/concepts/memory/overview/) interface so Zep plugs into `MemoryManager` for context injection, server-side extraction, and optional on-demand graph search.

## Core benefits

* **Native Strands `MemoryStore`**: Works with `MemoryManager` for injection, built-in memory tools, and automatic extraction
* **Context injection**: `MemoryManager` calls `store.search()` before each user turn and injects relevant Zep context
* **Batched server-side extraction**: Conversation turns are buffered and posted to Zep via `thread.add_messages` on Strands' default cadence (every 5 turns)
* **Whole-user-graph recall**: Context is fused across all of a user's threads, so a new conversation still recalls earlier facts
* **Pin-or-expose graph search**: `expose_search_tool` / `create_zep_search_tool` add an on-demand tool over `graph.search`, with every search parameter model-exposed by default or pinned/hidden per deployment
* **Standalone graph mode**: Scope a store to a shared `graph_id` for domain knowledge (search and add only)
* **Out-of-band provisioning**: `ensure_user` / `ensure_thread` create resources up front and raise loudly on genuine failures; the store falls back to lazy creation on first use
* **Framework-owned failure isolation**: Zep SDK errors propagate from store methods so Strands can skip, surface, or retry correctly

## How it works

The integration ships one main class, `ZepMemoryStore`, which implements Strands' `MemoryStore` Protocol and plugs into `MemoryManager(stores=[store])`. The manager owns injection, the extraction loop, and two built-in tools: `search_memory` (registered by default via `search_tool_config=True`; set `False` to disable) and `add_memory` (opt-in via `add_tool_config=True`). The store maps each hook onto Zep:

| Strands hook               | Zep call                     | Purpose                                                    |
| -------------------------- | ---------------------------- | ---------------------------------------------------------- |
| `MemoryStore.search`       | `graph.search`               | Recall for injection and the `search_memory` tool          |
| `MemoryStore.add_messages` | `thread.add_messages`        | Server-side extraction from conversation turns             |
| `MemoryStore.add`          | `graph.add`                  | Single-fact writes (`add_memory` tool / programmatic)      |
| First `search` / write     | `user.add` + `thread.create` | Provision resources on first use when helpers were skipped |
| `MemoryStore.get_tools`    | `create_zep_search_tool`     | Optional `zep_search` tool when `expose_search_tool=True`  |

Context comes from the **whole user graph**; the thread only scopes relevance and records the conversation. A new thread for the same user still recalls earlier facts.

`ZepMemoryStore.initialize()` deliberately makes **no** Zep calls. `Agent.__init__` is synchronous, so Strands runs that hook on a throwaway event loop in a worker thread; calling Zep there would drive your `AsyncZep` client from a second event loop. Deferring to first use keeps every Zep call on the agent's own loop.

## Installation

```bash
pip install zep-strands
```

The package depends on `strands-agents` and `zep-cloud`. The example below also uses a model provider:

```bash
pip install zep-strands 'strands-agents[openai]'
```

Requires Python 3.11+, `strands-agents>=1.45.0`, `zep-cloud>=3.23.0`, and a Zep Cloud API key. Get your API key from [app.getzep.com](https://app.getzep.com).

Set up your environment variables:

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

## Usage

Provision the Zep user and thread, construct a `ZepMemoryStore`, and pass it to `MemoryManager`:

```python Python
import asyncio
from strands import Agent
from strands.memory import MemoryManager
from zep_cloud.client import AsyncZep
from zep_strands import ZepMemoryStore, ensure_thread, ensure_user

zep = AsyncZep(api_key="your-zep-api-key")

async def main() -> None:
    await ensure_user(
        zep,
        user_id="user-123",
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
    )
    await ensure_thread(zep, thread_id="thread-abc", user_id="user-123")

    store = ZepMemoryStore(
        zep_client=zep,
        user_id="user-123",
        thread_id="thread-abc",
        first_name="Jane",
        last_name="Smith",
        writable=True,
        extraction=True,  # server-side via add_messages
    )

    agent = Agent(
        system_prompt="You are a helpful assistant with long-term memory.",
        memory_manager=MemoryManager(stores=[store]),
    )

    result = await agent.invoke_async("Hi, I'm a data scientist in Portland.")
    print(result)
    # Flush pending extraction before shutdown (required on invoke_async / stream_async).
    await agent.memory_manager.flush()

asyncio.run(main())
```

Memory is scoped per `ZepMemoryStore` instance to one `user_id` and `thread_id` (or one `graph_id`). For a multi-user application, construct one store per user or conversation, passing real names so Zep can resolve the user's identity node in the graph.

With no further configuration, the manager injects relevant Zep context before each user turn, registers the built-in `search_memory` tool, and runs server-side extraction on Strands' default cadence. Enable `add_tool_config=True` on the manager to also let the model call `add_memory`.

## Automatic extraction and delayed graph building

`extraction=True` (the default when the store is writable with `user_id` + `thread_id`) opts into Strands' automatic extraction loop. With the manager's defaults that means:

1. Conversation turns are buffered in the manager.
2. Every **5 turns**, Strands calls `add_messages`, which posts the batch to Zep via `thread.add_messages`.
3. Zep then processes the batch asynchronously into the user graph.

Until step 2 runs, **nothing has been sent to Zep**, so the graph does not grow turn-by-turn. After step 2, facts are still not instantly searchable (Zep ingestion is async). Plan for both delays:

* Call `await memory_manager.flush()` at session boundaries (required after `invoke_async` / `stream_async` if you need pending turns persisted before shutdown).
* Or pass an every-turn trigger if you need messages sent to Zep more often:

```python
from strands.memory.extraction.triggers import InvocationTrigger
from strands.memory.extraction.types import ExtractionConfig

store = ZepMemoryStore(
    zep_client=zep,
    user_id="user-123",
    thread_id="thread-abc",
    writable=True,
    extraction=ExtractionConfig(trigger=InvocationTrigger()),  # after every turn
)
```

`extraction=True` (or an `ExtractionConfig`) **requires** writable user-graph mode with both `user_id` and `thread_id`. Construction raises `ValueError` otherwise — use `extraction=False` for standalone graphs or read-only stores.

## Scoping modes

**User graph** (default for conversational agents) — pass `user_id` and `thread_id`:

```python
ZepMemoryStore(zep_client=zep, user_id="user-123", thread_id="thread-abc", ...)
```

**Standalone graph** (shared / domain knowledge) — pass `graph_id`. Supports `search` and `add` only (no `add_messages`):

```python
ZepMemoryStore(zep_client=zep, graph_id="company-kb", writable=True, extraction=False)
```

Provide exactly one of `user_id` or `graph_id`.

## Search and injection

By default `search_scope="auto"`, so injection receives Zep's assembled Context Block as a single `MemoryEntry`. Pin a scoped search when you want discrete facts:

```python
store = ZepMemoryStore(
    zep_client=zep,
    user_id="user-123",
    thread_id="thread-abc",
    search_scope="edges",
    search_filters={"edge_types": ["PREFERS"]},
)
```

## On-demand graph search

Beyond automatic injection and the manager's built-in `search_memory` tool, `create_zep_search_tool` returns a separate model-callable Strands tool over `graph.search`. The model decides when to look up specific facts, entities, or prior episodes. By default it searches the given user's graph; pass `graph_id=...` to target a shared standalone graph instead.

`search_memory` is on by default (`search_tool_config=True`) and calls `store.search`, so a Zep failure is logged and that store is skipped. `zep_search` (`expose_search_tool=True`) is opt-in, exposes pin-or-expose `graph.search` parameters to the model, and on failure returns `"Graph search failed."` rather than raising. Prefer one model-callable search path unless you intentionally want both.

The easiest way to use `zep_search` is `expose_search_tool=True` on `ZepMemoryStore`, which registers the tool via `get_tools()`:

```python
store = ZepMemoryStore(
    zep_client=zep,
    user_id="user-123",
    thread_id="thread-abc",
    expose_search_tool=True,
    search_pinned_params={"scope": "edges", "limit": 10},
)
```

With this configuration, the model sees the un-pinned parameters (`reranker`, `mmr_lambda`, `center_node_uuid`). `scope` and `limit` are hidden from the schema and sent with the pinned values.

Every search parameter (`scope`, `reranker`, `limit`, `mmr_lambda`, `center_node_uuid`) is exposed to the model in the tool's schema by default, with documented defaults. Two options override this per deployment: `search_pinned_params` fixes a parameter to a constant value and hides it from the schema, and `search_hidden_params` hides a parameter without pinning it, so Zep's server-side default applies. `search_filters` and `bfs_origin_node_uuids` are constructor-only — their complex shapes are not exposed to the model.

The standalone factory takes the same pin-or-expose options:

```python
from zep_strands import create_zep_search_tool

# Model chooses scope/reranker/limit/mmr_lambda/center_node_uuid freely.
tool = create_zep_search_tool(zep_client=zep, user_id="user-123")

# Pin scope to "edges" — hidden from the model, always sent.
tool = create_zep_search_tool(
    zep_client=zep,
    user_id="user-123",
    search_pinned_params={"scope": "edges"},
)

# Hide mmr_lambda from the schema; Zep applies its own default when omitted.
tool = create_zep_search_tool(
    zep_client=zep,
    user_id="user-123",
    search_hidden_params={"mmr_lambda"},
)

agent = Agent(tools=[tool])
```

Model-exposed search parameters (when not pinned or hidden), with their defaults. These are the **tool's** defaults and are independent of the store's `search_scope` (which defaults to `"auto"` for injection):

| Parameter          | Type                                                                                 | Default   | Description                                                  |
| ------------------ | ------------------------------------------------------------------------------------ | --------- | ------------------------------------------------------------ |
| `scope`            | `"edges" \| "nodes" \| "episodes" \| "observations" \| "thread_summaries" \| "auto"` | `"edges"` | What to search                                               |
| `reranker`         | `"rrf" \| "mmr" \| "node_distance" \| "episode_mentions" \| "cross_encoder"`         | `"rrf"`   | Result ordering (ignored for `scope="auto"`)                 |
| `limit`            | `int`                                                                                | `10`      | Maximum results (clamped to Zep's ceiling of 50)             |
| `mmr_lambda`       | `float`                                                                              | —         | Diversity/relevance balance; only used when `reranker="mmr"` |
| `center_node_uuid` | `str`                                                                                | —         | Center node for `reranker="node_distance"`                   |

## Writing facts

Write facts programmatically with `store.add`, or enable `add_tool_config=True` on `MemoryManager` so the model can call `add_memory`:

```python
# Text fact into the user graph
await store.add("Prefers aisle seats", metadata={"source": "prefs"})

# JSON payload
await store.add('{"plan": "premium"}', metadata={"type": "json"})
```

`metadata["type"]` selects the Zep data type (`text` default, `json`, or `message`). Remaining metadata keys are forwarded as episode metadata.

Oversized `text`/`message` payloads are truncated to Zep's `graph.add` limit with a warning. Oversized `json` is **rejected with a `ValueError`** instead — slicing JSON strips its closing syntax, so a truncated document would just be rejected by Zep. Split large JSON into smaller documents before adding; see [chunking large documents](/chunking-large-documents).

## Provisioning

`ensure_user` and `ensure_thread` provision the Zep user and thread out-of-band, before the first turn — useful for onboarding flows that want genuine failures (auth, network, 5xx) to raise loudly:

```python
from zep_strands import ensure_thread, ensure_user

async def setup_user(zep_client, user_id: str) -> None:
    ...  # e.g. configure per-user ontology

created = await ensure_user(
    zep,
    user_id="user-123",
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",
    on_created=setup_user,  # fires exactly once, only on real creation
)
await ensure_thread(zep, thread_id="thread-abc", user_id="user-123")
```

Both helpers are create-then-catch-conflict: they treat an "already exists" conflict as success (returning `False`), return `True` on genuine creation, and propagate genuine failures. Use the `on_created` hook (a `UserSetupHook`) — or the equivalent `on_user_created` option on `ZepMemoryStore` — to configure per-user resources such as a custom ontology, custom extraction instructions, or user summary instructions exactly once; see [customizing graph structure](/customizing-graph-structure) for the available options. If `on_created` raises, that exception propagates even though the user was created, so make the hook idempotent.

Calling these helpers is optional: if you skip them, the store provisions itself on its first search or write instead.

## Error handling

Zep SDK errors propagate out of the store methods deliberately: in Strands the **framework** owns failure isolation, and swallowing them breaks it.

| Path           | Framework behavior on a raise                                                                                                   |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `search`       | `MemoryManager.search` logs and skips the failing store; injection additionally fails open, so the turn proceeds without memory |
| `add`          | Surfaced as `AggregateMemoryError` so a failed write is never silent                                                            |
| `add_messages` | `ExtractionCoordinator` catches it and **rolls back its high-water mark so the batch retries**                                  |

That last path matters most: returning `None` after swallowing an error would be read as success, advancing the mark and discarding those messages permanently. This matches the SDK's own vended stores, which raise rather than degrade.

The one exception is the model-callable `zep_search` tool, which catches Zep errors and returns `"Graph search failed."` — a raw tool has no framework layer above it.

## Configuration options

`ZepMemoryStore` accepts:

| Field                    | Required                      | Default                               | Description                                                                                                           |
| ------------------------ | ----------------------------- | ------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `zep_client`             | Yes                           | —                                     | Initialized `AsyncZep` client (caller owns its lifecycle)                                                             |
| `user_id`                | One of `user_id` / `graph_id` | —                                     | Zep user ID for user-graph mode                                                                                       |
| `thread_id`              | With user-graph extraction    | —                                     | Zep thread ID used by `add_messages`                                                                                  |
| `graph_id`               | One of `user_id` / `graph_id` | —                                     | Standalone graph ID (mutually exclusive with `user_id`)                                                               |
| `name`                   | Optional                      | `"zep"`                               | Store name exposed to `MemoryManager` tools                                                                           |
| `description`            | Optional                      | Zep description                       | Store description exposed to `MemoryManager` tools                                                                    |
| `max_search_results`     | Optional                      | `10`                                  | Default search result cap                                                                                             |
| `writable`               | Optional                      | `True`                                | Whether writes are accepted                                                                                           |
| `extraction`             | Optional                      | `True` when writable with user+thread | `True`, `False`, or an `ExtractionConfig`; requires writable user/thread mode when enabled                            |
| `first_name`             | Recommended                   | `None`                                | User first name — helps Zep anchor identity                                                                           |
| `last_name`              | Optional                      | `None`                                | User last name                                                                                                        |
| `email`                  | Optional                      | `None`                                | User email                                                                                                            |
| `user_message_name`      | Optional                      | full name                             | Display name on persisted user messages                                                                               |
| `assistant_message_name` | Optional                      | `"Assistant"`                         | Display name on persisted assistant messages                                                                          |
| `ignore_roles`           | Optional                      | `None`                                | Roles to exclude from graph ingestion (still stored in thread history)                                                |
| `on_user_created`        | Optional                      | `None`                                | Async hook run once after a new user is created (see [provisioning](#provisioning))                                   |
| `search_scope`           | Optional                      | `"auto"`                              | Default `graph.search` scope for injection (`"auto"` returns Zep's Context Block)                                     |
| `search_reranker`        | Optional                      | `None`                                | Optional default reranker for `search`                                                                                |
| `search_filters`         | Optional                      | `None`                                | Constructor-only Zep search filters (`node_labels`, `edge_types`, etc.)                                               |
| `bfs_origin_node_uuids`  | Optional                      | `None`                                | Constructor-only node UUIDs for BFS seeding                                                                           |
| `expose_search_tool`     | Optional                      | `False`                               | Register a model-callable graph-search tool via `get_tools()` (see [on-demand graph search](#on-demand-graph-search)) |
| `search_pinned_params`   | Optional                      | `None`                                | Fix a search parameter to a value; hidden from the model schema                                                       |
| `search_hidden_params`   | Optional                      | `None`                                | Hide a search parameter from the schema without pinning (Zep's server-side default applies)                           |

## Best practices

* **Pass real names** so Zep can anchor and resolve the user's identity node in the graph
* **One store per user/conversation** — memory is scoped to a single `user_id`/`thread_id` (or `graph_id`)
* **Reuse a single `AsyncZep` client** across requests; the caller owns its lifecycle
* **Provision up front in onboarding flows** with `ensure_user` / `ensure_thread` so misconfiguration raises before the agent ever runs
* **Flush at session boundaries** — call `await memory_manager.flush()` after `invoke_async` / `stream_async` so buffered turns reach Zep before shutdown
* **Allow time for indexing** — Zep extracts knowledge asynchronously, so facts from a turn are not instantly retrievable (and with the default 5-turn cadence, messages may not have been sent yet)

## Next steps

* Explore [customizing graph structure](/customizing-graph-structure) for advanced knowledge organization
* Learn about [searching the graph](/searching-the-graph) and how to tune search
* See [code examples](https://github.com/getzep/zep/tree/main/integrations/strands/python/examples) for additional patterns