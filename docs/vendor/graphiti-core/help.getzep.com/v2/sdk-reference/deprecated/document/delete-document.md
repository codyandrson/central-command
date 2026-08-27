> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Delete Document from a DocumentCollection by UUID

DELETE https://api.getzep.com/api/v2/collections/{collectionName}/documents/uuid/{documentUUID}

Delete specified Document from a DocumentCollection.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/document/delete-document

## Request

### Path parameters

- `collectionName` (string, required) — Name of the Document Collection
- `documentUUID` (string, required) — UUID of the Document to be deleted

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
  "message": "Document successfully deleted from collection 'user_notes'."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.document.deleteDocument("collectionName", "documentUUID");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.document.delete_document(
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
    client.Document.DeleteDocument(
        context.TODO(),
        "collectionName",
        "documentUUID",
    )
}

```