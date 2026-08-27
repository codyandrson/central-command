"""Confidence: the contract field, the wire shape, and the agreement record.

Uncertainty triage ships in EVIDENCE mode — confidence is recorded on every
proposal and nothing is auto-approved. These tests hold the two things that
make it more than decoration: that it survives the round trip to the cockpit
(a field the gateway never sends is invisible to `tsc` AND to every frontend
test), and that it can be measured against the operator's verdicts.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ToolCallPart

from central_command.config import settings
from central_command.contract import Confidence, Proposal
from central_command.db import repo
from central_command.gateway.steward_agreement import pair_confidence
from central_command.runtime.durable import proposal_from_call
from tests.conftest import needs_pg

_CORE = {
    "intent": "Record the platform decision.",
    "actions": [{
        "capability": "graph.add_episode",
        "arguments": {"name": "n", "episode_body": "b", "source_description": "s"},
        "target_ref": {"system": "graphiti", "id": "central_command",
                       "read_version": "unknown"},
        "reversibility": "reversible",
    }],
    "evidence": [],
    "expected_effect": "the graph records it",
}


# --- the contract --------------------------------------------------------------


def test_a_proposal_without_confidence_still_validates():
    """Optional by construction: every existing path — email triage, coaching,
    tasks — parks proposals that state none, and must keep doing so."""
    p = Proposal.model_validate(_CORE)
    assert p.confidence is None


def test_a_bad_confidence_level_is_rejected():
    """An unvalidated free dict is how 'pretty sure' becomes data."""
    with pytest.raises(ValidationError):
        Proposal.model_validate(
            {**_CORE, "confidence": {"level": "pretty sure", "rationale": "vibes"}}
        )


def test_confidence_requires_a_rationale():
    with pytest.raises(ValidationError):
        Confidence.model_validate({"level": "high"})


def test_proposal_from_call_tolerates_confidence_nested():
    """The demo model's shape: a single-model tool param, nested."""
    call = ToolCallPart(
        tool_name="propose_action",
        args={"proposal": {**_CORE,
                           "confidence": {"level": "low", "rationale": "inferred"}}},
    )
    assert proposal_from_call(call).confidence.level.value == "low"


def test_proposal_from_call_tolerates_confidence_flattened():
    """Real Claude's shape: Pydantic AI hoists a single-model tool param to the
    top level. Both shapes must carry the new field, or it silently vanishes on
    exactly the path that matters."""
    call = ToolCallPart(
        tool_name="propose_action",
        args={**_CORE, "confidence": {"level": "high", "rationale": "stated outright"}},
    )
    assert proposal_from_call(call).confidence.level.value == "high"


def test_proposal_from_call_tolerates_a_json_string_of_the_flattened_shape():
    import json

    call = ToolCallPart(
        tool_name="propose_action",
        args=json.dumps({**_CORE,
                         "confidence": {"level": "medium", "rationale": "partly"}}),
    )
    assert proposal_from_call(call).confidence.level.value == "medium"


# --- the agreement record (pure) ----------------------------------------------


def _p(pid: str, level: str | None) -> dict:
    return {"id": pid, "intent": pid,
            "confidence": {"level": level, "rationale": "r"} if level else None}


def _d(pid: str, verdict: str, eid: int) -> dict:
    return {"id": eid, "ref_id": pid, "kind": "proposal.decided",
            "payload": {"verdict": verdict}}


def test_confidence_pairs_with_the_operators_verdict():
    report = pair_confidence(
        [_p("a", "high"), _p("b", "high"), _p("c", "low"), _p("d", "low")],
        [_d("a", "approved", 1), _d("b", "rejected", 2),
         _d("c", "approved", 3), _d("d", "rejected", 4)],
    )
    assert report["total_paired"] == 4
    assert report["by_level"]["high"] == {"approved": 1, "rejected": 1,
                                          "total": 2, "approval_rate": 0.5}
    assert [x["proposal_id"] for x in report["overconfident"]] == ["b"]
    assert [x["proposal_id"] for x in report["under_confident"]] == ["c"]


def test_an_undecided_proposal_is_awaiting_never_an_agreement():
    report = pair_confidence([_p("a", "high")], [])
    assert report["total_paired"] == 0 and report["awaiting_decision"] == 1


