"""Read-only bolt client for the cockpit Graph panel (rung 3, D-graph-inspect
2026-08-09). Canned parameterized queries only — no raw-Cypher endpoint, the
browser never talks to Neo4j directly. Every session is opened with the
driver's read access mode, so this client physically cannot write even if a
query slipped past review.

`runtime/` still holds zero Neo4j references: agents keep reaching the graph
only through Graphiti's MCP server. This is the API tier's own client, same
side of the trust boundary as the Executor.

Reachability comes from deploy/k3s/cc-graph-bolt.service, a standing
port-forward of svc/neo4j 7687 to 127.0.0.1 ONLY (Restart=always) — the
cc-neo4j Service itself stays ClusterIP-only. A dropped forward is a normal
outage-equivalent state (pod moved, service restarting), never a 500: every
read here degrades to "unavailable" rather than raising past the route.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from neo4j import READ_ACCESS, AsyncGraphDatabase
from neo4j.time import DateTime as Neo4jDateTime

from central_command.config import settings

_driver = None
_driver_loop = None


def _get_driver():
    """The one bolt driver, shared with `neo4j_writer` (same database, one pool
    — access mode is a property of the SESSION, which is what actually keeps
    this module's reads read-only).

    Cached PER EVENT LOOP, not just once: an async driver's pool holds futures
    bound to the loop that created it, so a module-global driver raises
    "attached to a different loop" from every loop but the first. Production has
    one loop and never sees it; pytest gives each test its own and sees it
    immediately — the same trap as a module-scope `asyncio.Event`."""
    global _driver, _driver_loop
    loop = asyncio.get_running_loop()
    if _driver is None or _driver_loop is not loop:
        _driver = AsyncGraphDatabase.driver(
            settings.neo4j_url, auth=("neo4j", settings.neo4j_password)
        )
        _driver_loop = loop
    return _driver


def _iso(value) -> str | None:
    """Neo4j temporal values arrive as neo4j.time.DateTime; JSON wants ISO
    strings. Passthrough for anything already str/None."""
    if value is None:
        return None
    if isinstance(value, Neo4jDateTime):
        return value.to_native().isoformat()
    return str(value)


async def _read(query: str, **params) -> list[dict]:
    driver = _get_driver()
    async with driver.session(default_access_mode=READ_ACCESS) as session:
        result = await session.run(query, **params)
        return [record.data() async for record in result]


async def ping() -> bool:
    try:
        await _read("RETURN 1")
        return True
    except Exception:
        return False


async def search_entities(q: str, group_id: str | None, limit: int) -> list[dict]:
    rows = await _read(
        """
        MATCH (n:Entity)
        WHERE toLower(n.name) CONTAINS toLower($q)
          AND ($group_id IS NULL OR n.group_id = $group_id)
        RETURN n.uuid AS uuid, n.name AS name, labels(n) AS labels,
               n.summary AS summary, n.group_id AS group_id
        LIMIT $limit
        """,
        q=q, group_id=group_id, limit=limit,
    )
    return rows


async def neighborhood(uuid: str, at: str | None, limit: int) -> dict:
    node_rows = await _read(
        """
        MATCH (n:Entity {uuid: $uuid})
        RETURN n.uuid AS uuid, n.name AS name, labels(n) AS labels,
               n.summary AS summary, n.group_id AS group_id
        """,
        uuid=uuid,
    )
    if at:
        edge_rows = await _read(
            """
            MATCH (n:Entity {uuid: $uuid})-[e:RELATES_TO]-(m:Entity)
            WHERE (e.valid_at IS NULL OR e.valid_at <= datetime($at))
              AND (e.invalid_at IS NULL OR e.invalid_at > datetime($at))
            RETURN DISTINCT e.uuid AS uuid, e.source_node_uuid AS source,
                   e.target_node_uuid AS target, e.name AS name, e.fact AS fact,
                   e.valid_at AS valid_at, e.invalid_at AS invalid_at,
                   e.created_at AS created_at,
                   m.uuid AS neighbor_uuid, m.name AS neighbor_name,
                   labels(m) AS neighbor_labels, m.summary AS neighbor_summary,
                   m.group_id AS neighbor_group_id
            LIMIT $limit
            """,
            uuid=uuid, at=at, limit=limit,
        )
    else:
        edge_rows = await _read(
            """
            MATCH (n:Entity {uuid: $uuid})-[e:RELATES_TO]-(m:Entity)
            RETURN DISTINCT e.uuid AS uuid, e.source_node_uuid AS source,
                   e.target_node_uuid AS target, e.name AS name, e.fact AS fact,
                   e.valid_at AS valid_at, e.invalid_at AS invalid_at,
                   e.created_at AS created_at,
                   m.uuid AS neighbor_uuid, m.name AS neighbor_name,
                   labels(m) AS neighbor_labels, m.summary AS neighbor_summary,
                   m.group_id AS neighbor_group_id
            LIMIT $limit
            """,
            uuid=uuid, limit=limit,
        )

    nodes = {}
    for row in node_rows:
        nodes[row["uuid"]] = {
            "uuid": row["uuid"], "name": row["name"], "labels": row["labels"],
            "summary": row["summary"], "group_id": row["group_id"],
        }
    edges = []
    now = datetime.now(timezone.utc)
    for row in edge_rows:
        nodes.setdefault(row["neighbor_uuid"], {
            "uuid": row["neighbor_uuid"], "name": row["neighbor_name"],
            "labels": row["neighbor_labels"], "summary": row["neighbor_summary"],
            "group_id": row["neighbor_group_id"],
        })
        invalid_at_native = row["invalid_at"].to_native() if isinstance(
            row["invalid_at"], Neo4jDateTime
        ) else None
        edges.append({
            "uuid": row["uuid"], "source": row["source"], "target": row["target"],
            "name": row["name"], "fact": row["fact"],
            "valid_at": _iso(row["valid_at"]), "invalid_at": _iso(row["invalid_at"]),
            "created_at": _iso(row["created_at"]),
            "invalid": invalid_at_native is not None and invalid_at_native <= now,
        })
    return {"nodes": list(nodes.values()), "edges": edges}


async def everything(group_id: str | None, limit: int, at: str | None = None) -> dict:
    """The whole graph (or one group of it) in a single read.

    The panel was built explore-only — search, then expand a neighbourhood —
    on the reasoning that a graph is too big to render. That is true at scale
    and false here: the live graph is tens of elements, and you cannot spot
    what is WRONG in a graph you can only reach by already knowing what to
    search for (2026-08-16, curating duplicates). Both modes stay: this one
    answers "show me everything", search still answers "find me this".

    Isolated nodes are included — a duplicate identity with no edges yet is
    precisely the thing worth seeing — which is why the nodes and edges are
    two queries rather than one pattern match. `truncated` is returned rather
    than silently capping: a view that quietly shows 500 of 900 is a view that
    lies about completeness.

    `at` filters edges to those valid at that instant, the same predicate as
    `neighborhood`. A null bound is OPEN on purpose — the operator can set
    "valid from" open-ended in the EdgeEditor, so a missing start means "since
    before anyone recorded when", not "never was". Nodes carry no validity
    window and always show.
    """
    node_rows = await _read(
        """
        MATCH (n:Entity)
        WHERE $group_id IS NULL OR n.group_id = $group_id
        RETURN n.uuid AS uuid, n.name AS name, labels(n) AS labels,
               n.summary AS summary, n.group_id AS group_id
        ORDER BY n.name
        LIMIT $limit
        """,
        group_id=group_id, limit=limit,
    )
    edge_rows = await _read(
        """
        MATCH (a:Entity)-[e:RELATES_TO]->(b:Entity)
        WHERE ($group_id IS NULL OR (a.group_id = $group_id AND b.group_id = $group_id))
          AND ($at IS NULL OR ((e.valid_at IS NULL OR e.valid_at <= datetime($at))
               AND (e.invalid_at IS NULL OR e.invalid_at > datetime($at))))
        RETURN e.uuid AS uuid, e.source_node_uuid AS source,
               e.target_node_uuid AS target, e.name AS name, e.fact AS fact,
               e.valid_at AS valid_at, e.invalid_at AS invalid_at,
               e.created_at AS created_at
        LIMIT $limit
        """,
        group_id=group_id, limit=limit, at=at,
    )
    now = datetime.now(timezone.utc)
    edges = []
    for row in edge_rows:
        invalid_at_native = row["invalid_at"].to_native() if isinstance(
            row["invalid_at"], Neo4jDateTime
        ) else None
        edges.append({
            "uuid": row["uuid"], "source": row["source"], "target": row["target"],
            "name": row["name"], "fact": row["fact"],
            "valid_at": _iso(row["valid_at"]), "invalid_at": _iso(row["invalid_at"]),
            "created_at": _iso(row["created_at"]),
            "invalid": invalid_at_native is not None and invalid_at_native <= now,
        })
    return {
        "nodes": node_rows,
        "edges": edges,
        "truncated": len(node_rows) >= limit or len(edge_rows) >= limit,
    }


async def provenance(uuid: str) -> list[dict]:
    """Episodic nodes MENTIONS-linked to an entity (or an edge's episode uuids,
    since RELATES_TO.episodes carries the same Episodic uuids)."""
    rows = await _read(
        """
        MATCH (e:Episodic)-[:MENTIONS]->(n {uuid: $uuid})
        RETURN e.uuid AS uuid, e.name AS name,
               e.source_description AS source_description, e.valid_at AS valid_at,
               e.content AS content
        ORDER BY e.valid_at DESC
        """,
        uuid=uuid,
    )
    episodes = []
    for row in rows:
        content = row["content"] or ""
        episodes.append({
            "uuid": row["uuid"], "name": row["name"],
            "source_description": row["source_description"],
            "valid_at": _iso(row["valid_at"]),
            "content_preview": content[:280],
        })
    return episodes


async def count_episodes(group_ids: list[str]) -> int:
    """Total Episodic nodes across `group_ids` — the memory panel's "N of
    TOTAL" figure, so a fixed `limit` never reads as the whole truth."""
    rows = await _read(
        "MATCH (e:Episodic) WHERE e.group_id IN $group_ids RETURN count(e) AS total",
        group_ids=group_ids,
    )
    return rows[0]["total"] if rows else 0


async def episode_by_marker(marker: str, group_id: str) -> dict | None:
    """The Episodic node the verification sweep is looking for — matched by a
    marker token stamped into `source_description` at propose time (Task 2's
    `| proposal=<id>`, or a test's own uuid4). Newest wins if more than one
    contains the marker; absence past the settle window IS the silent-drop
    finding this function exists to surface."""
    rows = await _read(
        """
        MATCH (e:Episodic)
        WHERE e.source_description CONTAINS $marker AND e.group_id = $group_id
        RETURN e.uuid AS uuid, e.name AS name, e.created_at AS created_at,
               e.group_id AS group_id, e.source_description AS source_description
        ORDER BY e.created_at DESC
        LIMIT 1
        """,
        marker=marker, group_id=group_id,
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "uuid": row["uuid"], "name": row["name"],
        "created_at": _iso(row["created_at"]), "group_id": row["group_id"],
        "source_description": row["source_description"],
    }


async def episode_delta(episode_uuid: str, window_minutes: int = 30) -> dict:
    """What one episode actually produced: the entities it MENTIONS, the
    RELATES_TO edges it is named on, and the edges it invalidated.

    Invalidation attribution uses BOTH methods and merges them (deduped by
    edge uuid, tagged `attributed_by`) because neither is complete — measured
    live 2026-08-19 (tests/test_graph_delta_live.py): of two edges one
    contradiction retired, only ONE carried the invalidating episode's uuid in
    `r.episodes`; the other was caught solely by `expired_at` falling in the
    ingestion window. The window is load-bearing, not a fallback. Its own
    residual: a concurrent episode in the same group could expire an edge
    inside this episode's window — acceptable because ingestion is queued
    serially per group and volume is human-approved-low."""
    # `new` distinguishes CREATED-BY-THIS-EPISODE from touched-but-pre-existing
    # (extraction dedupes onto existing nodes/edges, and MENTIONS points at
    # whatever it resolved to). Compared in Cypher against the Episodic node's
    # own created_at so no Python datetime handling is needed; an entity/edge
    # born during this episode's ingestion is never older than the episode.
    entity_rows = await _read(
        """
        MATCH (e:Episodic {uuid: $uuid})-[:MENTIONS]->(n)
        RETURN n.uuid AS uuid, n.name AS name, labels(n) AS labels,
               n.summary AS summary, n.name_embedding IS NOT NULL AS has_embedding,
               n.created_at AS created_at, n.created_at >= e.created_at AS new
        """,
        uuid=episode_uuid,
    )
    entities = [
        {**dict(row), "created_at": _iso(row["created_at"])} for row in entity_rows
    ]

    edge_rows = await _read(
        """
        MATCH (e:Episodic {uuid: $uuid})
        MATCH (a)-[r:RELATES_TO]->(b)
        WHERE $uuid IN r.episodes
        RETURN r.uuid AS uuid, r.name AS name, r.fact AS fact,
               a.name AS source, b.name AS target,
               r.valid_at AS valid_at, r.invalid_at AS invalid_at,
               r.expired_at AS expired_at,
               r.created_at AS created_at, r.created_at >= e.created_at AS new
        """,
        uuid=episode_uuid,
    )
    edges = [
        {
            "uuid": row["uuid"], "name": row["name"], "fact": row["fact"],
            "source": row["source"], "target": row["target"],
            "valid_at": _iso(row["valid_at"]), "invalid_at": _iso(row["invalid_at"]),
            "expired_at": _iso(row["expired_at"]),
            "created_at": _iso(row["created_at"]), "new": row["new"],
        }
        for row in edge_rows
    ]

    episode_row = await _read(
        "MATCH (e:Episodic {uuid: $uuid}) RETURN e.created_at AS created_at, e.group_id AS group_id",
        uuid=episode_uuid,
    )
    invalidated: dict[str, dict] = {}

    via_episodes_rows = await _read(
        """
        MATCH (a)-[r:RELATES_TO]->(b)
        WHERE $uuid IN r.episodes AND r.expired_at IS NOT NULL
        RETURN r.uuid AS uuid, r.name AS name, r.fact AS fact,
               a.name AS source, b.name AS target,
               r.valid_at AS valid_at, r.invalid_at AS invalid_at,
               r.expired_at AS expired_at
        """,
        uuid=episode_uuid,
    )
    for row in via_episodes_rows:
        invalidated[row["uuid"]] = {
            "uuid": row["uuid"], "name": row["name"], "fact": row["fact"],
            "source": row["source"], "target": row["target"],
            "valid_at": _iso(row["valid_at"]), "invalid_at": _iso(row["invalid_at"]),
            "expired_at": _iso(row["expired_at"]), "attributed_by": "episodes",
        }

    if episode_row and episode_row[0]["created_at"] is not None:
        via_window_rows = await _read(
            """
            MATCH (a)-[r:RELATES_TO]->(b)
            WHERE a.group_id = $group_id AND b.group_id = $group_id
              AND r.expired_at IS NOT NULL
              AND r.expired_at >= datetime($created_at)
              AND r.expired_at <= datetime($created_at) + duration({minutes: $window})
            RETURN r.uuid AS uuid, r.name AS name, r.fact AS fact,
                   a.name AS source, b.name AS target,
                   r.valid_at AS valid_at, r.invalid_at AS invalid_at,
                   r.expired_at AS expired_at
            """,
            group_id=episode_row[0]["group_id"],
            created_at=_iso(episode_row[0]["created_at"]),
            window=window_minutes,
        )
        for row in via_window_rows:
            if row["uuid"] in invalidated:
                invalidated[row["uuid"]]["attributed_by"] = "both"
            else:
                invalidated[row["uuid"]] = {
                    "uuid": row["uuid"], "name": row["name"], "fact": row["fact"],
                    "source": row["source"], "target": row["target"],
                    "valid_at": _iso(row["valid_at"]), "invalid_at": _iso(row["invalid_at"]),
                    "expired_at": _iso(row["expired_at"]), "attributed_by": "window",
                }

    return {"entities": entities, "edges": edges, "invalidated": list(invalidated.values())}


async def audit(group_id: str | None, threshold: float) -> dict:
    """Graph quality report — READ ONLY, surfaces problems as data for an
    operator to curate by hand (see the graph-curation routes); this module
    never writes.

    `duplicate_entities` catches near-duplicate identities two ways: cosine
    similarity of `name_embedding` (the semantic signal Graphiti's own dedupe
    uses) and, unioned in, exact case-insensitive name matches where either
    side is MISSING an embedding — a node with no embedding is invisible to
    every semantic search (see the CLAUDE.md half-write rule) and would
    otherwise be invisible here too. `basis` tags which check found the pair.

    # ponytail: pairwise O(n^2) over embedded nodes — fine at this graph's
    # size (tens to low thousands of entities); blocking by community/group
    # before the pairwise pass is the upgrade path if it ever isn't.
    """
    embedding_pairs = await _read(
        """
        MATCH (a:Entity), (b:Entity)
        WHERE a.uuid < b.uuid
          AND a.group_id = b.group_id
          AND ($group_id IS NULL OR a.group_id = $group_id)
          AND a.name_embedding IS NOT NULL AND b.name_embedding IS NOT NULL
        WITH a, b, vector.similarity.cosine(a.name_embedding, b.name_embedding) AS similarity
        WHERE similarity >= $threshold
        RETURN a.uuid AS a_uuid, a.name AS a_name, labels(a) AS a_labels, a.group_id AS a_group_id,
               b.uuid AS b_uuid, b.name AS b_name, labels(b) AS b_labels, b.group_id AS b_group_id,
               similarity
        ORDER BY similarity DESC
        LIMIT 100
        """,
        group_id=group_id, threshold=threshold,
    )
    name_pairs = await _read(
        """
        MATCH (a:Entity), (b:Entity)
        WHERE a.uuid < b.uuid
          AND a.group_id = b.group_id
          AND ($group_id IS NULL OR a.group_id = $group_id)
          AND (a.name_embedding IS NULL OR b.name_embedding IS NULL)
          AND toLower(a.name) = toLower(b.name)
        RETURN a.uuid AS a_uuid, a.name AS a_name, labels(a) AS a_labels, a.group_id AS a_group_id,
               b.uuid AS b_uuid, b.name AS b_name, labels(b) AS b_labels, b.group_id AS b_group_id
        LIMIT 100
        """,
        group_id=group_id,
    )

    def _pair(row, basis):
        return {
            "a": {"uuid": row["a_uuid"], "name": row["a_name"],
                  "labels": row["a_labels"], "group_id": row["a_group_id"]},
            "b": {"uuid": row["b_uuid"], "name": row["b_name"],
                  "labels": row["b_labels"], "group_id": row["b_group_id"]},
            "similarity": row.get("similarity", 1.0),
            "basis": basis,
        }

    duplicate_entities = (
        [_pair(r, "embedding") for r in embedding_pairs]
        + [_pair(r, "name") for r in name_pairs]
    )
    duplicate_entities.sort(key=lambda p: p["similarity"], reverse=True)
    duplicate_entities = duplicate_entities[:100]

    edge_groups = await _read(
        """
        MATCH (a:Entity)-[e:RELATES_TO]->(b:Entity)
        WHERE e.expired_at IS NULL AND e.invalid_at IS NULL
          AND ($group_id IS NULL OR (a.group_id = $group_id AND b.group_id = $group_id))
        WITH CASE WHEN a.uuid < b.uuid THEN a ELSE b END AS lo,
             CASE WHEN a.uuid < b.uuid THEN b ELSE a END AS hi,
             e
        WITH lo, hi, collect({uuid: e.uuid, name: e.name, fact: e.fact,
                               created_at: e.created_at, valid_at: e.valid_at}) AS edges
        WHERE size(edges) > 1
        RETURN lo.uuid AS source_uuid, lo.name AS source_name,
               hi.uuid AS target_uuid, hi.name AS target_name, edges
        LIMIT 50
        """,
        group_id=group_id,
    )
    duplicate_edges = [
        {
            "source": {"uuid": row["source_uuid"], "name": row["source_name"]},
            "target": {"uuid": row["target_uuid"], "name": row["target_name"]},
            "edges": [
                {
                    "uuid": e["uuid"], "name": e["name"], "fact": e["fact"],
                    "created_at": _iso(e["created_at"]), "valid_at": _iso(e["valid_at"]),
                }
                for e in row["edges"]
            ],
        }
        for row in edge_groups
    ]

    isolated_nodes = await _read(
        """
        MATCH (n:Entity)
        WHERE NOT (n)-[:RELATES_TO]-()
          AND ($group_id IS NULL OR n.group_id = $group_id)
        RETURN n.uuid AS uuid, n.name AS name, n.group_id AS group_id
        LIMIT 100
        """,
        group_id=group_id,
    )
    isolated_count = await _read(
        """
        MATCH (n:Entity)
        WHERE NOT (n)-[:RELATES_TO]-()
          AND ($group_id IS NULL OR n.group_id = $group_id)
        RETURN count(n) AS c
        """,
        group_id=group_id,
    )

    untyped_nodes = await _read(
        """
        MATCH (n:Entity)
        WHERE size(labels(n)) = 1
          AND ($group_id IS NULL OR n.group_id = $group_id)
        RETURN n.uuid AS uuid, n.name AS name, n.group_id AS group_id
        LIMIT 100
        """,
        group_id=group_id,
    )
    untyped_count = await _read(
        """
        MATCH (n:Entity)
        WHERE size(labels(n)) = 1
          AND ($group_id IS NULL OR n.group_id = $group_id)
        RETURN count(n) AS c
        """,
        group_id=group_id,
    )

    missing_embedding_nodes = await _read(
        """
        MATCH (n:Entity)
        WHERE n.name_embedding IS NULL
          AND ($group_id IS NULL OR n.group_id = $group_id)
        RETURN n.uuid AS uuid, n.name AS name, n.group_id AS group_id
        LIMIT 100
        """,
        group_id=group_id,
    )
    missing_embedding_count = await _read(
        """
        MATCH (n:Entity)
        WHERE n.name_embedding IS NULL
          AND ($group_id IS NULL OR n.group_id = $group_id)
        RETURN count(n) AS c
        """,
        group_id=group_id,
    )

    dangling_edges = await _read(
        """
        MATCH (a:Entity)-[e:RELATES_TO]->(b:Entity)
        WHERE (e.source_node_uuid <> a.uuid OR e.target_node_uuid <> b.uuid)
          AND ($group_id IS NULL OR (a.group_id = $group_id AND b.group_id = $group_id))
        RETURN e.uuid AS uuid,
               e.source_node_uuid AS claimed_source, a.uuid AS actual_source,
               e.target_node_uuid AS claimed_target, b.uuid AS actual_target
        LIMIT 50
        """,
        group_id=group_id,
    )
    dangling_count = await _read(
        """
        MATCH (a:Entity)-[e:RELATES_TO]->(b:Entity)
        WHERE (e.source_node_uuid <> a.uuid OR e.target_node_uuid <> b.uuid)
          AND ($group_id IS NULL OR (a.group_id = $group_id AND b.group_id = $group_id))
        RETURN count(e) AS c
        """,
        group_id=group_id,
    )

    return {
        "duplicate_entities": duplicate_entities,
        "duplicate_edges": duplicate_edges,
        "health": {
            "isolated_nodes": isolated_nodes,
            "untyped_nodes": untyped_nodes,
            "missing_embedding_nodes": missing_embedding_nodes,
            "dangling_edges": dangling_edges,
            "counts": {
                "isolated_nodes": isolated_count[0]["c"] if isolated_count else 0,
                "untyped_nodes": untyped_count[0]["c"] if untyped_count else 0,
                "missing_embedding_nodes": missing_embedding_count[0]["c"] if missing_embedding_count else 0,
                "dangling_edges": dangling_count[0]["c"] if dangling_count else 0,
            },
        },
    }


async def counts() -> dict:
    rows = await _read(
        """
        MATCH (n:Entity)
        WITH count(n) AS node_count, collect(DISTINCT n.group_id) AS group_ids
        OPTIONAL MATCH ()-[e:RELATES_TO]->()
        RETURN node_count, group_ids, count(e) AS edge_count
        """
    )
    if not rows:
        return {"node_count": 0, "edge_count": 0, "group_ids": []}
    row = rows[0]
    return {
        "node_count": row["node_count"], "edge_count": row["edge_count"],
        "group_ids": row["group_ids"],
    }
