> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Vercel AI SDK integration

The `@getzep/zep-vercel-ai` package adds long-term memory to the [Vercel AI SDK](https://ai-sdk.dev) (v6), backed by Zep's temporal knowledge graph. It exposes Zep through three layers so you can pick the integration point that fits your call: middleware, helpers, and tools.

## Core benefits

* **Automatic context injection**: Middleware prepends Zep's context block as a system message on each new user turn
* **One write per turn**: Persist through the middleware (`persist: true`) or an `onFinish` callback — either path writes the full turn once, even across a multi-step tool loop
* **On-demand tools**: Let the model search and persist memory explicitly inside a tool loop
* **Works with `generateText` and `streamText`**: The same inject-and-persist pattern applies to both
* **Graceful degradation**: A Zep outage degrades to "no memory" and never crashes the host call

## How it works

Inject the context block via middleware, and persist the whole turn once — the tool loop calls the model once per step, so persisting from a per-step hook would fragment one turn across many writes.

The package exposes three layers:

| Layer          | Export                                                 | Use when                                                                                                                                                                                                |
| -------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Middleware** | `createZepMiddleware`                                  | You want the context block injected automatically as a system message on each new user turn. Set `persist: true` for a guaranteed persistence loop, or leave it unset and pair with `createZepOnFinish` |
| **Helpers**    | `getZepContext`, `persistZepTurn`, `createZepOnFinish` | You want explicit control. `createZepOnFinish` persists the whole turn once per turn from `onFinish`                                                                                                    |
| **Tools**      | `createZepTools`                                       | You want the model to retrieve and persist on demand inside a tool loop                                                                                                                                 |

There are two ways to persist a turn. Pass `persist: true` to `createZepMiddleware` (recommended — a single wiring point on the wrapped model), or leave the middleware injection-only and add `createZepOnFinish` to the `generateText`/`streamText` call when you need control at the call site.

Enable exactly one persistence path per call. `persist: true` and `createZepOnFinish` each write one `thread.addMessages` call per turn — enabling both double-persists every turn.

## Installation

```bash
npm install @getzep/zep-vercel-ai @getzep/zep-cloud ai zod
```

Requires Node.js 20+, `ai>=6` (the Vercel AI SDK v6; not compatible with v5), `zod` 3 or 4, `@getzep/zep-cloud>=3.23.0`, and a Zep Cloud API key. You'll also want a model provider such as `@ai-sdk/openai`. Get your API key from [app.getzep.com](https://app.getzep.com).

Set up your environment variables:

```bash
export ZEP_API_KEY="your-zep-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

#### Upgrading from @getzep/zep-vercel-ai 0.1.x

Two changes affect existing code. First, the model-facing `zepSearch` schema exposes `scope`, `reranker`, `limit`, `mmrLambda`, and `centerNodeUuid` in addition to `query` — use the `pinnedParams`/`hiddenParams` recipe in [pin-or-expose search parameters](#pin-or-expose-search-parameters) to restore the query-only schema. Second, the default injected system-message wording renders `DEFAULT_CONTEXT_TEMPLATE`; to keep the 0.1.x wording, pass `formatContext`:

```typescript
createZepMiddleware({
  client,
  threadId: "t1",
  formatContext: (context) =>
    "The following is relevant long-term memory about the user, retrieved from Zep. " +
    "Use it to personalize and ground your response.\n\n" + context,
});
```

See the [CHANGELOG](https://github.com/getzep/zep/blob/main/integrations/vercel-ai/typescript/CHANGELOG.md) for the full 0.2.0 migration notes.

## Usage with generateText

Provision the Zep user and thread, then wrap the model with `persist: true` to inject context and persist each turn — no `onFinish` wiring needed:

```typescript
import { ZepClient } from "@getzep/zep-cloud";
import { openai } from "@ai-sdk/openai";
import { generateText, stepCountIs, wrapLanguageModel } from "ai";
import {
  createZepMiddleware,
  createZepTools,
  ensureZepUserAndThread,
} from "@getzep/zep-vercel-ai";

const client = new ZepClient({ apiKey: process.env.ZEP_API_KEY! });

// 1. Provision the Zep user + thread before the first turn.
await ensureZepUserAndThread({ client, userId: "u1", threadId: "t1", firstName: "Jane" });

// 2. Wrap the model: inject the context block on each new user turn AND
//    persist the turn — no onFinish wiring needed.
const model = wrapLanguageModel({
  model: openai("gpt-5-mini"),
  middleware: createZepMiddleware({ client, threadId: "t1", persist: true }),
});

// 3. Optionally let the model search/store memory explicitly.
const tools = createZepTools(client, { binding: { userId: "u1", threadId: "t1" } });

const { text } = await generateText({
  model,
  tools,
  stopWhen: stepCountIs(5),
  prompt: "What do you remember about me?",
});
```

Prefer explicit `onFinish` wiring instead? Leave `persist` unset (the middleware stays injection-only) and pair it with `createZepOnFinish`:

```typescript
const model = wrapLanguageModel({
  model: openai("gpt-5-mini"),
  middleware: createZepMiddleware({ client, threadId: "t1" }), // injection only
});

const prompt = "What do you remember about me?";
const { text } = await generateText({
  model,
  tools,
  stopWhen: stepCountIs(5),
  prompt,
  onFinish: createZepOnFinish({ client, threadId: "t1", user: prompt }),
});
```

Don't combine `persist: true` with `createZepOnFinish` on the same call — that persists every turn twice.

If your OpenAI organization enforces Zero Data Retention, use `openai.chat('gpt-5-mini')` (Chat Completions API) instead of `openai('gpt-5-mini')`. The Responses API references server-persisted item IDs across a multi-step tool loop, which ZDR organizations reject. This is an OpenAI account constraint, not a Zep issue.

## Usage with streamText

The same pattern works unchanged for streaming. The middleware's `transformParams` runs for `stream` calls too, and both persistence paths fire once per turn: `persist: true` accumulates `text-delta` parts and persists on the stream's `finish` part, while `createZepOnFinish` fires from `onFinish` after the whole tool loop completes.

```typescript
import { streamText, wrapLanguageModel } from "ai";
import { openai } from "@ai-sdk/openai";
import { createZepMiddleware } from "@getzep/zep-vercel-ai";

const model = wrapLanguageModel({
  model: openai("gpt-5-mini"),
  middleware: createZepMiddleware({ client, threadId: "t1", persist: true }),
});

const result = streamText({
  model,
  prompt: "I just adopted a beagle named Cooper.",
});

for await (const chunk of result.textStream) process.stdout.write(chunk);
```

To set the system prompt yourself instead of using the middleware, fetch the block with `getZepContext` and persist with `persistZepTurn` (or `createZepOnFinish`) directly.

## The layers in detail

### createZepMiddleware

Returns a Vercel AI SDK `LanguageModelMiddleware` for `wrapLanguageModel`.

* **`transformParams`** fetches the context block (`thread.getUserContext`, or a custom `contextBuilder`) and prepends it as a `system` message on both `generate` and `stream` calls — but only on a genuine new user turn (detected by the last prompt message being a `user` message). On tool-loop continuation steps it injects nothing, so the block is fetched at most once per turn. The injected text is `formatContext(context)`; the default renders `DEFAULT_CONTEXT_TEMPLATE` (exported), the canonical `<ZEP_CONTEXT>` wrapper shared by Zep's framework integrations, via literal `{context}` replacement — not a format string, so braces, `%`, and `$` in the context are safe.
* **`persist`** (`ZepPersistOptions`: `boolean | { userName?, assistantName? }`) opts into a guaranteed persistence loop; the object form records speaker names. When set, the middleware implements `wrapGenerate`/`wrapStream`: after the model's final step in a turn (`finishReason !== "tool-calls"`), it persists the user's message and the final assistant text via one fire-and-forget `thread.addMessages` call. Failures on that fire-and-forget path are logged and not surfaced to the caller. When unset, `wrapGenerate`/`wrapStream` are `undefined` and the middleware is injection-only — persist with `createZepOnFinish` instead.
* **`contextBuilder`** replaces the default `thread.getUserContext` retrieval with a custom async function: `(input: ZepContextBuilderInput) => Promise<string | undefined>`, where `input` is `{ client, userId?, threadId, userMessage, params }`. Return `undefined` to inject nothing for that turn. A rejection is logged and degrades to "no context injected", never crashing the call. The builder's result still passes through `formatContext`.

Other options: `userId` (threaded to `contextBuilder`), `templateId` (custom Zep context block layout; ignored when `contextBuilder` is set), `formatContext`, and `logger`.

### createZepOnFinish

Returns an `onFinish` callback that persists the whole turn once — the user's input plus the final assistant text — via `thread.addMessages`. Because `onFinish` fires exactly once per turn for both `generateText` and `streamText`, it records exactly one user message and one assistant message and never writes intermediate tool-call preamble. Supply the user side via `user` (a string or a `(event) => string` resolver); the assistant side is taken from `event.text`.

Use this **or** `createZepMiddleware({ ..., persist: true })` — not both on the same call.

### getZepContext and persistZepTurn

Plain async functions with no framework coupling. `getZepContext` returns the prompt-ready context block string. `persistZepTurn` writes a `{ user?, assistant? }` turn; pass `{ returnContext: true }` to fold persist and retrieval into one round-trip. Zep rejects direct thread-message payloads over 4,096 characters; this helper truncates over-long content before calling Zep, with a lengths-only warning.

### createZepTools

Returns `{ zepSearch, zepRemember, zepContext }` built with the AI SDK's `tool()` and Zod schemas. Spread them into a `generateText` / `streamText` `tools` record so the model decides when to retrieve or persist. Each tool is also exported as a standalone factory (`createZepSearchTool`, `createZepRememberTool`, `createZepContextTool`).

The tools return typed results: `zepSearch` → `{ facts: string[], found: boolean }`, `zepRemember` → `{ stored: boolean, message: string }`, and `zepContext` → `{ context: string, found: boolean }`. `zepSearch`'s `facts` are extracted strings tailored to the search scope (edge facts, `"name: summary"` for entities, episode content, and so on).

`zepRemember` truncates over-long content rather than dropping it, with a lengths-only warning: messages are capped at `MESSAGE_MAX_CHARS` (4,096 characters, Zep's thread-message limit) and graph facts at `GRAPH_MAX_CHARS` (10,000 characters, Zep's `graph.add` limit). The helper is exported as `truncateForZep`.

#### Pin-or-expose search parameters

`createZepSearchTool`'s Zod input schema exposes every `graph.search` knob to the model by default, alongside the always-required `query`, so the model can tune its own searches per call:

| Parameter        | Model-visible values                                                     | Default            |
| ---------------- | ------------------------------------------------------------------------ | ------------------ |
| `scope`          | `edges`, `nodes`, `episodes`, `observations`, `thread_summaries`, `auto` | `edges`            |
| `reranker`       | `rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder`       | `rrf`              |
| `limit`          | number (values above 50 are clamped to 50)                               | `10`               |
| `mmrLambda`      | number                                                                   | Zep server default |
| `centerNodeUuid` | string                                                                   | Zep server default |

Each parameter is independently tri-state at construction time (parameter names are typed as `ZepSearchParamName`):

* **`pinnedParams`** fixes a parameter to a constant value: hidden from the model's schema, always sent.
* **`hiddenParams`** removes a parameter from the schema *without* pinning it: omitted from the `graph.search` call entirely, so Zep's own server default applies.
* **Omitted from both** — exposed to the model with the documented default.

```typescript
// Model chooses scope/reranker/limit/mmrLambda/centerNodeUuid (default).
const tool = createZepSearchTool({ client, binding: { userId: "u1" } });

// Pin scope and limit; hide the other knobs so the model only sees `query`.
const pinnedTool = createZepSearchTool({
  client,
  binding: { userId: "u1" },
  pinnedParams: { scope: "edges", limit: 10 },
  hiddenParams: ["reranker", "mmrLambda", "centerNodeUuid"],
});
```

`searchFilters` and `bfsOriginNodeUuids` are always constructor-only — never exposed to the model — and applied whenever set. The legacy `scope`, `reranker`, and `limit` constructor arguments pin (and hide) their parameter, equivalent to the corresponding `pinnedParams` entry.

## Binding: user graph vs standalone graph

`createZepTools` is bound to a graph via a `ZepBinding`:

* **`userId`** targets a **user graph** — the home for personalized agent memory. `zepContext` and the middleware also need a `threadId` (the thread scopes relevance; retrieval still spans the whole user graph).
* **`graphId`** targets a **standalone graph** — shared or domain knowledge such as a product knowledge base or runbooks.

If both are set, `userId` wins. If neither is set, tools return a graceful "not configured" result instead of throwing.

## Provisioning with ensureZepUserAndThread

Idempotently creates the Zep user and thread before the first turn (create-then-catch-conflict — an already-exists response is treated as success). Pass `onUserCreated: async (client, userId) => { ... }` to run one-time setup — per-user ontology, custom instructions, seeding a user summary — exactly once, immediately after the user is genuinely created (never on an already-exists path). Hook errors are logged, not thrown: the function's `Promise<boolean>` keeps meaning "the user and thread are ready", not "the hook succeeded".

```typescript
await ensureZepUserAndThread({
  client,
  userId: "u1",
  threadId: "t1",
  firstName: "Jane",
  onUserCreated: async (zep, userId) => {
    // e.g. seed an initial graph fact or send a welcome event for this user.
    await zep.graph.add({ userId, type: "text", data: "New user onboarded." });
  },
});
```

## Roles

`zepRemember` accepts an arbitrary `role` string and maps it onto Zep's closed `RoleType` enum: `user`, `assistant`, `system`, `tool`, `function`, or `norole`. Loose role names like `human` or `ai` are coerced safely; unknown roles fall back to `norole`. The mapper is exported as `toRoleType`.

## Best practices

* **Pick one persistence path per call** — `persist: true` on the middleware (single wiring point) or `createZepOnFinish` (control at the call site); enabling both double-persists every turn
* **Call `ensureZepUserAndThread` once** before the first turn, then reuse a single `ZepClient`
* **Use AI SDK v6** — this package is not compatible with v5
* **Don't read-after-write within a turn** — Zep builds the graph asynchronously, so a just-stored fact is not instantly retrievable

## Next steps

* Explore [customizing graph structure](/customizing-graph-structure) for advanced knowledge organization
* Learn about [searching the graph](/searching-the-graph) and how to tune search
* See [code examples](https://github.com/getzep/zep/tree/main/integrations/vercel-ai/typescript/examples) for additional patterns