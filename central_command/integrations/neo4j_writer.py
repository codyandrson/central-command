"""OPERATOR curation writes against the Graphiti graph — the write half of the
cockpit Graph panel (increment #1 of the 2026-08-09 graph-inspection spec).

**This is not an agent path and must never become one.** Agents READ the graph
(in-process, `integrations/graphiti.py`), and the one write they can cause is a
gated `graph.add_episode` proposal, which the ingest worker extracts after
approval. What lives here is the operator's own hand on the data:
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
  as `source_node_uuid` / `target_node_uuid` properties. The relationship is
  the truth (every read — upstream's and the cockpit's since v2.61.0 — takes
  `startNode`/`endNode`), but the properties are part of the bulk shape and
  are still written. Neo4j cannot repoint a relationship in place, so
  `update_edge` recreates it and rewrites both properties together.
- **The shape is the bulk path's** (`add_nodes_and_edges_bulk`, what
  extraction writes) and `tests/test_graph_write_shape.py` compares the
  property sets written here against the INSTALLED package's: a fact carries
  `expired_at` and `reference_time` like an extracted one, and vectors are
  stored with `db.create.setNodeVectorProperty` /
  `setRelationshipVectorProperty`, as the bulk queries store them.
- **Embeddings are not optional decoration.** `name_embedding` / `fact_embedding`
  are half of Graphiti's hybrid search; a node written without one is reachable
  by keyword (the fulltext indexes) but INVISIBLE to the semantic half, so an
  agent asking "who is the operator's grandfather" would miss the fact the operator just
  typed in. Every write here re-embeds through the same LiteLLM alias and the
  same dimensions Graphiti is configured with — a mismatch there is a
  corrupt index, not an error. (Through our own request, not the library's
  embedder: `OpenAIEmbedder.create` slices the vector to `embedding_dim`, so
  a model answering at the wrong width would be TRUNCATED into a
  meaningless vector instead of being dropped.) Embedding is best-effort on purpose: if the
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
from central_command.integrations import neo4j_reader
from central_command.integrations.neo4j_reader import _get_driver

log = logging.getLogger(__name__)

# Mirrors `graph_ontology.ENTITY_TYPE_NAMES` (tests/test_graph_ontology.py pins the
# two equal). Closed on purpose:
# Neo4j 5.26 has no parameterized labels (dynamic `SET n:$(x)` is a 2025.x
# feature), so a label reaches Cypher via string interpolation and the allowlist
# is what makes that safe. A type added to the ontology belongs here too.
ENTITY_TYPES = (
    "Person", "Preference", "Requirement", "Procedure", "Location",
    "Event", "Organization", "Document", "Topic", "Object",
)

# Must equal the embedder `graphiti_client` builds (the same two settings).
# They index the same vectors.
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


async def _write_tx(work):
    """Run `work(run)` inside ONE managed write transaction, where `run(query,
    **params) -> list[dict]` executes on that transaction — all of it commits
    or none of it does. The driver retries the whole function on a transient
    error, so `work` must be safe to run again from the top (it is: it reads
    the state it acts on inside the same transaction)."""
    driver = _get_driver()

    async def tx_fn(tx):
        async def run(query: str, **params) -> list[dict]:
            result = await tx.run(query, **params)
            return [record.data() async for record in result]

        return await work(run)

    async with driver.session() as session:
        return await session.execute_write(tx_fn)


async def request_embedding(text: str, timeout: float = 60.0) -> list[float]:
    """The embedder round trip itself — URL, key, alias — RAISING on any
    failure and checking no width. `embed()` below wraps it in the degrade-
    don't-fail policy; the application self-check (`central_command/
    selfcheck.py`, `embedding-as-app`) calls it bare, so the check proves the
    writer's own request rather than a copy of it, and can say WHY it failed
    where `embed()` can only log."""
    async with httpx.AsyncClient(timeout=timeout, **http_client.client_kwargs()) as client:
        res = await client.post(
            f"{settings.llm_proxy_base_url}/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.llm_proxy_admin_key}"},
            json={"model": _EMBED_MODEL, "input": text},
        )
        res.raise_for_status()
        return res.json()["data"][0]["embedding"]


async def embed(text: str) -> list[float] | None:
    """Vector for `text` from the same embedder Graphiti uses, or None if the
    workstation is unreachable. None is a degraded write, never a failed one."""
    if not text.strip():
        return None
    try:
        vector = await request_embedding(text)
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


def _vector(var: str, prop: str, kind: str, vector: list[float] | None) -> tuple[str, dict]:
    """Store a vector the way the bulk path does — `db.create.setNodeVectorProperty`
    / `setRelationshipVectorProperty` (verified on Neo4j 5.26: it replaces an
    existing list property in place and refuses a null vector) — or, for a
    degraded write, CLEAR it: a stale vector beside changed text keeps matching
    the OLD text in every semantic search. Returns (cypher tail, params)."""
    if vector is None:
        return f" SET {var}.{prop} = null", {}
    proc = "setNodeVectorProperty" if kind == "node" else "setRelationshipVectorProperty"
    return f" WITH {var} CALL db.create.{proc}({var}, '{prop}', $vector)", {"vector": vector}


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
    now: datetime | None = None,
) -> str:
    """Provenance for a hand-made fact: an Episodic node saying WHO (the
    operator), WHEN (now) and WHAT was entered, MENTIONS-linked to the entities
    involved — the same shape extraction writes, so the Provenance drawer and
    Graphiti's own reads treat it as a first-class episode."""
    ep_uuid = str(_uuid.uuid4())
    now = now or _now()
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
    vec_tail, vec_params = _vector("n", "name_embedding", "node", vector)
    await _write(
        f"""
        CREATE (n:Entity{label_clause})
        SET n.uuid = $uuid, n.name = $name, n.summary = $summary,
            n.group_id = $group_id, n.created_at = $created_at,
            n.labels = $labels
        """ + vec_tail,
        uuid=node_uuid, name=name, summary=summary, group_id=group_id,
        created_at=_now(), labels=extra + ["Entity"], **vec_params,
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

    tail = ""
    if text_changed:
        tail, vec_params = _vector("n", "name_embedding", "node", vector)
        params.update(vec_params)
    await _write(
        f"MATCH (n:Entity {{uuid: $uuid}}) SET {', '.join(sets)}{tail}", **params
    )
    return {"uuid": uuid, "embedded": vector is not None if text_changed else None}


async def delete_node(uuid: str) -> dict:
    """DETACH DELETE: the node and every edge touching it. The Episodic node
    that produced it survives — provenance is history and stays readable, and
    re-ingesting that episode could legitimately recreate the entity."""
    rows = await _write(
        """
        MATCH (n:Entity {uuid: $uuid})
        OPTIONAL MATCH (n)-[r:RELATES_TO]-()
        WITH n, collect(DISTINCT r.uuid) AS edge_uuids
        DETACH DELETE n
        RETURN edge_uuids
        """,
        uuid=uuid,
    )
    if not rows:
        raise WriteError(f"no entity with uuid {uuid}")
    # RELATES_TO only: DETACH DELETE also drops MENTIONS provenance links, but
    # counting those read as knowledge edges the operator never had (a node
    # with 1 fact reported edges_removed: 3 — found in the 2026-08-25 sweep).
    # The uuids ride along because a count is untraceable: two edges a
    # verification row had recorded vanished on 2026-09-01 and nothing in
    # Postgres named them until this delete's proposal was read by hand.
    edge_uuids = [u for u in rows[0]["edge_uuids"] if u]
    return {"uuid": uuid, "edges_removed": len(edge_uuids), "edge_uuids": edge_uuids}


async def create_edge(
    source_uuid: str, target_uuid: str, name: str, fact: str,
    valid_at: str | None = None, invalid_at: str | None = None,
) -> dict:
    """A None `valid_at` is stored as null — an UNBOUNDED start (2026-09-19).
    It used to be stamped with the creation instant, which recorded newly-
    LEARNED as newly-TRUE; the gated path now requires the argument and spells
    the unknown case `unbounded`, and the operator's direct path (cockpit,
    API) records exactly what was given and nothing more."""
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
    now = _now()
    episode = await _record_operator_episode(
        fact, ends[0]["group_id"],
        mentions=[source_uuid, target_uuid], entity_edges=[edge_uuid], now=now,
    )
    vec_tail, vec_params = _vector("e", "fact_embedding", "edge", vector)
    await _write(
        """
        MATCH (a:Entity {uuid: $source}), (b:Entity {uuid: $target})
        CREATE (a)-[e:RELATES_TO]->(b)
        SET e.uuid = $uuid, e.name = $name, e.fact = $fact,
            e.group_id = $group_id, e.created_at = $created_at,
            e.valid_at = CASE WHEN $valid_at IS NULL THEN null
                              ELSE datetime($valid_at) END,
            e.invalid_at = CASE WHEN $invalid_at IS NULL THEN null
                                ELSE datetime($invalid_at) END,
            e.source_node_uuid = $source, e.target_node_uuid = $target,
            e.episodes = [$episode],
            e.expired_at = null, e.reference_time = $created_at
        """ + vec_tail,
        source=source_uuid, target=target_uuid, uuid=edge_uuid, name=name,
        fact=fact, group_id=ends[0]["group_id"], created_at=now,
        valid_at=valid_at, invalid_at=invalid_at, episode=episode, **vec_params,
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
    properties are rewritten in the same statement: reads take the topology
    as truth, but the properties are part of the bulk shape and the audit's
    mismatch check reads them."""
    current = await _write(
        """
        MATCH (a)-[e:RELATES_TO {uuid: $uuid}]->(b)
        RETURN e.name AS name, e.fact AS fact, a.uuid AS source, b.uuid AS target
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

    tail = ""
    if new_fact != row["fact"]:
        tail, vec_params = _vector("e", "fact_embedding", "edge", vector)
        params.update(vec_params)
    await _write(
        f"MATCH ()-[e:RELATES_TO {{uuid: $uuid}}]->() SET {', '.join(sets)}{tail}", **params
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
        # An exclusive edge keeps its uuid, so the original is deleted IN THE
        # SAME STATEMENT, by the matched variable: a second statement matching
        # by uuid would match the copy too and delete both (found 2026-10-05
        # on a scratch graph — the moved fact vanished).
        await _write(
            """
            MATCH ()-[e:RELATES_TO {uuid: $uuid}]->()
            MATCH (a:Entity {uuid: $src}), (b:Entity {uuid: $dst})
            CREATE (a)-[e2:RELATES_TO]->(b)
            SET e2 = properties(e), e2.uuid = $new_uuid, e2.group_id = $group_id,
                e2.source_node_uuid = $src, e2.target_node_uuid = $dst,
                e2.episodes = [$episode]
            """ + ("DELETE e" if exclusive else ""),
            uuid=e["uuid"], src=src, dst=dst, new_uuid=new_uuid,
            group_id=group_id, episode=episode_uuid,
        )
        if exclusive:
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


class PreviewChanged(WriteError):
    """The graph no longer deletes what the operator approved — refused."""


DELETE_FACTS = "MATCH ()-[r:RELATES_TO]->() WHERE r.uuid IN $facts DELETE r"
DELETE_ENTITIES = "MATCH (n:Entity) WHERE n.uuid IN $entities DETACH DELETE n"
DELETE_EPISODE = "MATCH (e:Episodic {uuid: $uuid}) DETACH DELETE e"
STRIP_EPISODE_FROM_FACTS = """
MATCH ()-[r:RELATES_TO]->() WHERE $uuid IN r.episodes
SET r.episodes = [x IN r.episodes WHERE x <> $uuid]
RETURN count(r) AS n
"""
STRIP_DEAD_FACTS_FROM_EPISODES = """
MATCH (o:Episodic) WHERE any(x IN coalesce(o.entity_edges, []) WHERE x IN $dead)
SET o.entity_edges = [x IN o.entity_edges WHERE NOT x IN $dead]
RETURN count(o) AS n
"""


async def delete_episode(
    episode_uuid: str, *, expected: dict | None = None, known_dead_facts: list[str] = (),
) -> dict:
    """Delete one episode and what only it produced — upstream's
    `Graphiti.remove_episode` rule (computed by `neo4j_reader.
    compute_delete_preview`, pinned to upstream by tests/test_graph_delete_episode.py)
    plus the provenance cleanup upstream omits — in ONE transaction of our own.

    Why not call `remove_episode` and then clean up: upstream runs it as three
    separate auto-commit deletes (`Edge.delete_by_uuids`, then
    `Node.delete_by_uuids` — itself `CALL … IN TRANSACTIONS` batches — then
    the episode), so a crash between them leaves an episode whose facts are
    gone and whose entities are not, and a re-run reads a DIFFERENT set (the
    deleted facts no longer count). For a destructive operation atomicity is
    worth more than reusing upstream's code; the pin test keeps the rule
    honest. Here the set is read, compared and deleted in one transaction:
    it happens whole or not at all, so a re-run after a crash is the same
    deletion.

    `expected` — the preview the deletion was approved against (the
    proposal's embedded preview, or the cockpit's confirmed one). When given
    and the sets differ, nothing is deleted: `PreviewChanged`.

    Idempotent: when the episode is already gone the deletion is DONE, and the
    cleanup still runs — the dead uuid leaves every fact's `episodes`, and
    `known_dead_facts` (the uuids the approved preview deleted) leave every
    episode's `entity_edges`."""

    async def work(run):
        preview = await neo4j_reader.compute_delete_preview(run, episode_uuid)
        if preview is None:
            dead = sorted(set(known_dead_facts))
            stripped = await run(STRIP_EPISODE_FROM_FACTS, uuid=episode_uuid)
            refs = await run(STRIP_DEAD_FACTS_FROM_EPISODES, dead=dead) if dead else []
            return {
                "uuid": episode_uuid, "already_absent": True,
                "facts_deleted": [], "collateral_deleted": [], "entities_deleted": [],
                "facts_unlinked": stripped[0]["n"] if stripped else 0,
                "episodes_updated": refs[0]["n"] if refs else 0,
            }
        if expected is not None:
            changes = neo4j_reader.preview_changes(expected, preview)
            if changes:
                raise PreviewChanged(
                    "the graph changed since this deletion was approved — nothing was "
                    "deleted: " + "; ".join(changes)
                )
        sets = neo4j_reader.preview_sets(preview)
        dead = sorted(set(sets["facts"]) | set(sets["collateral_facts"]))
        await run(DELETE_FACTS, facts=sets["facts"])
        await run(DELETE_ENTITIES, entities=sets["entities"])
        await run(DELETE_EPISODE, uuid=episode_uuid)
        stripped = await run(STRIP_EPISODE_FROM_FACTS, uuid=episode_uuid)
        refs = await run(STRIP_DEAD_FACTS_FROM_EPISODES, dead=dead) if dead else []
        return {
            "uuid": episode_uuid, "already_absent": False,
            "name": preview["episode"]["name"], "group_id": preview["episode"]["group_id"],
            "facts_deleted": sets["facts"], "collateral_deleted": sets["collateral_facts"],
            "entities_deleted": sets["entities"],
            "facts_unlinked": stripped[0]["n"] if stripped else 0,
            "episodes_updated": refs[0]["n"] if refs else 0,
            "digest": preview["digest"],
        }

    return await _write_tx(work)
