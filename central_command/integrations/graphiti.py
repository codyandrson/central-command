"""READS of the Graphiti knowledge graph, in-process through graphiti-core
(design record 2026-10-04, D1/D6). The module name and every read's signature
and result keys are the ones the MCP-server client had, so agent tools, routes
and their tests changed at the edges only.

Per DESIGN §3, **reading is layered, writing is gated**: everything here is a
read, ungated and safe for the runtime tier (agents pull context freely). There
is no write in this module. Episodes are written by the durable ingest worker
(`integrations/graphiti_ingest.py`) after the Executor enqueues an APPROVED
episode; curation is `integrations/neo4j_writer.py`. The runtime tier may
import this module and none of those (tests/test_governance.py) — which is why
the Graphiti object, which carries write methods, is reached here only through
a FUNCTION-LOCAL import of `graphiti_client`: importing this module loads no
write path, and the import graph stays checkable. The graph never ingests raw
email text, only distilled claims (D13: store refs, not bodies).

Search never calls `Graphiti.search()`: it assigns `.limit` on a module-level
recipe that `add_episode` also reads for its dedupe and invalidation
candidates, so one search would change extraction for the life of the
process. Every search goes through `search_()` with a DEEP COPY of the recipe
and the limit set on the copy — which also fixes the server's own limit bug
(it sliced a ten-result recipe, so asking for 25 facts returned ten).

Group tenanting: reads span the preserved homelab graph (`main`) plus
Central Command's own group; writes land in Central Command's shared group by
default, or in an agent's own PRIVATE partition (`central_command_<agent_id>`,
see `private_group`) when the approved proposal's scope says so (D11-r1: agents
have a private partition; every write is still gated — private only changes
WHERE the episode lands, never whether it needed approval). A read given an
`agent_id` additionally spans that agent's own private partition, so an agent
can recall its own past private episodes but not another agent's.
"""

from __future__ import annotations

import re

from central_command.config import settings
from central_command.integrations import neo4j_reader

# graphiti_core's own rule for a group id (`helpers.validate_group_id`).
GROUP_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


class GraphitiError(Exception):
    pass


def _client():
    # Function-local on purpose: see the module docstring (trust boundary).
    from central_command.integrations import graphiti_client

    return graphiti_client


async def steward_map() -> dict[str, str]:
    """`{steward_group: agent_id}` for every ACTIVE roster agent that stewards
    a domain group. Domain stewardship (2026-08-22): a steward's group lives
    INSIDE the shared read set (every agent reads it) but is that agent's
    primary write target — this map is what both `_read_groups` (read-side
    widening) and the read-tool attribution stamp (`runtime/tools.py`) key off.
    ponytail: fresh fetch per call, no cache — fine at current roster size,
    revisit if this path gets hot."""
    from central_command.db import repo as db_repo

    agents = await db_repo.list_agents(include_retired=False)
    return {
        a["steward_group"]: a["id"]
        for a in agents
        if (a.get("steward_group") or "").strip()
    }


async def _read_groups() -> list[str]:
    groups = [g.strip() for g in settings.graph_read_groups.split(",") if g.strip()]
    for group_id in (await steward_map()).keys():
        if group_id not in groups:
            groups.append(group_id)
    return groups


def private_group(agent_id: str) -> str:
    """An agent's own graph partition — never read by another agent.

    Underscore separator, NOT a colon: graphiti_core rejects any group_id
    outside ``[a-zA-Z0-9_-]`` — under the retired MCP server that was a silent
    drop that still acked (found 2026-08-15: 25 approved episodes lost); the
    ingest worker now fails such a job loudly. ``tests/test_graph_scope.py``
    pins the charset."""
    return f"{settings.graph_write_group}_{agent_id}"


async def groups_for(agent_id: str | None) -> list[str]:
    groups = await _read_groups()
    return groups + [private_group(agent_id)] if agent_id else groups


# --- result shapes (the keys the MCP server returned; callers depend on them) ---


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _no_embedding(attributes: dict | None) -> dict:
    """Upstream's readers surface every property they do not know as an
    ATTRIBUTE, so the `embedding_model`/`embedding_dimensions` stamps the
    curation writer wrote until v2.61.0 (and that `scripts/oneoff/
    reembed_graph.py` still writes) would ride into agents' results. Any key
    containing 'embedding' is dropped, as the retired server dropped it."""
    return {k: v for k, v in (attributes or {}).items() if "embedding" not in k.lower()}


def node_result(node) -> dict:
    """An EntityNode as the server's `search_nodes` returned it: no embedding
    anywhere in it (an attribute key containing 'embedding' included)."""
    return {
        "uuid": node.uuid,
        "name": node.name,
        "labels": list(node.labels or []),
        "created_at": _iso(node.created_at),
        "summary": node.summary,
        "group_id": node.group_id,
        "attributes": _no_embedding(node.attributes),
    }


