"""Slice 5 of outage equivalence: the `retry.sweep` heartbeat action and its
health gates.

Slices 2–4 gave every unit a place to WAIT out an outage. This one is the half
that makes waiting converge by itself: a schedule fires the sweep, the sweep
asks each dependency whether it is back, and drives the units whose dependency
is up through the drivers that already own them.

The two properties these tests pin:

- **A gated skip consumes NO attempt.** Attempt budgets exist to stop silent
  infinite loops, not to run out during an outage — at a 5-minute cadence a
  two-hour outage would otherwise exhaust every parked unit inside 25 minutes.
  Skipping is a `continue` before the driver call, so the test reads the
  attempt count back off the row and finds it untouched.
- **A sweep that did nothing writes nothing.** The overwhelmingly common tick
  finds nothing parked; the 2026-07-25 heartbeat-noise rule says it must leave
  the append-only log alone. A tick that DID something, or errored, is loud.

Probes are ALWAYS monkeypatched here — the offline suite must never reach the
Pi's n8n, Graphiti or LiteLLM.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.function import FunctionModel

from central_command.api import orchestration, routes
from central_command.config import settings
from central_command.db import repo
from central_command.heartbeat import actions as hb_actions
from central_command.heartbeat import probes
from central_command.heartbeat.engine import HeartbeatEngine, fire
from tests.conftest import aresolve, needs_pg

OUTAGE = ModelHTTPError(429, "cc-default", body="No deployments available")


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    # demo_mode pinned everywhere `resolve_model` is reachable — the sweep's
    # drivers all end in agent runs.
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)


class _Recorder:
    """Captures events.emit calls in order, across modules."""

    def __init__(self, monkeypatch):
        self.kinds: list[str] = []

        async def _emit(kind, ref_id=None, payload=None, actor=None):
            self.kinds.append(kind)
            return {"kind": kind, "id": 0}

        from central_command import events

        monkeypatch.setattr(events, "emit", _emit)


def _no_probes(monkeypatch, **answers):
    """Replace every probe with a recorder. Unnamed dependencies raise, so a
    test that expects a probe NOT to be consulted fails loudly rather than
    quietly reaching the network."""
    asked: list[str] = []

    def _make(name):
        async def _probe():
            asked.append(name)
            if name not in answers:
                raise AssertionError(f"probe {name} must not be consulted here")
            return answers[name]

        return _probe

    monkeypatch.setattr(
        probes, "PROBES", {name: _make(name) for name in probes.PROBES}
    )
    return asked


def _empty_worklists(monkeypatch, *, sessions=(), proposals=(), tasks=(),
                     orphans=()):
    async def _sessions():
        return list(sessions)

    async def _orphans(age_seconds):
        return list(orphans)

    async def _proposals(status=None):
        assert status == "RETRY_PENDING"
        return list(proposals)

    async def _tasks(due_only=True):
        assert due_only is True, "the sweep must respect the backoff"
        return list(tasks)

    monkeypatch.setattr(repo, "sessions_awaiting_resume", _sessions)
    monkeypatch.setattr(repo, "sessions_with_orphaned_resume", _orphans)
    monkeypatch.setattr(repo, "list_proposals", _proposals)
    monkeypatch.setattr(repo, "tasks_awaiting_retry", _tasks)


def _no_touch(monkeypatch, rec=None):
    async def _touch(schedule_id):
        if rec is not None:
            rec.kinds.append("touch:last_fired")

    monkeypatch.setattr(repo, "touch_heartbeat_fired", _touch)


SWEEP_SCHEDULE = {"id": "retry-sweep", "action_kind": "retry.sweep",
                  "action_params": {}}


# --- 1: the quiet tick ---------------------------------------------------------


async def test_a_sweep_with_nothing_parked_writes_nothing(monkeypatch):
    """The overwhelmingly common tick: no parked units, so no probes fire (they
    would raise here), all-zero counts, and the append-only log is untouched —
    `last_fired_at` alone records that the engine ran on cadence."""
    rec = _Recorder(monkeypatch)
    _no_touch(monkeypatch, rec)
    _no_probes(monkeypatch)  # every probe raises: none may be consulted
    _empty_worklists(monkeypatch)

    out = await fire(SWEEP_SCHEDULE)

    assert out["ok"] and out["quiet"] is True
    assert out["result"] == {
        "resumed": [], "orphaned": [], "retried_executions": [],
        "retried_tasks": [], "skipped_gated": [], "errors": [],
    }
    assert rec.kinds == ["touch:last_fired"], "a no-op sweep wrote to the log"


# --- 3: a down dependency skips without spending an attempt --------------------


async def test_a_gated_skip_never_calls_the_driver(monkeypatch):
    """By construction, which is the only way to be sure: a skip is a `continue`
    before the driver call, so there is no path on which a down dependency can
    spend a unit's attempt budget."""
    rec = _Recorder(monkeypatch)
    _no_touch(monkeypatch, rec)
    _no_probes(monkeypatch, llm=False)
    _empty_worklists(
        monkeypatch, sessions=["sess_gated"], tasks=[{"id": "task_gated"}]
    )

    async def _must_not_run(*a, **k):
        raise AssertionError("a gated unit reached its driver")

    monkeypatch.setattr(orchestration, "resume_parked_session", _must_not_run)
    monkeypatch.setattr(orchestration, "retry_parked_task", _must_not_run)

    out = await fire(SWEEP_SCHEDULE)

    assert out["quiet"] is True, "a tick that only skipped did nothing"
    result = out["result"]
    assert result["resumed"] == [] and result["retried_tasks"] == []
    assert [s["id"] for s in result["skipped_gated"]] == ["sess_gated", "task_gated"]
    assert {s["dependency"] for s in result["skipped_gated"]} == {probes.LLM}
    assert result["probes"] == {probes.LLM: False}
    assert rec.kinds == ["touch:last_fired"]


