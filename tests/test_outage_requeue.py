"""Slice 4 of outage equivalence: DISPATCH and TASK runs requeue on a transient
failure instead of failing out.

The evidence base (2026-07-31): the same LiteLLM/n8n class of transient error
that cost a resume its final turn and turned an approved proposal terminally
FAILED also failed work ITEMS out of the queue and resolved TASKS FAILED — the
operator's ask spent, the email dropped, for a reason that was never theirs.

The property these tests pin, per surface:

- **A work item** transiently released gives its attempt BACK and waits out a
  backoff. There is no exhaustion at all on that path: an email queued during a
  week-long outage must still process afterwards. A SEMANTIC failure keeps
  today's behaviour byte-for-byte — attempts consumed, FAILED at the cap.
- **A task** returns to ASSIGNED with retry bookkeeping and is re-run through
  the very entry the original used. The dead run's session stays FAILED with
  its partial transcript (sessions are never rewritten — the TASK is the unit
  that retries). Exhaustion is a promotion, not a loop.

The ledger half's invariants are SQL (the `not_before` predicate lives in the
claim statement, so two dispatchers cannot race into a backed-off row); these
tests therefore need a real Postgres and skip without one, like the rest of
`test_ledger.py`.
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
from central_command.ingest import dispatcher, ledger
from central_command.runtime.spike_model import make_jira_task_model
from tests.conftest import aresolve, event_kinds as _kinds, needs_pg

pytestmark = needs_pg

# The two shapes, as this deployment actually produces them.
OUTAGE = ModelHTTPError(429, "cc-default", body="No deployments available")
SEMANTIC = RuntimeError("field 'due_date' is not a valid date")


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    # Proposals accumulate across the file; pin the valve open or the drain
    # blocks once three are awaiting review.
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)


async def _event(ref_id: str, kind: str) -> dict:
    return [e for e in await repo.list_events(ref_id=ref_id) if e["kind"] == kind][-1]


# --- the ledger half ----------------------------------------------------------


@pytest.fixture()
async def clean_ledger():
    async def wipe():
        conn = await repo._conn()
        try:
            await conn.execute(
                "delete from work_item where message_id like '<cc-outage-%'"
            )
        finally:
            await conn.close()

    await wipe()
    yield
    await wipe()


def _raw(n: int) -> str:
    return (
        f"Message-ID: <cc-outage-{n}@example.com>\n"
        "From: sender@example.com\n"
        f"Subject: Outage test {n}\n"
        "Date: Wed, 1 Jul 2026 09:00:00 +0000\n"
        f"\nBody of outage test item {n}.\n"
    )


def _boom_handler(monkeypatch, exc: BaseException) -> None:
    """Make the email handler raise — a run that dies for the reason `exc`
    names, inside the one path every email takes."""

    async def boom(item, siblings, siblings_capped, note, model):
        raise exc

    monkeypatch.setitem(
        dispatcher.HANDLERS, "email",
        dispatcher._KindHandler(boom, "agent:inbox-triage", False),
    )


async def _row(item_id: str) -> dict:
    conn = await repo._conn()
    try:
        return dict(await conn.fetchrow(
            "select state, attempts, transient_releases, not_before, last_error "
            "from work_item where id = $1", item_id,
        ))
    finally:
        await conn.close()


async def _set_not_before(item_id: str, when) -> None:
    conn = await repo._conn()
    try:
        await conn.execute(
            "update work_item set not_before = $2 where id = $1", item_id, when
        )
    finally:
        await conn.close()


# 1
async def test_a_transient_dispatch_failure_releases_without_spending_an_attempt(
    monkeypatch, clean_ledger,
):
    """The provider being down is not the item's fault: the attempt the CLAIM
    consumed comes back, and what grows instead is the backoff."""
    _boom_handler(monkeypatch, OUTAGE)
    await ledger.enroll_email(_raw(1))
    item = await repo.claim_next_work_item("sess_outage_a")
    assert item is not None and item["attempts"] == 1

    out = await dispatcher.process_claimed(item)
    assert out["ok"] is False and out["transient"] is True

    row = await _row(item["id"])
    assert row["state"] == "UNPROCESSED"       # back in the queue, not FAILED
    assert row["attempts"] == 0, "a transient release hands the attempt back"
    assert row["transient_releases"] == 1
    assert row["not_before"] > datetime.now(timezone.utc)

    released = await _event(item["id"], "work.released")
    assert released["payload"]["transient"] is True
    assert released["payload"]["transient_releases"] == 1
    assert released["payload"]["backoff_seconds"] == 60
    assert "work.failed" not in await _kinds(item["id"])

    # THE point of `not_before`: the claim query — where the predicate lives —
    # will not hand the row out again until the backoff elapses.
    again = await repo.claim_next_work_item("sess_outage_b")
    assert again is None or again["id"] != item["id"]

    # No sleeping: due-ness is a timestamp, so move the timestamp.
    await _set_not_before(item["id"], datetime.now(timezone.utc) - timedelta(seconds=1))
    due = await repo.claim_next_work_item("sess_outage_c")
    assert due is not None and due["id"] == item["id"]
    assert due["transient_releases"] == 1, "the backoff exponent survives the claim"


# 2
async def test_a_semantic_dispatch_failure_keeps_todays_behaviour(
    monkeypatch, clean_ledger,
):
    """Regression, byte-for-byte: the dependency ANSWERED, so the attempt is
    spent, the row has no backoff, and at the cap it parks FAILED."""
    monkeypatch.setattr(settings, "dispatch_max_attempts", 2)
    _boom_handler(monkeypatch, SEMANTIC)
    await ledger.enroll_email(_raw(2))

    first = await repo.claim_next_work_item("sess_sem_a")
    await dispatcher.process_claimed(first)
    row = await _row(first["id"])
    assert row["state"] == "UNPROCESSED"
    assert row["attempts"] == 1, "a semantic failure spends its attempt"
    assert row["transient_releases"] == 0
    assert row["not_before"] is None
    assert (await _event(first["id"], "work.released"))["payload"]["transient"] is False

    second = await repo.claim_next_work_item("sess_sem_b")
    assert second is not None and second["id"] == first["id"]
    assert second["attempts"] == 2
    await dispatcher.process_claimed(second)

    row = await _row(first["id"])
    assert row["state"] == "FAILED"            # terminal at the cap, as before
    assert (await _event(first["id"], "work.failed"))["payload"]["transient"] is False


# 2a
async def test_a_context_overflow_parks_on_the_first_attempt(monkeypatch, clean_ledger):
    """The request did not FIT, and a retry re-sends the same input: five
    attempts were five identical 400s (2026-09-19). Through the entry, with
    attempts to spare — FAILED at once, never released."""
    monkeypatch.setattr(settings, "dispatch_max_attempts", 5)
    _boom_handler(monkeypatch, RuntimeError(
        "status_code: 400, model_name: cc-default, body: litellm.ContextWindowExceededError: "
        "request (84314 tokens) exceeds the available context size (81920 tokens)"))
    await ledger.enroll_email(_raw(21))

    item = await repo.claim_next_work_item("sess_overflow")
    await dispatcher.process_claimed(item)
    row = await _row(item["id"])
    assert row["state"] == "FAILED" and row["attempts"] == 1
    assert (await _event(item["id"], "work.failed"))["payload"]["transient"] is False


# 2b
async def test_a_cancelled_run_releases_its_claim(monkeypatch, clean_ledger):
    """A shutdown mid-run is CancelledError — a BaseException that sails past
    `except Exception`. It must release the row (and its thread lock) on the
    way out, or the item sits CLAIMED until a restart old enough for a sweep
    that no longer exists. Attempt handed back, no backoff: a deploy is never
    the item's fault."""
    _boom_handler(monkeypatch, asyncio.CancelledError())
    await ledger.enroll_email(_raw(21))
    item = await repo.claim_next_work_item("sess_cancel_a")
    assert item is not None and item["attempts"] == 1

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.process_claimed(item)

    row = await _row(item["id"])
    assert row["state"] == "UNPROCESSED"
    assert row["attempts"] == 0, "the cancelled claim hands its attempt back"
    assert row["not_before"] is None, "a shutdown is not an outage — no backoff"
    assert (await _event(item["id"], "work.released"))["payload"]["cancelled"] is True

    again = await repo.claim_next_work_item("sess_cancel_b")
    assert again is not None and again["id"] == item["id"]


