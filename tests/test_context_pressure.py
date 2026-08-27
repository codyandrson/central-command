"""Context management, slice 1: the archived/live split and the pressure signal.

Nothing here changes what a model sees. What is under test is that (a) a
`run_state` writer can no longer destroy a key it did not mention — the property
the whole split rests on, since `archived` is written by one path and `messages`
by five — and (b) a session approaching the context window says so on the event
log, once per meaningful step rather than once per turn.

No DDL: `session.run_state` is jsonb and `archived` is a KEY in it, so a fresh
database and the running one already have everything this needs.
"""

from __future__ import annotations

import uuid

import pytest

from central_command.config import settings
from central_command.db import repo
from central_command.runtime import context
from central_command.runtime.durable import full_messages, simplify_messages
from tests.conftest import needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    # Nothing here resolves a model, but `check_pressure` reads demo_mode and
    # the suite must never reach a provider.
    monkeypatch.setattr(settings, "demo_mode", False)
    context._last_fraction.clear()
    context._windows.clear()
    yield
    context._last_fraction.clear()
    context._windows.clear()


def _pin_window(tokens: int) -> None:
    """Pin the discovered window for the model the spine addresses, so no test
    reaches the proxy."""
    context._windows[(settings.default_model or "").split(":", 1)[-1]] = tokens


async def _a_session() -> str:
    """A session row of this test's own — assertions stay scoped to it."""
    agent_id = "ctx-test-agent"
    await repo.upsert_agent(agent_id, "Context Test", "ctx", "")
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(session_id, agent_id)
    return session_id


# --- the merge ----------------------------------------------------------------


@needs_pg
async def test_a_run_state_write_preserves_keys_it_did_not_mention():
    """The property the split needs: `update_session_run_state({'messages': …})`
    must not take `archived` (or a pending marker) down with it."""
    session_id = await _a_session()
    await repo.update_session_run_state(session_id, {
        "messages": [{"kind": "request", "parts": []}],
        "archived": [{"kind": "request", "parts": [{"part_kind": "system-prompt",
                                                    "content": "the charter"}]}],
    })
    await repo.park_session_awaiting_continue(session_id, {"windows": 1})

    # A plain transcript snapshot — mentions `messages` and nothing else.
    await repo.update_session_run_state(session_id, {
        "messages": [{"kind": "request", "parts": []},
                     {"kind": "response", "parts": []}],
    })

    rs = await repo.get_session_run_state(session_id)
    assert len(rs["messages"]) == 2, "the writer's own key IS replaced wholesale"
    assert len(rs["archived"]) == 1, "archived survived a write that never named it"
    assert rs["pending_continue"]["windows"] == 1


@needs_pg
async def test_a_claimed_marker_survives_the_run_that_follows_it():
    """A pending marker is consumed by the STATUS FLIP, never by its own
    absence — so the merge letting it outlive the next snapshot is the same
    record-keeping `pending_resume` already had (`test_resume_parking`'s "the
    record keeps the outage"). Re-claiming is blocked by the status, and a
    re-park overwrites the marker with the new window count."""
    session_id = await _a_session()
    await repo.update_session_run_state(session_id, {"messages": []})
    await repo.park_session_awaiting_continue(session_id, {"windows": 1})

    claimed = await repo.claim_parked_continue(session_id)
    assert claimed["pending_continue"]["windows"] == 1
    assert await repo.claim_parked_continue(session_id) is None, "the flip is the lock"

    await repo.update_session_run_state(session_id, {"messages": [{"kind": "response"}]})
    rs = await repo.get_session_run_state(session_id)
    assert rs["pending_continue"]["windows"] == 1


@needs_pg
async def test_the_paused_save_also_merges():
    """`save_paused_session` is a park, not a reset — same rule."""
    session_id = await _a_session()
    await repo.update_session_run_state(session_id, {"archived": [{"kind": "request"}]})
    await repo.save_paused_session(session_id, "ctx-test-agent", {
        "messages": [{"kind": "response", "parts": []}], "tool_call_id": "call_1",
    })

    rs = await repo.get_session_run_state(session_id)
    assert rs["tool_call_id"] == "call_1"
    assert len(rs["archived"]) == 1


# --- the full transcript ------------------------------------------------------


def test_full_messages_reads_both_halves_and_tolerates_neither():
    live = [{"kind": "response", "parts": [{"part_kind": "text", "content": "now"}]}]
    old = [{"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "then"}]}]

    assert full_messages({"messages": live}) == live, "archived is optional-empty"
    assert full_messages({}) == []
    assert full_messages({"archived": old, "messages": live}) == old + live, (
        "archived comes FIRST — it is the older half of one transcript"
    )
    # The human-read surface sees the whole thing, oldest first.
    assert [t["content"] for t in simplify_messages({"archived": old, "messages": live})] \
        == ["then", "now"]


# --- the pressure signal ------------------------------------------------------


def _bulky(fraction: float, window: int) -> dict:
    """A run_state whose live window estimates to roughly `fraction` of `window`
    (the estimate is chars/4, and json.dumps adds ~20 chars of framing)."""
    chars = int(fraction * window * 4)
    return {"messages": [{"kind": "request", "parts": [
        {"part_kind": "user-prompt", "content": "x" * chars}]}]}


@needs_pg
async def test_pressure_fires_over_the_threshold_and_then_throttles(monkeypatch):
    monkeypatch.setattr(settings, "context_pressure_threshold", 0.6)
    _pin_window(10_000)  # discovery is cached; pin it rather than hit a proxy
    session_id = await _a_session()

    async def emitted() -> list[dict]:
        return [e for e in await repo.list_events_of_kinds(["session.context_pressure"])
                if e["ref_id"] == session_id]

    await context.check_pressure(session_id, _bulky(0.5, 10_000), agent_id="ctx-test-agent")
    assert await emitted() == [], "under the threshold says nothing"

    await context.check_pressure(session_id, _bulky(0.7, 10_000), agent_id="ctx-test-agent")
    first = await emitted()
    assert len(first) == 1
    assert first[0]["payload"]["window"] == 10_000
    assert 0.65 < first[0]["payload"]["fraction"] < 0.75
    assert first[0]["payload"]["agent_id"] == "ctx-test-agent"

    # Grew, but by less than the step — one session must not narrate every turn.
    await context.check_pressure(session_id, _bulky(0.72, 10_000), agent_id="ctx-test-agent")
    assert len(await emitted()) == 1

    # Grew past the step — worth saying again.
    await context.check_pressure(session_id, _bulky(0.8, 10_000), agent_id="ctx-test-agent")
    assert len(await emitted()) == 2


@needs_pg
async def test_demo_mode_measures_nothing(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    _pin_window(10_000)
    session_id = await _a_session()

    await context.check_pressure(session_id, _bulky(0.9, 10_000), agent_id="ctx-test-agent")

    assert [e for e in await repo.list_events_of_kinds(["session.context_pressure"])
            if e["ref_id"] == session_id] == []


async def test_a_broken_check_cannot_break_the_run(monkeypatch):
    """Telemetry riding on a run must never be able to kill it."""
    async def boom() -> int:
        raise RuntimeError("proxy on fire")

    monkeypatch.setattr(context, "context_window", boom)
    await context.check_pressure("sess_nonexistent", _bulky(0.9, 10_000))


def test_the_estimate_ignores_the_archive():
    """Archived messages are not sent, so they exert no pressure."""
    big = {"archived": [{"content": "x" * 40_000}], "messages": []}
    assert context.estimate_tokens(big) == 0
