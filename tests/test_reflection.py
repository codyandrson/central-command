"""Session-close reflection (self-directed learning §2, slice 1 — SHADOW).

Everything reflection produces stays human-gated: lessons become ordinary
`graph.add_episode` proposals through the ONE park path, and the coaching
self-nomination is a signal, not a charter edit. These tests hold the shadow
guarantees: demo mode produces nothing, the per-session bound notes its
overflow rather than dropping it, and a nomination's quote is CHECKED (True or
False, never absent).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.runtime import converse, reflection
from central_command.runtime.reflection import ReflectedEpisode, bound_episodes
from tests.conftest import needs_pg

AGENT = "reflecttest-x"
QUOTE = "stop proposing dates you cannot source"
TRANSCRIPT = {
    "messages": [
        {"parts": [{"part_kind": "user-prompt", "content": "please set DEMO-1's due date"}]},
        {"parts": [{"part_kind": "text",
                    "content": f"the operator told me to {QUOTE}, so I asked first"}]},
    ]
}


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    # `demo_mode=True` is not optional: every path here can reach
    # `resolve_model`, and with a real key in .env an unpinned test calls
    # Claude for real and spends money.
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "reflection_enabled", True)
    # The scope floor (2026-08-05) would skip the tiny fixture transcript and
    # with it every mechanism test; the floor has its own test below.
    monkeypatch.setattr(settings, "reflection_min_transcript_chars", 0)


async def _fixture_agent() -> str:
    """A freshly hired agent holding graph-propose and nothing else.

    A new id per test rather than a shared one that gets deleted: sessions
    reference the agent row (and are never destroyed), so the delete would fail
    the moment a sibling test had already run a reflection under it.
    """
    agent_id = f"{AGENT}-{uuid.uuid4().hex[:8]}"
    await repo.hire_agent(
        agent_id, "Reflection fixture", "test fixture",
        "You are a fixture that records what it learns.", ["graph-propose"],
        template="analyst",
        # Uniform management applies to fixtures too: a non-consultable agent
        # without a recorded reason fails test_governance's roster walk — and
        # these rows persist (their sessions FK them, sessions are never
        # destroyed), so the walk meets them on every future full-suite run.
        deviations="test fixture: not consultable — exists only to exercise reflection",
    )
    return agent_id


async def _closed_session(agent_id: str, run_state: dict | None = TRANSCRIPT) -> str:
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_conversation_session(session_id, agent_id)
    if run_state is not None:
        await repo.update_session_run_state(session_id, run_state)
    return session_id


def _reflecting_model(reflection_args: dict):
    """One FunctionModel driving BOTH reflection runs. The distillation agent
    has a structured output tool; the per-episode proposer has none — which is
    what tells the two apart without the test knowing either prompt."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def _respond(messages, info: AgentInfo) -> ModelResponse:
        if info.output_tools:
            return ModelResponse(parts=[
                ToolCallPart(tool_name=info.output_tools[0].name, args=reflection_args),
            ])
        # The proposer run: echo the assigned episode back as ONE gated action.
        episode = reflection_args["episodes"][0]
        return ModelResponse(parts=[ToolCallPart(
            tool_name="propose_action",
            args={"proposal": {
                "intent": f"Record the lesson: {episode['name']}",
                "actions": [{
                    "capability": "graph.add_episode",
                    "arguments": {
                        "name": episode["name"],
                        "episode_body": episode["episode_body"],
                        "source_description": episode["source_description"],
                        "scope": episode["scope"],
                    },
                    "target_ref": {"system": "graphiti", "id": "central_command",
                                   "read_version": "unknown"},
                    "reversibility": "reversible",
                }],
                "evidence": [{
                    "kind": "record",
                    "source_ref": "the closed session",
                    "locator": "session transcript",
                    "claim": episode["episode_body"][:20],
                }],
                "expected_effect": "the graph holds the distilled lesson",
            }},
        )])

    return FunctionModel(_respond)


ONE_LESSON = {
    "episodes": [{
        "name": "Ask before dating",
        "episode_body": "Due dates asserted without a source get rejected here.",
        "scope": "private",
        "source_description": "this session",
    }],
    "nomination": None,
}


# --- the shadow guards --------------------------------------------------------


