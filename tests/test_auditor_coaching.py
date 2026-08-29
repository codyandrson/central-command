"""The auditor is a team member (The operator, 2026-07-20): on the roster, its charter
governed by M11 and loaded at audit time, and coachable through the D5 loop —
where its coaching signals ARE the agreement record's disagreements (every
over-cautious challenge and every miss is the operator's recorded verdict on
the auditor's judgment).
"""

from __future__ import annotations

import pytest

from central_command import events
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import auditor, gateway
from central_command.runtime.agent import builtin_charter
from central_command.runtime.spike_model import make_auditor_model, make_coach_model
from tests.conftest import aresolve, needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "auditor_enabled", True)
    monkeypatch.setattr(settings, "auditor_mode", "shadow")


# --- pure: pairing carries the operator's outcome, signals derive from it -----


def test_pairs_carry_outcome_event_and_reopen_note():
    rows = [
        {"id": 1, "kind": "audit.verdict", "ref_id": "wi_a", "actor": "auditor",
         "payload": {"verdict": "concur", "rationale": "newsletter", "mode": "shadow"}},
        {"id": 2, "kind": "work.reopened", "ref_id": "wi_a", "actor": "operator",
         "payload": {"note": "this was actionable"}},
        {"id": 3, "kind": "audit.verdict", "ref_id": "wi_b", "actor": "auditor",
         "payload": {"verdict": "challenge", "rationale": "looks actionable", "mode": "shadow"}},
        {"id": 4, "kind": "work.dismissal_confirmed", "ref_id": "wi_b", "actor": "operator",
         "payload": {}},
    ]
    report = auditor.pair_verdicts(rows)
    miss = report["auditor_misses"][0]
    assert (miss["outcome_event"], miss["note"]) == (2, "this was actionable")
    over = report["over_cautious"][0]
    assert over["outcome_event"] == 4 and over["note"] is None


def test_signals_derive_from_the_disagreement_quadrants():
    report = {
        "over_cautious": [{"item_id": "wi_b", "verdict_event": 3,
                           "rationale": "looks actionable"}],
        "auditor_misses": [{"item_id": "wi_a", "verdict_event": 1,
                            "rationale": "newsletter", "note": "this was actionable"}],
    }
    signals = auditor.signals_from_report(report, {"wi_a": "Renewal notice", "wi_b": "Promo"})
    kinds = {s["kind"]: s for s in signals}
    assert kinds["audit-overcautious"]["event_id"] == 3
    assert "Promo" in kinds["audit-overcautious"]["feedback"]
    assert kinds["audit-miss"]["event_id"] == 1
    assert "REOPENED" in kinds["audit-miss"]["feedback"]
    assert "this was actionable" in kinds["audit-miss"]["feedback"]


def test_builtin_charter_is_per_agent():
    assert builtin_charter("inbox-triage") != builtin_charter("jira-expert")
    assert "independent auditor" in builtin_charter("auditor")
    with pytest.raises(ValueError):
        builtin_charter("nonexistent")


# --- wiring (needs Postgres) --------------------------------------------------


@needs_pg
async def test_audit_runs_on_the_governed_charter_and_registers_the_agent(monkeypatch):
    await auditor.ensure_registered()  # guidance_version FKs the agent row
    row = await repo.save_charter_version(
        "auditor", "GOVERNED AUDITOR CHARTER vTEST", "test marker"
    )
    seen = {}
    real = auditor.build_auditor

    def spy(model, charter=None):
        seen["charter"] = charter
        return real(model, charter)

    monkeypatch.setattr(auditor, "build_auditor", spy)
    record = await auditor.audit_dismissal(
        "wi_gov_test", "subject", "email text", "no action", model=make_auditor_model()
    )
    assert record is not None
    assert seen["charter"] == "GOVERNED AUDITOR CHARTER vTEST"
    assert any(a["id"] == "auditor" for a in await repo.list_agents())
    # cleanup: supersede is append-only; leave history (test DB), no assertion needed
    assert row["version"] >= 1


@needs_pg
async def test_coach_loop_for_the_auditor_closes_end_to_end(monkeypatch):
    """Seed a disagreement on the log, coach the auditor over it, approve the
    diff — a pedigreed governed version lands, same gate as everything else."""
    from central_command.api import routes
    from central_command.runtime import agent as agent_mod

    # Stage-5a fix: `coach_agent` no longer resolves a model itself (that
    # billed and modelled the run as the coaching TARGET instead of the
    # coach — see routes.py). The model is now resolved inside
    # `build_coach_agent` -> `build_hired_agent`, which calls
    # `resolve_model` via the `agent` module's own imported reference, so
    # that is the seam to patch.
    monkeypatch.setattr(agent_mod, "resolve_model", aresolve(lambda *a, **k: make_coach_model()))
    # live executor so charter.update actually commits — the only action this
    # test executes is the DB-backed capability (same as test_coach_loop).
    monkeypatch.setattr(settings, "executor_mode", "live")
    # Pin the setting, not the ambient .env — CC_OPERATOR_NAME varies per
    # deployment (2026-08-21 Windows validation, W6).
    monkeypatch.setattr(settings, "operator_name", "lee")

    v = await events.emit(
        "audit.verdict", ref_id="wi_coach_test",
        payload={"verdict": "challenge", "rationale": "seems actionable", "mode": "shadow"},
        actor="auditor",
    )
    await events.emit(
        "work.dismissal_confirmed", ref_id="wi_coach_test", payload={}, actor="operator"
    )

    signals = (await routes.coaching_signals("auditor"))["signals"]
    assert any(s["event_id"] == v["id"] for s in signals)

    before = await repo.current_charter("auditor")
    # `background=False` runs the coach inline and returns the run's own
    # result. The wire always backgrounds it behind a task record (a coach run
    # takes minutes); this test is about the coaching LOOP, not the launch.
    out = await routes.coach_agent(
        "auditor", routes.CoachIn(signal_ids=[v["id"]], note=None),
        background=False,
    )
    assert out["deferred"] is True

    result = await gateway.approve_and_execute(
        out["session_id"], out["proposal_id"], model=make_coach_model()
    )
    assert result["ok"] is True
    after = await repo.current_charter("auditor")
    assert after["version"] > (before["version"] if before else 0)
    # Stage 5a: the drafter is the rostered coach, not the coaching target.
    assert after["created_by"] == "agent:coach for auditor (approved human:lee)"
