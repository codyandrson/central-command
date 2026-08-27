"""Lines of effort (LOE slice 1): the goal-tracking substrate, its gated
check-in/update capabilities, and the accountability agent.

The invariant under test throughout is the three-layer split: check-ins are
append-only DATA, `semantic` is versioned MEANING (so a past green keeps the
targets it was scored against), and `presentation` is unversioned DISPLAY.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import executor
from tests.conftest import needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    # Nothing here should reach resolve_model, but the roster/hire paths run
    # real code — pin demo mode so an accidental model call cannot spend money.
    monkeypatch.setattr(settings, "demo_mode", True)


_SEMANTIC = {"questions": ["Did you run this week?"], "thresholds": "3+ = green"}


async def _make(loe_id: str, **kw) -> dict:
    return await repo.create_loe(
        loe_id, kw.get("name", "Test line"), kw.get("cadence", "weekly"),
        kw.get("agent_id", "accountability"), kw.get("semantic", _SEMANTIC),
        kw.get("presentation"),
    )


# --- the store ------------------------------------------------------------------


async def test_create_and_list_scoped_to_our_own_rows():
    row = await _make("loe-create")
    assert row["semantic_version"] == 1
    assert row["status"] == "ACTIVE"
    assert row["presentation"] == {}
    # Assert on rows this test created — the ids, never a global count.
    ids = {r["id"] for r in await repo.list_loes()}
    assert "loe-create" in ids


async def test_a_semantic_change_bumps_the_version_and_a_presentation_edit_does_not():
    await _make("loe-version")
    same = await repo.update_loe("loe-version", semantic=dict(_SEMANTIC))
    assert same["semantic_version"] == 1, "an unchanged semantic is not a new meaning"

    changed = await repo.update_loe(
        "loe-version", semantic={"questions": ["Did you run twice?"], "thresholds": "2+"}
    )
    assert changed["semantic_version"] == 2

    display = await repo.update_loe("loe-version", presentation={"colour": "blue"})
    assert display["semantic_version"] == 2, "presentation is not meaning"
    assert display["presentation"] == {"colour": "blue"}

    renamed = await repo.update_loe("loe-version", name="Renamed")
    assert renamed["name"] == "Renamed" and renamed["semantic_version"] == 2


async def test_update_of_a_missing_line_returns_none():
    assert await repo.update_loe("loe-nope", name="x") is None


async def test_a_checkin_is_stamped_with_the_version_it_was_scored_under():
    await _make("loe-stamp")
    first = await repo.record_loe_checkin("loe-stamp", "green", 80, "went fine")
    assert first["semantic_version"] == 1

    await repo.update_loe("loe-stamp", semantic={"questions": ["harder"], "thresholds": "5+"})
    second = await repo.record_loe_checkin("loe-stamp", "red", 60, "harder now")
    assert second["semantic_version"] == 2

    # The old row keeps its old meaning — recalibration never rewrites history.
    history = await repo.list_loe_checkins("loe-stamp")
    assert [c["semantic_version"] for c in history] == [2, 1], "newest first"
    assert history[1]["light"] == "green"


async def test_retire_is_a_status_flip_that_refuses_further_writes():
    await _make("loe-retire")
    await repo.record_loe_checkin("loe-retire", "green", 90, "before retirement")

    assert await repo.retire_loe("loe-retire", "no longer a goal") is True
    assert await repo.retire_loe("loe-retire", "again") is False, "already retired"

    row = await repo.get_loe("loe-retire")
    assert row["status"] == "RETIRED" and row["retired_at"] is not None
    assert row["retired_reason"] == "no longer a goal"

    with pytest.raises(ValueError):
        await repo.record_loe_checkin("loe-retire", "green", 50, "after")
    with pytest.raises(ValueError):
        await repo.update_loe("loe-retire", name="nope")

    # Nothing was deleted: the history is still readable.
    assert len(await repo.list_loe_checkins("loe-retire")) == 1
    active = {r["id"] for r in await repo.list_loes()}
    assert "loe-retire" not in active
    assert "loe-retire" in {r["id"] for r in await repo.list_loes(include_retired=True)}


async def test_a_checkin_against_a_missing_line_is_refused():
    with pytest.raises(ValueError):
        await repo.record_loe_checkin("loe-ghost", "green", 50, "x")


# --- the gated pipeline ---------------------------------------------------------


def test_the_capabilities_are_registered_and_dispatchable():
    from central_command.gateway.capabilities import REGISTRY, gated_write_names

    assert {"loe.create", "loe.record_checkin", "loe.update"} <= gated_write_names()
    assert {"loe.create", "loe.record_checkin", "loe.update"} <= set(executor.HANDLERS)
    reads = {c.name for c in REGISTRY if c.kind == "read"}
    assert "loe.list" in reads and "loe.list" not in executor.HANDLERS


async def test_the_checkin_handler_validates_light_confidence_and_target():
    await _make("loe-exec")
    handler = executor.HANDLERS["loe.record_checkin"]

    for bad in ({"loe_id": "loe-exec", "light": "amber", "confidence": 50, "summary": "s"},
                {"loe_id": "loe-exec", "light": "green", "confidence": 101, "summary": "s"},
                {"loe_id": "loe-exec", "light": "green", "confidence": "80", "summary": "s"},
                {"loe_id": "loe-exec", "light": "green", "confidence": True, "summary": "s"},
                {"loe_id": "loe-exec", "light": "green", "confidence": 50, "summary": "  "},
                {"loe_id": "loe-missing", "light": "green", "confidence": 50, "summary": "s"}):
        with pytest.raises(executor.ExecutorError):
            await handler(bad, "lee", "accountability")

    out = await handler(
        {"loe_id": "loe-exec", "light": "yellow", "confidence": 55, "summary": "mixed week"},
        "lee", "accountability",
    )
    assert "yellow" in out
    rows = await repo.list_loe_checkins("loe-exec")
    assert len(rows) == 1 and rows[0]["summary"] == "mixed week"


async def test_the_create_handler_validates_and_refuses_a_duplicate_cleanly():
    handler = executor.HANDLERS["loe.create"]
    good = {"loe_id": "loe-exec-create", "name": "New line",
            "agent_id": "accountability", "semantic": _SEMANTIC}

    for bad in ({**good, "loe_id": " "},
                {**good, "name": "  "},
                {**good, "agent_id": ""},
                {k: v for k, v in good.items() if k != "semantic"},
                {**good, "semantic": "3+ a week"},
                {**good, "semantic": {"thresholds": "3+"}},          # no questions
                {**good, "semantic": {"questions": ["x"]}}):         # no thresholds
        with pytest.raises(executor.ExecutorError):
            await handler(bad, "lee", "accountability")

    out = await handler(good, "lee", "accountability")
    assert "loe-exec-create" in out
    row = await repo.get_loe("loe-exec-create")
    assert row["cadence"] == "weekly" and row["presentation"] == {}
    assert row["agent_id"] == "accountability" and row["semantic_version"] == 1

    # A duplicate id is a clean refusal, never a traceback out of asyncpg.
    with pytest.raises(executor.ExecutorError, match="already exists"):
        await handler(good, "lee", "accountability")


async def test_a_proposed_creation_is_written_only_by_the_executor(monkeypatch):
    """The full gated path: the drafter proposes, the operator approves, and
    the row appears with the goal's OWNING agent from the arguments — which is
    not the same field as the proposal's drafter."""
    monkeypatch.setattr(settings, "executor_mode", "live")
    from central_command.contract import Action

    await executor.execute(
        [Action(capability="loe.create",
                arguments={"loe_id": "loe-gated", "name": "Gated line",
                           "agent_id": "accountability", "cadence": "monthly",
                           "semantic": _SEMANTIC},
                target_ref={"system": "central_command", "id": "loe-gated",
                            "read_version": "unknown"},
                reversibility="reversible")],
        approver="lee", source_refs=[], proposer="accountability",
        proposal_id="prop-loe-create-1",
    )
    row = await repo.get_loe("loe-gated")
    assert row["name"] == "Gated line" and row["cadence"] == "monthly"
    assert row["agent_id"] == "accountability" and row["status"] == "ACTIVE"


