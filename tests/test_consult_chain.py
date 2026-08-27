"""Chained consult waits — the whole chain waits for the operator (2026-08-23).

Depth 2 (2026-08-22) let B consult C. It did not let B SURVIVE C drafting: the
`consult_wait` deferral bubbling out of B's advisory run hit the outer
`consult()`'s graceful-degrade branch, so B's run died with everything it had
read, A was told to "proceed without them", and C's proposal sat in the
Decisions Inbox with nobody left to receive the operator's decision. Decision 2's
premise ("it is not a collaboration if A carries on without B's answer") was
true one hop in and false two hops in.

What is pinned here is the part that fails SILENTLY, same as its sibling
`test_consult_wait.py`: a chain that collapses looks exactly like a chain that
answered fast. So the tests drive the REAL parks and the REAL resume drivers —
A→B→C end to end, then reject, dismiss, a dead specialist, and the chain
integrity that a resumed mid-chain run would otherwise lose in memory.
"""

from __future__ import annotations

import uuid

import pytest

from central_command.api import orchestration
from central_command.config import settings
from central_command.db import repo
from central_command.gateway import gateway
from central_command.runtime import consult as consult_mod
from central_command.runtime.run import run_task
from tests.conftest import aresolve, needs_pg

A_ID, B_ID, C_ID = "chaintest-a", "chaintest-b", "chaintest-c"

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


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    # A real key in .env would otherwise make this "offline" suite spend money.
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


@pytest.fixture
def woken(monkeypatch) -> list[str]:
    """Record the wake-up hook instead of firing it, and hand back the list.

    The hook is a fire-and-forget `asyncio.create_task` that resumes on
    `resolve_model(None, ...)` — in demo mode the inbox-triage spike double,
    whose only move is a Jira due-date proposal. Letting it race here would
    resume the chain on a model chosen for another agent entirely and win the
    claim before the test's own driver call. So these tests drive the resume
    EXPLICITLY, exactly as `resume_sweep` does after a restart, and assert
    separately (`test_the_mid_chain_session_is_found_by_the_wake_hook`) that
    the hook's worklist finds the mid-chain session at all."""
    from central_command.runtime import converse

    seen: list[str] = []
    monkeypatch.setattr(converse, "wake_consult_waiters", seen.append)
    return seen


# --- model doubles -------------------------------------------------------------


def _consulting_model(target: str, prefix: str, question: str = "what do you know?"):
    """An agent that consults `target` on its first turn and, once that consult
    RETURNS (which for a chained wait is only after the operator decided and
    every deeper level finished), answers in text quoting what it was handed.

    Quoting is what makes the assertions honest: it proves the outcome text
    actually reached the model, not merely that a run completed."""
    from pydantic_ai.messages import (
        ModelResponse, TextPart, ToolCallPart, ToolReturnPart,
    )
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        for message in reversed(messages):
            for part in getattr(message, "parts", []):
                if isinstance(part, ToolReturnPart) and part.tool_name == "consult_agent":
                    return ModelResponse(
                        parts=[TextPart(content=f"{prefix}: {part.content}")]
                    )
        return ModelResponse(parts=[ToolCallPart(
            tool_name="consult_agent",
            args={"agent_id": target, "question": question},
        )])

    return FunctionModel(_respond)


