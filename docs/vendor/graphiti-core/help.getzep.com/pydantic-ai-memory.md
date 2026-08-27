> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Pydantic AI integration

[Pydantic AI](https://ai.pydantic.dev) agents using Zep gain long-term memory backed by a temporal knowledge graph. The `zep-pydantic-ai` package persists both sides of each conversation turn, injects relevant context into the model prompt using Pydantic AI's native capabilities, and adds a model-callable graph-search tool.

## Core benefits

* **Native Pydantic AI capabilities**: `zep_capabilities(deps)` bundles the current `ProcessHistory` history-processor hook with a `Hooks(after_run=...)` hook — not a deprecated kwarg
* **Automatic assistant persistence**: The bundled `after_run` hook persists the assistant's reply when the run completes, so no manual persistence call is needed
* **Single round-trip**: Persists the user turn and retrieves context in one `add_messages` call
* **Correct under tool calls**: Dedupes per run (keyed by `RunContext.run_id`), so a run that makes tool calls records the turn exactly once
* **Pin-or-expose graph search**: A model-callable tool over `graph.search` — every search parameter is model-exposed by default, or pinned/hidden per deployment
* **Out-of-band provisioning**: `ensure_user` / `ensure_thread` create resources up front, with a per-user setup hook that fires only on real creation
* **Graceful degradation**: A Zep failure on the turn path is logged but never crashes the agent run

## How it works

The integration plugs into Pydantic AI through three components:

* **`ZepDeps`** — a dataclass used as the agent's `deps_type`. It carries the Zep client, the user/thread identity, and optional context-building configuration. Construct one per conversation and pass it to `agent.run(..., deps=deps)`; the history processor, the `after_run` hook, and the search tool all reach it through `RunContext.deps`.
* **`zep_capabilities(deps)`** — the recommended way to register memory. It returns a capabilities list bundling `ProcessHistory(zep_history_processor)` — which persists the latest user message via `thread.add_messages(return_context=True)` before each model request and prepends Zep's context block as a system message — with a `Hooks(after_run=...)` hook that persists the assistant's reply once the run completes. Use `create_zep_after_run_hook(deps)` to compose that hook with your own `Hooks(...)` instance instead.
* **`create_zep_search_tool`** — a factory returning a model-callable `pydantic_ai.Tool` over `graph.search`. The model decides when to search the knowledge graph and, by default, which search parameters to use.

Because `ProcessHistory` fires once per model request (not once per run), the history processor dedupes per run, keyed by `RunContext.run_id`: it persists and retrieves on the first model request of a run and replays the cached context on later requests within that same run, so tool-calling runs never create duplicate episodes.

## Installation

```bash
pip install zep-pydantic-ai
```

Requires Python 3.11+, `pydantic-ai>=1.107,<2`, and a Zep Cloud API key. Get your API key from [app.getzep.com](https://app.getzep.com).

Set up your environment variables:

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

#### Upgrading from zep-pydantic-ai 0.1.x

Two changes can require code updates: `create_zep_search_tool` returns a `pydantic_ai.Tool` rather than a bare function — code that invoked the return value directly should call `tool.function(ctx, query=..., **kwargs)` — and the default injected context wording follows the canonical `DEFAULT_CONTEXT_TEMPLATE`; pass `context_template=...` on `ZepDeps` to keep custom wording. See the [package changelog](https://github.com/getzep/zep/blob/main/integrations/pydantic-ai/python/CHANGELOG.md) for the full list of changes.

## Usage

Register `zep_capabilities(deps)` and the search tool when building the agent, then pass the same `ZepDeps` to each run. Both sides of every turn are persisted automatically. In this bundled form, `zep_capabilities(deps)` closes over one `ZepDeps` instance, so construct the `Agent` inside your per-conversation setup rather than sharing it across users.

```python Python
import asyncio
from pydantic_ai import Agent
from zep_cloud.client import AsyncZep
from zep_pydantic_ai import ZepDeps, create_zep_search_tool, zep_capabilities

zep = AsyncZep(api_key="your-zep-api-key")

deps = ZepDeps(
    client=zep,
    user_id="user_123",
    thread_id="thread_abc",
    first_name="Jane",
    last_name="Smith",
)

agent = Agent(
    "openai:gpt-5-mini",
    deps_type=ZepDeps,
    capabilities=zep_capabilities(deps),
    tools=[create_zep_search_tool()],
    instructions="You are a helpful assistant with long-term memory.",
)

async def main() -> None:
    result = await agent.run("What did I tell you about my project?", deps=deps)
    print(result.output)
    # The user turn and the assistant's reply are both already persisted.

asyncio.run(main())
```

### Explicit control over persistence

To control exactly when the assistant's reply reaches Zep, register the history processor directly and call `persist_run` yourself after the run completes:

```python Python
from pydantic_ai import Agent
from pydantic_ai.capabilities import ProcessHistory
from zep_pydantic_ai import ZepDeps, persist_run, zep_history_processor

agent = Agent(
    "openai:gpt-5-mini",
    deps_type=ZepDeps,
    capabilities=[ProcessHistory(zep_history_processor)],
    instructions="You are a helpful assistant with long-term memory.",
)

result = await agent.run("What did I tell you about my project?", deps=deps)
# Persist the assistant's reply (the user turn was already persisted).
await persist_run(deps, result.new_messages())
```

`persist_run` sends only assistant text — tool-call and tool-return scaffolding is skipped — so Zep records one clean assistant message per turn. It is not needed when the agent uses `zep_capabilities(deps)`.

## On-demand graph search

Beyond the automatic context injection, `create_zep_search_tool()` returns a model-callable `pydantic_ai.Tool` over `graph.search`; pass it directly in `tools=[...]`. The model decides when to look up specific facts, entities, or prior episodes, and the tool returns a formatted text summary of the matching results. By default it searches the current user's graph; pass `graph_id=...` to target a shared standalone graph.

Every search parameter (`scope`, `reranker`, `limit`, `mmr_lambda`, `center_node_uuid`) is exposed to the model in the tool's JSON schema by default, with documented defaults. Two constructor arguments override this per deployment: `pinned_params` fixes a parameter to a constant value and hides it from the schema, and `hidden_params` hides a parameter without pinning it, so Zep's server-side default applies:

```python
# Model chooses scope/reranker/limit/mmr_lambda/center_node_uuid freely.
tool = create_zep_search_tool()

# Pin scope to "nodes" and limit to 5 — hidden from the model, always sent.
tool = create_zep_search_tool(pinned_params={"scope": "nodes", "limit": 5})

# Hide mmr_lambda from the schema; Zep applies its own default when omitted.
tool = create_zep_search_tool(hidden_params={"mmr_lambda"})
```

The `scope`, `reranker`, and `limit` constructor arguments are back-compat aliases that pin (and hide) those parameters; prefer `pinned_params` in new code. `search_filters` and `bfs_origin_node_uuids` are constructor-only — their complex shapes are not exposed to the model.

## Memory vs tools

The integration combines two retrieval paths on the same agent:

| Path                    | How                                                            | When it fires                                           |
| ----------------------- | -------------------------------------------------------------- | ------------------------------------------------------- |
| **Automatic injection** | `zep_history_processor` (included in `zep_capabilities(deps)`) | Before every model request — prepends the context block |
| **On-demand search**    | `create_zep_search_tool()`                                     | When the model chooses to call it for a specific lookup |

Injection grounds each turn with cross-session context; the search tool lets the model actively dig for specific details.

## Provisioning

`ensure_user` and `ensure_thread` provision the Zep user and thread out-of-band, before the first turn — useful for onboarding flows that want genuine failures (auth, network, 5xx) to raise loudly rather than degrade silently:

```python
from zep_pydantic_ai import ensure_thread, ensure_user

async def setup_user(zep_client, user_id: str) -> None:
    ...  # e.g. configure per-user ontology

created = await ensure_user(
    zep,
    user_id="user_123",
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",
    on_created=setup_user,  # fires exactly once, only on real creation
)
await ensure_thread(zep, thread_id="thread_abc", user_id="user_123")
```

Both helpers are create-then-catch-conflict: they treat an "already exists" conflict as success (returning `False`), return `True` on genuine creation, and propagate genuine failures. Use the `on_created` hook (a `UserSetupHook`) to configure per-user resources — a custom ontology, custom extraction instructions, or user summary instructions — exactly once, on real creation; see [customizing graph structure](/customizing-graph-structure) for the available options. If `on_created` raises, that exception propagates even though the user was created, so make the hook idempotent.

Calling these helpers is optional: the history processor runs the same logic lazily on the turn path, wrapped so that a genuine failure there is logged and degrades to no-memory rather than breaking the run.

## Custom context building

Set `context_builder` on `ZepDeps` to replace the default context retrieval with custom logic — for example, searching a different graph, applying filters, or combining multiple sources:

```python
from zep_pydantic_ai import ContextInput, ZepDeps

async def my_builder(ctx: ContextInput) -> str | None:
    results = await ctx.zep.graph.search(
        user_id=ctx.user_id,
        query=ctx.user_message,
        scope="edges",
    )
    if not results.edges:
        return None
    return "\n".join(edge.fact for edge in results.edges)

deps = ZepDeps(client=zep, user_id="u", thread_id="t", context_builder=my_builder)
```

`ContextInput` is a frozen dataclass bundling `zep` (the `AsyncZep` client), `user_id`, `thread_id`, `user_message`, and `run_context` (the Pydantic AI `RunContext` for the turn). Returning `None` skips injection for that turn.

When `context_builder` is set, message persistence (`add_messages` without `return_context`) and the builder run concurrently, with per-side failure isolation:

* If the builder raises, a warning is logged and context injection is skipped for that turn — persistence still completes.
* If persistence raises, a warning is logged and the turn is not marked as persisted (so it retries on the next model request) — a successful builder result is still injected.

## Context template

`context_template` on `ZepDeps` controls how retrieved context is wrapped before injection. It must contain a literal `{context}` placeholder, rendered via plain string replacement (`template.replace("{context}", context)`, never `str.format`), so context text containing `{`, `}`, or `%` is always safe to inject:

```python
deps = ZepDeps(
    client=zep,
    user_id="u",
    thread_id="t",
    context_template="Relevant memory:\n{context}",
)
```

The default is `DEFAULT_CONTEXT_TEMPLATE`, an explicit `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block with canonical wording shared across Zep's framework integrations.

## Configuration options

### ZepDeps

| Field              | Type                     | Required | Default                    | Description                                                                                       |
| ------------------ | ------------------------ | -------- | -------------------------- | ------------------------------------------------------------------------------------------------- |
| `client`           | `AsyncZep`               | Yes      | —                          | Initialized Zep async client (caller owns its lifecycle)                                          |
| `user_id`          | `str`                    | Yes      | —                          | Zep user ID (one user graph)                                                                      |
| `thread_id`        | `str`                    | Yes      | —                          | Zep thread ID for the conversation                                                                |
| `first_name`       | `str`                    | No       | `None`                     | User first name (recommended; anchors the user node)                                              |
| `last_name`        | `str`                    | No       | `None`                     | User last name                                                                                    |
| `email`            | `str`                    | No       | `None`                     | User email (helps identity resolution)                                                            |
| `user_name`        | `str`                    | No       | `None`                     | Display name for persisted user messages (defaults to first + last)                               |
| `assistant_name`   | `str`                    | No       | `"Assistant"`              | Display name for persisted assistant messages                                                     |
| `ignore_roles`     | `list[str]`              | No       | `None`                     | Roles to exclude from graph ingestion                                                             |
| `context_builder`  | `ContextBuilder \| None` | No       | `None`                     | Custom async context-retrieval callable (see [custom context building](#custom-context-building)) |
| `context_template` | `str`                    | No       | `DEFAULT_CONTEXT_TEMPLATE` | Template wrapping injected context; must contain a literal `{context}` placeholder                |

### create\_zep\_search\_tool

Constructor arguments (returns a `pydantic_ai.Tool[ZepDeps]`):

| Parameter               | Type                     | Default              | Description                                                                                 |
| ----------------------- | ------------------------ | -------------------- | ------------------------------------------------------------------------------------------- |
| `graph_id`              | `str \| None`            | `None`               | Standalone graph to search; when unset, searches the current user's graph                   |
| `pinned_params`         | `dict[str, Any] \| None` | `None`               | Fix a search parameter to a value; hidden from the model schema                             |
| `hidden_params`         | `set[str] \| None`       | `None`               | Hide a search parameter from the schema without pinning (Zep's server-side default applies) |
| `search_filters`        | `dict[str, Any] \| None` | `None`               | Constructor-only Zep search filters (`node_labels`, `edge_types`, etc.)                     |
| `bfs_origin_node_uuids` | `list[str] \| None`      | `None`               | Constructor-only node UUIDs for BFS seeding                                                 |
| `name`                  | `str`                    | `"zep_search"`       | Tool name exposed to the model                                                              |
| `description`           | `str`                    | Built-in description | Tool description exposed to the model                                                       |
| `scope`                 | `Scope \| None`          | `None`               | Back-compat alias for `pinned_params={"scope": scope}`                                      |
| `reranker`              | `Reranker \| None`       | `None`               | Back-compat alias for `pinned_params={"reranker": reranker}`                                |
| `limit`                 | `int \| None`            | `None`               | Back-compat alias for `pinned_params={"limit": limit}`                                      |

Model-exposed search parameters (when not pinned or hidden), with their defaults:

| Parameter          | Type                                                                                 | Default   | Description                                                  |
| ------------------ | ------------------------------------------------------------------------------------ | --------- | ------------------------------------------------------------ |
| `scope`            | `"edges" \| "nodes" \| "episodes" \| "observations" \| "thread_summaries" \| "auto"` | `"edges"` | What to search                                               |
| `reranker`         | `"rrf" \| "mmr" \| "node_distance" \| "episode_mentions" \| "cross_encoder"`         | `"rrf"`   | Result ordering (ignored for `scope="auto"`)                 |
| `limit`            | `int`                                                                                | `10`      | Maximum results (clamped to Zep's ceiling of 50)             |
| `mmr_lambda`       | `float`                                                                              | —         | Diversity/relevance balance; only used when `reranker="mmr"` |
| `center_node_uuid` | `str`                                                                                | —         | Center node for `reranker="node_distance"`                   |

## Best practices

* **Construct one `ZepDeps` per conversation** and reuse a single `AsyncZep` client across runs
* **Pass real names** so Zep can anchor the user's identity node in the graph
* **Use `zep_capabilities(deps)`** so the assistant's reply is persisted automatically; call `persist_run` only when you register `ProcessHistory` directly and want explicit control
* **Provision up front in onboarding flows** with `ensure_user` / `ensure_thread` so misconfiguration raises before the agent ever runs
* **Allow time for indexing** — Zep extracts knowledge asynchronously, so facts from a turn are not instantly searchable

## Next steps

* Explore [customizing graph structure](/customizing-graph-structure) for advanced knowledge organization
* Learn about [searching the graph](/searching-the-graph) and how to tune search
* See [code examples](https://github.com/getzep/zep/tree/main/integrations/pydantic-ai/python/examples) for additional patterns