@needs_pg
async def test_demo_mode_and_the_flag_both_schedule_nothing(monkeypatch):
    """Demo mode must produce nothing and never touch a model — the check is
    synchronous in close_session, so no task is created at all. Same for the
    kill switch."""
    called: list[tuple] = []

    async def spy(*a, **k):
        called.append(a)

    monkeypatch.setattr(reflection, "reflect_on_close", spy)
    agent_id = await _fixture_agent()

    sid = await _closed_session(agent_id)
    await converse.close_session(sid, agent_id=agent_id, reason="operator", actor="operator")
    await asyncio.sleep(0)
    assert called == []

    # Live mode with the flag off: still nothing.
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "reflection_enabled", False)
    sid2 = await _closed_session(agent_id)
    await converse.close_session(sid2, agent_id=agent_id, reason="operator", actor="operator")
    await asyncio.sleep(0)
    assert called == []


@needs_pg
async def test_a_dismissed_session_is_never_reflected_on(monkeypatch):
    """Dismiss means "no action needed, don't ask again". A reflection on a
    dismissed session re-proposes what was just dismissed — the loop found live
    2026-08-13. Every other close reason still reflects."""
    called: list[str] = []

    async def spy(session_id, *a, **k):
        called.append(session_id)

    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "reflection_enabled", True)
    monkeypatch.setattr(reflection, "reflect_on_close", spy)
    agent_id = await _fixture_agent()

    sid = await _closed_session(agent_id)
    await converse.close_session(sid, agent_id=agent_id, reason="dismissed", actor="operator")
    await asyncio.sleep(0)
    assert called == []

    sid2 = await _closed_session(agent_id)
    await converse.close_session(sid2, agent_id=agent_id, reason="concluded", actor="operator")
    await asyncio.sleep(0)
    assert called == [sid2]


def test_the_per_session_bound_notes_its_overflow():
    """N is a bound, not a filter: what did not fit is recorded on the last kept
    episode, never silently dropped."""
    episodes = [
        ReflectedEpisode(name=f"e{i}", episode_body="b", scope="shared",
                         source_description="src")
        for i in range(5)
    ]
    kept = bound_episodes(episodes, 3)
    assert [e.name for e in kept] == ["e0", "e1", "e2"]
    assert kept[-1].source_description == "src | overflow: 2 further lesson(s) " \
        "distilled but over the per-session bound"
    # Under the bound nothing is annotated, and the inputs are untouched.
    assert [e.source_description for e in bound_episodes(episodes[:2], 3)] == ["src", "src"]
    assert episodes[2].source_description == "src"


@needs_pg
async def test_a_session_with_no_transcript_writes_nothing():
    """A record that authorises nothing needn't be written."""
    agent_id = await _fixture_agent()
    sid = await _closed_session(agent_id, run_state=None)
    since = await repo.latest_event_id()
    await reflection.reflect_on_close(sid, agent_id, model=_reflecting_model(ONE_LESSON))
    assert [e for e in await repo.list_events(since_id=since) if e["ref_id"] == sid] == []


# --- the two output vocabularies ---------------------------------------------


@needs_pg
async def test_one_lesson_parks_one_scoped_proposal():
    """The episode path end to end: one distilled lesson → its own session →
    ONE gated graph.add_episode carrying the scope the agent chose, announced
    by the one park path (no `proposal.created`, no proposal)."""
    agent_id = await _fixture_agent()
    sid = await _closed_session(agent_id)
    since = await repo.latest_event_id()

    await reflection.reflect_on_close(sid, agent_id, model=_reflecting_model(ONE_LESSON))

    created = [e for e in await repo.list_events(since_id=since)
               if e["kind"] == "proposal.created"
               and e["payload"].get("source_session_id") == sid]
    assert len(created) == 1
    assert created[0]["payload"]["reflection"] is True

    row = await repo.load_proposal(created[0]["ref_id"])
    assert row["agent_id"] == agent_id and row["status"] == "AWAITING_HUMAN"
    action = row["actions"][0]
    assert action["capability"] == "graph.add_episode"
    assert action["arguments"]["scope"] == "private"
    assert action["arguments"]["name"] == "Ask before dating"
    # A reflection proposal is parked, not run: its own session awaits the human.
    assert await repo.session_status(row["session_id"]) == "AWAITING_HUMAN"

    # THE WIRE SHAPE, per convention: the cockpit hand-declares its interfaces
    # over untyped RPC payloads, so a field the gateway never sends is invisible
    # to tsc and every frontend test. The Inbox's scope badge reads
    # `proposals.get` → `routes.get_proposal`; assert on that payload, not just
    # the repo row above.
    from central_command.api.routes import get_proposal

    detail = await get_proposal(created[0]["ref_id"])
    assert detail["actions"][0]["arguments"]["scope"] == "private"


