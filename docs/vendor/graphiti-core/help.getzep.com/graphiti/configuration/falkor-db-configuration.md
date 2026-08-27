> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# FalkorDB Configuration

FalkorDB configuration requires version 1.1.2 or higher.

## Installation

Install Graphiti with FalkorDB support:

```bash
pip install graphiti-core[falkordb]
```

or

```bash
uv add graphiti-core[falkordb]
```

## Docker Installation

To run FalkorDB locally, use Docker:

```bash
docker run -p 6379:6379 -p 3000:3000 -it --rm falkordb/falkordb:latest
```

This command:

* Exposes FalkorDB on port 6379 (Redis protocol)
* Provides a web interface on port 3000
* Runs in foreground mode for easy testing

## Configuration

Set the following environment variables for FalkorDB (optional):

```bash
export FALKORDB_HOST=localhost          # Default: localhost
export FALKORDB_PORT=6379              # Default: 6379
export FALKORDB_USERNAME=              # Optional: usually not required
export FALKORDB_PASSWORD=              # Optional: usually not required
```

## Connection in Python

```python
from graphiti_core import Graphiti
from graphiti_core.driver.falkordb_driver import FalkorDriver

# FalkorDB connection using FalkorDriver
falkor_driver = FalkorDriver(
    host='localhost',        # or os.environ.get('FALKORDB_HOST', 'localhost')
    port='6379',            # or os.environ.get('FALKORDB_PORT', '6379')
    username=None,          # or os.environ.get('FALKORDB_USERNAME', None)
    password=None           # or os.environ.get('FALKORDB_PASSWORD', None)
)

graphiti = Graphiti(graph_driver=falkor_driver)
```

FalkorDB uses a dedicated `FalkorDriver` and connects via Redis protocol on port 6379. Unlike Neo4j, authentication is typically not required for local FalkorDB instances.