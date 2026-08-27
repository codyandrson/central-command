> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Google ADK integration

Google's [Agent Development Kit (ADK)](https://google.github.io/adk-docs/) agents equipped with Zep's context layer can maintain context across conversations and access personalized knowledge graphs. The `zep-adk` package provides real-time message persistence and automatic context injection for ADK agents, and ships for **Python**, **TypeScript**, and **Go**.

## Core benefits

* **Zero restructuring**: Add Zep to an existing ADK agent without changing your agent architecture
* **Shared-agent architecture**: One `Agent` definition serves all users. Per-user identity is resolved at runtime from ADK session state
* **Real-time persistence**: Both user and assistant messages are persisted to Zep on every turn, not batched at session end
* **Automatic context injection**: Zep's context block — facts, relationships, and prior knowledge — is injected into the LLM prompt before each response
* **Explicit provisioning**: Idempotent helpers create Zep users and threads once, out of band, before the first turn
* **ADK-native memory service**: Zep backs ADK's built-in `load_memory`/`preload_memory` tools through a `BaseMemoryService` implementation

## How it works

The integration hooks into ADK's agent lifecycle to persist the user's message and inject relevant context before each model call, then persist the assistant's reply afterward. Each language exposes the same capabilities through its idiomatic ADK extension points:

| Capability                    | Python                          | TypeScript                                         | Go                            |
| ----------------------------- | ------------------------------- | -------------------------------------------------- | ----------------------------- |
| Context injection (per turn)  | `ZepContextTool`                | `createZepBeforeModelCallback` or `ZepContextTool` | `NewBeforeModelCallback`      |
| Assistant persistence         | `create_after_model_callback`   | `createZepAfterModelCallback`                      | `NewAfterModelCallback`       |
| Provisioning + created signal | `ensure_user` / `ensure_thread` | `ensureUser` / `ensureThread`                      | `EnsureUser` / `EnsureThread` |
| Custom context block          | `context_builder`               | `contextBuilder`                                   | `WithContextBuilder`          |
| Injection template            | `context_template`              | `contextTemplate`                                  | `WithContextTemplate`         |
| Model-callable graph search   | `ZepGraphSearchTool`            | `ZepGraphSearchTool`                               | `NewGraphSearchTool`          |
| ADK-native memory service     | `ZepMemoryService`              | `ZepMemoryService`                                 | `NewMemoryService`            |

In Python and TypeScript, `ZepContextTool` is a `BaseTool` that hooks ADK's `process_llm_request()` lifecycle method — the same hook ADK's own `PreloadMemoryTool` uses — and is never called by the model directly. In TypeScript, use either `createZepBeforeModelCallback` or `ZepContextTool`, not both: running both persists each user message twice. Go intentionally has no tool-based injection — callbacks are the idiomatic Go ADK hook.

On each turn the context hook resolves the user's Zep identity, persists the user's message, retrieves the relevant context block, and injects it into the model's system instruction. Tool-loop continuations are skipped, so a turn is recorded in Zep exactly once. The turn path assumes the Zep user and thread already exist — provision them with the `ensure_user`/`ensure_thread` helpers before the first turn (see [Provisioning users and threads](#provisioning-users-and-threads)). If persistence targets a user or thread that doesn't exist, a warning naming the helpers is logged and the turn continues without Zep memory.

### What gets persisted

Only the **user's message** and the **model's final response** are persisted to Zep on each turn. Intermediate model outputs — such as "thinking" text emitted alongside a tool call (e.g. "Let me look that up for you.") — are not persisted. Tool calls and tool results are also excluded. This keeps the Zep thread clean: one user message and one assistant message per turn, reflecting the actual conversation rather than internal agent mechanics.

If the user message contains multiple text parts (e.g. text alongside an image), all text parts are joined. Non-text parts (images, files) are ignored — only text is sent to Zep. The Zep API rejects thread messages over 4,096 characters; the ADK integration truncates longer messages before persisting rather than dropping the turn.

## Installation

```bash Python
pip install zep-adk
```

```bash TypeScript
npm install @getzep/zep-adk @google/adk @getzep/zep-cloud
```

```bash Go
go get github.com/getzep/zep/integrations/adk/go@latest
```

