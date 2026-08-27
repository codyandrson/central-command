> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# LiveKit integration

The `zep-livekit` package adds long-term agent memory to [LiveKit](https://docs.livekit.io/agents/) voice agents. It wraps LiveKit's `Agent` so that completed conversation turns are persisted to Zep and relevant context is injected before each response. Choose between [user thread memory](/users) or structured [knowledge graph memory](/graph-overview).

## Core benefits

* **Persistent voice memory**: Each completed turn is stored in Zep and contributes to the user's temporal knowledge graph
* **Automatic context injection**: Relevant context is retrieved and added as a system message before the agent's next response
* **Two access patterns**: `ZepUserAgent` for thread-based conversation memory, `ZepGraphAgent` for direct knowledge graph access
* **Drop-in replacement**: Both classes subclass LiveKit's `Agent` and accept all standard `Agent` parameters

## How it works

LiveKit's `AgentSession` owns the audio pipeline — speech-to-text, voice activity detection, turn detection, and text-to-speech. Zep does not touch audio. Instead, the Zep agent hooks into LiveKit's turn lifecycle and runs a write-then-read cycle on each completed user turn:

1. **Persist the turn** — when LiveKit fires `on_user_turn_completed`, the user message is written to Zep (a thread for `ZepUserAgent`, the graph for `ZepGraphAgent`). Assistant responses are captured separately via the `conversation_item_added` session event.
2. **Retrieve context** — `ZepUserAgent` folds persistence and retrieval into a single `thread.add_messages(..., return_context=True)` round-trip; `ZepGraphAgent` writes the message to the graph, then runs hybrid search across edges, nodes, and episodes.
3. **Inject context** — the retrieved context is wrapped in a [context template](#customizing-retrieved-context) and added to the turn as a system message, so the LLM's next response is grounded in prior conversation.

**Allow time for indexing**: Turns are ingested and knowledge is extracted asynchronously, so facts from the current turn are not searchable within that same turn. Context retrieved on a given turn reflects knowledge extracted from earlier turns.

## Installation

```bash pip
pip install zep-livekit zep-cloud "livekit-agents[openai,silero]>=1.0.0"
```

```bash uv
uv add zep-livekit zep-cloud "livekit-agents[openai,silero]>=1.0.0"
```

```bash poetry
poetry add zep-livekit zep-cloud "livekit-agents[openai,silero]>=1.0.0"
```

Requires Python 3.11+, `zep-livekit>=0.2.0`, LiveKit Agents v1.0+ (not v0.x), and `zep-cloud>=3.23.0`, plus a Zep Cloud API key. The examples use the v1.0 `AgentSession` API. Get your API key from [app.getzep.com](https://app.getzep.com).

Set up your environment variables:

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
export LIVEKIT_URL="your-livekit-url"
export LIVEKIT_API_KEY="your-livekit-api-key"
export LIVEKIT_API_SECRET="your-livekit-api-secret"
```

`LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` come from a [LiveKit Cloud](https://cloud.livekit.io) project or a self-hosted LiveKit server. They configure the LiveKit infrastructure your agent connects to and are unrelated to Zep.

#### Upgrading from zep-livekit 0.1.x

One breaking change affects existing code: both agents wrap injected context in the shared `DEFAULT_CONTEXT_TEMPLATE` (`<ZEP_CONTEXT>...</ZEP_CONTEXT>`) rather than the per-agent "Relevant user context:" and "Relevant knowledge from memory:" prefixes. Pass `context_template` to override the wrapper.

See the [package changelog](https://github.com/getzep/zep/blob/main/integrations/livekit/python/CHANGELOG.md) for the full list of changes.

## Agent types

* **`ZepUserAgent`**: Uses [user threads](/users) for conversation memory with automatic context injection
* **`ZepGraphAgent`**: Reads and writes a [knowledge graph](/graph-overview), optionally shaped by [custom entity models](/customizing-graph-structure)

## Identity and isolation

The example below derives a stable `user_id` from your application's auth system and scopes the `thread_id` (and `graph_id`) to the LiveKit room. Use a **stable, durable user ID** — do not derive `user_id` from the room name. A room is a per-session construct, so a room-derived `user_id` fragments a returning user's history across rooms and prevents Zep from accumulating long-term memory for that person.

Scope `thread_id` or `graph_id` to the room when you want per-session isolation while still attributing every session to the same long-lived user.

```python
# Stable identity from your auth system — survives across sessions
user_id = authenticated_user_id

# Room/session scopes the thread (or graph), not the user
thread_id = f"thread_{ctx.room.name}"
graph_id = f"graph_{ctx.room.name}"
```

## Provisioning users and threads

`ensure_user` and `ensure_thread` are idempotent create-then-catch-conflict helpers. Both return `True` when the resource is newly created and `False` when it already exists; genuine failures (auth, network, 5xx) raise. Call them before the first turn — for example during account or session onboarding — so misconfiguration surfaces loudly.

The optional `on_created` hook fires exactly once, only when the user is genuinely new — use it to seed initial facts, set custom instructions, or configure an ontology.

```python Python
from zep_livekit import ensure_user, ensure_thread

async def seed_new_user(zep_client, user_id: str) -> None:
    """Runs exactly once, right after the user is first created."""
    ...

created = await ensure_user(
    zep_client,
    user_id="user_123",
    first_name="Alice",
    on_created=seed_new_user,  # fires only for a genuinely new user
)
await ensure_thread(zep_client, thread_id="conversation_456", user_id="user_123")
```

`ZepUserAgent` also accepts `first_name`, `last_name`, `email`, and `on_created` directly and lazily calls the same helpers on the first turn, cached per agent instance. The lazy path logs and swallows failures rather than raising into the voice session — convenient for prototyping, but prefer the explicit helpers when provisioning failures need to surface.

`ZepGraphAgent` does not accept `on_created`: it is scoped to a standalone `graph_id`, not a Zep user, so there is no "user created" event to hook into. Passing it raises `TypeError`.

## User memory agent

`ZepUserAgent` stores each turn in a Zep thread and injects a context block before the next response.

```python Python
import logging
import os

from livekit import agents
from livekit.agents import AutoSubscribe
from livekit.plugins import openai, silero
from zep_cloud.client import AsyncZep
from zep_livekit import ZepUserAgent, ensure_thread, ensure_user


async def entrypoint(ctx: agents.JobContext):
    zep_client = AsyncZep(api_key=os.environ.get("ZEP_API_KEY"))

    # Stable user identity from your auth system; thread scoped to the room
    user_id = ctx.job.metadata or "user-123"
    thread_id = f"thread_{ctx.room.name}"

    # Provision the user and room-scoped thread (idempotent; genuine failures raise)
    await ensure_user(zep_client, user_id=user_id, first_name="Alice")
    await ensure_thread(zep_client, thread_id=thread_id, user_id=user_id)

    # Subscribe to audio only — a voice agent has no use for video tracks
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # AgentSession owns the audio pipeline (STT, VAD, turn detection, TTS)
    session = agents.AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-5-mini"),
        tts=openai.TTS(),
        vad=silero.VAD.load(),
    )

    # Drop-in Agent replacement that adds Zep memory
    agent = ZepUserAgent(
        zep_client=zep_client,
        user_id=user_id,
        thread_id=thread_id,
        user_message_name="Alice",
        assistant_message_name="Assistant",
        instructions="You are a helpful voice assistant with long-term memory. "
        "Reference details from previous conversations naturally.",
    )

    await session.start(agent=agent, room=ctx.room)
    logging.info("Voice assistant with Zep memory is running")


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
```

**Automatic memory integration**: `ZepUserAgent` captures each voice turn and injects relevant context from previous conversations, enabling continuity across sessions without manual memory management.

### ZepUserAgent configuration

`ZepUserAgent` accepts the following parameters in addition to all standard LiveKit `Agent` parameters (`stt`, `llm`, `tts`, `instructions`, `tools`, `chat_ctx`, etc.):

| Parameter                            | Description                                                                                                           |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| `zep_client`                         | Initialized `AsyncZep` client                                                                                         |
| `user_id`                            | User identifier for memory isolation (use a stable ID)                                                                |
| `thread_id`                          | Thread identifier for conversation continuity                                                                         |
| `user_message_name`                  | Optional name attributed to user messages in Zep                                                                      |
| `assistant_message_name`             | Optional name attributed to assistant messages in Zep                                                                 |
| `first_name` / `last_name` / `email` | Optional identity fields applied during lazy provisioning                                                             |
| `on_created`                         | Hook fired once when the Zep user is newly created on the lazy path                                                   |
| `context_builder`                    | Async callable replacing the built-in retrieval — see [customizing retrieved context](#customizing-retrieved-context) |
| `context_template`                   | Template wrapping injected context (default: `DEFAULT_CONTEXT_TEMPLATE`)                                              |

The `context_mode` parameter is deprecated and ignored; the Zep V3 context block returns a structured format and no longer accepts a mode selector.

## Knowledge graph agent

`ZepGraphAgent` writes each turn directly to a knowledge graph and retrieves context with hybrid search over edges (facts), nodes (entities), and episodes. You can optionally shape the graph with custom entity models.

```python Python
import os

from livekit import agents
from livekit.agents import AutoSubscribe
from livekit.plugins import openai, silero
from pydantic import Field
from zep_cloud import SearchFilters
from zep_cloud.client import AsyncZep
from zep_cloud.external_clients.ontology import EntityModel, EntityText
from zep_livekit import ZepGraphAgent


class Person(EntityModel):
    """A person entity for voice interactions."""

    role: EntityText = Field(description="person's role or profession", default=None)
    interests: EntityText = Field(description="topics the person is interested in", default=None)


class Topic(EntityModel):
    """A conversation topic or subject."""

    category: EntityText = Field(description="category of the topic", default=None)
    importance: EntityText = Field(description="importance to the user", default=None)


async def entrypoint(ctx: agents.JobContext):
    zep_client = AsyncZep(api_key=os.environ.get("ZEP_API_KEY"))

    # Optional: define a custom ontology for structured extraction
    await zep_client.graph.set_ontology(entities={"Person": Person, "Topic": Topic})

    # Room-scoped graph
    graph_id = f"graph_{ctx.room.name}"
    try:
        await zep_client.graph.get(graph_id)
    except Exception:
        await zep_client.graph.create(graph_id=graph_id, name="LiveKit Voice Knowledge Graph")

    # Subscribe to audio only — a voice agent has no use for video tracks
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    session = agents.AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-5-mini"),
        tts=openai.TTS(),
        vad=silero.VAD.load(),
    )

    agent = ZepGraphAgent(
        zep_client=zep_client,
        graph_id=graph_id,
        facts_limit=15,  # Max facts (edges) to retrieve
        entity_limit=8,  # Max entities (nodes) to retrieve
        episode_limit=2,  # Max episodes to retrieve
        search_filters=SearchFilters(node_labels=["Person"]),  # Constrain to Person entities
        instructions="You are a knowledgeable voice assistant. Use the provided "
        "context about entities and facts to give informed responses.",
    )

    await session.start(agent=agent, room=ctx.room)


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
```

**Search filters**: The `search_filters` parameter constrains which results the agent retrieves. Use `node_labels` to filter by entity types defined in your ontology.

**Graph memory context**: `ZepGraphAgent` writes each turn to the graph and injects relevant facts, entities, and episodes as context, grounding responses in prior conversations.

### ZepGraphAgent configuration

`ZepGraphAgent` accepts the following parameters in addition to all standard LiveKit `Agent` parameters:

| Parameter          | Description                                                                                                               |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| `zep_client`       | Initialized `AsyncZep` client                                                                                             |
| `graph_id`         | Graph identifier for knowledge storage                                                                                    |
| `user_name`        | Optional name prefixed to stored messages for attribution                                                                 |
| `facts_limit`      | Maximum facts (edges) to retrieve (default: `15`)                                                                         |
| `entity_limit`     | Maximum entities (nodes) to retrieve (default: `5`)                                                                       |
| `episode_limit`    | Maximum episodes to retrieve (default: `2`)                                                                               |
| `search_filters`   | Optional `SearchFilters` applied to graph search                                                                          |
| `reranker`         | Optional reranker for search results (default: `"rrf"`)                                                                   |
| `context_builder`  | Async callable replacing the built-in hybrid search — see [customizing retrieved context](#customizing-retrieved-context) |
| `context_template` | Template wrapping injected context (default: `DEFAULT_CONTEXT_TEMPLATE`)                                                  |

`ZepGraphAgent` has no `on_created` parameter — it is graph-scoped, with no Zep user to provision. Passing `on_created` raises `TypeError`.

## Customizing retrieved context

Both agents wrap injected context in `DEFAULT_CONTEXT_TEMPLATE` — a `<ZEP_CONTEXT>...</ZEP_CONTEXT>` block shared across Zep integrations — before adding it as a system message. Override with `context_template`: it must contain a literal `{context}` placeholder, substituted via plain string replacement (never `str.format`), so context text containing `{`, `}`, or `%` is always safe to inject.

```python Python
agent = ZepUserAgent(
    zep_client=zep_client,
    user_id="user_123",
    thread_id="conversation_456",
    context_template="Known facts about the user:\n{context}",
)
```

To replace the retrieval logic itself — a filtered graph search, a different graph, or multi-source context assembly — pass `context_builder`. On `ZepUserAgent` the builder is an async callable receiving a frozen `ContextInput` (`zep`, `user_id`, `thread_id`, `user_message`, `session`) and returning the context string, or `None` to skip injection:

```python Python
from zep_livekit import ContextInput

async def my_builder(ctx: ContextInput) -> str | None:
    results = await ctx.zep.graph.search(
        user_id=ctx.user_id,
        query=ctx.user_message,
        scope="edges",
    )
    if not results.edges:
        return None
    return "\n".join(edge.fact for edge in results.edges)

agent = ZepUserAgent(
    zep_client=zep_client,
    user_id="user_123",
    thread_id="conversation_456",
    context_builder=my_builder,
)
```

When `context_builder` is set on `ZepUserAgent`, message persistence and the builder run concurrently for lower latency, with per-side failure isolation: a builder error is logged and skips injection for that turn but does not stop persistence, and a persistence error is logged but a successful builder result is still injected.

`ZepGraphAgent` takes the analogous `context_builder` typed as `GraphContextBuilder`, receiving a `GraphContextInput` (`zep`, `graph_id`, `user_message`, `session`). Setting it fully replaces the built-in hybrid search rather than running concurrently with anything — graph message persistence happens independently, earlier in the turn.

## Graph search tool

In addition to the context injected automatically every turn, `create_graph_search_tool` builds a model-callable LiveKit function tool (via `function_tool(raw_schema=...)`, returning a `RawFunctionTool`) that lets the agent search a Zep graph on demand. Exactly one of `graph_id` or `user_id` is required: `graph_id` targets a shared standalone graph, `user_id` targets that user's personal graph. Register it through the standard `tools=[...]` parameter:

```python Python
from zep_livekit import ZepUserAgent, create_graph_search_tool

search_tool = create_graph_search_tool(zep_client, user_id="user_123")

agent = ZepUserAgent(
    zep_client=zep_client,
    user_id="user_123",
    thread_id="conversation_456",
    tools=[search_tool],
    instructions="...",
)
```

The tool exposes every `graph.search` parameter to the model by default:

| Parameter          | Values                                                                   | Default            |
| ------------------ | ------------------------------------------------------------------------ | ------------------ |
| `query`            | Natural language search query (required)                                 | —                  |
| `scope`            | `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto` | `edges`            |
| `reranker`         | `rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder`       | `rrf`              |
| `limit`            | Maximum results                                                          | `10`               |
| `mmr_lambda`       | Diversity/relevance balance for the `mmr` reranker                       | omitted when unset |
| `center_node_uuid` | Center node for `node_distance` reranking                                | omitted when unset |

Use `pinned_params` to fix a parameter to a constant value (hidden from the model, always sent), or `hidden_params` to hide a parameter without pinning it (Zep's server-side default applies). `search_filters` and `bfs_origin_node_uuids` are constructor-only and never exposed to the model.

```python Python
search_tool = create_graph_search_tool(
    zep_client,
    user_id="user_123",
    pinned_params={"scope": "edges", "limit": 5},
    hidden_params={"center_node_uuid"},
)
```

Zep failures are caught and returned as an error string to the model — the tool never raises into the voice session.

## Size limits

* Zep rejects direct thread-message payloads over 4,096 characters; LiveKit agents truncate message content to 4,000 characters before writing it, logging lengths only and never content.
* Zep rejects direct `graph.add` payloads over 10,000 characters; LiveKit graph agents truncate graph payloads to 9,900 characters before calling `graph.add`.

## Best practices

* **Use a stable user ID** — derive `user_id` from your auth system, not the room name, so a returning user's memory accumulates instead of fragmenting across sessions
* **Scope sessions with the thread or graph** — use the room name for `thread_id` or `graph_id` when you want per-session isolation, keeping `user_id` constant
* **Let LiveKit own audio** — `AgentSession` handles STT, VAD, turn detection, and TTS; Zep only persists turns and injects context
* **Allow time for indexing** — Zep extracts knowledge asynchronously, so facts from a turn are not instantly searchable

## Next steps

* Explore [customizing graph structure](/customizing-graph-structure) for advanced knowledge organization
* Learn about [searching the graph](/searching-the-graph) and how to tune search
* See [code examples](https://github.com/getzep/zep/tree/main/integrations/livekit/python/examples) for additional patterns