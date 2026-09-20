"""Graph verification tests (2026-08-19 spec).

The properties: a missing episode is a loud operator finding, never a judgment
question; mechanical failures skip the judgment agent entirely; shadow mode
parks EVERY audited row with the verdict attached; active mode auto-closes
ONLY aligned + mechanically-clean + addition-only; a delta that invalidates
existing facts always parks (its class graduates separately); the verdict hits
the log before the close it authorises; a judgment error degrades to the human
gate; and one bad row never starves the sweep.

Graph reads are faked (the delta shapes are pinned by the live probe in
test_graph_delta_live.py); the worklist rows, events and charter fallback run
against the real test database.
"""

from __future__ import annotations

import uuid as _uuid

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.gateway import graph_auditor
from central_command.heartbeat.actions import ACTIONS
from central_command.integrations import neo4j_reader
from tests.conftest import needs_pg

pytestmark = needs_pg

# Captured at import time, BEFORE the base fixture stubs it on the module —
# the chain test exercises the real derivation.
_REAL_APPROVED_BODY = graph_auditor._approved_episode_body

EPISODE = {"uuid": "ep-1", "name": "probe", "created_at": None,
           "group_id": "central_command", "source_description": "x"}


def _delta(*, edges=True, embedded=True, invalidated=False, fact="Ada leads the probe team"):
    entities = [{"uuid": "n1", "name": "Ada", "labels": ["Person"],
                 "summary": "", "has_embedding": embedded}]
    return {
        "entities": entities,
        "edges": ([{"uuid": "e1", "name": "LEADS", "fact": fact,
                    "source": "Ada", "target": "probe team",
                    "valid_at": None, "invalid_at": None, "expired_at": None}]
                  if edges else []),
        "invalidated": ([{"uuid": "e0", "name": "LEADS", "fact": "Bo leads the probe team",
                          "source": "Bo", "target": "probe team", "valid_at": None,
                          "invalid_at": None, "expired_at": "2026-08-19T00:00:00Z",
                          "created_at": "2026-08-01T00:00:00Z",
                          "attributed_by": "window"}]
                        if invalidated else []),
    }


@pytest.fixture(autouse=True)
async def base(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "graph_auditor_enabled", True)
    monkeypatch.setattr(settings, "graph_auditor_mode", "shadow")

    async def fake_body(row):
        return "Ada leads the probe team."

    monkeypatch.setattr(graph_auditor, "_approved_episode_body", fake_body)
    yield


def _graph(monkeypatch, episode=EPISODE, delta=None):
    async def by_marker(marker, group_id):
        return episode

    async def get_delta(uuid, window_minutes=30):
        return delta if delta is not None else _delta()

    monkeypatch.setattr(neo4j_reader, "episode_by_marker", by_marker)
    monkeypatch.setattr(neo4j_reader, "episode_delta", get_delta)


def _fresh_group() -> str:
    """The absence deadline counts PENDING rows queued ahead in the GROUP, so
    a test that wants 'past the deadline' needs a group no earlier test left
    a PENDING row in."""
    return f"gv-{_uuid.uuid4().hex[:8]}"


async def _row(**kwargs):
    defaults = dict(proposal_id="p-test", episode_name="probe",
                    group_id="central_command", scope="shared", marker="proposal=p-test")
    defaults.update(kwargs)
    return await repo.create_graph_verification(**defaults)


async def _events_for(ref_id):
    rows = await repo.list_events_of_kinds(
        ["graph.audit.verdict", "graph.verification.parked",
         "graph.verification.confirmed", "graph.verification.resubmitted"])
    return [r for r in rows if r.get("ref_id") == ref_id]


async def test_absent_episode_waits_inside_the_ingestion_deadline(monkeypatch):
    """Ingestion is a serial queue — a batch of approvals lands an hour after
    execute (measured 2026-08-20, nine healthy episodes false-parked as
    missing). Inside the deadline, absence means WAIT, not park."""
    _graph(monkeypatch, episode=None)
    row = await _row()
    outcome = await graph_auditor.verify_one(row)  # default 360-minute deadline
    assert outcome == "waiting"
    landed = await repo.get_graph_verification(row["id"])
    assert landed["status"] == "PENDING"
    assert await _events_for(row["id"]) == []


