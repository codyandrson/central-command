# MCP server creation via sandbox — design (reconstructed v3)

> Reconstructed 2026-08-03. A prior round of this design (v3) was produced in a
> scratchpad and lost before landing in the repo. This is a full rewrite from
> the accumulated verified facts and the operator's settled decisions, re-checked
> against the current repo (`runtime/packs.py`, `gateway/capabilities.py`,
> `runtime/litellm_manager.py`, `db/schema.sql`, `tests/test_governance.py`).
> Not yet built. Sections marked "proposed" are this document's design
> opinions, awaiting the operator's ratification — everything else is either a
> verified repo fact or one of the operator's settled decisions.

## 1. Problem

Agents today can *configure* things (Jira, the LiteLLM proxy, the knowledge
graph) through packs of `propose_*` tools gated by the Executor. They cannot
*build* anything: write code, run it, iterate, and turn the result into a new
capability the roster can use. The concrete want is agents that can create new
MCP servers from scratch — scaffold a server, write its tool implementations,
test them, build an image, deploy it, and register it on the LiteLLM MCP
gateway so any agent can be granted its tools.

That is a different shape of work than every existing gated action. A Jira
comment is one bounded write. "Build a server" is an open-ended editing
session — dozens of file writes, test runs, dependency installs — that
*ends* in one or more writes to the world. Gating every intermediate file
write would make the gate meaningless (nobody reviews a 200-step diff-by-diff
history) and would not actually catch anything a full end-state diff review
wouldn't catch better.

## 2. Governing doctrine (settled, not re-litigated here)

Central Command's standing rule: **gates are review, not ceiling.** There is no
capability an operator cannot grant; every capability ships with a
conservative default (gated, or not granted at all) and explicit operator
buy-in can widen it — up to and including fully ungated — for a specific
agent. Nothing in this document says "must never"; where a boundary is
stated, it is "not by default, until the operator explicitly grants it otherwise."

The one thing that is **architecture, not a capability knob**: which *process*
performs a write. `runtime/` proposes; the credentialed Executor, in
`gateway/`, is the only process that ever holds write credentials and performs
the write. That split predates this feature and this feature does not touch
it — sandbox exec still never happens in the runtime/uvicorn process (see §4).

## 3. The three decisions this design is built on

1. **Agents can create MCP servers from scratch** — not just configure
   existing ones. Write code, test it, build it, deploy it.
2. **Working-directory model = Option 3 ("sandbox, then one gated sync"):**
   copy relevant source in, let the agent work completely freely inside a
   sandbox (read/write/edit/exec all ungated — the sandbox *is* the blast
   radius), then require exactly **one** gated proposal that carries the full
   diff back to the real `servers/` tree for operator review. Everything
   before that proposal is invisible to governance by design: it is disposable
   scratch work, reviewed once, at the boundary, as a diff — the same
   information density as reviewing the finished PR, none of the noise of
   reviewing every keystroke.
3. **Governance doctrine** as in §2 — conservative defaults, operator can
   always widen.

## 4. Trust boundary analysis

This is the part that has to be gotten right, because "let the agent run
arbitrary code" is exactly the kind of capability that looks fine until it
isn't.

