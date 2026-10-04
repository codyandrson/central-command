"""The ontology module (D4): ten field-less models carrying MCP 1.1.0's docstrings verbatim.

The docstring is the extraction guidance (the prompt's entity-type block is
built from `__doc__` alone), so its bytes are pinned. Changing one is a change
to what the extraction model is told: measure it, then update the hash here.
The hashes were computed from the upstream file with
`ast.get_docstring(cls, clean=False)` — the raw, uncleaned text CPython 3.12
stores in `__doc__`.
"""

from __future__ import annotations

import hashlib

from central_command.integrations import graph_ontology as onto
from central_command.integrations import neo4j_writer

ORDER = (
    "Person", "Preference", "Requirement", "Procedure", "Location",
    "Event", "Organization", "Document", "Topic", "Object",
)

DOC_SHA256 = {
    "Person": "87d32d25c603f818a77a212f758030a08ef88f671befa4e5e0e5fef5077d5977",
    "Preference": "d927fada170bfdc4843c414ba7bf80b72099e13c84e3c6bed2cea5fc3de4d3cd",
    "Requirement": "cafc77a2ea2cfc20a71206ab527e2976e4c2773c4cf10fedff3225f216511aca",
    "Procedure": "09aff6522b84126fa148fbab997a2f23e35fb904f0d60a5efe0073b1d9a7f18f",
    "Location": "6179d306f5b9c360ca7aec45ad5cc221274b5814f0b7a55b030eab2b1f32eac5",
    "Event": "9e184161d015f4fb6befb22df4d0668c2871b5a78889cd4b8619516899d77828",
    "Organization": "c3be9393a71b5ef43f79fafc6a3a7f4964d3ed839288110d0ad9e272ee2e8b88",
    "Document": "0dfed00f85edeb0997c6592d405fc776e96fbc9f768d15d6ddb5b8446a6b2a9a",
    "Topic": "fcc5e52b2d73d00252d003aec20d3473c931ea36d38159a9c3681997ed49374d",
    "Object": "6a1b55bac09fe5153cc22ec8457d3f7f65323abde1db7a5bc9bc84b94394e265",
}

# graphiti-core 0.30.2: Node + EntityNode. A type attribute with one of these
# names would collide with the node's own fields in the extraction schema.
ENTITY_NODE_FIELDS = {
    "uuid", "name", "group_id", "labels", "created_at",
    "name_embedding", "summary", "attributes",
}


def test_the_ten_types_come_in_the_documented_order():
    assert onto.ENTITY_TYPE_NAMES == ORDER
    assert tuple(onto.ENTITY_TYPES) == ORDER
    assert all(m.__name__ == n for n, m in onto.ENTITY_TYPES.items())


def test_each_docstring_is_byte_identical_to_the_upstream_builtin():
    got = {n: hashlib.sha256(m.__doc__.encode()).hexdigest() for n, m in onto.ENTITY_TYPES.items()}
    assert got == DOC_SHA256


def test_the_docstrings_keep_their_raw_indentation_and_newlines():
    # Not cleaned: the prompt is built from `__doc__` as stored.
    assert onto.Person.__doc__.startswith("A Person represents an individual human")
    assert "\n    " in onto.Person.__doc__
    assert onto.Preference.__doc__.startswith("\n    IMPORTANT: Prioritize")


def test_every_model_is_field_less():
    # A field would bring back the per-entity attribute call (and an unbounded
    # attribute with it).
    for name, model in onto.ENTITY_TYPES.items():
        assert len(model.model_fields) == 0, name


def test_no_model_could_collide_with_an_entity_node_field():
    for name, model in onto.ENTITY_TYPES.items():
        assert not set(model.model_fields) & ENTITY_NODE_FIELDS, name


def test_the_writer_allowlist_names_the_same_types():
    # neo4j_writer keeps its own tuple for now; the later derivation is only
    # safe while the two agree.
    assert set(neo4j_writer.ENTITY_TYPES) == set(onto.ENTITY_TYPE_NAMES)
    assert tuple(neo4j_writer.ENTITY_TYPES) == onto.ENTITY_TYPE_NAMES
