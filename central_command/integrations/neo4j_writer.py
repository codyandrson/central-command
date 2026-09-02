"""OPERATOR curation writes against the Graphiti graph — the write half of the
cockpit Graph panel (increment #1 of the 2026-08-09 graph-inspection spec).

**This is not an agent path and must never become one.** Agents reach the graph
only through Graphiti's MCP server, and the one write they can cause is a gated
`graph.add_episode` proposal. What lives here is the operator's own hand on the data:
delete a hallucinated node, fix a wrong edge, add a fact the extractor missed.
There is no approval gate because the operator IS the gate — the corollary is
that nothing in `runtime/` may import this module, exactly as with `gateway/`.

Kept apart from `neo4j_reader.py` on purpose: that module's promise is that
every session it opens is READ_ACCESS and it physically cannot write. Adding a
write to it would retire that guarantee for every reader. Here the sessions are
write-access and the safety comes from the query set being closed and
parameterized — no raw Cypher crosses the API boundary, same rule as the reader.

## What Graphiti expects of a hand-written node/edge (verified against the live
graph, 2026-08-15) — get any of these wrong and the write "succeeds" while the
graph quietly misbehaves:

- **An entity carries its types TWICE**: as real Neo4j labels (`:Entity:Person`)
  and as a `labels` list property. Graphiti's own reads use the property, the
  cockpit's use `labels(n)`. Write one and not the other and the two views of
  the same node disagree forever.
- **An edge's endpoints are stored TWICE too**: as the relationship itself and
  as `source_node_uuid` / `target_node_uuid` properties — and the cockpit draws
  from the properties. Neo4j cannot repoint a relationship in place, so
  `update_edge` recreates it and rewrites both properties together.
- **Embeddings are not optional decoration.** `name_embedding` / `fact_embedding`
  are half of Graphiti's hybrid search; a node written without one is reachable
  by keyword (the fulltext indexes) but INVISIBLE to the semantic half, so an
  agent asking "who is the operator's grandfather" would miss the fact the operator just
  typed in. Every write here re-embeds through the same LiteLLM alias and the
  same dimensions Graphiti is configured with — a mismatch there is a
  corrupt index, not an error. Embedding is best-effort on purpose: if the
  workstation is off, the edit still lands (losing the operator's correction to
  a sleeping GPU is worse) and the response says the embedding is missing.
- **`episodes` is provenance, and an operator entry gets a REAL one.** Every
  create here writes an Episodic node ("operator manual entry", timestamped,
  `trust=operator-direct` in its source_description) MENTIONS-linked to the
  entities involved, exactly the shape extraction writes — so the Provenance
  drawer shows who put the fact in and when, instead of the old `episodes = []`
  convention whose "no source episodes" read as missing data, not as authorship
  (changed 2026-08-16). Updates still leave `episodes` untouched: a fix does
  not retire the evidence the original fact came from.
"""

from __future__ import annotations

import logging
import re
import uuid as _uuid
from datetime import datetime, timezone

import httpx

from central_command.config import settings
from central_command.integrations import http as http_client
from central_command.integrations.neo4j_reader import _get_driver

log = logging.getLogger(__name__)

# Mirrors `entity_types` in deploy/pi/graphiti/config.yaml. Closed on purpose:
# Neo4j 5.26 has no parameterized labels (dynamic `SET n:$(x)` is a 2025.x
# feature), so a label reaches Cypher via string interpolation and the allowlist
# is what makes that safe. A type added to the ontology belongs here too.
ENTITY_TYPES = (
    "Person", "Preference", "Requirement", "Procedure", "Location",
    "Event", "Organization", "Document", "Topic", "Object",
)

# Must equal the graphiti config's embedder block. They index the same vectors.
# Settings (CC_EMBED_ALIAS / CC_EMBED_DIM), defaulting to the homelab values —
# the single-node profile discovers both at setup (2026-08-21 design).
_EMBED_MODEL = settings.embed_alias
_EMBED_DIMENSIONS = settings.embed_dim

