> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Debug mode

> Enable Zep debug mode from Project Settings and inspect per-episode workflow logs, errors, and LLM reasoning traces available on Flex, Flex Plus, and Enterprise plans.

Debug mode helps you inspect how Zep processes new episodes. When it is enabled, Zep captures detailed workflow logs for episodes processed during the active debug window. For Flex, Flex Plus, and Enterprise accounts, debug mode also runs a second LLM review step for selected extraction and resolution outputs and captures reasoning traces from that review.

Use debug mode when an episode is pending, a graph update looks unexpected, or you need to understand why ingestion produced a specific result.

Debug logs can include data derived from your episode content and graph processing. Enable debug mode only when you need to troubleshoot, and review logs with the same care as other sensitive operational data.

## Enable debug mode

#### Open Project Settings

In the Zep dashboard, open the project you want to debug, then go to **Project Settings**.

#### Find Debug Options

In **Debug Options**, click **Enable Debug Logging**.

#### Process new episodes

Add or reprocess the episodes you want to inspect while debug mode is active. Debug mode does not backfill logs for episodes processed before it was enabled.

#### Open episode logs

Go to a user or graph episode list. Open the episode action menu and select **Debug Logs**.

Debug mode is scoped to the project where it is enabled. It is time-limited and automatically turns off:

* Standard accounts: 60 minutes.
* Enterprise accounts: 24 hours.

Enabling debug mode again resets the active window.

## Workflow logs

The **Workflow** tab in the episode logs dialog shows the ingestion workflow for that episode.

Workflow logs include:

* Episode status, start time, completion time, and top-level error details.
* Timestamped log entries with level and message.
* Step-by-step ingestion progress through graph construction.
* Errors and stack traces when a workflow step fails.
* Processing details that can help explain why a node, edge, or fact was created, changed, skipped, or failed.

Logs are available only for episodes processed while debug mode was active. Workflow logs are retained for a limited time: 12 hours by default and 7 days for Enterprise accounts.

Per-episode debug logs are not currently supported for batch ingestion operations.

## Reasoning traces

Reasoning traces are available for Flex, Flex Plus, and Enterprise accounts. Turning on debug mode for an eligible project also enables reasoning review for eligible ingestion steps during the active debug window.

Reasoning traces show how a reviewer model inspected selected LLM outputs during graph construction. When the reviewed output validates, it replaces the primary output before downstream steps run.

The **Reasoning** tab can include:

* Stage and prompt name, such as extraction or resolution.
* Validation status, including whether the reviewed output was applied or failed validation.
* Model and model size used for the review.
* Attempt count and error details when retries were needed.
* Explanations for the reviewer's decisions.
* Corrections, warnings, and metadata.
* Original output and reviewed output for comparison.

Reasoning traces are most useful when an extracted entity, edge, or fact looks wrong. They let you compare the primary model output with the reviewed output and see why the reviewer accepted, corrected, or rejected part of the result.

## Troubleshooting

If an episode has no logs, check that debug mode was enabled before the episode was processed and that the log retention window has not expired.

If the **Reasoning** tab is empty, one of the following is usually true:

* The account is not on a Flex, Flex Plus, or Enterprise plan.
* The episode was processed while debug mode was off.
* The episode did not run an ingestion step that emits reasoning traces.
* Zep could not store the reasoning trace.
* The review exhausted its retries. Ingestion continues with the primary output, and Zep stores a failed trace when possible.

Debug mode does not change retrieval behavior for existing graph data. It affects logging and, for Flex, Flex Plus, and Enterprise accounts, enables reasoning review during new ingestion workflows.