"""Wire-shape guards for the FastAPI endpoints behind cc-crons.ts, cc-activity.ts
and cc-kanban.ts.

Those three Hono routes (and their consuming hooks: useCrons.ts, useActivity.ts,
the kanban board) either forward or lightly remap a Central Command REST payload.
Their own `*.test.ts` files mock `global.fetch` with self-built payloads, so
they assert nothing about what FastAPI actually sends — the false-coverage
class CLAUDE.md documents (stalled_at/closedReason, 2026-07-27). Same idiom as
`test_session_sidebar_wire.py`: call the route function directly, assert the
exact keys the TS side reads, name the cockpit surface that goes blank if a
key vanishes. Monkeypatched-repo tests pin the SHAPE; the one pg-backed test
per SQL-computed field (`work_subject`, the task/session join) pins the QUERY.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from central_command.api import routes
from central_command.config import settings
from central_command.db import repo
from tests.conftest import needs_pg

NOW = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


async def _async(value):
    return value


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)


# ── cc-crons: GET /heartbeat/schedules → CcSchedule ────────────────────────


async def test_heartbeat_schedules_carry_every_field_the_crons_adapter_reads(monkeypatch):
    """`mapSchedule` in cc-crons.ts reads schedule_kind/schedule/action_kind/
    action_params/enabled/created_by plus the view-only next_due/last_status/
    last_error — losing any of these blanks the crons tab's row (schedule
    type, the assignee picker, or the last-run chip)."""
    row = {
        "id": "mail-poll", "name": "Mail poll", "schedule_kind": "every",
        "schedule": {"every_seconds": 300}, "action_kind": "feed.poll",
        "action_params": {}, "enabled": True, "created_by": "operator",
        "last_fired_at": NOW, "created_at": NOW, "updated_at": NOW,
    }
    monkeypatch.setattr(repo, "list_heartbeat_schedules", lambda **k: _async([row]))
    monkeypatch.setattr(repo, "list_events", lambda **k: _async([]))

    out = await routes.list_heartbeat_schedules()

    assert set(out.keys()) == {"schedules", "actions"}
    sched = out["schedules"][0]
    for key in (
        "id", "name", "schedule_kind", "schedule", "action_kind",
        "action_params", "enabled", "created_by", "last_fired_at",
    ):
        assert key in sched, f"crons tab row loses its {key} — mapSchedule reads it directly"
    assert "next_due" in sched, "without next_due the crons row's nextRunAtMs is always blank"
    assert "last_status" in sched, "without last_status the crons row's status chip vanishes"
    assert "last_error" in sched, "without last_error a failed schedule's error text is unreadable"


async def test_heartbeat_schedule_action_params_carry_the_task_create_fields(monkeypatch):
    """A `task.create` schedule's payload mapping (cc-crons.ts `mapSchedule`)
    reads `agent_id`/`instructions`/`model`/`thinking` off action_params — the
    "Agent task" dialog's assignee and instructions fields."""
    row = {
        "id": "weekly-audit", "name": "Weekly audit", "schedule_kind": "cron",
        "schedule": {"expr": "0 9 * * MON", "tz": "UTC"}, "action_kind": "task.create",
        "action_params": {
            "agent_id": "jira-expert", "instructions": "Audit the board",
            "title": "Weekly audit", "model": "qwen3-coder", "thinking": "high",
        },
        "enabled": True, "created_by": "operator", "last_fired_at": None,
        "created_at": NOW, "updated_at": NOW,
    }
    monkeypatch.setattr(repo, "list_heartbeat_schedules", lambda **k: _async([row]))
    monkeypatch.setattr(repo, "list_events", lambda **k: _async([]))

    out = await routes.list_heartbeat_schedules()
    params = out["schedules"][0]["action_params"]
    for key in ("agent_id", "instructions", "model", "thinking"):
        assert key in params, (
            f"a scheduled agent task loses its {key} — the crons dialog can't "
            "prefill it on edit"
        )


# ── cc-crons agents picker: GET /agents → {id, name, taskable, status} ─────


async def test_agents_list_carries_the_fields_the_crons_agent_picker_filters_on(
    monkeypatch,
):
    """cc-crons.ts's `/api/crons/agents` filters on `a.taskable && a.status ===
    'ACTIVE'` then maps to `{id, name}` — losing taskable or status empties
    the "assign to" dropdown silently (everything gets filtered out)."""
    agent = {
        "id": "jira-expert", "name": "Jira Expert", "model": None, "thinking": None,
        "created_at": NOW, "role": "jira", "status": "ACTIVE", "taskable": True,
        "consultable": True, "conversational": True, "deviations": None,
        "template": None, "hired_by": None, "retired_reason": None,
        "retired_at": None, "charter_version": 1,
    }
    monkeypatch.setattr(repo, "list_agents", lambda **k: _async([agent]))
    monkeypatch.setattr(repo, "list_grants", lambda _id: _async([]))

    out = await routes.list_agents()
    row = out["agents"][0]
    assert row["id"] == "jira-expert" and row["name"] == "Jira Expert"
    assert row["taskable"] is True, "without this the crons agent picker filters the row out"
    assert row["status"] == "ACTIVE", "same filter — a missing status also drops the row"