@needs_pg
async def test_the_bound_applies_to_what_actually_gets_proposed(monkeypatch):
    monkeypatch.setattr(settings, "reflection_max_episodes", 1)
    agent_id = await _fixture_agent()
    sid = await _closed_session(agent_id)
    two = {"episodes": ONE_LESSON["episodes"] * 2, "nomination": None}
    since = await repo.latest_event_id()

    await reflection.reflect_on_close(sid, agent_id, model=_reflecting_model(two))

    created = [e for e in await repo.list_events(since_id=since)
               if e["kind"] == "proposal.created"
               and e["payload"].get("source_session_id") == sid]
    assert len(created) == 1


@needs_pg
async def test_an_agent_without_the_grant_reflects_not_at_all():
    """Scope limit (2026-08-05): an agent without graph-propose no longer buys
    a distill call at all — no proposals AND no nomination. The narrowing to
    grant holders is the operator's re-enable condition, not an accident."""
    agent_id = await _fixture_agent()
    assert await repo.revoke_pack(agent_id, "graph-propose", "test")
    sid = await _closed_session(agent_id)
    since = await repo.latest_event_id()

    nominated = {**ONE_LESSON, "nomination": {
        "observation": "I should ask sooner.", "quote": QUOTE, "source_session_id": sid,
    }}
    await reflection.reflect_on_close(sid, agent_id, model=_reflecting_model(nominated))

    assert [e for e in await repo.list_events(since_id=since) if e["ref_id"] == sid] == []


@needs_pg
async def test_a_transcript_below_the_floor_reflects_not_at_all(monkeypatch):
    """Scope limit (2026-08-05): trivial sessions skip reflection entirely."""
    monkeypatch.setattr(settings, "reflection_min_transcript_chars", 100_000)
    agent_id = await _fixture_agent()
    sid = await _closed_session(agent_id)
    since = await repo.latest_event_id()

    await reflection.reflect_on_close(sid, agent_id, model=_reflecting_model(ONE_LESSON))

    assert [e for e in await repo.list_events(since_id=since) if e["ref_id"] == sid] == []


@needs_pg
@pytest.mark.parametrize(
    "quote, wrong_session, expected",
    [
        (QUOTE, False, True),                      # an exact span of the transcript
        ("stop proposing dates, obviously", False, False),  # a misquote
        (QUOTE, True, False),                      # a real quote, wrong session
    ],
)
async def test_a_nomination_quote_is_always_checked(quote, wrong_session, expected):
    """`claim_matches_source: None` means NOT CHECKED — a nomination must never
    read that way, including when it points at a session it never had."""
    agent_id = await _fixture_agent()
    sid = await _closed_session(agent_id)
    since = await repo.latest_event_id()

    payload = {"episodes": [], "nomination": {
        "observation": "I was right to ask before dating.",
        "quote": quote,
        "source_session_id": "sess_elsewhere" if wrong_session else sid,
    }}
    await reflection.reflect_on_close(sid, agent_id, model=_reflecting_model(payload))

    emitted = [e for e in await repo.list_events(since_id=since)
               if e["kind"] == "reflection.self_nomination" and e["ref_id"] == sid]
    assert len(emitted) == 1
    p = emitted[0]["payload"]
    assert p["claim_matches_source"] is expected
    assert p["agent_id"] == agent_id
    assert p["observation"] == "I was right to ask before dating."
    assert p["quote"] == quote


# --- and back into the coaching loop -----------------------------------------


@needs_pg
async def test_a_nomination_becomes_a_coaching_signal():
    """The nomination is EVIDENCE for a coach run, not a charter edit: it lands
    in the same pool the operator curates from, authored by the agent — and
    `_signal_line` must render it (a missing context key is a KeyError that
    blanks the whole picker)."""
    from central_command import events
    from central_command.runtime.run import _signal_line, gather_coaching_signals

    agent_id = await _fixture_agent()
    sid = await _closed_session(agent_id)
    await events.emit(
        "reflection.self_nomination", ref_id=sid,
        payload={"agent_id": agent_id, "observation": "I should ask sooner.",
                 "quote": QUOTE, "source_session_id": sid,
                 "claim_matches_source": True},
        actor=f"agent:{agent_id}",
    )

    signals = await gather_coaching_signals(agent_id)
    mine = [s for s in signals if s["kind"] == "self_reflection"]
    assert len(mine) == 1
    assert mine[0]["author"] == "agent"
    assert mine[0]["feedback"] == "I should ask sooner."
    assert mine[0]["intent"] == sid

    line = _signal_line(0, mine[0])
    assert "nominated yourself for coaching" in line
    assert "YOUR OWN EXACT WORDS" in line  # never labelled as the operator's
    assert "I should ask sooner." in line

    # Another agent's nomination is not this agent's signal.
    assert not [s for s in await gather_coaching_signals("coach")
                if s["kind"] == "self_reflection" and s["event_id"] == mine[0]["event_id"]]
