> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# AWS Neptune Configuration

Neptune DB is Amazon's fully managed graph database service that supports both property graph and RDF data models. Graphiti integrates with Neptune for managed graph storage with automatic backups, encryption, and high availability.

## Prerequisites

Neptune DB integration requires both Neptune and Amazon OpenSearch Serverless (AOSS) services:

* **Neptune Service**: For graph data storage and Cypher query processing
* **OpenSearch Serverless**: For text search and hybrid retrieval functionality
* **AWS Credentials**: Configured via AWS CLI, environment variables, or IAM roles

For detailed setup instructions, see:

* [AWS Neptune Developer Resources](https://aws.amazon.com/neptune/developer-resources/)
* [Neptune Database Documentation](https://docs.aws.amazon.com/neptune/latest/userguide/)
* [Neptune Analytics Documentation](https://docs.aws.amazon.com/neptune-analytics/latest/userguide/)
* [OpenSearch Serverless Documentation](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless.html)

## Setup

1. Create a Neptune Database cluster in the AWS Console or via CloudFormation
2. Create an OpenSearch Serverless collection for text search
3. Configure VPC networking and security groups to allow communication between services
4. Note your Neptune cluster endpoint and OpenSearch collection endpoint

## Configuration

Set the following environment variables:

```bash
export NEPTUNE_HOST=your-neptune-cluster.cluster-xyz.us-west-2.neptune.amazonaws.com
export NEPTUNE_PORT=8182  # Optional, defaults to 8182
export AOSS_HOST=your-collection.us-west-2.aoss.amazonaws.com
```

## Installation

Install the required dependencies:

```bash
pip install graphiti-core[neptune]
```

or

```bash
uv add graphiti-core[neptune]
```

## Connection in Python

```python
import os
from graphiti_core import Graphiti
from graphiti_core.driver.neptune_driver import NeptuneDriver

# Get connection parameters from environment
neptune_uri = os.getenv('NEPTUNE_HOST')
neptune_port = int(os.getenv('NEPTUNE_PORT', 8182))
aoss_host = os.getenv('AOSS_HOST')

# Validate required parameters
if not neptune_uri or not aoss_host:
    raise ValueError("NEPTUNE_HOST and AOSS_HOST environment variables must be set")

# Create Neptune driver
driver = NeptuneDriver(
    host=neptune_uri,        # Required: Neptune cluster endpoint
    aoss_host=aoss_host,     # Required: OpenSearch Serverless collection endpoint
    port=neptune_port        # Optional: Neptune port (defaults to 8182)
)

# Pass the driver to Graphiti
graphiti = Graphiti(graph_driver=driver)
```