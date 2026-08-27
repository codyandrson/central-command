> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# ElevenLabs Agents

A complete working example is available on GitHub: [elevenlabs-zep-example](https://github.com/getzep/zep/tree/main/examples/python/elevenlabs-zep-example)

[ElevenLabs Agents](https://elevenlabs.io/docs/agents-platform/overview) is a platform for building intelligent voice agents. This guide shows how to integrate Zep with ElevenLabs using a custom LLM proxy.

## Why use a proxy instead of tools

ElevenLabs supports custom tools, but using tools for context retrieval has problems:

* **Latency** — Tool calls add round-trips where the LLM decides whether to call the tool. For voice agents, this delay is noticeable.
* **Unreliable** — The LLM may skip retrieval when it shouldn't, or call it unnecessarily.

A proxy solves both problems. Context retrieval happens transparently on every request, without LLM involvement.

## Architecture

```
┌────────┐       ┌──────────┐       ┌─────────┐       ┌────────┐
│Frontend│ ◄───► │ElevenLabs│ ◄───► │LLM Proxy│ ◄───► │ OpenAI │
└────────┘       └──────────┘       └─────────┘       └────────┘
                                         ▲
                                         │
                                         ▼
                                      ┌─────┐
                                      │ Zep │
                                      └─────┘
```

The proxy sits between ElevenLabs and your LLM. On every request it:

1. Adds the user message to Zep and retrieves context in one call
2. Injects context into the system prompt
3. Forwards to the LLM and streams the response back
4. Persists the assistant response to Zep

## Implementation

### The proxy endpoint

The proxy exposes an OpenAI-compatible `/v1/chat/completions` endpoint:

```python
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()

    # ElevenLabs puts customLlmExtraBody in "elevenlabs_extra_body"
    extra = body.get("elevenlabs_extra_body", {})
    user_id = extra.get("user_id")
    conversation_id = extra.get("conversation_id")

    # Add user message to Zep and get context in one call
    user_message = get_latest_user_message(body["messages"])
    response = await zep.thread.add_messages(
        thread_id=conversation_id,
        messages=[Message(role="user", content=user_message)],
        return_context=True  # Returns context without separate call
    )

    # Inject context into system prompt
    messages = inject_context(body["messages"], response.context)

    # Stream response from LLM
    return StreamingResponse(
        stream_and_persist(messages, conversation_id)
    )
```

The key optimization is `return_context=True`, which retrieves context in the same call as adding the message.

### Frontend integration

Your frontend passes user identity via `customLlmExtraBody`:

```javascript
await conversation.startSession({
  agentId: 'your-agent-id',
  customLlmExtraBody: {
    user_id: user.id,
    conversation_id: crypto.randomUUID(),
  },
});
```

### ElevenLabs configuration

1. In your agent's **LLM** section, select **Custom LLM** and set the server URL to your proxy
2. Add an `Authorization` header for authentication
3. In **Security > Overrides**, enable **Custom LLM extra body** (required for the proxy to receive user identity)

## Production considerations

* **User identity** — Use your auth system's user ID, not random IDs
* **User metadata** — Create users in Zep during registration with `first_name`, `last_name`, `email` for better personalization
* **Cache warming** — Call `zep.user.warm(user_id)` when users arrive on your page to pre-fetch their data
* **Proxy location** — Embed the endpoint in your existing backend for direct access to user data, or deploy as a standalone service

## Learn more

* [ElevenLabs Custom LLM documentation](https://elevenlabs.io/docs/agents-platform/customization/llm/custom-llm)
* [Zep context retrieval](/retrieving-context)
* [Creating users in Zep](/users)