def _approved(monkeypatch, args=None, approver="human:doe"):
    """Stub the proposal record behind `_approved_episode` and capture what
    the sweep re-sends to Graphiti."""
    from central_command.integrations import graphiti

    sent = []

    async def fake_add_episode(name, episode_body, source_description, group_id=None, reference_time=None):
        sent.append(dict(name=name, episode_body=episode_body,
                         source_description=source_description, group_id=group_id))
        return "queued"

    async def fake_approved(row):
        return None if args is None else (args, approver)

    monkeypatch.setattr(graphiti, "add_episode", fake_add_episode)
    monkeypatch.setattr(graph_auditor, "_approved_episode", fake_approved)
    return sent


ARGS = {"name": "probe", "episode_body": "Ada leads the probe team.",
        "source_description": "operator statement",
        "reference_time": "2026-01-01T00:00:00Z"}


async def test_absent_episode_past_the_deadline_is_resubmitted_once(monkeypatch):
    """Graphiti's ingestion queue is in memory: the nightly Neo4j dump scales
    it to zero and every acked-but-unextracted episode is gone (2026-09-15,
    68 in one night). The first absence past the deadline RE-SENDS the approved
    episode byte-identical to the Executor's send — same marker, so the same
    lookup finds it — and restarts the clock. The row stays PENDING."""
    _graph(monkeypatch, episode=None)
    sent = _approved(monkeypatch, ARGS)
    group = _fresh_group()
    row = await _row(group_id=group)
    assert await graph_auditor.verify_one(row, missing_after_minutes=0) == "resubmitted"
    assert sent == [dict(
        name="probe", episode_body="Ada leads the probe team.",
        source_description="operator statement | trust=human-approved"
                           " | approver=human:doe | proposal=p-test",
        group_id=group)]
    landed = await repo.get_graph_verification(row["id"])
    assert landed["status"] == "PENDING"
    assert landed["resubmitted_at"] is not None
    assert [e["kind"] for e in await _events_for(row["id"])] == ["graph.verification.resubmitted"]
    # The deadline now runs from the re-submission, not the original row.
    assert await graph_auditor.verify_one(landed) == "waiting"
    assert len(sent) == 1


async def test_missing_episode_parks_loud_without_judgment(monkeypatch):
    """A SECOND absence is the finding — no third send, ever."""
    _graph(monkeypatch, episode=None)
    sent = _approved(monkeypatch, ARGS)
    row = await _row(group_id=_fresh_group())
    assert await graph_auditor.verify_one(row, missing_after_minutes=0) == "resubmitted"
    row = await repo.get_graph_verification(row["id"])
    outcome = await graph_auditor.verify_one(row, missing_after_minutes=0)
    assert outcome == "missing"
    assert len(sent) == 1
    landed = await repo.get_graph_verification(row["id"])
    assert landed["status"] == "AWAITING_OPERATOR"
    assert landed["mechanical"] == {"missing": True, "resubmitted": True}
    assert landed["verdict"] is None
    kinds = [e["kind"] for e in await _events_for(row["id"])]
    assert kinds == ["graph.verification.resubmitted", "graph.verification.parked"]


async def test_nothing_to_resend_parks_missing_at_once(monkeypatch):
    """An unattributed row has no approved text — the sweep cannot invent an
    episode, so absence parks on the first miss, marked as never re-sent."""
    _graph(monkeypatch, episode=None)
    sent = _approved(monkeypatch, None)
    row = await _row(group_id=_fresh_group())
    assert await graph_auditor.verify_one(row, missing_after_minutes=0) == "missing"
    assert sent == []
    landed = await repo.get_graph_verification(row["id"])
    assert landed["mechanical"] == {"missing": True, "resubmitted": False}


async def test_absence_deadline_stretches_with_the_queue_ahead(monkeypatch):
    """2026-09-15: a fixed 360-minute deadline re-sent 13 episodes that were
    merely QUEUED behind a 72-episode replay, and every one landed twice.
    The MCP server exposes no queue depth, so the PENDING rows queued ahead
    of a row in its group, at the measured drain rate, stretch the deadline;
    the floor still applies to the row at the head of the queue."""
    _graph(monkeypatch, episode=None)
    sent = _approved(monkeypatch, ARGS)
    group = _fresh_group()
    head = await _row(group_id=group)
    behind = await _row(group_id=group, proposal_id="p-behind", marker="proposal=p-behind")
    assert await graph_auditor.absence_deadline_minutes(head, 0) == 0
    assert await graph_auditor.absence_deadline_minutes(behind, 0) == (
        graph_auditor.INGEST_MINUTES_PER_EPISODE)
    # The row behind waits even with the floor at zero; the head does not.
    assert await graph_auditor.verify_one(behind, missing_after_minutes=0) == "waiting"
    assert sent == []
    assert await graph_auditor.verify_one(head, missing_after_minutes=0) == "resubmitted"
    assert len(sent) == 1


