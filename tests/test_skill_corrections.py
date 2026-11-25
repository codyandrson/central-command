"""The skill learning loop through the gate (DL-215).

Three things are held here:

  * WHO may propose: every roster agent but the auditors and the editor holds
    `skill-propose`, by the idempotent widening in schema.sql (the story 4.9
    task-create precedent) and by every hiring loadout; a revoked grant is
    never resurrected.
  * WHAT a correction is: one exact span of a document's CURRENT text and its
    replacement, applied at propose time by code. The proposal carries the full
    corrected document (written byte for byte), the diff, and the base version
    the Executor re-checks — a moved document is world-moved, never overwritten.
  * WHEN reflection may propose one: at most one per closed session, only for a
    skill the session loaded, only when the span matches and the quote is one
    verbatim span of the session; anything else is dropped and recorded.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai import CallDeferred

from central_command.config import settings
from central_command.contract.failures import WorldMoved, is_world_moved
from central_command.db import repo
from central_command.gateway import executor
from central_command.runtime import packs, reflection
from central_command.runtime import tools as tools_mod
from central_command.runtime.proposals import _keep_only_server_skill_base
from central_command.runtime.skill_corrections import SpanMismatch, apply_exact_span
from central_command.runtime.templates import (
    ACCOUNTABILITY_TEMPLATE,
    EA_TEMPLATE,
    EDITOR_TEMPLATE,
    GRAPH_CURATOR_TEMPLATE,
    MCP_MANAGER_TEMPLATE,
    STEWARD_TEMPLATE,
    TEMPLATES,
)
from tests.conftest import needs_pg

FOUNDING = (
    "inbox-triage",
    "jira-expert",
    "orchestrator",
    "litellm-manager",
    "coach",
    "confluence-expert",
    "wiki-agent",
)
EXCLUDED = ("auditor", "graph-auditor", "editor")
_SCHEMA = Path(repo.__file__).with_name("schema.sql")
_MARKER = "-- skill-propose for every roster agent but the auditors and the editor"

CAPTURED = datetime(2025, 3, 1, tzinfo=UTC)

GUIDANCE = (
    "# Expense reports\n"
    "1. Fill in the amount and the cost centre.\n"
    "2. Submit the report.\n"
    "Keep the report title short.\n"
    "Keep the report title short.\n"
)


async def _grants(agent_id: str, *, active: bool = True) -> set[str]:
    conn = await repo._conn()
    try:
        rows = await conn.fetch(
            "select pack from agent_grant where agent_id = $1"
            + (" and revoked_at is null" if active else ""),
            agent_id,
        )
    finally:
        await conn.close()
    return {r["pack"] for r in rows}


def _load_messages(skill_ids) -> list:
    """A live run's `ctx.messages` in which each skill's guidance was loaded:
    the `load_capability` call AND its return."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
    )

    out = []
    for i, sid in enumerate(skill_ids):
        out.append(
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="load_capability", args={"id": sid}, tool_call_id=f"lc{i}"
                    )
                ]
            )
        )
        out.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="load_capability",
                        content={"instructions": GUIDANCE},
                        tool_call_id=f"lc{i}",
                    )
                ]
            )
        )
    return out


class _Ctx:
    def __init__(self, agent_id="jira-expert", loaded=()):
        self.deps = type(
            "D", (), {"agent_id": agent_id, "session_id": "sess_corr", "loaded_skills_from": None}
        )()
        self.messages = _load_messages(loaded)


@pytest.fixture()
async def skill():
    """A throwaway skill with one guidance document at v1."""
    skill_id = f"cc-correction-test-{uuid.uuid4().hex[:8]}"
    await repo.create_skill(skill_id, "Expense reports", "how to file an expense report")
    await repo.add_skill_doc(
        skill_id,
        "guidance",
        "guidance",
        "Expense reports",
        GUIDANCE,
        describes="the expense tool",
        source_url="https://example.com/expenses",
        captured_at=CAPTURED,
    )
    # The grant is cascaded away with the skill row.
    await repo.grant_skill("jira-expert", skill_id)
    yield skill_id
    conn = await repo._conn()
    try:
        await conn.execute("delete from skill where id = $1", skill_id)
    finally:
        await conn.close()


# --- who may propose ----------------------------------------------------------


