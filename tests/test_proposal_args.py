"""Argument-shape validation runs BEFORE the Inbox and again at the Executor,
from one list (`contract.ARG_SPECS`).

Found live 2026-08-29: one `graph.add_episode` intent took the operator FOUR
approvals — `summary` instead of `episode_body`, no `name`, no `scope` —
because the Executor was the first thing to look at the arguments, and it
found one problem per round (two of them as bare KeyErrors: `'name'`).

Story 11.3 (epic small-fixes R1/R2, GATE-009 and GATE-067, ruled by the
operator 2026-10-29): a `charter.update` without its `rationale` is handed back
in-run and refused by the Executor from this same list; a draft the model
writes (the four `ProposalArg` tools) without a `confidence` is handed back
in-run by `contract.validate_proposal_shape`, while a proposal a tool builds
server-side carries none and still parks.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry

from central_command import events
from central_command.contract import ARG_SPECS, Action, Proposal, Reversibility, validate_action_args
from central_command.contract import validate_proposal_shape
from central_command.db import repo
from central_command.gateway import executor
from central_command.gateway.capabilities import gated_write_names
from central_command.integrations import graphiti_ingest
from central_command.runtime import packs
from central_command.runtime import tools as tools_mod
from central_command.runtime.deps import TriageDeps
from tests.conftest import needs_pg

# The live 2026-08-29 first draft, verbatim in shape.
_BAD_ARGS = {"summary": "EA scope — operator contact doctrine …"}


_CONFIDENCE = {"level": "high", "rationale": "The operator stated the doctrine outright."}


def _proposal(arguments: dict, capability: str = "graph.add_episode",
              confidence: dict | None = _CONFIDENCE) -> Proposal:
    return Proposal(
        intent="record the contact doctrine", expected_effect="graph episode",
        evidence=[], confidence=confidence,
        actions=[Action(capability=capability, arguments=arguments,
                        target_ref={"system": "graphiti", "id": "central_command"},
                        reversibility=Reversibility.reversible)],
    )


# The one spec that is not a gated write: task creation is a TRIGGER (story
# 4.7, GATE-032), done directly by the `create_task` tool, which validates
# through this same list rather than a second one. Adding a name here is a
# governance change, not a test fix.
UNGATED_TRIGGER_SPECS = {"task.create"}


def test_every_spec_names_a_real_gated_write():
    """A spec for a capability the Executor does not dispatch is a check on
    nothing — and the seven curation handlers READ their required keys from
    the spec, so a missing one fails at import. The declared trigger specs are
    the one exception, and each must really be ungated and really be checked
    by the tool that performs it."""
    assert set(ARG_SPECS) - UNGATED_TRIGGER_SPECS <= gated_write_names()
    assert UNGATED_TRIGGER_SPECS <= set(ARG_SPECS)
    assert not UNGATED_TRIGGER_SPECS & gated_write_names()
    assert not UNGATED_TRIGGER_SPECS & set(executor.HANDLERS)
    import inspect

    assert '"task.create"' in inspect.getsource(tools_mod.create_task)


def _subscripted_keys(fn) -> set[str]:
    """The `args["k"]` subscripts in a handler's CODE (docstring excluded) —
    each one is a KeyError waiting for a draft that omits it."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    keys = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == "args" and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            keys.add(node.slice.value)
    return keys


def test_every_subscripted_argument_has_a_spec():
    """Found live 2026-09-29: jira.update_attributes subscripted `priority`,
    no Jira capability had a spec, so a draft without it passed BOTH tiers,
    executed three creates, and died on a bare KeyError "'priority'" —
    half-applied. A handler that subscripts a key must have a spec that
    guarantees it: required (non-empty), or named in an any_present group
    when an empty value is legitimate."""
    unguarded = {}
    for cap, fn in executor.HANDLERS.items():
        keys = _subscripted_keys(fn)
        if not keys:
            continue
        spec = ARG_SPECS.get(cap)
        guaranteed = set(spec.required) if spec else set()
        if spec:
            guaranteed |= {g[0] for g in spec.any_present if len(g) == 1}
        missing = keys - guaranteed
        if missing:
            unguarded[cap] = sorted(missing)
    assert unguarded == {}, (
        f"handlers subscript arguments no ARG_SPECS entry requires: {unguarded} "
        "— add the spec (contract/args.py), or read the key with .get() if it "
        "is genuinely optional"
    )