def test_render_delta_explains_window_attribution():
    """The judge and the operator read the same rendering. A window-only
    entry is timing, not a recorded link — 56 of 90 entries on 2026-09-15
    were a neighbouring episode's retirement — so the rendering says so,
    and says when the retired fact was first known."""
    delta = _delta(invalidated=True)
    delta["invalidated"][0]["created_at"] = "2026-09-01T00:00:00+00:00"
    text = graph_auditor.render_delta(delta)
    assert "known since 2026-09-01" in text
    assert "attributed_by=window" in text
    assert "may be a neighbouring episode's retirement" in text
    delta["invalidated"][0]["attributed_by"] = "episodes"
    assert "neighbouring" not in graph_auditor.render_delta(delta)



async def test_shadow_mode_parks_even_an_aligned_clean_row(monkeypatch):
    _graph(monkeypatch)
    row = await _row()
    outcome = await graph_auditor.verify_one(row)
    assert outcome == "awaiting"
    landed = await repo.get_graph_verification(row["id"])
    assert landed["status"] == "AWAITING_OPERATOR"
    assert landed["verdict"] == "aligned"
    assert landed["episode_uuid"] == "ep-1"
    assert landed["delta"]["edges"]
    # The verdict is on the log BEFORE the parking record (M8 ordering).
    kinds = [e["kind"] for e in await _events_for(row["id"])]
    assert kinds == ["graph.audit.verdict", "graph.verification.parked"]


async def test_active_mode_auto_closes_only_the_clean_aligned_addition(monkeypatch):
    monkeypatch.setattr(settings, "graph_auditor_mode", "active")
    _graph(monkeypatch)
    row = await _row()
    outcome = await graph_auditor.verify_one(row)
    assert outcome == "auto_verified"
    landed = await repo.get_graph_verification(row["id"])
    assert landed["status"] == "VERIFIED"
    assert landed["closed_by"] == "graph-auditor"
    assert landed["closed_at"] is not None
    kinds = [e["kind"] for e in await _events_for(row["id"])]
    assert kinds == ["graph.audit.verdict", "graph.verification.confirmed"]


async def test_invalidations_always_park_and_carry_their_own_class(monkeypatch):
    monkeypatch.setattr(settings, "graph_auditor_mode", "active")
    _graph(monkeypatch, delta=_delta(invalidated=True))
    row = await _row()
    outcome = await graph_auditor.verify_one(row)
    assert outcome == "awaiting"
    landed = await repo.get_graph_verification(row["id"])
    assert landed["status"] == "AWAITING_OPERATOR"
    assert landed["has_invalidations"] is True
    events = await _events_for(row["id"])
    verdict = next(e for e in events if e["kind"] == "graph.audit.verdict")
    assert verdict["payload"]["action_class"] == "graph.verify_invalidation"


async def test_active_mode_flag_parks(monkeypatch):
    monkeypatch.setattr(settings, "graph_auditor_mode", "active")
    # The demo graph auditor flags on "unrelated" in the rendered delta.
    _graph(monkeypatch, delta=_delta(fact="Zed painted an unrelated fence"))
    row = await _row()
    outcome = await graph_auditor.verify_one(row)
    assert outcome == "awaiting"
    landed = await repo.get_graph_verification(row["id"])
    assert landed["status"] == "AWAITING_OPERATOR"
    assert landed["verdict"] == "flag"


async def test_mechanical_failure_skips_the_judgment_agent(monkeypatch):
    called = {"judged": False}

    async def boom(row, body, delta, model=None):
        called["judged"] = True

    monkeypatch.setattr(graph_auditor, "_judge", boom)
    _graph(monkeypatch, delta=_delta(embedded=False))
    row = await _row()
    outcome = await graph_auditor.verify_one(row)
    assert outcome == "awaiting"
    assert called["judged"] is False
    landed = await repo.get_graph_verification(row["id"])
    assert landed["mechanical"]["unembedded"] == ["Ada"]
    assert landed["verdict"] is None


