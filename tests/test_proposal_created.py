"""Every proposal announces itself — and there is exactly one place that does it.

The gap this locks shut (found 2026-07-25): `proposal.created` was emitted at
the call sites, so the two paths added later — a coach run and an operator task
— simply forgot it. Their proposals reached the Decisions Inbox with NO creation
event at all, and a charter edit (the highest-leverage change in the system)
appeared in the M16 audit trail only at decision time.

The source walk is the real guard. A count of today's paths would pass forever
while path six quietly repeated the omission; requiring every
`repo.save_proposal(...)` to go through `runtime/proposals.py` fails the suite
the moment someone adds one, in the same commit that adds it — the same shape as
the runtime→gateway import ban and the heartbeat quiet-eligibility allowlist.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "central_command"

# The seam itself, and the persistence layer that defines the function.
ALLOWED = {"runtime/proposals.py", "db/repo.py"}


def _calls_save_proposal(path: pathlib.Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name == "save_proposal":
            return True
    return False


def test_only_the_park_path_creates_proposals():
    offenders = sorted(
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if p.relative_to(SRC).as_posix() not in ALLOWED and _calls_save_proposal(p)
    )
    assert offenders == [], (
        "these modules persist a proposal without going through "
        "runtime/proposals.park_proposal, so their proposals would reach the "
        f"inbox with no proposal.created event: {offenders}"
    )


def test_the_seam_emits_the_event_it_promises():
    """park_proposal must both persist AND announce — a version that only
    persisted would satisfy the walk above while restoring the original bug."""
    src = (SRC / "runtime" / "proposals.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "park_proposal"
    )
    calls = [
        c.func.attr for c in ast.walk(fn)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
    ]
    assert "save_proposal" in calls
    assert "emit" in calls
    assert "proposal.created" in src


def _emits(src: str, kind: str) -> bool:
    """True if this source calls `emit(...)` with `kind` as a literal argument."""
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.attr if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", ""))
        if name != "emit":
            continue
        args = list(node.args) + [k.value for k in node.keywords]
        if any(isinstance(a, ast.Constant) and a.value == kind for a in args):
            return True
    return False


def test_no_other_module_emits_proposal_created():
    """Two emitters would double-log one proposal — the failure mode when the
    dispatcher's own emit was left behind after the seam took over.

    Checked on the EMIT CALL (ast), not on the presence of the string. This was
    a text match until 2026-07-30, when `reports/ea_report.py` NAMED
    `proposal.created` in its read-only activity vocabulary and was flagged as
    an emitter. A reader that names a kind is not a writer of it, and a guard
    that cannot tell the difference eventually gets an allowlist bolted on —
    which is how a guard stops guarding. The ast form is strictly narrower in
    what it flags and strictly stronger about what it means.
    """
    offenders = sorted(
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if p.relative_to(SRC).as_posix() != "runtime/proposals.py"
        and _emits(p.read_text(encoding="utf-8"), "proposal.created")
    )
    assert offenders == [], f"proposal.created is emitted outside the seam: {offenders}"


@pytest.mark.parametrize(
    "module,path",
    [
        ("run.ingest_and_propose (email)", "runtime/run.py"),
        ("run.run_coach / run.run_task", "runtime/run.py"),
        ("converse (mid-conversation)", "runtime/converse.py"),
        ("gateway._park_redraft", "gateway/gateway.py"),
    ],
)
def test_every_known_creation_path_imports_the_seam(module, path):
    src = (SRC / path).read_text(encoding="utf-8")
    assert "park_proposal" in src, f"{module} no longer routes through the seam"
