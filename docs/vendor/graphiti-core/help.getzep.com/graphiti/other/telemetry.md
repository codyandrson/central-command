> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Telemetry

Graphiti collects anonymous usage statistics to help us understand how the framework is being used and improve it for everyone. We believe transparency is important, so here's exactly what we collect and why.

## What We Collect

When you initialize a Graphiti instance, we collect:

* **Anonymous identifier**: A randomly generated UUID stored locally in `~/.cache/graphiti/telemetry_anon_id`
* **System information**: Operating system, Python version, and system architecture
* **Graphiti version**: The version you're using
* **Configuration choices**:
  * LLM provider type (OpenAI, Azure, Anthropic, etc.)
  * Database backend (Neo4j, FalkorDB)
  * Embedder provider (OpenAI, Azure, Voyage, etc.)

## What We Don't Collect

We are committed to protecting your privacy. We **never** collect:

* Personal information or identifiers
* API keys or credentials
* Your actual data, queries, or graph content
* IP addresses or hostnames
* File paths or system-specific information
* Any content from your episodes, nodes, or edges

## Why We Collect This Data

This information helps us:

* Understand which configurations are most popular to prioritize support and testing
* Identify which LLM and database providers to focus development efforts on
* Track adoption patterns to guide our roadmap
* Ensure compatibility across different Python versions and operating systems

By sharing this anonymous information, you help us make Graphiti better for everyone in the community.

## View the Telemetry Code

The Telemetry code [may be found here](https://github.com/getzep/graphiti/blob/main/graphiti_core/telemetry/telemetry.py).

## How to Disable Telemetry

Telemetry is **opt-out** and can be disabled at any time. To disable telemetry collection:

### Option 1: Environment Variable

```bash
export GRAPHITI_TELEMETRY_ENABLED=false
```

### Option 2: Set in your shell profile

```bash
# For bash users (~/.bashrc or ~/.bash_profile)
echo 'export GRAPHITI_TELEMETRY_ENABLED=false' >> ~/.bashrc

# For zsh users (~/.zshrc)
echo 'export GRAPHITI_TELEMETRY_ENABLED=false' >> ~/.zshrc
```

### Option 3: Set for a specific Python session

```python
import os
os.environ['GRAPHITI_TELEMETRY_ENABLED'] = 'false'

# Then initialize Graphiti as usual
from graphiti_core import Graphiti
graphiti = Graphiti(...)
```

Telemetry is automatically disabled during test runs (when `pytest` is detected).

## Technical Details

* Telemetry uses PostHog for anonymous analytics collection
* All telemetry operations are designed to fail silently - they will never interrupt your application or affect Graphiti functionality
* The anonymous ID is stored locally and is not tied to any personal information