"""The knowledge steward — hire, run, and the citation re-check.

The steward is HIRED, not seeded: a fresh database reproduces it through
`ensure_registered()` rather than through schema.sql's founding-roster
UPDATEs, whose `where … and role = ''` guards shipped a broken roster once.
"""

from __future__ import annotations

import pytest

from central_command.config import settings
from central_command.contract import Evidence
from central_command.db import repo
from central_command.runtime import steward
from central_command.runtime.spike_model import (
    make_deferred_tool_model,
    make_steward_model,
    make_steward_no_extraction_model,
)
from central_command.runtime.steward_profile import AGENT_ID
from tests.conftest import needs_pg

DOC = (
    "Title: Platform decision\n\n"
    "The platform team standardised on Postgres 17 for all new services.\n"
    "Dana owns the migration."
)


# --- the citation re-check (pure) ---------------------------------------------


def _ev(kind: str, claim: str) -> Evidence:
    return Evidence(kind=kind, source_ref="doc", locator="work item payload",
                    claim=claim)


def test_a_verbatim_quote_verifies():
    out = steward.verified_evidence(
        [_ev("document", "standardised on Postgres 17")], DOC
    )
    assert out[0]["claim_matches_source"] is True


def test_a_paraphrase_is_flagged():
    """A faithful paraphrase is still not a quote — the reviewer is told."""
    out = steward.verified_evidence(
        [_ev("document", "the team picked Postgres as its standard database")], DOC
    )
    assert out[0]["claim_matches_source"] is False


def test_other_evidence_kinds_are_left_not_checked():
    """`None` means NOT CHECKED, never "checked and inconclusive" — setting it
    False on a kind this checker cannot judge would cry wolf."""
    out = steward.verified_evidence([_ev("jira_state", "TASKS-1 is open")], DOC)
    assert out[0].get("claim_matches_source") is None


def test_the_check_never_sees_a_serialized_payload():
    """The `_event_source_texts` lesson: matching against a serialized dict
    lets a quote verify against JSON structure. The steward checks against
    `payload["text"]` and nothing else, so a key name is not a source."""
    out = steward.verified_evidence(
        [_ev("document", "text title")],
        DOC,  # the STORED TEXT — not {"text": ..., "title": ...}
    )
    assert out[0]["claim_matches_source"] is False


# --- everything else needs a database ----------------------------------------


pytestmark = needs_pg


@pytest.fixture(autouse=True)
async def demo(monkeypatch):
    # Anywhere a model is reachable, pin demo mode — with a real key in .env the
    # "offline" suite would otherwise call Claude for real and spend money.
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    yield


# --- the hire ------------------------------------------------------------------


async def test_ensure_registered_is_idempotent_and_hires_a_full_roster_member():
    await steward.ensure_registered()
    first = await repo.get_agent(AGENT_ID)
    await steward.ensure_registered()
    again = await repo.get_agent(AGENT_ID)

    assert first is not None and again is not None
    assert first["created_at"] == again["created_at"], "the second call re-hired"

    # Roster membership IS a non-empty role column — an `upsert_agent`
    # registration would leave this empty and the agent off the roster.
    assert first["role"].strip()
    assert first["status"] == "ACTIVE"
    # consultable=True is the operator decision of 2026-07-30 (doctrine
    # Decision 3, steward-as-librarian), recorded in steward.py precisely so a
    # FRESH database reproduces it. This assertion said `consultable is False`
    # until 2026-08-01 and passed only on accumulated dev-box state: the row had
    # been hired under the older code and `ensure_hired` is idempotent, so it
    # was never corrected. The k3s migration gave us a genuinely empty Postgres
    # and the stale expectation failed immediately — the same "ask what a
    # brand-new database does, not what the dev box does" trap CLAUDE.md warns
    # about, this time sprung on the test database.
    assert first["taskable"] is False and first["consultable"] is True
    # A False management capability needs a RECORDED reason (the uniform-
    # management rule; tests/test_governance.py enforces it roster-wide).
    assert first["deviations"].strip()

    grants = [g["pack"] for g in await repo.list_grants(AGENT_ID)
              if g["revoked_at"] is None]
    # web-read added 2026-08-06 (operator-approved sweep): corroborate URLs
    # cited by ingested documents before proposing graph facts. skill-propose
    # + web-search added 2026-08-07 (agent-proposed skill authoring) — this
    # list lagged that change for two days because the accumulated test
    # database's steward row predated it and idempotent ensure_hired never
    # re-grants; the fresh-per-run test database (conftest, 2026-08-09)
    # exposed the stale expectation on its first run.
    # catalog-propose added 2026-08-30 (sources-catalog slice 6): the steward
    # reads every catalog version off the ledger, so it is the agent that KNOWS
    # which lineage a new one supersedes — gated like everything else.
    assert sorted(grants) == ["ask-operator", "catalog-propose", "consult",
                              "graph-propose", "graph-read", "skill-propose",
                              "web-read", "web-search"]
    # Slice 1 grants no task-propose and no jira packs.
    assert "task-propose" not in grants

    current = await repo.current_charter(AGENT_ID)
    assert current and current["content"].strip()


async def test_the_charter_writes_no_capability_list_of_its_own():
    """Capability lists are GENERATED from grants (D25). A hand-written one can
    drift from what the agent holds; a generated one cannot."""
    # Self-sufficient on a fresh test DB: the steward is HIRED, never seeded,
    # so without this an -k subset that skips the hire test above reads zero
    # grants and fails here (bit two agents, 2026-08-14).
    await steward.ensure_registered()

    from central_command.runtime.steward_profile import CHARTER

    # Naming the mechanism in prose is fine (every founder charter does); what
    # must never appear is a LIST — the `capability = ... / arguments = ...`
    # block, which is generated from grants and supersedes anything written by
    # hand. Assembling the real prompt must add exactly one such section.
    assert "GRANTED CAPABILITIES" not in CHARTER
    assert "capability = '" not in CHARTER
    assert "arguments  =" not in CHARTER

    from central_command.runtime.agent import build_charter

    assembled = build_charter(CHARTER, await repo.list_grants(AGENT_ID) and
                              [g["pack"] for g in await repo.list_grants(AGENT_ID)
                               if g["revoked_at"] is None])
    assert assembled.count("YOUR GRANTED CAPABILITIES") == 1
    assert "capability = 'graph.add_episode'" in assembled


