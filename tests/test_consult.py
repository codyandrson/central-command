"""Consultation, generalized (doctrine Decision 1, 2026-07-29).

`consult_agent(agent_id, question)` replaced the hardcoded `consult_jira_expert`.
What has to hold: an unconsultable target is REFUSED mechanically and told who
IS consultable; the specialist's sub-run holds its granted propose packs (the
consultation drives the draft, doctrine Decision 2); the `agent.consulted`
event names the REAL caller as actor; and the answer is size-capped at the
deployment's text ceiling rather than a fresh magic number.

Depth 2 (operator decision, 2026-08-22): a consulted specialist's advisory
toolset now KEEPS `consult_agent`, so it may open one consult of its own —
what used to be a by-construction depth-1/cycle-proof guarantee (no tool, no
second consult) is now a RUNTIME chain check (`TriageDeps.consult_chain` +
`consult.refusal_for`), covered separately below.
"""

from __future__ import annotations

import inspect
import uuid

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.runtime import consult as consult_mod
from central_command.runtime import packs as packs_mod
from central_command.runtime import tools as tools_mod
from central_command.runtime.deps import TriageDeps
from central_command.runtime.tools import consult_agent
from tests.conftest import aresolve, needs_pg


async def _consult_expecting_a_draft(ctx, target: str, question: str) -> tuple[str, dict]:
    """Drive a consult whose specialist DRAFTS, and return `(proposal_id,
    wait)`.

    Since the wait landed (2026-08-13) a drafted consult does not RETURN to the
    asker at all — it raises `CallDeferred` and the asker parks until the
    specialist is finished. Everything these tests pin about the specialist's
    side is unchanged (its own session, its own agent_id, the consult chain in
    `origin`); only how the asker learns of it moved, from a returned string to
    the deferral's metadata. That the deferral escapes at all IS the new
    contract, so it is asserted here rather than swallowed.
    """
    from pydantic_ai import CallDeferred

    try:
        answer = await consult_agent(ctx, target, question)
    except CallDeferred as deferred:
        wait = (deferred.metadata or {}).get("consult_wait") or {}
        assert wait.get("proposal_id"), f"deferral carried no proposal: {wait}"
        return wait["proposal_id"], wait
    raise AssertionError(
        "a consult whose specialist drafted returned inline instead of parking "
        f"the asker — the asker would carry on without the answer: {answer!r}"
    )


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    # Anything here can reach resolve_model(); a real key in .env would make the
    # "offline" suite spend money.
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


class _Ctx:
    """Stand-in RunContext for calling the tool directly, carrying the caller's
    deps exactly as a real run does."""

    def __init__(
        self, agent_id: str | None = None, session_id: str | None = None,
        consult_chain: tuple[str, ...] = (),
    ):
        self.deps = TriageDeps(
            agent_id=agent_id, session_id=session_id, consult_chain=consult_chain,
        )


def _tool_names(agent) -> set[str]:
    names: set[str] = set()
    for ts in agent.toolsets:
        names |= set(getattr(ts, "tools", {}) or {})
    return names


# --- refusals: every one names the consultable roster --------------------------


async def test_refusal_for_a_target_that_is_not_consultable():
    # The auditor is THE non-consultable agent since the 2026-08-05 widening
    # (gate independence — a drafter must not pre-game the audit). This test
    # used inbox-triage until that decision made the whole rest of the active
    # roster consultable.
    refusal = await consult_agent(
        _Ctx(agent_id="jira-expert"), "auditor", "would you approve this?"
    )
    assert "refused" in refusal and "not consultable" in refusal
    # The list is the point: a model told only "no" answers from guesswork.
    assert "jira-expert" in refusal


async def test_refusal_for_an_unknown_target():
    refusal = await consult_agent(
        _Ctx(agent_id="inbox-triage"), "no-such-agent", "hello?"
    )
    assert "not on the team's roster" in refusal
    assert "jira-expert" in refusal


async def test_refusal_for_consulting_yourself():
    refusal = await consult_agent(
        _Ctx(agent_id="jira-expert"), "jira-expert", "what do I think?"
    )
    assert "you ARE jira-expert" in refusal


