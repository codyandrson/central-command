# echo-demo and image lessons

Source: `servers/echo-demo/README.md`, `docs/STATUS.md`,
`central_command/gateway/executor.py` (`_mcp_deployment_manifest`,
`_mcp_build_image`).

## `servers/echo-demo/` is a verification artifact, NOT a template

It is the first server the pipeline ever produced — a single tool,
`echo(text) -> "echo: {text}"` — written from scratch by the (now retired)
agent `sandbox-tester` inside its sandbox on 2026-08-04, and carried through
every gate: `mcp.sync_source` -> `mcp.build_image` -> `mcp.server_deploy` ->
`litellm.register_mcp_server`, each an operator-approved proposal, ending in
a gated `mcp.tool_call` that returned `echo: gated call works`.

**Do not copy its code as a starting point without checking the installed
library first.** Its own README says so directly: it is correct for
`mcp==2.0.0` as of 2026-08-04 (`mcp.server.mcpserver.MCPServer`,
`run_streamable_http_async`) — and that API had **already moved once inside
a single major version**: the previous draft was written from training data
against `mcp.server.fastmcp` and crashed on deploy. The fix was not "use the
right remembered API" — it was introspecting the real installed package live
in the sandbox (its docs, `--help`, an actual import and read of its
interface) before writing code against it. That habit is the actual
deliverable; `echo-demo`'s code is just what it produced on one particular
day with one particular pip-installed version. A new server build should
repeat the introspection step, not trust that `echo-demo`'s imports are
still current.

## Why it's still deployed

It stays up as a live fixture for two things that needed a real registered
server to test against: an **ungated** MCP tool as a direct callable (proved
2026-08-05 via a temporary grant on `jira-expert`, then revoked), and
**server removal with coupled deregistration** (`mcp.server_remove`, built
2026-08-05). Removing it is an explicit operator decision, not a cleanup
chore — see `pipeline-and-state-machine.md` for what `mcp.server_remove`
actually does.

## Image-pull and build lessons that apply to every future server, not just echo-demo

- **`imagePullPolicy: IfNotPresent` is baked into every deploy manifest**
  (`_mcp_deployment_manifest`) — never `Always`. An agent-built image lives
  only on the single build host it was built for (single-arch by default,
  per §9.3 of the design); `Always` would try to pull it from a registry
  that doesn't have it and CrashLoop.
- **Fully-qualified image refs, always.** `mcp.build_image` tags with
  `docker.io/library/cc-mcp-<server_id>:<prop12>` explicitly — not a bare
  name. A podman-built local image tagged only `localhost/<name>` is
  invisible to containerd's lookup path once imported under a different
  namespace convention; the fully-qualified `docker.io/library/...` form is
  what the kubelet actually resolves. This mirrors the same bite mark
  documented for the Graphiti and sandbox-runner images elsewhere in this
  deployment (`CLAUDE.md`) — it is a general k3s/podman fact, not something
  specific to MCP, but every MCP build hits the same seam.
- **Single-arch build is pinned to one build host.** There is no cross-arch
  step here — `mcp.build_image` builds and imports on exactly
  `settings.mcp_build_host`, and `mcp.server_deploy`'s manifest carries a
  **required** (not preferred) node affinity to that same node, because
  there is no fallback node with the image. Escalating a server to run on
  both nodes is real, separate work (a second build, a second
  `imagePullPolicy`/presence verification) that nothing in the pipeline
  does automatically — say so plainly if a task seems to assume failover
  for an agent-built server.
- **Verify image presence with an exact match on a captured list, never a
  piped grep** — see `naming-and-plumbing-gotchas.md`'s `ctr images ls`
  note; the same `pipefail` trap that broke `migrate-from-docker.sh`'s
  preflight is exactly what `_mcp_build_image`'s verification step avoids.