def test_a_proposal_with_no_stated_confidence_is_counted_apart():
    """Absent is not low: counting it as low would invent evidence against the
    agent, and counting it as high would invent evidence for it."""
    report = pair_confidence([_p("a", None)], [_d("a", "approved", 1)])
    assert report["no_confidence_stated"] == 1
    assert report["total_paired"] == 0


def test_the_latest_decision_wins_over_a_redraft_cycle():
    report = pair_confidence(
        [_p("a", "high")],
        [_d("a", "rejected", 1), _d("a", "approved", 2)],
    )
    assert report["pairs"][0]["verdict"] == "approved"


# --- the wire shape (backend-sends-first) -------------------------------------


async def test_proposal_detail_carries_confidence_to_the_cockpit(monkeypatch):
    """The cockpit hand-declares its interfaces over untyped RPC payloads, so a
    field the gateway never sends is invisible to `tsc` AND to every frontend
    test — the feature just silently never renders. The defence is a BACKEND
    test on the real detail-payload builder."""
    from central_command.api import routes

    monkeypatch.setattr(settings, "demo_mode", True)
    row = {
        "id": "prop_x", "session_id": "sess_x", "agent_id": "knowledge-steward",
        "intent": "Record it.", "status": "AWAITING_HUMAN",
        "actions": _CORE["actions"], "evidence": [],
        "expected_effect": "recorded", "provenance": None,
        "created_at": None, "decided_at": None,
        "confidence": {"level": "medium", "rationale": "one relation inferred"},
    }
    monkeypatch.setattr(repo, "load_proposal", lambda pid: _async(row))
    monkeypatch.setattr(repo, "work_items_for_session", lambda sid: _async([]))
    monkeypatch.setattr(repo, "work_items_folded_into", lambda pid: _async([]))

    detail = await routes.get_proposal("prop_x")
    assert detail["confidence"] == {"level": "medium",
                                    "rationale": "one relation inferred"}


async def test_work_items_carry_kind_to_the_reviewer_pane(monkeypatch):
    """Same rule for the source pane: it says "Source document" only if the
    backend actually sends `kind`."""
    from central_command.api import routes

    monkeypatch.setattr(settings, "demo_mode", True)
    item = {"id": "wi_1", "message_id": "<cc-doc-1@central_command.local>",
            "subject": "A doc", "payload": {"text": "body"}, "state": "PROCESSED",
            "kind": "document"}
    monkeypatch.setattr(repo, "load_proposal", lambda pid: _async({
        "id": "prop_y", "session_id": "sess_y", "agent_id": "knowledge-steward",
        "intent": "i", "status": "EXECUTED", "actions": [], "evidence": [],
        "expected_effect": "", "provenance": None, "created_at": None,
        "decided_at": None, "confidence": None,
    }))
    monkeypatch.setattr(repo, "work_items_for_session", lambda sid: _async([item]))
    monkeypatch.setattr(repo, "work_items_folded_into", lambda pid: _async([]))

    detail = await routes.get_proposal("prop_y")
    assert detail["source_emails"][0]["kind"] == "document"


async def _async(value):
    return value


# --- persistence round trip (real Postgres) -----------------------------------


pytestmark_db = needs_pg


@pytestmark_db
async def test_confidence_survives_the_round_trip_through_the_park_path(monkeypatch):
    """`load_proposal` names its columns explicitly — anything not selected is
    invisible, however well it persisted."""
    from central_command.ingest import ledger
    from central_command.runtime import steward
    from central_command.runtime.spike_model import make_steward_model

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")

    text = "Title: cc-conftest\n\nThe cutover lands on 2026-09-01, owned by Dana."
    result = await steward.run_document(
        text, source_text=text, model=make_steward_model(level="low")
    )
    row = await repo.load_proposal(result["proposal_id"])
    assert row["confidence"]["level"] == "low"
    assert row["confidence"]["rationale"]

    report = await __import__(
        "central_command.gateway.steward_agreement", fromlist=["x"]
    ).steward_agreement_report()
    assert report["agent_id"] == "knowledge-steward"
    # Undecided, so it must sit in awaiting — never counted as an agreement.
    assert report["awaiting_decision"] >= 1
    assert ledger  # the enroll path this proposal's real siblings come from