# 2c
async def test_startup_reclaims_orphans_regardless_of_age(monkeypatch, clean_ledger):
    """systemd restarts in seconds, so a SIGKILLed process's orphan is always
    far younger than the stale threshold at the moment the sweep runs. With
    the lease held and nothing in flight, age proves nothing — reclaim all."""
    await ledger.enroll_email(_raw(22))
    orphan = await repo.claim_next_work_item("sess_dead_process")
    assert orphan is not None

    async def shut():
        return False, "pinned shut for the test"

    monkeypatch.setattr(dispatcher, "check_valves", shut)
    d = dispatcher.Dispatcher()
    await d.start()
    try:
        row = await _row(orphan["id"])
        assert row["state"] == "UNPROCESSED", "a fresh orphan must not survive startup"
    finally:
        await d.stop()


# --- the task half ------------------------------------------------------------


def _model_that_raises(exc: BaseException) -> FunctionModel:
    """A model whose turn raises — the provider being down, from the run's point
    of view. Deliberately a real run (not a stubbed `run_task`): the session it
    creates, and what happens to that session, is half of what these tests pin.
    """

    def respond(messages, info):
        raise exc

    return FunctionModel(respond)


def _resolver_raising(exc: BaseException):
    """The provider is DOWN before the run's first request exists — the one
    case a task run is still retry-parked FRESH (`park_task_retry`). Since
    outage doctrine story 6, a model that dies on a request the run already
    persisted parks the TURN in place instead (`tests/test_turn_parking.py`),
    so these tests, which pin the fresh retry park, fail at model resolution:
    no session, no record to resume, nothing to re-send."""
    async def _resolve(*a, **k):
        raise exc

    return _resolve


