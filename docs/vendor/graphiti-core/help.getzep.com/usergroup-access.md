> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# UserGroup access

> UserGroup access in Zep: group Zep users into managed UserGroups and the virtual All Users group, attach ABAC policies, and control which standalone graphs Memory MCP users can reach.

Available to [Enterprise Plan](https://www.getzep.com/pricing) customers only.

## Overview

UserGroup access applies [policy-based access control](/policy-based-access-control) to your users instead of to an API key. You attach policy sets to a **UserGroup** — a set of Zep users managed as one security subject — and every user in the group inherits its grants.

A UserGroup is a security grouping, not a data container. It is distinct from a [user graph](/users-and-user-graphs), which stores one user's context, and from a standalone graph, which stores shared context. Grouping users for access control does not move or merge their data.

Today, UserGroup grants are enforced for [Memory MCP standalone graph authorization](/memory-mcp-server/standalone-graph-authorization): a connection in UserGroup ABAC mode decides which standalone graphs each authenticated Memory MCP user can discover, search, and write. Ordinary API-key traffic is governed by [agent access](/attribute-based-access-control) instead, and is unaffected by UserGroup policies.

## UserGroups

A project has two kinds of UserGroup:

* **All Users** — a virtual group that always contains every active user in the project, including users created later. It cannot be renamed, deleted, or have its membership edited. Every project has exactly one, even with zero users.
* **Managed UserGroups** — groups you create and name, with membership you administer explicitly. Use them to grant a subset of users access that **All Users** should not receive.

Members of a UserGroup are the same [users](/users-and-user-graphs) you manage elsewhere in the project, referenced by their user UUID. Membership is direct — a UserGroup cannot contain another UserGroup — and is managed manually; Zep does not sync it from an identity provider. Deleting a user removes it from every group.

## Effective UserGroups

Zep evaluates a user through their **effective UserGroups**: the virtual **All Users** group plus every managed UserGroup where the user has an active membership. Policy sets attached through more than one group are evaluated once.

Evaluation is default-deny. A user reaches a resource only when an enforced allow rule matches, and an enforced deny takes precedence over every allow across the combined set of groups. Membership changes, policy attachments, and policy edits apply to the next operation.

## Attaching policies

You attach authorization policy sets to a managed UserGroup or to **All Users**. Retention policy sets cannot attach to a UserGroup.

To grant access to specific standalone graphs, a policy opts in to resource selectors with `resource_selector_version: 1` and lists the graphs by their public `uuid`:

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

A rule without `resources` is action-wide and applies to every standalone graph, including graphs created later. The [standalone graph authorization guide](/memory-mcp-server/standalone-graph-authorization) covers how these grants map to Memory MCP tools, directory filtering, attribute-filtered search, write gates, and migrating a connection to UserGroup ABAC.

## Managing UserGroups

UserGroup management is restricted to account administrators.

* **Dashboard** — the **Users & Groups** section lists **All Users** and your managed groups, and lets you create groups, edit membership, and attach or detach policy sets.
* **`zepctl`** — the `user-group` commands manage groups, membership, and policy attachments, and the `user-group` and `user` dry-run commands report what a group or user would be allowed without changing any state. Create the policy set first, then attach it:

```bash
zepctl auth login
zepctl config set-project

# Create the policy set from the YAML above
zepctl policy-set validate --file policy.yaml
zepctl policy-set create   --file policy.yaml

# Groups, membership, and attachments
zepctl user-group list
zepctl user-group create --name Support --description "Support-facing users"
zepctl user-group members add <group-uuid> <user-uuid>
zepctl user-group policy-sets attach <group-uuid> <policy-set-uuid>

# Dry-run evaluation, with no state change
zepctl user-group evaluate <group-uuid> --action graph.search --graph <graph-uuid>
zepctl user evaluate <user-uuid> --action graph.search --graph <graph-uuid>
```

Policy-set authoring and the shared `zepctl` commands also appear under [agent access](/attribute-based-access-control#zepctl).