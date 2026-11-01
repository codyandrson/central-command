"""The graph declares its embedder, and one check says whether it still holds.

Nothing else looks at what the graph holds: the width guards compare config to
config, and a same-width swap behind the alias is otherwise undetectable. So the
graph carries one node, `(:CCGraphMeta {key: 'embedder'})`, and `check()` compares
it with the configuration, with a census of the stored vectors and with fresh
embeddings of the record's canary texts. The record lives in the graph because a
restore must bring its record with it (DL-149). It has no `group_id` and a label
no graphiti-core or `neo4j_reader` query matches, so it is invisible to them.

`check()` answers ok, absent-and-empty (no vectors, no record: open) or a mismatch
with a plain reason. A check that CANNOT answer is not a verdict: the embedder or
the graph being unreachable propagates as the outage it is and nothing is cached.
A mismatch is raised as `contract.EmbedderMismatch`, a `DependencyDown`, because a
plain exception would classify as a NO and tell the agent to proceed without the
graph, which is the fail-open this module exists to prevent.

`graphiti._search` reaches this module through a function-local import; `runtime/`
never imports it. Writes go through `neo4j_writer._write` (function-local, so
loading this module loads no write path), the one seam the suite's
`no_live_graph_writes` guard refuses. `neo4j_reader` provides the reads.
"""

from __future__ import annotations

import contextvars
import json
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from central_command.config import settings
from central_command.contract import EmbedderMismatch
from central_command.contract.embedding_text import CANARY_TEXTS, TEXT_RECIPE
from central_command.contract.failures import EMBEDDER_REMEDY
from central_command.integrations import neo4j_reader

OK = "ok"
ABSENT_EMPTY = "absent_empty"
MISMATCH = "mismatch"

STABLE = "stable"
MIGRATING = "migrating"
AWAITING_CONFIG = "awaiting_config"
_CLOSED_STATES = (MIGRATING, AWAITING_CONFIG)

FIELDS = frozenset({
    "alias", "dimensions", "epoch", "text_recipe", "state", "canary_texts",
    "canary_vectors", "upstream_model", "updated_at", "history", "committed_at",
    "counters",
})
# Neo4j stores no list of lists and no map as a property.
_JSON_FIELDS = ("canary_vectors", "counters")

_CONSTRAINT = (
    "CREATE CONSTRAINT cc_graph_meta_key IF NOT EXISTS "
    "FOR (m:CCGraphMeta) REQUIRE m.key IS UNIQUE"
)
_READ_RECORD = "MATCH (m:CCGraphMeta {key: 'embedder'}) RETURN properties(m) AS m"
_UPDATE_RECORD = (
    "MERGE (m:CCGraphMeta {key: 'embedder'}) SET m += $fields RETURN properties(m) AS m"
)
# Conditional in the statement, not only in the caller: two first writers race
# between their read and their write, and the second must change nothing.
_BOOTSTRAP_RECORD = (
    "MERGE (m:CCGraphMeta {key: 'embedder'}) WITH m "
    "WHERE m.alias IS NULL AND coalesce(m.state, '') <> 'migrating' "
    "AND coalesce(m.state, '') <> 'awaiting_config' "
    "SET m += $fields RETURN properties(m) AS m"
)
_CENSUS = {
    "entity": (
        "MATCH (n:Entity) WHERE n.name_embedding IS NOT NULL "
        "RETURN size(n.name_embedding) AS width, count(*) AS n"
    ),
    "edge": (
        "MATCH ()-[r:RELATES_TO]->() WHERE r.fact_embedding IS NOT NULL "
        "RETURN size(r.fact_embedding) AS width, count(*) AS n"
    ),
    "community": (
        "MATCH (c:Community) WHERE c.name_embedding IS NOT NULL "
        "RETURN size(c.name_embedding) AS width, count(*) AS n"
    ),
}


@dataclass(frozen=True)
class Verdict:
    """`status` is OK, ABSENT_EMPTY or MISMATCH. A mismatch carries its `code`,
    `i` to `v`, and a `reason` that ends with the remedy."""

    status: str
    code: str | None = None
    reason: str | None = None

    @property
    def open(self) -> bool:
        return self.status != MISMATCH


VERDICT_OK = Verdict(OK)


def _mismatch(code: str, what: str) -> Verdict:
    return Verdict(MISMATCH, code, f"{what}; {EMBEDDER_REMEDY}")


def _width_reason(width: int, count: int, kind: str, dimensions: int) -> str:
    return f"census found width {width} on {count} {kind}, record says {dimensions}"