async def test_disabled_auditor_still_runs_mechanicals_and_parks(monkeypatch):
    monkeypatch.setattr(settings, "graph_auditor_enabled", False)
    _graph(monkeypatch)
    row = await _row()
    outcome = await graph_auditor.verify_one(row)
    assert outcome == "awaiting"
    landed = await repo.get_graph_verification(row["id"])
    assert landed["status"] == "AWAITING_OPERATOR"
    assert landed["verdict"] is None
    assert landed["mechanical"]["missing"] is False


async def test_judgment_error_degrades_to_the_human_gate(monkeypatch):
    async def broken(row, body, delta, model=None):
        return None  # what _judge returns after emitting graph.audit.error

    monkeypatch.setattr(graph_auditor, "_judge", broken)
    monkeypatch.setattr(settings, "graph_auditor_mode", "active")
    _graph(monkeypatch)
    row = await _row()
    outcome = await graph_auditor.verify_one(row)
    assert outcome == "awaiting"
    landed = await repo.get_graph_verification(row["id"])
    assert landed["status"] == "AWAITING_OPERATOR"
    assert landed["verdict"] is None


async def test_one_bad_row_never_starves_the_sweep(monkeypatch):
    _graph(monkeypatch)
    bad = await _row(marker="proposal=bad")
    good = await _row(marker="proposal=good")

    real = graph_auditor.verify_one

    async def flaky(row, model=None, **kwargs):
        if row["id"] == bad["id"]:
            raise RuntimeError("bolt hiccup")
        return await real(row, model=model, **kwargs)

    monkeypatch.setattr(graph_auditor, "verify_one", flaky)

    async def due(settle_minutes, limit=20):
        return [bad, good]

    monkeypatch.setattr(repo, "due_graph_verifications", due)
    summary = await graph_auditor.verify_sweep(settle_minutes=0)
    assert summary["checked"] == 2
    assert summary["awaiting"] == 1
    assert summary["errors"] and summary["errors"][0]["id"] == bad["id"]
    # The failed row stays PENDING for the next tick.
    assert (await repo.get_graph_verification(bad["id"]))["status"] == "PENDING"


async def test_finish_only_transitions_pending_rows(monkeypatch):
    _graph(monkeypatch)
    row = await _row()
    await graph_auditor.verify_one(row)
    # A second landing attempt on the same (now AWAITING_OPERATOR) row is a no-op.
    assert not await repo.finish_graph_verification(row["id"], status="VERIFIED",
                                                    closed_by="graph-auditor")


def test_sweep_action_is_registered_and_quiet_when_idle():
    spec = ACTIONS["graph.verify_sweep"]
    assert spec.material is not None
    assert not spec.material({"checked": 0, "auto_verified": 0, "awaiting": 0,
                              "missing": 0, "resubmitted": 0, "errors": []})
    assert spec.material({"checked": 1, "resubmitted": 1})
    assert spec.material({"checked": 1, "awaiting": 1})
    assert spec.material({"checked": 1, "errors": [{"id": "x"}]})


# --- Task 5: operator decisions + the agreement record --------------------------


def _ev(i, kind, ref, actor="graph-auditor", **payload):
    return {"id": i, "kind": kind, "ref_id": ref, "actor": actor, "payload": payload}


def test_pairing_quadrants_and_the_dangerous_one():
    rows = [
        _ev(1, "graph.audit.verdict", "v1", verdict="aligned",
            rationale="faithful", mode="shadow", action_class="graph.verify"),
        _ev(2, "graph.verification.confirmed", "v1", actor="operator"),
        _ev(3, "graph.audit.verdict", "v2", verdict="aligned",
            rationale="looks fine", mode="shadow", action_class="graph.verify"),
        _ev(4, "graph.verification.problem", "v2", actor="operator",
            note="edge points the wrong way"),
        _ev(5, "graph.audit.verdict", "v3", verdict="flag",
            rationale="unsure", mode="shadow", action_class="graph.verify"),
        _ev(6, "graph.verification.confirmed", "v3", actor="operator"),
        # A different class never mixes into this report.
        _ev(7, "graph.audit.verdict", "v4", verdict="aligned",
            rationale="x", mode="shadow", action_class="graph.verify_invalidation"),
        # An auto-close carries no operator signal.
        _ev(8, "graph.audit.verdict", "v5", verdict="aligned",
            rationale="y", mode="active", action_class="graph.verify"),
        _ev(9, "graph.verification.confirmed", "v5", actor="graph-auditor"),
    ]
    report = graph_auditor.pair_graph_verdicts(rows, "graph.verify")
    assert report["quadrants"] == {"aligned_confirmed": 1, "flag_problem": 0,
                                   "flag_confirmed": 1, "aligned_problem": 1}
    assert report["total_paired"] == 3
    assert report["agreed"] == 1
    assert report["auto_confirmed"] == 1
    assert report["awaiting_outcome"] == 0
    (miss,) = report["auditor_misses"]
    assert miss["verification_id"] == "v2"
    assert miss["note"] == "edge points the wrong way"
    inval = graph_auditor.pair_graph_verdicts(rows, "graph.verify_invalidation")
    assert inval["awaiting_outcome"] == 1 and inval["total_paired"] == 0


