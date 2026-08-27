> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Memory for Agent Frameworks

> Use Zep as agent memory with LangGraph, CrewAI, Google ADK, AutoGen, LiveKit, Strands, Vercel AI SDK, Eve, and other supported frameworks. Integration packages and SDK patterns handle context injection, persistence, and graph search.

Zep provides agent memory for agents you build on popular frameworks. Most integrations ship as packages that wire Zep into the framework's native patterns: they provision users and threads, inject a prompt-ready context block from the user's [Context Graph](/graph-overview), persist conversation turns, and can expose graph search as a tool the model calls on demand. Some frameworks — such as [Eve](/eve) and [ElevenLabs](/elevenlabs) — use the Zep SDK directly in the framework's own hooks, proxies, or instructions.

You keep the framework's orchestration, tools, and runtime. Zep stores and serves memory across sessions — memory of your users, your business, and the work your agents do — so agents stay consistent without you rebuilding retrieval for each stack.

If your framework is not listed below, use the [Zep SDKs](/sdk-reference) directly. The same create-user → create-thread → add-messages → get-context loop applies everywhere; see the [quick start guide](/quick-start-guide).

## Supported frameworks

* [**AG2**](/ag2-memory) — An automatic memory loop on AG2's hook system, with context injection and Zep search tools.
* [**AutoGen**](/autogen-memory) — Long-term memory classes that plug into AutoGen's Memory interface, plus tools for search and data ingestion.
* [**CrewAI**](/crewai-memory) — Storage adapters and tools so CrewAI agents carry context across executions and share a knowledge base.
* [**ElevenLabs**](/elevenlabs) — Persistent context for ElevenLabs voice agents through a custom LLM proxy.
* [**Eve**](/eve) — Channel `onMessage` + turn-scoped dynamic instructions for turn-relevant `graph.search` recall, with hooks that persist turns (SDK pattern, no integration package).
* [**Google ADK**](/google-adk-memory) — Real-time message persistence and automatic context injection for Google ADK agents in Python, TypeScript, and Go.
* [**LangGraph**](/langgraph-memory) — Durable, cross-session memory for LangGraph agents via node helpers, a pre-model hook, and a graph-search tool.
* [**LiveKit**](/livekit-memory) — Persistent memory for LiveKit voice agents, with turn persistence and context injection before each response.
* [**Mastra**](/mastra-memory) — Processors and tools that add long-term memory to Mastra agents.
* [**Microsoft Agent Framework**](/microsoft-agent-framework-memory) — A context provider that persists turns, injects context on every run, and can register a graph-search tool.
* [**NVIDIA NeMo Agent Toolkit**](/nvidia-nemo) — Automatic memory for NVIDIA NeMo Agent Toolkit agents.
* [**Pydantic AI**](/pydantic-ai-memory) — Turn persistence, native prompt context injection, and a model-callable graph-search tool for Pydantic AI.
* [**Strands**](/strands-memory) — A Strands `MemoryStore` for `MemoryManager` with context injection, batched extraction, and optional graph search.
* [**Vercel AI SDK**](/vercel-ai-memory) — Middleware, helpers, and tools for long-term memory in Vercel AI SDK (v6) applications.