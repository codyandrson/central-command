> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Reading Data from the Graph

Zep provides APIs to read Edges, Nodes, and Episodes from the graph. These elements can be retrieved individually using their `UUID`, or as lists associated with a specific `user_id` or `group_id`. The latter method returns all objects within the user's or group's graph.

Examples of each retrieval method are provided below.

## Reading Edges

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

edge = client.graph.edge.get(edge_uuid)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const edge = client.graph.edge.get(edge_uuid);
```

## Reading Nodes

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

node = client.graph.node.get_by_user(user_uuid)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const node = client.graph.node.get_by_user(user_uuid);
```

## Reading Episodes

```python Python
from zep_cloud.client import Zep

client = Zep(
    api_key=API_KEY,
)

episode = client.graph.episode.get_by_group(group_uuid)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

const episode = client.graph.episode.get_by_group(group_uuid);
```