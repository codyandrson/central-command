> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Kuzu DB Configuration

Kuzu is an embedded graph engine that does not require any additional setup. You can enable the Kuzu driver by installing graphiti with the Kuzu extra:

```bash
pip install graphiti-core[kuzu]
```

## Configuration

Set the following environment variables for Kuzu (optional):

```bash
export KUZU_DB=/path/to/graphiti.kuzu          # Default: :memory:
```

## Connection in Python

```python
from graphiti_core import Graphiti
from graphiti_core.driver.kuzu_driver import KuzuDriver

# Kuzu connection using KuzuDriver
kuzu_driver = KuzuDriver(
    db='/path/to/graphiti.kuzu'        # or os.environ.get('KUZU_DB', ':memory:')
)

graphiti = Graphiti(graph_driver=kuzu_driver)
```