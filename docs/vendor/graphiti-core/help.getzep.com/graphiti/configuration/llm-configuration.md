> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# LLM Configuration

Graphiti works best with LLM services that support Structured Output (such as OpenAI and Gemini). Using other services may result in incorrect output schemas and ingestion failures, particularly when using smaller models.

Graphiti defaults to using OpenAI for LLM inference and embeddings, but supports multiple LLM providers including Azure OpenAI, Google Gemini, Anthropic, Groq, and local models via Ollama. This guide covers configuring Graphiti with alternative LLM providers.

## Azure OpenAI

**Azure OpenAI v1 API Opt-in Required for Structured Outputs**

Graphiti uses structured outputs via the `client.beta.chat.completions.parse()` method, which requires Azure OpenAI deployments to opt into the v1 API. Without this opt-in, you'll encounter 404 Resource not found errors during episode ingestion.

To enable v1 API support in your Azure OpenAI deployment, follow Microsoft's guide: [Azure OpenAI API version lifecycle](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/api-version-lifecycle?tabs=key#api-evolution).

Azure OpenAI deployments often require different endpoints for LLM and embedding services, and separate deployments for default and small models.

### Installation

```bash
pip install graphiti-core
```

### Configuration

```python
from openai import AsyncAzureOpenAI
from graphiti_core import Graphiti
from graphiti_core.llm_client import LLMConfig, OpenAIClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

# Azure OpenAI configuration - use separate endpoints for different services
api_key = "<your-api-key>"
api_version = "<your-api-version>"
llm_endpoint = "<your-llm-endpoint>"  # e.g., "https://your-llm-resource.openai.azure.com/"
embedding_endpoint = "<your-embedding-endpoint>"  # e.g., "https://your-embedding-resource.openai.azure.com/"

# Create separate Azure OpenAI clients for different services
llm_client_azure = AsyncAzureOpenAI(
    api_key=api_key,
    api_version=api_version,
    azure_endpoint=llm_endpoint
)

embedding_client_azure = AsyncAzureOpenAI(
    api_key=api_key,
    api_version=api_version,
    azure_endpoint=embedding_endpoint
)

# Create LLM Config with your Azure deployment names
azure_llm_config = LLMConfig(
    small_model="gpt-4.1-nano",
    model="gpt-4.1-mini",
)

# Initialize Graphiti with Azure OpenAI clients
graphiti = Graphiti(
    "bolt://localhost:7687",
    "neo4j",
    "password",
    llm_client=OpenAIClient(
        config=azure_llm_config,
        client=llm_client_azure
    ),
    embedder=OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            embedding_model="text-embedding-3-small-deployment"  # Your Azure embedding deployment name
        ),
        client=embedding_client_azure
    ),
    cross_encoder=OpenAIRerankerClient(
        config=LLMConfig(
            model=azure_llm_config.small_model  # Use small model for reranking
        ),
        client=llm_client_azure
    )
)
```

Make sure to replace the placeholder values with your actual Azure OpenAI credentials and deployment names.

### Environment Variables

Azure OpenAI can also be configured using environment variables:

* `AZURE_OPENAI_ENDPOINT` - Azure OpenAI LLM endpoint URL
* `AZURE_OPENAI_DEPLOYMENT_NAME` - Azure OpenAI LLM deployment name
* `AZURE_OPENAI_API_VERSION` - Azure OpenAI API version
* `AZURE_OPENAI_EMBEDDING_API_KEY` - Azure OpenAI Embedding deployment key (if different from `OPENAI_API_KEY`)
* `AZURE_OPENAI_EMBEDDING_ENDPOINT` - Azure OpenAI Embedding endpoint URL
* `AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME` - Azure OpenAI embedding deployment name
* `AZURE_OPENAI_EMBEDDING_API_VERSION` - Azure OpenAI embedding API version
* `AZURE_OPENAI_USE_MANAGED_IDENTITY` - Use Azure Managed Identities for authentication

## Google Gemini

Google's Gemini models support structured output and can be used for LLM inference, embeddings, and cross-encoding/reranking.

### Installation

```bash
pip install "graphiti-core[google-genai]"
```

### Configuration

```python
from graphiti_core import Graphiti
from graphiti_core.llm_client.gemini_client import GeminiClient, LLMConfig
from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig
from graphiti_core.cross_encoder.gemini_reranker_client import GeminiRerankerClient

# Google API key configuration
api_key = "<your-google-api-key>"

# Initialize Graphiti with Gemini clients
graphiti = Graphiti(
    "bolt://localhost:7687",
    "neo4j",
    "password",
    llm_client=GeminiClient(
        config=LLMConfig(
            api_key=api_key,
            model="gemini-2.0-flash"
        )
    ),
    embedder=GeminiEmbedder(
        config=GeminiEmbedderConfig(
            api_key=api_key,
            embedding_model="embedding-001"
        )
    ),
    cross_encoder=GeminiRerankerClient(
        config=LLMConfig(
            api_key=api_key,
            model="gemini-2.0-flash-exp"
        )
    )
)
```

The Gemini reranker uses the `gemini-2.0-flash-exp` model by default, which is optimized for low-latency classification tasks.

### Environment Variables

Google Gemini can be configured using:

* `GOOGLE_API_KEY` - Your Google API key

## Anthropic

Anthropic's Claude models can be used for LLM inference with OpenAI embeddings and reranking.

When using Anthropic for LLM inference, you still need an OpenAI API key for embeddings and reranking functionality. Make sure to set both `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` environment variables.

### Installation

```bash
pip install "graphiti-core[anthropic]"
```

### Configuration

