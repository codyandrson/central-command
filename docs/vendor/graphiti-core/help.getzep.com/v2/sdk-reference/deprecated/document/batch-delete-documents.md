> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Batch Deletes Documents from a DocumentCollection by UUID

POST https://api.getzep.com/api/v2/collections/{collectionName}/documents/batchDelete
Content-Type: application/json

Deletes specified Documents from a DocumentCollection.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/document/batch-delete-documents

## Request

### Path parameters

- `collectionName` (string, required) — Name of the Document Collection

### Body (application/json)

- `list of string`

## Response

### 200

OK

- `message` (string, optional)

## Examples

**Request**

```json
[
  "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "7c9e6679-7425-40de-944b-e07fc1f90ae7"
]
```

**Response**

```json
{
  "message": "Successfully deleted 2 documents from the collection."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.document.batchDeleteDocuments("collectionName", [
        "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    ]);
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.document.batch_delete_documents(
    collection_name="collectionName",
    request=[
        "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "7c9e6679-7425-40de-944b-e07fc1f90ae7"
    ],
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
    request := []string{
        "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    }
    client.Document.BatchDeleteDocuments(
        context.TODO(),
        "collectionName",
        request,
    )
}

```