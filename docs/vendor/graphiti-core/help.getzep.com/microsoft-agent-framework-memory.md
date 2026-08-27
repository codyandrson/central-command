> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Microsoft Agent Framework integration

[Microsoft Agent Framework](https://github.com/microsoft/agent-framework) agents using Zep gain long-term memory backed by a temporal knowledge graph. The `zep-ms-agent-framework` package attaches a context provider that persists each conversation turn, injects relevant context into the model on every run, and can register a model-callable graph-search tool.

## Core benefits

* **Native context-provider hook**: Uses the framework's own `before_run` / `after_run` pipeline — the same surface as its built-in memory providers
* **Single round-trip**: Persists the user turn and retrieves the context block in one call (or concurrently, with a custom context builder)
* **Whole-user-graph recall**: Context is fused across all of a user's threads, so a new conversation still recalls earlier facts
* **Pin-or-expose graph search**: `expose_search_tool` / `create_zep_search_tool` add an on-demand tool over `graph.search`, with every search parameter model-exposed by default or pinned/hidden per deployment
* **Per-user setup hook**: `on_user_created` runs once per new user — for configuring ontology, extraction instructions, or user summary instructions
* **Out-of-band provisioning**: `ensure_user` / `ensure_thread` create resources up front and raise loudly on genuine failures; the run path falls back to lazy creation
* **Graceful degradation**: A Zep failure on the run path is logged but never crashes the host agent — the turn proceeds without memory

## How it works

The integration ships one main class, `ZepContextProvider`, which subclasses the framework's `ContextProvider` and overrides the two lifecycle hooks called around every `agent.run(...)`:

**`before_run`** — runs before the model is invoked. On each turn it:

1. Registers the graph-search tool via `context.extend_tools(...)`, when `expose_search_tool=True`
2. Extracts the latest user message from `context.input_messages`
3. Lazily creates the Zep user and thread on first use (cached thereafter), using the same logic as `ensure_user` / `ensure_thread`
4. Persists the message — via `thread.add_messages(return_context=True)` by default (a single round-trip), or concurrently with a custom `context_builder` when one is set
5. Injects the resulting context block, wrapped in `context_template`, into the model's instructions via `context.extend_instructions(...)`

**`after_run`** — runs after the model responds. It reads the assistant's reply from `context.response.messages` and persists it to the same thread, so both sides of the conversation are captured.

Because context is assembled from the entire user graph, the thread only scopes relevance — an agent on a new thread still recalls facts the same user shared earlier.

## Installation

```bash
pip install zep-ms-agent-framework
```

The package depends only on `agent-framework-core`. The example below also uses a model provider:

```bash
pip install zep-ms-agent-framework agent-framework-openai
```

Requires Python 3.11+, `agent-framework-core>=1.8.1`, and a Zep Cloud API key. Get your API key from [app.getzep.com](https://app.getzep.com).

Set up your environment variables:

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

#### Upgrading from zep-ms-agent-framework 0.1.x

Two changes can require attention: the default injected context wording follows the canonical `DEFAULT_CONTEXT_TEMPLATE` — pass `context_template=...` to keep custom wording — and `on_user_created` runs through `ensure_user`, so on the lazy run path a hook failure is logged, swallowed, and skips that turn's Zep persistence, while a hook failure during an out-of-band `ensure_user` call propagates. See the [package changelog](https://github.com/getzep/zep/blob/main/integrations/ms-agent-framework/python/CHANGELOG.md) for the full list of changes.

## Usage

Attach a `ZepContextProvider` to an agent through the `context_providers` keyword argument:

```python Python
import asyncio
from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient
from zep_cloud.client import AsyncZep
from zep_ms_agent_framework import ZepContextProvider

zep = AsyncZep(api_key="your-zep-api-key")

agent = Agent(
    OpenAIChatClient(model="gpt-5-mini"),
    instructions="You are a helpful assistant with long-term memory.",
    context_providers=[
        ZepContextProvider(
            zep_client=zep,
            user_id="user-123",
            thread_id="thread-abc",
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",  # optional
        )
    ],
)

async def main() -> None:
    result = await agent.run("Hi, I'm a data scientist in Portland.")
    print(result.text)

asyncio.run(main())
```

Memory is scoped per `ZepContextProvider` instance to one `user_id` and `thread_id`. For a multi-user application, construct one provider per user or conversation, passing real names so Zep can resolve the user's identity node in the graph.

## On-demand graph search

Beyond the automatic context injection, `create_zep_search_tool` returns a model-callable `agent_framework.FunctionTool` over `graph.search`. The model decides when to look up specific facts, entities, or prior episodes. By default it searches the given user's graph; pass `graph_id=...` to target a shared standalone graph instead.

The easiest way to use it is `expose_search_tool=True` on `ZepContextProvider`, which builds the tool once at construction and registers it on every run via `context.extend_tools(...)`:

```python
provider = ZepContextProvider(
    zep_client=zep,
    user_id="user-123",
    thread_id="thread-abc",
    expose_search_tool=True,
    search_pinned_params={"scope": "nodes", "limit": 5},
)
```

With this configuration, the model sees the un-pinned parameters (`reranker`, `mmr_lambda`, `center_node_uuid`). `scope` and `limit` are hidden from the schema and sent with the pinned values.

Every search parameter (`scope`, `reranker`, `limit`, `mmr_lambda`, `center_node_uuid`) is exposed to the model in the tool's JSON schema by default, with documented defaults. Two options override this per deployment: `search_pinned_params` fixes a parameter to a constant value and hides it from the schema, and `search_hidden_params` hides a parameter without pinning it, so Zep's server-side default applies. `search_filters` and `bfs_origin_node_uuids` are constructor-only — their complex shapes are not exposed to the model.

The standalone factory takes the same pin-or-expose options:

```python
from zep_ms_agent_framework import create_zep_search_tool

# Model chooses scope/reranker/limit/mmr_lambda/center_node_uuid freely.
tool = create_zep_search_tool(zep_client=zep, user_id="user-123")

# Pin scope to "nodes" and limit to 5 — hidden from the model, always sent.
tool = create_zep_search_tool(
    zep_client=zep, user_id="user-123",
    search_pinned_params={"scope": "nodes", "limit": 5},
)

# Hide mmr_lambda from the schema; Zep applies its own default when omitted.
tool = create_zep_search_tool(
    zep_client=zep, user_id="user-123", search_hidden_params={"mmr_lambda"},
)
```

Model-exposed search parameters (when not pinned or hidden), with their defaults:

| Parameter          | Type                                                                                 | Default   | Description                                                  |
| ------------------ | ------------------------------------------------------------------------------------ | --------- | ------------------------------------------------------------ |
| `scope`            | `"edges" \| "nodes" \| "episodes" \| "observations" \| "thread_summaries" \| "auto"` | `"edges"` | What to search                                               |
| `reranker`         | `"rrf" \| "mmr" \| "node_distance" \| "episode_mentions" \| "cross_encoder"`         | `"rrf"`   | Result ordering (ignored for `scope="auto"`)                 |
| `limit`            | `int`                                                                                | `10`      | Maximum results (clamped to Zep's ceiling of 50)             |
| `mmr_lambda`       | `float`                                                                              | —         | Diversity/relevance balance; only used when `reranker="mmr"` |
| `center_node_uuid` | `str`                                                                                | —         | Center node for `reranker="node_distance"`                   |

## Provisioning

`ensure_user` and `ensure_thread` provision the Zep user and thread out-of-band, before the first run — useful for onboarding flows that want genuine failures (auth, network, 5xx) to raise loudly rather than degrade silently:

```python
from zep_ms_agent_framework import ensure_thread, ensure_user

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

Both helpers are create-then-catch-conflict: they treat an "already exists" conflict as success (returning `False`), return `True` on genuine creation, and propagate genuine failures. Use the `on_created` hook (a `UserSetupHook`) — or the equivalent `on_user_created` option on `ZepContextProvider` — to configure per-user resources such as a custom ontology, custom extraction instructions, or user summary instructions exactly once; see [customizing graph structure](/customizing-graph-structure) for the available options. If `on_created` raises, that exception propagates even though the user was created, so make the hook idempotent.

Calling these helpers is optional: `before_run` runs the same logic lazily on the run path, wrapped so that a genuine failure there — including an `on_user_created` hook failure — is logged, swallowed, and skips that turn's Zep persistence rather than breaking the run. Called out-of-band, the same failures propagate to the caller.

## Custom context building

Set `context_builder` on `ZepContextProvider` to replace the default context retrieval with custom logic — for example, searching a different graph, applying filters, or combining multiple sources:

```python
from zep_ms_agent_framework import ContextInput, ZepContextProvider

async def my_builder(ctx: ContextInput) -> str | None:
    results = await ctx.zep.graph.search(
        user_id=ctx.user_id,
        query=ctx.user_message,
        scope="edges",
    )
    if not results.edges:
        return None
    return "\n".join(edge.fact for edge in results.edges)

provider = ZepContextProvider(
    zep_client=zep,
    user_id="user-123",
    thread_id="thread-abc",
    context_builder=my_builder,
)
```

`ContextInput` bundles `zep` (the `AsyncZep` client), `user_id`, `thread_id`, `user_message`, and `session_context` (the Agent Framework `SessionContext` for the turn). Returning `None` skips injection for that turn.

When `context_builder` is set, message persistence (`add_messages` without `return_context`) and the builder run concurrently, with per-side failure isolation:

* If the builder raises, a warning is logged and context injection is skipped for that turn — persistence still completes and the turn is marked as persisted.
* If persistence raises, a warning is logged and the turn is not marked as persisted (so `after_run` skips writing the assistant reply, and the turn can be retried on the next invocation) — a successful builder result is still injected.

## Context template

`context_template` controls how retrieved context is wrapped before injection. It must contain a literal `{context}` placeholder, rendered via plain string replacement (`template.replace("{context}", context)`, never `str.format`), so context text containing `{`, `}`, or `%` is always safe to inject:

```python
provider = ZepContextProvider(
    zep_client=zep,
    user_id="user-123",
    thread_id="thread-abc",
    context_template="Relevant memory:\n{context}",
)
```

The default is `DEFAULT_CONTEXT_TEMPLATE`, an explicit `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block with canonical wording shared across Zep's framework integrations.

## Configuration options

`ZepContextProvider` accepts:

| Field                    | Required    | Default                    | Description                                                                                                      |
| ------------------------ | ----------- | -------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `zep_client`             | Yes         | —                          | Initialized `AsyncZep` client (caller owns its lifecycle)                                                        |
| `user_id`                | Yes         | —                          | Zep user ID this provider's memory is scoped to                                                                  |
| `thread_id`              | Yes         | —                          | Zep thread ID the conversation is recorded in                                                                    |
| `first_name`             | Recommended | `None`                     | User first name — helps Zep anchor identity                                                                      |
| `last_name`              | Optional    | `None`                     | User last name                                                                                                   |
| `email`                  | Optional    | `None`                     | User email                                                                                                       |
| `user_message_name`      | Optional    | full name                  | Display name on persisted user messages                                                                          |
| `assistant_message_name` | Optional    | `"Assistant"`              | Display name on persisted assistant messages                                                                     |
| `source_id`              | Optional    | `"zep"`                    | Attribution ID for injected instructions and tools                                                               |
| `ignore_roles`           | Optional    | `None`                     | Roles to exclude from graph ingestion (still stored in thread history)                                           |
| `on_user_created`        | Optional    | `None`                     | Async hook run once after a new user is created (see [provisioning](#provisioning))                              |
| `context_builder`        | Optional    | `None`                     | Custom async context-retrieval callable (see [custom context building](#custom-context-building))                |
| `context_template`       | Optional    | `DEFAULT_CONTEXT_TEMPLATE` | Template wrapping injected context; must contain a literal `{context}` placeholder                               |
| `expose_search_tool`     | Optional    | `False`                    | Register a model-callable graph-search tool on every run (see [on-demand graph search](#on-demand-graph-search)) |
| `search_pinned_params`   | Optional    | `None`                     | Fix a search parameter to a value; hidden from the model schema                                                  |
| `search_hidden_params`   | Optional    | `None`                     | Hide a search parameter from the schema without pinning (Zep's server-side default applies)                      |
| `search_filters`         | Optional    | `None`                     | Constructor-only Zep search filters (`node_labels`, `edge_types`, etc.)                                          |
| `bfs_origin_node_uuids`  | Optional    | `None`                     | Constructor-only node UUIDs for BFS seeding                                                                      |

## Best practices

* **Pass real names** so Zep can anchor and resolve the user's identity node in the graph
* **One provider per user/conversation** — memory is scoped to a single `user_id` and `thread_id`
* **Reuse a single `AsyncZep` client** across requests; the caller owns its lifecycle
* **Provision up front in onboarding flows** with `ensure_user` / `ensure_thread` so misconfiguration raises before the agent ever runs
* **Allow time for indexing** — Zep extracts knowledge asynchronously, so facts from a turn are not instantly retrievable

## Next steps

* Explore [customizing graph structure](/customizing-graph-structure) for advanced knowledge organization
* Learn about [searching the graph](/searching-the-graph) and how to tune search
* See [code examples](https://github.com/getzep/zep/tree/main/integrations/ms-agent-framework/python/examples) for additional patterns