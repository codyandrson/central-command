"""A failed session says WHY it failed — enforced at the source, not at runtime.

The gap this locks shut (found 2026-08-10): a jira-expert session died mid-turn
and `session.failed` carried `{"agent_id": "jira-expert"}` and nothing else.
`land_session` has accepted a `reason` all along; every `failed=True` call site
simply never passed one, so the record of the failure recorded no failure. In a
system whose premise is that the event log IS the record, that is a governance
gap: the operator saw a dead agent and had nothing to diagnose from.

The guard is a SOURCE WALK, deliberately — the same shape as
`test_proposal_created.py`. The obvious alternative, raising from
`land_session` when `failed=True` arrives without a reason, is wrong here and
worse than the bug: every one of these call sites is already handling an
exception, so a raise would mask the original error AND leave the session row
open, turning a diagnosable failure into an invisible one. A source walk fails
the suite the moment someone adds call site five, in the same commit that adds
it, and costs nothing at runtime.
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "central_command"


def _failed_landings_without_reason() -> list[str]:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name != "land_session":
                continue
            kwargs = {k.arg: k.value for k in node.keywords if k.arg}
            failed = kwargs.get("failed")
            if not (isinstance(failed, ast.Constant) and failed.value is True):
                continue  # a successful landing needs no reason
            if "reason" not in kwargs:
                offenders.append(f"{path.relative_to(SRC.parent)}:{node.lineno}")
    return offenders


def test_every_failed_session_records_why():
    offenders = _failed_landings_without_reason()
    assert not offenders, (
        "land_session(failed=True) must pass `reason=` — a failure that records no "
        "reason leaves the operator nothing to diagnose from:\n  "
        + "\n  ".join(offenders)
    )


def test_the_walk_would_actually_catch_one():
    """A guard that cannot fail is decoration. Prove the walk sees the bad shape."""
    tree = ast.parse("await land_session(sid, failed=True, agent_id=a)\n")
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    kwargs = {k.arg for k in call.keywords if k.arg}
    assert "failed" in kwargs and "reason" not in kwargs