def _drafting_model():
    """The leaf: answers a consultation by proposing, every time."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[
            ToolCallPart(tool_name="propose_action", args={"proposal": _DRAFT}),
        ])

    return FunctionModel(_respond)


def _cycling_model(target: str):
    """A resumed mid-chain specialist that reaches BACK toward `target`.

    Counts the `consult_agent` calls already in the history rather than looking
    for a tool return, because on a resume the return of the FIRST consult is
    the very thing being fed in — a return-shaped check would answer
    immediately and never make the second call at all."""
    from pydantic_ai.messages import (
        ModelResponse, TextPart, ToolCallPart, ToolReturnPart,
    )
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        calls = sum(
            1 for m in messages for p in getattr(m, "parts", [])
            if isinstance(p, ToolCallPart) and p.tool_name == "consult_agent"
        )
        if calls < 2:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="consult_agent",
                args={"agent_id": target, "question": "one more thing?"},
            )])
        for message in reversed(messages):
            for part in getattr(message, "parts", []):
                if isinstance(part, ToolReturnPart) and part.tool_name == "consult_agent":
                    return ModelResponse(
                        parts=[TextPart(content=f"B tried the cycle: {part.content}")]
                    )
        return ModelResponse(parts=[TextPart(content="B tried the cycle: nothing")])

    return FunctionModel(_respond)


def _text_model(text: str):
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=text)])

    return FunctionModel(_respond)


# --- the chain fixture ---------------------------------------------------------


async def _hire_trio() -> None:
    await repo.hire_agent(
        A_ID, "Chain A", "the asker", "You are A, the asker.",
        ["consult"], template="analyst", consultable=True,
    )
    await repo.hire_agent(
        B_ID, "Chain B", "the middle specialist", "You are B, consulted by A.",
        ["consult"], template="analyst", consultable=True,
    )
    await repo.hire_agent(
        C_ID, "Chain C", "the leaf specialist", "You are C, the leaf.",
        ["jira-propose"], template="analyst", consultable=True,
    )


async def _fire_trio() -> None:
    conn = await repo._conn()
    try:
        for agent_id in (A_ID, B_ID, C_ID):
            await conn.execute(
                "delete from proposal where agent_id = $1", agent_id)
            await conn.execute(
                "delete from session where agent_id = $1", agent_id)
            await conn.execute(
                "delete from agent_grant where agent_id = $1", agent_id)
            await conn.execute(
                "delete from guidance_version where agent_id = $1", agent_id)
            await conn.execute("delete from agent where id = $1", agent_id)
    finally:
        await conn.close()


class _Chain:
    """The parked A→B→C chain: three session ids plus the leaf's proposal."""

    def __init__(self, a: str, b: str, c: str, proposal_id: str) -> None:
        self.a, self.b, self.c = a, b, c
        self.proposal_id = proposal_id


async def _park_a_chain(monkeypatch, b_model=None) -> _Chain:
    """Drive A→B→C for real up to the parked state, and assert the shape.

    `run_task` is the entry deliberately: the park has to happen through the
    same call site production uses, not through a hand-built run_state."""
    monkeypatch.setattr(
        consult_mod, "_consult_model",
        aresolve(lambda agent_id, model=None: (
            (b_model or _consulting_model(C_ID, "B synthesized"))
            if agent_id == B_ID else _drafting_model()
        )),
    )
    out = await run_task(
        A_ID, "Should TASKS-12 block TASKS-13?",
        model=_consulting_model(B_ID, "A acted on"),
    )
    assert out.get("consult_waiting"), (
        f"A did not park on its consult — the chain collapsed: {out}"
    )
    a_session = out["session_id"]

    a_state = await repo.get_session_run_state(a_session)
    b_session = a_state["consult_wait"]["specialist_session_id"]
    assert b_session and b_session != a_session
    b_state = await repo.get_session_run_state(b_session)
    c_session = b_state["consult_wait"]["specialist_session_id"]
    prop = await repo.latest_proposal_for_session(c_session)
    assert prop is not None and prop["status"] == "AWAITING_HUMAN"
    return _Chain(a_session, b_session, c_session, prop["id"])


# --- (a) the full chain, end to end --------------------------------------------