@needs_pg
async def test_refusal_for_a_retired_target_says_it_is_retired():
    """Consultability is a roster fact and retirement removes it — a retired
    specialist must not keep answering consults because someone's charter still
    names it."""
    agent_id = "consulttest-retired"
    try:
        await repo.hire_agent(
            agent_id, "Retired Specialist", "test specialist", "You are a specialist.",
            ["jira-read"], template="analyst", consultable=True,
        )
        assert agent_id in await consult_mod.consultable_ids()
        assert await repo.retire_agent(agent_id, "test over")

        refusal = await consult_agent(_Ctx(agent_id="inbox-triage"), agent_id, "q")
        assert "RETIRED" in refusal and agent_id not in await consult_mod.consultable_ids()
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from agent_grant where agent_id = $1", agent_id)
            await conn.execute(
                "delete from guidance_version where agent_id = $1", agent_id)
            await conn.execute("delete from agent where id = $1", agent_id)
        finally:
            await conn.close()


async def test_consult_itself_refuses_a_bad_target_loudly():
    """The tool turns a refusal into text; the seam underneath must still refuse
    — a direct caller (or a later API route) must not be able to run an
    advisory sub-run for an agent the roster does not allow."""
    with pytest.raises(ValueError, match="not on the team's roster"):
        await consult_mod.consult("no-such-agent", "q", caller_id="inbox-triage")


# --- the sub-run's surface: read-only + granted propose + one consult ---------


def test_advisory_packs_keep_the_propose_packs_and_the_consult_pack():
    """Slice 2 + depth-2 (2026-08-22): the specialist's GRANTED propose packs
    ride into its consults — `consult()` is their driver — and so does the
    `consult` pack itself now, since a consulted specialist may open one
    consult of its own. What still goes is every deferral the sub-run cannot
    host: an operator question, a project tool."""
    granted = ("jira-read", "jira-propose", "graph-read", "graph-propose",
               "consult", "task-propose", "ask-operator")
    assert packs_mod.advisory_packs(granted) == (
        "jira-read", "jira-propose", "graph-read", "graph-propose",
        "consult", "task-propose",
    )
    assert packs_mod.advisory_packs(("orchestrate", "graph-read")) == ("graph-read",)


async def test_the_advisory_toolset_holds_propose_and_consult_but_never_ask():
    granted = ("jira-read", "jira-propose", "graph-read", "graph-propose",
               "consult", "task-propose", "ask-operator")
    advisory = packs_mod.advisory_packs(granted)
    agent = await consult_mod.build_advisory_agent(
        "jira-expert", "You are the Jira expert.", packs=advisory,
    )
    names = _tool_names(agent)
    assert names, "could not introspect agent tools"
    assert "jira_get_issue" in names and "search_knowledge_graph" in names
    assert "propose_action" in names, (
        "the specialist drafts the gated change itself (doctrine Decision 2)"
    )
    assert "consult_agent" in names, (
        "depth 2 (2026-08-22): a consulted specialist may open one consult "
        "of its own — refused mechanically past that, not by a missing tool"
    )
    for deferring in ("ask_operator", "conclude_discussion", "submit_plan",
                      "assign_work"):
        assert deferring not in names, (
            f"{deferring} raises CallDeferred and a consultation is not its "
            "host — there is no operator loop and no plan here"
        )
    # Holding a deferral tool without DeferredToolRequests is a UserError
    # mid-consult, so the output type is derived from the packs, never assumed.
    assert packs_mod.has_advisory_deferral(advisory)
    from pydantic_ai import DeferredToolRequests

    assert DeferredToolRequests in agent.output_type


async def test_a_read_only_advisory_agent_still_answers_in_plain_text():
    """The str-only shape is preserved for loadouts that cannot defer — adding
    DeferredToolRequests to a run with no deferral tool only invites the model
    to look for one it has not got."""
    advisory = packs_mod.advisory_packs(("jira-read", "graph-read", "record-read"))
    assert not packs_mod.has_advisory_deferral(advisory)
    agent = await consult_mod.build_advisory_agent(
        "jira-expert", "You are the Jira expert.", packs=advisory,
    )
    assert agent.output_type is str


