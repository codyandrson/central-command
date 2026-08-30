"""A tier-3 discussion lane must run under the agent's charter, and must not
be closable by hand while its question is still open.

Found live 2026-08-29 (the onboarding tour): `_open_discussion` seeded the
lane with the agent's question as a `ModelResponse` and nothing else, and
pydantic-ai adds system-prompt parts only when the history is EMPTY — so every
turn of every seeded lane ran with the agent's tools and none of its charter.
The EA told the operator "there is nothing for me to conclude", the operator
closed the chat from the session panel, and the task sat blocked on an OPEN
question whose lane was dead.

Two seams, two pins: the seed carries exactly the prompt a fresh chat would
have persisted, and `end_conversation` refuses a lane whose item is OPEN.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart

from central_command.config import settings
from central_command.db import repo
from central_command.runtime import converse, questions
from central_command.runtime.durable import load_messages
from central_command.runtime.spike_model import make_converse_model
from tests.conftest import needs_pg

AGENT = "jira-expert"  # holds ask-operator, and no attention budget to spend


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)


async def _open_lane(agent_id: str = AGENT) -> dict:
    """What `park_question` does for a tier-3 ask: one paused task session,
    one OPEN item, and the lane `_open_discussion` opens for it."""
    suffix = uuid.uuid4().hex[:8]
    task_session = f"lanetest-task-{suffix}"
    item_id = f"lanetest-item-{suffix}"
    await repo.create_running_session(task_session, agent_id)
    await repo.create_operator_item(
        item_id, "question", task_session, agent_id, "Which project?",
    )
    lane = await questions._open_discussion(
        item_id, agent_id, task_session, "Which project?", "the task names none",
    )
    assert lane is not None
    return {"lane": lane, "item_id": item_id, "task_session": task_session}


def _system_prompt(messages) -> str:
    first = messages[0]
    assert isinstance(first, ModelRequest), "the charter request must come first"
    parts = [p for p in first.parts if isinstance(p, SystemPromptPart)]
    assert parts, "the seeded lane carries no system prompt"
    return "\n\n".join(p.content for p in parts)


def _without_stamp(prompt: str) -> str:
    # `build_charter` ends with a minute-resolution time stamp; two builds a
    # minute apart differ only there.
    return prompt.split("\n\nToday's date is", 1)[0]


@needs_pg
async def test_a_seeded_lane_carries_the_same_charter_a_fresh_chat_gets():
    ids = await _open_lane()
    seeded = load_messages(await repo.get_session_run_state(ids["lane"]))
    assert isinstance(seeded[1], ModelResponse), "the question follows the charter"

    fresh = await converse.start_conversation(AGENT, "hello", model=make_converse_model())
    persisted = load_messages(await repo.get_session_run_state(fresh["session_id"]))

    assert _without_stamp(_system_prompt(seeded)) == _without_stamp(_system_prompt(persisted))
    # The pack guidance the tour ran without.
    from central_command.runtime import packs as packs_mod

    assert packs_mod.PACKS["ask-operator"].guidance in _system_prompt(seeded)


@needs_pg
async def test_a_seeded_lane_takes_a_turn_on_that_history():
    """pydantic-ai must accept charter-request → question-response → operator
    turn as a history; a shape it rejected would fail the lane's FIRST turn."""
    ids = await _open_lane()
    out = await converse.conversation_turn(ids["lane"], "use TASKS", model=make_converse_model())
    assert out["session_id"] == ids["lane"]
    history = load_messages(await repo.get_session_run_state(ids["lane"]))
    # Still exactly one charter, at the front — the turn did not add a second.
    charters = [m for m in history
                if isinstance(m, ModelRequest)
                and any(isinstance(p, SystemPromptPart) for p in m.parts)]
    assert charters == [history[0]]


@needs_pg
async def test_closing_a_lane_by_hand_is_refused_while_its_question_is_open(monkeypatch):
    ids = await _open_lane()
    with pytest.raises(ValueError, match="open question"):
        await converse.end_conversation(ids["lane"])
    assert await repo.session_status(ids["lane"]) == "AWAITING_OPERATOR"

    # Answering the item is the door: the control plane closes the lane with
    # the answer, and a second close is the usual quiet no-op.
    await repo.answer_operator_item(ids["item_id"], "TASKS", None)
    assert (await converse.end_conversation(ids["lane"]))["status"] == "DONE"
