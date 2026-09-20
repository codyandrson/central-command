"""A mis-sized vector is dropped, because nothing downstream would complain.

`neo4j_writer` is the operator's own hand on the graph — the write half of the
cockpit Graph panel, with no approval gate because the operator IS the gate.
Every write here re-embeds the text it just changed, through the same alias and
at the same width Graphiti is configured with (`CC_EMBED_DIM`, 1024).

The reason the width is checked rather than trusted is written in the module:
"A wrong-width vector would corrupt the index rather than fail a query". Neo4j
stores whatever list it is handed; the vector index does not validate width, no
exception is raised, and the node stays perfectly visible to the keyword half
of hybrid search. What breaks is the semantic half — silently, for that node,
forever. Swapping `CC_EMBED_ALIAS` to a 768- or 1536-dimension model is a
one-line config change that would do exactly this to every subsequent write.

So `embed()` drops the vector and degrades the write instead: the edit still
lands (losing the operator's correction to a sleeping GPU is worse) and the
response says `embedded: False`, which is a fact the caller can see. A
`None` and a wrong width are the same answer on purpose.

No live graph and no network here: the embedder is a fake, per
`tests/conftest.py::no_live_graph_writes` — the bolt writes it guards are
opt-in behind `CC_LIVE_GRAPH_TESTS=1`, and nothing in this file reaches Neo4j
at all.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from central_command.config import Settings, settings
from central_command.integrations import neo4j_writer

SRC = pathlib.Path(__file__).resolve().parent.parent / "central_command"
WRITER = SRC / "integrations" / "neo4j_writer.py"


class _FakeResponse:
    def __init__(self, vector):
        self._vector = vector

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": [{"embedding": self._vector}]}


class _FakeClient:
    """Stands in for `httpx.AsyncClient` — the embedder call never leaves the
    process, so this runs with the workstation off and the graph untouched."""

    def __init__(self, vector, calls):
        self._vector = vector
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        self._calls.append(json)
        return _FakeResponse(self._vector)


@pytest.fixture
def fake_embedder(monkeypatch):
    """Returns a factory: `fake_embedder(vector)` -> list of request bodies."""
    def install(vector):
        calls: list[dict] = []
        monkeypatch.setattr(
            neo4j_writer.httpx, "AsyncClient",
            lambda *a, **k: _FakeClient(vector, calls),
        )
        return calls

    return install


def test_the_configured_width_is_still_the_one_graphiti_indexes():
    """1024 is not this module's opinion — it is the width of the vectors
    already in the graph. A default that drifted would make every write here
    degrade silently, which is the failure mode the check is for."""
    # The DEFAULT, not this checkout's .env: the shipped value is what a fresh
    # install indexes at, and an .env override is the operator's own decision.
    shipped = Settings.model_fields["embed_dim"].default
    assert shipped == 1024, (
        f"the shipped CC_EMBED_DIM default is {shipped}, not 1024. It must "
        "equal the embedder width in the Graphiti config — they index the same "
        "vectors, and a mismatch is a corrupt index rather than an error"
    )
    assert Settings.model_fields["embed_alias"].default == "cc-embedding"
    # And the module must read the setting rather than carrying its own copy,
    # or an override would desync the check from what is actually written.
    assert neo4j_writer._EMBED_DIMENSIONS == settings.embed_dim
    assert neo4j_writer._EMBED_MODEL == settings.embed_alias


async def test_a_right_sized_vector_is_stored(fake_embedder):
    fake_embedder([0.5] * settings.embed_dim)
    vector = await neo4j_writer.embed("the operator's grandfather was a machinist")
    assert vector is not None and len(vector) == settings.embed_dim


@pytest.mark.parametrize("width", [768, 1023, 1025, 1536, 0])
async def test_a_mis_sized_vector_is_dropped_not_stored(width, fake_embedder):
    fake_embedder([0.5] * width)
    vector = await neo4j_writer.embed("the operator's grandfather was a machinist")
    assert vector is None, (
        f"embed() returned a {width}-dimension vector where "
        f"{settings.embed_dim} was expected. Neo4j will store it and the vector "
        "index will not complain — the node simply stops answering the semantic "
        "half of hybrid search, with nothing anywhere to notice it by. No "
        "embedding beats a mis-sized one: return None and let the write degrade"
    )


async def test_the_provenance_stamp_is_nulled_with_the_vector():
    """A stamp beside a missing vector would claim an embedding that is not
    there — and `scripts/oneoff/reembed_graph.py` keys its resumability and its
    --verify on exactly these two properties."""
    present = neo4j_writer._stamp_params([0.5] * settings.embed_dim)
    assert present == {
        "embed_model": settings.embed_alias, "embed_dims": settings.embed_dim,
    }
    assert neo4j_writer._stamp_params(None) == {"embed_model": None, "embed_dims": None}


async def test_an_unreachable_embedder_degrades_the_write_rather_than_failing_it(monkeypatch):
    """The other half of the same rule: losing the operator's correction to a
    sleeping GPU is worse than storing it without a vector."""
    class _Boom:
        async def __aenter__(self):
            raise OSError("workstation is off")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(neo4j_writer.httpx, "AsyncClient", lambda *a, **k: _Boom())
    assert await neo4j_writer.embed("anything") is None


# --------------------------------------------------------------------------
# The other half of "EVERY write re-embeds": a walk, because a new write
# function would simply not call embed() and nothing would ever say so.
# --------------------------------------------------------------------------

def _writer_functions():
    tree = ast.parse(WRITER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


# Write functions that touch no embeddable text, with what they do instead.
NO_TEXT_TO_EMBED = {
    "delete_node": "deletes; there is no text left to embed",
    "delete_edge": "deletes; there is no text left to embed",
    "merge_nodes": "repoints relationships onto a node that already carries its own vector",
    "rescope_episode": "moves an episode between groups; name and fact are untouched",
}


def test_every_text_changing_graph_write_re_embeds_and_stamps():
    """`create_node`, `create_edge`, `update_node`, `update_edge` — the four
    that write a name or a fact. Each must pass its new text through `embed()`
    and write the stamp beside it, or the node goes into the graph invisible to
    semantic search."""
    offenders = []
    for fn in _writer_functions():
        name = fn.name
        if name.startswith("_") or name in NO_TEXT_TO_EMBED:
            continue
        if not name.startswith(("create_", "update_")):
            continue
        called = {
            (c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", None))
            for c in ast.walk(fn) if isinstance(c, ast.Call)
        }
        if "embed" not in called or "_stamp_params" not in called:
            offenders.append(
                f"neo4j_writer.{name} (calls embed={'embed' in called}, "
                f"_stamp_params={'_stamp_params' in called})"
            )
    assert sorted(offenders) == [], (
        "a graph write changes a node's name or an edge's fact without "
        "re-embedding it. The write will succeed and the row will look fine; "
        "it just stops answering the semantic half of hybrid search, keyword "
        "hits masking the gap. Call `embed(...)` on the new text and splat "
        "`**_stamp_params(vector)` into the same query — or, if the function "
        "genuinely changes no embeddable text, add it to NO_TEXT_TO_EMBED with "
        f"the reason: {sorted(offenders)}"
    )


def test_the_writer_walk_sees_the_writes_it_is_guarding():
    names = {fn.name for fn in _writer_functions()}
    assert {"create_node", "create_edge", "update_node", "update_edge"} <= names
    stale = sorted(set(NO_TEXT_TO_EMBED) - names)
    assert stale == [], (
        f"NO_TEXT_TO_EMBED names {stale}, which neo4j_writer.py no longer "
        "defines — a dead exemption is how an allowlist stops guarding"
    )
