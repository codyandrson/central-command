> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# NVIDIA NeMo Agent Toolkit

## What is NeMo Agent Toolkit?

[NVIDIA NeMo Agent Toolkit](https://github.com/NVIDIA/NeMo-Agent-Toolkit) (NAT) is a framework-agnostic library for building AI agents. It uses a configuration-driven approach where you define agents, tools, and workflows in YAML files. NAT works alongside existing frameworks like LangChain and LlamaIndex, adding capabilities like memory and observability without modifying your agent code.

## Zep integration

See NVIDIA's official documentation: [Auto Memory Wrapper](https://docs.nvidia.com/nemo/agent-toolkit/latest/components/agents/auto-memory-wrapper/auto-memory-wrapper.html)

The Zep integration for NAT uses the **automatic memory wrapper** — a general-purpose wrapper that adds memory capabilities to any NAT agent. Rather than requiring agents to explicitly call memory tools, the wrapper intercepts agent invocations and handles memory operations transparently.

This approach guarantees that all conversations are captured and relevant context is retrieved, regardless of which agent type you use or how the agent is implemented.

## Why use automatic memory

Traditional tool-based memory requires agents to explicitly invoke memory tools, which can be unreliable. The auto memory wrapper provides:

* **Guaranteed capture** of all user messages and agent responses
* **Automatic retrieval** of relevant context before each agent call
* **Zero agent configuration** — memory operations happen transparently
* **Universal compatibility** with any agent type (ReAct, ReWOO, Tool Calling, Reasoning)

## Install dependencies

```bash pip
pip install nvidia-nat-zep-cloud
```

```bash uv
uv add nvidia-nat-zep-cloud
```

```bash poetry
poetry add nvidia-nat-zep-cloud
```

**Package information:**

* **Package**: `nvidia-nat-zep-cloud`
* **Python**: `>=3.11, <3.13`

## Quick start

### Set your API key

```bash
export ZEP_API_KEY="your-zep-api-key"
```

### Configure Zep memory

Create a configuration file that defines the Zep memory backend and wraps your agent with automatic memory:

```yaml
memory:
  zep_memory:
    _type: nat.plugins.zep_cloud/zep_memory

llm:
  nim_llm:
    _type: nim
    model_name: meta/llama-3.3-70b-instruct

functions:
  my_react_agent:
    _type: react_agent
    llm_name: nim_llm
    tool_names: [calculator]

workflow:
  _type: auto_memory_agent
  inner_agent_name: my_react_agent
  memory_name: zep_memory
  llm_name: nim_llm
```

This configuration wraps a ReAct agent with automatic memory. Every user message and agent response is captured in Zep, and relevant context is retrieved before each agent call.

## How it works

The auto memory wrapper intercepts agent invocations and handles memory operations in this sequence:

1. **User message received** — incoming message captured
2. **Memory retrieval** — relevant context fetched from Zep and injected as a system message
3. **User message stored** — message saved to Zep's thread memory
4. **Agent invocation** — wrapped agent processes request with memory context
5. **Response stored** — agent response saved to Zep
6. **Response returned** — final response sent to user

The wrapped agent is unaware of memory operations — it simply receives enriched context and produces responses.

## Configuration reference

### Required parameters

| Parameter          | Description                                     |
| ------------------ | ----------------------------------------------- |
| `inner_agent_name` | Name of the agent function to wrap              |
| `memory_name`      | Name of the memory backend (e.g., `zep_memory`) |
| `llm_name`         | Name of the LLM for memory operations           |

### Optional feature flags

All flags default to `true`:

| Parameter                            | Description                                   |
| ------------------------------------ | --------------------------------------------- |
| `save_user_messages_to_memory`       | Store user messages in Zep                    |
| `retrieve_memory_for_every_response` | Fetch relevant context before each agent call |
| `save_ai_messages_to_memory`         | Store agent responses in Zep                  |

### Zep-specific parameters

Configure memory retrieval and storage behavior:

```yaml
workflow:
  _type: auto_memory_agent
  inner_agent_name: my_react_agent
  memory_name: zep_memory
  llm_name: nim_llm

  search_params:
    top_k: 5         # Number of memory results to retrieve

  add_params:
    ignore_roles: ["assistant"]  # Zep roles to exclude from graph ingestion
```

## Multi-tenant memory isolation

Zep automatically isolates memory by user. User IDs are extracted in this priority:

1. **`user_manager.get_id()`** — production with custom auth middleware (recommended)
2. **`X-User-ID` HTTP header** — testing without middleware
3. **`"default_user"`** — fallback for local development

For production deployments, implement a custom `user_manager` that extracts user IDs from your authentication system.

## Full configuration example

```yaml
telemetry:
  tracer:
    _type: phoenix

llm:
  nim_llm:
    _type: nim
    model_name: meta/llama-3.3-70b-instruct
    temperature: 0.0
    max_tokens: 1024

memory:
  zep_memory:
    _type: nat.plugins.zep_cloud/zep_memory

function_groups:
  calculator:
    - add
    - subtract
    - multiply
    - divide

functions:
  my_react_agent:
    _type: react_agent
    llm_name: nim_llm
    tool_names: [calculator]
    system_prompt: "You are a helpful assistant with memory capabilities."

workflow:
  _type: auto_memory_agent
  inner_agent_name: my_react_agent
  memory_name: zep_memory
  llm_name: nim_llm

  # Feature flags
  save_user_messages_to_memory: true
  retrieve_memory_for_every_response: true
  save_ai_messages_to_memory: true  # Persists model replies as Zep assistant messages

  # Zep-specific parameters
  search_params:
    top_k: 5
  add_params:
    ignore_roles: ["assistant"]  # Zep roles to exclude from graph ingestion
```

## Wrapping different agent types

The auto memory wrapper works with any NeMo agent type:

#### ReAct Agent

```yaml
functions:
  my_agent:
    _type: react_agent
    llm_name: nim_llm
    tool_names: [calculator, search]

workflow:
  _type: auto_memory_agent
  inner_agent_name: my_agent
  memory_name: zep_memory
  llm_name: nim_llm
```

#### Tool Calling Agent

```yaml
functions:
  my_agent:
    _type: tool_calling_agent
    llm_name: nim_llm
    tool_names: [web_search]

workflow:
  _type: auto_memory_agent
  inner_agent_name: my_agent
  memory_name: zep_memory
  llm_name: nim_llm
```

#### ReWOO Agent

```yaml
functions:
  my_agent:
    _type: rewoo_agent
    llm_name: nim_llm
    tool_names: [calculator]

workflow:
  _type: auto_memory_agent
  inner_agent_name: my_agent
  memory_name: zep_memory
  llm_name: nim_llm
```

## Resources

* [NeMo Agent Toolkit GitHub Repository](https://github.com/NVIDIA/NeMo-Agent-Toolkit)
* [PyPI Package](https://pypi.org/project/nvidia-nat-zep-cloud/)