def fact_result(edge) -> dict:
    """An EntityEdge as the server's `search_memory_facts` returned it — with
    the same embedding-key rule as nodes applied to its attributes (until
    v2.61.0 a fact's `embedding_model`/`embedding_dimensions` leaked here)."""
    out = edge.model_dump(mode="json", exclude={"fact_embedding"})
    out["attributes"] = _no_embedding(out.get("attributes"))
    return out


# --- reads (ungated; safe for the runtime tier) --------------------------------


def _recipe(kind: str, limit: int, centered: bool = False):
    """A DEEP COPY of the recipe with `limit` set on the copy — never the
    module-level object (see the module docstring). With a centre node, the
    node-distance recipe (whatever the reranker — the retired server did the
    same: its centre-node search never used the cross-encoder); without one,
    cross-encoder recipes when a reranker of either kind (`/rerank` or chat)
    is configured, RRF otherwise (D6). A configured reranker that fails RAISES
    out of `search_()` — there is no unranked fallback."""
    client = _client()
    client._prepare_environment()  # before graphiti_core's first import
    from graphiti_core.search import search_config_recipes as recipes

    if centered:
        base = {"edge": recipes.EDGE_HYBRID_SEARCH_NODE_DISTANCE,
                "node": recipes.NODE_HYBRID_SEARCH_NODE_DISTANCE}[kind]
    else:
        rerank = client.reranker_configured()
        base = {
            ("edge", True): recipes.EDGE_HYBRID_SEARCH_CROSS_ENCODER,
            ("edge", False): recipes.EDGE_HYBRID_SEARCH_RRF,
            ("node", True): recipes.NODE_HYBRID_SEARCH_CROSS_ENCODER,
            ("node", False): recipes.NODE_HYBRID_SEARCH_RRF,
        }[(kind, rerank)]
    config = base.model_copy(deep=True)
    config.limit = max(1, int(limit))
    return config


# --- search filters (design record 2026-10-04, D6; release 2) --------------------
# graphiti_core names a date filter's query parameter by its POSITION INSIDE
# ITS AND GROUP only (`valid_at_0`, `valid_at_1`, … — search_filters.py,
# `edge_search_filter_query_constructor`), so two OR groups that each carry a
# compared date write the same parameter name and the second silently
# overwrites the first. The rule here: per date field, AT MOST ONE OR group
# carries a parameter; the only other group allowed is the parameterless
# `IS NULL` alternative, which is what an open validity bound means (a fact
# with no `invalid_at` is still true). tests/test_graph_search_filters.py runs
# the library's own constructor over every combination we build.


def fact_filters(*, as_of=None, after=None, before=None):
    """`SearchFilters` for a fact validity window, or None when no bound is
    given (so the default search is unchanged). Instants are timezone-aware
    datetimes.

    - `as_of`: facts true at that instant — started at or before it (or no
      recorded start) and not ended by it (or no recorded end);
    - `after` / `before`: facts true at SOME point in that window — not ended
      by `after`, started before `before` (either bound alone works).
    `as_of` with `after`/`before` is refused (one window per search)."""
    if as_of is None and after is None and before is None:
        return None
    if as_of is not None and (after is not None or before is not None):
        raise ValueError("give either as_of, or after/before — not both")
    _client()._prepare_environment()
    from graphiti_core.search.search_filters import (
        ComparisonOperator as Op,
        DateFilter,
        SearchFilters,
    )

    def open_or(op, when):
        # [[IS NULL], [<op> when]]: exactly one parameter-carrying group.
        return [[DateFilter(comparison_operator=Op.is_null)],
                [DateFilter(date=when, comparison_operator=op)]]

    valid_at = invalid_at = None
    if as_of is not None:
        valid_at = open_or(Op.less_than_equal, as_of)
        invalid_at = open_or(Op.greater_than, as_of)
    if after is not None:
        invalid_at = open_or(Op.greater_than, after)
    if before is not None:
        valid_at = open_or(Op.less_than, before)
    return SearchFilters(valid_at=valid_at, invalid_at=invalid_at)


def entity_type_names() -> tuple[str, ...]:
    """The ontology's type names, in declaration order — what an entity-type
    filter may name (`graph_ontology.ENTITY_TYPE_NAMES`)."""
    from central_command.integrations import graph_ontology

    return graph_ontology.ENTITY_TYPE_NAMES


def node_filters(entity_types=None):
    """`SearchFilters` restricting entity search to these ontology types, or
    None when none are given. Names are validated by the caller against
    `graph_ontology.ENTITY_TYPE_NAMES`; an unknown one is refused here too
    (it is interpolated into Cypher as a label)."""
    if not entity_types:
        return None
    from central_command.integrations import graph_ontology

    unknown = [t for t in entity_types if t not in graph_ontology.ENTITY_TYPE_NAMES]
    if unknown:
        raise ValueError(
            f"unknown entity type(s) {unknown}; valid: {', '.join(graph_ontology.ENTITY_TYPE_NAMES)}"
        )
    _client()._prepare_environment()
    from graphiti_core.search.search_filters import SearchFilters

    return SearchFilters(node_labels=list(dict.fromkeys(entity_types)))