async def test_the_update_handler_recalibrates_and_retires():
    await _make("loe-exec-update")
    handler = executor.HANDLERS["loe.update"]

    with pytest.raises(executor.ExecutorError):
        await handler({"loe_id": "loe-exec-update"}, "lee", "accountability")
    with pytest.raises(executor.ExecutorError):
        await handler({"loe_id": "loe-exec-update", "retire": True}, "lee", "accountability")
    with pytest.raises(executor.ExecutorError):
        await handler({"loe_id": "loe-missing", "name": "x"}, "lee", "accountability")

    await handler(
        {"loe_id": "loe-exec-update", "semantic": {"questions": ["new"], "thresholds": "1+"}},
        "lee", "accountability",
    )
    assert (await repo.get_loe("loe-exec-update"))["semantic_version"] == 2

    await handler(
        {"loe_id": "loe-exec-update", "retire": True, "reason": "done"},
        "lee", "accountability",
    )
    assert (await repo.get_loe("loe-exec-update"))["status"] == "RETIRED"


async def test_execution_stamps_the_proposal_id_from_the_control_plane(monkeypatch):
    """Provenance comes from the execution context, never from the arguments —
    an agent cannot write which proposal its own check-in belongs to."""
    monkeypatch.setattr(settings, "executor_mode", "live")
    from central_command.contract import Action

    await _make("loe-prov")
    await executor.execute(
        [Action(capability="loe.record_checkin",
                arguments={"loe_id": "loe-prov", "light": "green",
                           "confidence": 70, "summary": "good week"},
                target_ref={"system": "central_command", "id": "loe-prov",
                            "read_version": "unknown"},
                reversibility="reversible")],
        approver="lee", source_refs=[], proposer="accountability",
        proposal_id="prop-loe-1",
    )
    rows = await repo.list_loe_checkins("loe-prov")
    assert rows[0]["proposal_id"] == "prop-loe-1"


