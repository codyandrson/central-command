> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Mastra integration

[Mastra](https://mastra.ai) agents using Zep gain long-term memory backed by a temporal knowledge graph. The `@getzep/zep-mastra` package provides two complementary surfaces:

* **Automatic memory (recommended)** — `createZepProcessors` builds a `ZepInputProcessor`/`ZepOutputProcessor` pair that plugs into Mastra's native `inputProcessors`/`outputProcessors` pipeline. Context is injected and turns are persisted on every call, with no tool-calling round-trip.
* **Tools** — `createZepToolset` builds `zepRemember`/`zepSearch`/`zepContext` tools that let the model decide when to persist or recall. Use them when you want the model in the loop, or alongside the processors.

## Core benefits

* **Automatic memory loop**: Processors inject Zep's context block before each model call and persist each completed turn after it
* **Model-in-the-loop tools**: `zepRemember`, `zepSearch`, and `zepContext` drop straight into an `Agent`'s `tools` record
* **Per-call identity**: Resolve `userId`/`threadId` from Mastra's `requestContext` so one processor or tool instance serves many end users
* **User and standalone graphs**: Bind to a user's personal graph or a shared knowledge base
* **Graceful degradation**: A Zep outage is logged and surfaced as a non-fatal result — it never crashes the host agent

## How it works

The processors sit on opposite sides of the model call:

1. **`ZepInputProcessor`** runs before the model is called. It extracts the latest user message, retrieves a Zep context block (`thread.getUserContext`, or a custom `contextBuilder`), wraps it with `contextTemplate`/`formatContext`, and injects it as a system message.
2. **`ZepOutputProcessor`** runs after the model responds. It persists the completed turn — the latest user message plus the assistant's response — to the bound thread via a single `thread.addMessages` call. The assistant text persisted is the final step's text; when generation ends mid-tool-loop (`finishReason === "tool-calls"`), the user message is still persisted.

Because injection and persistence sit on opposite sides of the model call, it's safe to enable both processors together — they don't interfere with each other. Every Zep call is wrapped: a missing `threadId` or any Zep failure degrades gracefully — messages pass through unchanged, a warning is logged — and the input processor never calls `abort()` or throws into the agent loop.

Zep is a temporal knowledge graph, not a row-oriented message store, so the package exposes Zep's two real operations — persist and retrieve — through processors and tools rather than a `MastraStorage` adapter, which would require CRUD operations a temporal knowledge graph can't honor faithfully.

## Installation

```bash
npm install @getzep/zep-mastra @getzep/zep-cloud @mastra/core
```

Requires Node.js 20+, `@mastra/core>=1.42.0` (peer), `@getzep/zep-cloud>=3.23.0`, and a Zep Cloud API key. Get your API key from [app.getzep.com](https://app.getzep.com).

Set up your environment variables:

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

#### Upgrading from @getzep/zep-mastra 0.1.x

One breaking change affects existing code: in 0.1.x the model's `zepSearch` schema contained only `query`; 0.2.0 also exposes `scope`, `reranker`, `limit`, `mmrLambda`, and `centerNodeUuid`. Callers using the legacy `scope`/`reranker`/`limit` constructor arguments keep the fully-pinned behavior with no code changes; tools constructed without them expose those parameters to the model. To pin explicitly, use `pinnedParams` — see [pin-or-expose search parameters](#pin-or-expose-search-parameters).

See the [CHANGELOG](https://github.com/getzep/zep/blob/main/integrations/mastra/typescript/CHANGELOG.md) for the full 0.2.0 migration notes.

## Automatic memory with processors

`createZepProcessors` builds a bound `{ inputProcessor, outputProcessor }` pair. Attach both to an agent for a guaranteed memory loop on every call:

```typescript
import { ZepClient } from "@getzep/zep-cloud";
import { Agent } from "@mastra/core/agent";
import { createZepProcessors, ensureZepUserAndThread } from "@getzep/zep-mastra";

const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });
const userId = "user-123";
const threadId = "thread-abc";

// 1. Provision the Zep user + thread before the first turn.
await ensureZepUserAndThread({ client, userId, threadId, firstName: "Jane", lastName: "Smith" });

// 2. Build the processor pair bound to that user + thread.
const { inputProcessor, outputProcessor } = createZepProcessors({ client, userId, threadId });

// 3. Attach to a Mastra agent (id and name are both required by Mastra).
const agent = new Agent({
  id: "memory-agent",
  name: "Memory Agent",
  instructions: "You have long-term memory about the user. Use it to personalize replies.",
  model: "openai/gpt-5-mini",
  inputProcessors: [inputProcessor],
  outputProcessors: [outputProcessor],
});
```

### Customizing context injection

By default the input processor retrieves the context block with `thread.getUserContext` and wraps it in `DEFAULT_CONTEXT_TEMPLATE` — the canonical `<ZEP_CONTEXT>` wrapper shared by Zep's framework integrations. Three options change that, in increasing order of control:

```typescript
const { inputProcessor, outputProcessor } = createZepProcessors({
  client,
  userId,
  threadId,
  // Replace thread.getUserContext with your own retrieval:
  contextBuilder: async ({ client, userId, threadId, userMessage }) => {
    const result = await client.graph.search({ userId, query: userMessage, scope: "edges" });
    return result.edges?.map((e) => e.fact).join("\n");
  },
  // Or just customize the wrapping template (must contain a literal `{context}`):
  contextTemplate: "Known facts about the user:\n{context}",
  // Or fully take over formatting (wins over contextTemplate):
  formatContext: (context) => `<memory>${context}</memory>`,
});
```

* **`contextBuilder`** replaces the default retrieval with your own async function. It receives a `ZepContextBuilderInput` — the client, the resolved `userId`/`threadId`, and the latest user message — and returns the context string (or `undefined` to inject nothing for that turn). The result still passes through the template or `formatContext`.
* **`contextTemplate`** customizes the wrapping text. It must contain a literal `{context}` placeholder, replaced via literal string replacement (not a format string), so braces, `%`, and `$` in the retrieved context are safe.
* **`formatContext`** takes over formatting entirely and wins over `contextTemplate`.

### Per-call identity

Pass `resolveIdentity` (a `ZepIdentityResolver`, sync or async) to resolve `userId`/`threadId` per call from Mastra's `requestContext` instead of binding a fixed identity at construction time — useful when a single processor instance serves many end users:

```typescript
const { inputProcessor, outputProcessor } = createZepProcessors({
  client,
  resolveIdentity: (requestContext) => ({
    userId: (requestContext as { userId?: string } | undefined)?.userId,
    threadId: (requestContext as { threadId?: string } | undefined)?.threadId,
  }),
});
```

The same `resolveIdentity` option is accepted by `createZepSearchTool`, `createZepRememberTool`, and `createZepContextTool` (resolved from each tool call's `context.requestContext`), and `createZepToolset` forwards it to all three tools.

If both a fixed `userId`/`threadId` and `resolveIdentity` are set, `resolveIdentity`'s result wins for whichever fields it returns; any field it omits or resolves to `undefined` falls back to the constructor-bound value.

## Provisioning with ensureZepUserAndThread

Zep requires the user and thread to exist before messages are added. Call `ensureZepUserAndThread` once, out-of-band, before the first turn. It creates then catches the conflict, so calling it repeatedly for the same user and thread is safe. Already-exists detection is typed — a 409 status, or a 400 with "already exists" — so genuine failures (auth, network, 5xx) are never mistaken for a conflict; they are logged at `warn` and reported via a `false` return, never thrown.

Pass `onUserCreated` (a `ZepUserCreatedHook`) to run one-time setup — per-user ontology, custom instructions, seeding — exactly once, only when the user is genuinely newly created:

```typescript
await ensureZepUserAndThread({
  client,
  userId,
  threadId,
  firstName: "Jane",
  lastName: "Smith",
  email: "jane@example.com",
  // Fires exactly once, only when the user is genuinely newly created —
  // e.g. configure per-user summary instructions:
  onUserCreated: async (client, userId) => {
    await client.user.addUserSummaryInstructions({
      userIds: [userId],
      instructions: [{ name: "diet", text: "Track the user's dietary preferences." }],
    });
  },
});
```

## Tools

The toolset puts the model in the loop: the agent calls a tool when it decides to persist or recall. Use it standalone or alongside the processors.

```typescript
import { ZepClient } from "@getzep/zep-cloud";
import { Agent } from "@mastra/core/agent";
import { createZepToolset, ensureZepUserAndThread } from "@getzep/zep-mastra";

const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });

// 1. Provision the Zep user + thread before the first turn.
const binding = { userId: "user-123", threadId: "thread-abc" };
await ensureZepUserAndThread({ client, ...binding, firstName: "Jane", lastName: "Smith" });

// 2. Build the tool set bound to that user + thread.
const { zepRemember, zepSearch, zepContext } = createZepToolset({ client, binding });

// 3. Attach the tools to an Agent (id and name are both required by Mastra).
const agent = new Agent({
  id: "memory-agent",
  name: "Memory Agent",
  instructions: "You have long-term memory. Store and recall user facts.",
  model: "openai/gpt-5-mini",
  tools: { zepRemember, zepSearch, zepContext },
});
```

The toolset provides three tools:

| Tool          | Zep operation                      | What it does                                                                                                                                                          |
| ------------- | ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `zepRemember` | `thread.addMessages` / `graph.add` | Persists a message via `thread.addMessages` only when a `role`, `userId`, and `threadId` are all present; otherwise the content is ingested as a fact via `graph.add` |
| `zepSearch`   | `graph.search`                     | Model-callable search over the bound graph; each search parameter can be exposed to the model, pinned, or hidden                                                      |
| `zepContext`  | `thread.getUserContext`            | Returns the prompt-ready context block assembled from the whole user graph                                                                                            |

Each tool is also exported as a standalone factory (`createZepRememberTool`, `createZepSearchTool`, `createZepContextTool`) for wiring a single tool with custom options.

Each tool has a typed input and output schema:

| Tool          | Input                                                                                                                       | Output                                 |
| ------------- | --------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| `zepRemember` | `content` (string); optional `role`; optional `name`                                                                        | `{ stored: boolean, message: string }` |
| `zepSearch`   | `query` (string, 1–400 chars); optional `scope`, `reranker`, `limit`, `mmrLambda`, `centerNodeUuid` unless pinned or hidden | `{ facts: string[], found: boolean }`  |
| `zepContext`  | none                                                                                                                        | `{ context: string, found: boolean }`  |

`zepSearch` returns `facts` as extracted strings tailored to the search scope — edge facts, `"name: summary"` for entities, episode content, and so on — with `found` set to `true` when the result is non-empty.

### Pin-or-expose search parameters

`createZepSearchTool` exposes each `graph.search` parameter to the model by default, alongside the always-required `query`, so the model can tune its own searches per call:

| Parameter        | Model-visible values                                                     | Default            |
| ---------------- | ------------------------------------------------------------------------ | ------------------ |
| `scope`          | `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto` | `edges`            |
| `reranker`       | `rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder`       | `rrf`              |
| `limit`          | number (values above 50 are clamped to 50)                               | `10`               |
| `mmrLambda`      | number                                                                   | Zep server default |
| `centerNodeUuid` | string                                                                   | Zep server default |

Each parameter is independently tri-state at construction time (`ZepSearchPinnableParams`):

* **`pinnedParams`** fixes a parameter to a constant value: hidden from the model's schema, always sent.
* **`hiddenParams`** removes a parameter from the schema *without* pinning it: omitted from the `graph.search` call entirely, so Zep's own server default applies.
* **Omitted from both** — exposed to the model with the documented default.

```typescript
// Model only ever sees `query`; scope/reranker/limit are fixed.
createZepSearchTool({
  client,
  binding: { userId },
  pinnedParams: { scope: "edges", reranker: "rrf", limit: 10 },
});

// Hide mmrLambda/centerNodeUuid from the schema without fixing a value.
createZepSearchTool({
  client,
  binding: { userId },
  hiddenParams: new Set(["mmrLambda", "centerNodeUuid"]),
});
```

`searchFilters` and `bfsOriginNodeUuids` are always constructor-only — never exposed to the model — and applied whenever set. The legacy `scope`/`reranker`/`limit` constructor arguments pin (and hide) their parameter, equivalent to the corresponding `pinnedParams` entry.

## Binding: user graph vs standalone graph

Tools and processors are bound to a graph via `userId`/`graphId` (tools take these on a `ZepBinding`; the processors take `userId`/`threadId` directly):

* **`userId`** targets a **user graph** — the home for personalized agent memory. Use it for a conversational agent that remembers an end user. Context retrieval and the `zepContext` tool also need a `threadId` (the thread scopes relevance; retrieval still spans the whole user graph).
* **`graphId`** targets a **standalone graph** — shared or domain knowledge such as a product knowledge base or runbooks. No user node, no user summary. Standalone graphs are supported by the tools; the processors are thread-oriented and expect a `userId`.

If both are set, `userId` wins. If neither is set (or `threadId` can't be resolved), tools and processors degrade gracefully instead of throwing.

## Roles

`zepRemember` accepts an arbitrary `role` string and maps it onto Zep's closed `RoleType` enum: `user`, `assistant`, `system`, `tool`, `function`, or `norole`. Host-framework role names like `human` or `ai` are coerced safely; unknown roles fall back to `norole`. The mapper is exported as `toRoleType`.

## Best practices

* **Use the processors as the default** — they guarantee context injection and persistence on every call; add tools when you want the model to decide when to persist or recall
* **Call `ensureZepUserAndThread` once** before the first turn, then reuse a single `ZepClient`
* **Pass real names** so Zep can anchor the user's identity node in the graph
* **Don't read-after-write within a turn** — Zep builds the graph asynchronously, so a just-stored fact is not instantly retrievable
* **Pass a custom `logger`** to route Zep warnings into your logging stack

## Next steps

* Explore [customizing graph structure](/customizing-graph-structure) for advanced knowledge organization
* Learn about [searching the graph](/searching-the-graph) and how to tune search
* See [code examples](https://github.com/getzep/zep/tree/main/integrations/mastra/typescript/examples) for additional patterns