```python
from graphiti_core import Graphiti
from graphiti_core.llm_client.anthropic_client import AnthropicClient, LLMConfig
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

# Configure Anthropic LLM with OpenAI embeddings and reranking
graphiti = Graphiti(
    "bolt://localhost:7687",
    "neo4j", 
    "password",
    llm_client=AnthropicClient(
        config=LLMConfig(
            api_key="<your-anthropic-api-key>",
            model="claude-sonnet-4-20250514",
            small_model="claude-3-5-haiku-20241022"
        )
    ),
    embedder=OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key="<your-openai-api-key>",
            embedding_model="text-embedding-3-small"
        )
    ),
    cross_encoder=OpenAIRerankerClient(
        config=LLMConfig(
            api_key="<your-openai-api-key>",
            model="gpt-4.1-nano"  # Use a smaller model for reranking
        )
    )
)
```

### Environment Variables

Anthropic can be configured using:

* `ANTHROPIC_API_KEY` - Your Anthropic API key
* `OPENAI_API_KEY` - Required for embeddings and reranking

## Groq

Groq provides fast inference with various open-source models, using OpenAI for embeddings and reranking.

When using Groq, avoid smaller models as they may not accurately extract data or output the correct JSON structures required by Graphiti. Use larger, more capable models like Llama 3.1 70B for best results.

### Installation

```bash
pip install "graphiti-core[groq]"
```

### Configuration

```python
from graphiti_core import Graphiti
from graphiti_core.llm_client.groq_client import GroqClient, LLMConfig
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

# Configure Groq LLM with OpenAI embeddings and reranking
graphiti = Graphiti(
    "bolt://localhost:7687",
    "neo4j",
    "password", 
    llm_client=GroqClient(
        config=LLMConfig(
            api_key="<your-groq-api-key>",
            model="llama-3.1-70b-versatile",
            small_model="llama-3.1-8b-instant"
        )
    ),
    embedder=OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key="<your-openai-api-key>",
            embedding_model="text-embedding-3-small"
        )
    ),
    cross_encoder=OpenAIRerankerClient(
        config=LLMConfig(
            api_key="<your-openai-api-key>",
            model="gpt-4.1-nano"  # Use a smaller model for reranking
        )
    )
)
```

### Environment Variables

Groq can be configured using:

* `GROQ_API_KEY` - Your Groq API key
* `OPENAI_API_KEY` - Required for embeddings

## Ollama (Local LLMs)

Ollama enables running local LLMs and embedding models via its OpenAI-compatible API, ideal for privacy-focused applications or avoiding API costs.

When using Ollama, avoid smaller local models as they may not accurately extract data or output the correct JSON structures required by Graphiti. Use larger, more capable models and ensure they support structured output for reliable knowledge graph construction.

Ollama provides an OpenAI-compatible API, but does not support the `/v1/responses` endpoint that `OpenAIClient` uses. Use `OpenAIGenericClient` instead, which uses the `/v1/chat/completions` endpoint with `response_format` for structured outputs—both of which Ollama supports.

### Installation

First, install and configure Ollama:

```bash
# Install Ollama (visit https://ollama.ai for installation instructions)
# Then pull the models you want to use:
ollama pull deepseek-r1:7b     # LLM
ollama pull nomic-embed-text   # embeddings
```

### Configuration

```python
from graphiti_core import Graphiti
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

# Configure Ollama LLM client using OpenAIGenericClient for compatibility
llm_config = LLMConfig(
    api_key="ollama",  # Ollama doesn't require a real API key
    model="deepseek-r1:7b",
    small_model="deepseek-r1:7b",
    base_url="http://localhost:11434/v1",
)

llm_client = OpenAIGenericClient(config=llm_config)

# Initialize Graphiti with Ollama clients
graphiti = Graphiti(
    "bolt://localhost:7687",
    "neo4j",
    "password",
    llm_client=llm_client,
    embedder=OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key="ollama",
            embedding_model="nomic-embed-text",
            embedding_dim=768,
            base_url="http://localhost:11434/v1",
        )
    ),
    cross_encoder=OpenAIRerankerClient(client=llm_client, config=llm_config),
)
```

Ensure Ollama is running (`ollama serve`) and that you have pulled the models you want to use.

## OpenAI Compatible Services

Many LLM providers offer OpenAI-compatible APIs. Use the `OpenAIGenericClient` for these services, which ensures proper schema injection for JSON output since most providers don't support OpenAI's structured output format.

When using OpenAI-compatible services, avoid smaller models as they may not accurately extract data or output the correct JSON structures required by Graphiti. Choose larger, more capable models that can handle complex reasoning and structured output.

### Installation

```bash
pip install graphiti-core
```

### Configuration

```python
from graphiti_core import Graphiti
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

# Configure OpenAI-compatible service
llm_config = LLMConfig(
    api_key="<your-api-key>",
    model="<your-main-model>",        # e.g., "mistral-large-latest"
    small_model="<your-small-model>", # e.g., "mistral-small-latest"
    base_url="<your-base-url>",       # e.g., "https://api.mistral.ai/v1"
)

# Initialize Graphiti with OpenAI-compatible service
graphiti = Graphiti(
    "bolt://localhost:7687",
    "neo4j",
    "password",
    llm_client=OpenAIGenericClient(config=llm_config),
    embedder=OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key="<your-api-key>",
            embedding_model="<your-embedding-model>", # e.g., "mistral-embed"
            base_url="<your-base-url>",
        )
    ),
    cross_encoder=OpenAIRerankerClient(
        config=LLMConfig(
            api_key="<your-api-key>",
            model="<your-small-model>",  # Use smaller model for reranking
            base_url="<your-base-url>",
        )
    )
)
```

Replace the placeholder values with your actual service credentials and model names.