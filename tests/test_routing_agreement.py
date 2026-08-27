"""Routing agreement tests (D7 graduation record).

The properties, mirroring the auditor's pair_verdicts: every recommendation
pairs with the operator's NEXT outcome for that task; accepted vs overridden
is the auto-assign graduation evidence; overrides become coaching signals
citing the assignment event; assignments with no standing recommendation and
cancellations are tallied separately, never as agreement data. Pure functions
— testable without a database (the report endpoint just feeds them the log).
"""

from __future__ import annotations

from central_command.runtime.orchestrator import pair_routes, signals_from_routes


def _rec(id, task, agent, rationale="fits the role"):
    return {"id": id, "kind": "task.recommended", "ref_id": task,
            "payload": {"agent_id": agent, "rationale": rationale}}


def _assign(id, task, agent):
    return {"id": id, "kind": "task.assigned", "ref_id": task,
            "payload": {"agent_id": agent}}


def _cancel(id, task):
    return {"id": id, "kind": "task.cancelled", "ref_id": task, "payload": {}}


def test_accepted_and_overridden_pair_correctly():
    rows = [
        _rec(1, "t1", "jira-expert"), _assign(2, "t1", "jira-expert"),   # accepted
        _rec(3, "t2", "jira-expert"), _assign(4, "t2", "other-agent"),   # overridden
        _rec(5, "t3", None), _assign(6, "t3", "jira-expert"),            # no-fit overridden
    ]
    r = pair_routes(rows)
    assert r["accepted"] == 1
    assert [p["task_id"] for p in r["overridden"]] == ["t2", "t3"]
    assert r["total_paired"] == 3
    assert abs(r["agreement_rate"] - 1 / 3) < 1e-9
    # The pair carries both events — the record is citable end to end.
    assert r["overridden"][0]["recommended_event"] == 3
    assert r["overridden"][0]["outcome_event"] == 4


def test_latest_recommendation_is_the_operative_one():
    rows = [
        _rec(1, "t1", None, "unsure"),          # first look: no fit
        _rec(2, "t1", "jira-expert", "re-ask"),  # operator re-routed; this stands
        _assign(3, "t1", "jira-expert"),
    ]
    r = pair_routes(rows)
    assert r["accepted"] == 1 and not r["overridden"]


def test_cancellations_and_unrouted_assignments_are_not_agreement_data():
    rows = [
        _rec(1, "t1", "jira-expert"), _cancel(2, "t1"),  # cancelled after rec
        _assign(3, "t2", "jira-expert"),                  # operator beat the router
        _rec(4, "t3", "jira-expert"),                     # still awaiting
    ]
    r = pair_routes(rows)
    assert r["total_paired"] == 0 and r["agreement_rate"] is None
    assert r["cancelled_after_recommendation"] == 1
    assert r["unrouted_assignments"] == 1
    assert r["awaiting_outcome"] == 1


def test_overrides_become_citable_coaching_signals():
    rows = [_rec(1, "t1", None, "no candidate covers this"),
            _assign(2, "t1", "jira-expert")]
    signals = signals_from_routes(pair_routes(rows), {"t1": "Water the plants"})
    assert len(signals) == 1
    s = signals[0]
    assert s["kind"] == "route-override"
    assert s["event_id"] == 2  # cites the operator's assignment event
    assert "Water the plants" in s["feedback"]
    assert "no one (no fit)" in s["feedback"]
    assert "jira-expert" in s["feedback"]
