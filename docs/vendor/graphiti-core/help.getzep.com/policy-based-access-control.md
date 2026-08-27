> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Policy-based access control

> Policy-based access control (ABAC) for Zep: attach policy sets to API keys for agents and to UserGroups for Memory MCP users to enforce least privilege over context.

Available to [Enterprise Plan](https://www.getzep.com/pricing) customers only.

## Overview

Policy-based access control gives you least-privilege control over context inside a project. It uses attribute-based access control (ABAC): reusable policy sets of allow and deny rules that Zep evaluates on each request to decide which actions a caller may perform and which context it may see.

Project-level isolation separates one project from another. Policies add a second level of control inside a project, on top of that isolation.

The same policy language applies to two kinds of principal:

#### [Agent access](/attribute-based-access-control)

Attach policies to the API key an agent authenticates with. Limits which actions and which classes of data each agent can reach.

#### [UserGroup access](/usergroup-access)

Attach policies to a UserGroup — a set of Zep users managed as one security subject. Every Memory MCP user in the group inherits its grants.

## How policies work

A policy set is a named, versioned collection of allow and deny rules. Each rule names a non-empty list of actions, an `effect` of `allow` or `deny`, and optional constraints. Rules narrow access along two dimensions:

* **By action** — which operations the caller may perform, such as read-only access or a block on destructive calls.
* **By class of data** — which context the caller may read within a graph, based on the metadata attached at ingestion.

Data-class rules match on **attributes**: the metadata you attach to episodes at ingestion — a source system, context type, sensitivity level, department, or any other key you define. Zep projects that metadata along episode associations onto every node, edge, community, and summary derived from the episode, so a rule can admit an object only when the object's projected metadata matches the required values exactly — an extra value from an uncovered source hides the object. See [episode metadata projection](/episode-metadata-projection) for how metadata propagates and [adding business data](/adding-business-data#episode-metadata) for how to attach it.

Each policy set has a `mode` of `off`, `report_only`, or `enforce`, so you can measure the impact of a policy before enforcing it. When several rules match, a matching deny takes precedence over any allow.

The principal determines how policies are attached and what the base posture is:

| Principal                   | Attach policies to                                     | Base posture                                      | Enforcement surface                                                                            |
| --------------------------- | ------------------------------------------------------ | ------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| API key (agent)             | The API key                                            | The key's role, `default_allow` or `default_deny` | SDK and API requests made with that key, when the key's `abac_mode` is not `off`               |
| UserGroup (Memory MCP user) | A managed UserGroup or the virtual **All Users** group | Default-deny                                      | Memory MCP [standalone graph authorization](/memory-mcp-server/standalone-graph-authorization) |

## Relationship to team access

Policy-based access control governs agents and other callers reaching project context. It is distinct from [managing team access](/role-based-access-control) (role-based access control, RBAC), which governs which humans can administer the account and its projects in the dashboard. Both live under [Governance](/governance).