@needs_pg
async def test_every_founding_agent_but_the_auditor_holds_skill_propose():
    for agent_id in FOUNDING:
        assert "skill-propose" in await _grants(agent_id), agent_id
    for agent_id in EXCLUDED:
        assert "skill-propose" not in await _grants(agent_id, active=False), agent_id


def test_default_packs_carry_the_pack_for_every_founding_agent():
    for agent_id, held in packs.DEFAULT_PACKS.items():
        assert "skill-propose" in held, agent_id


def test_every_hiring_loadout_but_the_editors_carries_the_pack():
    for t in [
        *TEMPLATES.values(),
        STEWARD_TEMPLATE,
        GRAPH_CURATOR_TEMPLATE,
        EA_TEMPLATE,
        MCP_MANAGER_TEMPLATE,
        ACCOUNTABILITY_TEMPLATE,
    ]:
        assert "skill-propose" in t.default_packs, t.id
    # The editor holds no propose pack at all; its charter says so.
    assert not any(p.endswith("-propose") for p in EDITOR_TEMPLATE.default_packs)


def test_every_member_of_the_pack_is_a_gated_proposal():
    """Checked before widening: the pack's tools all end in a gated proposal,
    and its capabilities are registered gated writes."""
    from central_command.gateway.capabilities import gated_write_names
    from central_command.runtime.durable import PROPOSE_TOOLS

    pack = packs.PACKS["skill-propose"]
    assert set(pack.tool_names) <= PROPOSE_TOOLS
    assert {c.name for c in pack.capabilities} <= gated_write_names()


@needs_pg
async def test_the_orchestrator_deviation_no_longer_claims_no_propose_tools():
    conn = await repo._conn()
    try:
        text = await conn.fetchval("select deviations from agent where id = 'orchestrator'")
    finally:
        await conn.close()
    assert "Holds NO propose tools" not in text
    assert "skill-propose" in text


def test_the_widening_never_names_an_excluded_agent_as_a_target():
    block = _SCHEMA.read_text()[_SCHEMA.read_text().index(_MARKER) :]
    assert "a.id not in ('auditor', 'graph-auditor', 'editor')" in block
    assert not re.search(r"\bdelete\s+from\s+agent_grant", block, re.IGNORECASE)


@needs_pg
async def test_the_widening_is_additive_idempotent_and_respects_history():
    block = _SCHEMA.read_text()[_SCHEMA.read_text().index(_MARKER) :]
    tag = uuid.uuid4().hex[:8]
    holder, revoked, bare, retired = (
        f"sp-{n}-{tag}" for n in ("holder", "revoked", "bare", "retired")
    )
    conn = await repo._conn()
    try:
        for aid, status in (
            (holder, "ACTIVE"),
            (revoked, "ACTIVE"),
            (bare, "ACTIVE"),
            (retired, "RETIRED"),
        ):
            await conn.execute(
                "insert into agent (id, name, model, charter, role, status) "
                "values ($1, $1, '', '', 'r', $2)",
                aid,
                status,
            )
        for aid in (holder, revoked, retired):
            await conn.execute(
                "insert into agent_grant (agent_id, pack) values ($1, 'jira-read')", aid
            )
        await conn.execute(
            "insert into agent_grant (agent_id, pack, revoked_at, revoked_reason) "
            "values ($1, 'skill-propose', now(), 'operator took it away')",
            revoked,
        )
        # the first update after this release: no marker yet (the seed is once-only)
        await conn.execute("delete from app_setting where key = 'seed:skill-propose-all'")
        await conn.execute(block)
        await conn.execute(block)  # a second run changes nothing
    finally:
        await conn.close()
    try:
        assert "skill-propose" in await _grants(holder)
        assert "skill-propose" not in await _grants(revoked)  # never resurrected
        assert "skill-propose" in await _grants(revoked, active=False)
        assert await _grants(bare, active=False) == set()  # takes no toolsets
        assert await _grants(retired, active=False) == {"jira-read"}
    finally:
        conn = await repo._conn()
        try:
            ids = [holder, revoked, bare, retired]
            await conn.execute("delete from agent_grant where agent_id = any($1)", ids)
            await conn.execute("delete from agent where id = any($1)", ids)
        finally:
            await conn.close()