class WriteError(Exception):
    """A curation write that could not be applied as asked."""


async def _write(query: str, **params) -> list[dict]:
    """Default (write) access mode — the driver itself is the reader's, because
    one database deserves one connection pool. What made the reader read-only
    was never the driver, it was `default_access_mode=READ_ACCESS` on each of
    its sessions, and that is untouched."""
    driver = _get_driver()
    async with driver.session() as session:
        result = await session.run(query, **params)
        return [record.data() async for record in result]


async def embed(text: str) -> list[float] | None:
    """Vector for `text` from the same embedder Graphiti uses, or None if the
    workstation is unreachable. None is a degraded write, never a failed one."""
    if not text.strip():
        return None
    try:
        async with httpx.AsyncClient(timeout=60.0, **http_client.client_kwargs()) as client:
            res = await client.post(
                f"{settings.llm_proxy_base_url}/v1/embeddings",
                headers={"Authorization": f"Bearer {settings.llm_proxy_admin_key}"},
                json={"model": _EMBED_MODEL, "input": text},
            )
            res.raise_for_status()
            vector = res.json()["data"][0]["embedding"]
    except Exception as exc:
        # Degrading silently to the CALLER is deliberate (see docstring) — but
        # a persistently-misconfigured alias looks identical to a transiently
        # unreachable workstation from here, and only a log line tells them
        # apart after the fact. CC_EMBED_ALIAS pointing at a nonexistent
        # LiteLLM model is exactly this: every write "succeeds" as degraded,
        # forever, with nothing in the API response to notice it by.
        log.warning("embed(%r): degraded write, embedding failed: %s", _EMBED_MODEL, exc)
        return None
    # A wrong-width vector would corrupt the index rather than fail a query, so
    # it is dropped: no embedding beats a mis-sized one.
    if len(vector) != _EMBED_DIMENSIONS:
        log.warning(
            "embed(%r): degraded write, got %d dims, expected %d",
            _EMBED_MODEL, len(vector), _EMBED_DIMENSIONS,
        )
        return None
    return vector


def _stamp_params(vector: list[float] | None) -> dict:
    """The provenance stamp written beside every embedding — the same
    `embedding_model`/`embedding_dimensions` properties
    scripts/oneoff/reembed_graph.py keys its resumability and --verify on,
    so a migration can tell re-embedded rows from pending ones. A degraded
    write (vector None) nulls the stamp too: a stamp beside a missing
    vector would claim an embedding that is not there."""
    present = vector is not None
    return {
        "embed_model": _EMBED_MODEL if present else None,
        "embed_dims": _EMBED_DIMENSIONS if present else None,
    }


def _validated_labels(labels: list[str] | None) -> list[str]:
    chosen = [l for l in (labels or []) if l != "Entity"]
    unknown = [l for l in chosen if l not in ENTITY_TYPES]
    if unknown:
        raise WriteError(
            f"unknown entity type(s) {unknown}; allowed: {', '.join(ENTITY_TYPES)}"
        )
    return chosen


def _now() -> datetime:
    return datetime.now(timezone.utc)


_OPERATOR_SOURCE = (
    "Manually entered by the operator in the cockpit Graph panel"
    " | trust=operator-direct"
)


async def _record_operator_episode(
    content: str, group_id: str, mentions: list[str], entity_edges: list[str],
) -> str:
    """Provenance for a hand-made fact: an Episodic node saying WHO (the
    operator), WHEN (now) and WHAT was entered, MENTIONS-linked to the entities
    involved — the same shape extraction writes, so the Provenance drawer and
    Graphiti's own reads treat it as a first-class episode."""
    ep_uuid = str(_uuid.uuid4())
    now = _now()
    await _write(
        """
        CREATE (ep:Episodic)
        SET ep.uuid = $uuid, ep.name = 'operator manual entry',
            ep.content = $content, ep.source = 'text',
            ep.source_description = $source_description,
            ep.group_id = $group_id, ep.created_at = $now, ep.valid_at = $now,
            ep.entity_edges = $entity_edges
        WITH ep
        MATCH (n:Entity) WHERE n.uuid IN $mentions
        CREATE (ep)-[m:MENTIONS]->(n)
        SET m.uuid = randomUUID(), m.group_id = $group_id, m.created_at = $now
        """,
        uuid=ep_uuid, content=content, source_description=_OPERATOR_SOURCE,
        group_id=group_id, now=now, entity_edges=entity_edges, mentions=mentions,
    )
    return ep_uuid


