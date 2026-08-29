"""Consult-and-wait: the asker waits for the specialist it consulted.

Until 2026-08-13 a consultation whose specialist DRAFTED a gated proposal
returned "I proposed prop_xxx, carry on without me" and the asker kept running
— against a world where the change did not exist yet. The operator's objection is the
whole feature: *"It's not really a collaboration if teammate A asks teammate B
a question, but A just continues working w/o the response from B."*

What is pinned here is the part that fails SILENTLY. A wait that never ends
looks exactly like an agent thinking, so the tests that matter most are the
ones about who wakes the asker and when — especially DISMISS, which closes the
specialist's session through a seam that never lands, and the redraft loop,
where the proposal id the wait started with is no longer the live one.
"""

from __future__ import annotations

import pytest

from central_command.api import orchestration
from central_command.runtime import consult as consult_mod
from central_command.runtime.durable import classify_deferred


class _Call:
    def __init__(self, tool_name: str, tool_call_id: str = "call_1") -> None:
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        self.args: dict = {}


def test_consult_agent_is_classified_as_its_own_kind() -> None:
    """`unknown` would fall through every landing site to "close out in text",
    which is how the asker would stop waiting without anyone noticing."""
    assert classify_deferred(_Call("consult_agent")) == "consult"
    assert classify_deferred(_Call("propose_action")) == "proposal"
    assert classify_deferred(_Call("ask_operator")) == "ask"


@pytest.mark.anyio
async def test_pure_advice_consult_returns_inline_and_never_parks(monkeypatch) -> None:
    """Only a consult that PARKED A PROPOSAL is worth waiting for. Deferring a
    pure-advice answer would park a run for an answer it already holds."""
    from pydantic_ai import CallDeferred

    from central_command.runtime import tools

    monkeypatch.setattr(consult_mod, "refusal_for", _async_none)

    async def _advice(agent_id, question, **kw):
        return "Use TASKS; it is the only real project."

    monkeypatch.setattr(consult_mod, "consult", _advice)
    out = await _call_consult(tools, monkeypatch, expect_defer=False)
    assert out == "Use TASKS; it is the only real project."


@pytest.mark.anyio
async def test_a_drafted_consult_defers_and_keys_the_wait_on_the_session(
    monkeypatch,
) -> None:
    """THE regression that matters for the redraft loop.

    A rejection resumes the specialist, which redrafts under a NEW proposal id.
    A wait keyed on the proposal would orphan there — the id it holds goes
    REJECTED forever while the specialist is still working. The session is what
    stays put, so that is what the metadata must carry."""
    from central_command.runtime import tools

    monkeypatch.setattr(consult_mod, "refusal_for", _async_none)

    async def _drafts(agent_id, question, outcome=None, **kw):
        outcome["proposal_id"] = "prop_abc"
        outcome["session_id"] = "sess_specialist"
        return "drafted"

    monkeypatch.setattr(consult_mod, "consult", _drafts)
    meta = await _call_consult(tools, monkeypatch, expect_defer=True)
    wait = meta["consult_wait"]
    assert wait["specialist_session_id"] == "sess_specialist"
    assert wait["proposal_id"] == "prop_abc"
    assert wait["target"] == "jira-expert"


def test_dismissal_wakes_the_asker() -> None:
    """HANG #1. `dismiss_proposal` withdraws the proposal and closes the
    drafting session through `close_session`, which never calls `land_session`
    — so a wake-up hook on the landing seam alone leaves every dismissed
    consult's asker parked forever. Both seams, or the feature has a silent
    hole exactly where the operator says "no action needed"."""
    import inspect

    from central_command.runtime import converse

    for seam in (converse.land_session, converse.close_session):
        assert "wake_consult_waiters" in inspect.getsource(seam), (
            f"{seam.__name__} does not wake consult waiters — an asker parked "
            "on a specialist that finished through this seam waits forever"
        )


