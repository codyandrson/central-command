"""`reports/ea_report.snapshot` — the deterministic data block.

The snapshot is what makes "the LLM narrates, never improvises the query"
mechanical: the digest's numbers are composed by the control plane and embedded
in the brief, so a model cannot arrive at them by arithmetic. These tests run
against a real Postgres and assert ONLY on rows they created — the dev database
is shared, so a global count assertion would be a coin flip.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

from central_command import events
from central_command.reports import ea_report
from tests.conftest import needs_pg


def test_the_reports_tier_never_reaches_the_write_tier():
    """`runtime/tools.read_team_activity` imports this package, so a gateway
    import here would smuggle the write tier into an agent's reach through a
    package `test_governance`'s runtime walk does not cover. Parsed with `ast`
    for the same reason that one is: every import spelling counts."""
    reports_dir = Path(ea_report.__file__).parent
    for py in reports_dir.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"), filename=str(py))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                names = [prefix] + [f"{prefix}.{a.name}" for a in node.names]
            for dotted in names:
                parts = dotted.split(".")
                assert not (len(parts) >= 2 and parts[0] == "central_command"
                            and parts[1] == "gateway"), (
                    f"{py.name} imports {dotted!r} — reports/ must stay "
                    "runtime-importable"
                )


def test_one_activity_vocabulary_for_both_readers():
    """The digest block and the agent's follow-up read must cover the same
    kinds, or a follow-up would legitimately disagree with the digest the
    operator is looking at. One tuple, imported by both."""
    from central_command.runtime import tools

    source = Path(tools.__file__).read_text(encoding="utf-8")
    assert "ea_report.snapshot" in source
    assert set(ea_report.ACTIVITY_KINDS) == {
        "proposal.created", "proposal.decided", "task.completed", "task.blocked",
        "task.review", "agent.asked", "gap.declared", "work.enrolled",
    }


@needs_pg
async def test_the_snapshot_windows_activity_and_reports_current_state():
    before = datetime.now(timezone.utc)
    mine = await events.emit(
        "gap.declared", ref_id="eareport_1",
        payload={"kind": "missing_tool", "subject": "eareport",
                 "need": "a widget only this test wants"},
        actor="agent:eareport",
    )

    snap = await ea_report.snapshot(before)

    # Windowed: MY row is in the window's activity.
    ids = {e["event_id"] for e in snap["activity"]["gap.declared"]["examples"]}
    assert mine["id"] in ids or snap["activity"]["gap.declared"]["truncated"] > 0
    assert snap["activity"]["gap.declared"]["count"] >= 1
    # Every fixed kind is present even at zero — a missing key would make the
    # agent's narration depend on whether anything happened.
    assert set(snap["activity"]) == set(ea_report.ACTIVITY_KINDS)

    # Current state, not windowed: these are depths, and they are integers.
    for name in ("proposals_awaiting_decision", "work_items_in_flight",
                 "dismissals_awaiting_confirmation", "operator_items_open",
                 "discussions_open"):
        assert isinstance(snap["queues"][name], int)
    assert isinstance(snap["work_ledger_by_state"], dict)
    assert isinstance(snap["tasks_by_status"], dict)
    assert snap["window"]["since"] and snap["window"]["until"]


@needs_pg
async def test_an_empty_window_is_a_complete_answer():
    """A window starting in the future contains nothing, and the block says so
    with zeroes rather than by omitting keys — 'nothing happened' has to be
    narratable."""
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    snap = await ea_report.snapshot(future)
    assert all(entry["count"] == 0 for entry in snap["activity"].values())
    assert all(entry["examples"] == [] for entry in snap["activity"].values())


@needs_pg
async def test_the_window_boundary_is_inclusive_of_since_and_excludes_before():
    """The boundary itself: an event emitted before `since` must not appear."""
    old = await events.emit("gap.declared", ref_id="eareport_old",
                            payload={"subject": "old"}, actor="agent:eareport")
    boundary = datetime.now(timezone.utc)
    new = await events.emit("gap.declared", ref_id="eareport_new",
                            payload={"subject": "new"}, actor="agent:eareport")

    rows = await ea_report.activity_since(boundary)
    ids = {r["id"] for r in rows}
    assert new["id"] in ids
    assert old["id"] not in ids


@needs_pg
async def test_the_snapshot_is_json_serialisable():
    """It is embedded in the brief as a fenced ```json block — a datetime that
    only `str()`s would land in the agent's prompt as a Python repr."""
    import json

    snap = await ea_report.snapshot(datetime.now(timezone.utc) - timedelta(hours=1))
    text = json.dumps(snap)  # no default= — nothing exotic may survive to here
    assert "datetime.datetime" not in text
