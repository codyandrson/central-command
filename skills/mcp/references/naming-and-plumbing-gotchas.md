# Naming and plumbing gotchas

These are the facts that cost a live 401, a silently-empty tools list, or a
false-negative test before they were understood. Source:
`central_command/gateway/executor.py`, `central_command/runtime/packs.py`,
`central_command/integrations/litellm.py`, `docs/STATUS.md`.

This file is deliberately **thin on generic virtual-key doctrine** — budgets,
`create_key`/`update_key`/`delete_key` semantics, the Enterprise-tags 403,
credentials-by-name — all of that already lives in
`skills/litellm/references/keys-and-teams.md` and applies unchanged here.
What follows is only what's specific to MCP servers on top of that.

## LiteLLM forbids `-`; Kubernetes requires it

LiteLLM's `/v1/mcp/server` rejects a server name containing `-` outright
("Server name cannot contain '-'", live 400, 2026-08-04). Kubernetes object
and DNS names require RFC1123 form, which needs `-` and forbids `_`. So:

- **`mcp_server.id` stays the k8s-valid canonical id** everywhere in this
  system (e.g. `echo-demo`) — never store the underscored form as the
  canonical id.
- The **proxy-side name** is `_litellm_server_name(server_id)` =
  `server_id.replace("-", "_")` (e.g. `echo_demo`).
- This translation is **duplicated in exactly two places on purpose** —
  `gateway/executor.py::_litellm_server_name` and
  `runtime/packs.py`'s `_mcp_live_toolset` (inlined there as
  `server_id.replace("-", "_")`) — because `runtime/` may never import
  `gateway/`. If you change one, change the other; a guard test does not
  exist for this specific duplication the way it does for `_mcp_servers_root`
  (below), so check both by hand.
- The k8s **Service DNS name keeps the hyphens** — it's a k8s object, not a
  proxy-side name: `<server_id>.cc-mcp.svc.cluster.local:8000`.

## The gateway namespaces tool names; the filter must accept both shapes

LiteLLM's MCP gateway returns tool names as `{server_name}-{tool}` (live-
probed 2026-08-04: `tools/list` on `/echo_demo/mcp` returned
`"echo_demo-echo"`), while `mcp_tool.tool_name` stores the **bare** name the
server itself declares (`echo`). `_mcp_live_toolset` in `runtime/packs.py`
builds its allowlist as the union of both shapes:

```python
allowed = frozenset(ungated_tool_names) | frozenset(
    f"{litellm_name}-{t}" for t in ungated_tool_names
)
```

Before this fix (found live 2026-08-05) the filter compared only bare names
against the gateway's namespaced ones, so an ungated toolset attached with
**zero** tools and a test built the same way would "pass" while proving
nothing. If you write or advise on a filter/allowlist against this gateway,
it must accept both shapes.

## A virtual key sees NO MCP servers until it's in `object_permission.mcp_servers`

Even a fully-registered server is invisible to a key that hasn't been
granted it — the proxy answers `200` with an **empty tools list**, a silent
failure with no error to catch (live finding, 2026-08-05). This is why:

- `litellm.register_mcp_server`'s Executor handler **couples**
  `add_mcp_server_to_key(settings.llm_api_key, litellm_id)` right after a
  successful registration (`integrations/litellm.py`'s
  `add_mcp_server_to_key`: `GET /key/info` first, then `POST /key/update`
  with the widened list — merge, never clobber; idempotent if already
  present).
- `mcp.server_remove`'s handler mirrors it with `remove_mcp_server_from_key`
  (narrow, never clobber; idempotent no-op if already absent).
- Both are **best-effort/non-fatal** — the registration or removal itself
  already stands either way — but both report failure **loudly** in the
  outcome string, because nothing downstream will surface a dangling id
  otherwise.
- When per-agent LiteLLM keys eventually land, this coupling point is
  expected to move to grant time (which agent's key depends on the grant,
  not the registration) — not built as of 2026-08-05.

`skills/litellm/references/keys-and-teams.md` covers `object_permission`
nowhere — this is the one place that doctrine lives for MCP.

## `/mcp-rest/tools/call` wants `server_id` in the BODY, and `Authorization: Bearer`

Two proxy-side facts verified live, both easy to get wrong from generic
LiteLLM docs:

- `POST /mcp-rest/tools/call` needs `server_id` in the **request body**, not
  a query param or path segment — a name it never registered 404s.
- The `/{server}/mcp` route (used by ungated, directly-callable toolsets)
  401s **"Malformed API Key"** on the `x-litellm-api-key` header that
  LiteLLM's own docs name for MCP — it wants **`Authorization: Bearer
  <key>`** instead (`runtime/packs.py::_mcp_live_toolset` sets this
  explicitly, with the comment recording the live 401 that found it).

## `_mcp_servers_root()` must resolve to the same path on both sides

`runtime/tools.py::_mcp_servers_root` and `gateway/executor.py::_mcp_servers_root`
each derive the repo's `servers/` directory from their own file's location
(`Path(__file__).resolve().parents[2] / "servers"` on both sides, or
`settings.mcp_servers_root` if set). They were **off by one directory level**
on the first cutover — the Executor wrote to `central_command/servers/` while
the runtime diffed against the repo-root `servers/`, so the operator
reviewed a diff against a path the write never touched. A guard test
(`test_servers_root_is_the_same_path_on_both_sides`) pins this now. If you
ever add a third place that needs the servers root, make it call one of
these two functions rather than re-deriving the path a third way.

## `ctr images ls | grep -q` inverts under `pipefail`

`mcp.build_image`'s post-import verification captures `ctr -n k8s.io images
ls -q` into a variable **first**, then checks membership in Python
(`ref not in ls_out.splitlines()`), rather than piping to `grep -q`. Under
`set -o pipefail`, `grep -q` exits at its first match and SIGPIPEs `ctr`,
which then reports failure — so a naive pipe would report "not found" even
when the image demonstrably was. This exact bug broke
`migrate-from-docker.sh`'s own preflight once; the MCP build handler avoids
it by construction. Don't reintroduce the piped form if you touch this code.