**Where exec runs.** Sandbox exec runs as a **k8s Job, in a separate
"sandbox-runner" service** — never inside the `runtime`/`uvicorn` process, and
never inside the Executor's process either. This is a hard line, not a
default: the in-process pattern used for `fetch_url`-style reads (an ungated
read performed inline, because a read can't mutate anything durable) must
**not** extend to exec, because exec can do anything a shell can do. A runtime
process that can `subprocess.run()` arbitrary agent-authored code is a runtime
process that can perform writes without going through the Executor at all —
that collapses the trust boundary the whole system is built on. The
sandbox-runner is a distinct service so that even a fully-compromised sandbox
job cannot reach Postgres, the LiteLLM admin key, the Jira token, or any
Executor credential: it has none of them. It gets a workspace and a network
policy, nothing else.

**Isolation mechanism.** gVisor (`runtimeClass: gvisor`) as a one-line
`RuntimeClass` on the Job's pod spec — verified to run on both arm64 (the Pi)
and amd64 (the chromebox) via the `systrap` platform, which needs no KVM
(neither node has nested virtualization). `anthropic-experimental/sandbox-runtime`
(`srt`, bubblewrap + an egress-controlling proxy) runs *inside* the container
as defense-in-depth under gVisor, not instead of it — two independent layers,
neither load-bearing alone. Both are apt-installable/importable on Debian 12
(bubblewrap confirmed packaged for both arches; `nsjail` is not, which is why
it's not on the list). Workspace is an `emptyDir` — no PVC, nothing to clean
up, nothing that outlives the Job.

Sandboxes considered and dismissed, for the record (don't re-research these
without new information):
- **E2B self-host** — needs Terraform + GCP + Cloudflare. Wrong shape for a
  two-node homelab cluster with no cloud dependency today.
- **Daytona OSS** — unmaintained since June 2026.
- **Kata Containers / microsandbox / Firecracker** — all need KVM; unconfirmed
  whether either node's virtualization support allows it. Set aside, not
  ruled out — worth a cheap re-check if gVisor ever proves insufficient.
- **kubernetes-sigs/agent-sandbox CRD** — active but young, and it delegates
  the actual isolation to a `RuntimeClass` anyway (the same lever this design
  already uses directly). A plausible *compositional* upgrade later — adopt
  the CRD's lifecycle management without changing the isolation mechanism —
  not a v1 dependency.

**What crosses the boundary, and when.** Nothing the sandbox produces is
trusted, read, or acted on until it crosses through exactly one seam:
`mcp.sync_source`. Before that, the sandbox is a black box the agent owns
completely; the operator is not asked to review anything happening inside it,
because nothing happening inside it is durable or reachable by anyone else.
After that seam, everything downstream (build, deploy, register) operates on
reviewed, approved source — the same posture as any other gated write.

**Blast radius of a compromised or hallucinating agent, by stage:**
| Stage | What the agent can affect | Contained by |
|---|---|---|
| Sandbox work (scaffold/write/test/exec) | its own ephemeral Job workspace only | gVisor + srt egress control + no credentials in the sandbox-runner service + Job deleted on completion |
| `mcp.sync_source` | the `servers/` tree in the main repo | gated: operator reviews the full diff before merge |
| `mcp.build_image` | a container image, not yet running anywhere | gated: reviewed source only, digest-pinned base images by default |
| `mcp.server_deploy` | a namespace-scoped k8s object | gated + namespace-scoped ServiceAccount bound to a `Role` (never `ClusterRole`) — a compromised deploy step cannot touch other namespaces or cluster-scoped objects |
| `litellm.register_mcp_server` | which tools are reachable through the MCP gateway at all | gated, litellm-manager-style (propose → approve → Executor holds the LiteLLM admin key) |
| granting a tool to an agent | which agent can invoke a registered tool | pack grant + per-tool `default_gate`, both operator-controlled |

## 5. Pipeline

```
 [sandbox: scaffold / write / test / exec]   ── ungated, no exit ──▶ (nothing persists)
              │
              │ agent believes the server is ready
              ▼
      mcp.sync_source            (gated: full diff review → servers/<name>/ in main repo)
              │ approved
              ▼
      mcp.build_image            (gated: build from reviewed source, single-arch, pinned base)
              │ approved
              ▼
      mcp.server_deploy          (gated: Executor applies via namespace-scoped SA + Role)
              │ approved
              ▼
      litellm.register_mcp_server (gated: litellm-manager-style — proxy admin key stays Executor-only)
              │ approved
              ▼
      tool grants via packs       (per-tool default_gate; operator override per agent)
```

Each gated step is its own proposal, its own Decisions Inbox entry, its own
Executor handler — consistent with every other capability in the registry.
Nothing skips a step: an agent cannot deploy source that was never synced, and
cannot register a server that was never deployed, because each Executor
handler's precondition is "the prior row exists in the `mcp_server` state
machine" (see §6).

## 6. Data model (proposed)

Modeled directly on the existing `agent_grant` pattern in `db/schema.sql`
(§304 today) — revocation-as-timestamp, `granted_by`, idempotent seeds guarded
by `where … and … = ''`-style predicates where relevant.

```sql
-- One row per MCP server the system knows about, structured params rather
-- than a config blob so the registry (below) and the Executor can reason
-- about state machine transitions instead of parsing text.
create table if not exists mcp_server (
    id              text primary key,      -- server slug, matches servers/<id>/
    status          text not null default 'proposed',
        -- 'proposed' | 'synced' | 'built' | 'deployed' | 'registered' | 'retired'
    image_ref       text,                  -- set once mcp.build_image lands
    digest          text,                  -- image digest, pinned once built
    namespace       text,                  -- k8s namespace the deploy targets
    litellm_alias   text,                  -- set once registered with LiteLLM
    created_by      text not null,         -- agent_id of the proposer (drafter, not necessarily owner)
    created_at      timestamptz not null default now(),
    retired_at      timestamptz
);

-- One row per tool a server exposes, carrying the DEFAULT gate for that tool.
-- This is the charter-vs-capability-drift fix from D25 applied to MCP tools:
-- the registry generates the charter-facing capability list, never hand-written.
create table if not exists mcp_tool (
    server_id       text not null references mcp_server (id),
    tool_name       text not null,
    default_gate    text not null default 'human approval',  -- 'human approval' | 'ungated'
    description     text not null default '',
    primary key (server_id, tool_name)
);

-- Operator-set override of a tool's gate, per agent. Its EXISTENCE is the
-- explicit buy-in the governance doctrine requires to widen a default —
-- there is no code path that grants an ungated MCP tool without a row here
-- written by the operator.
create table if not exists agent_mcp_gate_override (
    agent_id        text not null references agent (id),
    server_id       text not null references mcp_server (id),
    tool_name       text not null,
    gate            text not null,          -- 'human approval' | 'ungated'
    granted_by      text not null default 'operator',
    granted_at      timestamptz not null default now(),
    revoked_at      timestamptz,
    primary key (agent_id, server_id, tool_name)
);

-- Which copy-in sources an agent may pull into its sandbox. Same shape as
-- agent_grant on purpose: revoke-as-timestamp, operator-attributed, seeded
-- idempotently. This is what makes copy-in "operator-granted", not
-- "agent-chosen path" — an agent cannot copy in a source nobody granted it.
create table if not exists agent_sandbox_source (
    agent_id        text not null references agent (id),
    source_path     text not null,          -- relative to repo root, e.g. "central_command/integrations/"
    granted_by      text not null default 'operator',
    granted_at      timestamptz not null default now(),
    revoked_at      timestamptz,
    primary key (agent_id, source_path)
);
```

## 7. Gated tool surface

**One generic wrapper**, following the `propose_jira_update` pattern
(`runtime/tools.py:537` today — a single tool that raises `CallDeferred` with
metadata identifying the action; the *content* of the proposal, not the tool
name, carries the specific action):

```python
async def propose_mcp_tool_call(ctx: RunContext, proposal: Proposal) -> str:
    """Propose a call to a gated MCP-pipeline or MCP-server tool (sync_source,
    build_image, server_deploy, register_mcp_server, or any registered
    server's default_gate='human approval' tool). Reviewed and, once
    approved, executed by the control plane — this tool never performs the
    call itself.
    """
    raise CallDeferred(metadata={"kind": "mcp_tool_call"})
```

One wrapper for the whole pipeline (`sync_source` → `register_mcp_server`)
*and* for every gated tool any deployed MCP server exposes — mirrors how
`jira-propose` uses a single `propose_jira_update` for six different Jira
capabilities (`runtime/packs.py`, `_JIRA_TARGET`/`PACKS["jira-propose"]`
today). The Action's `target_ref`/arguments distinguish which capability is
being invoked; the Executor dispatches on that, exactly like
`gateway/executor.HANDLERS` does today for Jira/graph/charter/task actions.

**Executor parity is GENERATED from `mcp_tool` rows**, following the
`test_governance.py` pattern (`test_registry_and_executor_dispatch_in_parity`,
`gated_write_names() == set(executor.HANDLERS)` today): every `mcp_tool` row
with `default_gate='human approval'` — and every pipeline capability
(`mcp.sync_source`, `mcp.build_image`, `mcp.server_deploy`,
`litellm.register_mcp_server`) — must have a matching Executor handler, and a
handler with no matching row is a false claim to the operator. A new guard
test (`test_mcp_tool_rows_match_executor_handlers` or similar, same shape as
the existing one) locks this in the same way.

**Deferred/lazy loading applies to the read half only.** Central Command's skills
mechanism (deferred `Capability` loading, `pydantic_ai.capabilities.mcp.MCP`)
is the right fit for *reading* an MCP server's docs/schema on demand — an
agent shouldn't eagerly load every registered server's full tool catalog into
context. But gated tools must stay **always-loaded** in the toolset an agent
is granted, never lazily fetched, because a lazy-load path is a second way
into a tool that bypasses the moment the propose seam gets attached. If a
tool can be deferred into existence, it can be deferred around review. This
mirrors the existing rule that a hand-written capability list can drift — a
lazy-loaded gated tool is the same drift risk in a different shape.

## 8. Safety defaults (operator-overridable, per §2)

- **Digest-pinned images.** `mcp.build_image` records the resulting digest in
  `mcp_server.digest`; `mcp.server_deploy` deploys by digest, not by mutable
  tag, by default. An operator can override to a floating tag for a specific
  server if there's a reason to.
- **No secrets in tool args.** Gated MCP tool calls carry no credential
  material in their arguments by default — anything a server needs at
  runtime is injected by the Executor via k8s Secret at deploy time, never
  passed through the proposal/approval pipeline where it would sit in the
  event log forever (the event log is append-only, per the standing rule —
  nothing that shouldn't be permanent should be written to it).
- **Mandatory resource limits.** `mcp.server_deploy`'s Executor handler
  refuses to apply a Deployment/Job manifest with no `resources.limits` set —
  same instinct as the LiteLLM memory-leak incident that drove the k3s
  migration (`CLAUDE.md`, "one measured cause, three symptoms"): an
  unconstrained pod is a blast-radius guard waiting to be needed twice.

None of these are hard ceilings. Each is a default an operator can widen for
a specific server with an explicit grant, consistent with §2.

## 9. Designer decisions — **RATIFIED by the operator 2026-08-03** (all three)

These were this document's opinions on questions that needed *an* answer to
make the pipeline concrete; the operator ratified all three on 2026-08-03:

1. **Server source lives in a `servers/` tree in the main repo**, not a
   separate remote. One repo, one `git diff`/history as the review artifact
   for `mcp.sync_source` — reuses the existing git-based review muscle rather
   than inventing a second review surface.
2. **Copy-in sources are operator-granted, not agent-chosen paths.** The
   agent requests a copy-in; the Executor (or the sandbox-runner, reading
   `agent_sandbox_source`) is what actually decides what's copied — never the
   agent's own choice of `../../../whatever`. Enforces the same "operator
   decides the surface, agent works freely inside it" split as pack grants.
3. **Image builds default to single-arch, pinned to one node's
   architecture**, not the multi-arch floating pattern used for upstream
   images elsewhere in the deployment (`CLAUDE.md`'s "Two locally-built
   architectures" section — the Graphiti image is built per-node,
   per-architecture, by hand, specifically because cross-arch local builds
   are real work). A newly agent-built MCP server should default to running
   pinned to one node (no failover) until an operator deliberately escalates
   it to both-arch — that escalation is real effort (a second build, a second
   `imagePullPolicy` verification) and shouldn't be the default for a
   server nobody has decided is important enough to need failover yet.

## 10. Slice order

Built and shipped as five independent, individually-mergeable slices — each
one leaves the system in a working, tested state with a strictly larger
capability surface than the last:

1. **Sandbox mechanism alone.** exec/read/write/copy_in inside the
   gVisor+srt Job, fully ungated, **no exit** — the sandbox-runner service
   exists, the Job spec exists, `agent_sandbox_source` grants exist, but
   there is no `mcp.sync_source` yet, so nothing produced inside a sandbox can
   reach anywhere durable. Ships and is safe to leave running indefinitely,
   because it authorizes nothing (echoes the heartbeat "no action is a claim"
   rule: a mechanism that does nothing durable needn't be gated to be safe).
2. **`mcp.sync_source`** — the one gated exit into `servers/`. This is the
   whole trust-boundary crossing; everything after this slice is "normal"
   gated-write plumbing applied to a new domain.
3. **`mcp.build_image` + `mcp.server_deploy`**, single-arch/pinned per §9.3.
4. **`litellm.register_mcp_server`**, litellm-manager-style.
5. **Tool-grant flow**: `mcp_tool.default_gate` + `agent_mcp_gate_override` +
   pack wiring so a deployed server's tools become grantable to any agent,
   same lever as every other pack today.

Parallel track (decided 2026-08-03, see §11.5): **`sandbox.run_script`** — the
heartbeat action for scheduled script runs + agent evaluation — depends only
on slice 1 and can land any time after it, independent of slices 2–5.

Each slice is independently useful and independently reviewable — slice 1 in
particular is a good place to stop and validate the isolation mechanism on
real hardware (Pi + chromebox) before building anything that depends on it.

## 11. Open questions — **DECIDED by the operator 2026-08-03** (Q5 in research)

1. **Initial copy-in grants for the MCP-manager: DECIDED.** Default-grant the
   `servers/` tree plus a small templates/examples directory; widen per-agent
   with explicit grants when a task needs it. (Conservative default +
   explicit buy-in, per §2.)
2. **Sandbox egress: CORRECTED 2026-08-04 — OPEN to the internet.** The
   2026-08-03 "empty by default" answer was based on a wrong model of what
   the sandbox is FOR. The operator's correction: the sandbox contains *effects on
   his system*; it does not limit what the agent may reach. An agent must be
   able to pip/npm install, fetch docs and TEST what it builds — otherwise it
   writes from stale training data (proved live the next day: echo-demo was
   authored against mcp 1.x, untestable in a no-egress sandbox, and crashed
   on deploy). Egress is therefore open to the internet, EXCLUDING the
   operator's own internal services TOO (clarified same day: an agent asked
   to integrate with n8n/LiteLLM/the graph must be able to reach and read
   them; "tools that would allow making changes to those services however
   need to be gated by default"). Reachability is not a gate — the CAPABILITY
   is. What constrains a sandbox is that it carries NO CREDENTIALS; the
   corollary is that provisioning a credential into a sandbox turns reach
   into ungated writes, so that is an explicit operator decision, never a
   default. Ingress stays deny-all. The review gate lives on the EXIT
   (`mcp.sync_source`), never on what the sandbox reads.
3. **Sync-back auto-runs tests: DECIDED — yes.** Tests run in the sandbox
   when `mcp.sync_source` is proposed; results attach to the proposal as
   evidence (a recorded fact, not an agent claim). **Never blocking** — red
   results inform the operator's decision, a mechanical gate never decides
   in their place.
4. **Server removal couples LiteLLM deregistration: DECIDED — coupled.** One
   approved `mcp.server_remove` deletes the deployment AND deregisters from
   LiteLLM (one intent, one approval, no orphaned-registration drift). A
   separate deregister-only capability remains for the
   keep-the-pod-running-while-debugging case.
5. **Scheduled script + agent evaluation: DECIDED 2026-08-03 —
   heartbeat-native** (The operator approved the recommendation in
   `2026-08-03-scheduled-script-verify-patterns.md`, the companion survey of
   k8s CronJob, Argo, CI pipelines, Temporal/DBOS and scheduled-agent
   products). One new `ActionSpec`, `sandbox.run_script`: the heartbeat
   schedule (governed data) fires it; it runs the already-synced script via
   the sandbox-runner Job (depends only on sandbox slice 1 — no gated exit);
   it captures the standard evaluation contract (exit code + `result.json` +
   log tail + declared artifacts dir) and hands the run record to the
   existing `create_and_run_task` path so the owning agent gets an
   evaluation task — failure always creates the task, success-creates-task
   is a per-schedule parameter. Nothing else is built: no CronJob-as-truth,
   no Job watcher, no Argo/Temporal. This also settles the original egress
   question: no per-call profiles — the scheduled run executes an
   already-reviewed script under the OWNING AGENT's egress grants
   (per-agent grants, decision 2 above). Slots into §10 after slice 1.

## 12. Repo facts checked while writing this

- `runtime/packs.py`: packs are frozen dataclasses (`Pack`, `GatedCapability`)
  bundling `tool_names` + `capabilities` + charter `guidance`; the existing
  `jira-propose` pack is the direct template for a future `mcp-propose` pack
  (one tool name, several `GatedCapability` entries).
- `gateway/capabilities.py`: the `Capability` registry entries carry
  `name`/`kind`/`gate`/`risk`/`holder`/`route`/`arguments`/`description`; every
  gated write here needs a matching entry, holder `"Executor"`.
- `tests/test_governance.py`: `test_registry_and_executor_dispatch_in_parity`
  is the exact parity mechanism this design's MCP guard test should mirror;
  `_BANNED_RUNTIME_IMPORTS` confirms `runtime/` may not import `gateway`,
  which is why the sandbox-runner must be its own service reachable only from
  the Executor side, never called directly by `runtime/` code.
- `runtime/litellm_manager.py`: confirms the read/propose split pattern
  (`advisory=True` builds hold only read packs, no propose tool) that
  `litellm.register_mcp_server` should follow — the same agent that manages
  MCP servers can plausibly get an advisory/consult mode later, at no extra
  design cost.
- `db/schema.sql` (`agent_grant`, ~line 304): confirmed the
  revoke-as-timestamp + `granted_by` + idempotent-seed-with-guard pattern that
  `agent_sandbox_source` and `agent_mcp_gate_override` are modeled on above.
- `runtime/tools.py:537` (`propose_jira_update`): confirmed the one-wrapper,
  `CallDeferred(metadata={"kind": ...})` pattern `propose_mcp_tool_call`
  reuses.

No contradictions surfaced between the brief's accumulated facts and the
current repo — the packs/capabilities/litellm-manager/governance-test shapes
described in the brief all matched what's on disk.
