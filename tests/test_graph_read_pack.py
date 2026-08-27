"""A pack names tools by STRING, so a rename or a deletion is invisible until an
agent holding that pack tries to run — `build_toolsets` resolves the names with
`getattr(tools, ...)` at agent-build time, i.e. live, mid-turn.

Both graph reads must stay granted together: `search_knowledge_graph` finds
relationships and `search_knowledge_graph_entities` finds the entity itself, and
the steward failure of 2026-08-15 ("what do you know about my family") is what
holding only the first one looks like.
"""

from __future__ import annotations

from central_command.runtime import packs, tools


def test_graph_read_pack_matches_the_defined_tools():
    granted = packs.PACKS["graph-read"].tool_names
    assert set(granted) == {"search_knowledge_graph", "search_knowledge_graph_entities"}
    for name in granted:
        assert callable(getattr(tools, name, None)), f"graph-read names a missing tool: {name}"