def test_update_attributes_is_a_partial_edit():
    """The shape the 2026-09-29 draft had — date and labels, no priority —
    is valid now; naming nothing is not; null/[] count as naming."""
    assert validate_action_args("jira.update_attributes", {
        "issue_key": "TASKS-21", "due_date": "2026-10-11", "labels": ["cayenne"]}) == []
    assert validate_action_args("jira.update_attributes", {
        "issue_key": "TASKS-21", "due_date": None}) == []
    assert validate_action_args("jira.update_attributes", {
        "issue_key": "TASKS-21", "labels": []}) == []
    (problem,) = validate_action_args("jira.update_attributes", {"issue_key": "TASKS-21"})
    assert "priority, labels, due_date" in problem


def test_all_problems_are_reported_at_once():
    """One message per problem, all missing keys in one line — the one-field-
    per-round grind is what turned one bad draft into three failed approvals."""
    problems = validate_action_args("graph.add_episode", _BAD_ARGS)
    assert len(problems) == 1
    assert "name, episode_body, scope, reference_time" in problems[0]
    assert "present: summary" in problems[0]
    assert validate_action_args("graph.add_episode@v1", {"scope": "team", "name": "n", "episode_body": "b", "reference_time": "2026-01-01T00:00:00Z"}) == [
        "graph.add_episode@v1: scope='team' is not one of shared, private"
    ]
    assert validate_action_args("no.such_capability", {}) == []  # no spec = no guess


@needs_pg
async def test_bad_draft_is_handed_back_in_run_and_recorded():
    """The runtime half: ModelRetry (the model redrafts in the same run, the
    Inbox never sees it) AND a `proposal.rejected_in_run` event — the
    operator is spared the re-approval, not the knowledge."""
    since = await repo.latest_event_id()
    ctx = SimpleNamespace(deps=TriageDeps(agent_id="ea", session_id="sess_test_args"))
    with pytest.raises(ModelRetry) as excinfo:
        await tools_mod._validate_proposal(ctx, _proposal(_BAD_ARGS), agent_authored=True)
    assert "name, episode_body, scope, reference_time" in str(excinfo.value)
    rows = await repo.list_events(since_id=since, kind="proposal.rejected_in_run")
    assert len(rows) == 1
    assert rows[0]["ref_id"] == "sess_test_args"
    assert rows[0]["payload"]["agent_id"] == "ea"
    assert rows[0]["payload"]["capabilities"] == ["graph.add_episode"]
    assert "name, episode_body, scope, reference_time" in rows[0]["payload"]["problems"][0]


async def test_executor_refuses_the_same_shape_before_any_action_runs(monkeypatch):
    """The Executor half, from the same list: the trust boundary never assumes
    the runtime checked (an API-submitted proposal skips the tool path). Every
    action is checked before the FIRST runs, so the failure carries an empty
    completed-cursor and no handler is ever called."""
    async def explode(*a, **k):
        raise AssertionError("handler ran on a malformed action")

    monkeypatch.setattr(graphiti_ingest, "enqueue", explode)
    good = Action(capability="graph.add_episode",
                  arguments={"name": "n", "episode_body": "b", "reference_time": "2026-01-01T00:00:00Z", "scope": "shared"},
                  target_ref={"system": "graphiti", "id": "central_command"},
                  reversibility=Reversibility.reversible)
    bad = _proposal(_BAD_ARGS).actions[0]
    with pytest.raises(executor.ExecutionFailed) as excinfo:
        await executor.execute([good, bad], approver="human:lee", source_refs=[])
    assert excinfo.value.completed == []
    assert excinfo.value.failed_capability == "graph.add_episode"
    assert "name, episode_body, scope, reference_time" in excinfo.value.error
    assert excinfo.value.error != "'name'"  # the bare KeyError is gone


