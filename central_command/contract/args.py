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
    # Groups of keys of which AT LEAST ONE must be present and non-empty.
    any_of: tuple[tuple[str, ...], ...] = ()
    # Dotted path → inclusive maximum. A value present at that path must be a
    # number no greater than the ceiling; absent passes.
    ceilings: dict[str, float] = field(default_factory=dict)


# LiteLLM's per-token cost fields — the only price vocabulary a proposal may
# carry, shared with the Executor (which overwrites them from the credential's
# catalog) and the spend repair script. Contract-level because both tiers
# check it and contract has no dependencies.
COST_FIELDS = (
    "input_cost_per_token", "output_cost_per_token",
    "cache_read_input_token_cost", "cache_creation_input_token_cost",
)

# USD per TOKEN. The dearest model on any public card is around 1.5e-4; a
# value above a cent per token is a unit error — the per-MILLION card price
# written as the per-token price (2026-09-13: nine gateway models registered
# at 2.0/7.5 instead of 2e-7/7.5e-7, and the probe's 12 requests each booked
# $80,589 of spend). LiteLLM answers 200 and prices every request with it,
# so this is the kind of failure a shape guard exists for.
COST_CEILING = 0.01
_COST_CEILINGS = {
    f"{parent}.{f}": COST_CEILING
    for parent in ("model_info", "litellm_params", "extra")
    for f in COST_FIELDS
}


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
    "graph.rescope_episode": ArgSpec(required=("episode_uuid", "group_id")),
    # `mode` is optional and defaults to 'replace'; only the CLOSED set of
    # values belongs here. That the document exists is the Executor's check.
    "catalog.tag": ArgSpec(
        required=("document_id", "tags"),
        enums={"mode": ("replace", "add", "remove")},
    ),
    # mail actions (2026-09-12): both handlers subscript these. `url` is also
    # RE-DERIVED by the Executor from the mailbox — the spec only says it must
    # be present, the Executor says it must be the message's own.
    "mail.report_spam": ArgSpec(required=("provider_uuid",)),
    "mail.unsubscribe": ArgSpec(required=("provider_uuid", "url")),
    # autodiscovery.skip (2026-09-13): the Executor subscripts both. The list
    # is checked for shape only; that the ids exist in a catalog is not a
    # world-state fact worth a call — an unknown id on the skip list is inert.
    # `vendors` (2026-09-14) names whole groups; the Executor expands them.
    "autodiscovery.skip": ArgSpec(required=("credential_name",),
                                  any_of=(("model_ids", "vendors"),)),
    # litellm model writes (2026-09-14): every handler subscripts these, and
    # a per-token price is a shape fact — see COST_CEILING. `extra` is
    # add_model's litellm_params; update_model names it litellm_params.
    "litellm.add_model": ArgSpec(required=("model_name", "model"), ceilings=_COST_CEILINGS),
    "litellm.update_model": ArgSpec(required=("model_id",), ceilings=_COST_CEILINGS),
    "litellm.delete_model": ArgSpec(required=("model_id",)),
}


def _at_path(args: dict, path: str):
    node = args
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


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
    for group in spec.any_of:
        if not any(args.get(k) for k in group):
            problems.append(
                f"{capability}: at least one of {', '.join(group)} is required "
                f"(present: {', '.join(sorted(args)) or 'none'})"
            )
    for key, allowed in spec.enums.items():
        if key in args and args[key] not in allowed:
            problems.append(
                f"{capability}: {key}={args[key]!r} is not one of {', '.join(allowed)}"
            )
    for path, ceiling in spec.ceilings.items():
        value = _at_path(args, path)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            problems.append(f"{capability}: {path}={value!r} is not a number")
            continue
        if number > ceiling:
            problems.append(
                f"{capability}: {path}={value!r} exceeds {ceiling} — prices are USD per "
                f"TOKEN, not per million (a $2.00/1M card price is 2e-06). Leave cost "
                f"fields out: the Executor copies them from the credential's catalog."
            )
    return problems