def test_signals_cite_the_verdict_event():
    report = graph_auditor.pair_graph_verdicts([
        _ev(1, "graph.audit.verdict", "v1", verdict="aligned",
            rationale="faithful", mode="shadow", action_class="graph.verify"),
        _ev(2, "graph.verification.problem", "v1", actor="operator", note="wrong"),
    ], "graph.verify")
    (signal,) = graph_auditor.signals_from_graph_report(report, names={"v1": "probe"})
    assert signal["kind"] == "graph-audit-miss"
    assert signal["event_id"] == 1
    assert "probe" in signal["feedback"] and "wrong" in signal["feedback"]


async def test_operator_confirm_and_problem_routes(monkeypatch):
    from central_command.api import routes

    _graph(monkeypatch)
    row = await _row()
    await graph_auditor.verify_one(row)  # shadow → AWAITING_OPERATOR

    assert (await routes.confirm_graph_verification(row["id"]))["ok"]
    landed = await repo.get_graph_verification(row["id"])
    assert landed["status"] == "VERIFIED" and landed["closed_by"] == "operator"
    # Decided rows are guarded — a second decision 409s.
    import fastapi

    with pytest.raises(fastapi.HTTPException):
        await routes.problem_graph_verification(
            row["id"], routes.GraphProblemIn(note="too late", remediate=False))

    row2 = await _row(marker="proposal=p-2")
    await graph_auditor.verify_one(row2)
    assert (await routes.problem_graph_verification(
        row2["id"], routes.GraphProblemIn(note="edge inverted", remediate=False)))["ok"]
    landed2 = await repo.get_graph_verification(row2["id"])
    assert landed2["status"] == "PROBLEM" and landed2["problem_note"] == "edge inverted"

    # The operator outcomes pair against the verdicts in the fresh report.
    report = (await graph_auditor.agreement_report())["classes"]["graph.verify"]
    paired_ids = {p["verification_id"] for p in report["pairs"]}
    assert {row["id"], row2["id"]} <= paired_ids
    assert any(p["verification_id"] == row2["id"] for p in report["auditor_misses"])


async def test_verifications_wire_shape_for_the_cockpit(monkeypatch):
    """The cockpit hand-declares its interface over an untyped payload — a
    field missing HERE is invisible to tsc and to every frontend test."""
    from central_command.api import routes

    _graph(monkeypatch, delta=_delta(invalidated=True))
    row = await _row()
    await graph_auditor.verify_one(row)

    out = await routes.graph_verifications()
    assert set(out) == {"enabled", "mode", "awaiting", "recent_closed"}
    mine = next(r for r in out["awaiting"] if r["id"] == row["id"])
    for key in ("id", "proposal_id", "episode_name", "group_id", "scope",
                "status", "episode_uuid", "mechanical", "delta", "verdict",
                "verdict_rationale", "has_invalidations", "problem_note",
                "created_at", "closed_at", "closed_by", "approved_text"):
        assert key in mine, f"cockpit field {key} missing from the wire"
    # source-material-inspectable: the claim sits beside the delta it produced.
    assert mine["approved_text"] == "Ada leads the probe team."
    assert mine["delta"]["invalidated"][0]["fact"]
    # `created_at` is what lets the cockpit say "known since": the reader
    # only reports facts that PRE-DATE the episode (2026-09-15).
    assert "created_at" in mine["delta"]["invalidated"][0]
    assert mine["has_invalidations"] is True


# --- the remediation loop (2026-08-20) -------------------------------------------


