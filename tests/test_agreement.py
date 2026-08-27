"""Agreement-report tests (D8 graduation evidence).

`pair_verdicts` is pure over event rows, so the quadrant logic tests run
without a database; one test drives `agreement_report` end-to-end over the
real log. Assertions are on rows these tests created, never on totals — the
log is shared and append-only.
"""

from __future__ import annotations

import uuid

from central_command import events
from central_command.gateway.auditor import agreement_report, pair_verdicts
from tests.conftest import needs_pg


def _row(i, kind, ref, actor="operator", payload=None):
    return {"id": i, "kind": kind, "ref_id": ref, "actor": actor,
            "payload": payload or {}}


def _verdict(i, ref, verdict, mode="shadow"):
    return _row(i, "audit.verdict", ref, actor="auditor",
                payload={"verdict": verdict, "rationale": f"r{i}", "mode": mode})


def test_all_four_quadrants_tally():
    rows = [
        _verdict(1, "a", "concur"),
        _row(2, "work.dismissal_confirmed", "a"),
        _verdict(3, "b", "challenge"),
        _row(4, "work.reopened", "b"),
        _verdict(5, "c", "challenge"),
        _row(6, "work.dismissal_confirmed", "c"),
        _verdict(7, "d", "concur"),
        _row(8, "work.reopened", "d"),
    ]
    r = pair_verdicts(rows)
    assert r["quadrants"] == {"concur_confirmed": 1, "challenge_reopened": 1,
                             "challenge_confirmed": 1, "concur_reopened": 1}
    assert r["agreed"] == 2 and r["total_paired"] == 4
    assert r["agreement_rate"] == 0.5
    assert [p["item_id"] for p in r["auditor_misses"]] == ["d"]
    assert [p["item_id"] for p in r["over_cautious"]] == ["c"]


def test_bulk_confirm_all_pairs_every_listed_item():
    rows = [
        _verdict(1, "a", "concur"),
        _verdict(2, "b", "concur"),
        _row(3, "work.dismissal_confirmed", None,
             payload={"count": 2, "item_ids": ["a", "b"]}),
    ]
    r = pair_verdicts(rows)
    assert r["quadrants"]["concur_confirmed"] == 2
    assert r["awaiting_outcome"] == 0


def test_auto_confirms_carry_no_operator_signal():
    rows = [
        _verdict(1, "a", "concur", mode="active"),
        _row(2, "work.dismissal_confirmed", "a", actor="auditor",
             payload={"by": "auditor"}),
    ]
    r = pair_verdicts(rows)
    assert r["total_paired"] == 0
    assert r["auto_confirmed"] == 1
    assert r["awaiting_outcome"] == 0


def test_a_reopen_cycle_contributes_one_pair_per_round():
    rows = [
        _verdict(1, "a", "concur"),
        _row(2, "work.reopened", "a"),          # round 1: auditor miss
        _verdict(3, "a", "challenge"),
        _row(4, "work.dismissal_confirmed", "a"),  # round 2: over-cautious
    ]
    r = pair_verdicts(rows)
    assert r["total_paired"] == 2
    assert r["quadrants"]["concur_reopened"] == 1
    assert r["quadrants"]["challenge_confirmed"] == 1


def test_unresolved_verdicts_count_as_awaiting():
    r = pair_verdicts([_verdict(1, "a", "concur")])
    assert r["total_paired"] == 0 and r["awaiting_outcome"] == 1
    assert r["agreement_rate"] is None


def test_outcomes_without_verdicts_are_ignored():
    rows = [
        _row(1, "work.dismissal_confirmed", "x"),
        _row(2, "work.reopened", "y", payload={"note": "n"}),
    ]
    r = pair_verdicts(rows)
    assert r["total_paired"] == 0 and r["awaiting_outcome"] == 0


# --- end to end over the real log --------------------------------------------




@needs_pg
async def test_agreement_report_reads_the_real_log():
    ref = "wi_agree_" + uuid.uuid4().hex[:8]
    await events.emit(
        "audit.verdict", ref_id=ref, actor="auditor",
        payload={"verdict": "concur", "rationale": "synthetic", "mode": "shadow"},
    )
    await events.emit("work.reopened", ref_id=ref, payload={"note": "look again"},
                      actor="operator")

    report = await agreement_report()
    ours = [p for p in report["auditor_misses"] if p["item_id"] == ref]
    assert len(ours) == 1
    assert ours[0]["subject"] is None  # no such work item — subject lookup tolerates it
