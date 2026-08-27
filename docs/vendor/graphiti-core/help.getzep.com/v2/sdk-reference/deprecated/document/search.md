> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Searches Documents in a DocumentCollection

POST https://api.getzep.com/api/v2/collections/{collectionName}/search
Content-Type: application/json

Searches over documents in a collection based on provided search criteria. One of text or metadata must be provided. Returns an empty list if no documents are found.

Reference: https://help.getzep.com/v2/sdk-reference/deprecated/document/search

## Request

### Path parameters

- `collectionName` (string, required) — Name of the Document Collection

### Query parameters

- `limit` (integer, optional) — Limit the number of returned documents

### Body (application/json)

- `metadata` (map from string to any, optional) — Document metadata to filter on.
- `min_score` (double, optional)
- `mmr_lambda` (double, optional) — The lambda parameter for the MMR Reranking Algorithm.
- `search_type` (enum, optional, default: similarity) — The type of search to perform. Defaults to "similarity". Must be one of "similarity" or "mmr".
  - Allowed values: `similarity`, `mmr`
- `text` (string, optional) — The search text.

## Response

### 200

OK

- `current_page` (integer, optional)
- `query_vector` (list of double, optional)
- `result_count` (integer, optional)
- `results` (list of object, optional)
  - `content` (string, optional)
  - `created_at` (string, optional)
  - `document_id` (string, optional)
  - `embedding` (list of double, optional)
  - `is_embedded` (boolean, optional)
  - `metadata` (map from string to any, optional)
  - `score` (double, optional)
  - `updated_at` (string, optional)
  - `uuid` (string, optional)
- `total_pages` (integer, optional)

## Examples

**Request**

```json
{}
```

**Response**

```json
{
  "current_page": 1,
  "query_vector": [
    0.256,
    -0.134,
    0.789,
    0.045,
    -0.321
  ],
  "result_count": 1,
  "results": [
    {
      "content": "The quick brown fox jumps over the lazy dog.",
      "created_at": "2024-05-20T10:15:30Z",
      "document_id": "doc_987654321",
      "embedding": [
        0.256,
        -0.134,
        0.789,
        0.045,
        -0.321
      ],
      "is_embedded": true,
      "metadata": {},
      "score": 0.87,
      "updated_at": "2024-05-21T08:00:00Z",
      "uuid": "123e4567-e89b-12d3-a456-426614174000"
    }
  ],
  "total_pages": 1
}
```

**SDK Code**

```typescript
import { ZepClient } from "zep-cloud";

async function main() {
    const client = new ZepClient();
    await client.document.search("collectionName", {});
}
main();

```

```python
from zep_cloud import Zep

client = Zep()

client.document.search(
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
    request := &zep.DocumentSearchPayload{}
    client.Document.Search(
        context.TODO(),
        "collectionName",
        request,
    )
}

```