async def test_the_steward_is_not_seeded_in_the_schema():
    """A fresh database reproduces it through the HIRE path, never by touching
    schema.sql's founding-roster seeds and their `role = ''` guards."""
    from pathlib import Path

    schema = (Path(__file__).parents[1] / "central_command" / "db" / "schema.sql").read_text(encoding="utf-8")
    seeding = [
        line for line in schema.splitlines()
        if AGENT_ID in line and not line.lstrip().startswith("--")
    ]
    assert seeding == [], f"the steward is seeded in schema.sql: {seeding}"


# --- the run path --------------------------------------------------------------


async def test_one_document_yields_one_proposal_with_one_episode():
    result = await steward.run_document(
        DOC, source_text=DOC, model=make_steward_model()
    )
    assert result["deferred"] is True
    row = await repo.load_proposal(result["proposal_id"])
    assert row["agent_id"] == AGENT_ID
    assert len(row["actions"]) == 1
    assert row["actions"][0]["capability"] == "graph.add_episode"
    # And the demo model's citation really is verbatim — so the check above is
    # exercised against a live pairing, not a hand-written one.
    assert row["evidence"][0]["claim_matches_source"] is True


async def test_the_run_loads_the_governed_charter_not_the_triage_one():
    """`build_agent_for` is the RESUME factory and passes no charter, so a fresh
    run built through it wears the inbox-triage charter. The steward's system
    prompt must be its own."""
    result = await steward.run_document(
        DOC, source_text=DOC, model=make_steward_model()
    )
    run_state = await repo.get_session_run_state(result["session_id"])
    system = next(
        p for m in run_state["messages"] for p in m["parts"]
        if p.get("part_kind") == "system-prompt"
    )
    assert "knowledge steward" in str(system["content"])
    assert "inbox-triage agent" not in str(system["content"])


async def test_no_extraction_returns_a_text_outcome_not_a_proposal():
    result = await steward.run_document(
        DOC, source_text=DOC, model=make_steward_no_extraction_model()
    )
    assert result["deferred"] is False
    assert "Nothing durable" in result["output"]
    assert result.get("proposal_id") is None


async def test_an_ask_parks_an_operator_item_instead_of_exploding():
    """The steward holds ask-operator. Handing an unclassified deferral to
    `Proposal.model_validate` raises inside the parser, 500s the caller, and
    leaves the session RUNNING forever — classify before parking."""
    result = await steward.run_document(
        DOC, source_text=DOC,
        model=make_deferred_tool_model(
            "ask_operator", {"question": "Which Postgres 17 minor?"}
        ),
    )
    assert result["asked"] is True
    item = await repo.get_operator_item(result["item_id"])
    assert item["agent_id"] == AGENT_ID
    assert item["status"] == "OPEN"


async def test_a_paraphrased_citation_reaches_the_inbox_flagged():
    result = await steward.run_document(
        DOC, source_text=DOC,
        model=make_steward_model(claim="the team went with Postgres, broadly"),
    )
    row = await repo.load_proposal(result["proposal_id"])
    assert row["evidence"][0]["claim_matches_source"] is False


# --- follow-on proposals (2026-08-13) ------------------------------------------


async def test_follow_on_proposals_drain_a_rich_document_one_decision_at_a_time():
    """The APPROVED resume leg parks the run's next proposal instead of landing
    the session — one episode per decision until the steward closes out in
    text. Before 2026-08-13 the second propose call was silently dropped and
    the session closed after a single approved episode."""
    from central_command.gateway import gateway
    from central_command.ingest import dispatcher, ledger
    from central_command.runtime.spike_model import make_steward_multifact_model

    facts = ["standardised on Postgres 17", "Dana owns the migration"]
    model = make_steward_multifact_model(facts)

    enrolled = await ledger.enroll_document("Platform decision (follow-on)", DOC)
    result = await dispatcher.dispatch_item(enrolled["item_id"], model=model)
    assert result["deferred"] is True
    session_id = result["session_id"]

    # Approve fact 1 → the follow-on parks; the session does NOT land.
    out = await gateway.approve_and_execute(
        session_id, result["proposal_id"], model=model
    )
    follow_id = out.get("follow_on_proposal_id")
    assert follow_id, f"no follow-on parked: {out}"
    assert out["final_output"] is None
    row = await repo.load_proposal(follow_id)
    assert row["status"] == "AWAITING_HUMAN"
    assert row["agent_id"] == AGENT_ID
    assert row["actions"][0]["capability"] == "graph.add_episode"
    # Facts 2..N keep the anti-hallucination defence: the verbatim quote is
    # re-checked against the STORED document via the session→work-item link,
    # exactly as fact 1 was inside `run_document`.
    assert row["evidence"][0]["claim_matches_source"] is True
    assert await repo.session_status(session_id) not in ("DONE", "FAILED")

    # Approve fact 2 → nothing remains → the run closes in text and lands.
    out2 = await gateway.approve_and_execute(session_id, follow_id, model=model)
    assert out2.get("follow_on_proposal_id") is None
    assert "Nothing durable remains" in str(out2["final_output"])
    assert await repo.session_status(session_id) == "DONE"
