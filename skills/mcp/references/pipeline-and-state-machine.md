# The MCP pipeline — capabilities, arguments, preconditions

Source of truth: `central_command/runtime/tools.py` (the `propose_mcp_*` tools
that build each `Proposal`) and `central_command/gateway/executor.py` (the
handlers that actually perform the write on approval). Pack: `mcp-propose`
(`central_command/runtime/packs.py`, 5 tool names) — granted to nobody by
default.

## The state machine

`mcp_server.status` moves one way, checked by every handler before it acts:

```
(no row) -> proposed/synced -> built -> deployed -> registered
                                                          \-> retired (terminal)
```

Each Executor handler's precondition is "the prior row exists in the right
status" — never "the agent said so". A handler call against a row in the
wrong status raises `ExecutorError` and nothing happens.

## 1. `mcp.sync_source` — the one sandbox exit

Tool: `propose_mcp_sync_source(server_id, paths, summary)`.

- `server_id` must match `^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$` and must **not**
  start with `cc-` (reserved for the system's own services) — checked in
  both `runtime/tools.py` and `gateway/executor.py` independently.
  Enforcement mirrors elsewhere: never trust the model twice for free.
- `paths` name files already written and tested inside the sandbox,
  relative, no `..`, no leading `/`. The tool itself calls
  `sandbox_client.read_file` for each path and builds `files: [{path,
  content}]` — the model never authors `content`. A **truncated read
  refuses the whole proposal** rather than syncing a partial file.
- A running byte total is capped at `settings.mcp_sync_max_bytes`; exceeding
  it refuses before any file is captured.
- The proposal also carries a **unified diff** against what's currently at
  `servers/<server_id>/<path>` (empty string if the file is new), and one
  `Evidence(kind="sandbox", ...)` record naming the sandbox id the bytes came
  from.
- Executor precondition: **none on the `mcp_server` row** (this is the step
  that creates it) — but it does require the target `servers/<id>/` tree to
  have a **clean git status** first (`_mcp_sync_source` in `executor.py`
  runs `git status --porcelain` and refuses if anything is already dirty:
  "an Executor commit must never mix with work already in the tree").
- On success: writes every file verbatim under `servers/<server_id>/`,
  `git add`, `git commit` with a message naming the proposal id and both the
  drafting and approving agent, `git push origin master` (a push failure is
  **non-fatal** — the local commit stands, only the push status is reported
  degraded), then `repo.upsert_mcp_server_synced(server_id, ...)`.

## 2. `mcp.build_image`

Tool: `propose_mcp_build(server_id, rationale)` — a "simple shape" proposal
(no sandbox read; the model authors only `rationale`).

- Executor precondition: `mcp_server.status` in `("synced", "built",
  "deployed", "registered")` — i.e. it must have passed sync review at least
  once. Also requires `settings.mcp_build_host` configured and
  `servers/<server_id>/Dockerfile` to exist.
- Build is **single-arch, pinned to one build host** (`settings.mcp_build_host`
  — the chromebox today): `tar -C <server_dir> -cf - . | ssh <host> podman
  build -t <ref> -f Dockerfile -`. No cross-arch build, no multi-arch
  manifest — per the design's §9.3 ratified decision, a newly agent-built
  server defaults to no-failover until an operator deliberately escalates it.
- **Immutable per-proposal image tag**: `docker.io/library/cc-mcp-<server_id>:<prop12>`
  where `<prop12>` is the last 12 hex characters of the executing proposal id
  (`_mcp_proposal_suffix`) — never `:latest`. There is no registry here to
  pin a digest against, so the "digest" recorded is actually
  `podman image inspect --format '{{.Id}}'` (the image id), used as the
  pinning value everywhere downstream calls it "digest".
- The built image is saved as an OCI archive and piped over SSH straight
  into `sudo k3s ctr -n k8s.io images import -` — no intermediate file on
  either box. Presence is then verified by an **exact-match** check against
  `ctr -n k8s.io images ls -q` (see `naming-and-plumbing-gotchas` for why
  this can't be a `grep -q` pipe).
- On success: `repo.mark_mcp_server_built(server_id, ref, digest)`.

## 3. `mcp.server_deploy`

Tool: `propose_mcp_deploy(server_id, rationale)` — same simple shape.

- Executor precondition: `status` in `("built", "deployed", "registered")`
  **and** both `image_ref` and `digest` recorded on the row.
- Requires `settings.mcp_deploy_kubeconfig` configured (a namespace-scoped
  kubeconfig for `cc-mcp`).
- Applies a `Deployment` + `Service` (as one `kind: List`) to namespace
  **`cc-mcp`**: 1 replica, `runtimeClassName: gvisor`,
  `automountServiceAccountToken: false`, **required** (not preferred) node
  affinity pinning to `chromebox` (the image was built single-arch there —
  no fallback node exists), `imagePullPolicy: IfNotPresent`, container port
  **8000**, `ClusterIP` Service on 8000.
- **Mandatory resource limits, refused even against its own generated
  manifest**: `_mcp_deployment_manifest` raises `ExecutorError` before apply
  if `resources.limits`/`requests` are missing — asserted every call, not
  trusted as a constant that can't drift. Defaults:
  `requests={memory: 128Mi, cpu: 100m}`, `limits={memory: 512Mi, cpu: 500m}`.
- Waits on `kubectl rollout status deployment/<server_id> --timeout=90s`
  before declaring success.
- On success: `repo.mark_mcp_server_deployed(server_id, "cc-mcp")`.

## 4. `litellm.register_mcp_server`

Tool: `propose_mcp_register(server_id, rationale, transport='http')`.

- Executor precondition: `status` in `("deployed", "registered")` and a
  `namespace` recorded. `transport` must be one of `http`, `sse`.
- **Idempotent**: if the row already carries a `litellm_alias`, the Executor
  calls `list_mcp_servers()` and returns "already registered — no
  double-create" if the proxy still lists it, rather than re-registering.
- Registers `http://<server_id>.<namespace>.svc.cluster.local:8000/mcp`
  under the **underscored** LiteLLM name (see `naming-and-plumbing-gotchas`).
- On success it does two more things, both **best-effort/non-fatal** (the
  registration itself already stands either way, but a failure here is
  reported LOUDLY in the outcome string, because nothing downstream will
  surface it otherwise):
  1. **Tool discovery** — `list_mcp_server_tools` seeds `mcp_tool` rows at
     `default_gate='human approval'` (the conservative default; an operator
     widens per tool/per agent afterward).
  2. **Key permission** — `add_mcp_server_to_key` adds the new server to the
     shared key's `object_permission.mcp_servers` (see
     `naming-and-plumbing-gotchas` for why this step exists at all).

## 5. `mcp.server_remove` — coupled, one approval

Tool: `propose_mcp_remove(server_id, rationale)`.

- Executor precondition: row exists and `status != "retired"`.
- `kubectl delete deployment/<id> service/<id> --ignore-not-found` (a pod
  already gone must never fail a legitimate removal).
- Deregisters from LiteLLM **only if** the row's `litellm_alias` is still
  listed by the proxy (mirrors registration's own idempotency check) —
  `deregister_mcp_server`, then best-effort `remove_mcp_server_from_key` to
  strip the id from the shared key's `object_permission.mcp_servers` (loud
  "key permission cleanup FAILED" note on failure, non-fatal to the removal).
- Flips `status='retired'` + `retired_at` — the row is **never deleted**.
  `mcp_tool` rows and any `mcp:<id>` grants are left untouched (inert by
  construction: a retired server's tools can't be reached because the
  `mcp_toolsets_for` wiring re-checks `status` via `list_mcp_tools`/gates at
  build time, not by deleting the grant).
- Archive-in-place: `servers/<id>/` source and built image tags are **never
  touched** — a later `mcp.server_deploy` (re-approved) can bring the same
  server back.
- A separate "deregister-only, keep the pod running" capability is discussed
  in the design doc for the debugging case but was **not built** as of
  2026-08-05 (`docs/STATUS.md`) — don't propose it as if it exists.

## `mcp.tool_call` — the one gated wrapper for every server's gated tools

Not part of the pipeline above — this is what a **granted, non-mcp-manager**
agent uses once a server is registered and some of its tools are gated.
`propose_mcp_tool_call(server_id, tool_name, arguments, rationale)`
(`runtime/tools.py`) checks the tool is known (`repo.list_mcp_tools`) and
that its *effective* gate for the calling agent is `human approval` before
building the proposal — an ungated tool refuses here, because it's already a
direct callable (see `sandbox-mechanics`'s note on gate resolution, or
`naming-and-plumbing-gotchas` for the tool-name shape). The Executor
(`_mcp_tool_call`) re-checks server status is `registered` and the tool is
known before dispatching through `litellm_client.call_mcp_tool` — never
trusts the approved proposal's arguments a second time for free. Result
strings are capped at 20,000 characters (`_MCP_TOOL_RESULT_CAP`).