@pytest.fixture()
def sessions_created(monkeypatch) -> list[str]:
    """Every session id a run opens, so a test can look at the transcript the
    dead run left behind — nothing links a task to the session of a run that
    never got far enough to park."""
    created: list[str] = []
    real = repo.create_running_session

    async def spy(session_id, agent_id, **kw):
        created.append(session_id)
        return await real(session_id, agent_id, **kw)

    monkeypatch.setattr(repo, "create_running_session", spy)
    return created


_MADE: list[str] = []


@pytest.fixture(autouse=True)
async def disarm_leftovers():
    """Un-park anything this file leaves queued for retry. `resume_sweep` is a
    GLOBAL worklist — a still-parked test task would come due a minute later
    and be re-run by some other test's sweep, which is exactly the kind of
    cross-test surprise the shared dev database punishes."""
    yield
    if not _MADE:
        return
    conn = await repo._conn()
    try:
        await conn.execute(
            "update task set retry_state = null where id = any($1::text[])", _MADE
        )
    finally:
        await conn.close()
        _MADE.clear()


async def _assigned_task(agent_id: str = "jira-expert", instructions: str = "x") -> dict:
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "outage requeue test", instructions, agent_id)
    _MADE.append(task_id)
    return await repo.get_task(task_id)


# 3
async def test_a_transient_task_run_parks_the_task_and_keeps_the_dead_session(
    monkeypatch, sessions_created,
):
    monkeypatch.setattr(routes, "resolve_model", _resolver_raising(OUTAGE))
    task = await _assigned_task()

    await routes._run_assigned_task_detached(task, "operator")

    row = await repo.get_task(task["id"])
    assert row["status"] == "ASSIGNED", "back to its pre-run status, not FAILED"
    state = row["retry_state"]
    assert state["kind"] == "run"
    assert state["attempts"] == 1
    assert "No deployments available" in state["last_error"]
    assert state["parked_at"] and state["not_before"]
    # The board renders `outcome` as the task's result today, so the hint needs
    # no new wire field to reach the operator.
    assert row["outcome"].startswith(orchestration.RETRY_HINT)
    # …and on the REAL payload the cockpit fetches (`/api/tasks` → the board's
    # `result` field), because a hint the wire never carries is invisible to
    # `tsc` and to every frontend test alike.
    wire = [t for t in (await routes.list_tasks())["tasks"] if t["id"] == task["id"]][0]
    assert wire["status"] == "ASSIGNED"
    assert wire["outcome"].startswith(orchestration.RETRY_HINT)

    # Rewritten for outage doctrine story 6: the outage now hits before the
    # run's first request exists, so there is no session to keep — which is
    # exactly why the task is retry-parked FRESH rather than parked in place.
    assert not sessions_created, "nothing ran, so there is nothing to resume"

    parked = await _event(task["id"], "task.run_parked")
    assert parked["payload"]["transient"] is True
    assert parked["payload"]["attempts"] == 1
    assert parked["payload"]["backoff_seconds"] == 60
    assert "task.run_failed" not in await _kinds(task["id"])


