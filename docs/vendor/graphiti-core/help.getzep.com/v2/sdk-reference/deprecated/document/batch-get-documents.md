> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Batch Gets Documents from a DocumentCollection

POST https://api.getzep.com/api/v2/collections/{collectionName}/documents/batchGet
Content-Type: application/json

Returns Documents from a DocumentCollection specified by UUID or ID.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/document/batch-get-documents

## Request

### Path parameters

- `collectionName` (string, required) — Name of the Document Collection

### Body (application/json)

- `document_ids` (list of string, optional)
- `uuids` (list of string, optional)

## Response

### 200

OK

- `list of list of object`
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
[
  [
    {
      "content": "This document contains the quarterly financial report for Q1 2024, including revenue, expenses, and net profit analysis.",
      "created_at": "2024-04-15T09:30:00Z",
      "document_id": "doc-987654321",
      "embedding": [
        0.123456789,
        -0.987654321,
        0.456789123,
        -0.123456789,
        0.789123456
      ],
      "is_embedded": true,
      "metadata": {},
      "updated_at": "2024-04-20T16:45:00Z",
      "uuid": "550e8400-e29b-41d4-a716-446655440000"
    }
  ]
]
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.document.batchGetDocuments("collectionName", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.document.batch_get_documents(
    collection_name="collectionName",
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
    request := &zep.GetDocumentListRequest{}
    client.Document.BatchGetDocuments(
        context.TODO(),
        "collectionName",
        request,
    )
}

```