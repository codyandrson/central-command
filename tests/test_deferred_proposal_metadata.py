"""A tool that builds its proposal in CODE parks on every run path.

`propose_skill_correction`, `propose_skill_doc`, `propose_bulk_dismiss` and
the mcp family defer with `CallDeferred(metadata={"proposal": ...})`: their
`call.args` hold the tool's own arguments, not a Proposal. A path that read
the proposal from the args alone raised inside `Proposal.model_validate` and
left its session RUNNING. Since v2.87.0 gave the coach and the wiki agent
`skill-propose`, that reached the steward, the wiki agent and the coach
(found in review). Every run path now reads through
`durable.deferred_proposal`, and the walk below keeps it that way.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

from central_command.db import repo
from tests.conftest import needs_pg

SRC = Path(__file__).resolve().parents[1] / "central_command"

# `proposal_from_call(...)` sites that may omit `metadata=`, each with why.
# Empty: every caller outside `runtime/durable.py` has a result to read it from.
ALLOWED_WITHOUT_METADATA: dict[str, str] = {}

GUIDANCE = "# Expense reports\n1. Fill in the amount.\n2. Submit the report.\n"


def _sources() -> dict[str, str]:
    return {
        p.relative_to(SRC).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(SRC.rglob("*.py"))
    }


def _calls_without_metadata(sources: dict[str, str]) -> list[str]:
    found = []
    for rel, text in sources.items():
        if rel == "runtime/durable.py":
            continue
        tree = ast.parse(text)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(fn):
                if (
                    isinstance(n, ast.Call)
                    and getattr(n.func, "id", getattr(n.func, "attr", None)) == "proposal_from_call"
                    and not any(k.arg == "metadata" for k in n.keywords)
                    and len(n.args) < 2
                ):
                    found.append(f"{rel}:{fn.name}")
    return sorted(set(found))


def test_no_proposal_from_call_outside_durable_omits_metadata():
    bad = [s for s in _calls_without_metadata(_sources()) if s not in ALLOWED_WITHOUT_METADATA]
    assert bad == [], (
        "these read a deferred call's proposal from its args alone — a tool that "
        f"builds its proposal in code would crash them; use durable.deferred_proposal: {bad}"
    )


def test_the_walk_finds_a_site_that_omits_it():
    sources = {
        "runtime/new_path.py": "def land(call):\n    return proposal_from_call(call)\n",
        "runtime/ok_path.py": "def land(call, m):\n    return proposal_from_call(call, metadata=m)\n",
    }
    assert _calls_without_metadata(sources) == ["runtime/new_path.py:land"]


def _correcting_model(skill_id: str):
    """Load the skill's guidance, then propose a correction to it."""
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not any(p.tool_name == "load_capability" for p in returns):
            return ModelResponse(
                parts=[ToolCallPart(tool_name="load_capability", args={"id": skill_id})]
            )
        if any(p.tool_name == "propose_skill_correction" for p in returns):
            return ModelResponse(parts=[TextPart(content="the correction was refused")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="propose_skill_correction",
                    args={
                        "skill_id": skill_id,
                        "find": "2. Submit the report.\n",
                        "replace": "2. Attach the receipt.\n3. Submit the report.\n",
                        "why": "A report without its receipt is sent back.",
                    },
                )
            ]
        )

    return FunctionModel(_respond)


@pytest.fixture()
async def skill_for():
    made: list[str] = []

    async def _make(agent_id: str) -> str:
        skill_id = f"cc-correction-path-{uuid.uuid4().hex[:8]}"
        await repo.create_skill(skill_id, "Expense reports", "how to file an expense report")
        await repo.add_skill_doc(skill_id, "guidance", "guidance", "Expense reports", GUIDANCE)
        await repo.grant_skill(agent_id, skill_id)
        made.append(skill_id)
        return skill_id

    yield _make
    conn = await repo._conn()
    try:
        await conn.execute("delete from skill where id = any($1)", made)
    finally:
        await conn.close()


async def _assert_parked_correction(out: dict, agent_id: str, skill_id: str) -> None:
    assert out["deferred"] is True, out
    row = await repo.load_proposal(out["proposal_id"])
    assert row["agent_id"] == agent_id
    action = row["actions"][0]
    assert action["capability"].startswith("skill.doc_add")
    assert action["arguments"]["skill_id"] == skill_id
    assert "2. Attach the receipt." in action["arguments"]["content"]
    assert await repo.session_status(out["session_id"]) == "AWAITING_HUMAN"


@needs_pg
async def test_the_steward_parks_a_code_built_correction(skill_for):
    from central_command.runtime import steward
    from central_command.runtime.steward_profile import AGENT_ID

    await steward.ensure_registered()
    skill_id = await skill_for(AGENT_ID)
    out = await steward.run_document(
        "a document about expenses",
        source_text="a document about expenses",
        model=_correcting_model(skill_id),
    )
    await _assert_parked_correction(out, AGENT_ID, skill_id)


@needs_pg
async def test_the_wiki_agent_parks_a_code_built_correction(skill_for):
    from central_command.runtime import wiki_agent

    skill_id = await skill_for(wiki_agent.AGENT_ID)
    out = await wiki_agent.run_repair("repair the expenses page", model=_correcting_model(skill_id))
    await _assert_parked_correction(out, wiki_agent.AGENT_ID, skill_id)


@needs_pg
async def test_the_coach_parks_a_code_built_correction(skill_for):
    from central_command import events
    from central_command.runtime.run import run_coach

    target = "coach-corr-" + uuid.uuid4().hex[:6]
    await repo.upsert_agent(target, target, "demo", "c")
    await repo.save_charter_version(
        target, "CHARTER for a coaching test.", "seed", created_by="operator"
    )
    note = await events.emit(
        "agent.coach_requested",
        ref_id=target,
        payload={"signal_ids": [], "note": "Look at receipts."},
        actor="operator",
    )
    skill_id = await skill_for("coach")
    out = await run_coach(
        target,
        model=_correcting_model(skill_id),
        note="Look at receipts.",
        note_event_id=note["id"],
    )
    await _assert_parked_correction(out, "coach", skill_id)