def test_every_deferral_tool_is_classified_from_the_tools_source():
    """The two sets are DATA, so they can drift. Re-derive from the tools' own
    bodies: every pack tool that raises CallDeferred is either excluded from
    consults (no host) or explicitly allowed (this module drives it). A tool in
    neither would end an advisory run with nothing to park it."""
    defers = set()
    for pack in packs_mod.PACKS.values():
        for tool_name in pack.tool_names:
            src = inspect.getsource(getattr(tools_mod, tool_name))
            if "CallDeferred" in src:
                defers.add(tool_name)
    unclassified = (
        defers - packs_mod.NON_ADVISORY_TOOLS - packs_mod.ADVISORY_DEFERRAL_TOOLS
        - packs_mod.ADVISORY_GRACEFUL_TOOLS
    )
    assert not unclassified, (
        f"deferral tools classified neither way: {unclassified}"
    )
    # The three sets are pairwise disjoint — a tool cannot be barred, driven
    # AS A PROPOSAL, and degraded gracefully all at once.
    assert not (packs_mod.NON_ADVISORY_TOOLS & packs_mod.ADVISORY_DEFERRAL_TOOLS)
    assert not (packs_mod.NON_ADVISORY_TOOLS & packs_mod.ADVISORY_GRACEFUL_TOOLS)
    assert not (packs_mod.ADVISORY_DEFERRAL_TOOLS & packs_mod.ADVISORY_GRACEFUL_TOOLS)
    # And every listed name is real (a typo would silently exclude nothing).
    for name in (
        packs_mod.NON_ADVISORY_TOOLS | packs_mod.ADVISORY_DEFERRAL_TOOLS
        | packs_mod.ADVISORY_GRACEFUL_TOOLS
    ):
        assert getattr(tools_mod, name, None) is not None, f"no such tool: {name}"
    # Whatever a consultation CAN drive must be a proposal to `park_proposal`'s
    # classifier, or the driver would hand a non-Proposal to the validator.
    from central_command.runtime.durable import PROPOSE_TOOLS

    assert packs_mod.ADVISORY_DEFERRAL_TOOLS <= PROPOSE_TOOLS


async def test_an_advisory_agent_cannot_be_built_without_a_charter():
    """The fresh-run rule: `build_charter`'s `content or CHARTER` fallback would
    hand a silent inbox-triage charter to a specialist that passed nothing. Only
    the RESUME factory may leave the charter empty."""
    with pytest.raises(ValueError, match="no charter"):
        await consult_mod.build_advisory_agent("jira-expert", "   ", packs=("jira-read",))


@needs_pg
async def test_the_sub_run_loads_the_targets_governed_charter():
    from pydantic_ai.messages import ModelResponse, SystemPromptPart, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from central_command.runtime.agent import CHARTER as TRIAGE_CHARTER

    seen: list[str] = []

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        for message in messages:
            for part in getattr(message, "parts", []):
                if isinstance(part, SystemPromptPart):
                    seen.append(part.content)
        return ModelResponse(parts=[TextPart(content="advice")])

    agent_id = "consulttest-charter"
    try:
        await repo.hire_agent(
            agent_id, "Charter Specialist", "test specialist",
            "You are the SPECIALIST-UNDER-TEST.", ["jira-read"],
            template="analyst", consultable=True,
        )
        out = await consult_mod.consult(
            agent_id, "how?", model=FunctionModel(_respond), caller_id="inbox-triage",
        )
        assert out == "advice"
        prompt = "\n".join(seen)
        assert "SPECIALIST-UNDER-TEST" in prompt
        assert TRIAGE_CHARTER[:60] not in prompt, (
            "the sub-run fell through to the inbox-triage charter"
        )
        # The advisory framing is stated where it is TRUE for every agent.
        assert "YOU ARE BEING CONSULTED" in prompt
        assert "No propose capabilities granted" in prompt
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from agent_grant where agent_id = $1", agent_id)
            await conn.execute(
                "delete from guidance_version where agent_id = $1", agent_id)
            await conn.execute("delete from agent where id = $1", agent_id)
        finally:
            await conn.close()