async def test_problem_with_remediate_tasks_the_curator(monkeypatch):
    from central_command.api import routes
    from central_command.runtime import hiring

    _graph(monkeypatch)
    row = await _row(marker="proposal=p-remed")
    await graph_auditor.verify_one(row)

    hired = {}
    tasked = {}

    async def fake_hire(**kwargs):
        hired.update(kwargs)

    async def fake_task(instructions, title, agent_id, actor="operator", **kwargs):
        tasked.update(instructions=instructions, title=title, agent_id=agent_id)
        return {"task_id": "task-1"}

    monkeypatch.setattr(hiring, "ensure_hired", fake_hire)
    monkeypatch.setattr(routes, "create_and_run_task", fake_task)

    out = await routes.problem_graph_verification(
        row["id"], routes.GraphProblemIn(note="the edge points the wrong way"))
    assert out == {"ok": True, "task_id": "task-1"}
    assert hired["agent_id"] == "graph-curator"
    assert tasked["agent_id"] == "graph-curator"
    # The brief carries the record: verification id, approved text, uuids, note.
    assert row["id"] in tasked["instructions"]
    assert "Ada leads the probe team." in tasked["instructions"]
    assert "uuid e1" in tasked["instructions"]
    assert "the edge points the wrong way" in tasked["instructions"]


async def test_curation_execution_queues_one_recheck_per_proposal(monkeypatch):
    from central_command.gateway import executor
    from central_command.integrations import neo4j_writer

    _graph(monkeypatch)
    original = await _row(marker="proposal=p-orig")
    await graph_auditor.verify_one(original)  # parks AWAITING_OPERATOR

    applied = []

    async def fake_delete_edge(uuid):
        applied.append(uuid)
        return {"uuid": uuid}

    monkeypatch.setattr(neo4j_writer, "delete_edge", fake_delete_edge)
    token = executor._current_proposal_id.set("prop_curation_1")
    try:
        handler = executor.HANDLERS["graph.delete_edge"]
        await handler({"uuid": "e-bad-1", "verification_id": original["id"]},
                      approver="human:lee", proposer="graph-curator")
        await handler({"uuid": "e-bad-2", "verification_id": original["id"]},
                      approver="human:lee", proposer="graph-curator")
    finally:
        executor._current_proposal_id.reset(token)

    assert applied == ["e-bad-1", "e-bad-2"]
    recheck = await repo.graph_verification_for_proposal("prop_curation_1")
    assert recheck is not None
    assert recheck["remediation_of"] == original["id"]
    assert recheck["marker"] == original["marker"]  # same episode, fresh read-back
    assert recheck["status"] == "PENDING"
    # Two actions, ONE re-check row.
    rows = [r for r in await repo.list_graph_verifications()
            if r["proposal_id"] == "prop_curation_1"]
    assert len(rows) == 1


async def test_recheck_rows_inherit_the_original_approved_text(monkeypatch):
    """A re-check row's own proposal is the CURATION proposal (no episode on
    it) — the judgment must compare against the ORIGINAL approved text, found
    through the remediation_of chain."""
    _graph(monkeypatch)
    original = await _row(proposal_id="p-orig-2", marker="proposal=p-orig-2")
    recheck = await repo.create_graph_verification(
        proposal_id="p-fix-2", episode_name="probe", group_id="central_command",
        scope="shared", marker="proposal=p-orig-2", remediation_of=original["id"])

    async def fake_load(proposal_id):
        if proposal_id == "p-orig-2":
            return {"actions": [{"capability": "graph.add_episode@v1",
                                 "arguments": {"episode_body": "the original claim"}}]}
        return {"actions": [{"capability": "graph.delete_edge@v1",
                             "arguments": {"uuid": "x"}}]}

    monkeypatch.setattr(repo, "load_proposal", fake_load)
    # The base fixture stubs the module attribute — use the import-time capture.
    assert await _REAL_APPROVED_BODY(recheck) == "the original claim"


