"""Contract-model tests. The M1 proof starts here: a Proposal round-trips cleanly
through serialization (the same path used to persist it as jsonb)."""

from datetime import datetime, timezone

from central_command.contract import (
    Action,
    Decision,
    Evidence,
    Proposal,
    ProposalRecord,
    ProposalStatus,
    Reversibility,
)


def _sample_proposal() -> Proposal:
    return Proposal(
        intent="Move TEAM-142 due date to Aug 3 — Dana's email says the deadline slipped.",
        actions=[
            Action(
                capability="jira.update_issue@v2",
                arguments={"issue": "TEAM-142", "field": "duedate", "value": "2026-08-03"},
                target_ref={"system": "jira", "id": "TEAM-142", "read_version": "etag:88af"},
                reversibility=Reversibility.reversible,
            )
        ],
        evidence=[
            Evidence(
                kind="email",
                source_ref="gmail:msg_1846d2",
                locator="email.get_message(msg_1846d2)",
                claim="Sender states TEAM-142 deadline moves Jul 27 to Aug 3.",
            )
        ],
        expected_effect="TEAM-142.duedate == 2026-08-03",
    )


def test_proposal_json_roundtrip():
    p = _sample_proposal()
    restored = Proposal.model_validate_json(p.model_dump_json())
    assert restored == p
    assert restored.actions[0].reversibility is Reversibility.reversible


def test_proposal_requires_an_action():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Proposal(intent="do nothing", actions=[], evidence=[], expected_effect="")


def test_proposal_record_wraps_core_with_lifecycle():
    rec = ProposalRecord(
        id="prop_7f3a",
        session_id="sess_91c",
        agent_id="inbox-triage",
        proposal=_sample_proposal(),
        idempotency_key="prop_7f3a:act_1",
        created_at=datetime.now(timezone.utc),
    )
    assert rec.status is ProposalStatus.proposed
    assert rec.decision is None
    restored = ProposalRecord.model_validate_json(rec.model_dump_json())
    assert restored == rec