# --- the event: the actor is the REAL caller ----------------------------------


@needs_pg
async def test_consult_returns_advice_and_lands_on_the_log():
    since = await repo.latest_event_id()
    advice = await consult_agent(
        _Ctx(agent_id="inbox-triage"), "jira-expert",
        "How should I record TASKS-12 blocking TASKS-13?",
    )
    assert "blocks" in advice  # the deterministic advisor's content
    consulted = await repo.list_events(since_id=since, kind="agent.consulted")
    assert consulted and consulted[0]["ref_id"] == "jira-expert"


@needs_pg
async def test_a_spendy_caller_does_not_starve_the_specialist():
    """The per-consult REQUEST_LIMIT is relative to what is already on the
    shared usage clock — an absolute limit charged the specialist for the
    caller's whole prior spend (found live 2026-08-05: a consult died
    `UsageLimitExceeded` and the caller was told the specialist was down)."""
    from pydantic_ai.usage import RunUsage

    from central_command.runtime import consult as consult_mod

    spent = RunUsage(requests=consult_mod.REQUEST_LIMIT + 5)
    advice = await consult_mod.consult(
        "jira-expert", "How should I record TASKS-12 blocking TASKS-13?",
        usage=spent, caller_id="inbox-triage",
    )
    assert "blocks" in advice


@needs_pg
async def test_the_consulted_event_names_the_real_caller_as_actor():
    """Regression for the hardcoded `actor="agent:inbox-triage"` that stood at
    runtime/tools.py:335 until 2026-07-29: every consult on the audit trail
    read as triage's, whoever actually asked."""
    since = await repo.latest_event_id()
    session_id = "sess_consult_" + uuid.uuid4().hex[:8]
    await consult_agent(
        _Ctx(agent_id="litellm-manager", session_id=session_id),
        "jira-expert", "How do I link two issues?",
    )
    rows = [
        r for r in await repo.list_events(since_id=since, kind="agent.consulted")
        if (r["payload"] or {}).get("session_id") == session_id
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["actor"] == "agent:litellm-manager"
    assert row["payload"]["caller"] == "litellm-manager"
    assert row["payload"]["target"] == "jira-expert"
    assert row["payload"]["answer_chars"] > 0


async def test_consult_degrades_when_the_specialist_is_down(monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("specialist offline")

    monkeypatch.setattr(consult_mod, "consult", broken)
    advice = await consult_agent(_Ctx(agent_id="inbox-triage"), "jira-expert", "anything")
    assert "unavailable" in advice  # the run continues without them


# --- distillation: the answer is capped at the deployment's text ceiling -------


def test_the_answer_ceiling_is_the_deployment_text_bound():
    assert consult_mod.ANSWER_CEILING == tools_mod._MODEL_TEXT_CEILING


@needs_pg
async def test_a_runaway_answer_is_cut_honestly(monkeypatch):
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content="x" * 5000)])

    monkeypatch.setattr(consult_mod, "ANSWER_CEILING", 100)
    answer = await consult_mod.consult(
        "jira-expert", "how?", model=FunctionModel(_respond), caller_id="inbox-triage",
    )
    assert answer.startswith("x" * 100)
    assert "TRUNCATED at 100 chars" in answer
    assert len(answer) < 500  # the cap, plus the honest tail — never 5000


# --- a gap declared mid-consultation stays traceable to the parent session ----


