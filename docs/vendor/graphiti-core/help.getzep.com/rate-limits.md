> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Rate limits

The Zep API enforces rate limits on incoming requests. Rate limits are measured in requests per minute (RPM) and applied per account.

The exact RPM limit for your account depends on your plan. See the [Zep pricing page](https://www.getzep.com/pricing) for details.

## Rate limit headers

Every response from the Zep API includes headers that describe your current rate limit state. Inspect these headers to monitor usage and pace your client before you hit the limit.

| Header                  | Description                                                             |
| ----------------------- | ----------------------------------------------------------------------- |
| `X-RateLimit-Limit`     | The per-minute request limit for your account.                          |
| `X-RateLimit-Remaining` | The number of requests remaining in the current window.                 |
| `X-RateLimit-Reset`     | Unix timestamp (in seconds) at which the current window resets.         |
| `X-RateLimit-Increment` | The cost of the current request, in units of the limit. Always `1`.     |
| `Retry-After`           | Number of seconds to wait before retrying. Only set on `429` responses. |

## Reading rate limit headers from the SDK

The Zep SDKs do not return response headers from a normal method call. To read headers, use the SDK's raw response accessor, which returns both the parsed response data and the raw HTTP response.

```python Python
response = client.thread.with_raw_response.add_messages(
    thread_id="thread_123",
    messages=messages,
)

remaining = response.headers.get("x-ratelimit-remaining")
reset = response.headers.get("x-ratelimit-reset")
data = response.data
```

```typescript TypeScript
const { data, rawResponse } = await client.thread
  .addMessages("thread_123", { messages: messages })
  .withRawResponse();

const remaining = rawResponse.headers.get("x-ratelimit-remaining");
const reset = rawResponse.headers.get("x-ratelimit-reset");
```

```go Go
resp, err := client.Thread.WithRawResponse.AddMessages(
    context.TODO(),
    "thread_123",
    &zep.AddThreadMessagesRequest{Messages: messages},
)
if err != nil {
    // handle error
}

remaining := resp.Header.Get("X-RateLimit-Remaining")
reset := resp.Header.Get("X-RateLimit-Reset")
data := resp.Body
```

## Handling 429 responses

When you exceed your rate limit, the Zep API returns HTTP `429 Too Many Requests`. The SDK surfaces this as a typed error whose response headers include `Retry-After`, indicating how many seconds to wait before retrying.

Catch the error, read `Retry-After`, wait, and retry.

```python Python
import time
from zep_cloud import ApiError

try:
    client.thread.add_messages(thread_id="thread_123", messages=messages)
except ApiError as err:
    if err.status_code == 429:
        retry_after = int(err.headers.get("retry-after", "1"))
        time.sleep(retry_after)
        # retry your call
```

```typescript TypeScript
import { ZepError } from "@getzep/zep-cloud";

try {
  await client.thread.addMessages("thread_123", { messages: messages });
} catch (err) {
  if (err instanceof ZepError && err.statusCode === 429) {
    const retryAfter = Number(err.rawResponse?.headers.get("retry-after") ?? "1");
    await new Promise((resolve) => setTimeout(resolve, retryAfter * 1000));
    // retry your call
  }
}
```

```go Go
import (
    "context"
    "errors"
    "strconv"
    "time"

    "github.com/getzep/zep-go/v3/core"
)

_, err := client.Thread.AddMessages(context.TODO(), "thread_123", req)
var apiErr *core.APIError
if errors.As(err, &apiErr) && apiErr.StatusCode == 429 {
    retryAfter, _ := strconv.Atoi(apiErr.Header.Get("Retry-After"))
    time.Sleep(time.Duration(retryAfter) * time.Second)
    // retry your call
}
```

Combine `Retry-After` with exponential backoff and jitter to avoid synchronized retries when many clients are throttled at the same time.

## Pacing requests proactively

To avoid hitting `429` responses in the first place, use `X-RateLimit-Remaining` and `X-RateLimit-Reset` to pace your requests:

* If `X-RateLimit-Remaining` is approaching zero, slow your request rate or pause until the window resets.
* The current window ends at the Unix timestamp in `X-RateLimit-Reset`. After this time, a fresh allowance is available.

This is particularly useful for bulk operations, such as batch ingestion, where you control the cadence of outgoing requests.