async def _search(kind: str, query: str, limit: int, group_ids: list[str], *,
                  search_filter=None, center_node_uuid: str | None = None):
    client = _client()
    graphiti = client.get_graphiti()
    config = _recipe(kind, limit, centered=bool(center_node_uuid))
    # No filter and no centre: exactly the call v2.60.0 made (pinned).
    extra = {}
    if search_filter is not None:
        extra["search_filter"] = search_filter
    if center_node_uuid:
        extra["center_node_uuid"] = center_node_uuid
    return await graphiti.search_(query, config=config, group_ids=group_ids, **extra)


async def search_facts(
    query: str, max_facts: int = 8, agent_id: str | None = None,
    group_ids: list[str] | None = None, *, as_of=None, after=None, before=None,
    center_node_uuid: str | None = None,
) -> list[dict]:
    """Relevant facts (entity relationships, with temporal validity), up to
    `max_facts`. `group_ids` overrides the caller's read scope — the curator's
    any-partition read (graph-curate pack); every other caller leaves it None.
    `as_of` / `after` / `before` (aware datetimes) restrict to a validity
    window (`fact_filters`); `center_node_uuid` ranks by graph distance from
    that entity."""
    results = await _search(
        "edge", query, max_facts, group_ids or await groups_for(agent_id),
        search_filter=fact_filters(as_of=as_of, after=after, before=before),
        center_node_uuid=center_node_uuid,
    )
    return [fact_result(e) for e in results.edges][:max_facts]


async def search_nodes(
    query: str, max_nodes: int = 8, agent_id: str | None = None,
    group_ids: list[str] | None = None, *, entity_types: list[str] | None = None,
    center_node_uuid: str | None = None,
) -> list[dict]:
    """Relevant entities (nodes with summaries), up to `max_nodes`. `group_ids`
    as in search_facts; `entity_types` restricts to those ontology types;
    `center_node_uuid` ranks by graph distance from that entity."""
    results = await _search(
        "node", query, max_nodes, group_ids or await groups_for(agent_id),
        search_filter=node_filters(entity_types), center_node_uuid=center_node_uuid,
    )
    return [node_result(n) for n in results.nodes][:max_nodes]


async def episode_delete_preview(episode_uuid: str) -> dict | None:
    """What `graph.delete_episode` would remove (a read; `neo4j_reader`'s
    READ_ACCESS session). The runtime's propose tool embeds it in the
    proposal; None when there is no such episode."""
    return await neo4j_reader.episode_delete_preview(episode_uuid)


async def known_groups() -> list[str]:
    """Every group Central Command would ever have written to: the shared read
    set (steward domains included) plus one private partition per active
    roster agent. Derived from the roster, not from Neo4j — a group nobody on
    the roster owns is the cockpit's business, not an agent's."""
    from central_command.db import repo as db_repo

    groups = await _read_groups()
    for a in await db_repo.list_agents(include_retired=False):
        groups.append(private_group(a["id"]))
    return groups


async def get_episodes(
    last_n: int = 50, agent_id: str | None = None, private_only: bool = False
) -> list[dict]:
    """Most recent episodes in our groups — the shared memory, listed, plus the
    caller's own private partition when `agent_id` is given (D11-r1: agents
    have a private partition; a caller with no agent context sees shared only,
    e.g. the cockpit's team-wide memory panel). `private_only=True` (with an
    `agent_id`) narrows the read to exactly that agent's private group — the
    per-agent chat panel's default view."""
    group_ids = [private_group(agent_id)] if private_only and agent_id else await groups_for(agent_id)
    return await get_group_episodes(group_ids, last_n)


async def get_group_episodes(group_ids: list[str], last_n: int = 50) -> list[dict]:
    """The latest `last_n` episodes in exactly these groups, newest first — no
    caller-scope widening. Our own Cypher (`neo4j_reader.latest_episodes`):
    upstream's `EpisodicNode.get_by_group_ids` orders by uuid, so past the
    limit its "latest N" was an arbitrary slice (upstream #1724)."""
    bad = [g for g in group_ids if not GROUP_ID.match(g or "")]
    if bad:
        raise GraphitiError(
            f"invalid group id(s) {bad!r}: a group id is letters, digits, '_' and '-' only"
        )
    return await neo4j_reader.latest_episodes(group_ids, max(1, int(last_n)))


async def get_status() -> dict:
    """A Neo4j ping (D6). `{"status": "ok"|"error", "message": …}` — the shape
    the server's get_status had; `error` names the exception type when the
    database did not answer. Never raises."""
    try:
        await neo4j_reader._read("RETURN 1 AS ok")
    except Exception as exc:  # noqa: BLE001 — the status IS the answer
        return {"status": "error", "error": type(exc).__name__,
                "message": f"{type(exc).__name__}: {exc}"}
    return {"status": "ok", "message": "Neo4j answers over bolt"}
