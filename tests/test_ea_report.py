"""`reports/ea_report.snapshot` — the deterministic data block.

The snapshot is what makes "the LLM narrates, never improvises the query"
mechanical: the digest's numbers are composed by the control plane and embedded
in the brief, so a model cannot arrive at them by arithmetic. These tests run
against a real Postgres and assert ONLY on rows they created — the dev database
is shared, so a global count assertion would be a coin flip.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from central_command import events
from central_command.db import repo
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
    before = datetime.now(UTC)
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
    future = datetime.now(UTC) + timedelta(hours=1)
    snap = await ea_report.snapshot(future)
    assert all(entry["count"] == 0 for entry in snap["activity"].values())
    assert all(entry["examples"] == [] for entry in snap["activity"].values())


@needs_pg
async def test_the_window_boundary_is_inclusive_of_since_and_excludes_before():
    """The boundary itself: an event emitted before `since` must not appear."""
    old = await events.emit("gap.declared", ref_id="eareport_old",
                            payload={"subject": "old"}, actor="agent:eareport")
    new = await events.emit("gap.declared", ref_id="eareport_new",
                            payload={"subject": "new"}, actor="agent:eareport")
    # The boundary is the DATABASE's clock, like the rows it is compared with:
    # a host clock a few ms behind the DB put `old` inside the window.
    boundary = datetime.fromisoformat(new["created_at"])

    rows = await ea_report.activity_since(boundary)
    ids = {r["id"] for r in rows}
    assert new["id"] in ids
    assert old["id"] not in ids


@needs_pg
async def test_the_snapshot_is_json_serialisable():
    """It is embedded in the brief as a fenced ```json block — a datetime that
    only `str()`s would land in the agent's prompt as a Python repr."""
    import json

    snap = await ea_report.snapshot(datetime.now(UTC) - timedelta(hours=1))
    text = json.dumps(snap)  # no default= — nothing exotic may survive to here
    assert "datetime.datetime" not in text


async def _asked(item_id: str) -> dict:
    """The `agent.asked` event the question tool emits, for `item_id`."""
    return await events.emit(
        "agent.asked", ref_id=item_id,
        payload={"agent_id": "eareport", "question": f"question for {item_id}?"},
        actor="agent:eareport",
    )


async def _mine_only(monkeypatch, item_ids: set[str]):
    """The snapshot's activity read, narrowed to the events this test made: the
    shared dev database may hold other `agent.asked` rows, and `still_open`
    counts the whole window."""
    async def only_mine(since):
        rows = await repo.list_events_of_kinds(["agent.asked"])
        return [r for r in rows if r["ref_id"] in item_ids]

    monkeypatch.setattr(ea_report, "activity_since", only_mine)


async def _seed_asked_items() -> dict[str, str]:
    """One item per state, each with its `agent.asked` event: open, answered,
    withdrawn, and an event whose item was never created. Returns name -> id."""
    from central_command.runtime import ea

    await ea.ensure_registered()  # a session row carries a foreign key to its agent
    tag = uuid.uuid4().hex[:8]
    ids = {name: f"item_{name}_{tag}"
           for name in ("open", "answered", "withdrawn", "missing")}
    for name in ("open", "answered"):
        session_id = f"sess_eareport_{name}_{tag}"
        await repo.create_running_session(session_id, "ea")
        await repo.create_operator_item(
            item_id=ids[name], kind="question", session_id=session_id,
            agent_id="eareport", body=f"question for {ids[name]}?")
    session_id = f"sess_eareport_withdrawn_{tag}"
    await repo.create_running_session(session_id, "ea")
    await repo.create_operator_item(
        item_id=ids["withdrawn"], kind="question", session_id=session_id,
        agent_id="eareport", body="a question whose work was cancelled")
    assert await repo.answer_operator_item(ids["answered"], "do the first one")
    await repo.withdraw_operator_items_for_session(session_id)
    for item_id in ids.values():
        await _asked(item_id)
    return ids


@needs_pg
async def test_an_asked_question_carries_its_items_current_state(monkeypatch):
    """The defect: the block listed `agent.asked` with no sign of the answer, so
    a day of answered questions read as a day of open gaps. Each example now
    carries the operator item's CURRENT status, and an event whose item cannot
    be found reads `unknown` rather than open."""
    ids = await _seed_asked_items()
    await _mine_only(monkeypatch, set(ids.values()))

    snap = await ea_report.snapshot(datetime.now(UTC) - timedelta(hours=1))

    state = {e["ref_id"]: e["state"]
             for e in snap["activity"]["agent.asked"]["examples"]}
    assert state[ids["open"]] == "open"
    assert state[ids["answered"]] == "answered"
    assert state[ids["withdrawn"]] == "withdrawn"
    assert state[ids["missing"]] == "unknown"
    # Only the kind that needs it carries a state.
    assert all("state" not in e
               for kind, entry in snap["activity"].items() if kind != "agent.asked"
               for e in entry["examples"])


@needs_pg
async def test_still_open_counts_only_open_questions_over_the_whole_window(monkeypatch):
    """Answered, withdrawn and unknown are not open. The count covers every
    asked event in the window, not just the five examples shown."""
    ids = await _seed_asked_items()
    # Six more OPEN questions push the examples past EXAMPLES_PER_KIND, so the
    # count has to reach events the examples do not show.
    extra = set()
    for n in range(ea_report.EXAMPLES_PER_KIND + 1):
        item_id = f"item_extra{n}_{ids['open'][-8:]}"
        session_id = f"sess_eareport_extra{n}_{ids['open'][-8:]}"
        await repo.create_running_session(session_id, "ea")
        await repo.create_operator_item(
            item_id=item_id, kind="question", session_id=session_id,
            agent_id="eareport", body="another open question")
        await _asked(item_id)
        extra.add(item_id)
    await _mine_only(monkeypatch, set(ids.values()) | extra)

    snap = await ea_report.snapshot(datetime.now(UTC) - timedelta(hours=1))

    entry = snap["activity"]["agent.asked"]
    assert entry["count"] == 4 + len(extra)
    assert entry["truncated"] > 0
    assert entry["still_open"] == 1 + len(extra)
    # Nothing else's entry gains the key.
    assert all("still_open" not in e
               for kind, e in snap["activity"].items() if kind != "agent.asked")


@needs_pg
async def test_an_answered_question_is_not_still_open(monkeypatch):
    ids = await _seed_asked_items()
    # Answer the open one too: nothing in the window is waiting any more.
    assert await repo.answer_operator_item(ids["open"], "yes")
    await _mine_only(monkeypatch, set(ids.values()))

    snap = await ea_report.snapshot(datetime.now(UTC) - timedelta(hours=1))

    assert snap["activity"]["agent.asked"]["still_open"] == 0


@needs_pg
async def test_read_team_activity_shows_the_same_state(monkeypatch):
    """The agent's follow-up read renders `agent.asked` from the same snapshot,
    so it must say what the brief's block says about what is still waiting."""
    from central_command.runtime import tools

    ids = await _seed_asked_items()
    await _mine_only(monkeypatch, set(ids.values()))

    text = await tools.read_team_activity(None, since_hours=1)

    assert "agent.asked: 4 (1 still open)" in text
    assert f"[{ids['open']}] (open)" in text
    assert f"[{ids['answered']}] (answered)" in text
    assert f"[{ids['missing']}] (unknown)" in text


@needs_pg
async def test_the_item_status_read_is_batched_and_invents_nothing():
    assert await repo.operator_item_statuses([]) == {}
    assert await repo.operator_item_statuses(["item_does_not_exist_x"]) == {}
