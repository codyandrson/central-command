> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Updates a Document

PATCH https://api.getzep.com/api/v2/collections/{collectionName}/documents/uuid/{documentUUID}
Content-Type: application/json

Updates a Document in a DocumentCollection by UUID

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/document/updates-a-document

## Request

### Path parameters

- `collectionName` (string, required) — Name of the Document Collection
- `documentUUID` (string, required) — UUID of the Document to be updated

### Body (application/json)

- `document_id` (string, optional)
- `metadata` (map from string to any, optional)

## Response

### 200

OK

- `message` (string, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "message": "Document updated successfully."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.document.updatesADocument("collectionName", "documentUUID", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.document.updates_a_document(
    collection_name="collectionName",
    document_uuid="documentUUID",
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
    request := &zep.UpdateDocumentRequest{}
    client.Document.UpdatesADocument(
        context.TODO(),
        "collectionName",
        "documentUUID",
        request,
    )
}

```