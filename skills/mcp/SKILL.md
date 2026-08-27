---
name: MCP
description: The MCP server creation pipeline as Central Command actually built it — the sandbox that authors servers from scratch, the one gated exit (mcp.sync_source), and the state machine that carries a server through build, deploy and LiteLLM registration. Load before working in the sandbox, before proposing any mcp.* or litellm.register_mcp_server capability, or before advising on a server's naming/removal.
---

# MCP

Central Command agents can **build MCP servers from scratch**, not just configure
existing ones: write code, test it live, and drive it through a gated
pipeline until any granted agent can call its tools. This is the
`mcp-manager` agent's whole job (`central_command/runtime/mcp_manager.py`,
`mcp_manager_profile.py`). Everything below is true of *this* repo's
implementation — the sandbox runner, the Executor handlers, the LiteLLM
plumbing — not upstream MCP or LiteLLM documentation.

## Load this skill when

- You are about to **work in your sandbox** — `sandbox-mechanics`.
- You are about to **propose** any `mcp.*` or `litellm.register_mcp_server`
  capability — `pipeline-and-state-machine`.
- You are naming a server, wondering why a tool call 401s or comes back
  empty, or touching the LiteLLM key/proxy side — `naming-and-plumbing-gotchas`.
- You are authoring a new server's code, or deciding whether `servers/echo-demo/`
  is something to copy — `echo-demo-and-image-lessons`.

## Four things to hold even without loading a reference

1. **The pipeline is a state machine: synced -> built -> deployed ->
   registered.** Each step's Executor handler refuses to run unless the prior
   step already landed (`mcp_server.status` in the right set) — you cannot
   build what isn't synced, deploy what isn't built, or register what isn't
   deployed, no matter what you ask for.
2. **`mcp.sync_source` is the ONE exit from your sandbox.** Nothing else you
   do there — write, exec, test, iterate — is reviewed or durable. Never
   author file bytes into a proposal yourself: `propose_mcp_sync_source`
   reads your sandbox and captures the exact bytes into the proposal at
   propose time; the Executor later writes exactly those bytes and never
   re-reads your sandbox. Only sync what you actually ran and watched work.
3. **Port 8000 is mandatory.** Every agent-built server must listen on 8000
   — the Deployment's `containerPort` and the Service's `port`/`targetPort`
   are hard-coded to it (`central_command/gateway/executor.py`'s
   `_mcp_deployment_manifest`/`_mcp_service_manifest`). A server bound to any
   other port deploys and answers nothing.
4. **Never trust training data about a library's API — introspect the
   installed version first.** `servers/echo-demo/` was written from memory
   against `mcp.server.fastmcp`, crashed on deploy, and had to be rewritten
   against the real `mcp==2.0.0` API (`mcp.server.mcpserver.MCPServer`) once
   the agent actually checked what was installed. That habit — read the real
   docs/`--help`/source live in the sandbox before writing code — is the
   whole lesson; see `echo-demo-and-image-lessons`.

Removal is coupled, not two capabilities: an approved `mcp.server_remove`
tears down the Deployment+Service, deregisters from LiteLLM, and retires the
`mcp_server` row, all in one approval — never propose "just deregister" or
"just delete the pod" as if they were separate acts. It archives in place:
`servers/<id>/` source and built image tags are never touched, so a removed
server can be redeployed later.

## References

- `pipeline-and-state-machine` — the 5 gated capabilities with their exact
  arguments and Executor preconditions, the `mcp_server` status column, the
  immutable per-proposal image tag, and the mandatory resource limits.
- `sandbox-mechanics` — the 5 sandbox tools, what `sandbox_copy_in` actually
  checks, and why containment (not restriction) means egress stays open.
- `naming-and-plumbing-gotchas` — the LiteLLM-forbids-`-`/k8s-requires-`-`
  translation, the namespaced tool-name shape the gateway returns, the
  virtual-key `object_permission.mcp_servers` coupling, and the exact
  headers/body shape the proxy wants.
- `echo-demo-and-image-lessons` — what `servers/echo-demo/` proves and does
  not prove, plus the image-pull and build-verification bite marks any new
  server build can walk straight into.
