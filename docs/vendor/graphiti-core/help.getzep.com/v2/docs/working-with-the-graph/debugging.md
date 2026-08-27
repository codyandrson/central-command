> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Debugging

Zep provides detailed debugging capabilities to help you troubleshoot and optimize your graph operations. Debug logging captures detailed workflow execution logs that can be invaluable for understanding how your data flows through the system.

## Enabling Debug Logging

Debug logging is enabled from the Project Settings page in your Zep dashboard. Once enabled, debug logging will be active for 60 minutes.

> **Important**: Debug logging will be active for the next 60 minutes. During this time, all workflow executions will have detailed logs captured. You can view these logs in the session logs dialog for any session that runs during this period.

## Accessing Debug Logs

Debug logs are available from episode lists for both individual users and graph-wide operations.

## Viewing Debug Logs

To view debug logs for a specific episode:

1. Navigate to the episode list (either from a user or graph view)
2. Find the episode you want to debug
3. Click on the **Actions** menu for that episode
4. Select **Debug Logs**

This will open a detailed view of the workflow execution logs for that specific episode, showing you:

* Step-by-step execution flow
* Processing timestamps
* Error messages and stack traces
* Performance metrics
* Data transformation details

## Best Practices for Debug Logging

* **Enable selectively**: Only enable debug logging when actively troubleshooting to avoid unnecessary overhead
* **Time-limited sessions**: Debug logging automatically disables after 60 minutes to prevent performance impact
* **Review promptly**: Review debug logs within 24 hours. Stale debug logs are removed after 24 hours.