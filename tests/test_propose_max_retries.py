"""Every `propose_*` tool a toolset attaches carries a 10-retry budget.

pydantic-ai's default retry budget is ONE — the second bad draft failed
whole sessions live (2026-08-18, see `runtime/packs.py:toolset_for`'s
comment). `test_proposal_args.py::test_every_propose_tool_has_a_redraft_budget`
proves this for the static `toolset_for` path by building the real toolset.
It cannot reach `mcp_toolsets_for` the same way — that path needs a live DB
to resolve an agent's `mcp:` grants — so this is a source walk instead, in
the style of `test_proposal_created.py`: it looks at every list literal
handed to a `FunctionToolset(...)` call anywhere in `runtime/` and fails if
a `propose_*` function appears there unwrapped by `Tool(..., max_retries=10)`.

Found live 2026-09-20: `mcp_toolsets_for` attached
`tools_mod.propose_mcp_tool_call` bare, so the shared MCP-call proposal tool
had no redraft budget at all — the exact 2026-08-18 failure mode, just on a
path the runtime test above never executes.
"""

from __future__ import annotations

import ast
import pathlib

RUNTIME = pathlib.Path(__file__).resolve().parent.parent / "central_command" / "runtime"


def _propose_name(node: ast.AST) -> str | None:
    """The dotted-or-bare name if this node names a `propose_*` function."""
    if isinstance(node, ast.Attribute) and node.attr.startswith("propose_"):
        return node.attr
    if isinstance(node, ast.Name) and node.id.startswith("propose_"):
        return node.id
    return None


def _is_max_retries_wrapped(node: ast.AST) -> bool:
    """True if `node` is a `Tool(fn, max_retries=10)`-shaped call (or a Name
    bound to one — `toolset_for` assigns `fn = Tool(fn, max_retries=10)`
    before adding it to the toolset, so a bare Name there is not itself
    proof of anything; that path is covered by the runtime test instead)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if called != "Tool":
        return False
    return any(kw.arg == "max_retries" for kw in node.keywords)


def _find_unwrapped_propose_literals(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "FunctionToolset"):
            continue
        if not call.args or not isinstance(call.args[0], ast.List):
            # Not a list literal (e.g. `toolset_for`'s `list(seen.values())`,
            # built dynamically and checked at runtime instead).
            continue
        for element in call.args[0].elts:
            name = _propose_name(element)
            if name and not _is_max_retries_wrapped(element):
                offenders.append(f"{path.name}: {name}")
    return offenders


def test_every_literal_toolset_wraps_propose_tools():
    offenders = sorted(
        offender
        for path in RUNTIME.rglob("*.py")
        for offender in _find_unwrapped_propose_literals(path)
    )
    assert offenders == [], (
        "these FunctionToolset(...) list literals hand a propose_* function "
        "straight to the toolset with no Tool(fn, max_retries=10) wrap, so "
        "the second bad draft would fail the whole session (2026-08-18): "
        f"{offenders}"
    )