@needs_pg
async def test_a_gated_skip_leaves_the_attempt_count_on_the_row(monkeypatch):
    """The same property, read back off the database: a task parked once is
    still on attempt 1 after a sweep that found its dependency down."""
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "slice 5 gate test", "advise only", "jira-expert")
    try:
        await orchestration.park_task_retry(task_id, OUTAGE)
        # Due-ness is a timestamp, not a sleep.
        conn = await repo._conn()
        try:
            await conn.execute(
                "update task set retry_state = jsonb_set(retry_state, "
                "'{not_before}', to_jsonb($2::text)) where id = $1",
                task_id,
                (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            )
        finally:
            await conn.close()
        before = (await repo.get_task(task_id))["retry_state"]["attempts"]

        _no_probes(monkeypatch, llm=False)

        async def _must_not_run(*a, **k):
            raise AssertionError("a gated task reached its driver")

        monkeypatch.setattr(orchestration, "retry_parked_task", _must_not_run)
        result = await orchestration.retry_sweep()

        assert task_id in [s["id"] for s in result["skipped_gated"]]
        row = await repo.get_task(task_id)
        assert row["status"] == "ASSIGNED"
        assert row["retry_state"]["attempts"] == before == 1
    finally:
        conn = await repo._conn()
        try:
            await conn.execute(
                "update task set retry_state = null where id = $1", task_id
            )
        finally:
            await conn.close()


# --- 4: a mixed tick is material -----------------------------------------------


async def test_a_tick_that_skipped_and_errored_is_recorded(monkeypatch):
    """Skipping is quiet; failing never is. One unit gated on a dead LLM and one
    proposal whose retry raised: the firing lands on the log carrying both."""
    rec = _Recorder(monkeypatch)
    _no_touch(monkeypatch, rec)
    _no_probes(monkeypatch, llm=False, n8n=True)
    _empty_worklists(
        monkeypatch, sessions=["sess_gated"], proposals=[{"id": "prop_boom"}]
    )

    async def _load(proposal_id):
        return {"id": proposal_id,
                "retry_state": {"failed_capability": "jira.set_due_date"}}

    monkeypatch.setattr(repo, "load_proposal", _load)

    from central_command.gateway import gateway

    async def _boom(proposal_id, model=None):
        raise RuntimeError("the façade answered 500")

    monkeypatch.setattr(gateway, "retry_execution", _boom)

    out = await fire(SWEEP_SCHEDULE)

    assert out["ok"] and "quiet" not in out
    result = out["result"]
    assert [s["id"] for s in result["skipped_gated"]] == ["sess_gated"]
    assert len(result["errors"]) == 1
    assert result["errors"][0]["id"] == "prop_boom"
    assert "500" in result["errors"][0]["error"]
    assert rec.kinds == ["touch:last_fired", "heartbeat.completed"]


# --- 7: the gating map ---------------------------------------------------------


def test_the_capability_gating_map_is_by_prefix():
    """A retry's gate is the system the FAILED capability talks to."""
    assert probes.dependency_for_capability("jira.set_due_date") == probes.N8N
    assert probes.dependency_for_capability("graph.add_episode@v1") == probes.GRAPHITI
    assert probes.dependency_for_capability("litellm.add_model") == probes.LITELLM
    # task.create's handler is a local DB write + an agent run — its retry
    # gates on the LLM, not n8n (plan error, corrected in review).
    assert probes.dependency_for_capability("task.create") == probes.LLM
    # UNKNOWN is not DOWN: no probe defined means attempt anyway — the attempt
    # is the probe, bounded by the attempt budget.
    assert probes.dependency_for_capability("charter.update") is None
    assert probes.dependency_for_capability(None) is None
    assert probes.dependency_for_capability("brand.new_capability") is None
    # Every mapped dependency must have a probe, or the map would gate on
    # something that always answers "healthy".
    for dependency in probes.CAPABILITY_DEPENDENCY.values():
        assert dependency in probes.PROBES


async def test_a_jira_proposal_gates_on_n8n_not_on_the_llm(monkeypatch):
    """The blocked call is the EXECUTOR's, so the LLM's health is irrelevant to
    it — and consulting the LLM probe costs money, so it must not be asked. (The
    post-execution resume IS agent work, but slice 2 parks that leg on its own.)
    """
    _Recorder(monkeypatch)
    _no_touch(monkeypatch)
    asked = _no_probes(monkeypatch, n8n=False)  # the llm probe would raise
    _empty_worklists(monkeypatch, proposals=[{"id": "prop_jira"}])

    async def _load(proposal_id):
        return {"id": proposal_id,
                "retry_state": {"failed_capability": "jira.set_due_date"}}

    monkeypatch.setattr(repo, "load_proposal", _load)

    from central_command.gateway import gateway

    async def _must_not_run(*a, **k):
        raise AssertionError("a gated proposal reached the executor")

    monkeypatch.setattr(gateway, "retry_execution", _must_not_run)

    out = await fire(SWEEP_SCHEDULE)

    assert asked == [probes.N8N], "only the failed capability's system is probed"
    assert [s["dependency"] for s in out["result"]["skipped_gated"]] == [probes.N8N]


async def test_probes_are_asked_once_per_sweep(monkeypatch):
    """The cache: three parked sessions cost one probe, not three."""
    _Recorder(monkeypatch)
    _no_touch(monkeypatch)
    asked = _no_probes(monkeypatch, llm=False)
    _empty_worklists(monkeypatch, sessions=["a", "b", "c"])

    out = await fire(SWEEP_SCHEDULE)

    assert asked == [probes.LLM]
    assert len(out["result"]["skipped_gated"]) == 3


async def test_an_unreachable_probe_reads_as_down(monkeypatch):
    """A probe that RAISES is the case the gate exists for — it must defer the
    worklist, never fail the whole sweep."""
    _Recorder(monkeypatch)
    _no_touch(monkeypatch)
    _no_probes(monkeypatch)  # every probe raises AssertionError when called
    _empty_worklists(monkeypatch, sessions=["sess_x"])

    out = await fire(SWEEP_SCHEDULE)

    assert out["ok"] and out["quiet"] is True
    assert out["result"]["skipped_gated"][0]["dependency"] == probes.LLM


# --- 6: it fires from the engine loop like any other action --------------------


async def test_the_engine_fires_the_sweep_like_any_action(monkeypatch):
    rec = _Recorder(monkeypatch)
    _no_touch(monkeypatch, rec)
    ran: list[bool] = []

    async def _sweep(cache=None):
        ran.append(True)
        return {"resumed": ["sess_1"], "retried_executions": [],
                "retried_tasks": [], "skipped_gated": [], "errors": []}

    monkeypatch.setattr(orchestration, "retry_sweep", _sweep)

    now = datetime.now(timezone.utc)
    schedule = {"id": "retry-sweep", "name": "Retry sweep", "schedule_kind": "every",
                "schedule": {"every_seconds": 300}, "action_kind": "retry.sweep",
                "action_params": {}, "enabled": True, "updated_at": now}

    async def _list(enabled_only=False):
        return [schedule]

    monkeypatch.setattr(repo, "list_heartbeat_schedules", _list)

    engine = HeartbeatEngine()
    await engine._tick()          # first sight: due is computed from now
    assert ran == [], "a just-seen schedule must not fire immediately"
    engine._next_due["retry-sweep"] = now - timedelta(seconds=1)
    await engine._tick()          # due: fires

    assert ran == [True]
    assert rec.kinds == ["touch:last_fired", "heartbeat.completed"]
    assert engine._next_due["retry-sweep"] > datetime.now(timezone.utc)


# --- 2: the whole loop, on real parked units -----------------------------------


@needs_pg
async def test_the_sweep_drives_one_of_each_parked_kind(monkeypatch):
    """End to end on real rows: a session parked AWAITING_RESUME, an approved
    proposal parked RETRY_PENDING, and a due retry-parked task all converge in
    one healthy sweep — each through its own driver, each leaving its own
    retried event on the log. Scoped to the rows this test created; the dev
    database is shared."""
    from central_command.gateway import executor, gateway
    from central_command.integrations import graphiti
    from central_command.runtime.run import ingest_and_propose
    from central_command.runtime.spike_model import make_sc1_model, make_spike_model

    monkeypatch.setattr(settings, "executor_mode", "live")  # real handlers, spied
    down = {"on": True}

    # (a) a session parked AWAITING_RESUME: the world change landed, the
    # drafter's closing turn did not.
    async def set_due_date(issue_key, due_date):
        return None

    monkeypatch.setattr(executor.jira, "set_due_date", set_due_date)
    real_resume = gateway.resume

    async def maybe_boom(*a, **k):
        if down["on"]:
            raise OUTAGE
        return await real_resume(*a, **k)

    monkeypatch.setattr(gateway, "resume", maybe_boom)
    spike = make_spike_model()
    parked_session = await ingest_and_propose(
        "Dana: DEMO-1 slipped, due Aug 3.", model=spike
    )
    await gateway.approve_and_execute(
        parked_session["session_id"], parked_session["proposal_id"], model=spike
    )
    assert await repo.session_status(parked_session["session_id"]) == "AWAITING_RESUME"

    # (b) an approved proposal parked RETRY_PENDING: jira landed, the graph
    # write never ran.
    graph_calls: list[tuple] = []

    async def add_episode(name, body, source_description="", group_id=None):
        graph_calls.append((name, body))
        if down["on"]:
            raise RuntimeError("Connection refused talking to graphiti")
        return "ack"

    monkeypatch.setattr(graphiti, "add_episode", add_episode)
    sc1 = make_sc1_model()
    parked_exec = await ingest_and_propose(
        "Dana: DEMO-1 slipped, due Aug 3.", model=sc1
    )
    await gateway.approve_and_execute(
        parked_exec["session_id"], parked_exec["proposal_id"], model=sc1
    )
    assert (await repo.load_proposal(parked_exec["proposal_id"]))["status"] == (
        "RETRY_PENDING"
    )

    # (c) a task parked for a re-run, backdated so it is DUE.
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "slice 5 sweep test", "Advise only.", "jira-expert")
    await orchestration.park_task_retry(task_id, OUTAGE)
    conn = await repo._conn()
    try:
        await conn.execute(
            "update task set retry_state = jsonb_set(retry_state, '{not_before}', "
            "to_jsonb($2::text)) where id = $1",
            task_id, (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        )
    finally:
        await conn.close()

    # The dependencies are back.
    down["on"] = False
    _no_probes(monkeypatch, llm=True, n8n=True, graphiti=True, litellm=True)
    monkeypatch.setattr(
        routes, "resolve_model", aresolve(lambda *a, **k: _advice_model()),
    )

    result = await orchestration.retry_sweep()

    assert parked_session["session_id"] in result["resumed"]
    assert parked_exec["proposal_id"] in result["retried_executions"]
    assert task_id in result["retried_tasks"]
    assert result["errors"] == []
    assert not [s for s in result["skipped_gated"]
                if s["id"] in (parked_session["session_id"],
                               parked_exec["proposal_id"], task_id)]

    # Each driver's OWN event is on the log — the sweep is quiet-eligible
    # precisely because these exist.
    async def kinds(ref_id):
        return [e["kind"] for e in await repo.list_events(ref_id=ref_id)]

    assert "session.resume_retried" in await kinds(parked_session["session_id"])
    assert "proposal.execution_retried" in await kinds(parked_exec["proposal_id"])
    assert "task.run_retried" in await kinds(task_id)

    # And they converged, exactly as a no-outage run would have.
    assert await repo.session_status(parked_session["session_id"]) == "DONE"
    assert (await repo.load_proposal(parked_exec["proposal_id"]))["status"] == "EXECUTED"
    assert len(graph_calls) == 2, "the replay re-ran the graph write, once"
    assert (await repo.get_task(task_id))["status"] == "DONE"
    assert (await repo.get_task(task_id))["retry_state"] is None


def _advice_model() -> FunctionModel:
    """A task model that answers in text — no proposal, nothing to approve."""
    from pydantic_ai.messages import ModelResponse, TextPart

    def _respond(messages, info):
        return ModelResponse(parts=[TextPart(content="Nothing needs changing.")])

    return FunctionModel(_respond)


# --- the seed (fresh-database rule) --------------------------------------------


def test_the_retry_sweep_seed_is_enabled_at_five_minutes():
    """A deliberate deviation from born-disabled, so it is pinned in the schema
    TEXT as well as in the database: convergence that waits for an operator
    click is not convergence."""
    from pathlib import Path

    schema = (Path(hb_actions.__file__).parents[1] / "db" / "schema.sql").read_text(encoding="utf-8")
    start = schema.index("insert into heartbeat_schedule", schema.index("'ea-digest'"))
    seed = schema[start:schema.index("on conflict (id) do nothing", start)]
    assert "'retry-sweep'" in seed
    assert "'retry.sweep'" in seed and "retry.sweep" in hb_actions.ACTIONS
    assert '"every_seconds": 300' in seed
    assert "enabled" in seed.split("values")[0] and "true" in seed
    hb_actions.validate_action("retry.sweep", {})


@needs_pg
async def test_a_fresh_database_seeds_the_retry_sweep_enabled():
    """`schema.sql` is auto-loaded on a FRESH database only — the test database
    is re-executed every run, so it is the thing that proves the seed."""
    row = await repo.get_heartbeat_schedule("retry-sweep")
    assert row is not None, "the seed never landed"
    assert row["action_kind"] == "retry.sweep"
    assert row["schedule_kind"] == "every"
    assert row["schedule"] == {"every_seconds": 300}
    assert row["created_by"] == "seed:heartbeat"
    assert row["enabled"] is True, "the recorded deviation: convergence needs no click"


# --- 8: the orphan worklist, on the only cadence that can see it ---------------


@needs_pg
async def test_a_killed_resume_converges_on_the_recurring_sweep(monkeypatch):
    """A resume killed with its process is recovered WITHOUT a restart.

    `resume_sweep` has owned the orphan worklist since 2026-08-15, but it runs
    only at startup — and an orphan is invisible until it is older than
    `dispatch_stale_claim_seconds`, because until then it is indistinguishable
    from a resume still in flight. The restart that creates an orphan is the
    same restart that runs the sweep, so the sweep always looks too early. On
    2026-08-19 two triage sessions killed 100s before a restart sat AWAITING_
    HUMAN over an EXECUTED and a REJECTED proposal for as long as the process
    stayed up: armed, findable, and on nobody's cadence.

    So this drives `fire(SWEEP_SCHEDULE)` — the heartbeat ENTRY, not the sweep
    function — because a worklist that exists only in the startup path is
    exactly the gap, and the entry is what actually runs every five minutes.
    """
    from central_command.gateway import gateway
    from central_command.runtime.run import ingest_and_propose
    from central_command.runtime.spike_model import make_spike_model

    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    async def resume_killed(*a, **k):
        raise asyncio.CancelledError()  # a BaseException: no `except` writes a park

    monkeypatch.setattr(gateway, "resume", resume_killed)
    with pytest.raises(asyncio.CancelledError):
        await gateway.approve_and_execute(
            run["session_id"], run["proposal_id"], model=model
        )
    session_id = run["session_id"]
    assert await repo.session_status(session_id) == "AWAITING_HUMAN"
    assert session_id not in await repo.sessions_awaiting_resume(), (
        "the state a killed process leaves: armed, but on no worklist"
    )

    monkeypatch.undo()
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "dispatch_stale_claim_seconds", 0)
    _no_probes(monkeypatch, llm=True)
    _no_touch(monkeypatch)

    out = await fire(SWEEP_SCHEDULE)

    assert session_id in out["result"]["orphaned"]
    assert session_id in out["result"]["resumed"], (
        "one pass must both park and drive it — two passes is 5 more minutes"
    )
    assert await repo.session_status(session_id) == "DONE"