def test_dismiss_outcome_forbids_re_proposing() -> None:
    """An agent handed a dismissed transcript re-proposes it — the loop the
    session-close reflection guard already had to stop (2026-08-13). The asker
    is woken with the dismissal, so it must be told plainly, not just informed."""
    text = orchestration._consult_outcome_text(
        "jira-expert",
        {"status": "WITHDRAWN", "intent": "create a CS project", "id": "prop_1"},
        "dismissed",
    )
    assert "DISMISSED" in text
    assert "do not propose this change yourself" in text.lower()


def test_executed_outcome_hands_back_the_real_result() -> None:
    """The point of waiting: the asker gets the value that now exists (a
    project key, an issue key) instead of guessing at one."""
    text = orchestration._consult_outcome_text(
        "jira-expert",
        {"status": "EXECUTED", "intent": "create the CS project", "id": "prop_1",
         "provenance": {"result_text": "created project CS"}},
        None,
    )
    assert "created project CS" in text
    assert "APPROVED" in text


def test_missing_proposal_never_reads_as_success() -> None:
    """A specialist that finished leaving nothing on the record must not let
    the asker infer the change happened."""
    text = orchestration._consult_outcome_text("jira-expert", None, "operator")
    assert "Nothing was changed" in text
    assert "do NOT assume" in text


def test_sweep_dispatches_consult_waits_away_from_maybe_resume() -> None:
    """HANG #2's guarantee. Hooks are lost across a restart; the sweep is what
    makes the wait survivable. But `maybe_resume` returns None for any session
    without `orch` in its run state — so without a dispatch on the run-state
    shape a consult wait sits in this worklist forever, reported as "still
    parked" on every sweep and resumed by nothing."""
    import inspect

    src = inspect.getsource(orchestration.resume_sweep)
    assert "consult_wait" in src and "resume_consult_wait" in src


def test_resume_waits_while_the_specialist_is_still_working() -> None:
    """Reject→redraft must NOT wake the asker: the specialist re-parks
    AWAITING_HUMAN under a new proposal and is still working. Only a terminal
    session means finished."""
    assert orchestration._TERMINAL_SESSION == ("DONE", "FAILED")
    assert "AWAITING_HUMAN" not in orchestration._TERMINAL_SESSION


def test_a_waiting_conversation_cannot_be_typed_into() -> None:
    """The `_COMPOSER_DISABLING` bite mark, and this feature is what triggers
    it: AWAITING_AGENTS reaches a CONVERSATION for the first time here (before
    the wait, only orchestration runs parked that way, and those are caught by
    `not_a_conversation`). That tuple is an ALLOWLIST, not a check — a refusal
    code missing from it renders an ENABLED message box that `_chat_send` then
    409s, and a send into a run paused mid-deferral is worse than a 409."""
    from central_command.api.nerve_gateway import _COMPOSER_DISABLING
    from central_command.runtime.converse import why_not_sendable

    code, message = why_not_sendable(
        {"id": "sess_w", "mode": "conversation", "status": "AWAITING_AGENTS"}
    )
    assert code == "awaiting_agents"
    assert code in _COMPOSER_DISABLING
    assert "Decisions Inbox" in message


async def _async_none(*a, **kw):
    return None


async def _call_consult(tools, monkeypatch, *, expect_defer: bool):
    """Drive `consult_agent` with a stub context; return its text, or the
    metadata of the CallDeferred it raised."""
    from pydantic_ai import CallDeferred

    class _Deps:
        agent_id = "inbox-triage"
        session_id = "sess_asker"

    class _Ctx:
        deps = _Deps()
        usage = None

    try:
        out = await tools.consult_agent(_Ctx(), "jira-expert", "which project?")
    except CallDeferred as e:
        assert expect_defer, "consult deferred when it should have answered inline"
        return e.metadata
    assert not expect_defer, "consult answered inline when it should have waited"
    return out
