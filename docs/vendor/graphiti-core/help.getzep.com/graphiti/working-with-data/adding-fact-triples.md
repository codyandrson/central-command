> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Adding Fact Triples

A "fact triple" consists of two nodes and an edge between them, where the edge typically contains some fact. You can manually add a fact triple of your choosing to the graph like this:

```python
from graphiti_core.nodes import EpisodeType, EntityNode
from graphiti_core.edges import EntityEdge
import uuid
from datetime import datetime

source_name = "Bob"
target_name = "bananas"
source_uuid = "some existing UUID" # This is an existing node, so we use the existing UUID obtained from Neo4j Desktop
target_uuid = str(uuid.uuid4()) # This is a new node, so we create a new UUID
edge_name = "LIKES"
edge_fact = "Bob likes bananas"


source_node = EntityNode(
    uuid=source_uuid,
    name=source_name,
    group_id=""
)
target_node = EntityNode(
    uuid=target_uuid,
    name=target_name,
    group_id=""
)
edge = EntityEdge(
    group_id="",
    source_node_uuid=source_uuid,
    target_node_uuid=target_uuid,
    created_at=datetime.now(),
    name=edge_name,
    fact=edge_fact
)

await graphiti.add_triplet(source_node, edge, target_node)
```

When you add a fact triple, Graphiti will attempt to deduplicate your passed in nodes and edge with the already existing nodes and edges in the graph. If there are no duplicates, it will add them as new nodes and edges.

Also, you can avoid constructing `EntityEdge` or `EntityNode` objects manually by using the results of a Graphiti search (see [Searching the Graph](/graphiti/graphiti/searching)).