# --- the API wire shape ---------------------------------------------------------


async def test_the_loe_route_carries_the_full_wire_shape_to_the_cockpit():
    """The cockpit hand-declares `Loe` over an untyped payload
    (web/src/features/loe/types.ts), so a field the backend never sends is
    invisible to `tsc` AND to every frontend test. This backend assertion is
    the only real guard — same reasoning as
    test_composer_state_carries_closed_reason_to_the_cockpit.
    """
    from central_command.api import routes

    await _make("loe-wire", presentation={"colour": "green"})
    await repo.record_loe_checkin("loe-wire", "green", 90, "first")
    await repo.record_loe_checkin("loe-wire", "yellow", 40, "second")

    body = await routes.list_loes()
    row = next(r for r in body["loes"] if r["id"] == "loe-wire")
    assert set(row) == {
        "id", "name", "cadence", "status", "agent_id", "semantic",
        "semantic_version", "presentation", "latest", "history",
        "semantic_history",
    }
    assert row["semantic_history"] == [
        {"version": 1, "semantic": _SEMANTIC, "superseded_at": None}
    ], "with no recalibration yet, history is just the current version"
    assert row["semantic"] == _SEMANTIC and row["presentation"] == {"colour": "green"}
    assert row["latest"]["light"] == "yellow", "latest is the newest check-in"
    assert set(row["latest"]) == {"light", "confidence", "summary", "created_at",
                                  "semantic_version"}
    assert [c["light"] for c in row["history"]] == ["yellow", "green"]

    # A line with no check-ins says so rather than omitting the field.
    await _make("loe-wire-empty")
    empty = next(r for r in (await routes.list_loes())["loes"]
                 if r["id"] == "loe-wire-empty")
    assert empty["latest"] is None and empty["history"] == []


