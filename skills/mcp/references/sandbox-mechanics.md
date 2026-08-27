# The sandbox — mechanics, grants, and containment

Source: `central_command/runtime/tools.py` (the 5 sandbox tools),
`central_command/runtime/packs.py` (pack `sandbox`), `central_command/sandbox/runner.py`
(the credential-free runner service), `db/schema.sql`
(`agent_sandbox_source`), `docs/STATUS.md` (live-verification notes).

## The 5 tools (pack `sandbox`, ungated)

| tool | does |
|---|---|
| `sandbox_exec(command)` | runs a shell command in your session's container; returns `exit_code`/`stdout`/`stderr`, output capped and the cut marked, never silent |
| `sandbox_write_file(path, content)` | writes under `/workspace`; refuses a path that tries to escape it (`..`, or absolute outside `/workspace`) |
| `sandbox_read_file(path)` | reads back; capped at 200,000 bytes, a truncated read says so explicitly |
| `sandbox_copy_in(source_path)` | copies a granted repo path into your workspace — see below |
| `sandbox_reset()` | destroys your session's Job; your next tool call creates a fresh, empty one |

All five no-op with a stub message in `CC_DEMO_MODE`. A session is
identified by `(agent_id, session_id)`; `_sandbox_id_for` gets-or-creates it
on every call (idempotent on the runner side), so there is no separate
"start a session" step to remember.

## `sandbox_copy_in` — the grant check happens in the RUNTIME, not the runner

`sandbox/runner.py` is deliberately **credential-free** (no DB access at
all — an AST-guard test enforces it), so it cannot itself decide what an
agent is allowed to copy in. The decision is made **before** the runner is
ever called: `sandbox_copy_in` in `runtime/tools.py` calls
`repo.list_sandbox_sources(agent_id)` and checks `_sandbox_source_granted`
against the unrevoked `agent_sandbox_source` rows for that agent. A path is
allowed if it **equals** a granted path or sits **under** one as a directory
prefix — never the reverse (granting `servers/` does not grant its parent,
and granting `servers/echo-demo/` would not grant all of `servers/`). A
refused copy-in tells the agent to ask the operator, or `declare_gap(kind=
'missing_tool')` — never to guess at a nearby path.

`mcp-manager`'s **default grant is `servers`** (`_DEFAULT_SANDBOX_SOURCE` in
`central_command/runtime/mcp_manager.py`), set at hire time via
`repo.grant_sandbox_source(AGENT_ID, "servers", granted_by="system")` — so it
can see prior art and the pipeline's own conventions from its first task.
Widening to a different path is an explicit operator grant, same lever as
every other `agent_grant`-shaped table (revoke-as-timestamp, `granted_by`).

## Containment, not restriction

The sandbox exists to contain **effects on the operator's system**, not to limit
what an agent can reach. Concretely (per the ratified design doc, §11.2, and
`docs/STATUS.md`'s sandbox-slice-1 entry):

- **Egress is OPEN to the internet** — an agent must be able to `pip`/`npm`
  install, fetch real docs, and actually run what it writes, or it ships
  code written against stale training data. This was proved the hard way:
  `echo-demo`'s first draft was authored against `mcp.server.fastmcp` from
  memory and crashed on deploy; the fix was introspecting the real installed
  `mcp==2.0.0` package live in the sandbox.
- **Ingress stays deny-all** (a live-verified NetworkPolicy in `cc-sandbox`)
  — nothing reaches into a sandbox from outside.
- **The sandbox carries no credentials.** That is the actual constraint —
  provisioning a credential into a sandbox turns "can reach" into "can
  write", which is why that is an explicit, never-default operator decision.
- **Isolation**: gVisor (`runtimeClassName: gvisor`) on the chromebox only —
  one k8s Job **per sandbox session** (not per-exec, or the workspace would
  be lost between tool calls), `emptyDir` workspace, `1Gi`/`1cpu` limits,
  `automountServiceAccountToken: false`. Nothing that happens inside is
  reviewed or durable, because nothing in it is reachable by anyone else —
  the only thing that crosses the boundary is `mcp.sync_source`, and only
  the exact bytes it explicitly names.
- Residual note from the live drill: `srt` (the userspace egress-controlling
  proxy meant to run as defense-in-depth) cannot actually run under gVisor —
  its netns removal trips gVisor's netstack, no off switch — so it ships
  dormant in the sandbox image. The two layers actually live are gVisor
  itself and the CNI-level deny-all NetworkPolicy.

## Gate resolution happens outside the sandbox pack entirely

Whether a *registered server's* tool is reachable directly or only through
`propose_mcp_tool_call` is decided by `mcp_tool.default_gate` +
`agent_mcp_gate_override` at toolset-build time
(`runtime/packs.py:mcp_toolsets_for`) — nothing in the sandbox pack touches
this. The sandbox pack is only ever about the agent's own authoring
workspace; once a server is registered, granting an agent access to *call*
its tools is a separate `mcp:<server_id>` pack grant, unrelated to whatever
`agent_sandbox_source` rows that agent holds.