def test_executor_normalises_the_reference_time_and_refuses_words():
    """The shape half of the reference-time law (2026-09-19): a real ISO-8601
    instant is normalised to UTC `Z` form (a bare date reads as midnight UTC,
    an offset is converted); "today"/"now" are refused — a relative time is
    the assumption the argument exists to outlaw. Presence is the shared
    ARG_SPECS' job; this is what the handler does with the value."""
    from central_command.gateway.executor import ExecutorError, _episode_reference_time

    assert _episode_reference_time("2026-05-12T09:30:00Z") == "2026-05-12T09:30:00Z"
    assert _episode_reference_time("2026-05-12") == "2026-05-12T00:00:00Z"
    assert _episode_reference_time("2026-05-12T09:30:00-06:00") == "2026-05-12T15:30:00Z"
    assert _episode_reference_time("2026-05-12T09:30:00") == "2026-05-12T09:30:00Z"
    for bad in ("today", "now", "", None, "May 12th"):
        with pytest.raises(ExecutorError) as excinfo:
            _episode_reference_time(bad)
        assert "reference_time" in str(excinfo.value)


def test_every_propose_tool_has_a_redraft_budget():
    """pydantic-ai's default is ONE retry; the second bad draft failed whole
    sessions live (2026-08-18). Every propose tool hands drafts back now, so
    every one carries the budget — not just bulk-dismiss."""
    toolset = packs.toolset_for(list(packs.PACKS))
    propose = {name: t for name, t in toolset.tools.items() if name.startswith("propose_")}
    assert propose, "no propose tools in the granted toolset?"
    assert {t.max_retries for t in propose.values()} == {10}


# --- story 11.3: rationale (GATE-009) and confidence (GATE-067) ---------------

_CHARTER_NO_RATIONALE = {"agent_id": "a", "content": "c"}


def test_a_charter_update_without_rationale_is_named_by_the_one_list():
    (problem,) = validate_action_args("charter.update", _CHARTER_NO_RATIONALE)
    assert "rationale" in problem and "present: agent_id, content" in problem
    assert validate_action_args("charter.update", {**_CHARTER_NO_RATIONALE,
                                                   "rationale": "why"}) == []


@needs_pg
async def test_a_coach_run_drafting_without_rationale_is_handed_back_and_redrafts(monkeypatch):
    """The coach's first draft drops `rationale`: the run gets a ModelRetry
    naming it (recorded as `proposal.rejected_in_run`), redrafts in the same
    run, and only the redraft reaches the Inbox."""
    from pydantic_ai.messages import RetryPromptPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from central_command.config import settings
    from central_command.runtime import spike_model
    from central_command.runtime.run import run_coach
    from tests.test_coach_loop import _seeded_agent

    monkeypatch.setattr(settings, "test_model", True)
    retries: list[str] = []

    def respond(messages, info):
        sent_back = [p for m in messages for p in getattr(m, "parts", [])
                     if isinstance(p, RetryPromptPart)]
        retries[:] = [str(p.content) for p in sent_back]
        response = spike_model._respond_coach(messages, info)
        if not sent_back:
            for part in response.parts:
                if isinstance(part, ToolCallPart) and part.tool_name == "propose_action":
                    part.args["proposal"]["actions"][0]["arguments"].pop("rationale")
        return response

    agent = await _seeded_agent("rationale")
    since = await repo.latest_event_id()
    out = await run_coach(agent, model=FunctionModel(respond))

    assert retries and "rationale" in retries[0], retries
    assert out["deferred"] is True
    row = await repo.load_proposal(out["proposal_id"])
    assert row["actions"][0]["arguments"]["rationale"]
    rejected = await repo.list_events(since_id=since, kind="proposal.rejected_in_run")
    mine = [r for r in rejected if r["ref_id"] == out["session_id"]]
    assert len(mine) == 1 and "rationale" in mine[0]["payload"]["problems"][0]


