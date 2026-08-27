> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Gets a list of DocumentCollections

GET https://api.getzep.com/api/v2/collections

Returns a list of all DocumentCollections.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/document/list-collections

## Response

### 200

OK

- `list of list of object`
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
[
  [
    {
      "created_at": "2024-05-20T10:15:30Z",
      "description": "Collection of technical documentation for the Zep Cloud API.",
      "document_count": 42,
      "document_embedded_count": 40,
      "embedding_dimensions": 768,
      "embedding_model_name": "text-embedding-ada-002",
      "is_auto_embedded": true,
      "is_indexed": true,
      "is_normalized": false,
      "metadata": {
        "owner": "engineering-team",
        "project": "zep-cloud-api"
      },
      "name": "Zep Cloud API Docs",
      "updated_at": "2024-06-01T08:45:00Z",
      "uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    }
  ]
]
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.document.listCollections();
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.document.list_collections()

```

```go
package example

import (
    context "context"

    client "github.com/getzep/zep-go/client"
)

func do() {
    client := client.NewClient()
    client.Document.ListCollections(
        context.TODO(),
    )
}

```