async def create_node(
    name: str, labels: list[str] | None, summary: str, group_id: str,
) -> dict:
    if not name.strip():
        raise WriteError("a node needs a name")
    extra = _validated_labels(labels)
    vector = await embed(f"{name}\n{summary}".strip())
    node_uuid = str(_uuid.uuid4())
    label_clause = "".join(f":{l}" for l in extra)
    await _write(
        f"""
        CREATE (n:Entity{label_clause})
        SET n.uuid = $uuid, n.name = $name, n.summary = $summary,
            n.group_id = $group_id, n.created_at = $created_at,
            n.labels = $labels, n.name_embedding = $embedding,
            n.embedding_model = $embed_model, n.embedding_dimensions = $embed_dims
        """,
        uuid=node_uuid, name=name, summary=summary, group_id=group_id,
        created_at=_now(), labels=extra + ["Entity"], embedding=vector,
        **_stamp_params(vector),
    )
    episode = await _record_operator_episode(
        f"{name}: {summary}" if summary else name, group_id,
        mentions=[node_uuid], entity_edges=[],
    )
    return {"uuid": node_uuid, "embedded": vector is not None, "episode": episode}


async def update_node(
    uuid: str, name: str | None = None, summary: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    """Rename / re-summarise / re-type. Re-embeds whenever the embedded text
    changed, because a stale `name_embedding` keeps matching the OLD name in
    every semantic search — the failure looks like the rename never happened."""
    current = await _write(
        "MATCH (n:Entity {uuid: $uuid}) RETURN n.name AS name, n.summary AS summary",
        uuid=uuid,
    )
    if not current:
        raise WriteError(f"no entity with uuid {uuid}")
    new_name = current[0]["name"] if name is None else name
    new_summary = current[0]["summary"] if summary is None else summary
    if not new_name.strip():
        raise WriteError("a node needs a name")

    text_changed = new_name != current[0]["name"] or new_summary != current[0]["summary"]
    vector = await embed(f"{new_name}\n{new_summary or ''}".strip()) if text_changed else None

    sets = ["n.name = $name", "n.summary = $summary"]
    params: dict = {"uuid": uuid, "name": new_name, "summary": new_summary}
    if text_changed:
        sets.append("n.name_embedding = $embedding")
        sets.append("n.embedding_model = $embed_model")
        sets.append("n.embedding_dimensions = $embed_dims")
        params["embedding"] = vector
        params.update(_stamp_params(vector))
    if labels is not None:
        extra = _validated_labels(labels)
        # Strip every ontology label, then re-apply the chosen ones. `:Entity`
        # is never removed — it is what makes the node an entity at all.
        removals = "".join(f":{l}" for l in ENTITY_TYPES)
        additions = "".join(f":{l}" for l in extra)
        await _write(f"MATCH (n:Entity {{uuid: $uuid}}) REMOVE n{removals}", uuid=uuid)
        if additions:
            await _write(f"MATCH (n:Entity {{uuid: $uuid}}) SET n{additions}", uuid=uuid)
        sets.append("n.labels = $labels")
        params["labels"] = extra + ["Entity"]

    await _write(
        f"MATCH (n:Entity {{uuid: $uuid}}) SET {', '.join(sets)}", **params
    )
    return {"uuid": uuid, "embedded": vector is not None if text_changed else None}


async def delete_node(uuid: str) -> dict:
    """DETACH DELETE: the node and every edge touching it. The Episodic node
    that produced it survives — provenance is history and stays readable, and
    re-ingesting that episode could legitimately recreate the entity."""
    rows = await _write(
        """
        MATCH (n:Entity {uuid: $uuid})
        WITH n, count { (n)-[:RELATES_TO]-() } AS edges
        DETACH DELETE n
        RETURN edges
        """,
        uuid=uuid,
    )
    if not rows:
        raise WriteError(f"no entity with uuid {uuid}")
    # RELATES_TO only: DETACH DELETE also drops MENTIONS provenance links, but
    # counting those read as knowledge edges the operator never had (a node
    # with 1 fact reported edges_removed: 3 — found in the 2026-08-25 sweep).
    return {"uuid": uuid, "edges_removed": rows[0]["edges"]}


async def create_edge(
    source_uuid: str, target_uuid: str, name: str, fact: str,
    valid_at: str | None = None, invalid_at: str | None = None,
) -> dict:
    if not name.strip() or not fact.strip():
        raise WriteError("an edge needs both a name and a fact")
    ends = await _write(
        """
        MATCH (a:Entity {uuid: $source}), (b:Entity {uuid: $target})
        RETURN a.group_id AS group_id
        """,
        source=source_uuid, target=target_uuid,
    )
    if not ends:
        raise WriteError("source or target entity not found")
    vector = await embed(fact)
    edge_uuid = str(_uuid.uuid4())
    episode = await _record_operator_episode(
        fact, ends[0]["group_id"],
        mentions=[source_uuid, target_uuid], entity_edges=[edge_uuid],
    )
    await _write(
        """
        MATCH (a:Entity {uuid: $source}), (b:Entity {uuid: $target})
        CREATE (a)-[e:RELATES_TO]->(b)
        SET e.uuid = $uuid, e.name = $name, e.fact = $fact,
            e.group_id = $group_id, e.created_at = $created_at,
            e.valid_at = CASE WHEN $valid_at IS NULL THEN $created_at
                              ELSE datetime($valid_at) END,
            e.invalid_at = CASE WHEN $invalid_at IS NULL THEN null
                                ELSE datetime($invalid_at) END,
            e.source_node_uuid = $source, e.target_node_uuid = $target,
            e.episodes = [$episode], e.fact_embedding = $embedding,
            e.embedding_model = $embed_model, e.embedding_dimensions = $embed_dims
        """,
        source=source_uuid, target=target_uuid, uuid=edge_uuid, name=name,
        fact=fact, group_id=ends[0]["group_id"], created_at=_now(),
        valid_at=valid_at, invalid_at=invalid_at, embedding=vector, episode=episode,
        **_stamp_params(vector),
    )
    return {"uuid": edge_uuid, "embedded": vector is not None, "episode": episode}


async def update_edge(
    uuid: str, name: str | None = None, fact: str | None = None,
    source_uuid: str | None = None, target_uuid: str | None = None,
    valid_at: str | None = None, invalid_at: str | None = None,
    clear_invalid: bool = False, clear_valid: bool = False,
    clear_expired: bool = False,
) -> dict:
    """Edit an edge, including REPOINTING it. Neo4j cannot move a relationship's
    endpoints, so a repoint is create-copy-then-delete — and the two uuid
    properties are rewritten in the same statement, because the cockpit draws
    the graph from those properties and not from the topology."""
    current = await _write(
        """
        MATCH ()-[e:RELATES_TO {uuid: $uuid}]->()
        RETURN e.name AS name, e.fact AS fact,
               e.source_node_uuid AS source, e.target_node_uuid AS target
        """,
        uuid=uuid,
    )
    if not current:
        raise WriteError(f"no relationship with uuid {uuid}")
    row = current[0]
    new_fact = row["fact"] if fact is None else fact
    new_name = row["name"] if name is None else name
    vector = await embed(new_fact) if new_fact != row["fact"] else None

    sets = ["e.name = $name", "e.fact = $fact"]
    params: dict = {"uuid": uuid, "name": new_name, "fact": new_fact}
    if new_fact != row["fact"]:
        sets.append("e.fact_embedding = $embedding")
        sets.append("e.embedding_model = $embed_model")
        sets.append("e.embedding_dimensions = $embed_dims")
        params["embedding"] = vector
        params.update(_stamp_params(vector))
    if clear_valid:
        # An OPEN start: "true since before anyone recorded when". Distinct
        # from leaving the field alone, which is what None means.
        sets.append("e.valid_at = null")
    elif valid_at is not None:
        sets.append("e.valid_at = datetime($valid_at)")
        params["valid_at"] = valid_at
    if clear_invalid:
        sets.append("e.invalid_at = null")
    elif invalid_at is not None:
        sets.append("e.invalid_at = datetime($invalid_at)")
        params["invalid_at"] = invalid_at
    if clear_expired:
        # Restoring a wrongly-retired fact needs BOTH halves cleared:
        # invalid_at is the semantic end, expired_at is Graphiti's retirement
        # bookkeeping — and the verification sweep attributes invalidations by
        # expired_at, so a restore that leaves it set still reads as retired.
        sets.append("e.expired_at = null")

    await _write(
        f"MATCH ()-[e:RELATES_TO {{uuid: $uuid}}]->() SET {', '.join(sets)}", **params
    )

    new_source = row["source"] if source_uuid is None else source_uuid
    new_target = row["target"] if target_uuid is None else target_uuid
    if (new_source, new_target) != (row["source"], row["target"]):
        moved = await _write(
            """
            MATCH (a:Entity {uuid: $source}), (b:Entity {uuid: $target})
            MATCH (old)-[e:RELATES_TO {uuid: $uuid}]->(oldb)
            CREATE (a)-[e2:RELATES_TO]->(b)
            SET e2 = properties(e),
                e2.source_node_uuid = $source, e2.target_node_uuid = $target
            DELETE e
            RETURN e2.uuid AS uuid
            """,
            uuid=uuid, source=new_source, target=new_target,
        )
        if not moved:
            raise WriteError("new source or target entity not found")
    return {"uuid": uuid, "embedded": vector is not None if fact is not None else None}


async def delete_edge(uuid: str) -> dict:
    rows = await _write(
        "MATCH ()-[e:RELATES_TO {uuid: $uuid}]->() DELETE e RETURN 1 AS ok",
        uuid=uuid,
    )
    if not rows:
        raise WriteError(f"no relationship with uuid {uuid}")
    return {"uuid": uuid}


async def merge_nodes(keep_uuid: str, drop_uuid: str) -> dict:
    """Fold a duplicate entity into the one being kept — the fix for the same
    human appearing twice. Every edge is re-created against the survivor with
    its uuid properties rewritten, then the duplicate is deleted. Self-loops
    that would result (an edge that already joined the two) are dropped rather
    than kept: "X is the parent of X" is never the fact that was meant."""
    if keep_uuid == drop_uuid:
        raise WriteError("cannot merge a node into itself")
    both = await _write(
        "MATCH (n:Entity) WHERE n.uuid IN [$keep, $drop] RETURN n.uuid AS uuid",
        keep=keep_uuid, drop=drop_uuid,
    )
    if len(both) != 2:
        raise WriteError("both entities must exist to merge")

    out = await _write(
        """
        MATCH (keep:Entity {uuid: $keep}), (drop:Entity {uuid: $drop})-[e:RELATES_TO]->(m)
        WHERE m.uuid <> $keep
        CREATE (keep)-[e2:RELATES_TO]->(m)
        SET e2 = properties(e), e2.source_node_uuid = $keep
        RETURN count(e2) AS moved
        """,
        keep=keep_uuid, drop=drop_uuid,
    )
    incoming = await _write(
        """
        MATCH (keep:Entity {uuid: $keep}), (m)-[e:RELATES_TO]->(drop:Entity {uuid: $drop})
        WHERE m.uuid <> $keep
        CREATE (m)-[e2:RELATES_TO]->(keep)
        SET e2 = properties(e), e2.target_node_uuid = $keep
        RETURN count(e2) AS moved
        """,
        keep=keep_uuid, drop=drop_uuid,
    )
    dropped = await delete_node(drop_uuid)
    moved = (out[0]["moved"] if out else 0) + (incoming[0]["moved"] if incoming else 0)
    return {
        "uuid": keep_uuid,
        "edges_moved": moved,
        "edges_discarded": dropped["edges_removed"] - moved,
    }


_GROUP_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


async def rescope_episode(episode_uuid: str, group_id: str) -> dict:
    """Move one episode — and what extraction produced from it — to another
    graph group (2026-09-01: eight onboarding doctrines committed under
    `private` landed in the EA's partition because `private` could only mean
    the PROPOSER's own group).

    The episode is the unit of scope: Graphiti dedupes entities within a
    group, so a node like "operator" is shared by every episode in the
    partition and cannot follow one of them. The rule per object:

    - an entity MENTIONED only by this episode (among its group's episodes)
      MOVES; one still mentioned by a staying episode is SPLIT — a copy in
      the target group, this episode's MENTIONS repointed to the copy;
    - a RELATES_TO edge whose `episodes` is only this episode MOVES (re-created
      when an endpoint was split, since Neo4j cannot repoint in place); an
      edge that also cites a staying episode is SPLIT the same way and this
      episode is removed from the original's `episodes`.

    Which is exactly the graph that ingesting the episode into the target
    group in the first place would have produced. Embeddings are copied, never
    recomputed — no text changes. Nothing is deleted."""
    if not _GROUP_ID_RE.match(group_id or ""):
        raise WriteError(f"group_id {group_id!r} must match [A-Za-z0-9_-]+")
    rows = await _write(
        "MATCH (ep:Episodic {uuid: $uuid}) RETURN ep.group_id AS group_id",
        uuid=episode_uuid,
    )
    if not rows:
        raise WriteError(f"no episode with uuid {episode_uuid}")
    old_group = rows[0]["group_id"]
    if old_group == group_id:
        raise WriteError(f"episode already in group {group_id}")

    # Entities: exclusive → move; shared with a staying episode → split.
    mentioned = await _write(
        """
        MATCH (ep:Episodic {uuid: $uuid})-[:MENTIONS]->(n:Entity)
        OPTIONAL MATCH (other:Episodic)-[:MENTIONS]->(n)
          WHERE other.uuid <> $uuid AND other.group_id = n.group_id
        RETURN n.uuid AS uuid, labels(n) AS labels, count(other) AS others
        """,
        uuid=episode_uuid,
    )
    remap: dict[str, str] = {}  # old entity uuid -> uuid now in the target group
    moved_nodes = split_nodes = 0
    for n in mentioned:
        if n["others"] == 0:
            await _write(
                "MATCH (n:Entity {uuid: $uuid}) SET n.group_id = $group_id",
                uuid=n["uuid"], group_id=group_id,
            )
            remap[n["uuid"]] = n["uuid"]
            moved_nodes += 1
        else:
            remap[n["uuid"]] = await _copy_entity(n["uuid"], n["labels"], group_id)
            split_nodes += 1

    # Edges this episode produced. An endpoint outside the MENTIONS set is
    # split too — the copy is what keeps every edge inside one group.
    edges = await _write(
        """
        MATCH (a:Entity)-[e:RELATES_TO]->(b:Entity) WHERE $uuid IN e.episodes
        RETURN e.uuid AS uuid, a.uuid AS source, b.uuid AS target,
               labels(a) AS a_labels, labels(b) AS b_labels, e.episodes AS episodes
        """,
        uuid=episode_uuid,
    )
    moved_edges = split_edges = 0
    new_edge_uuids: list[str] = []
    for e in edges:
        for end, lbls in ((e["source"], e["a_labels"]), (e["target"], e["b_labels"])):
            if end not in remap:
                remap[end] = await _copy_entity(end, lbls, group_id)
                split_nodes += 1
        exclusive = [x for x in e["episodes"] if x != episode_uuid] == []
        src, dst = remap[e["source"]], remap[e["target"]]
        if exclusive and (src, dst) == (e["source"], e["target"]):
            await _write(
                "MATCH ()-[e:RELATES_TO {uuid: $uuid}]->() SET e.group_id = $group_id",
                uuid=e["uuid"], group_id=group_id,
            )
            new_edge_uuids.append(e["uuid"])
            moved_edges += 1
            continue
        new_uuid = e["uuid"] if exclusive else str(_uuid.uuid4())
        await _write(
            """
            MATCH ()-[e:RELATES_TO {uuid: $uuid}]->()
            MATCH (a:Entity {uuid: $src}), (b:Entity {uuid: $dst})
            CREATE (a)-[e2:RELATES_TO]->(b)
            SET e2 = properties(e), e2.uuid = $new_uuid, e2.group_id = $group_id,
                e2.source_node_uuid = $src, e2.target_node_uuid = $dst,
                e2.episodes = [$episode]
            """,
            uuid=e["uuid"], src=src, dst=dst, new_uuid=new_uuid,
            group_id=group_id, episode=episode_uuid,
        )
        if exclusive:
            await _write("MATCH ()-[e:RELATES_TO {uuid: $uuid}]->() DELETE e", uuid=e["uuid"])
            moved_edges += 1
        else:
            await _write(
                """
                MATCH ()-[e:RELATES_TO {uuid: $uuid}]->()
                SET e.episodes = [x IN e.episodes WHERE x <> $episode]
                """,
                uuid=e["uuid"], episode=episode_uuid,
            )
            split_edges += 1
        new_edge_uuids.append(new_uuid)

    # The episode itself: group, its edge list, and MENTIONS repointed to copies.
    await _write(
        """
        MATCH (ep:Episodic {uuid: $uuid})
        SET ep.group_id = $group_id, ep.entity_edges = $entity_edges
        WITH ep
        MATCH (ep)-[m:MENTIONS]->(n:Entity)
        SET m.group_id = $group_id
        """,
        uuid=episode_uuid, group_id=group_id, entity_edges=new_edge_uuids,
    )
    for old, new in remap.items():
        if old != new:
            await _write(
                """
                MATCH (ep:Episodic {uuid: $uuid})-[m:MENTIONS]->(old:Entity {uuid: $old})
                MATCH (new:Entity {uuid: $new})
                CREATE (ep)-[m2:MENTIONS]->(new)
                SET m2 = properties(m), m2.uuid = randomUUID(), m2.group_id = $group_id
                DELETE m
                """,
                uuid=episode_uuid, old=old, new=new, group_id=group_id,
            )
    return {
        "uuid": episode_uuid, "from": old_group, "to": group_id,
        "nodes_moved": moved_nodes, "nodes_split": split_nodes,
        "edges_moved": moved_edges, "edges_split": split_edges,
    }


async def _copy_entity(uuid: str, labels: list[str], group_id: str) -> str:
    """A same-named twin of an entity in another group: every property
    (embedding included) except uuid and group. Labels come from the node
    itself, filtered through the ontology allowlist because Cypher cannot
    parameterize them."""
    label_clause = "".join(f":{l}" for l in labels if l in ENTITY_TYPES)
    new_uuid = str(_uuid.uuid4())
    await _write(
        f"""
        MATCH (n:Entity {{uuid: $uuid}})
        CREATE (n2:Entity{label_clause})
        SET n2 = properties(n), n2.uuid = $new_uuid, n2.group_id = $group_id,
            n2.created_at = $now
        """,
        uuid=uuid, new_uuid=new_uuid, group_id=group_id, now=_now(),
    )
    return new_uuid