@needs_pg
async def test_a_gap_declared_during_consultation_is_escalatable():
    """The consult runs inside the CONSULTING agent's session (M15), so a gap
    the specialist declares must carry that session id or
    `repo.gaps_for_session(...)` — what the orchestrator uses to escalate a
    child's gaps — finds nothing."""
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        for message in messages:
            for part in getattr(message, "parts", []):
                if isinstance(part, ToolReturnPart) and part.tool_name == "declare_gap":
                    return ModelResponse(parts=[TextPart(content="noted")])
        return ModelResponse(parts=[ToolCallPart(tool_name="declare_gap", args={
            "kind": "missing_knowledge", "subject": "confluence",
            "need": "a Confluence Cloud REST reference",
        })])

    session_id = "sess_consult_" + uuid.uuid4().hex[:8]
    advice = await consult_mod.consult(
        "jira-expert", "How do I link this?", model=FunctionModel(_respond),
        session_id=session_id, caller_id="inbox-triage",
    )
    assert advice == "noted"

    escalatable = await repo.gaps_for_session(session_id)
    assert len(escalatable) == 1, "the orchestrator would have escalated nothing"
    assert escalatable[0]["subject"] == "confluence"
    assert escalatable[0]["agent_id"] == "jira-expert"


# --- slice 2: the specialist drafts the proposal itself -----------------------

_DRAFT = {
    "intent": "Link TASKS-12 as blocking TASKS-13 — the dependency is real.",
    "actions": [{
        "capability": "jira.link_issues",
        "arguments": {"from_key": "TASKS-12", "to_key": "TASKS-13",
                      "link_type": "Blocks"},
        "target_ref": {"system": "jira", "id": "TASKS-12", "read_version": "unknown"},
        "reversibility": "reversible",
    }],
    "evidence": [{
        "kind": "jira",
        "source_ref": "jira:TASKS-12",
        "locator": "jira_get_issue(TASKS-12)",
        "claim": "TASKS-12 has no outward link to TASKS-13.",
    }],
    "expected_effect": "TASKS-12 blocks TASKS-13",
}