Requires a Zep Cloud API key — get yours from [app.getzep.com](https://app.getzep.com) — plus the ADK runtime for your language: Python 3.11+ with `google-adk>=1.19.0,<3` and `zep-cloud>=3.23.0`, Node.js 20+ with `@google/adk` (a `^1.2.0` peer dependency), or Go 1.25+ with `google.golang.org/adk` v1.4.0. The Go package is imported as `zepadk "github.com/getzep/zep/integrations/adk/go"`.

Set up your Zep API key and Google API key:

```bash Python
export ZEP_API_KEY="your-zep-api-key"
export GOOGLE_API_KEY="your-google-api-key"
```

```bash TypeScript
export ZEP_API_KEY="your-zep-api-key"
export GOOGLE_API_KEY="your-google-api-key"
```

```bash Go
export ZEP_API_KEY="your-zep-api-key"
export GOOGLE_API_KEY="your-google-api-key"
```

#### Upgrading from earlier versions

Versions `zep-adk` 0.3.0 (Python), `@getzep/zep-adk` 0.2.0 (TypeScript), and `zepadk` 0.2.0 (Go) replaced lazy in-band resource creation with explicit provisioning. If you're upgrading:

* **Python**: The `on_user_created` constructor argument on `ZepContextTool` is removed — pass the hook as `ensure_user(..., on_created=...)`.
* **Python**: `ContextBuilder` takes a single `ContextInput` argument instead of four positional arguments.
* **TypeScript**: `ZepResourceManager` is removed — use `createZepCallbacks`, or share a `TurnDedup` instance via the `dedup` option.
* **Go**: `EnsureUser`/`EnsureThread` return `(created bool, err error)` instead of `error`.
* **All languages**: The `zep_email` session-state key is removed — pass `email` to `ensure_user`/`ensureUser`/`EnsureUser`.

For the full list of changes, see the package CHANGELOGs in the [zep-adk repository](https://github.com/getzep/zep/tree/main/integrations/adk).

## Adding Zep to an agent

Whether you're building a new agent or adding Zep to an existing one, the setup is the same: provision the Zep user and thread out of band, then wire up the context hook and the after-model callback. The Python example below shows the full runner flow; the TypeScript and Go tabs show the equivalent wiring.

```python Python
import os
from uuid import uuid4

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from zep_cloud.client import AsyncZep
from zep_adk import ZepContextTool, create_after_model_callback, ensure_user, ensure_thread

zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

# One shared agent definition — serves all users
agent = Agent(
    name="my_agent",
    model="gemini-2.5-flash",
    instruction="You are a helpful assistant with long-term memory.",
    tools=[
        # Your existing tools stay as-is.
        ZepContextTool(
            zep_client=zep,
            ignore_roles=["assistant"],      # optional — excludes assistant messages from graph ingestion
        ),
    ],
    after_model_callback=create_after_model_callback(
        zep_client=zep,
        assistant_name="my_agent",           # name shown in Zep (default: "Assistant")
        ignore_roles=["assistant"],          # optional — matches ZepContextTool setting
    ),
)

session_service = InMemorySessionService()
runner = Runner(agent=agent, app_name="my_app", session_service=session_service)

# Provision the Zep user and thread out of band, BEFORE the first turn —
# e.g. during account or session onboarding in your app. Both calls are idempotent.
session_id = f"session-{uuid4().hex[:8]}"
await ensure_user(
    zep,
    user_id="user-123",
    first_name="Jane",
    last_name="Smith",
    email="jane@example.com",                # optional — email lives on the Zep user profile
)
await ensure_thread(zep, thread_id=session_id, user_id="user-123")

# Per-user session — identity maps automatically to Zep:
#   user_id    → Zep user ID
#   session_id → Zep thread ID (Zep's knowledge graph spans all threads for a user)
await session_service.create_session(
    app_name="my_app",
    user_id="user-123",
    session_id=session_id,
    state={
        "zep_first_name": "Jane",            # recommended — anchors the identity node
        "zep_last_name": "Smith",            # optional (default: "User")
    },
)

# Send a message — persistence and context injection happen automatically
content = types.Content(role="user", parts=[types.Part(text="Hi, I work at Acme Corp.")])
async for event in runner.run_async(
    user_id="user-123", session_id=session_id, new_message=content
):
    if event.is_final_response() and event.content:
        print(event.content.parts[0].text)
```

```typescript TypeScript
import { LlmAgent } from "@google/adk";
import { ZepClient } from "@getzep/zep-cloud";
import { createZepCallbacks, ensureUser, ensureThread } from "@getzep/zep-adk";

const zep = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });

// Provision the Zep user and thread once, out of band — NOT on every turn.
await ensureUser(zep, {
  userId: "user-123",
  firstName: "Jane",
  lastName: "Smith",
  email: "jane@example.com",
});
await ensureThread(zep, { threadId: "thread-abc", userId: "user-123" });

// createZepCallbacks builds the before/after pair sharing one dedup guard —
// the recommended way to wire both callbacks together.
const { beforeModelCallback, afterModelCallback } = createZepCallbacks(zep, {
  userId: "user-123",
  threadId: "thread-abc",
  firstName: "Jane",
  lastName: "Smith",
});

const agent = new LlmAgent({
  name: "memory_agent",
  model: "gemini-2.5-flash",
  instruction: "You are a helpful assistant with long-term memory.",
  // Persist the user turn + inject the context block before each model call.
  beforeModelCallback,
  // Persist the assistant response after each model call.
  afterModelCallback,
});
```

```go Go
// Imports for the ADK runtime (llmagent, runner, tool, model) are omitted for
// brevity — see examples/main.go for the full wiring.
import zepadk "github.com/getzep/zep/integrations/adk/go"

client := zepadk.NewClientFromEnv() // nil when ZEP_API_KEY is unset -> safe no-op

// Provision the Zep user and thread out of band, before the first turn.
// Both calls are idempotent.
created, err := zepadk.EnsureUser(ctx, client, "user-123", "Jane", "Smith", "jane@example.com")
if err != nil {
    // Provisioning fails loudly — handle or surface the error.
}
if created {
    // One-time per-user setup goes here (ontology, custom instructions, etc.).
}
threadCreated, err := zepadk.EnsureThread(ctx, client, "thread-abc", "user-123")
if err != nil {
    // Provisioning fails loudly — handle or surface the error.
}
_ = threadCreated // Only user creation gates one-time setup in this example.

agent, _ := llmagent.New(llmagent.Config{
    Name:                 "assistant",
    Model:                llm, // a model.LLM, e.g. gemini.NewModel(...)
    BeforeModelCallbacks: []llmagent.BeforeModelCallback{zepadk.NewBeforeModelCallback(client)},
    AfterModelCallbacks:  []llmagent.AfterModelCallback{zepadk.NewAfterModelCallback(client)},
})

run, _ := runner.New(runner.Config{
    AppName:        "my_app",
    Agent:          agent,
    SessionService: sessions,
    MemoryService:  zepadk.NewMemoryService(client), // optional — see "Memory service" below
})
```

That's it. Every user message is persisted to Zep, relevant context is injected into the LLM prompt, and assistant responses are captured — all automatically.

The `ignore_roles` parameter shown above excludes specific message roles from graph ingestion while still storing them in the thread history. This is useful when assistant messages don't add meaningful knowledge to the graph — they're preserved for conversation context but don't create nodes or edges. Both `ZepContextTool` and `create_after_model_callback` accept `ignore_roles` (TypeScript: `ignoreRoles`). See [Ignore assistant messages](/adding-messages#ignore-assistant-messages) in the Zep docs for more detail.

### Provisioning users and threads

`ensure_user` and `ensure_thread` (TypeScript: `ensureUser`/`ensureThread`, Go: `EnsureUser`/`EnsureThread`) are explicit, idempotent provisioning helpers. Call them once — during onboarding, account creation, or before the first turn of a new conversation — **before** the agent runs. Each calls the Zep SDK's create method directly and reports whether the resource was newly created: Python and TypeScript return `True`/`true` for a new resource and `False`/`false` for one that already existed; Go returns `(created bool, err error)`. An "already exists" conflict is treated as success. Genuine failures (auth, network, 5xx) raise, so misconfiguration is caught immediately rather than silently swallowed.

Two error philosophies apply, by design:

* **Provisioning fails loudly.** `ensure_user`/`ensure_thread` raise on genuine failures, so a misconfigured API key or network problem surfaces before the agent ever runs.
* **The turn path degrades gracefully.** The callbacks and tools never raise a Zep error into the agent — failures are logged and the turn continues without Zep memory. If a persist call targets a user or thread that was never provisioned, the logged warning names `ensure_user`/`ensure_thread`.

Pass the user's `email` to `ensure_user` — the name and email on the Zep user profile are set at provisioning time, not through session state.

### Identity and session state

The integration maps ADK session metadata to Zep automatically: `user_id` becomes the Zep user ID, and `session_id` becomes the Zep thread ID. Zep's knowledge graph is **per-user, not per-thread** — it accumulates knowledge across all of a user's conversations, so when they start a new session they get context from everything Zep has learned about them.

The following session state keys are recognized (all optional):

| Key              | Default          | Description                                                                                                                           |
| ---------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `zep_first_name` | `"Anonymous"`    | User's first name. Attached as the author name on persisted messages so Zep anchors them to the identity node in the knowledge graph. |
| `zep_last_name`  | `"User"`         | User's last name.                                                                                                                     |
| `zep_user_id`    | ADK `user_id`    | Override if the Zep user ID differs from the ADK user ID.                                                                             |
| `zep_thread_id`  | ADK `session_id` | Override if the Zep thread ID differs from the ADK session ID.                                                                        |

Identity resolves by precedence: explicit construction options (`userId`/`threadId`) take precedence over the `zep_user_id`/`zep_thread_id` session-state keys, which in turn take precedence over the ADK `user_id`/`session_id`.

## Advanced usage

### Per-user setup

When `ensure_user` creates a genuinely new user, an optional hook runs exactly once — the place to configure per-user resources such as a custom ontology, custom extraction instructions, or user summary instructions. Pass the hook as `on_created` (TypeScript: `onCreated`); in Go, branch on the `created` bool that `EnsureUser` returns.

```python Python
from pydantic import Field
from zep_cloud import CustomInstruction
from zep_cloud.client import AsyncZep
from zep_cloud.external_clients.ontology import EntityModel, EntityText
from zep_cloud.types import UserInstruction
from zep_adk import ensure_user

class Company(EntityModel):
    """A company or organization the user is associated with."""
    industry: EntityText = Field(description="The company's industry", default=None)

async def setup_user(zep_client: AsyncZep, user_id: str) -> None:
    """Runs once when a new Zep user is created."""
    # Set a custom ontology for this user's knowledge graph
    await zep_client.graph.set_ontology(
        entities={"Company": Company},
        user_ids=[user_id],
    )

    # Add custom extraction instructions
    await zep_client.graph.add_custom_instructions(
        user_ids=[user_id],
        instructions=[
            CustomInstruction(
                name="purchase_intent",
                text="Extract product preferences and purchase intent.",
            )
        ],
    )

    # Configure how user summaries are generated
    await zep_client.user.add_user_summary_instructions(
        user_ids=[user_id],
        instructions=[
            UserInstruction(
                name="work_focus",
                text="Focus on the user's role, team, and active projects.",
            )
        ],
    )

await ensure_user(zep, user_id="user-123", first_name="Jane", on_created=setup_user)
```

```typescript TypeScript
import { ensureUser } from "@getzep/zep-adk";

await ensureUser(zep, {
  userId: "user-123",
  firstName: "Jane",
  lastName: "Smith",
  onCreated: async (zep, userId) => {
    // One-time setup: ontology, custom instructions, summary instructions.
  },
});
```

```go Go
// Go has no hook — use the created bool returned by EnsureUser.
created, err := zepadk.EnsureUser(ctx, client, "user-123", "Jane", "Smith", "")
if err != nil {
    // Provisioning fails loudly — handle or surface the error.
}
if created {
    // One-time setup: ontology, custom instructions, summary instructions.
}
```

The hook fires only when the user is genuinely new — not for users that already exist. If the hook raises an exception, the exception propagates; the user was still created, so retrying `ensure_user` will **not** re-run the hook. Keep the hook idempotent and re-run its logic directly to recover from a partial failure.

See [custom ontology](/customizing-graph-structure), [custom instructions](/custom-instructions), and [user summary instructions](/user-summary-instructions) for details on each API.

### Custom context builder

By default, the integration uses `thread.add_messages(return_context=True)` — a single API call that persists the message and retrieves context. This works well for most use cases.

For advanced scenarios — multi-graph searches, custom filtering, or combining multiple Zep API calls — you can provide a context builder: `context_builder` on `ZepContextTool` (Python), `contextBuilder` on `createZepBeforeModelCallback`, `ZepContextTool`, or `createZepCallbacks` (TypeScript), or `WithContextBuilder` on `NewBeforeModelCallback` (Go). The builder receives a single input object bundling everything it needs.

```python Python
import asyncio
from zep_adk import ZepContextTool, ContextInput

async def my_context_builder(ctx: ContextInput) -> str | None:
    """Custom context: combine user context with a targeted graph search."""
    user_context, search_results = await asyncio.gather(
        ctx.zep.thread.get_user_context(ctx.thread_id),
        ctx.zep.graph.search(
            user_id=ctx.user_id,
            query=ctx.user_message,
            scope="edges",
            limit=10,
        ),
    )

    parts = []
    if user_context and user_context.context:
        parts.append(user_context.context)
    if search_results and search_results.edges:
        facts = [e.fact for e in search_results.edges if e.fact]
        if facts:
            parts.append("Additional facts:\n" + "\n".join(f"- {f}" for f in facts))

    return "\n\n".join(parts) if parts else None

tool = ZepContextTool(zep_client=zep, context_builder=my_context_builder)
```

```typescript TypeScript
import { createZepBeforeModelCallback } from "@getzep/zep-adk";
import type { ContextBuilderInput } from "@getzep/zep-adk";

async function multiGraphBuilder(input: ContextBuilderInput): Promise<string | undefined> {
  const [userGraph, orgGraph] = await Promise.all([
    input.zep.graph.search({ userId: input.userId, query: input.userMessage, scope: "edges" }),
    input.zep.graph.search({ graphId: "org-kb", query: input.userMessage, scope: "edges" }),
  ]);
  const facts = [...(userGraph.edges ?? []), ...(orgGraph.edges ?? [])].map((e) => e.fact);
  return facts.length > 0 ? facts.join("\n") : undefined;
}

const beforeModelCallback = createZepBeforeModelCallback(zep, {
  userId: "user-123",
  threadId: "thread-abc",
  contextBuilder: multiGraphBuilder,
});
```

```go Go
builder := func(ctx context.Context, in zepadk.ContextInput) (string, error) {
    results, err := in.Client.Graph.Search(ctx, &zep.GraphSearchQuery{
        UserID: zep.String(in.UserID),
        Query:  in.UserMessage,
        Scope:  zep.GraphSearchScopeEdges.Ptr(),
    })
    if err != nil {
        return "", err
    }
    var facts []string
    for _, e := range results.Edges {
        facts = append(facts, e.Fact)
    }
    return strings.Join(facts, "\n"), nil
}

before := zepadk.NewBeforeModelCallback(client, zepadk.WithContextBuilder(builder))
```

When a builder is set, message persistence and context building run **concurrently** for lower latency, and each is isolated from the other's failure: if the builder fails, a warning is logged and injection is skipped, but persistence still completes; if persistence fails, the turn is not marked as persisted (so it can be retried), but a successful builder result may still be injected. Return `None` (TypeScript: `undefined`) from the builder to skip injection for that turn without affecting persistence.

The Python type signatures (both importable from `zep_adk`):

```python
ContextBuilder = Callable[[ContextInput], Awaitable[str | None]]

# ContextInput is a frozen dataclass with fields:
#   zep           — the AsyncZep client
#   user_id       — resolved Zep user ID
#   thread_id     — resolved Zep thread ID
#   user_message  — the user's latest message text
#   tool_context  — ADK session state / invocation metadata
#   llm_request   — the outgoing model request

UserSetupHook = Callable[[AsyncZep, str], Awaitable[None]]  # consumed by ensure_user(on_created=...)
```

TypeScript exports the equivalent `ContextBuilder` and `ContextBuilderInput` types; Go's builder is `func(ctx context.Context, in zepadk.ContextInput) (string, error)`.

See [advanced context block construction](/advanced-context-block-construction) and [context templates](/context-templates) for more on assembling custom context.

### Injection template

The retrieved (or built) context block is wrapped in a template before it is injected into the system instruction. The default — `DEFAULT_CONTEXT_TEMPLATE` (Python and TypeScript) or `DefaultContextTemplate` (Go) — introduces the context and wraps it in `<ZEP_CONTEXT>` tags; the wording is identical across all three languages. Override it with `context_template` / `contextTemplate` / `WithContextTemplate`:

```python Python
from zep_adk import ZepContextTool

tool = ZepContextTool(
    zep_client=zep,
    context_template="Relevant memory:\n{context}",
)
```

```typescript TypeScript
const beforeModelCallback = createZepBeforeModelCallback(zep, {
  contextTemplate: "Relevant memory:\n{context}",
});
```

```go Go
before := zepadk.NewBeforeModelCallback(client,
    zepadk.WithContextTemplate("Relevant memory:\n{context}"))
```

The template must contain a literal `{context}` placeholder. It is rendered by plain string replacement — never `str.format` or another format-string engine — so templates and context text containing `{`, `}`, `%`, or `$` are always safe to inject. In Go, `WithContextPrefix` is deprecated in favor of `WithContextTemplate`.

### Graph search tool

`ZepContextTool` injects context automatically on every turn. For cases where the model needs to **actively search** the knowledge graph — e.g. looking up specific facts, entities, or prior messages — you can add `ZepGraphSearchTool` (Go: `NewGraphSearchTool`). This is a model-callable tool: the model sees it in its tool list and decides when to invoke it.

```python Python
from zep_adk import ZepContextTool, ZepGraphSearchTool, create_after_model_callback

agent = Agent(
    name="my_agent",
    model="gemini-2.5-flash",
    instruction="...",
    tools=[
        ZepContextTool(zep_client=zep),          # automatic context every turn
        ZepGraphSearchTool(zep_client=zep),      # on-demand search
    ],
    after_model_callback=create_after_model_callback(zep_client=zep),
)
```

```typescript TypeScript
import { LlmAgent } from "@google/adk";
import {
  ZepContextTool,
  ZepGraphSearchTool,
  createZepAfterModelCallback,
} from "@getzep/zep-adk";

const agent = new LlmAgent({
  name: "my_agent",
  model: "gemini-2.5-flash",
  instruction: "...",
  tools: [
    new ZepContextTool({ zep, userId: "user-123", threadId: "thread-abc" }), // automatic context every turn
    new ZepGraphSearchTool({ zep, userId: "user-123", scope: "edges", limit: 5 }), // on-demand search
  ],
  afterModelCallback: createZepAfterModelCallback(zep, {
    userId: "user-123",
    threadId: "thread-abc",
  }),
});
```

```go Go
searchTool, _ := zepadk.NewGraphSearchTool(client) // on-demand, model-callable search

agent, _ := llmagent.New(llmagent.Config{
    Name:                 "assistant",
    Model:                llm,
    BeforeModelCallbacks: []llmagent.BeforeModelCallback{zepadk.NewBeforeModelCallback(client)}, // automatic context
    AfterModelCallbacks:  []llmagent.AfterModelCallback{zepadk.NewAfterModelCallback(client)},
    Tools:                []tool.Tool{searchTool},
})
```

The tool automatically resolves the user identity from session state, so the model only needs to provide a search query. Unless pinned, the model can also choose the `scope` (`edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto`), the `reranker` (`rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder`), `limit`, `mmr_lambda`, and `center_node_uuid` — see [search parameters](/searching-the-graph#configurable-parameters).

#### Pinning and hiding parameters

Every search parameter is independently in one of three states at construction time:

| State                 | How to set it                                                                                    | Effect                                                                                             |
| --------------------- | ------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| **Exposed** (default) | Omit the parameter                                                                               | Appears in the model's tool schema with the default below; the model chooses a value per call.     |
| **Pinned**            | Pass a concrete value (e.g. `scope="edges"`; Go: `WithToolSearchScope`, `WithToolReranker`, ...) | Hidden from the model's tool schema. Always used, even if the model would have chosen differently. |
| **Hidden**            | Pass `None`/`null` (Go: `WithHiddenParams`)                                                      | Hidden from the model's tool schema **and** omitted from the search call entirely.                 |

Defaults when exposed: `scope="edges"`, `reranker="rrf"`, `limit=10`; `mmr_lambda` and `center_node_uuid` have no default and are omitted unless the model supplies one. `search_filters` and `bfs_origin_node_uuids` (TypeScript: `searchFilters`/`bfsOriginNodeUuids`, Go: `WithToolSearchFilters`/`WithToolBFSOriginNodeUUIDs`) are always constructor-only — never exposed to the model, always applied to every search when set.

```python Python
# Pin reranker and limit; hide mmr_lambda and center_node_uuid;
# the model still chooses scope per call.
ZepGraphSearchTool(
    zep_client=zep,
    reranker="cross_encoder",                    # pinned — hidden from the model
    limit=5,                                     # pinned
    mmr_lambda=None,                             # hidden — omitted from every search
    center_node_uuid=None,                       # hidden
    search_filters={"node_labels": ["Person"]},  # constructor-only
    bfs_origin_node_uuids=["node-uuid-1"],       # constructor-only — seed BFS traversal
)
```

```typescript TypeScript
// Pin scope and limit, but let the model choose the reranker.
new ZepGraphSearchTool({ zep, userId: "user-123", scope: "edges", limit: 5 });

// Fully pinned: the model only ever sees `query`.
new ZepGraphSearchTool({
  zep,
  userId: "user-123",
  scope: "edges",
  reranker: "rrf",
  limit: 10,
  mmrLambda: null,
  centerNodeUuid: null,
});
```

```go Go
// Pin scope and limit; leave reranker, mmr_lambda, center_node_uuid exposed.
tool, _ := zepadk.NewGraphSearchTool(client,
    zepadk.WithToolSearchScope(zep.GraphSearchScopeNodes),
    zepadk.WithToolSearchLimit(5),
)

// Or: hide mmr_lambda and center_node_uuid without pinning them to a value
// (useful when the reranker is never "mmr" or "node_distance").
tool, _ = zepadk.NewGraphSearchTool(client,
    zepadk.WithHiddenParams(zepadk.SearchParamMMRLambda, zepadk.SearchParamCenterNodeUUID),
)
```

An invalid enum value sent by the model never reaches Zep and never crashes the agent: TypeScript falls back to the documented default and logs a warning; Go rejects it through ADK's schema validation and surfaces a tool error the model can correct on its next call.

#### Shared documentation graph

To search a fixed graph that all users share (e.g. a documentation knowledge base), pass `graph_id`. The tool will search that graph instead of the current user's personal graph. Use distinct `name` and `description` values when combining multiple instances:

```python
agent = Agent(
    name="my_agent",
    model="gemini-2.5-flash",
    instruction="...",
    tools=[
        ZepContextTool(zep_client=zep),
        ZepGraphSearchTool(
            zep_client=zep,
            name="search_user_memory",
            description="Search the user's knowledge graph for information from previous conversations, known facts, or general context about the user.",
        ),
        ZepGraphSearchTool(
            zep_client=zep,
            name="search_docs",
            description="Search the shared documentation knowledge base.",
            graph_id="docs-graph-123",
        ),
    ],
    after_model_callback=create_after_model_callback(zep_client=zep),
)
```

The model sees two distinct tools and chooses which to call based on the user's query.

### Memory service

All three packages implement ADK's native memory extension point: `ZepMemoryService` (Python and TypeScript) implements `BaseMemoryService`, and `NewMemoryService` (Go) returns an ADK `memory.Service`. Registered on the `Runner`, it lets ADK's built-in `load_memory`/`preload_memory` tools (Go: `ToolContext.SearchMemory`) search the calling user's Zep graph whenever the model decides memory is relevant.

The two extension points are complementary: `ZepContextTool` (or the before-model callback) **guarantees** injection — it runs on every turn regardless of what the model decides. The memory service is **model-opt-in** — the model decides, per turn, whether to call `load_memory`. Pair them: keep the context tool for always-on context, and add the memory service when you also want the model to dig further on demand, or when integrating with ADK code paths that expect a memory service (e.g. evaluation harnesses).

```python Python
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.tools import load_memory
from zep_cloud.client import AsyncZep
from zep_adk import ZepMemoryService

zep = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

agent = Agent(
    name="my_agent",
    model="gemini-2.5-flash",
    instruction="You are a helpful assistant. Use load_memory to recall prior context when relevant.",
    tools=[load_memory],
)

runner = Runner(
    agent=agent,
    app_name="my_app",
    session_service=session_service,
    memory_service=ZepMemoryService(zep=zep, scope="edges"),
)
```

```typescript TypeScript
import { Runner, LlmAgent, InMemorySessionService, LOAD_MEMORY } from "@google/adk";
import { ZepClient } from "@getzep/zep-cloud";
import { ZepMemoryService } from "@getzep/zep-adk";

const zep = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });

const agent = new LlmAgent({
  name: "memory_agent",
  model: "gemini-2.5-flash",
  instruction: "You are a helpful assistant. Use load_memory to recall relevant facts about the user.",
  tools: [LOAD_MEMORY],
});

const runner = new Runner({
  agent,
  appName: "my_app",
  sessionService: new InMemorySessionService(),
  memoryService: new ZepMemoryService({ zep, scope: "edges" }),
});
```

```go Go
run, _ := runner.New(runner.Config{
    AppName:        "my_app",
    Agent:          agent,
    SessionService: sessions,
    MemoryService:  zepadk.NewMemoryService(client),
})
// Tools reach the service through ToolContext.SearchMemory.
```

Each memory search runs `graph.search` against the calling user's graph with a configurable scope — the same six scopes as the graph search tool (`edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto`) — and maps each result into an ADK memory entry. A Zep failure is logged and returns an empty result rather than raising into the agent, so a memory lookup can never break a turn.

`add_session_to_memory` (TypeScript: `addSessionToMemory`) is a deliberate no-op: Zep already ingests each turn live via the context tool and after-model callback, so flushing the full session again would persist the same conversation into the graph twice.

In TypeScript, the memory service requires the full `Runner`, not `InMemoryRunner` — only `Runner`'s `RunnerConfig` accepts a `memoryService` option. Wiring `Runner` directly means providing a `sessionService` yourself; an `InMemorySessionService` works for development.

## Backfill strategy for existing users

If you have existing users with conversation history, you can backfill their data into Zep so they get rich context from day one. Use direct `thread.add_messages` calls for small or session-scale imports. For large historical imports, use the [Batch API](/adding-batch-data) with `thread_message` items so Zep can process the backfill as an asynchronous job.

### ID matching

**Use the same user IDs and thread IDs.** The backfill script must create Zep users and threads with the exact same IDs used in ADK:

* **User IDs** must match what you pass as `user_id` to ADK's `create_session()`. This links live sessions to the correct knowledge graph. Mismatched user IDs mean backfilled history is orphaned.
* **Thread IDs** must match the ADK `session_id` for each conversation. If a user continues an existing session after cutover, the integration uses that session ID as the Zep thread ID. If the backfill used a different thread ID, the conversation history is split — the continued thread won't see the backfilled messages in its thread context.

### Example small backfill script

This runs outside of ADK as a standalone script using the Zep Python SDK directly, with `zep-adk`'s idempotent provisioning helpers. It keeps each `thread.add_messages` call within Zep's limits: at most 30 messages per request, and below the 4,096-character hard limit per message. The sample uses a 4,000-character safety margin, matching the other integration examples. Map source-system roles to Zep's canonical roles before sending: `user`, `assistant`, `system`, `function`, `tool`, or `norole`.

```python
import asyncio
from zep_cloud.client import AsyncZep
from zep_cloud import Message
from zep_adk import ensure_user, ensure_thread

zep = AsyncZep(api_key="your-zep-api-key")
MAX_MESSAGES_PER_CALL = 30
MAX_MESSAGE_CHARS = 4000

def truncate_for_zep(content: str) -> str:
    return content[:MAX_MESSAGE_CHARS]

async def backfill_user(
    user_id: str,                          # must match ADK create_session() user_id
    first_name: str,
    last_name: str,
    conversations: list[dict],             # list of {session_id, messages} dicts
):
    # 1. Create the user (idempotent — safe to re-run)
    await ensure_user(zep, user_id=user_id, first_name=first_name, last_name=last_name)

    # 2. Load each conversation — use the original ADK session ID as the Zep thread ID
    for convo in conversations:
        thread_id = convo["session_id"]    # must match ADK session_id
        created = await ensure_thread(zep, thread_id=thread_id, user_id=user_id)
        if not created:
            continue                       # thread already backfilled

        messages = [
            Message(
                role=msg["role"],
                content=truncate_for_zep(msg["content"]),
                name=f"{first_name} {last_name}" if msg["role"] == "user" else "Assistant",
            )
            for msg in convo["messages"]
        ]
        for start in range(0, len(messages), MAX_MESSAGES_PER_CALL):
            await zep.thread.add_messages(
                thread_id=thread_id,
                messages=messages[start : start + MAX_MESSAGES_PER_CALL],
            )

    print(f"Backfilled {len(conversations)} conversations for {user_id}")

async def main():
    users = [
        {
            "user_id": "user-123",       # same ID used in ADK sessions
            "first_name": "Jane",
            "last_name": "Smith",
            "conversations": [
                {
                    "session_id": "session-abc",  # original ADK session ID
                    "messages": [
                        {"role": "user", "content": "I need help with my account settings."},
                        {"role": "assistant", "content": "I can help. What would you like to change?"},
                        {"role": "user", "content": "I want to enable two-factor authentication."},
                        {"role": "assistant", "content": "Go to Settings > Security > 2FA to enable it."},
                    ],
                },
            ],
        },
    ]
    for user in users:
        await backfill_user(**user)

asyncio.run(main())
```

After backfilling, allow time for Zep to process the messages and build knowledge graphs. Zep processes messages asynchronously — the graph won't be available instantly. For large backfills, prefer the [Batch API](/adding-batch-data) over manual sleeps and direct SDK loops.

### Transition gap

There is a window between when the backfill runs and when the Zep-integrated agent goes live. Any messages sent to existing threads during this window won't be in Zep. For most use cases this is acceptable — the knowledge graph catches up quickly once the agent is live. But if thread-level continuity is critical, consider a dual-write period: after the backfill completes but before full cutover, have your application write new messages to Zep (via the SDK directly) alongside the existing system. This ensures no messages are missed in the transition.

### Cutover checklist

1. **Run the backfill script** — Zep now has knowledge graphs for existing users
2. **Update your agent** — Add `ZepContextTool` and the after-model callback
3. **Add session state keys** — Include `zep_first_name` and `zep_last_name` in `create_session()` calls
4. **Provision at onboarding** — Call `ensure_user` (including the user's email) and `ensure_thread` in your app's onboarding or session-creation code, before the first turn of every conversation
5. **Deploy** — Existing users get rich context from their first message; new users build context over time

## Next steps

* Explore [customizing graph structure](/customizing-graph-structure) for advanced knowledge organization
* Learn about [searching the graph](/searching-the-graph) for direct graph queries and how to tune search
* See the [Zep Python SDK reference](/sdk-reference) for all available API methods