# 4
async def test_a_semantic_task_run_failure_keeps_todays_behaviour(monkeypatch):
    """Regression: only a transient failure parks."""
    monkeypatch.setattr(
        routes, "resolve_model",
        aresolve(lambda *a, **k: _model_that_raises(SEMANTIC)),
    )
    task = await _assigned_task()

    await routes._run_assigned_task_detached(task, "operator")

    row = await repo.get_task(task["id"])
    assert row["status"] == "FAILED"
    assert row["retry_state"] is None
    assert (await _event(task["id"], "task.run_failed"))["payload"]["transient"] is False
    assert "task.run_parked" not in await _kinds(task["id"])


# 5
async def test_retrying_a_parked_task_runs_it_again_and_lands_the_outcome(monkeypatch):
    """The dependency is back: a fresh run, through the same entry the original
    used, lands its outcome exactly as a no-outage first run would have."""
    monkeypatch.setattr(routes, "resolve_model", _resolver_raising(OUTAGE))
    task = await _assigned_task(instructions="Advise only, no change needed.")
    await routes._run_assigned_task_detached(task, "operator")
    assert (await repo.get_task(task["id"]))["status"] == "ASSIGNED"

    monkeypatch.setattr(routes, "resolve_model", aresolve(lambda *a, **k: make_jira_task_model()))
    out = await orchestration.retry_parked_task(task["id"])

    assert out is not None and out["retried"] is True and out["attempts"] == 1
    row = await repo.get_task(task["id"])
    assert row["status"] == "DONE"
    assert row["retry_state"] is None, "the bookkeeping is cleared on convergence"
    assert orchestration.RETRY_HINT not in (row["outcome"] or "")
    kinds = await _kinds(task["id"])
    assert "task.run_retried" in kinds
    assert "task.completed" in kinds


# 6 — rewritten for outage doctrine story 4: there is no task retry limit and
# no promotion (the operator's ruling, 2026-10-27). The old 6b, "exhaustion
# gives the operator an item", tested the promotion itself and went with it.
async def test_task_retries_never_exhaust(monkeypatch):
    monkeypatch.setattr(routes, "resolve_model", _resolver_raising(OUTAGE))
    task = await _assigned_task()

    await routes._run_assigned_task_detached(task, "operator")     # attempt 1
    for attempt in range(2, 8):                                     # past the old 5
        again = await orchestration.retry_parked_task(task["id"])
        assert again["parked"] is True and again["attempts"] == attempt

    row = await repo.get_task(task["id"])
    assert row["status"] == "ASSIGNED"
    assert row["retry_state"]["attempts"] == 7
    kinds = await _kinds(task["id"])
    assert not [k for k in kinds if k.endswith("exhausted")]
    assert "task.run_failed" not in kinds


