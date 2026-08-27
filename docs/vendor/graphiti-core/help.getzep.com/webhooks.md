> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Webhooks

Webhooks allow your application to receive real-time notifications when specific events occur in Zep, such as when an episode finishes processing or a batch ingestion completes. Instead of polling for changes, Zep pushes event data directly to your server as HTTP POST requests.

Webhooks are only available on certain plans. See the [Zep pricing page](https://www.getzep.com/pricing) for details.

## Why use webhooks

Webhooks enable event-driven architectures where your application reacts immediately to changes:

* **Episode processed notifications:** Trigger downstream processing when new data is added to a graph
* **Batch completion alerts:** Know when large data imports finish so you can start using the data
* **BYOM monitoring:** Receive alerts when your own LLM credentials hit rate limits or fail, so you can respond to provider issues quickly
* **Reduced polling:** Eliminate the need to continuously check for updates

## Setting up webhooks

Webhooks are configured per project within the Webhooks page in the Zep Dashboard sidebar.

### Navigate to webhooks

Open the Webhooks page from the sidebar in the Zep Dashboard.

### Create an endpoint

Add a new endpoint by providing:

* **Endpoint URL:** The HTTPS URL on your server that will receive webhook events
* **Subscribed events:** Select which events you want to receive (e.g., `episode.processed`, `ingest.batch.completed`)

### Save your signing secret

After creating an endpoint, you'll see a signing secret. Copy and securely store this secret—you'll need it to verify that incoming webhooks are genuinely from Zep.

## Available events

### Graph events

| Event                    | Description                                                                                                                                                                                                                                                       |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `episode.processed`      | Fired when an episode finishes processing and is added to the graph                                                                                                                                                                                               |
| `ingest.batch.completed` | Fired when a batch ingestion operation completes. Fires for both the [Batch API](/adding-batch-data) and the deprecated `graph.add_batch` / `thread.add_messages_batch` methods — see [Batch completion payloads](#batch-completion-payloads) for the differences |

### BYOM events

These events are specific to [Bring Your Own LLM (BYOM)](/bring-your-own-llm) configurations. They notify you when requests using your own model credentials encounter rate limits or failures, so you can monitor and react to issues with your LLM provider configuration.

BYOM webhook events are **aggregated** rather than sent per-request. Zep accumulates events over a 60-second window and delivers a single webhook summarizing all occurrences in that period. This prevents webhook spam during bursts of errors or rate limiting.

| Event                 | Description                                                                                                                                            |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `byom.rate_limited`   | Fired when BYOM requests are rejected due to rate limiting from your LLM provider                                                                      |
| `byom.request_failed` | Fired when BYOM requests fail due to credential or provider issues (invalid credentials, expired credentials, provider unavailable, unsupported model) |

## Webhook payload schemas

### Episode processed payload

`episode.processed` events include the following fields:

| Field          | Description                                                 |
| -------------- | ----------------------------------------------------------- |
| `event_name`   | The type of event (`episode.processed`)                     |
| `account_uuid` | Your Zep account identifier                                 |
| `project_uuid` | The project where the event occurred                        |
| `episode_uuid` | The UUID of the episode that finished processing            |
| `source`       | The source of the episode (e.g., `text`, `json`, `message`) |
| `completed_at` | RFC3339 timestamp of when processing completed              |
| `graph_type`   | Either `user` or `graph`, indicating the graph type         |
| `graph_id`     | The graph identifier\*                                      |
| `user_id`      | The user identifier\*                                       |

\*Only one of `graph_id` or `user_id` will be present in a given payload, depending on the graph type.

### Batch completion payloads

`ingest.batch.completed` is fired by two different code paths and the payload differs depending on the source.

#### Batch API payload

Sent when a batch created via the [Batch API](/adding-batch-data) finishes processing. The `batch_id` matches the value returned by `batch.create()`.

| Field          | Description                                                                |
| -------------- | -------------------------------------------------------------------------- |
| `event_name`   | `ingest.batch.completed`                                                   |
| `account_uuid` | Your Zep account identifier                                                |
| `project_uuid` | The project where the event occurred                                       |
| `batch_id`     | The UUID of the completed batch (matches `batch.create()` / `batch.get()`) |
| `completed_at` | RFC3339 timestamp of when the batch reached a terminal state               |

Example payload:

```json
{
  "event_name": "ingest.batch.completed",
  "account_uuid": "1811f9c5-336c-402a-964b-1a62faddc9c0",
  "project_uuid": "a3627577-88b1-46ed-bcb9-0e90ff32dba3",
  "batch_id": "52caee86-759e-4bc2-8d20-c5c1a8212368",
  "completed_at": "2026-05-05T18:16:34Z"
}
```

#### Legacy batch payload

Sent when a batch submitted through the deprecated `graph.add_batch()` or `thread.add_messages_batch()` methods finishes processing. The `task_id` matches the task UUID returned by those calls.

| Field           | Description                                          |
| --------------- | ---------------------------------------------------- |
| `event_name`    | `ingest.batch.completed`                             |
| `account_uuid`  | Your Zep account identifier                          |
| `project_uuid`  | The project where the event occurred                 |
| `task_id`       | The UUID of the async task that processed the batch  |
| `episode_uuids` | Array of UUIDs for the episodes created by the batch |
| `graph_type`    | Either `user` or `graph`, indicating the graph type  |
| `graph_id`      | The graph identifier\*                               |
| `user_id`       | The user identifier\*                                |
| `completed_at`  | RFC3339 timestamp of when the batch finished         |

\*Only one of `graph_id` or `user_id` will be present in a given payload, depending on the graph type.

The presence of `batch_id` versus `task_id` is how you tell the two variants apart. New Batch API payloads include `batch_id` and omit graph/episode fields; legacy payloads include `task_id`, `episode_uuids`, and graph identifiers.

### BYOM event payload

BYOM events (`byom.rate_limited`, `byom.request_failed`) include the following fields:

| Field              | Description                                                      |
| ------------------ | ---------------------------------------------------------------- |
| `event_name`       | The type of event (`byom.rate_limited` or `byom.request_failed`) |
| `account_uuid`     | Your Zep account identifier                                      |
| `project_uuid`     | The project where the event occurred                             |
| `provider`         | The LLM provider (e.g., `openai`, `anthropic`)                   |
| `model`            | The model name (e.g., `gpt-4o`, `claude-3`)                      |
| `count`            | Number of occurrences in the aggregation window                  |
| `first_occurrence` | ISO 8601 timestamp of the first event in the window              |
| `last_occurrence`  | ISO 8601 timestamp of the last event in the window               |
| `window_seconds`   | Duration of the aggregation window in seconds (minimum 60)       |
| `error_code`       | The specific error code (`byom.request_failed` only)             |

#### BYOM error codes

The `error_code` field in `byom.request_failed` events indicates the specific failure reason:

| Error code                  | Description                                                         |
| --------------------------- | ------------------------------------------------------------------- |
| `BYOM_CREDENTIALS_INVALID`  | The provided LLM credentials are invalid or missing required fields |
| `BYOM_CREDENTIALS_EXPIRED`  | The provided LLM credentials have expired                           |
| `BYOM_PROVIDER_UNAVAILABLE` | The LLM provider is unavailable or unreachable                      |
| `BYOM_MODEL_NOT_SUPPORTED`  | The requested model is not supported by the provider                |

## Receiving webhooks

Your webhook endpoint must:

* Accept HTTP POST requests
* Return a `2xx` status code (200-299) within 15 seconds to acknowledge receipt
* Disable CSRF protection for the webhook route if your framework enables it by default

```python Python
from flask import Flask, request

app = Flask(__name__)

@app.route("/webhooks/zep", methods=["POST"])
def handle_webhook():
    payload = request.get_data(as_text=True)
    headers = request.headers

    # Verify the webhook signature (see next section)
    # Process the event
    event = request.json
    event_name = event.get("event_name")

    if event_name == "episode.processed":
        # Handle episode processed event
        pass
    elif event_name == "ingest.batch.completed":
        # Handle batch completion event
        pass
    elif event_name == "byom.rate_limited":
        # Handle BYOM rate limiting
        pass
    elif event_name == "byom.request_failed":
        # Handle BYOM request failure
        error_code = event.get("error_code")
        pass

    return "", 200
```

```typescript TypeScript
import express from "express";

const app = express();

// Important: Use raw body for signature verification
app.post("/webhooks/zep", express.raw({ type: "application/json" }), (req, res) => {
    const payload = req.body.toString();
    const headers = req.headers;

    // Verify the webhook signature (see next section)
    // Process the event
    const event = JSON.parse(payload);
    const eventName = event.event_name;

    if (eventName === "episode.processed") {
        // Handle episode processed event
    } else if (eventName === "ingest.batch.completed") {
        // Handle batch completion event
    } else if (eventName === "byom.rate_limited") {
        // Handle BYOM rate limiting
    } else if (eventName === "byom.request_failed") {
        // Handle BYOM request failure
        const errorCode = event.error_code;
    }

    res.status(200).send();
});
```

```go Go
package main

import (
    "encoding/json"
    "io"
    "net/http"
)

func handleWebhook(w http.ResponseWriter, r *http.Request) {
    payload, err := io.ReadAll(r.Body)
    if err != nil {
        http.Error(w, "Error reading body", http.StatusBadRequest)
        return
    }

    // Verify the webhook signature (see next section)
    // Process the event
    var event map[string]interface{}
    json.Unmarshal(payload, &event)
    eventName := event["event_name"].(string)

    switch eventName {
    case "episode.processed":
        // Handle episode processed event
    case "ingest.batch.completed":
        // Handle batch completion event
    case "byom.rate_limited":
        // Handle BYOM rate limiting
    case "byom.request_failed":
        // Handle BYOM request failure
        errorCode := event["error_code"].(string)
        _ = errorCode
    }

    w.WriteHeader(http.StatusOK)
}
```

## Verifying webhook signatures

Verifying webhook signatures is **recommended**. Without verification, attackers could send fake HTTP POST requests to your endpoint, potentially causing your application to process fraudulent data.

Zep signs every webhook with a cryptographic signature using your endpoint's signing secret. Verifying this signature ensures that:

* The webhook genuinely came from Zep
* The payload hasn't been tampered with in transit

### Using the Svix libraries (recommended)

Zep uses [Svix](https://www.svix.com/) to manage webhooks. The easiest way to verify signatures is with the Svix client libraries.

First, install the Svix library:

```bash Python
pip install svix
```

```bash TypeScript
npm install svix
```

```bash Go
go get github.com/svix/svix-webhooks/go
```

Then verify incoming webhooks:

```python Python
from svix.webhooks import Webhook

# Your signing secret from the Zep Dashboard
WEBHOOK_SECRET = "whsec_..."

def verify_webhook(payload: str, headers: dict) -> dict:
    wh = Webhook(WEBHOOK_SECRET)

    # This will raise an exception if verification fails
    return wh.verify(payload, headers)

# In your webhook handler:
@app.route("/webhooks/zep", methods=["POST"])
def handle_webhook():
    payload = request.get_data(as_text=True)
    headers = {
        "svix-id": request.headers.get("svix-id"),
        "svix-timestamp": request.headers.get("svix-timestamp"),
        "svix-signature": request.headers.get("svix-signature"),
    }

    try:
        event = verify_webhook(payload, headers)
        # Process the verified event
        return "", 200
    except Exception as e:
        print(f"Webhook verification failed: {e}")
        return "", 400
```

```typescript TypeScript
import { Webhook } from "svix";

// Your signing secret from the Zep Dashboard
const WEBHOOK_SECRET = "whsec_...";

function verifyWebhook(payload: string, headers: Record<string, string>): any {
    const wh = new Webhook(WEBHOOK_SECRET);

    // This will throw if verification fails
    return wh.verify(payload, headers);
}

// In your webhook handler:
app.post("/webhooks/zep", express.raw({ type: "application/json" }), (req, res) => {
    const payload = req.body.toString();
    const headers = {
        "svix-id": req.headers["svix-id"] as string,
        "svix-timestamp": req.headers["svix-timestamp"] as string,
        "svix-signature": req.headers["svix-signature"] as string,
    };

    try {
        const event = verifyWebhook(payload, headers);
        // Process the verified event
        res.status(200).send();
    } catch (err) {
        console.error("Webhook verification failed:", err);
        res.status(400).send();
    }
});
```

```go Go
package main

import (
    "io"
    "net/http"

    svix "github.com/svix/svix-webhooks/go"
)

// Your signing secret from the Zep Dashboard
var webhookSecret = "whsec_..."

func handleWebhook(w http.ResponseWriter, r *http.Request) {
    payload, err := io.ReadAll(r.Body)
    if err != nil {
        http.Error(w, "Error reading body", http.StatusBadRequest)
        return
    }

    wh, err := svix.NewWebhook(webhookSecret)
    if err != nil {
        http.Error(w, "Error creating webhook verifier", http.StatusInternalServerError)
        return
    }

    err = wh.Verify(payload, r.Header)
    if err != nil {
        http.Error(w, "Webhook verification failed", http.StatusBadRequest)
        return
    }

    // Process the verified event
    w.WriteHeader(http.StatusOK)
}
```

The verification process requires the **raw request body** exactly as received. Many web frameworks automatically parse JSON bodies, which can break signature verification. Make sure to access the raw body before any parsing middleware runs.

### Manual verification

If you prefer not to use the Svix libraries, you can verify signatures manually using HMAC-SHA256.

Every webhook includes three headers for verification:

* `svix-id`: Unique message identifier
* `svix-timestamp`: Unix timestamp (seconds since epoch)
* `svix-signature`: Base64-encoded signatures (may include multiple, comma-separated)

### Construct the signed content

Concatenate the `svix-id`, `svix-timestamp`, and the raw request body, separated by periods:

```
{svix-id}.{svix-timestamp}.{raw-body}
```

### Calculate the expected signature

Use HMAC-SHA256 with your signing secret (base64-decoded, excluding the `whsec_` prefix) to sign the content:

```javascript
const crypto = require('crypto');

const signedContent = `${svixId}.${svixTimestamp}.${rawBody}`;
const secret = "whsec_...";
const secretBytes = Buffer.from(secret.split('_')[1], "base64");
const expectedSignature = crypto
    .createHmac('sha256', secretBytes)
    .update(signedContent)
    .digest('base64');
```

### Compare signatures

The `svix-signature` header may contain multiple signatures prefixed with version numbers (e.g., `v1,abc123`). Remove the version prefix and compare against your calculated signature.

Use constant-time string comparison to prevent timing attacks.

### Validate the timestamp

Compare the `svix-timestamp` against your server's current time. Reject webhooks with timestamps more than 5 minutes old to prevent replay attacks.

For more details on manual verification, see the [Svix documentation on manual verification](https://docs.svix.com/receiving/verifying-payloads/how-manual).

## Managing webhooks

The Webhooks tab in the Zep Dashboard provides several management features:

* **Disable/Enable:** Temporarily stop receiving events without deleting your endpoint configuration
* **Activity logs:** View the history of webhook deliveries and their status
* **Replay messages:** Re-send failed webhooks for debugging or recovery
* **Rate limiting:** Configure throttling to control the rate of incoming webhooks
* **Delete:** Remove an endpoint entirely

Webhook configuration is **project-specific**. Each project has its own set of webhook endpoints and subscriptions. If you have multiple projects, you'll need to configure webhooks separately for each one.

Changes to webhook configuration (including creating, updating, or deleting endpoints) take **5-10 minutes to propagate** and take effect. This delay is due to configuration caching.

## Pricing

Webhook deliveries consume credits. See the [Zep pricing page](https://www.getzep.com/pricing) for costs.

## Best practices

* **Always verify signatures:** Treat unverified webhooks as potentially malicious
* **Respond quickly:** Return a `2xx` response within 15 seconds to avoid timeout retries
* **Process asynchronously:** If handling takes longer than a few seconds, acknowledge receipt immediately and process the event in a background job
* **Handle duplicates:** Webhooks may occasionally be delivered more than once; use the `svix-id` header to deduplicate
* **Monitor failures:** Check the activity logs in the Dashboard to identify and fix delivery issues

## Further reading

For additional information on consuming webhooks:

* [Why verify webhooks](https://docs.svix.com/receiving/verifying-payloads/why) - Security considerations explained
* [Svix webhook verification libraries](https://docs.svix.com/receiving/verifying-payloads/how) - Full library documentation
* [Manual verification guide](https://docs.svix.com/receiving/verifying-payloads/how-manual) - Detailed manual verification steps