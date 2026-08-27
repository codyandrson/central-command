> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# API logging

Available to [Flex Plus and Enterprise](https://www.getzep.com/pricing) customers.

API logs provide a complete audit trail of all SDK and API key usage across your account, including successful requests and errors. Use API logs to monitor usage, debug integrations, and track request patterns.

API logs track requests made with project API keys. Webapp requests are not included. For webapp activity, see [Audit logging](/audit-logging).

## What is logged

Every API request made using a project API key is logged, including:

| Field        | Description                                |
| ------------ | ------------------------------------------ |
| **Time**     | When the request was made                  |
| **Method**   | HTTP method (GET, POST, PUT, DELETE)       |
| **Endpoint** | The API endpoint called                    |
| **Status**   | HTTP status code (2xx, 4xx, 5xx)           |
| **Latency**  | Response time in milliseconds              |
| **API Key**  | Which API key was used (name + masked key) |

## Viewing API logs

Access API logs from your account settings:

1. Navigate to **Account Settings** in the Zep dashboard
2. Select **API Logs** from the sidebar
3. Browse the chronological list of requests

Results are sorted by most recent first.

## Data retention

### Dashboard access

API logs are available in the dashboard according to your plan:

| Plan       | Retention Period |
| ---------- | ---------------- |
| Enterprise | 30 days          |
| Flex Plus  | 7 days           |

### Long-term storage

Enterprise customers have access to extended log retention in cold storage:

| Plan                      | Retention Period |
| ------------------------- | ---------------- |
| Enterprise                | 1 year           |
| Enterprise with HIPAA BAA | 7 years          |

To access logs beyond the dashboard retention period, contact your account manager.

## Access control

Viewing API logs requires the **API Logs View** permission. Configure this through [managing team access](/role-based-access-control).