# 7
async def test_two_concurrent_retries_run_the_task_exactly_once(monkeypatch):
    monkeypatch.setattr(routes, "resolve_model", _resolver_raising(OUTAGE))
    task = await _assigned_task(instructions="Advise only, no change needed.")
    await routes._run_assigned_task_detached(task, "operator")

    runs: list[str] = []

    async def one_model(*a, **k):
        runs.append("run")
        return make_jira_task_model()

    monkeypatch.setattr(routes, "resolve_model", one_model)
    a, b = await asyncio.gather(
        orchestration.retry_parked_task(task["id"]),
        orchestration.retry_parked_task(task["id"]),
    )

    assert [a is not None, b is not None].count(True) == 1, (
        "the ASSIGNED-with-retry_state → IN_PROGRESS flip is the lock"
    )
    assert len(runs) == 1
    assert (await repo.get_task(task["id"]))["status"] == "DONE"


# 8
async def test_the_sweep_drives_due_retry_parked_tasks_and_skips_the_rest(monkeypatch):
    """The "after a restart, or whenever in doubt" lever covers parked tasks
    too — respecting the backoff, so a sweep can't burn the attempt budget into
    a dependency that is still down. (Its cadence is slice 5.)"""
    monkeypatch.setattr(routes, "resolve_model", _resolver_raising(OUTAGE))
    due = await _assigned_task(instructions="Advise only, no change needed.")
    later = await _assigned_task(instructions="Advise only, no change needed.")
    await routes._run_assigned_task_detached(due, "operator")
    await routes._run_assigned_task_detached(later, "operator")

    # Due-ness is a timestamp, not a sleep.
    conn = await repo._conn()
    try:
        await conn.execute(
            "update task set retry_state = jsonb_set(retry_state, '{not_before}', "
            "to_jsonb($2::text)) where id = $1",
            due["id"], (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        )
    finally:
        await conn.close()

    # Scoped to the rows this test created — the dev database is shared.
    due_ids = [t["id"] for t in await repo.tasks_awaiting_retry(due_only=True)]
    assert due["id"] in due_ids and later["id"] not in due_ids
    parked_ids = [t["id"] for t in await repo.tasks_awaiting_retry(due_only=False)]
    assert due["id"] in parked_ids and later["id"] in parked_ids

    monkeypatch.setattr(routes, "resolve_model", aresolve(lambda *a, **k: make_jira_task_model()))
    swept = await orchestration.resume_sweep()

    assert due["id"] in swept["tasks_retried"]
    assert later["id"] not in swept["tasks_retried"]
    assert (await repo.get_task(due["id"]))["status"] == "DONE"
    still = await repo.get_task(later["id"])
    assert still["status"] == "ASSIGNED" and still["retry_state"]["attempts"] == 1


# 10 — the gap that let three tasks sit stuck on the live board (2026-08-10).
async def test_a_synchronous_run_lands_its_failure_too(monkeypatch):
    """Every test above drives the DETACHED wrapper, which is precisely how the
    synchronous entries went unguarded: `create_and_run_task(background=False)`
    (the heartbeat's `ea.contact` path) and assign-and-run raised straight past
    the task record, leaving IN_PROGRESS forever. A 429 on 2026-07-31 and a
    provider timeout on 2026-08-02 both stuck that way — transient errors the
    retry machinery would have re-run had anything asked it.

    The exception still propagates (the heartbeat records `heartbeat.error`,
    an HTTP caller still gets its 500); only the record is new.
    """
    monkeypatch.setattr(routes, "resolve_model", _resolver_raising(OUTAGE))
    task = await _assigned_task()

    with pytest.raises(ModelHTTPError):
        await routes._run_assigned_task(task, actor="heartbeat:ea-morning-report")

    row = await repo.get_task(task["id"])
    assert row["status"] == "ASSIGNED", "parked for retry, not abandoned mid-run"
    assert row["retry_state"]["attempts"] == 1


async def test_a_synchronous_semantic_failure_fails_the_task(monkeypatch):
    """…and a semantic one lands FAILED rather than parking, same as detached."""
    monkeypatch.setattr(routes, "resolve_model", aresolve(lambda *a, **k: _model_that_raises(SEMANTIC)))
    task = await _assigned_task()

    with pytest.raises(RuntimeError):
        await routes._run_assigned_task(task, actor="operator")

    row = await repo.get_task(task["id"])
    assert row["status"] == "FAILED"
    assert (await _event(task["id"], "task.run_failed"))["payload"]["transient"] is False


async def test_a_retry_is_not_parked_twice_by_the_fresh_run_guard(monkeypatch):
    """The guard must not fire underneath `retry_parked_task`, whose own handler
    is attempt-AWARE: two landings would re-park at attempts=1 every round and
    the backoff would never grow past its first step."""
    monkeypatch.setattr(routes, "resolve_model", _resolver_raising(OUTAGE))
    task = await _assigned_task()
    await routes._run_assigned_task_detached(task, "operator")          # attempt 1

    await orchestration.retry_parked_task(task["id"])                   # attempt 2
    row = await repo.get_task(task["id"])
    assert row["status"] == "ASSIGNED"
    assert row["retry_state"]["attempts"] == 2, "the counter advanced, not reset"


# 11 — a run that died with its process (2026-08-01: a restart mid-run).
async def test_the_orphan_sweep_lands_the_task_not_just_the_session():
    """The task is still LANDED by the sweep — the rule's point since
    2026-08-01, when flipping only the session left the board IN_PROGRESS
    forever. Rewritten for outage doctrine story 5: it lands by RETRY-PARK,
    not FAILED. The task goes back to ASSIGNED with a `restart` retry_state due
    now (re-run fresh, whatever its age), and the dead transcript is kept,
    closed STOPPED with a reason naming the task."""
    agent_id = "jira-expert"
    task_id = "task_" + uuid.uuid4().hex[:12]
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "orphan sweep test", "x", agent_id)
    _MADE.append(task_id)
    await repo.create_running_session(session_id, agent_id)
    await repo.start_task(task_id, session_id)

    conn = await repo._conn()
    try:
        await conn.execute(
            "update session set updated_at = now() - interval '2 hours' where id = $1",
            session_id,
        )
    finally:
        await conn.close()

    landed = {r["session_id"]: r for r in await repo.park_orphaned_sessions()}

    assert landed[session_id]["outcome"] == "task_rerun"
    assert landed[session_id]["task_id"] == task_id
    assert await repo.session_status(session_id) == "STOPPED", "transcript kept"
    assert task_id in (await repo.get_session(session_id))["closed_reason"]
    row = await repo.get_task(task_id)
    assert row["status"] == "ASSIGNED"
    assert row["retry_state"]["kind"] == "restart"
    assert row["retry_state"]["attempts"] == 0
    assert task_id in [t["id"] for t in await repo.tasks_awaiting_retry(due_only=True)]


