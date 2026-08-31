"""Per-capability argument SHAPE — the checks both tiers run from one list.

The runtime runs `validate_action_args` at propose time (a bad draft is handed
back to the model and never reaches the Decisions Inbox); the Executor runs
the same function before any action executes (the trust boundary never
assumes the runtime checked anything — an API-submitted proposal skips the
tool path entirely). It lives here, not in either tier, for the reason
`claim_supported` does: runtime may never import gateway.

Only SHAPE belongs here — required keys and closed enums, true of the
arguments alone. World-state checks (a uuid exists in Neo4j, a Jira project
exists) stay in the Executor: the world can change between propose and
approve, and those need reads the contract layer has no business making.

Found live 2026-08-29: one `graph.add_episode` intent took FOUR approvals —
`summary` instead of `episode_body`, no `name`, no `scope` — because the
Executor validated one field per round (two of them as bare KeyErrors) and
nothing looked at arguments before approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ArgSpec:
    required: tuple[str, ...] = ()
    enums: dict[str, tuple[str, ...]] = field(default_factory=dict)


ARG_SPECS: dict[str, ArgSpec] = {
    "graph.add_episode": ArgSpec(
        required=("name", "episode_body", "scope"),
        enums={"scope": ("shared", "private")},
    ),
    "graph.create_node": ArgSpec(required=("name", "group_id", "summary")),
    "graph.create_edge": ArgSpec(required=("source_uuid", "target_uuid", "name", "fact")),
    "graph.merge_nodes": ArgSpec(required=("keep_uuid", "drop_uuid")),
    "graph.update_node": ArgSpec(required=("uuid",)),
    "graph.delete_node": ArgSpec(required=("uuid",)),
    "graph.update_edge": ArgSpec(required=("uuid",)),
    "graph.delete_edge": ArgSpec(required=("uuid",)),
    # `mode` is optional and defaults to 'replace'; only the CLOSED set of
    # values belongs here. That the document exists is the Executor's check.
    "catalog.tag": ArgSpec(
        required=("document_id", "tags"),
        enums={"mode": ("replace", "add", "remove")},
    ),
}


def validate_action_args(capability: str, arguments: dict | None) -> list[str]:
    """Every shape problem with one action's arguments, in one message each —
    an empty list means the shape is fine. A capability with no spec passes:
    a spec is a promise of a check, never a guess."""
    spec = ARG_SPECS.get(capability.split("@", 1)[0])
    if spec is None:
        return []
    args = arguments or {}
    problems: list[str] = []
    missing = [k for k in spec.required if not args.get(k)]
    if missing:
        problems.append(
            f"{capability}: missing required argument(s) {', '.join(missing)} "
            f"(present: {', '.join(sorted(args)) or 'none'})"
        )
    for key, allowed in spec.enums.items():
        if key in args and args[key] not in allowed:
            problems.append(
                f"{capability}: {key}={args[key]!r} is not one of {', '.join(allowed)}"
            )
    return problems
