"""Two governance rules that only prose was holding — now the suite holds them.

1. `system_prompt=` is load-bearing. A system prompt is PERSISTED into the
   message history, so a session paused at a deferral resumes under the charter
   it proposed under; `instructions=` is re-applied from the live agent at every
   run, so a resume would silently execute under whatever the charter says NOW.
   An `Agent(..., instructions=...)` anywhere in the package is that rewrite.

2. The approval gate is `CallDeferred` + `propose_*` + the credentialed
   Executor. pydantic-ai's `approval_required()` means "collect a yes, then run
   the tool IN THIS PROCESS" — which puts the write back in `runtime/`, the tier
   that may never hold credentials.

Source walks, the same shape as `test_proposal_created.py`: the suite fails in
the commit that introduces the violation, not at the resume that exposes it.
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "central_command"


def _call_name(node: ast.Call) -> str | None:
    fn = node.func
    return fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)


def _sources():
    for path in SRC.rglob("*.py"):
        yield path.relative_to(SRC).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def test_no_agent_is_built_with_instructions():
    # Only `Agent(...)` calls: `Capability(instructions=...)` in runtime/skills.py
    # is a different object and a legitimate use of the word.
    offenders = sorted(
        f"{rel}:{node.lineno}"
        for rel, tree in _sources()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _call_name(node) == "Agent"
        and any(kw.arg == "instructions" for kw in node.keywords)
    )
    assert offenders == [], (
        "these Agent(...) calls pass instructions=, which is re-applied from the "
        "live agent on every run instead of persisted with the session — a paused "
        f"session would resume under a charter it never proposed under: {offenders}"
    )


def test_the_walk_sees_the_agents_it_is_guarding():
    """A walk that matched nothing would pass forever; prove it finds the real
    construction sites and that they carry the persisted prompt."""
    with_prompt = [
        rel
        for rel, tree in _sources()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _call_name(node) == "Agent"
        and any(kw.arg == "system_prompt" for kw in node.keywords)
    ]
    assert "runtime/agent.py" in with_prompt


def test_nothing_uses_pydantic_ais_approval_required():
    offenders = sorted(
        f"{rel}:{node.lineno}"
        for rel, tree in _sources()
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "approval_required")
        or (isinstance(node, ast.Attribute) and node.attr == "approval_required")
        or (isinstance(node, ast.alias) and node.name.split(".")[-1] == "approval_required")
    )
    assert offenders == [], (
        "approval_required() runs the approved tool in the runtime process; the "
        "gate here is CallDeferred + propose_* + the Executor, so the runtime "
        f"cannot perform the write even if approved by mistake: {offenders}"
    )
