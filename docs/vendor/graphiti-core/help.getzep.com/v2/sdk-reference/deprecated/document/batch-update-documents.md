> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Batch Updates Documents in a DocumentCollection

PATCH https://api.getzep.com/api/v2/collections/{collectionName}/documents/batchUpdate
Content-Type: application/json

Updates Documents in a specified DocumentCollection.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/document/batch-update-documents

## Request

### Path parameters

- `collectionName` (string, required) — Name of the Document Collection

### Body (application/json)

- `list of object`
  - `uuid` (string, required)
  - `document_id` (string, optional)
  - `metadata` (map from string to any, optional)

## Response

### 200

OK

- `message` (string, optional)

## Examples

**Request**

```json
[
  {
    "uuid": "a3f1c9e2-7b4d-4f6a-9d3e-2b8f1c7a9d5e"
  },
  {
    "uuid": "b7d2e4f8-1c3a-4e5b-8f9d-0a2b3c4d5e6f"
  }
]
```

**Response**

```json
{
  "message": "Documents updated successfully."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.document.batchUpdateDocuments("collectionName", [
        {
            uuid: "a3f1c9e2-7b4d-4f6a-9d3e-2b8f1c7a9d5e",
        },
        {
            uuid: "b7d2e4f8-1c3a-4e5b-8f9d-0a2b3c4d5e6f",
        },
    ]);
}
main();

```

```python
from zep_cloud import Zep, UpdateDocumentListRequest

client = Zep()

client.document.batch_update_documents(
    collection_name="collectionName",
    request=[
        UpdateDocumentListRequest(
            uuid_="a3f1c9e2-7b4d-4f6a-9d3e-2b8f1c7a9d5e",
        ),
        UpdateDocumentListRequest(
            uuid_="b7d2e4f8-1c3a-4e5b-8f9d-0a2b3c4d5e6f",
        )
    ],
)

```

```go
package example

import (
    context "context"

    zep "github.com/getzep/zep-go"
    client "github.com/getzep/zep-go/client"
)

func do() {
    client := client.NewClient()
    request := []*zep.UpdateDocumentListRequest{
        &zep.UpdateDocumentListRequest{
            UUID: "a3f1c9e2-7b4d-4f6a-9d3e-2b8f1c7a9d5e",
        },
        &zep.UpdateDocumentListRequest{
            UUID: "b7d2e4f8-1c3a-4e5b-8f9d-0a2b3c4d5e6f",
        },
    }
    client.Document.BatchUpdateDocuments(
        context.TODO(),
        "collectionName",
        request,
    )
}

```