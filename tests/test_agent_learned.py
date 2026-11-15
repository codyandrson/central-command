"""The one-agent "what has this agent learned" read (`GET /api/agents/{id}/learned`),
behind the cockpit's workspace Memory tab.

Held here: the two sections no other endpoint returns for ONE agent — the
skill document versions the Executor wrote from this agent's approved
proposals (and nobody else's), and this agent's proposals of the learning
kinds still awaiting a decision — with the wire shape the cockpit reads, a 404
for an unknown agent, and no write path on the route.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from central_command.api import routes
from central_command.db import repo
from tests.conftest import needs_pg

pytestmark = needs_pg


def _tag() -> str:
    return uuid.uuid4().hex[:8]


async def _agent(agent_id: str) -> None:
    conn = await repo._conn()
    try:
        await conn.execute(
            "insert into agent (id, name, model, charter, role, status) "
            "values ($1, $1, '', '', 'r', 'ACTIVE')", agent_id)
    finally:
        await conn.close()


async def _proposal(agent_id: str, session_id: str, capability: str, args: dict,
                    status: str = "AWAITING_HUMAN") -> str:
    pid = "prop_" + uuid.uuid4().hex[:12]
    await repo.save_proposal(
        proposal_id=pid, session_id=session_id, agent_id=agent_id,
        intent=f"intent for {capability}", expected_effect="e",
        idempotency_key=f"{pid}:0", evidence=[], status=status,
        actions=[{"capability": capability, "arguments": args,
                  "target_ref": {"system": "central_command", "id": "x"},
                  "reversibility": "reversible"}],
    )
    return pid


@pytest.fixture()
async def world():
    """An agent, another agent, and everything a test makes under them, removed
    afterwards (tests may delete; product code never does)."""
    t = _tag()
    mine, other = f"learned-a-{t}", f"learned-b-{t}"
    await _agent(mine)
    await _agent(other)
    skills = [f"learned-skill-{t}-1", f"learned-skill-{t}-2"]
    for s in skills:
        await repo.create_skill(s, f"Skill {s}", "a routing line")
    ctx = {"mine": mine, "other": other, "skills": skills, "sessions": {}}
    for who in (mine, other):
        sid = "sess_" + uuid.uuid4().hex[:12]
        await repo.create_conversation_session(sid, who)
        ctx["sessions"][who] = sid
    yield ctx
    conn = await repo._conn()
    try:
        await conn.execute("delete from proposal where agent_id = any($1)", [mine, other])
        await conn.execute("delete from skill where id = any($1)", skills)  # cascades skill_doc
        await conn.execute("delete from session where id = any($1)",
                           list(ctx["sessions"].values()))
        await conn.execute("delete from agent where id = any($1)", [mine, other])
    finally:
        await conn.close()


async def test_an_unknown_agent_is_a_404():
    with pytest.raises(HTTPException) as e:
        await routes.list_agent_learned("no-such-agent-" + _tag())
    assert e.value.status_code == 404


async def test_a_fresh_agent_has_empty_sections():
    aid = f"learned-empty-{_tag()}"
    await _agent(aid)
    try:
        out = jsonable_encoder(await routes.list_agent_learned(aid))
        assert out == {"agent_id": aid, "skill_docs": [], "pending": []}
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from agent where id = $1", aid)
        finally:
            await conn.close()


async def test_skill_docs_are_only_those_this_agent_wrote_newest_first_without_content(world):
    mine, other = world["mine"], world["other"]
    s1, s2 = world["skills"]
    await repo.add_skill_doc(s1, "guidance", "guidance", "Guidance", "first body",
                             added_by=f"agent:{mine}")
    await repo.add_skill_doc(s1, "guidance", "guidance", "Guidance", "second body",
                             added_by=f"agent:{mine}")
    await repo.add_skill_doc(s2, "ref-a", "reference", "Ref A", "reference body",
                             describes="Some API", added_by=f"agent:{other}")
    await repo.add_skill_doc(s2, "guidance", "guidance", "Guidance", "operator body",
                             added_by="operator")

    out = jsonable_encoder(await routes.list_agent_learned(mine))
    docs = out["skill_docs"]
    assert [(d["skill_id"], d["doc_key"], d["version"], d["is_current"]) for d in docs] == [
        (s1, "guidance", 2, True), (s1, "guidance", 1, False)]
    assert docs[0]["skill_title"] == f"Skill {s1}" and docs[0]["skill_status"] == "ACTIVE"
    assert all("content" not in d for d in docs)

    theirs = jsonable_encoder(await routes.list_agent_learned(other))["skill_docs"]
    assert [(d["skill_id"], d["doc_key"], d["describes"]) for d in theirs] == [
        (s2, "ref-a", "Some API")]


async def test_pending_lists_only_this_agents_open_learning_proposals(world):
    mine, other = world["mine"], world["other"]
    sess = world["sessions"]
    note = await _proposal(mine, sess[mine], "memory.note_add@v1",
                           {"scope": "operator", "text": "The operator prefers ISO dates."})
    retire = await _proposal(mine, sess[mine], "memory.note_retire@v1",
                             {"note_id": "note_abc", "reason": "no longer true"})
    skill = await _proposal(mine, sess[mine], "skill.doc_add@v1",
                            {"skill_id": "jira", "doc_key": "guidance"})
    episode = await _proposal(mine, sess[mine], "graph.add_episode@v1",
                              {"name": "Doe visit", "scope": "private"})
    # Not listed: another agent's, a decided one, and a capability outside the five.
    await _proposal(other, sess[other], "memory.note_add@v1", {"scope": "private", "text": "x"})
    await _proposal(mine, sess[mine], "memory.note_add@v1", {"scope": "private", "text": "done"},
                    status="EXECUTED")
    await _proposal(mine, sess[mine], "jira.update_issue@v1", {"key": "DOE-1"})

    out = jsonable_encoder(await routes.list_agent_learned(mine))
    got = {p["id"]: p for p in out["pending"]}
    assert set(got) == {note, retire, skill, episode}
    n = got[note]
    assert n["status"] == "AWAITING_HUMAN" and n["session_id"] == sess[mine]
    assert n["created_at"] and n["intent"] == "intent for memory.note_add@v1"
    assert n["actions"] == [{"capability": "memory.note_add",
                             "label": "operator note: The operator prefers ISO dates."}]
    assert got[retire]["actions"][0]["label"] == "retire note note_abc: no longer true"
    assert got[skill]["actions"][0]["label"] == "skill jira / guidance"
    assert got[episode]["actions"][0] == {"capability": "graph.add_episode",
                                          "label": "private episode: Doe visit"}


async def test_a_long_note_label_is_clipped(world):
    mine = world["mine"]
    await _proposal(mine, world["sessions"][mine], "memory.note_add@v1",
                    {"scope": "private", "text": "word " * 100})
    [p] = jsonable_encoder(await routes.list_agent_learned(mine))["pending"]
    label = p["actions"][0]["label"]
    assert label.startswith("private note: word word") and label.endswith("…")
    assert len(label) <= len("private note: ") + 140


def test_the_route_is_get_only():
    methods = {m for r in routes.router.routes
               if getattr(r, "path", "").endswith("/agents/{agent_id}/learned")
               for m in r.methods}
    assert methods == {"GET"}