# --- the exact-span correction -------------------------------------------------


def test_apply_exact_span_replaces_the_one_occurrence():
    out = apply_exact_span(
        GUIDANCE, "2. Submit the report.\n", "2. Attach the receipt.\n3. Submit the report.\n"
    )
    assert "2. Attach the receipt.\n3. Submit the report.\n" in out
    with pytest.raises(SpanMismatch) as none:
        apply_exact_span(GUIDANCE, "3. Approve it.", "x")
    assert none.value.count == 0
    with pytest.raises(SpanMismatch) as twice:
        apply_exact_span(GUIDANCE, "Keep the report title short.", "x")
    assert twice.value.count == 2
    # Exact: a case or whitespace variant is not the span.
    with pytest.raises(SpanMismatch):
        apply_exact_span(GUIDANCE, "2. submit the report.", "x")


@needs_pg
async def test_the_tool_captures_the_corrected_document_and_its_diff(skill):
    with pytest.raises(CallDeferred) as e:
        await tools_mod.propose_skill_correction(
            _Ctx(loaded=(skill,)),
            skill_id=skill,
            find="2. Submit the report.\n",
            replace="2. Attach the receipt.\n3. Submit the report.\n",
            why="A report without its receipt is sent back.",
        )
    proposal = e.value.metadata["proposal"]
    action = proposal["actions"][0]
    args = action["arguments"]
    assert action["capability"] == "skill.doc_add@v1"
    assert args["skill_id"] == skill and args["doc_key"] == "guidance"
    assert args["content"] == GUIDANCE.replace(
        "2. Submit the report.\n", "2. Attach the receipt.\n3. Submit the report.\n"
    )
    assert args["base_version"] == 1
    assert args["correction"] == {
        "find": "2. Submit the report.\n",
        "replace": "2. Attach the receipt.\n3. Submit the report.\n",
    }
    assert args["describes"] == "the expense tool"
    effect = proposal["expected_effect"]
    assert "+2. Attach the receipt." in effect and "-2. Submit the report." in effect
    assert "A report without its receipt is sent back." in proposal["intent"]


@needs_pg
@pytest.mark.parametrize(
    "find, expected",
    [
        ("3. Approve it.", "not found"),
        ("Keep the report title short.", "occurs 2 times"),
    ],
)
async def test_the_tool_refuses_a_span_that_is_not_exactly_once(skill, find, expected):
    out = await tools_mod.propose_skill_correction(
        _Ctx(loaded=(skill,)), skill_id=skill, find=find, replace="x", why="a lesson"
    )
    assert out.startswith("refused:") and expected in out
    assert "Nothing was proposed" in out and "CURRENT text" in out


@needs_pg
async def test_the_tool_refuses_an_unknown_document_or_an_empty_why(skill):
    assert "no current document" in await tools_mod.propose_skill_correction(
        _Ctx(loaded=(skill,)), skill_id=skill, doc_key="nope", find="a", replace="b", why="w"
    )
    assert "why" in await tools_mod.propose_skill_correction(
        _Ctx(loaded=(skill,)), skill_id=skill, find="2. Submit the report.", replace="b", why="  "
    )


def test_a_model_written_base_version_is_dropped_at_park():
    from central_command.contract import Action, Proposal, Reversibility

    def _proposal():
        return Proposal(
            intent="i",
            expected_effect="e",
            evidence=[],
            actions=[
                Action(
                    capability="skill.doc_add@v1",
                    arguments={
                        "skill_id": "s",
                        "doc_key": "guidance",
                        "content": "c",
                        "base_version": 7,
                    },
                    target_ref={"system": "central_command", "id": "s"},
                    reversibility=Reversibility.reversible,
                )
            ],
        )

    authored = _proposal()
    _keep_only_server_skill_base(authored, server_built=False)
    assert "base_version" not in authored.actions[0].arguments
    built = _proposal()
    _keep_only_server_skill_base(built, server_built=True)
    assert built.actions[0].arguments["base_version"] == 7


async def _proposed_args(skill_id: str) -> dict:
    with pytest.raises(CallDeferred) as e:
        await tools_mod.propose_skill_correction(
            _Ctx(loaded=(skill_id,)),
            skill_id=skill_id,
            find="2. Submit the report.\n",
            replace="2. Attach the receipt.\n3. Submit the report.\n",
            why="receipts",
        )
    return e.value.metadata["proposal"]["actions"][0]["arguments"]