async def test_create_edge_carries_the_validity_window(monkeypatch):
    """graph.create_edge exists for extraction-DROPPED facts (2026-08-20) —
    and a missed fact must land with its REAL validity window, not today's."""
    from central_command.gateway import executor
    from central_command.integrations import neo4j_writer

    _graph(monkeypatch)
    original = await _row(marker="proposal=p-create")
    created = {}

    async def fake_create_edge(source_uuid, target_uuid, name, fact,
                               valid_at=None, invalid_at=None):
        created.update(source=source_uuid, target=target_uuid, name=name,
                       fact=fact, valid_at=valid_at, invalid_at=invalid_at)
        return {"uuid": "e-new"}

    monkeypatch.setattr(neo4j_writer, "create_edge", fake_create_edge)
    token = executor._current_proposal_id.set("prop_create_1")
    try:
        await executor.HANDLERS["graph.create_edge"](
            {"source_uuid": "n1", "target_uuid": "n2", "name": "OWNED",
             "fact": "Lee owned the roadster from 2011 until 2022",
             "valid_at": "2011-06-01T00:00:00Z", "invalid_at": "2022-03-01T00:00:00Z",
             "verification_id": original["id"]},
            approver="human:lee", proposer="graph-curator")
    finally:
        executor._current_proposal_id.reset(token)

    assert created["valid_at"] == "2011-06-01T00:00:00Z"
    assert created["invalid_at"] == "2022-03-01T00:00:00Z"
    recheck = await repo.graph_verification_for_proposal("prop_create_1")
    assert recheck and recheck["remediation_of"] == original["id"]


def test_create_edge_requires_a_start_and_spells_the_unknown_one():
    """The operator's-hand half of the reference-time law (2026-09-19): a
    created edge with no `valid_at` used to be stamped "became true now".
    The argument is required in the shared spec, and the literal `unbounded`
    is the explicit spelling for "the text gives no start"."""
    from central_command.contract import validate_action_args
    from central_command.gateway import executor

    base = {"source_uuid": "n1", "target_uuid": "n2", "name": "KNOWS", "fact": "A knows B"}
    problems = validate_action_args("graph.create_edge", base)
    assert len(problems) == 1 and "valid_at" in problems[0]
    assert validate_action_args("graph.create_edge", {**base, "valid_at": "unbounded"}) == []
    assert executor.UNBOUNDED == "unbounded"


async def test_unbounded_reaches_the_writer_as_null(monkeypatch):
    from central_command.gateway import executor
    from central_command.integrations import neo4j_writer

    _graph(monkeypatch)
    original = await _row(marker="proposal=p-unbounded")
    created = {}

    async def fake_create_edge(source_uuid, target_uuid, name, fact,
                               valid_at=None, invalid_at=None):
        created.update(valid_at=valid_at, invalid_at=invalid_at)
        return {"uuid": "e-unb"}

    monkeypatch.setattr(neo4j_writer, "create_edge", fake_create_edge)
    token = executor._current_proposal_id.set("prop_unbounded_1")
    try:
        await executor.HANDLERS["graph.create_edge"](
            {"source_uuid": "n1", "target_uuid": "n2", "name": "KNOWS",
             "fact": "A has long known B", "valid_at": "Unbounded",
             "verification_id": original["id"]},
            approver="human:lee", proposer="graph-curator")
    finally:
        executor._current_proposal_id.reset(token)
    assert created == {"valid_at": None, "invalid_at": None}


async def test_judgment_prompt_checks_every_window_against_the_text(monkeypatch):
    """The judge is asked, in so many words, to compare each fact's window
    with the dates the approved text states, and is shown the episode's
    reference time so 'dated to the reference time' is a check it can make
    (2026-09-19). Prompt text is the seam the operator's charter cannot
    remove, so it is pinned here rather than in the charter."""
    seen = {}

    class _Run:
        output = None

    class _Agent:
        async def run(self, prompt):
            seen["prompt"] = prompt
            return _Run()

    async def fake_resolve(model):
        return model

    monkeypatch.setattr(graph_auditor, "build_graph_auditor", lambda model, charter=None: _Agent())
    monkeypatch.setattr(graph_auditor, "_resolve_model", fake_resolve)
    row = await _row(marker="proposal=p-judge-window")
    delta = {"episode": {"uuid": "ep", "valid_at": "2026-05-12T09:30:00Z",
                         "created_at": "2026-09-19T00:00:00Z"},
             "entities": [], "invalidated": [],
             "edges": [{"uuid": "e1", "name": "SUBSCRIBED_TO", "fact": "Lee is subscribed",
                        "source": "Lee", "target": "List", "new": True,
                        "valid_at": "2026-05-12T09:30:00Z", "invalid_at": None}]}
    await graph_auditor._judge(row, "Lee has been subscribed since 2025-03-11.", delta)
    prompt = seen["prompt"]
    assert "Episode reference time" in prompt and "2026-05-12T09:30:00Z" in prompt
    assert "CHECK EVERY FACT'S WINDOW" in prompt
    assert "valid from 2026-05-12T09:30:00Z" in prompt