async def test_a_recalibration_archives_the_outgoing_semantic_version():
    """The current semantic lives on the LOE row and update_loe overwrites it
    in place — so without the archive, the version a past check-in's stamp
    points at would have no readable content anywhere (the detail panel's
    version history exists to show it)."""
    from central_command.api import routes

    await _make("loe-archive")
    v2 = {"questions": ["Did you run twice?"], "thresholds": "2+ = green"}
    await repo.update_loe("loe-archive", semantic=v2)

    archived = await repo.list_loe_semantic_versions("loe-archive")
    assert archived == [
        {"version": 1, "semantic": _SEMANTIC,
         "superseded_at": archived[0]["superseded_at"]}
    ]
    assert archived[0]["superseded_at"] is not None

    row = next(r for r in (await routes.list_loes())["loes"]
               if r["id"] == "loe-archive")
    assert [(h["version"], h["semantic"]) for h in row["semantic_history"]] == [
        (2, v2), (1, _SEMANTIC)
    ]
    assert row["semantic_history"][0]["superseded_at"] is None


async def test_the_history_is_capped_at_twelve_newest_first():
    from central_command.api import routes

    await _make("loe-cap")
    for i in range(14):
        await repo.record_loe_checkin("loe-cap", "green", i, f"week {i}")
    row = next(r for r in (await routes.list_loes())["loes"] if r["id"] == "loe-cap")
    assert len(row["history"]) == 12
    assert row["history"][0]["summary"] == "week 13"


async def test_the_history_param_widens_or_caps_the_window():
    from central_command.api import routes

    await _make("loe-depth")
    for i in range(14):
        await repo.record_loe_checkin("loe-depth", "green", i, f"week {i}")

    wide = next(r for r in (await routes.list_loes(history=50))["loes"]
                if r["id"] == "loe-depth")
    assert len(wide["history"]) == 14, "a deeper window returns everything so far"

    capped = next(r for r in (await routes.list_loes(history=999))["loes"]
                  if r["id"] == "loe-depth")
    assert len(capped["history"]) == 14, "still just the 14 rows that exist"

    # And the sane max clamps an absurd request rather than trusting it.
    from unittest.mock import AsyncMock, patch
    with patch.object(repo, "list_loe_checkins", AsyncMock(wraps=repo.list_loe_checkins)) as spy:
        await routes.list_loes(history=999)
        assert spy.call_args.kwargs["limit"] == 200


async def test_retired_lines_are_hidden_unless_asked_for():
    from central_command.api import routes

    await _make("loe-hidden")
    await repo.retire_loe("loe-hidden", "done")
    assert not any(r["id"] == "loe-hidden" for r in (await routes.list_loes())["loes"])
    body = await routes.list_loes(include_retired=True)
    assert any(r["id"] == "loe-hidden" for r in body["loes"])


async def test_create_route_refuses_a_duplicate_id():
    from fastapi import HTTPException

    from central_command.api import routes

    body = routes.LoeIn(id="loe-post", name="Posted", agent_id="accountability",
                        semantic=_SEMANTIC)
    created = await routes.create_loe(body)
    assert created["id"] == "loe-post" and created["latest"] is None
    with pytest.raises(HTTPException) as e:
        await routes.create_loe(body)
    assert e.value.status_code == 409


# --- the accountability agent ---------------------------------------------------


async def test_the_agent_is_hired_idempotently_with_its_packs():
    from central_command.runtime import accountability

    await accountability.ensure_registered()
    first = await repo.get_agent(accountability.AGENT_ID)
    await accountability.ensure_registered()
    again = await repo.get_agent(accountability.AGENT_ID)

    assert first is not None and again is not None
    assert first["created_at"] == again["created_at"], "the second call re-hired"
    assert first["role"].strip() and first["status"] == "ACTIVE"
    assert first["taskable"] is True and first["conversational"] is True
    assert first["consultable"] is True and first["deviations"].strip()

    grants = sorted(g["pack"] for g in await repo.list_grants(accountability.AGENT_ID)
                    if g["revoked_at"] is None)
    # jira-read + calendar-read (2026-08-18): evidence at check-in time —
    # ungated reads so the agent can cite what the calendar and board show.
    assert grants == ["ask-operator", "calendar-read", "consult", "jira-read",
                      "loe-propose", "task-propose"]

    current = await repo.current_charter(accountability.AGENT_ID)
    assert current and current["content"].strip()