@needs_pg
async def test_the_whole_chain_parks_and_unwinds_on_the_decision(monkeypatch, woken):
    """THE feature. A consults B, B consults C, C drafts — and every level is
    durably parked rather than collapsed. Approving C then unwinds the chain
    inward-out: C lands, B resumes with the real outcome and answers in TEXT,
    landing B's session hands that answer to A."""
    await _hire_trio()
    try:
        chain = await _park_a_chain(monkeypatch)

        # Parked, all three, each on the level below it.
        a_row = await repo.get_session(chain.a)
        b_row = await repo.get_session(chain.b)
        assert a_row["status"] == "AWAITING_AGENTS"
        assert b_row["status"] == "AWAITING_AGENTS"
        # The mid-chain session belongs to B — the drafter/owner rule one level
        # up: a rejection or a coaching signal here is B's, never A's.
        assert b_row["agent_id"] == B_ID
        b_state = await repo.get_session_run_state(chain.b)
        assert b_state["consult_wait"]["target"] == C_ID
        # ...and it carries B's chain, which its resume rebuilds deps from.
        assert b_state["consult_chain"] == [A_ID]

        # The operator approves the LEAF's proposal.
        await gateway.approve_and_execute(
            chain.c, chain.proposal_id, approver="operator",
            model=_text_model("C: done, the link is in place."),
        )
        assert (await repo.get_session(chain.c))["status"] == "DONE"

        # Wake B, the way the hook (or the sweep) does.
        woke_b = await orchestration.resume_consult_wait(
            chain.b, model=_consulting_model(C_ID, "B synthesized"),
        )
        assert woke_b and woke_b["resumed"] is True
        assert (await repo.get_session(chain.b))["status"] == "DONE"
        b_answer = (await repo.get_session_run_state(chain.b))["consult_answer"]
        # B was handed the REAL outcome, not "nothing happened".
        assert b_answer.startswith("B synthesized:")
        assert "APPROVED" in b_answer

        # Wake A. Its consult result is B's ANSWER — not a verdict about a
        # proposal B never drafted, which is what the pre-2026-08-23 outcome
        # text would have said ("finished without leaving a proposal").
        woke_a = await orchestration.resume_consult_wait(
            chain.a, model=_consulting_model(B_ID, "A acted on"),
        )
        assert woke_a and woke_a["resumed"] is True
        assert (await repo.get_session(chain.a))["status"] == "DONE"
        assert "B synthesized:" in woke_a["output"]
        assert "Nothing was changed" not in woke_a["output"]
    finally:
        await _fire_trio()


# --- (b) rejection: the chain simply stays parked ------------------------------


@needs_pg
async def test_a_rejection_redraft_leaves_the_whole_chain_parked(monkeypatch, woken):
    """Nothing to build, everything to verify. `_TERMINAL_SESSION` means
    FINISHED, not DECIDED — so a redraft must not wake either waiter, at any
    depth. If it did, A would act on a proposal that no longer exists."""
    await _hire_trio()
    try:
        chain = await _park_a_chain(monkeypatch)
        await gateway.reject_with_feedback(
            chain.c, chain.proposal_id, "wrong link type — try again",
            model=_drafting_model(),
        )
        # C redrafted: a NEW proposal on the SAME session, still awaiting.
        assert (await repo.get_session(chain.c))["status"] == "AWAITING_HUMAN"
        redraft = await repo.latest_proposal_for_session(chain.c)
        assert redraft["id"] != chain.proposal_id
        assert redraft["status"] == "AWAITING_HUMAN"

        # Neither waiter moved, and the drivers say "keep waiting" (None), not
        # "resumed".
        assert await orchestration.resume_consult_wait(chain.b) is None
        assert await orchestration.resume_consult_wait(chain.a) is None
        for sid in (chain.a, chain.b):
            assert (await repo.get_session(sid))["status"] == "AWAITING_AGENTS"
    finally:
        await _fire_trio()


# --- (c) dismissal: the chain wakes with "no action needed" --------------------


@needs_pg
async def test_a_dismissal_wakes_the_middle_of_the_chain(monkeypatch, woken):
    """Dismissal closes the leaf's session through `close_session`, which never
    lands — the hang `test_consult_wait` pins one level up, and it has to hold
    one level deeper too or a dismissed chain waits forever."""
    await _hire_trio()
    try:
        chain = await _park_a_chain(monkeypatch)
        await gateway.dismiss_proposal(chain.c, chain.proposal_id, "no action needed")

        woke = await orchestration.resume_consult_wait(
            chain.b, model=_consulting_model(C_ID, "B synthesized"),
        )
        assert woke and woke["resumed"] is True
        answer = (await repo.get_session_run_state(chain.b))["consult_answer"]
        assert "DISMISSED" in answer
        assert "do not propose this change yourself" in answer.lower()
    finally:
        await _fire_trio()