@needs_pg
async def test_the_executor_writes_exactly_the_captured_bytes(skill):
    args = await _proposed_args(skill)
    await executor.WORLD_CHECKS["skill.doc_add"](args)  # the base still holds
    out = await executor._skill_doc_add(args, "operator", "jira-expert")
    assert "v2" in out
    current = next(d for d in await repo.list_skill_docs(skill) if d["doc_key"] == "guidance")
    assert current["version"] == 2 and current["content"] == args["content"]
    assert current["added_by"] == "agent:jira-expert"


@needs_pg
async def test_a_moved_document_is_world_moved_and_nothing_is_written(skill):
    args = await _proposed_args(skill)
    await repo.add_skill_doc(
        skill, "guidance", "guidance", "Expense reports", GUIDANCE + "4. The operator's own edit.\n"
    )
    with pytest.raises(WorldMoved) as pre:
        await executor.WORLD_CHECKS["skill.doc_add"](args)
    assert pre.value.expected == 1 and pre.value.actual == 2
    with pytest.raises(WorldMoved) as e:
        await executor._skill_doc_add(args, "operator", "jira-expert")
    assert is_world_moved(e.value)
    assert e.value.target == f"skill_doc:{skill}/guidance"
    history = await repo.skill_doc_history(skill, "guidance")
    assert [h["version"] for h in history] == [2, 1]  # the correction never landed
    # The in-transaction refusal holds on its own, past the pre-check.
    with pytest.raises(repo.StaleSkillDoc):
        await repo.add_skill_doc(skill, "guidance", "guidance", "t", "c", expected_version=1)


def test_a_correction_names_its_document_as_a_world_check_target():
    from central_command.contract import Action, Reversibility

    a = Action(
        capability="skill.doc_add@v1",
        arguments={"skill_id": "s", "doc_key": "guidance", "base_version": 1},
        target_ref={"system": "central_command", "id": "s"},
        reversibility=Reversibility.reversible,
    )
    assert ("skill_doc", "s/guidance") in executor._targets_of(a)


# --- reflection proposes at most one -------------------------------------------


QUOTE = "always attach the receipt before you submit"


def _transcript(skill_id: str, *, loaded: bool = True) -> dict:
    load = (
        [
            {
                "parts": [
                    {
                        "part_kind": "tool-call",
                        "tool_name": "load_capability",
                        "args": {"id": skill_id},
                        "tool_call_id": "c1",
                    }
                ]
            },
            {
                "parts": [
                    {
                        "part_kind": "tool-return",
                        "tool_name": "load_capability",
                        "content": {"instructions": GUIDANCE},
                        "tool_call_id": "c1",
                    }
                ]
            },
        ]
        if loaded
        else []
    )
    return {
        "messages": [
            {"parts": [{"part_kind": "user-prompt", "content": "file my expense report"}]},
            *load,
            {"parts": [{"part_kind": "text", "content": "I submitted the report."}]},
            {
                "parts": [
                    {"part_kind": "user-prompt", "content": f"it bounced — {QUOTE}, every time"}
                ]
            },
        ]
    }


def _correction(skill_id: str, **over) -> dict:
    c = {
        "skill_id": skill_id,
        "doc_key": "guidance",
        "find": "2. Submit the report.\n",
        "replace": "2. Attach the receipt.\n3. Submit the report.\n",
        "why": "A report without its receipt is sent back.",
        "quote": QUOTE,
    }
    return {**c, **over}


