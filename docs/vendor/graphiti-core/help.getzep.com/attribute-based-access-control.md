> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Agent access

> Attribute-based access control (ABAC) for agents: attach policies to API keys to limit which actions and context each agent can reach inside a project.

Available to [Enterprise Plan](https://www.getzep.com/pricing) customers only.

## Overview

Multi-agent systems need least privilege over context: each agent should reach only the actions and data its task requires. Agent access solves that with attribute-based access control (ABAC) — reusable policies, attached to the API key an agent authenticates with, that decide what the agent may do and which context it may see inside a project.

Agent access is one half of [policy-based access control](/policy-based-access-control). The other half, [UserGroup access](/usergroup-access), applies the same policy engine to Memory MCP users through the groups they belong to.

Project-level isolation separates one project from another. Policies add a second level of control inside a project. API clients authenticate with an API key and Zep evaluates the policies attached to that key.

Policies narrow access along two dimensions:

* **By action** — which operations the agent may perform, such as read-only access or a block on destructive calls.
* **By class of data** — which context the agent may read within a graph. A single graph may hold several classes of data (by sensitivity, department, or source system, for example), and a key is restricted to the classes it may read, based on the metadata attached at ingestion.

A key with no ABAC policies has full read and write access to project data. Agent access is opt-in per key.

## How a request is evaluated

ABAC evaluates a request in two layers, in order:

1. **Action layer.** Determines whether the request action is permitted. The outcome applies to the whole request: it proceeds, or it returns `403`.
2. **Attribute layer.** For a permitted request that returns data, determines which objects appear in the response. It filters search, list, and read results by the source metadata of each object. It never overrides an action-level denial; it only narrows what a permitted read returns.

Three controls determine the result:

* **Role** — the key's base posture, either `default_allow` or `default_deny`.
* **Policy sets** — reusable collections of allow and deny rules attached to the key.
* **Mode** — set on both the key and each policy set, one of `off`, `report_only`, or `enforce`.

## API key roles

Every API key has a role that sets its starting permissions before any policy is evaluated. You choose the role when you create the API key in the [Zep web app](https://app.getzep.com/).

| Role                      | Starting permissions                       | Policies                     |
| ------------------------- | ------------------------------------------ | ---------------------------- |
| `default_allow` (default) | Full read and write access to project data | Restrict access from there   |
| `default_deny`            | Project access only, with no data access   | Must grant access explicitly |

A `default_allow` key with no policies has full data access, which preserves the behavior of keys created before ABAC. A `default_deny` key authenticates and reaches the API, but every data operation returns `403` until a policy grants access.

Use `default_deny` for keys issued to external integrations, partners, or limited-scope agents, where each permitted action should be enumerated explicitly.

## Policy sets and modes

A policy set is a named, versioned collection of rules. One set may attach to many keys, and one key may have many sets attached. A policy set is written as YAML:

```yaml
policy_set:
  name: support_agent_readonly        # unique within the project
  description: Read-only access for support tooling.
  mode: enforce                       # off | report_only | enforce
  spec:
    policies:
      - id: allow_reads
        type: authorization
        effect: allow                 # allow | deny
        actions: [readonly]
```

Each rule has an `id`, an `effect` of `allow` or `deny`, a non-empty list of `actions`, and an optional `attributes` map for source-based rules.

Mode exists at two levels. The key's `abac_mode` is the master switch, and each policy set has its own `mode`. A set is evaluated only when the key is not `off` and the set is not `off`.

| Mode          | Effect                                                                                                         |
| ------------- | -------------------------------------------------------------------------------------------------------------- |
| `off`         | Not evaluated.                                                                                                 |
| `report_only` | Evaluated and logged, but never blocks a request or filters a result. Used to measure impact before enforcing. |
| `enforce`     | Evaluated and applied.                                                                                         |

When several rules match, denial takes precedence, otherwise a matching allow rule produces an allow, otherwise the result is neutral and the role base decides. What a matching deny produces depends on the rule: a deny without `attributes` is an action-level denial and the whole request returns `403`, while a deny with `attributes` excludes only the matching objects from an otherwise-permitted read — search and list results drop them, and single-object reads return `404`.

## Action-level policies

Action-level policies gate actions. An action is the dotted name of one SDK method or API route, such as `thread.get`, `graph.search`, `thread.add_messages`, or `user.delete`.

### The readonly macro

`readonly` is a macro that expands to every action that does not mutate project data. It is computed at evaluation time, so new read-only endpoints are covered automatically. Write actions are always listed explicitly.

| Action                                                     | Read-only |
| ---------------------------------------------------------- | --------- |
| `thread.get`, `thread.list_all`, `thread.get_user_context` | Yes       |
| `graph.search`                                             | Yes       |
| `user.get`, `user.list_ordered`                            | Yes       |
| `thread.create`, `thread.delete`                           | No        |
| `thread.add_messages`, `graph.add`, `user.delete`          | No        |

This table is illustrative, not an exhaustive list of actions.

### Example: a read-only key

A key that may read project data but write nothing. The recommended pattern is a `default_deny` key that is granted reads:

```yaml
policy_set:
  name: readonly_everything
  mode: enforce
  spec:
    policies:
      - id: allow_all_reads
        type: authorization
        effect: allow
        actions: [readonly]
```

Attached to a `default_deny` key in `enforce` mode, read requests succeed and write requests return `403`.

### Example: read access plus one write

Allow rules combine, so a literal write grant may be added alongside the macro:

```yaml
policy_set:
  name: reader_plus_message_writes
  mode: enforce
  spec:
    policies:
      - id: allow_reads
        type: authorization
        effect: allow
        actions: [readonly]
      - id: allow_message_writes
        type: authorization
        effect: allow
        actions: [thread.add_messages]
```

### Example: blocking specific actions

Starting from a `default_allow` key, a deny rule blocks destructive actions regardless of other rules, because denial takes precedence:

```yaml
policy_set:
  name: block_deletes
  mode: enforce
  spec:
    policies:
      - id: no_deletes
        type: authorization
        effect: deny
        actions: [thread.delete, user.delete, graph.edge.delete]
```

## Source-based policies

Action-level policies decide whether a read runs. Source-based policies decide which objects that read returns. They are the mechanism behind access control by class of data within a graph.

### Tagging data at ingestion

Every object derived from ingestion carries [effective metadata](/episode-metadata-projection): the combined metadata of all the episodes that contributed to it. To make data eligible for source-based policies, attach metadata when you add it to the graph; see [Adding business data](/adding-business-data#episode-metadata) for how. Metadata accepts up to 10 keys, with scalar values or arrays of scalars.

Every node, edge, community, and summary derived from those episodes inherits the metadata in its effective metadata. See [Episode metadata projection](/episode-metadata-projection) for how metadata propagates to derived objects.

### Matching is exact set equality

A source-based rule adds an `attributes` map. Each key maps to a non-empty list of required values. An object satisfies a constraint on a key when its effective value set for that key is exactly equal to the required set, neither a subset nor a superset.

| Object's effective `department` | Constraint `department: [finance]`          | Result  |
| ------------------------------- | ------------------------------------------- | ------- |
| `{finance}`                     | Exact match                                 | Visible |
| `{finance, engineering}`        | A source outside the grant also contributed | Hidden  |
| `{}` (key absent)               | The empty set does not equal `{finance}`    | Hidden  |
| `{engineering}`                 | Different value                             | Hidden  |

The superset case is the important one. Effective metadata is the union over every contributing source, so an extra value means a source the grant does not name helped produce the object. Equality fails closed in that case to avoid returning data from an uncovered source.

When a constraint names several keys, they combine with AND. An object is admitted only when its effective value set matches exactly for every named key.

### Example: scope a key to one class of data

A key that may search the graph but should only return objects sourced entirely from the `finance` department:

```yaml
policy_set:
  name: finance_only
  mode: enforce
  spec:
    policies:
      - id: finance_objects
        type: authorization
        effect: allow
        actions: [graph.search, graph.node.get, graph.edge.get]
        attributes:
          department: [finance]
```

The search runs, and the result set contains only objects whose effective `department` is exactly `{finance}`.

### Example: hide objects with a deny rule

Attributes also work on `deny` rules, with the opposite effect. On a key that may already read the data, a deny rule with attributes hides the objects whose effective metadata matches. Denial takes precedence, so matching objects are removed even when an allow rule would otherwise return them.

```yaml
policy_set:
  name: hide_restricted
  mode: enforce
  spec:
    policies:
      - id: deny_restricted
        type: authorization
        effect: deny
        actions: [graph.search, graph.node.get, graph.edge.get]
        attributes:
          classification: [restricted]
```

Allow rules combine disjunctively. An unconstrained allow for the same action, such as a bare `readonly` grant, makes every object eligible and nullifies a sibling source constraint. To enforce a positive source restriction, do not also grant an unconstrained allow for that action.

### What a caller sees

Filtering happens during the read, so pagination and limits operate over the visible set.

| Request                       | Result for a non-visible object                                                          |
| ----------------------------- | ---------------------------------------------------------------------------------------- |
| Search or list                | Silently absent. A result with no visible objects is a normal empty `200`, not an error. |
| Read of a single object by id | `404 Not Found`, so the caller is unable to determine whether the object exists.         |

## Managing policies

ABAC management is restricted to account administrators; project API keys are the subjects of ABAC and may not manage it. Policy sets are created, attached, and detached with the `zepctl` CLI. The dashboard is used to set an API key's role and to review ABAC decisions.

### Dashboard

When ABAC is enabled for an account, the dashboard exposes the key role at creation, an editable role with a security-change warning, a computed capabilities badge, and the ABAC decision for each request on the API log view.

### zepctl

`zepctl` authenticates through the browser and manages policy sets and keys:

```bash
zepctl auth login                  # authenticate
zepctl config set-project          # select the project to operate on

# Policy sets
zepctl policy-set validate --file policy.yaml   # validate without persisting
zepctl policy-set create   --file policy.yaml
zepctl policy-set list
zepctl policy-set update <policy-set-uuid> --file policy.yaml
zepctl policy-set delete <policy-set-uuid>

# API keys
zepctl api-key settings set <key-uuid> --mode report_only   # off | report_only | enforce
zepctl api-key policy-sets attach <key-uuid> <policy-set-uuid>
zepctl api-key policy-sets detach <key-uuid> <policy-set-uuid>

# Dry-run evaluation, with no state change
zepctl api-key evaluate <key-uuid> --action thread.get
zepctl api-key explain  <key-uuid> --action thread.get
```

## Rolling out with report-only

`report_only` mode evaluates policies and records the result without changing the response. Use it to measure the impact of a policy before enforcing it.

### Author and attach the policy

Write the policy set in `enforce` mode and attach it to the key, but set the key's `abac_mode` to `report_only`. No request is blocked or filtered yet.

### Let traffic flow

Each request records the decision it would have reached under enforcement, including the action, the outcome, and whether the result differs from the key's current behavior.

### Measure the impact

Review the requests that would change. The API log view filters by ABAC outcome and mode, so you may list exactly the requests that would begin to fail or be filtered under enforcement.

### Enforce

When the set of affected requests matches your expectation, set the key's `abac_mode` to `enforce`.

## Auditing

ABAC produces two kinds of records:

* **Management operations**, such as creating, updating, or deleting a policy set, attaching or detaching a set, and changing a key's role or mode, are recorded as audit events.
* **Runtime decisions** are recorded on each API log record, with the evaluated action, the outcome, and the mode.

The API log view filters by ABAC outcome and mode, which supports both the report-only rollout above and ongoing review of enforced policies.