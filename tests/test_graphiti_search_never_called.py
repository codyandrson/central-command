"""`Graphiti.search()` is never called — DL-131, by name.

`Graphiti.search()` assigns `limit` on a MODULE-LEVEL recipe object that
`add_episode` also reads for its dedupe and invalidation candidates, so one
search call changes extraction for the life of the process. Every search goes
through `search_()` with a deep copy of the recipe
(`tests/test_graphiti_client.py::test_search_uses_a_deep_copy_with_the_requested_limit`
proves the copy). This walks the source, as `tests/test_proposal_created.py`
does for its seam: in `central_command/`, no `.search(` is called on the
Graphiti client — a name bound from `get_graphiti()` or the ingest module's
`_client()`, or either call directly. `search_(` is the allowed spelling.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "central_command"
FACTORIES = {"get_graphiti", "_client"}


def _factory_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
    return name in FACTORIES


def offenders(source: str, filename: str = "<src>") -> list[str]:
    tree = ast.parse(source, filename)
    bound: set[str] = set()
    for node in ast.walk(tree):
        value = getattr(node, "value", None)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and _factory_call(value):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            bound |= {t.id for t in targets if isinstance(t, ast.Name)}
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "search"):
            continue
        recv = node.func.value
        if (isinstance(recv, ast.Name) and recv.id in bound) or _factory_call(recv):
            out.append(f"{filename}:{node.lineno}")
    return out


def test_no_graphiti_search_call_anywhere_in_the_package():
    found = []
    for path in sorted(PKG.rglob("*.py")):
        found += offenders(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)))
    assert not found, (
        "Graphiti.search() mutates a module-level recipe add_episode reads — "
        f"use search_() with a deep-copied recipe (DL-131): {found}"
    )


def test_the_walk_catches_every_spelling_and_allows_search_():
    bad = (
        "g = graphiti_client.get_graphiti()\nawait g.search('q')\n"
        "await get_graphiti().search('q')\n"
        "c = _client()\nawait c.search('q')\n"
        "if (h := client.get_graphiti()):\n    h.search('q')\n"
    )
    assert len(offenders(bad)) == 4, offenders(bad)
    good = (
        "g = graphiti_client.get_graphiti()\nawait g.search_('q', config=cfg)\n"
        "import re\nre.search('a', 'b')\n"
    )
    assert offenders(good) == []