async def test_a_mid_life_dispatcher_start_leaves_a_live_run_alone(monkeypatch):
    """Rewritten for outage doctrine story 5. The scope guard used to be an AGE
    threshold on a mid-life sweep; now there is no mid-life session sweep at
    all — a run in a live process is alive, however long it takes. Starting
    dispatch mid-life (the operator may) reclaims the ledger and touches no
    session."""
    from central_command.ingest import dispatcher as dispatcher_mod

    agent_id = "jira-expert"
    task_id = "task_" + uuid.uuid4().hex[:12]
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "live run", "x", agent_id)
    _MADE.append(task_id)
    await repo.create_running_session(session_id, agent_id)
    await repo.start_task(task_id, session_id)
    conn = await repo._conn()
    try:
        await conn.execute(
            "update session set updated_at = now() - interval '2 hours' where id = $1",
            session_id,
        )
    finally:
        await conn.close()

    async def _idle(self):
        return None

    monkeypatch.setattr(dispatcher_mod.Dispatcher, "_loop", _idle)
    d = dispatcher_mod.Dispatcher()
    try:
        await d.start()
        assert (await repo.get_task(task_id))["status"] == "IN_PROGRESS"
        assert await repo.session_status(session_id) == "RUNNING"
    finally:
        await d._release_lease()
        conn = await repo._conn()
        try:
            await conn.execute("delete from session where id = $1", session_id)
        finally:
            await conn.close()
