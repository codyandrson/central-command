> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Neo4j Configuration

Neo4j is the primary graph database backend for Graphiti. Version 5.26 or higher is required for full functionality.

## Neo4j Community Edition

Neo4j Community Edition is free and suitable for development, testing, and smaller production workloads.

### Installation via Neo4j Desktop

To install Neo4j locally, use [Neo4j Desktop](https://neo4j.com/download/), which provides an interface to manage Neo4j instances and databases.

1. Download and install Neo4j Desktop
2. Create a new project
3. Add a new database (Local DBMS)
4. Set a password for the `neo4j` user
5. Start the database

### Docker Installation

For containerized deployments:

```bash
docker run \
    --name neo4j-community \
    -p 7474:7474 -p 7687:7687 \
    -e NEO4J_AUTH=neo4j/your_password \
    -e NEO4J_PLUGINS='["apoc"]' \
    neo4j:5.26-community
```

### Configuration

Set the following environment variables:

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=your_password
```

### Connection in Python

```python
from graphiti_core import Graphiti

graphiti = Graphiti(
    neo4j_uri="bolt://localhost:7687",
    neo4j_user="neo4j",
    neo4j_password="your_password"
)
```

## Neo4j AuraDB (Cloud)

Neo4j AuraDB is a fully managed cloud service that handles infrastructure, backups, and updates automatically.

### Setup

1. Sign up for [Neo4j Aura](https://neo4j.com/tryauradb/)
2. Create a new AuraDB instance
3. Note down the connection URI and credentials
4. Download the connection details or copy the connection string

### Configuration

AuraDB connections use the `neo4j+s://` protocol for secure connections:

```bash
export NEO4J_URI=neo4j+s://your-instance.databases.neo4j.io
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=your_generated_password
```

### Connection in Python

```python
from graphiti_core import Graphiti

graphiti = Graphiti(
    neo4j_uri="neo4j+s://your-instance.databases.neo4j.io",
    neo4j_user="neo4j",
    neo4j_password="your_generated_password"
)
```

AuraDB instances automatically include APOC procedures. No additional configuration is required for most Graphiti operations.

## Neo4j Enterprise Edition

Neo4j Enterprise Edition provides clustering, hot backups, and performance optimizations.

### Installation

Enterprise Edition requires a commercial license. Installation options include:

* **Neo4j Desktop**: Add Enterprise Edition license key
* **Docker**: Use `neo4j:5.26-enterprise` image with license
* **Server Installation**: Download from Neo4j website with valid license

### Docker with Enterprise Features

```bash
docker run \
    --name neo4j-enterprise \
    -p 7474:7474 -p 7687:7687 \
    -e NEO4J_AUTH=neo4j/your_password \
    -e NEO4J_PLUGINS='["apoc"]' \
    -e NEO4J_ACCEPT_LICENSE_AGREEMENT=yes \
    neo4j:5.26-enterprise
```

### Parallel Runtime Configuration

Enterprise Edition supports parallel runtime for improved query performance:

```bash
export USE_PARALLEL_RUNTIME=true
```

The `USE_PARALLEL_RUNTIME` feature is only available in Neo4j Enterprise Edition and larger AuraDB instances. It is not supported in Community Edition or smaller AuraDB instances.

### Connection in Python

```python
import os
from graphiti_core import Graphiti

# Enable parallel runtime for Enterprise Edition
os.environ['USE_PARALLEL_RUNTIME'] = 'true'

graphiti = Graphiti(
    neo4j_uri="bolt://localhost:7687",
    neo4j_user="neo4j",
    neo4j_password="your_password"
)
```