# ── cc-kanban: GET /tasks → CcTask (mapTask) ────────────────────────────────


@needs_pg
async def test_tasks_list_carries_every_field_mapTask_reads():
    """`mapTask` in cc-kanban.ts reads id/title/instructions/agent_id/status/
    session_id/parent_session_id/outcome/created_at/started_at/terminal_at/
    session_status/stopped straight off the wire row — a real INSERT +
    `repo.list_tasks()` round trip, because `session_status`/`stopped` are a
    JOIN + derivation the SQL does, not a stub anyone hand-builds."""
    agent_id = "kanban-wire-agent"
    await repo.upsert_agent(agent_id, "Kanban Wire", "kanban", "")
    parent_session = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(parent_session, agent_id)
    child_session = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(child_session, agent_id)
    conn = await repo._conn()
    try:
        await conn.execute(
            "update session set status = 'STOPPED' where id = $1", child_session
        )
    finally:
        await conn.close()

    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "Reconcile the board", "do it", agent_id)
    await repo.start_task(task_id, child_session)
    conn = await repo._conn()
    try:
        await conn.execute(
            "update task set parent_session_id = $2 where id = $1",
            task_id, parent_session,
        )
    finally:
        await conn.close()

    rows = {r["id"]: r for r in await repo.list_tasks(agent_id=agent_id)}
    row = rows[task_id]

    for key in (
        "id", "title", "instructions", "agent_id", "status", "session_id",
        "parent_session_id", "outcome", "created_at", "started_at",
        "terminal_at", "session_status",
    ):
        assert key in row, f"the kanban board loses its {key} column — mapTask reads it directly"
    assert row["parent_session_id"] == parent_session
    assert row["session_status"] == "STOPPED", (
        "session_status is a joined column — a wrong join silently breaks "
        "the board's `stopped` badge"
    )
    assert row["stopped"] is True, (
        "`stopped` is derived from session_status in `_task_row` — without it "
        "the board never shows the paused badge even though the run is parked"
    )


# ── cc-activity Runs tab: GET /sessions → RunRow (work_subject is SQL) ─────


@needs_pg
async def test_sessions_list_carries_work_subject_for_the_activity_runs_tab():
    """`RunRow.work_subject` (useActivity.ts) is a correlated subquery against
    `work_item` — 70% of runs are ledger drains with no task, and without this
    column the Activity browser's Runs tab prints a bare uuid for all of them
    (repo.list_sessions's own docstring). Needs a real INSERT: the subquery
    lives in SQL, a monkeypatched row would just echo whatever the test wrote."""
    agent_id = "activity-wire-agent"
    await repo.upsert_agent(agent_id, "Activity Wire", "activity", "")
    session_id = "sess_" + uuid.uuid4().hex[:12]
    await repo.create_running_session(session_id, agent_id)

    conn = await repo._conn()
    try:
        await conn.execute(
            "insert into work_item (id, message_id, feed, source, subject,"
            " payload, state, session_id)"
            " values ($1, $2, 'test', 'test', $3, '{}'::jsonb, 'PROCESSED', $4)",
            "wi_" + uuid.uuid4().hex[:12], uuid.uuid4().hex, "Renew the domain",
            session_id,
        )
    finally:
        await conn.close()

    rows = {r["id"]: r for r in await repo.list_sessions(200, agent_id=agent_id)}
    row = rows[session_id]

    for key in (
        "id", "agent_id", "status", "mode", "created_at", "updated_at",
        "closed_reason", "parent_session_id", "task_title", "proposal_count",
        "token_estimate",
    ):
        assert key in row, f"the Activity Runs tab loses its {key} column"
    assert row["work_subject"] == "Renew the domain", (
        "the Runs tab shows a bare uuid for this row without work_subject — "
        "70% of runs are ledger drains with no task"
    )


# ── cc-activity Overview tab: GET /activity/overview → ActivityOverview ────