# --- the record ----------------------------------------------------------------


def _decoded(properties: dict) -> dict:
    record = dict(properties)
    for field in _JSON_FIELDS:
        if isinstance(record.get(field), str):
            try:
                record[field] = json.loads(record[field])
            except ValueError:
                record[field] = None  # unusable reads as absent, which closes
    return record


def _encoded(fields: dict) -> dict:
    encoded = dict(fields)
    for field in _JSON_FIELDS:
        if encoded.get(field) is not None:
            encoded[field] = json.dumps(encoded[field])
    return encoded


async def read_record() -> dict | None:
    rows = await neo4j_reader._read(_READ_RECORD)
    return _decoded(rows[0]["m"]) if rows else None


async def _write(query: str, **params) -> list[dict]:
    from central_command.integrations import neo4j_writer

    return await neo4j_writer._write(query, **params)


async def ensure_constraint() -> None:
    """Idempotent, and it runs before every record write, so the record never
    exists without it."""
    await _write(_CONSTRAINT)


async def update_record(**fields) -> dict:
    """Write any of `FIELDS` on the record, creating it if it is not there. A
    None value removes the property. The check's cached verdict goes with it."""
    unknown = sorted(set(fields) - FIELDS)
    if unknown:
        raise ValueError(f"not embedder record fields: {', '.join(unknown)}")
    fields.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
    await ensure_constraint()
    rows = await _write(_UPDATE_RECORD, fields=_encoded(fields))
    clear_cache()
    return _decoded(rows[0]["m"])


async def census() -> dict[str, dict[int, int]]:
    """Stored vectors by kind and width: `{'entity': {1024: 98}, 'edge': {...},
    'community': {}}`. Elements with no vector are not counted."""
    out: dict[str, dict[int, int]] = {}
    for kind, query in _CENSUS.items():
        rows = await neo4j_reader._read(query)
        out[kind] = {row["width"]: row["n"] for row in rows}
    return out


async def _embed_canaries(texts) -> list[list[float]]:
    # Bare, so the check measures the width itself: `embed()` would refuse a
    # wrong width as a write error, and graphiti-core's embedder slices it.
    from central_command.integrations import neo4j_writer

    return [await neo4j_writer.request_embedding(text) for text in texts]


def _refusal(exc: httpx.HTTPStatusError) -> Verdict | None:
    """The mismatch for an embedder that ANSWERED no: the alias deleted, or out of
    the app key's reach. Left as an error it would classify as a NO and reach the
    agent as "proceed without it". 408 and 429 mean come back later, and with
    every 5xx they stay the outage they are (None: the caller re-raises)."""
    status = exc.response.status_code
    if not 400 <= status < 500 or status in (408, 429):
        return None
    return _mismatch(
        "ii", f"configured {settings.embed_alias} is refused by the proxy (HTTP {status})",
    )


def _wrong_width(vectors: list[list[float]], dimensions: int) -> tuple[int, int] | None:
    """(width, how many) of the first canary whose width is wrong, else None."""
    for vector in vectors:
        if len(vector) != dimensions:
            return len(vector), sum(1 for v in vectors if len(v) == len(vector))
    return None


async def bootstrap_if_empty() -> bool:
    """Write the first record on a graph with no record and no vectors, from the
    configured alias. True when it wrote; a no-op otherwise, including on a graph
    in migration. This is the one automatic record write, the same kind of
    self-describing write as the index DDL. A configured width the embedder does
    not produce raises the mismatch and writes nothing: a record of a width
    nothing can store would only close the graph later, for a worse reason."""
    record = await read_record()
    if record and (record.get("alias") or record.get("state") in _CLOSED_STATES):
        return False
    if any((await census()).values()):
        return False
    dimensions = int(settings.embed_dim)
    try:
        vectors = await _embed_canaries(CANARY_TEXTS)
    except httpx.HTTPStatusError as exc:
        refused = _refusal(exc)
        if refused is None:
            raise
        raise EmbedderMismatch(refused.reason) from exc
    wrong = _wrong_width(vectors, dimensions)
    if wrong:
        raise EmbedderMismatch(
            _mismatch("iii", _width_reason(wrong[0], wrong[1], "canary", dimensions)).reason
        )
    await ensure_constraint()
    rows = await _write(_BOOTSTRAP_RECORD, fields=_encoded({
        "alias": settings.embed_alias,
        "dimensions": dimensions,
        "epoch": 1,
        "text_recipe": TEXT_RECIPE,
        "state": STABLE,
        "canary_texts": list(CANARY_TEXTS),
        "canary_vectors": vectors,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }))
    clear_cache()
    return bool(rows)