def _drafting_model():
    """A specialist that answers a consultation by proposing, every time."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[
            ToolCallPart(tool_name="propose_action", args={"proposal": _DRAFT}),
        ])

    return FunctionModel(_respond)


async def _drop_proposal(proposal_id: str, session_id: str) -> None:
    conn = await repo._conn()
    try:
        await conn.execute("delete from proposal where id = $1", proposal_id)
        await conn.execute("delete from session where id = $1", session_id)
    finally:
        await conn.close()


@needs_pg
async def test_a_consulted_specialist_parks_its_own_proposal(monkeypatch):
    """Doctrine Decision 2 end to end: the asker gets TEXT naming a proposal the
    SPECIALIST owns — its own fresh session, its own agent_id, the consult chain
    in `origin` and on the creation event."""
    monkeypatch.setattr(
        consult_mod, "_consult_model",
        aresolve(lambda agent_id, model=None: _drafting_model()),
    )
    since = await repo.latest_event_id()
    caller_session = "sess_consult_" + uuid.uuid4().hex[:8]

    proposal_id, wait = await _consult_expecting_a_draft(
        _Ctx(agent_id="inbox-triage", session_id=caller_session),
        "jira-expert", "Should TASKS-12 block TASKS-13?",
    )

    row = await repo.load_proposal(proposal_id)
    try:
        assert row is not None, "the specialist's proposal never landed"
        # The DRAFTER owns the row — that is what routes a rejection to the
        # agent whose conventions these are.
        assert row["agent_id"] == "jira-expert"
        assert row["status"] == "AWAITING_HUMAN"
        assert row["origin"] == {
            "consulted_by": "inbox-triage", "caller_session_id": caller_session,
        }
        # A FRESH session of the specialist's own, never the caller's.
        assert row["session_id"] != caller_session
        # ...and THAT is what the asker's wait is keyed on, not the proposal
        # id: the next test rejects this proposal and the specialist redrafts
        # under a new id on this same session, so a proposal-keyed wait would
        # orphan there while the specialist was still working.
        assert wait["specialist_session_id"] == row["session_id"]
        session = await repo.get_session(row["session_id"])
        assert session["agent_id"] == "jira-expert"
        assert session["status"] == "AWAITING_HUMAN"
        assert await repo.load_paused_session(row["session_id"]) is not None

        created = [
            e for e in await repo.list_events(since_id=since, kind="proposal.created")
            if e["ref_id"] == proposal_id
        ]
        assert len(created) == 1
        assert created[0]["actor"] == "agent:jira-expert"
        assert created[0]["payload"]["consulted_by"] == "inbox-triage"
        assert created[0]["payload"]["caller_session_id"] == caller_session

        consulted = [
            e for e in await repo.list_events(since_id=since, kind="agent.consulted")
            if (e["payload"] or {}).get("session_id") == caller_session
        ]
        assert len(consulted) == 1
        assert consulted[0]["payload"]["proposal_id"] == proposal_id
    finally:
        if row is not None:
            await _drop_proposal(proposal_id, row["session_id"])


@needs_pg
async def test_a_pure_advice_consult_still_opens_no_session_and_no_proposal():
    """Slice 1's shape is preserved where nothing gated is warranted: the
    `proposal_id` on the event is NULL, not absent-and-unexplained."""
    since = await repo.latest_event_id()
    caller_session = "sess_consult_" + uuid.uuid4().hex[:8]
    await consult_agent(
        _Ctx(agent_id="inbox-triage", session_id=caller_session),
        "jira-expert", "How should I record TASKS-12 blocking TASKS-13?",
    )
    consulted = [
        e for e in await repo.list_events(since_id=since, kind="agent.consulted")
        if (e["payload"] or {}).get("session_id") == caller_session
    ]
    assert len(consulted) == 1
    assert consulted[0]["payload"]["proposal_id"] is None
    assert not [
        e for e in await repo.list_events(since_id=since, kind="proposal.created")
    ]


@needs_pg
async def test_rejecting_a_consult_proposal_coaches_the_drafter(monkeypatch):
    """The whole point of Decision 2: the operator's feedback goes back into the
    SPECIALIST's run, and its redraft stays the specialist's — the asker never
    appears."""
    from central_command.gateway import gateway

    monkeypatch.setattr(
        consult_mod, "_consult_model",
        aresolve(lambda agent_id, model=None: _drafting_model()),
    )
    caller_session = "sess_consult_" + uuid.uuid4().hex[:8]
    proposal_id, wait = await _consult_expecting_a_draft(
        _Ctx(agent_id="inbox-triage", session_id=caller_session),
        "jira-expert", "Should TASKS-12 block TASKS-13?",
    )
    row = await repo.load_proposal(proposal_id)
    redraft_row = None
    try:
        out = await gateway.reject_with_feedback(
            row["session_id"], proposal_id,
            "Other way round — TASKS-13 blocks TASKS-12.",
            model=_drafting_model(),
        )
        redraft_id = out["redraft_proposal_id"]
        redraft_row = await repo.load_proposal(redraft_id)
        assert redraft_row["agent_id"] == "jira-expert", (
            "a rejection must coach the drafter, not the agent that asked"
        )
        assert redraft_row["session_id"] == row["session_id"]
        assert (await repo.load_proposal(proposal_id))["status"] == "REJECTED"
        # The redraft is a NEW proposal id on the SAME session the asker is
        # waiting on — which is exactly why the wait is session-keyed. Pinned
        # here, at the one place the redraft actually happens: a wait holding
        # `proposal_id` would now be pointing at a REJECTED row forever while
        # the specialist carried on working.
        assert redraft_id != proposal_id
        assert wait["specialist_session_id"] == redraft_row["session_id"]
    finally:
        if redraft_row is not None:
            conn = await repo._conn()
            try:
                await conn.execute(
                    "delete from proposal where id = $1", redraft_row["id"])
            finally:
                await conn.close()
        if row is not None:
            await _drop_proposal(proposal_id, row["session_id"])


@needs_pg
async def test_the_decisions_wire_carries_the_consult_origin(monkeypatch):
    """BACKEND test of the WIRE, from the real route — the cockpit hand-declares
    its interfaces over untyped RPC, so a field the gateway never sends is
    invisible to `tsc` and to every frontend test, and the chip just silently
    never renders (2026-07-27, twice in one session)."""
    from central_command.api import nerve_gateway

    monkeypatch.setattr(
        consult_mod, "_consult_model",
        aresolve(lambda agent_id, model=None: _drafting_model()),
    )
    caller_session = "sess_consult_" + uuid.uuid4().hex[:8]
    proposal_id, _wait = await _consult_expecting_a_draft(
        _Ctx(agent_id="inbox-triage", session_id=caller_session),
        "jira-expert", "Should TASKS-12 block TASKS-13?",
    )
    row = await repo.load_proposal(proposal_id)
    try:
        payload = await nerve_gateway._decisions_list()
        mine = [p for p in payload["proposals"] if p["id"] == proposal_id]
        assert len(mine) == 1, "the parked proposal is not in the inbox payload"
        assert mine[0]["origin"] == {
            "consulted_by": "inbox-triage", "caller_session_id": caller_session,
        }
        # The detail pane reads the same field off the same row.
        detail = await repo.load_proposal(proposal_id)
        assert detail["origin"]["consulted_by"] == "inbox-triage"
    finally:
        if row is not None:
            await _drop_proposal(proposal_id, row["session_id"])


# --- depth 2 (operator decision, 2026-08-22) ------------------------------------


@needs_pg
async def test_a_consulted_specialist_may_open_one_consult_of_its_own():
    """Depth 2 end to end: A consults B, B consults C, C answers in text, and
    B's own answer (built from C's) is what flows all the way back to A."""
    from pydantic_ai.messages import (
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
    )
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    b_id, c_id = "consulttest-b", "consulttest-c"

    def _b_responds(messages, info: AgentInfo) -> ModelResponse:
        for message in reversed(messages):
            for part in getattr(message, "parts", []):
                if isinstance(part, ToolReturnPart) and part.tool_name == "consult_agent":
                    return ModelResponse(
                        parts=[TextPart(content=f"B synthesized: {part.content}")]
                    )
        return ModelResponse(parts=[ToolCallPart(
            tool_name="consult_agent",
            args={"agent_id": c_id, "question": "what do you know about this?"},
        )])

    try:
        await repo.hire_agent(
            b_id, "Consult Test B", "test specialist B",
            "You are SPECIALIST B, consulted by another agent.", ["consult"],
            template="analyst", consultable=True,
        )
        await repo.hire_agent(
            c_id, "Consult Test C", "test specialist C",
            "You are SPECIALIST C, the leaf of this chain.", ["jira-read"],
            template="analyst", consultable=True,
        )
        answer = await consult_mod.consult(
            b_id, "please advise", model=FunctionModel(_b_responds),
            caller_id="inbox-triage",
        )
        assert answer.startswith("B synthesized:"), (
            f"the depth-2 hop through C never reached B's answer: {answer!r}"
        )
    finally:
        for agent_id in (b_id, c_id):
            conn = await repo._conn()
            try:
                await conn.execute(
                    "delete from agent_grant where agent_id = $1", agent_id)
                await conn.execute(
                    "delete from guidance_version where agent_id = $1", agent_id)
                await conn.execute("delete from agent where id = $1", agent_id)
            finally:
                await conn.close()


async def test_depth_beyond_two_is_refused_mechanically_not_raised():
    """A chain already 2 deep refuses the next hop with TEXT — the mechanical
    refusal `consult_agent` returns for every other unreachable target — never
    an exception that would crash the run."""
    ctx = _Ctx(agent_id="jira-expert", consult_chain=("inbox-triage", "confluence-expert"))
    refusal = await consult_agent(ctx, "wiki-agent", "one more thing?")
    assert "refused" in refusal.lower()
    assert "deep" in refusal.lower() or "depth" in refusal.lower()


async def test_consulting_back_into_the_chain_is_refused_as_a_cycle():
    """B (mid-chain, having been asked by A) tries to consult A back — refused
    as a cycle, not merely discouraged in prose."""
    ctx = _Ctx(agent_id="confluence-expert", consult_chain=("inbox-triage",))
    refusal = await consult_agent(ctx, "inbox-triage", "can you weigh in?")
    assert "refused" in refusal.lower()
    assert "cycle" in refusal.lower()


async def test_self_consult_is_still_refused_with_a_chain_present():
    """The pre-existing self-consult refusal is unchanged by the chain check —
    asking yourself is refused whether or not you are mid-chain."""
    ctx = _Ctx(agent_id="jira-expert", consult_chain=("inbox-triage",))
    refusal = await consult_agent(ctx, "jira-expert", "anything?")
    assert "refused" in refusal.lower()
    assert "yourself" in refusal.lower() or "yourself adds nothing" in refusal.lower()


async def test_the_tool_forwards_the_chain_into_the_consult(monkeypatch):
    """The seam the refusal tests above cannot see: `consult_agent` must pass
    the caller's chain into `consult()`, or the chain RESETS at every level and
    A->B->C->D each sees depth 1 forever — the depth cap and cycle refusal
    would never fire on the real path. Pins the forwarding, not the check."""
    seen: dict = {}

    async def _capture(agent_id, question, **kw):
        seen.update(kw)
        return "advice"

    monkeypatch.setattr(consult_mod, "consult", _capture)
    ctx = _Ctx(agent_id="confluence-expert", consult_chain=("inbox-triage",))
    answer = await consult_agent(ctx, "jira-expert", "how do links work?")
    assert answer == "advice"
    assert seen.get("chain") == ("inbox-triage",)


# --- a crash-replayed consult reuses the surviving draft ----------------------


@needs_pg
async def test_a_replayed_consult_reuses_the_surviving_draft():
    """The specialist's proposal commits inside `consult()`; the asker's own
    consult-wait park commits later, in the caller — so a process death in
    between leaves a live draft with nobody parked on it, and the retry sweep
    re-drives the asker's whole turn. Found live 2026-08-31: two replays 11
    minutes apart each drafted for real, and TASKS-61 AND TASKS-62 were both
    approved and created. The replay must reuse the surviving AWAITING_HUMAN
    draft, never re-run the specialist — and a DECIDED draft must not
    short-circuit a genuinely new consult."""
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    caller_session = "sess_" + uuid.uuid4().hex[:12]
    specialist_session = "sess_" + uuid.uuid4().hex[:12]
    prop_id = "prop_" + uuid.uuid4().hex[:12]
    try:
        await repo.create_running_session(specialist_session, "jira-expert")
        await repo.save_proposal(
            proposal_id=prop_id,
            session_id=specialist_session,
            agent_id="jira-expert",
            intent="create the tracking issue",
            actions=[], evidence=[],
            expected_effect="one issue exists in TASKS",
            idempotency_key=f"{prop_id}:0",
            origin={"consulted_by": "inbox-triage",
                    "caller_session_id": caller_session},
        )
        since = await repo.latest_event_id()

        def _explode(messages, info: AgentInfo) -> ModelResponse:
            raise AssertionError("a replayed consult must not re-run the specialist")

        outcome: dict = {}
        answer = await consult_mod.consult(
            "jira-expert", "ensure the task is tracked in Jira",
            model=FunctionModel(_explode), caller_id="inbox-triage",
            session_id=caller_session, outcome=outcome,
        )
        assert prop_id in answer and "AWAITING THE OPERATOR'S REVIEW" in answer
        assert outcome == {"proposal_id": prop_id, "session_id": specialist_session}
        reused = [
            r for r in await repo.list_events(
                since_id=since, kind="agent.consult_draft_reused")
            if r["ref_id"] == caller_session
        ]
        assert len(reused) == 1
        assert reused[0]["payload"]["proposal_id"] == prop_id

        # Once the draft is DECIDED the wait is genuinely over: the same
        # session consulting again is a new question, not a replay.
        await repo.set_proposal_status(prop_id, "EXECUTED")

        def _advise(messages, info: AgentInfo) -> ModelResponse:
            return ModelResponse(parts=[TextPart(content="fresh advice")])

        answer = await consult_mod.consult(
            "jira-expert", "and how do I link it?",
            model=FunctionModel(_advise), caller_id="inbox-triage",
            session_id=caller_session,
        )
        assert answer == "fresh advice"
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from proposal where id = $1", prop_id)
            await conn.execute(
                "delete from session where id = $1", specialist_session)
        finally:
            await conn.close()
