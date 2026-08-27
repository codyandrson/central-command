> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Gets a Document from a DocumentCollection by UUID

GET https://api.getzep.com/api/v2/collections/{collectionName}/documents/uuid/{documentUUID}

Returns specified Document from a DocumentCollection.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/document/gets-a-document-from-a-document-collection-by-uuid

## Request

### Path parameters

- `collectionName` (string, required) — Name of the Document Collection
- `documentUUID` (string, required) — UUID of the Document to be updated

## Response

### 200

OK

- `content` (string, optional)
- `created_at` (string, optional)
- `document_id` (string, optional)
- `embedding` (list of double, optional)
- `is_embedded` (boolean, optional)
- `metadata` (map from string to any, optional)
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
  "content": "This document contains the quarterly financial report for Q1 2024, including revenue, expenses, and profit analysis.",
  "created_at": "2024-04-15T09:30:00Z",
  "document_id": "doc-789456123",
  "embedding": [
    0.12345,
    -0.98765,
    0.45678,
    0.23456,
    -0.34567,
    0.56789,
    -0.12345,
    0.6789,
    -0.23456,
    0.34567
  ],
  "is_embedded": true,
  "metadata": {
    "author": "Jane Doe",
    "department": "Finance",
    "confidential": false,
    "tags": [
      "financial",
      "Q1",
      "report"
    ]
  },
  "updated_at": "2024-04-20T16:45:00Z",
  "uuid": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.document.getsADocumentFromADocumentCollectionByUuid("collectionName", "documentUUID");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.document.gets_a_document_from_a_document_collection_by_uuid(
    collection_name="collectionName",
    document_uuid="documentUUID",
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
    client.Document.GetsADocumentFromADocumentCollectionByUUID(
        context.TODO(),
        "collectionName",
        "documentUUID",
    )
}

```