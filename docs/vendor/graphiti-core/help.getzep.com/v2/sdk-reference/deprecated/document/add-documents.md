> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Creates Multiple Documents in a DocumentCollection

POST https://api.getzep.com/api/v2/collections/{collectionName}/documents
Content-Type: application/json

Creates Documents in a specified DocumentCollection and returns their UUIDs.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/document/add-documents

## Request

### Path parameters

- `collectionName` (string, required) — Name of the Document Collection

### Body (application/json)

- `list of object`
  - `content` (string, required)
  - `document_id` (string, optional)
  - `metadata` (map from string to any, optional)

## Response

### 200

OK

- `list of list of string`

## Examples

**Request**

```json
[
  {
    "content": "The quick brown fox jumps over the lazy dog."
  },
  {
    "content": "In 2024, AI technologies continue to evolve rapidly."
  }
]
```

**Response**

```json
[
  [
    "3fa85f64-5717-4562-b3fc-2c963f66afa6"
  ],
  [
    "7c9e6679-7425-40de-944b-e07fc1f90ae7"
  ]
]
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.document.addDocuments("collectionName", [
        {
            content: "The quick brown fox jumps over the lazy dog.",
        },
        {
            content: "In 2024, AI technologies continue to evolve rapidly.",
        },
    ]);
}
main();

```

```python
from zep_cloud import Zep, CreateDocumentRequest

client = Zep()

client.document.add_documents(
    collection_name="collectionName",
    request=[
        CreateDocumentRequest(
            content="The quick brown fox jumps over the lazy dog.",
        ),
        CreateDocumentRequest(
            content="In 2024, AI technologies continue to evolve rapidly.",
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
    request := []*zep.CreateDocumentRequest{
        &zep.CreateDocumentRequest{
            Content: "The quick brown fox jumps over the lazy dog.",
        },
        &zep.CreateDocumentRequest{
            Content: "In 2024, AI technologies continue to evolve rapidly.",
        },
    }
    client.Document.AddDocuments(
        context.TODO(),
        "collectionName",
        request,
    )
}

```