# --- the check -----------------------------------------------------------------

_cached: tuple[float, Verdict] | None = None

# A ContextVar, so nothing outside the `with` block can see it; no env key or
# setting reaches it. `graph_reembed` verifies its own search under it.
_operator_session: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "embedder_operator_session", default=False,
)


def clear_cache() -> None:
    global _cached
    _cached = None


def cached_verdict() -> Verdict | None:
    """The last verdict this process reached, expired or not, or None before the
    first. No network: it is what the retry sweep and the Systems row read to
    know whether to ask, and a stale answer there only costs one more probe."""
    return _cached[1] if _cached is not None else None


@contextmanager
def operator_session() -> Iterator[None]:
    """`check()` answers ok inside this block. For `graph_reembed`'s verification
    only: it searches a graph whose gate is deliberately closed. Never reached
    from `runtime/` (tests/test_embedder_record.py)."""
    token = _operator_session.set(True)
    try:
        yield
    finally:
        _operator_session.reset(token)


def _cosine(a: list[float], b: list[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / norm if norm else 0.0


async def _compare_canaries(record: dict, alias: str, dimensions: int) -> Verdict | None:
    texts = record.get("canary_texts") or []
    stored = record.get("canary_vectors") or []
    if not texts or len(texts) != len(stored) or any(len(v) != dimensions for v in stored):
        return _mismatch(
            "iv", f"the record for {alias} holds no usable canaries, so the model behind "
                  "it cannot be confirmed",
        )
    try:
        vectors = await _embed_canaries(texts)
    except httpx.HTTPStatusError as exc:
        refused = _refusal(exc)
        if refused is None:
            raise
        return refused
    wrong = _wrong_width(vectors, dimensions)
    if wrong:
        return _mismatch("iii", _width_reason(wrong[0], wrong[1], "canary", dimensions))
    lowest = min(_cosine(a, b) for a, b in zip(stored, vectors))
    if lowest < settings.embedder_canary_threshold:
        return _mismatch(
            "iv", f"the model behind {alias} has changed since the graph was embedded "
                  f"(canary cosine {lowest:.4f})",
        )
    return None


async def _verdict() -> Verdict:
    record = await read_record()
    state = record.get("state") if record else None
    alias = record.get("alias") if record else None
    # First, and for any record: a migration closes the graph whether or not it
    # has declared an alias yet, and the alias is expected to disagree by then.
    if state in _CLOSED_STATES or (alias and state != STABLE):
        return _mismatch("v", f"embedding migration in progress ({state})")
    counted = await census()
    if not alias:  # an interim record with no alias counts as absent
        if any(counted.values()):
            return _mismatch("i", "graph has vectors but no embedder record")
        return Verdict(ABSENT_EMPTY)
    dimensions = record.get("dimensions")
    if alias != settings.embed_alias or dimensions != int(settings.embed_dim):
        return _mismatch(
            "ii", f"configured {settings.embed_alias}/{settings.embed_dim}, "
                  f"graph holds {alias}/{dimensions}",
        )
    for kind, widths in counted.items():
        for width in sorted(widths):
            if width != dimensions:
                return _mismatch("iii", _width_reason(width, widths[width], kind, dimensions))
    return await _compare_canaries(record, alias, dimensions) or VERDICT_OK


async def check(force: bool = False) -> Verdict:
    """The one check every gate calls. Cached `embedder_check_ttl_seconds`, because
    a read can pass the gate twice and a census scans the graph; `force` asks
    afresh. An exception means no answer, and it propagates uncached, so the next
    caller asks again."""
    global _cached
    if _operator_session.get():
        return VERDICT_OK
    if not force and _cached is not None and time.monotonic() < _cached[0]:
        return _cached[1]
    verdict = await _verdict()
    _cached = (time.monotonic() + settings.embedder_check_ttl_seconds, verdict)
    return verdict


async def require_open() -> None:
    """Raise the mismatch `check()` found. What a read gate calls."""
    verdict = await check()
    if not verdict.open:
        raise EmbedderMismatch(verdict.reason)


async def require_open_for_write() -> None:
    """`require_open`, and the first vector on an empty graph writes the record
    first. What the curation writer calls, after `embed()` and before its write."""
    verdict = await check()
    if verdict.status == ABSENT_EMPTY:
        await bootstrap_if_empty()
    elif not verdict.open:
        raise EmbedderMismatch(verdict.reason)
