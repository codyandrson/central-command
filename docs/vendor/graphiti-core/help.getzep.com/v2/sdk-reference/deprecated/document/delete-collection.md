> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Deletes a DocumentCollection

DELETE https://api.getzep.com/api/v2/collections/{collectionName}

If a collection with the same name already exists, it will be overwritten.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/document/delete-collection

## Request

### Path parameters

- `collectionName` (string, required) — Name of the Document Collection

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
  "message": "Document collection 'user_profiles' successfully deleted."
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.document.deleteCollection("collectionName");
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.document.delete_collection(
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
    client.Document.DeleteCollection(
        context.TODO(),
        "collectionName",
    )
}

```