async def test_the_agent_is_taskable_and_not_seeded_in_the_schema():
    from pathlib import Path

    from central_command.runtime import accountability
    from central_command.runtime.roster import taskable_agent_ids

    await accountability.ensure_registered()
    assert accountability.AGENT_ID in await taskable_agent_ids()

    schema = (Path(__file__).parents[1] / "central_command" / "db" / "schema.sql").read_text(encoding="utf-8")
    assert f"'{accountability.AGENT_ID}'" not in schema
    # And no LOE rows are seeded either — lines of effort are operator data.
    assert "insert into line_of_effort" not in schema


def test_the_charter_carries_the_behaviours_the_design_depends_on():
    from central_command.runtime.accountability import CHARTER

    lowered = CHARTER.lower()
    assert "loe_list" in lowered              # read the CURRENT questions first
    assert "ask_operator" in lowered          # the check-in is a conversation
    assert "loe.record_checkin" in lowered
    assert "loe.update" in lowered
    assert "three or more" in lowered         # the recalibration trigger
    assert "never nag" in lowered and "shame" in lowered
    # Goals drive work (2026-08-18): a cited task.create is how a goal that is
    # not turning into action becomes calendar time or board work.
    assert "task.create" in lowered
    assert "consult_agent" in lowered
    assert "you originate work, you never assign it" in lowered


def test_the_pack_is_wired_end_to_end():
    from central_command.runtime import packs
    from central_command.runtime.durable import PROPOSE_TOOLS

    pack = packs.PACKS["loe-propose"]
    assert sorted(c.name for c in pack.capabilities) == [
        "loe.create", "loe.record_checkin", "loe.update"]
    assert set(packs.toolset_for(["loe-propose"]).tools) == {"propose_loe", "loe_list"}
    # A deferral tool the driver cannot classify parks nothing.
    assert "propose_loe" in PROPOSE_TOOLS


def test_the_goal_preview_doctrine_rides_every_charter():
    """Presentation doctrine, like CHART_MARKERS: nothing injects the marker
    syntax at runtime, so an agent never told it exists can never use it."""
    from central_command.runtime.agent import GOAL_PREVIEW_MARKERS, build_charter

    assert "[goal-preview:{" in GOAL_PREVIEW_MARKERS
    assert GOAL_PREVIEW_MARKERS in build_charter()


def test_goal_awareness_is_spread_to_the_team():
    """Goals drive work (2026-08-18): loe-read is the shared-context half —
    read-only, no gated capabilities, and granted to the agents whose judgment
    goals should inform (EA via its template; the two founders via
    DEFAULT_PACKS mirroring the schema seeds). Toolset assembly dedupes by
    name, so loe_list living in both loe packs cannot collide."""
    from pathlib import Path

    from central_command.runtime import packs
    from central_command.runtime.templates import EA_TEMPLATE

    pack = packs.PACKS["loe-read"]
    assert pack.tool_names == ("loe_list",)
    assert not pack.capabilities, "loe-read must stay read-only"
    assert set(packs.toolset_for(["loe-read"]).tools) == {"loe_list"}
    assert set(packs.toolset_for(["loe-read", "loe-propose"]).tools) == {
        "loe_list", "propose_loe"}

    assert "loe-read" in EA_TEMPLATE.default_packs
    for founder in ("jira-expert", "orchestrator"):
        assert "loe-read" in packs.DEFAULT_PACKS[founder]

    schema = (Path(__file__).parents[1] / "central_command" / "db" / "schema.sql").read_text(encoding="utf-8")
    assert "('jira-expert',  'loe-read', 'seed:loe-read')" in schema
    assert "('orchestrator', 'loe-read', 'seed:loe-read')" in schema


def test_the_agent_delegates_the_hire_rather_than_keeping_a_second_copy():
    from pathlib import Path

    from central_command.runtime import accountability

    source = Path(accountability.__file__).read_text(encoding="utf-8")
    assert "ensure_hired" in source
    assert "repo.hire_agent" not in source