def _model(reflection_args: dict):
    """The distiller answers with `reflection_args`; the drafting sub-run calls
    `propose_skill_correction` with the correction as distilled."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        if info.output_tools:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=reflection_args)]
            )
        c = dict(reflection_args["skill_correction"])
        c.pop("quote")
        return ModelResponse(parts=[ToolCallPart(tool_name="propose_skill_correction", args=c)])

    return FunctionModel(_respond)


@pytest.fixture()
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "test_model", True)
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "reflection_enabled", True)
    monkeypatch.setattr(settings, "reflection_skill_corrections", True)
    monkeypatch.setattr(settings, "reflection_min_transcript_chars", 0)


async def _agent(packs_held=("graph-propose", "skill-propose"), *, skill=None) -> str:
    agent_id = f"skillcorr-x-{uuid.uuid4().hex[:8]}"
    await repo.hire_agent(
        agent_id,
        "Skill correction fixture",
        "test fixture",
        "You are a fixture that files expense reports.",
        list(packs_held),
        template="analyst",
        deviations="test fixture: not consultable — exists only to exercise reflection",
    )
    if skill:
        await repo.grant_skill(agent_id, skill)
    return agent_id


async def _reflect(agent_id: str, run_state: dict, args: dict) -> tuple[str, list[dict]]:
    sid = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_conversation_session(sid, agent_id)
    await repo.update_session_run_state(sid, run_state)
    since = await repo.latest_event_id()
    await reflection.reflect_on_close(sid, agent_id, model=_model(args))
    return sid, await repo.list_events(since_id=since)


def _created(evts: list[dict], sid: str) -> list[dict]:
    return [
        e
        for e in evts
        if e["kind"] == "proposal.created" and e["payload"].get("source_session_id") == sid
    ]


def _dropped(evts: list[dict], sid: str) -> list[dict]:
    return [
        e for e in evts if e["kind"] == "reflection.skill_correction_dropped" and e["ref_id"] == sid
    ]


def test_loaded_skills_needs_a_call_and_its_return():
    state = _transcript("sk-a")
    state["messages"].insert(
        1,
        {
            "parts": [
                {
                    "part_kind": "tool-call",
                    "tool_name": "load_capability",
                    "args": '{"id": "sk-never-returned"}',
                    "tool_call_id": "c0",
                }
            ]
        },
    )
    assert reflection.loaded_skills(state) == ["sk-a"]
    assert reflection.loaded_skills(_transcript("sk-a", loaded=False)) == []


def test_the_quote_corpus_leaves_out_the_skill_text_itself():
    corpus = reflection._quote_corpus(_transcript("sk-a"))
    assert any(QUOTE in t for t in corpus)
    assert not any("Fill in the amount" in t for t in corpus)


def test_the_reflection_prompt_carries_the_rules_and_the_current_text():
    prompt = reflection._distill_prompt("charter", "transcript", {"sk-a": GUIDANCE})
    for rule in (
        "AT MOST ONE",
        "most sessions produce none",
        "ONE contiguous span",
        "a fragment, a paraphrase, an ellipsis or pieces joined",
        "Never your own words",
        "at least four words",
        "never for one incident",
        "negative claim about a tool",
        "outage",
        "Write the lesson, not the log",
    ):
        assert rule in prompt, rule
    assert "1. Fill in the amount and the cost centre." in prompt
    # No loaded skill, no correction section.
    assert "SKILL CORRECTION" not in reflection._distill_prompt("c", "t", {})


@needs_pg
async def test_a_loaded_skill_and_a_correction_yield_one_gated_proposal(pinned, skill):
    agent_id = await _agent(skill=skill)
    sid, evts = await _reflect(
        agent_id, _transcript(skill), {"episodes": [], "skill_correction": _correction(skill)}
    )
    created = _created(evts, sid)
    assert len(created) == 1 and created[0]["payload"]["skill_correction"] is True
    row = await repo.load_proposal(created[0]["ref_id"])
    assert row["agent_id"] == agent_id and row["status"] == "AWAITING_HUMAN"
    args = row["actions"][0]["arguments"]
    assert row["actions"][0]["capability"].startswith("skill.doc_add")
    assert args["base_version"] == 1 and "2. Attach the receipt." in args["content"]
    quoted = [e for e in row["evidence"] if e.get("claim") == QUOTE]
    assert len(quoted) == 1 and quoted[0]["claim_matches_source"] is True
    assert quoted[0]["source_ref"] == f"session:{sid}"
    assert _dropped(evts, sid) == []


@needs_pg
async def test_a_skill_the_session_did_not_load_yields_nothing(pinned, skill):
    agent_id = await _agent(skill=skill)
    sid, evts = await _reflect(
        agent_id,
        _transcript(skill, loaded=False),
        {"episodes": [], "skill_correction": _correction(skill)},
    )
    assert _created(evts, sid) == []


@needs_pg
async def test_a_correction_naming_another_skill_is_dropped_and_recorded(pinned, skill):
    agent_id = await _agent(skill=skill)
    other = _correction("cc-correction-test-elsewhere")
    sid, evts = await _reflect(
        agent_id, _transcript(skill), {"episodes": [], "skill_correction": other}
    )
    assert _created(evts, sid) == []
    [d] = _dropped(evts, sid)
    assert "did not load" in d["payload"]["reason"]


@needs_pg
@pytest.mark.parametrize(
    "over, reason",
    [
        ({"find": "3. Approve it.\n"}, "not found"),
        ({"find": "Keep the report title short."}, "occurs 2 times"),
        ({"quote": "always attach the receipt ... every time"}, "verbatim"),
        ({"quote": "1. Fill in the amount and the cost centre."}, "verbatim"),
    ],
)
async def test_a_span_or_quote_that_fails_is_dropped_and_recorded(pinned, skill, over, reason):
    agent_id = await _agent(skill=skill)
    sid, evts = await _reflect(
        agent_id,
        _transcript(skill),
        {"episodes": [], "skill_correction": _correction(skill, **over)},
    )
    assert _created(evts, sid) == []
    [d] = _dropped(evts, sid)
    assert reason in d["payload"]["reason"]
    assert d["payload"]["skill_id"] == skill


@needs_pg
async def test_the_off_switch_proposes_no_correction(pinned, monkeypatch, skill):
    monkeypatch.setattr(settings, "reflection_skill_corrections", False)
    agent_id = await _agent(skill=skill)
    sid, evts = await _reflect(
        agent_id, _transcript(skill), {"episodes": [], "skill_correction": _correction(skill)}
    )
    assert _created(evts, sid) == [] and _dropped(evts, sid) == []


@needs_pg
async def test_a_revoked_pack_proposes_no_correction(pinned, skill):
    agent_id = await _agent(skill=skill)
    assert await repo.revoke_pack(agent_id, "skill-propose", "test")
    sid, evts = await _reflect(
        agent_id, _transcript(skill), {"episodes": [], "skill_correction": _correction(skill)}
    )
    assert _created(evts, sid) == [] and _dropped(evts, sid) == []


@needs_pg
async def test_an_agent_without_graph_propose_still_corrects_a_loaded_skill(pinned, skill):
    """The one widening of reflection's scope limit: a skill-propose holder
    whose session loaded a skill buys the distill call for the correction
    alone — any lessons it returns are not proposed."""
    agent_id = await _agent(("skill-propose", "task-create"), skill=skill)
    lesson = {"name": "n", "episode_body": "b", "scope": "shared", "source_description": "s"}
    sid, evts = await _reflect(
        agent_id, _transcript(skill), {"episodes": [lesson], "skill_correction": _correction(skill)}
    )
    created = _created(evts, sid)
    assert len(created) == 1 and created[0]["payload"]["skill_correction"] is True
    # Without a loaded skill it reflects not at all, as before.
    sid2, evts2 = await _reflect(
        agent_id, _transcript(skill, loaded=False), {"episodes": [lesson], "skill_correction": None}
    )
    assert [e for e in evts2 if e["ref_id"] == sid2] == []


# --- review fixes (v2.87.0 review) ---------------------------------------------


@needs_pg
async def test_a_correction_keeps_the_source_and_capture_time_of_its_base(skill):
    args = await _proposed_args(skill)
    assert args["source_url"] == "https://example.com/expenses"
    assert datetime.fromisoformat(args["captured_at"]) == CAPTURED
    await executor._skill_doc_add(args, "operator", "jira-expert")
    current = next(d for d in await repo.list_skill_docs(skill) if d["doc_key"] == "guidance")
    assert current["version"] == 2
    assert current["source_url"] == "https://example.com/expenses"
    assert current["captured_at"] == CAPTURED


@needs_pg
async def test_an_ungranted_skill_and_a_missing_one_get_the_same_refusal(skill):
    from central_command.runtime.skill_corrections import NOT_GRANTED

    other = await _agent(skill=None)  # holds no skill grant at all
    ungranted = await tools_mod.propose_skill_correction(
        _Ctx(other, loaded=(skill,)),
        skill_id=skill,
        find="2. Submit the report.\n",
        replace="x",
        why="w",
    )
    missing = await tools_mod.propose_skill_correction(
        _Ctx(other, loaded=("cc-correction-test-missing",)),
        skill_id="cc-correction-test-missing",
        find="2. Submit the report.\n",
        replace="x",
        why="w",
    )
    assert ungranted == missing == NOT_GRANTED
    # The span is never matched for an ungranted skill: a span that would miss
    # gets the same sentence, so nothing about the text leaks.
    assert (
        await tools_mod.propose_skill_correction(
            _Ctx(other, loaded=(skill,)), skill_id=skill, find="no such text", replace="x", why="w"
        )
        == NOT_GRANTED
    )


@needs_pg
async def test_a_granted_skill_not_loaded_this_session_is_refused(skill):
    out = await tools_mod.propose_skill_correction(
        _Ctx(), skill_id=skill, find="2. Submit the report.\n", replace="x", why="w"
    )
    assert out.startswith("refused:") and "not loaded" in out
    # A reference doc key needs the guidance load too.
    out = await tools_mod.propose_skill_correction(
        _Ctx(), skill_id=skill, doc_key="api", find="a", replace="b", why="w"
    )
    assert "not loaded" in out


@needs_pg
async def test_a_park_through_the_seam_drops_a_model_written_base_version():
    """Through `park_proposal` itself: a model-authored skill.doc_add loses its
    base_version, a tool-built one keeps it."""
    from pydantic_ai import DeferredToolRequests
    from pydantic_ai.messages import ToolCallPart

    from central_command.runtime.proposals import park_proposal

    agent_id = await _agent(skill=None)
    body = {
        "intent": "i",
        "expected_effect": "e",
        "evidence": [],
        "actions": [
            {
                "capability": "skill.doc_add@v1",
                "arguments": {
                    "skill_id": "s",
                    "doc_key": "guidance",
                    "content": "c",
                    "base_version": 7,
                },
                "target_ref": {"system": "central_command", "id": "s"},
                "reversibility": "reversible",
            }
        ],
    }

    class _Result:
        def __init__(self, call, metadata):
            self.output = DeferredToolRequests(calls=[call], metadata=metadata)

        def all_messages(self):
            return []

    for tool, args, metadata, kept in (
        ("propose_action", {"proposal": body}, {}, False),
        ("propose_skill_correction", {"skill_id": "s"}, None, True),
    ):
        call = ToolCallPart(tool_name=tool, args=args, tool_call_id="t1")
        meta = {"t1": {"proposal": body}} if metadata is None else {}
        sid = "sess_" + uuid.uuid4().hex[:12]
        await repo.create_conversation_session(sid, agent_id)
        parked = await park_proposal(
            session_id=sid, agent_id=agent_id, result=_Result(call, meta), call=call
        )
        row = await repo.load_proposal(parked["proposal_id"])
        assert ("base_version" in row["actions"][0]["arguments"]) is kept, tool


@needs_pg
async def test_the_loser_of_a_race_names_the_real_new_version(skill):
    """Two corrections stamped with the same base: one lands, the other fails
    naming v2 — never v0 (the winner flipped `is_current` under it)."""

    async def write(text):
        try:
            return await repo.add_skill_doc(
                skill, "guidance", "guidance", "t", text, expected_version=1
            )
        except repo.StaleSkillDoc as e:
            return e

    outcomes = await asyncio.gather(write("one"), write("two"))
    stale = [o for o in outcomes if isinstance(o, repo.StaleSkillDoc)]
    assert len(stale) == 1 and stale[0].expected == 1 and stale[0].actual == 2
    # A retired key with nothing newer reads as gone (0), not as the base.
    await repo.retire_skill_doc(skill, "guidance", "test")
    with pytest.raises(repo.StaleSkillDoc) as e:
        await repo.add_skill_doc(skill, "guidance", "guidance", "t", "x", expected_version=2)
    assert e.value.actual == 0


def test_a_crlf_document_matches_an_lf_span_and_keeps_its_endings():
    doc = GUIDANCE.replace("\n", "\r\n")
    out = apply_exact_span(
        doc,
        "1. Fill in the amount and the cost centre.\n2. Submit",
        "1. Fill in the amount.\n2. Submit",
    )
    assert "1. Fill in the amount.\r\n2. Submit" in out
    assert "\n" not in out.replace("\r\n", "")
    mixed = "a\r\nb\nc\n"
    with pytest.raises(SpanMismatch) as e:
        apply_exact_span(mixed, "a\nb", "x")
    assert "line endings" in str(e.value)


@pytest.mark.parametrize(
    "quote, ok",
    [
        ("receipt", False),  # one word
        ("attach the receipt", False),  # under four words
        ("always attach the receipt before you submit", True),
    ],
)
def test_the_quote_floor(quote, ok):
    assert reflection.quote_clears_floor(quote) is ok


def test_the_quote_corpus_is_only_what_the_operator_said_or_a_tool_returned():
    state = _transcript("sk-a")
    state["messages"] += [
        {
            "parts": [
                {
                    "part_kind": "tool-call",
                    "tool_name": "search_my_sessions",
                    "args": {"query": "receipt"},
                    "tool_call_id": "r1",
                }
            ]
        },
        {
            "parts": [
                {
                    "part_kind": "tool-return",
                    "tool_name": "search_my_sessions",
                    "content": "recalled: receipts must be scanned in colour always",
                    "tool_call_id": "r1",
                }
            ]
        },
        {
            "parts": [
                {
                    "part_kind": "tool-return",
                    "tool_name": "jira_get_issue",
                    "content": "error: the receipt attachment is missing from the report",
                    "tool_call_id": "j1",
                }
            ]
        },
    ]
    corpus = reflection._quote_corpus(state)
    assert any(QUOTE in t for t in corpus)  # the operator
    assert any("receipt attachment is missing" in t for t in corpus)  # a tool
    assert not any("I submitted the report." in t for t in corpus)  # its own words
    assert not any("scanned in colour" in t for t in corpus)  # a recall
    assert not any("Fill in the amount" in t for t in corpus)  # the skill


@needs_pg
@pytest.mark.parametrize(
    "quote, reason",
    [
        ("receipt", "shorter than"),
        ("I submitted the report to the expense tool", "verbatim"),
        ("receipts must be scanned in colour always", "verbatim"),
    ],
)
async def test_a_weak_or_self_sourced_quote_is_dropped(pinned, skill, quote, reason):
    state = _transcript(skill)
    state["messages"] += [
        {"parts": [{"part_kind": "text", "content": "I submitted the report to the expense tool"}]},
        {
            "parts": [
                {
                    "part_kind": "tool-return",
                    "tool_name": "search_my_sessions",
                    "content": "receipts must be scanned in colour always",
                    "tool_call_id": "r1",
                }
            ]
        },
    ]
    agent_id = await _agent(skill=skill)
    sid, evts = await _reflect(
        agent_id, state, {"episodes": [], "skill_correction": _correction(skill, quote=quote)}
    )
    assert _created(evts, sid) == []
    [d] = _dropped(evts, sid)
    assert reason in d["payload"]["reason"]


@needs_pg
async def test_a_drafting_model_that_rewords_why_parks_nothing(pinned, skill):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    args = {"episodes": [], "skill_correction": _correction(skill)}

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        if info.output_tools:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)]
            )
        c = dict(args["skill_correction"])
        c.pop("quote")
        c["why"] = "Receipts are optional, but nice to have."
        return ModelResponse(parts=[ToolCallPart(tool_name="propose_skill_correction", args=c)])

    agent_id = await _agent(skill=skill)
    sid = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_conversation_session(sid, agent_id)
    await repo.update_session_run_state(sid, _transcript(skill))
    since = await repo.latest_event_id()
    await reflection.reflect_on_close(sid, agent_id, model=FunctionModel(_respond))
    evts = await repo.list_events(since_id=since)
    assert _created(evts, sid) == []
    [d] = _dropped(evts, sid)
    assert "not the correction distilled" in d["payload"]["reason"]


@needs_pg
async def test_an_agent_without_the_skill_grant_corrects_nothing_at_close(pinned, skill):
    agent_id = await _agent(skill=None)
    sid, evts = await _reflect(
        agent_id, _transcript(skill), {"episodes": [], "skill_correction": _correction(skill)}
    )
    assert _created(evts, sid) == []
    [d] = _dropped(evts, sid)
    assert "granted to you" in d["payload"]["reason"]
