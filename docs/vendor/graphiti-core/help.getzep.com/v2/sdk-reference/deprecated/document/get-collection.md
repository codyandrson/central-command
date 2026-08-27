> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Gets a DocumentCollection

GET https://api.getzep.com/api/v2/collections/{collectionName}

Returns a DocumentCollection if it exists.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/document/get-collection

## Request

### Path parameters

- `collectionName` (string, required) — Name of the Document Collection

## Response

### 200

OK

- `created_at` (string, optional)
- `description` (string, optional)
- `document_count` (integer, optional)
- `document_embedded_count` (integer, optional)
- `embedding_dimensions` (integer, optional)
- `embedding_model_name` (string, optional)
- `is_auto_embedded` (boolean, optional)
- `is_indexed` (boolean, optional)
- `is_normalized` (boolean, optional)
- `metadata` (map from string to any, optional)
- `name` (string, optional)
- `updated_at` (string, optional)
- `uuid` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "created_at": "2023-11-10T09:15:00Z",
  "description": "Collection of user-uploaded product manuals and guides.",
  "document_count": 1250,
  "document_embedded_count": 1200,
  "embedding_dimensions": 768,
  "embedding_model_name": "text-embedding-ada-002",
  "is_auto_embedded": true,
  "is_indexed": true,
  "is_normalized": false,
  "metadata": {
    "owner": "product-team",
    "region": "us-east-1"
  },
  "name": "product_manuals",
  "updated_at": "2024-05-20T16:45:00Z",
  "uuid": "a3f1c9d2-7b4e-4f8a-9d3e-2b6f7c8e9a12"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.document.getCollection("collectionName");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.document.get_collection(
    collection_name="collectionName",
)

```

```go
package example

import (
    context "context"

    client "github.com/getzep/zep-go/client"
)

func do() {
    client := client.NewClient()
    client.Document.GetCollection(
        context.TODO(),
        "collectionName",
    )
}

```