# --- (d) chain integrity across the resume -------------------------------------


@needs_pg
async def test_the_consult_chain_survives_into_the_resumed_deps(monkeypatch, woken):
    """CORRECTNESS, not bookkeeping. `TriageDeps.consult_chain` lived only in
    memory, so a resumed mid-chain specialist came back with `chain=()` and
    sailed past the depth and cycle refusals on anything it consulted next.

    Driven end to end rather than asserted on the run_state: B's resumed run
    consults A — which is a CYCLE that must be refused mechanically. With the
    chain lost, that refusal never fires and B really would consult its own
    asker."""
    await _hire_trio()
    try:
        chain = await _park_a_chain(monkeypatch)
        await gateway.approve_and_execute(
            chain.c, chain.proposal_id, approver="operator",
            model=_text_model("C: done."),
        )
        # On resume, B reaches back toward A.
        await orchestration.resume_consult_wait(chain.b, model=_cycling_model(A_ID))
        answer = (await repo.get_session_run_state(chain.b))["consult_answer"]
        assert "refused" in answer and "cycle" in answer, (
            "B's resumed run consulted its own asker — the persisted "
            f"consult_chain was not restored into its deps: {answer!r}"
        )
    finally:
        await _fire_trio()


# --- (e) a dead specialist still wakes the level above it ----------------------


@needs_pg
async def test_a_failed_mid_chain_specialist_wakes_its_asker_honestly(monkeypatch, woken):
    """The orphan-sweep case: a mid-chain session FAILED (its process died, or
    its resume exhausted). The asker above it must still be woken, and must be
    told plainly that nothing happened — never left to infer success."""
    from central_command.runtime.converse import land_session

    await _hire_trio()
    try:
        chain = await _park_a_chain(monkeypatch)
        await land_session(
            chain.b, failed=True, agent_id=B_ID, reason="the run died with its process",
        )
        woke = await orchestration.resume_consult_wait(
            chain.a, model=_consulting_model(B_ID, "A acted on"),
        )
        assert woke and woke["resumed"] is True
        assert "Nothing was changed" in woke["output"]
        assert "do NOT assume" in woke["output"]
    finally:
        await _fire_trio()


# --- the degrade path survives only as the genuine fallback --------------------


@needs_pg
async def test_degrading_is_reached_only_when_persistence_itself_fails(monkeypatch):
    """The old behaviour is not gone, it is DEMOTED: with no durable record of
    the mid-chain wait there is nobody for a decision to wake, so telling the
    asker to proceed without an answer is the honest thing left."""
    await _hire_trio()
    try:
        async def _no_session(*a, **kw):
            raise RuntimeError("postgres is down")

        monkeypatch.setattr(repo, "create_running_session", _no_session)
        monkeypatch.setattr(
            consult_mod, "_consult_model",
            aresolve(lambda agent_id, model=None: (
                _consulting_model(C_ID, "B synthesized")
                if agent_id == B_ID else _drafting_model()
            )),
        )
        answer = await consult_mod.consult(
            B_ID, "please advise", caller_id=A_ID,
            session_id="sess_" + uuid.uuid4().hex[:8],
        )
        assert "returned no answer" in answer
        assert "say what you could not check" in answer
    finally:
        await _fire_trio()


# --- the mid-chain session is an ordinary waiter -------------------------------


@needs_pg
async def test_the_mid_chain_session_is_found_by_the_wake_hook(monkeypatch):
    """No new machinery is the whole design. The wake hook's worklist
    (`repo.sessions_waiting_on`) and `resume_sweep`'s AWAITING_AGENTS worklist
    must find the mid-chain session with nothing added — if they don't, the
    chain survives a restart only as far as the level that was already
    supported."""
    await _hire_trio()
    try:
        chain = await _park_a_chain(monkeypatch)
        assert chain.b in await repo.sessions_waiting_on(chain.c)
        assert chain.a in await repo.sessions_waiting_on(chain.b)
        awaiting = await repo.sessions_awaiting_agents()
        assert chain.a in awaiting and chain.b in awaiting
    finally:
        await _fire_trio()