async def test_the_executor_refuses_a_charter_update_without_rationale_before_it_runs(
        monkeypatch):
    """The Executor half: an API-submitted charter edit skips the runtime, so
    the same list refuses it before the handler writes a version."""
    async def explode(*a, **k):
        raise AssertionError("the charter handler ran on a rationale-less edit")

    monkeypatch.setitem(executor.HANDLERS, "charter.update", explode)
    action = Action(capability="charter.update", arguments=dict(_CHARTER_NO_RATIONALE),
                    target_ref={"system": "central_command", "id": "a"},
                    reversibility=Reversibility.reversible)
    with pytest.raises(executor.ExecutionFailed) as excinfo:
        await executor.execute([action], approver="human:lee", source_refs=[])
    assert excinfo.value.completed == []
    assert "rationale" in excinfo.value.error


def test_validate_proposal_shape_names_a_missing_or_empty_confidence():
    assert validate_proposal_shape(_proposal({}, confidence=None))[0].startswith("confidence:")
    (problem,) = validate_proposal_shape(
        _proposal({}, confidence={"level": "low", "rationale": "  "}))
    assert problem.startswith("confidence.rationale")
    assert validate_proposal_shape(_proposal({})) == []


_GOOD_EPISODE = {"name": "n", "episode_body": "b", "scope": "shared",
                 "reference_time": "2026-01-01T00:00:00Z"}


@needs_pg
async def test_propose_action_without_confidence_is_handed_back_and_with_one_parks():
    from pydantic_ai import CallDeferred

    ctx = SimpleNamespace(deps=TriageDeps(agent_id="ea", session_id="sess_test_conf"))
    with pytest.raises(ModelRetry) as excinfo:
        await tools_mod.propose_action(ctx, _proposal(_GOOD_EPISODE, confidence=None))
    assert "confidence" in str(excinfo.value)
    with pytest.raises(CallDeferred):
        await tools_mod.propose_action(ctx, _proposal(_GOOD_EPISODE))


async def test_a_server_built_mail_send_without_confidence_still_parks():
    """`propose_mail_send` builds its proposal from plain arguments, so it
    carries no confidence, and inventing one would falsify the record."""
    from pydantic_ai import CallDeferred

    ctx = SimpleNamespace(deps=TriageDeps(agent_id="ea", session_id="sess_test_mail"))
    with pytest.raises(CallDeferred) as excinfo:
        await tools_mod.propose_mail_send(
            ctx, to=["jane.doe@example.com"], subject="Thursday",
            body="Thursday works.", rationale="She asked for a time.")
    assert excinfo.value.metadata["proposal"]["confidence"] is None


def test_the_agent_authored_set_is_exactly_the_proposal_arg_tools():
    """Pins `packs.AGENT_AUTHORED_PROPOSE_TOOLS` to the tool signatures, and
    each propose tool's call to `_validate_proposal` to its membership — a new
    `ProposalArg` tool that forgot the confidence check fails here."""
    import inspect
    import re

    authored = {
        name for name, fn in inspect.getmembers(tools_mod, inspect.iscoroutinefunction)
        if name.startswith("propose_")
        and any(p.annotation in (tools_mod.ProposalArg, "ProposalArg")
                for p in inspect.signature(fn).parameters.values())
    }
    assert authored == packs.AGENT_AUTHORED_PROPOSE_TOOLS
    for name, fn in inspect.getmembers(tools_mod, inspect.iscoroutinefunction):
        flags = re.findall(r"_validate_proposal\(ctx, proposal, agent_authored=(\w+)\)",
                           inspect.getsource(fn))
        if name.startswith("propose_") and flags:
            assert flags == [str(name in authored)], (name, flags)


def test_every_agent_offered_an_authored_tool_is_told_to_state_confidence():
    for name, pack in packs.PACKS.items():
        section = packs.charter_section([name])
        offers = set(pack.tool_names) & packs.AGENT_AUTHORED_PROPOSE_TOOLS
        assert (packs.CONFIDENCE_GUIDANCE in section) == bool(offers), name
