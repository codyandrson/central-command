> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Creates a new DocumentCollection

POST https://api.getzep.com/api/v2/collections/{collectionName}
Content-Type: application/json

If a collection with the same name already exists, an error will be returned.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/document/add-collection

## Request

### Path parameters

- `collectionName` (string, required) — Name of the Document Collection

### Body (application/json)

- `description` (string, optional)
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
  "message": "Document collection 'research-papers' created successfully."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.document.addCollection("collectionName", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.document.add_collection(
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
    request := &zep.CreateDocumentCollectionRequest{}
    client.Document.AddCollection(
        context.TODO(),
        "collectionName",
        request,
    )
}

```