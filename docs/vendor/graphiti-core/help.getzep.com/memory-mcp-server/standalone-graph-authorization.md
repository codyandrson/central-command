> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Authorizing standalone graphs

Standalone graph authorization determines which project graphs an authenticated
Memory MCP user can discover, search, and write. It does not change access to
the user's own memory graph, which remains bound to the authenticated identity.

## Authorization modes

Each project connection has one standalone graph authorization mode:

| Mode           | Availability                                                                                                                  | Standalone graph access                                                                                                                                                                                                        |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Project-wide   | Connections with standalone graph access                                                                                      | Every authenticated MCP user can list and search every standalone graph in the token-bound project. Writes also require the `graph:write` scope, **Allow writes**, and the account-level writes kill switch to permit writes.  |
| UserGroup ABAC | Zep managed cloud Enterprise accounts with policy-based access control; BYOC when the deployment UserGroup ABAC setting is on | Default-deny. A user can discover or search a standalone graph only when an enforced UserGroup policy grants `graph.search` for that graph. Writing also requires an enforced `graph.add` grant and every existing write gate. |

Connections use project-wide access until an administrator explicitly moves
them to UserGroup ABAC. In Zep managed cloud that move requires the Enterprise
plan and the ABAC entitlement. In BYOC it also requires the deployment
UserGroup ABAC setting. Accounts without the ABAC entitlement remain
project-wide; the missing entitlement does not disable standalone graph access.

Moving a connection to UserGroup ABAC is one-way because moving it back would
silently widen access. To respond to an incident or configuration problem,
disable **Allow standalone graphs** or disable the connection. Re-enabling
standalone access resumes the same UserGroup ABAC mode.

## How UserGroup policies apply

This mode grants standalone graph access through [UserGroup access](/usergroup-access),
which manages the groups and their attached policy sets.

Zep evaluates the authenticated user through the virtual **All Users** group
and every managed UserGroup where the user has an active membership. Policy
sets attached through more than one group are evaluated once.

UserGroup ABAC is default-deny:

* An enforced allow must match the action and the standalone graph UUID.
* An enforced deny takes precedence over every allow.
* `report_only` policies record what they would decide but do not grant,
  deny, or filter access.
* Membership removal, policy detachment, policy updates, and mode changes
  affect the next MCP operation.

The graph directory and `list_graphs` use the same `graph.search` grants as
`search_graph_in`. Hidden graphs do not appear in directory results, counts,
search ranking, or pagination. A client receives the same not-found response
for an unknown graph and a graph it is not authorized to use. See
[Graph directory](/graph-directory) for the directory contract across API, SDK,
and MCP.

## Define graph resource policies

Graph resource selectors use the standalone graph's public `uuid`, not its
customer-defined `graph_id` or internal graph-service identifier. Opt in to
resource selectors with `resource_selector_version: 1`.

```yaml
policy_set:
  name: support_graph_access
  description: Let the Support UserGroup search and add memory to one graph.
  mode: enforce
  spec:
    resource_selector_version: 1
    policies:
      - id: search_support_graph
        type: authorization
        effect: allow
        actions: [graph.search]
        resources:
          graph_uuids:
            - 2f6d9ff7-8f63-4d4d-a7f0-429eb75546d6
      - id: write_support_graph
        type: authorization
        effect: allow
        actions: [graph.add]
        resources:
          graph_uuids:
            - 2f6d9ff7-8f63-4d4d-a7f0-429eb75546d6
```

Attach the policy set to a managed UserGroup or **All Users**. A rule without
`resources` is action-wide and applies to every standalone graph, including
graphs created later. Use action-wide grants deliberately.

Resource-capable policy sets cannot attach to API keys. API-key policies keep
their existing action and source-attribute behavior.

## Move a connection to UserGroup ABAC

Before changing the mode, create the required UserGroups, memberships, policy
sets, and attachments. Then use **Settings ▸ MCP**:

#### Run preflight

Review active users, UserGroups, policy modes, resource-capable policies,
dead graph references, and a bounded sample of access changes. No enforce
allow means every standalone graph will be hidden.

#### Resolve warnings

Review dead graph references and legacy incompatible attachments, removing
them when they are unintended. Confirm that action-wide grants are
intentional.

#### Confirm default-deny

Acknowledge that the transition changes the connection from project-wide
access to explicit grants. If action-wide allows exist, acknowledge them
separately.

#### Enable UserGroup ABAC

Zep reruns preflight against current connection and policy state before
changing the mode. Transition is unavailable unless durable authorization
logging is configured, the account is eligible (Enterprise on Zep managed
cloud), and — on BYOC — the deployment UserGroup ABAC setting is on. The
next MCP operation uses live UserGroup membership and policy state.

## Writes

ABAC never replaces the Memory MCP write gates.

In project-wide mode, a standalone write succeeds only when:

* the token has the `graph:write` scope;
* the connection's **Allow writes** setting is on;
* standalone graph access is on; and
* the account-level writes kill switch permits writes.

UserGroup ABAC adds a matching enforced `graph.add` allow to those requirements.
A `graph.search` grant or `readonly` rule never grants writes. If a matching
write policy requires source-attribute filtering, the write fails closed
because a new object has no existing source metadata to evaluate.

## Attribute-filtered search

A graph grant may include the same source attributes used by
[agent access](/attribute-based-access-control). For
`search_graph_in`, Zep applies those constraints inside graph search before
ranking and limits. It never returns an unfiltered result when a required
filter cannot be applied.

## Revocation and rollback

To revoke one user's graph access, remove the user from the granting UserGroup
or detach the policy. To revoke a graph for everyone, remove or disable its
allow policy. These changes apply to the next MCP operation.

To stop all standalone access without widening permissions, turn off **Allow
standalone graphs** or disable the connection. Do not use project-wide mode as
a rollback from UserGroup ABAC.