> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# CRUD Operations

The Graphiti library uses eight core classes to add data to your graph:

* `Node`
* `EpisodicNode`
* `EntityNode`
* `Edge`
* `EpisodicEdge`
* `EntityEdge`
* `CommunityNode`
* `CommunityEdge`

The generic `Node` and `Edge` classes are abstract base classes, and the other four classes inherit from them.
Each of `EpisodicNode`, `EntityNode`, `EpisodicEdge`, and `EntityEdge` have fully supported CRUD operations.

The save method performs a find or create based on the uuid of the object, and will add or update any other data from the class to the graph.
A driver must be provided to the save method. The Entity Node save method is shown below as a sample.

```python
    async def save(self, driver: AsyncDriver):
        result = await driver.execute_query(
            """
        MERGE (n:Entity {uuid: $uuid})
        SET n = {uuid: $uuid, name: $name, name_embedding: $name_embedding, summary: $summary, created_at: $created_at}
        RETURN n.uuid AS uuid""",
            uuid=self.uuid,
            name=self.name,
            summary=self.summary,
            name_embedding=self.name_embedding,
            created_at=self.created_at,
        )

        logger.info(f'Saved Node to neo4j: {self.uuid}')

        return result
```

Graphiti also supports hard deleting nodes and edges using the delete method, which also requires a driver.

```python
    async def delete(self, driver: AsyncDriver):
        result = await driver.execute_query(
            """
        MATCH (n:Entity {uuid: $uuid})
        DETACH DELETE n
        """,
            uuid=self.uuid,
        )

        logger.info(f'Deleted Node: {self.uuid}')

        return result
```

Finally, Graphiti also provides class methods to get nodes and edges by uuid.
Note that because these are class methods they are called using the class rather than an instance of the class.

```python
    async def get_by_uuid(cls, driver: AsyncDriver, uuid: str):
        records, _, _ = await driver.execute_query(
            """
        MATCH (n:Entity {uuid: $uuid})
        RETURN
            n.uuid As uuid, 
            n.name AS name, 
            n.created_at AS created_at, 
            n.summary AS summary
        """,
            uuid=uuid,
        )

        nodes: list[EntityNode] = []

        for record in records:
            nodes.append(
                EntityNode(
                    uuid=record['uuid'],
                    name=record['name'],
                    labels=['Entity'],
                    created_at=record['created_at'].to_native(),
                    summary=record['summary'],
                )
            )

        logger.info(f'Found Node: {uuid}')

        return nodes[0]
```