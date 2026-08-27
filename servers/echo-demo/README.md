# echo-demo — VERIFICATION ARTIFACT, not production

This directory is the first thing the MCP creation pipeline ever produced, and
it is kept as evidence, not as a service anyone should depend on.

**What it is.** A minimal MCP server exposing one tool, `echo(text) -> "echo:
{text}"`. It was written from scratch by the agent `sandbox-tester` (now
RETIRED) inside its sandbox on 2026-08-04, then carried through every gate of
the pipeline — `mcp.sync_source` → `mcp.build_image` → `mcp.server_deploy` →
`litellm.register_mcp_server` — each one an operator-approved proposal. The
final proof was a gated `mcp.tool_call` returning `echo: gated call works`.

**Do not treat this as a template.** It is correct for `mcp==2.0.0` as of the
date above (`mcp.server.mcpserver.MCPServer`, `run_streamable_http_async`),
and that API moved once already inside a single major version — the previous
draft was written against `mcp.server.fastmcp` and crashed on deploy. An agent
building a new server should install the library in its sandbox and check the
real API, exactly as this one eventually did. That habit is the point; this
file is just what it produced.

**Why it is still deployed.** It runs in `cc-mcp` (gVisor, resource-limited,
reachable only from LiteLLM) and is registered with the proxy as `echo_demo`
— LiteLLM forbids `-` in server names where Kubernetes requires it, so the
canonical id here stays hyphenated and the Executor translates at the
boundary. Leaving it up makes it the fixture for the two checks the arc has
not yet made: an UNGATED MCP tool as a live direct callable, and server
removal with coupled deregistration.

**Removing it** is an operator decision, not a cleanup chore: delete the
Deployment/Service in `cc-mcp`, `DELETE /v1/mcp/server/{id}` on the proxy,
and retire the `mcp_server` row. The gated capability that does this in one
approved action is designed but not built.