async def test_activity_overview_carries_every_top_level_field(monkeypatch):
    """`ActivityOverview` (useActivity.ts) reads dispatch/oldest_unprocessed_at/
    failures/dismiss_pending/heartbeat/schedules — the Overview tab composes
    all five into one screen precisely so the operator never reads a dispatch
    number from one instant beside a ledger number from another."""
    dispatch_status = {
        "running": True, "may_dispatch": True, "reason": "ok", "in_flight": 1,
        "awaiting_human": 0, "tokens_spent": 100,
        "valves": {"concurrency": 4, "approval_limit": 3, "token_budget": 1000},
        "ledger": {"UNPROCESSED": 2},
    }
    from central_command.ingest import dispatcher as dispatcher_mod

    monkeypatch.setattr(dispatcher_mod.dispatcher, "status", lambda: _async(dispatch_status))
    monkeypatch.setattr(repo, "list_work_items", lambda *a, **k: _async([]))
    monkeypatch.setattr(repo, "oldest_unprocessed_at", lambda: _async(None))
    monkeypatch.setattr(repo, "count_dismiss_pending", lambda: _async(0))
    monkeypatch.setattr(repo, "list_heartbeat_schedules", lambda **k: _async([]))

    class FakeEngine:
        def status(self):
            return {"running": True, "reason": "ok",
                     "tick_seconds": settings.heartbeat_tick_seconds, "next_due": {}}

    monkeypatch.setattr(routes, "_heartbeat_engine", lambda: FakeEngine())

    out = await routes.activity_overview()

    for key in (
        "dispatch", "oldest_unprocessed_at", "failures", "dismiss_pending",
        "heartbeat", "schedules",
    ):
        assert key in out, f"the Activity Overview tab loses its {key} section"
    assert out["dispatch"] == dispatch_status
    assert out["heartbeat"]["tick_seconds"] == settings.heartbeat_tick_seconds


async def test_activity_overview_failure_rows_carry_the_failures_strip_fields(monkeypatch):
    """`WorkFailure` (useActivity.ts) reads id/subject/kind/source/state/
    session_id/attempts/last_error/enrolled_at/terminal_at — and the endpoint
    itself must drop an already-acknowledged row (`failure_ack`), since the
    strip means "still wants a decision"."""
    open_failure = {
        "id": "wi_open", "subject": "Bounced invoice", "kind": "email",
        "source": "gmail", "state": "FAILED", "session_id": None, "attempts": 3,
        "last_error": "Jira 400", "enrolled_at": NOW, "terminal_at": NOW,
        "payload": {},
    }
    acked_failure = {**open_failure, "id": "wi_acked", "payload": {"failure_ack": True}}

    from central_command.ingest import dispatcher as dispatcher_mod

    monkeypatch.setattr(
        dispatcher_mod.dispatcher, "status",
        lambda: _async({
            "running": False, "may_dispatch": False, "reason": "stopped",
            "in_flight": 0, "awaiting_human": 0, "tokens_spent": 0,
            "valves": {"concurrency": 0, "approval_limit": 0, "token_budget": 0},
            "ledger": {},
        }),
    )
    monkeypatch.setattr(
        repo, "list_work_items", lambda *a, **k: _async([open_failure, acked_failure])
    )
    monkeypatch.setattr(repo, "oldest_unprocessed_at", lambda: _async(None))
    monkeypatch.setattr(repo, "count_dismiss_pending", lambda: _async(0))
    monkeypatch.setattr(repo, "list_heartbeat_schedules", lambda **k: _async([]))

    class FakeEngine:
        def status(self):
            return {"running": False, "reason": "stopped",
                     "tick_seconds": settings.heartbeat_tick_seconds, "next_due": {}}

    monkeypatch.setattr(routes, "_heartbeat_engine", lambda: FakeEngine())

    out = await routes.activity_overview()

    ids = [f["id"] for f in out["failures"]]
    assert ids == ["wi_open"], (
        "an acknowledged failure must drop out of the strip — otherwise it "
        "keeps asking the operator for a decision that's already been made"
    )
    row = out["failures"][0]
    for key in (
        "id", "subject", "kind", "source", "state", "session_id", "attempts",
        "last_error", "enrolled_at", "terminal_at",
    ):
        assert key in row, f"the Failures strip loses its {key} field"


# ── cc-activity Events tab: GET /events, /events/kinds ─────────────────────


async def test_events_list_carries_the_fields_the_events_tab_reads(monkeypatch):
    """`EventRow` (useActivity.ts) reads id/kind/ref_id/actor/payload/
    created_at — losing any of these blanks the audit-trail browser's row."""
    event = {
        "id": 1, "kind": "task.completed", "ref_id": "task_1",
        "actor": "agent:jira-expert", "payload": {"outcome": "done"},
        "created_at": NOW,
    }
    monkeypatch.setattr(repo, "list_events", lambda **k: _async([event]))
    monkeypatch.setattr(repo, "latest_event_id", lambda: _async(1))

    out = await routes.get_events()
    assert out["events"] == [event]
    row = out["events"][0]
    for key in ("id", "kind", "ref_id", "actor", "payload", "created_at"):
        assert key in row, f"the Events tab loses its {key} field"


async def test_event_kinds_carries_kind_and_count(monkeypatch):
    """`EventKind` (useActivity.ts) reads kind/count for the events facet bar."""
    monkeypatch.setattr(repo, "event_kinds", lambda: _async([{"kind": "task.completed", "count": 3}]))

    out = await routes.get_event_kinds()
    assert out["kinds"] == [{"kind": "task.completed", "count": 3}]
