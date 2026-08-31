"""Heartbeat (D27) guard tests.

The invariant, stated once: approval attaches to what the work DOES, never to
the trigger. The heartbeat schedules work through levers the operator already
has and can neither perform nor approve a write. These tests make that
structural — the import ban, the closed registry's lever allowlist, the
event-before-action ordering, the born-disabled seeds, the skip-missed-fires
policy, and the singleton lease.

Also here: quiet firings (2026-07-25). An action that did nothing writes
nothing to the append-only log — but which actions may do that is an explicit
allowlist below, loud is the default, and errors are always material.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from central_command.config import settings
from central_command.heartbeat import actions as hb_actions
from central_command.heartbeat.engine import (
    HEARTBEAT_LEASE_KEY,
    HeartbeatEngine,
    compute_next_due,
    fire,
    validate_schedule,
)
from tests.conftest import needs_pg

HEARTBEAT_DIR = Path(hb_actions.__file__).parent
SCHEMA = (Path(hb_actions.__file__).parents[1] / "db" / "schema.sql").read_text(encoding="utf-8")

# The ONE gateway symbol the heartbeat may touch: a pure read over the event
# log. Anything wider — executor, gateway.gateway, the auditor's ability to
# close dismissals — is banned below.
ALLOWED_GATEWAY_IMPORT = "from central_command.gateway.auditor import agreement_report"


def test_heartbeat_never_imports_the_gate():
    """The trust boundary, heartbeat edition (same pattern as the runtime
    import ban): no approve/execute machinery is reachable from this package."""
    for py in HEARTBEAT_DIR.glob("*.py"):
        source = py.read_text(encoding="utf-8")
        stripped = source.replace(ALLOWED_GATEWAY_IMPORT, "")
        assert "central_command.gateway" not in stripped, (
            f"{py.name} imports the gateway tier beyond the allowed "
            f"agreement_report read"
        )
        for banned in ("approve_and_execute", "executor.execute", "audit_dismissal"):
            assert banned not in source, f"{py.name} references {banned}"


def test_action_registry_parity_with_the_lever_allowlist():
    """The registry is executable truth: every action's levers must be in this
    explicit allowlist. Extending the registry means extending this test in
    the same commit — reviewable, like a pack change."""
    allowlist = {
        "ingest.feed.poll_once",            # idempotent enrollment
        "ingest.dispatcher.start",          # bounded drain window...
        "ingest.dispatcher.stop",           # ...that closes itself
        "api.routes.create_and_run_task",   # THE task-create path
        "gateway.auditor.agreement_report", # pure read (the one exemption)
        "runtime.converse.conversation_turn", # the S2 nudge: one ordinary turn
        "api.orchestration.retry_sweep",    # re-drives units parked by an outage
                                            # through their EXISTING drivers
        "api.orchestration.graph_verify_sweep", # audits approved graph episodes
                                            # post-ingestion; opens no gate —
                                            # it reads the graph, records
                                            # verdicts, and parks rows for the
                                            # operator
        "integrations.sandbox_client.exec_cmd", # runs an already-synced script
                                            # in the owning agent's own sandbox
        "integrations.litellm_credstore.list_provider_credentials", # LiteLLM's
                                            # OWN stored credentials, decrypted
        "integrations.litellm.provider_catalog", # a credential's own live catalog
        "integrations.litellm.list_models",  # the proxy's configured deployments
        "integrations.litellm.check_model_health", # per-deployment health
        "integrations.litellm.probe_model", # measures undeclared capabilities
        "ingest.watcher.walk_source",       # deterministic inventory: stat +
                                            # extract, no LLM, no ledger writes
        "ingest.catalog_enroll.enroll_pending", # the catalog backlog onto the
                                            # ordinary ledger, at the source's
                                            # own pace
        "ingest.wiki_freshness.sweep",      # compares recorded evidence
                                            # versions against the catalog and
                                            # enrolls one repair item per
                                            # stale page — deterministic, no
                                            # LLM, no gate opened
    }
    seen = set()
    for kind, spec in hb_actions.ACTIONS.items():
        assert kind == spec.kind
        assert spec.levers, f"{kind} declares no levers"
        for lever in spec.levers:
            assert lever in allowlist, f"{kind} touches undeclared lever {lever}"
            seen.add(lever)
        assert set(spec.required) <= set(spec.params), (
            f"{kind} requires params it does not document"
        )
        assert asyncio.iscoroutinefunction(spec.run)
    assert seen == allowlist, f"allowlist entries no action uses: {allowlist - seen}"


def test_validate_action_rejects_bad_input():
    with pytest.raises(ValueError, match="unknown action kind"):
        hb_actions.validate_action("jira.write_everything", {})
    with pytest.raises(ValueError, match="requires param"):
        hb_actions.validate_action("task.create", {"agent_id": "jira-expert"})
    with pytest.raises(ValueError, match="no param"):
        hb_actions.validate_action("feed.poll", {"surprise": 1})


def test_enum_params_are_enforced_mechanically():
    """`ActionSpec.choices` (2026-07-30). Documenting the legal values in the
    param's prose would be guidance with no mechanism behind it: the schedule
    would be created happily and fail later, inside a background loop, at the
    hour nobody is watching."""
    for good in ("digest", "check_in", "morning_report", "week_ahead"):
        hb_actions.validate_action("ea.contact", {"kind": good})
    with pytest.raises(ValueError, match="must be one of"):
        hb_actions.validate_action("ea.contact", {"kind": "hourly_nag"})
    with pytest.raises(ValueError, match="requires param"):
        hb_actions.validate_action("ea.contact", {})
    # Every declared choice set must name a param the action documents, or the
    # enforcement would silently apply to nothing.
    for kind, spec in hb_actions.ACTIONS.items():
        for name, allowed in spec.choices.items():
            assert name in spec.params, f"{kind} constrains undocumented param {name!r}"
            assert allowed, f"{kind} declares an empty choice set for {name!r}"


def test_schedule_math_and_validation():
    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)

    assert compute_next_due("every", {"every_seconds": 300}, now) == now + timedelta(
        seconds=300
    )
    # 02:00 America/Denver = 08:00 UTC (MDT): asked at noon UTC, the next
    # occurrence is tomorrow's — cron math is timezone-honest.
    nxt = compute_next_due(
        "cron", {"expr": "0 2 * * *", "tz": "America/Denver"}, now
    )
    assert nxt == datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)
    # A future one-shot is due at its moment; a passed one is NEVER due again —
    # this None is the skip-missed policy for one-shots.
    assert compute_next_due("at", {"at": "2026-07-23T13:00:00+00:00"}, now) is not None
    assert compute_next_due("at", {"at": "2026-07-23T11:00:00+00:00"}, now) is None

    for kind, schedule in [
        ("cron", {"expr": "not a cron"}),
        ("cron", {"expr": "0 2 * * *", "tz": "Mars/Olympus"}),
        ("every", {"every_seconds": 1}),
        ("at", {"at": "yesterday-ish"}),
        ("hourly", {}),
    ]:
        with pytest.raises((ValueError, KeyError, Exception)):
            validate_schedule(kind, schedule)


def test_seeds_are_born_disabled_and_reseed_safe():
    """The never-starts-by-surprise rule applied to data, checked at the
    offline floor (the schema text — same idiom as the roster seed test)."""
    assert "create table if not exists heartbeat_schedule" in SCHEMA
    seed_start = SCHEMA.index("insert into heartbeat_schedule")
    # The statement ends at its conflict clause, not the first ';' — the
    # jira-hygiene mission text legitimately contains one.
    seed_end = SCHEMA.index("on conflict (id) do nothing", seed_start)
    seed_sql = SCHEMA[seed_start:seed_end]
    for sid in ("mail-poll", "drain-window", "jira-hygiene", "audit-agreement",
                "discussion-sweep", "ea-morning-report", "ea-checkin",
                "ea-digest"):
        assert f"'{sid}'" in seed_sql, f"seed {sid} missing"
    # `enabled` is absent from the insert column list, so every seed takes the
    # column default (false) — and a re-seed can never re-enable or overwrite.
    assert "enabled" not in seed_sql.split("values")[0]
    # Every seeded action kind must exist in the registry (executable truth).
    for kind in ("feed.poll", "dispatch.window", "task.create",
                 "report.audit_agreement", "discussion.sweep", "ea.contact"):
        assert kind in hb_actions.ACTIONS
        assert f"'{kind}'" in seed_sql
    # A seeded enum param must be a value the action can actually render — the
    # seed goes in with `on conflict do nothing` and nothing validates it on
    # the way, so a typo would first surface at 07:30 in a background loop.
    for params in ('{"kind": "morning_report"}', '{"kind": "check_in"}',
                   '{"kind": "digest"}'):
        assert params in seed_sql
        hb_actions.validate_action("ea.contact", json.loads(params))


class _Recorder:
    """Captures events.emit calls in order, across modules."""

    def __init__(self, monkeypatch):
        self.kinds: list[str] = []

        async def _emit(kind, ref_id=None, payload=None, actor=None):
            self.kinds.append(kind)
            return {"kind": kind}

        from central_command import events

        monkeypatch.setattr(events, "emit", _emit)


async def test_fired_is_recorded_before_the_action_runs(monkeypatch):
    """The event-before-the-thing rule: heartbeat.fired precedes the action's
    own record, and heartbeat.completed follows it."""
    rec = _Recorder(monkeypatch)

    async def _touch(schedule_id):
        rec.kinds.append("touch:last_fired")

    from central_command.db import repo

    monkeypatch.setattr(repo, "touch_heartbeat_fired", _touch)

    async def _stub_action(schedule_id, params):
        rec.kinds.append("action:ran")
        return {"ok": True}

    monkeypatch.setitem(
        hb_actions.ACTIONS, "stub.action",
        hb_actions.ActionSpec(
            kind="stub.action", description="test stub", params={},
            required=(), levers=("ingest.feed.poll_once",), run=_stub_action,
        ),
    )
    result = await fire(
        {"id": "s1", "action_kind": "stub.action", "action_params": {}}
    )
    assert result["ok"]
    assert rec.kinds == [
        "heartbeat.fired", "touch:last_fired", "action:ran", "heartbeat.completed",
    ]


async def test_a_failing_action_lands_error_and_still_stamps_fired(monkeypatch):
    rec = _Recorder(monkeypatch)

    async def _touch(schedule_id):
        rec.kinds.append("touch:last_fired")

    from central_command.db import repo

    monkeypatch.setattr(repo, "touch_heartbeat_fired", _touch)

    async def _boom(schedule_id, params):
        raise RuntimeError("provider flap")

    monkeypatch.setitem(
        hb_actions.ACTIONS, "stub.boom",
        hb_actions.ActionSpec(
            kind="stub.boom", description="test stub", params={},
            required=(), levers=("ingest.feed.poll_once",), run=_boom,
        ),
    )
    result = await fire({"id": "s1", "action_kind": "stub.boom", "action_params": {}})
    assert not result["ok"]
    # last_fired stamped BEFORE the run: a failing action must not refire
    # every tick forever.
    assert rec.kinds == ["heartbeat.fired", "touch:last_fired", "heartbeat.error"]


def test_quiet_eligibility_is_an_explicit_allowlist():
    """Which actions may skip the log when nothing happened is reviewable
    truth, like the lever allowlist: loud is the DEFAULT, and going quiet means
    editing this test in the same commit.

    An action qualifies only if it is non-authorising (starts no gated work,
    opens no gate — so its heartbeat row is provenance, not authorisation) AND
    self-recording (whatever it actually does emits its own event).
    """
    quiet_allowlist = {
        "feed.poll",        # enrollment only; new mail emits work.enrolled itself
        "dispatch.window",  # a window that did not open touched nothing
        "discussion.sweep", # a sweep with nothing stalled read rows and stopped;
                            # every flag emits its own discussion.stalled, and a
                            # nudge (an agent turn) is never immaterial
        "retry.sweep",      # NON-AUTHORISING: every unit it re-drives was
                            # authorised before it parked (an approved
                            # proposal's decision, an operator's task, a resume
                            # already under way) and it opens no gate.
                            # SELF-RECORDING: each driver emits its own
                            # session.resume_retried / proposal.execution_retried
                            # / task.run_retried. Nothing parked — the common
                            # tick — must cost no history.
        "sandbox.run_script", # NON-AUTHORISING: the script runs ungated, under
                            # grants the agent already holds, exactly like the
                            # sandbox pack's own tools. SELF-RECORDING: only
                            # material when it created the evaluation task, and
                            # that creation emits task.created itself.
        "graph.verify_sweep", # NON-AUTHORISING: it opens no gate — reads the
                            # graph, records verdicts, parks rows; its one
                            # close (active mode, aligned, addition-only) is a
                            # verification bookkeeping flip, never a world
                            # change. SELF-RECORDING: every parked row emits
                            # graph.verification.parked, every verdict
                            # graph.audit.verdict, every auto-close
                            # graph.verification.confirmed. The common tick
                            # finds no due rows and must write nothing.
        "litellm.discovery", # NON-AUTHORISING: the diff is plain code and opens
                            # no gate; the task it may create runs the
                            # litellm-manager under its own existing grants.
                            # SELF-RECORDING: task creation emits task.created
                            # itself. The common tick — nothing enrolled, or
                            # nothing drifted — costs no history.
        "source.walk",      # NON-AUTHORISING: it writes inventory rows and
                            # enrolls ledger items — nothing is WORKED until
                            # the dispatcher's valves say so, and every
                            # proposal that follows still gates.
                            # SELF-RECORDING: the walk emits
                            # catalog.walk.completed, the sweep
                            # catalog.enrolled / catalog.version.autofolded.
                            # A tree nobody touched must cost no history.
        "wiki.freshness",   # NON-AUTHORISING: it enrolls repair items —
                            # nothing is WORKED until the dispatcher's valves
                            # say so, and the repair proposal still gates. The
                            # 'auto' annotation it may write is the operator's
                            # own graduated action class (CC_WIKI_ANNOTATION_
                            # MODE), off by default. SELF-RECORDING: a changed
                            # stale set emits wiki.claims.stale, an enrollment
                            # work.enrolled, a panel wiki.page.annotated. A
                            # wiki whose sources have not moved must cost no
                            # history.
    }
    quiet = {k for k, s in hb_actions.ACTIONS.items() if s.material is not None}
    assert quiet == quiet_allowlist, (
        f"quiet-eligibility changed without updating this test: {quiet ^ quiet_allowlist}"
    )
    # task.create starts real agent work and report.audit_agreement is a
    # daily record — both must stay loud (fired BEFORE the action). ea.contact
    # spends tokens and may interrupt the operator, and a contact DEFERRED for
    # budget is the firing they most need on the log when the digest never
    # arrives — so it is loud in both outcomes.
    for kind in ("task.create", "report.audit_agreement", "ea.contact"):
        assert hb_actions.ACTIONS[kind].material is None, f"{kind} must stay loud"

    # The declared predicates, on the shapes their actions actually return.
    poll = hb_actions.ACTIONS["feed.poll"].material
    assert not poll({"refs": 4, "new": 0, "enrolled": 0})
    assert poll({"refs": 4, "new": 1, "enrolled": 1})
    window = hb_actions.ACTIONS["dispatch.window"].material
    assert not window({"opened": False, "reason": "drain already running"})
    assert window({"opened": True, "minutes": 10})
    sweep = hb_actions.ACTIONS["discussion.sweep"].material
    assert not sweep({"checked": 3, "stalled": [], "nudged": []})
    assert sweep({"checked": 3, "stalled": ["item_1"], "nudged": []})
    assert sweep({"checked": 3, "stalled": [], "nudged": ["item_1"]})
    retry = hb_actions.ACTIONS["retry.sweep"].material
    zero = {"resumed": [], "retried_executions": [], "retried_tasks": [],
            "skipped_gated": [], "errors": []}
    assert not retry(zero)
    # A tick that ONLY skipped gated units did nothing either: no attempt was
    # spent, and the outage is already on the log from the failures that parked
    # the units. The skip list rides the result for the ticks that ARE material.
    assert not retry({**zero, "skipped_gated": [{"unit": "session", "id": "s"}]})
    for did_something in ("resumed", "retried_executions", "retried_tasks",
                          "errors"):
        assert retry({**zero, did_something: ["x"]}), did_something
    run_script = hb_actions.ACTIONS["sandbox.run_script"].material
    assert not run_script({"exit_code": 0, "success": True})
    assert run_script({"exit_code": 1, "success": False, "task_id": "task_1"})


async def _fire_quiet_stub(monkeypatch, result, *, material):
    """Fire a quiet-eligible stub action returning `result`; return the
    recorded event kinds."""
    rec = _Recorder(monkeypatch)

    async def _touch(schedule_id):
        rec.kinds.append("touch:last_fired")

    from central_command.db import repo

    monkeypatch.setattr(repo, "touch_heartbeat_fired", _touch)

    async def _stub_action(schedule_id, params):
        rec.kinds.append("action:ran")
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setitem(
        hb_actions.ACTIONS, "stub.quiet",
        hb_actions.ActionSpec(
            kind="stub.quiet", description="quiet-eligible test stub", params={},
            required=(), levers=("ingest.feed.poll_once",), run=_stub_action,
            material=material,
        ),
    )
    out = await fire({"id": "s1", "action_kind": "stub.quiet", "action_params": {}})
    return rec.kinds, out


async def test_an_immaterial_quiet_firing_writes_nothing_to_the_log(monkeypatch):
    """The 576-rows-a-day fix: a poll that enrolled nothing leaves the
    append-only log untouched — but STILL stamps last_fired_at, which is where
    cadence liveness lives (the crons tab's "last run")."""
    kinds, out = await _fire_quiet_stub(
        monkeypatch, {"new": 0}, material=lambda r: bool(r.get("new"))
    )
    assert kinds == ["touch:last_fired", "action:ran"], "a no-op wrote to the log"
    assert out == {"ok": True, "result": {"new": 0}, "quiet": True}


async def test_a_material_quiet_firing_is_recorded(monkeypatch):
    """Quiet is about NOTHING-HAPPENED, never about hiding work: the moment the
    action does something, the firing lands on the log."""
    kinds, out = await _fire_quiet_stub(
        monkeypatch, {"new": 2}, material=lambda r: bool(r.get("new"))
    )
    assert kinds == ["touch:last_fired", "action:ran", "heartbeat.completed"]
    assert out["ok"] and "quiet" not in out


async def test_a_quiet_action_still_records_its_failures(monkeypatch):
    """Failure is material for every action, quiet-eligible or not."""
    kinds, out = await _fire_quiet_stub(
        monkeypatch, RuntimeError("provider flap"), material=lambda r: False
    )
    assert kinds == ["touch:last_fired", "action:ran", "heartbeat.error"]
    assert not out["ok"]


async def test_a_quiet_recovery_clears_a_recorded_error_in_the_crons_view(monkeypatch):
    """Quiet firings write no outcome event, so the crons tab's derived
    last_status must not show a failure the schedule has since recovered from.
    last_fired_at newer than the last outcome == clean firings since."""
    from central_command.api import routes
    from central_command.db import repo

    failed_at = datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc)

    async def _events(**kwargs):
        return [{
            "kind": "heartbeat.error", "created_at": failed_at,
            "payload": {"error": "provider flap"},
        }]

    monkeypatch.setattr(repo, "list_events", _events)
    base = {
        "id": "mail-poll", "schedule_kind": "every",
        "schedule": {"every_seconds": 300}, "action_kind": "feed.poll",
    }

    # Nothing has fired since the failure: the error still stands.
    view = await routes._heartbeat_schedule_view({**base, "last_fired_at": failed_at})
    assert view["last_status"] == "error"
    assert view["last_error"] == "provider flap"

    # A later firing wrote no outcome — it was quiet, therefore it succeeded.
    view = await routes._heartbeat_schedule_view(
        {**base, "last_fired_at": failed_at + timedelta(minutes=5)}
    )
    assert view["last_status"] == "ok"
    assert view["last_error"] is None


async def test_missed_fires_are_skipped_never_replayed(monkeypatch):
    """A schedule that was due many times while the engine was down fires ZERO
    times when the engine first sees it — next due is computed from now."""
    rec = _Recorder(monkeypatch)
    stale = {
        "id": "mail-poll", "schedule_kind": "every",
        "schedule": {"every_seconds": 300}, "action_kind": "feed.poll",
        "action_params": {}, "enabled": True,
        "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "last_fired_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    from central_command.db import repo

    async def _list(enabled_only=False):
        return [stale]

    monkeypatch.setattr(repo, "list_heartbeat_schedules", _list)

    engine = HeartbeatEngine()
    await engine._tick()
    assert "heartbeat.fired" not in rec.kinds, "a missed occurrence was replayed"
    due = engine._next_due["mail-poll"]
    assert due is not None and due > datetime.now(timezone.utc)


async def test_run_now_works_with_the_engine_stopped(monkeypatch):
    rec = _Recorder(monkeypatch)
    from central_command.db import repo

    async def _touch(schedule_id):
        pass

    async def _get(schedule_id):
        return {"id": schedule_id, "action_kind": "stub.now", "action_params": {}}

    monkeypatch.setattr(repo, "touch_heartbeat_fired", _touch)
    monkeypatch.setattr(repo, "get_heartbeat_schedule", _get)

    async def _stub(schedule_id, params):
        return {"ran": True}

    monkeypatch.setitem(
        hb_actions.ACTIONS, "stub.now",
        hb_actions.ActionSpec(
            kind="stub.now", description="test stub", params={},
            required=(), levers=("ingest.feed.poll_once",), run=_stub,
        ),
    )
    engine = HeartbeatEngine()
    assert not engine.running
    result = await engine.run_now("anything")
    assert result["ok"] and result["result"] == {"ran": True}
    assert rec.kinds == ["heartbeat.fired", "heartbeat.completed"]


# --- lease (needs a real Postgres, same gate as the dispatcher lease tests) -----


@needs_pg
async def test_second_heartbeat_is_refused_while_the_lease_is_held(monkeypatch):
    from central_command.db import repo

    # Long tick + an empty schedule list: started loops must idle, never fire
    # anything real in the shared dev database.
    monkeypatch.setattr(settings, "heartbeat_tick_seconds", 60.0)

    async def _none(enabled_only=False):
        return []

    monkeypatch.setattr(repo, "list_heartbeat_schedules", _none)

    a, b = HeartbeatEngine(), HeartbeatEngine()
    since = await repo.latest_event_id()
    await a.start()
    try:
        assert a.running
        await b.start()
        assert not b.running
        assert "lease" in b.last_reason
        refused = await repo.list_events(since_id=since, kind="heartbeat.lease_refused")
        assert refused, "the refusal must be on the record"
    finally:
        await a.stop()
        await b.stop()
    assert HEARTBEAT_LEASE_KEY != 0x67760001, "must not share the dispatcher's lease"


# --- ea.contact: the attention loop's trigger (2026-07-30) ---------------------
# These drive `fire()` with `create_and_run_task` stubbed. The point is the
# ACTION's own contract — what window it reads, what it hands the agent, and
# that a deferral costs nothing — not the agent run behind it, which
# tests/test_ea.py walks end to end on demo models.


def _ea_schedule(schedule_id="ea-digest", kind="digest"):
    return {"id": schedule_id, "action_kind": "ea.contact",
            "action_params": {"kind": kind}}


@pytest.fixture()
async def ea_stubs(monkeypatch):
    """Silence the parts that are not under test: the hire, the snapshot, and
    the run. `create_and_run_task` is recorded rather than called — a real run
    here would spend tokens and put a task on the shared dev board."""
    calls: dict = {"tasks": [], "snapshots": []}

    # Unlimited by default: the budget has its own tests, and leaving it at the
    # shipped 3 would make these pass or fail on how much unrelated EA traffic
    # the shared dev database already saw today.
    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 0)

    from central_command.api import routes
    from central_command.reports import ea_report
    from central_command.runtime import ea

    async def fake_ensure():
        return None

    async def fake_snapshot(since):
        calls["snapshots"].append(since)
        return {"window": {"since": since.isoformat(), "until": "now"},
                "queues": {"proposals_awaiting_decision": 2},
                "activity": {"proposal.created": {"count": 1, "examples": [],
                                                  "truncated": 0}}}

    async def fake_create(instructions, title, agent_id, actor="operator", **kw):
        calls["tasks"].append({"instructions": instructions, "title": title,
                               "agent_id": agent_id, "actor": actor})
        return {"task_id": "task_ea_stub", "session_id": "sess_ea_stub",
                "deferred": False}

    monkeypatch.setattr(ea, "ensure_registered", fake_ensure)
    monkeypatch.setattr(ea_report, "snapshot", fake_snapshot)
    monkeypatch.setattr(routes, "create_and_run_task", fake_create)
    yield calls


@needs_pg
async def test_ea_contact_hands_the_agent_a_fenced_data_block(monkeypatch, ea_stubs):
    """Zero tool calls to produce the numbers: the snapshot rides the brief."""
    from central_command.db import repo

    since_id = await repo.latest_event_id()
    out = await fire(_ea_schedule())
    assert out["ok"] and out["result"]["delivered"] is True

    brief = ea_stubs["tasks"][0]["instructions"]
    assert "```json" in brief and '"queues"' in brief
    assert "END-OF-DAY WRAP-UP" in brief
    assert ea_stubs["tasks"][0]["agent_id"] == "ea"
    # The actor names the schedule, so the record says WHY the agent ran.
    assert ea_stubs["tasks"][0]["actor"] == "heartbeat:ea-digest"

    delivered = await repo.list_events(since_id=since_id, kind="ea.contact_delivered")
    assert delivered and delivered[0]["payload"]["kind"] == "digest"


@needs_pg
async def test_the_window_anchors_on_delivery_not_on_last_fired(monkeypatch, ea_stubs):
    """`engine.fire` stamps `last_fired_at` BEFORE the action, so the schedule
    row can only say when the engine last TRIED. The window anchors on the
    newest ea.contact_delivered OF THIS KIND instead — which is also what makes
    a budget-deferred contact lossless."""
    from central_command import events
    from central_command.db import repo

    anchor = await events.emit(
        "ea.contact_delivered", ref_id="ea-digest",
        payload={"kind": "digest"}, actor="heartbeat",
    )
    # Another kind's delivery must NOT move the digest's window.
    await events.emit("ea.contact_delivered", ref_id="ea-checkin",
                      payload={"kind": "check_in"}, actor="heartbeat")

    await fire(_ea_schedule())
    used = ea_stubs["snapshots"][-1]
    row = await repo.get_event(anchor["id"])
    assert used == row["created_at"], (
        "the window must start at this kind's newest delivery"
    )


@needs_pg
async def test_a_deferred_contact_spends_nothing(monkeypatch, ea_stubs):
    """Over budget: the event lands, no task is created, no model is reached —
    and crucially NO ea.contact_delivered, so the next contact of this kind
    still covers this window."""
    from central_command.db import repo

    monkeypatch.setattr(settings, "ea_contact_budget_per_day", 3)

    async def spent(kind, actor, since):
        return 3

    monkeypatch.setattr(repo, "count_events_since", spent)

    since_id = await repo.latest_event_id()
    out = await fire(_ea_schedule())

    assert out["ok"] and out["result"]["delivered"] is False
    assert ea_stubs["tasks"] == [], "a task was created over budget"
    deferred = await repo.list_events(since_id=since_id, kind="ea.contact_deferred")
    assert deferred and deferred[0]["payload"]["budget"] == 3
    assert not await repo.list_events(
        since_id=since_id, kind="ea.contact_delivered"
    ), "a deferral must not stamp the delivery anchor"
    # And the firing itself is still LOUD — the operator wondering where the
    # digest went finds it on the log.
    assert await repo.list_events(since_id=since_id, kind="heartbeat.fired")


# --- sandbox.run_script: scheduled evaluation runs (2026-08-05) ----------------
# Runs `.run` directly (not `fire()`) — its own contract (exec + task-on-
# failure-or-flag) is what's under test, no DB needed once the sandbox HTTP
# client and the task-create path are stubbed.


@pytest.fixture()
def sandbox_stubs(monkeypatch):
    """Stub the sandbox-runner client and create_and_run_task — a real call
    here would need a live runner and would spend tokens on a real task."""
    from central_command.api import routes
    from central_command.integrations import sandbox_client

    calls: dict = {"tasks": [], "exec": []}
    state = {"exit_code": 0, "stdout": "ok\n", "stderr": "", "result_json": None}

    async def fake_create_session(agent_id, session_id):
        return {"sandbox_id": "cc-sbx-stub"}

    async def fake_exec_cmd(sandbox_id, command, timeout=None):
        calls["exec"].append(command)
        return {"exit_code": state["exit_code"], "stdout": state["stdout"],
                "stderr": state["stderr"]}

    async def fake_read_file(sandbox_id, path):
        if state["result_json"] is None:
            raise RuntimeError("404: no such file")
        import json

        return {"content": json.dumps(state["result_json"]), "truncated": False}

    async def fake_create(instructions, title, agent_id, actor="operator", **kw):
        calls["tasks"].append({"instructions": instructions, "title": title,
                               "agent_id": agent_id, "actor": actor})
        return {"task_id": "task_eval_stub", "session_id": "sess_eval_stub"}

    monkeypatch.setattr(sandbox_client, "create_session", fake_create_session)
    monkeypatch.setattr(sandbox_client, "exec_cmd", fake_exec_cmd)
    monkeypatch.setattr(sandbox_client, "read_file", fake_read_file)
    monkeypatch.setattr(routes, "create_and_run_task", fake_create)
    calls["state"] = state
    return calls


def _run_script_params(**extra):
    return {"agent_id": "jira-expert", "session_id": "sess-1",
            "command": "python3 script.py", **extra}


async def test_a_failing_run_always_creates_the_task(sandbox_stubs):
    sandbox_stubs["state"]["exit_code"] = 1
    out = await hb_actions.ACTIONS["sandbox.run_script"].run(
        "s1", _run_script_params()
    )
    assert out["success"] is False and out["task_id"] == "task_eval_stub"
    assert sandbox_stubs["tasks"][0]["agent_id"] == "jira-expert"
    assert sandbox_stubs["tasks"][0]["actor"] == "heartbeat:s1"


async def test_a_clean_run_with_the_flag_off_creates_no_task(sandbox_stubs):
    out = await hb_actions.ACTIONS["sandbox.run_script"].run(
        "s1", _run_script_params()
    )
    assert out["success"] is True and "task_id" not in out
    assert sandbox_stubs["tasks"] == []


async def test_a_clean_run_with_the_flag_on_still_creates_the_task(sandbox_stubs):
    out = await hb_actions.ACTIONS["sandbox.run_script"].run(
        "s1", _run_script_params(success_creates_task="true")
    )
    assert out["success"] is True and out["task_id"] == "task_eval_stub"
    assert len(sandbox_stubs["tasks"]) == 1


async def test_result_json_rides_the_report_when_the_script_wrote_one(sandbox_stubs):
    sandbox_stubs["state"]["exit_code"] = 1
    sandbox_stubs["state"]["result_json"] = {"passed": 3, "failed": 1}
    out = await hb_actions.ACTIONS["sandbox.run_script"].run(
        "s1", _run_script_params()
    )
    assert out["result_json"] == {"passed": 3, "failed": 1}
    assert '"passed": 3' in sandbox_stubs["tasks"][0]["instructions"]


async def test_missing_result_json_is_not_an_error(sandbox_stubs):
    """Not every script writes one — a failed read must not crash the run."""
    out = await hb_actions.ACTIONS["sandbox.run_script"].run(
        "s1", _run_script_params()
    )
    assert out["result_json"] is None


async def test_a_raising_exec_call_is_material_through_fire(monkeypatch, sandbox_stubs):
    """The run() function propagates a sandbox-client failure; `fire()` is
    where every action's errors become material — this pins that
    sandbox.run_script is no exception."""
    from central_command.integrations import sandbox_client

    async def boom(sandbox_id, session_id):
        raise RuntimeError("runner unreachable")

    monkeypatch.setattr(sandbox_client, "create_session", boom)

    rec = _Recorder(monkeypatch)
    from central_command.db import repo

    async def _touch(schedule_id):
        rec.kinds.append("touch:last_fired")

    monkeypatch.setattr(repo, "touch_heartbeat_fired", _touch)

    out = await fire({
        "id": "s1", "action_kind": "sandbox.run_script",
        "action_params": _run_script_params(),
    })
    assert not out["ok"]
    assert rec.kinds == ["touch:last_fired", "heartbeat.error"], (
        "sandbox.run_script is quiet-eligible, so heartbeat.fired stays "
        "silent on the happy path — but a raised error must still land"
    )


@needs_pg
async def test_an_unrenderable_kind_fails_loudly(monkeypatch, ea_stubs):
    """The belt behind `validate_action`'s braces: a row edited straight in SQL
    must error, never silently pick a default framing."""
    out = await fire(_ea_schedule(kind="hourly_nag"))
    assert not out["ok"] and "must be one of" in out["error"]
    assert ea_stubs["tasks"] == []


@needs_pg
async def test_schedule_runs_route_clamps_a_negative_limit():
    """A negative limit used to bypass `min(limit, 200)` and reach SQL raw."""
    from central_command.api import routes

    out = await routes.heartbeat_schedule_runs("no-such-schedule", limit=-5)
    assert out["events"] == []


# --- litellm.discovery: scheduled model autodiscovery (2026-08-20 redesign) --
# Autodiscovery is CREDENTIAL-DRIVEN: enrollment is a credential stored in
# LiteLLM's own credential table (read via integrations.litellm_credstore),
# never a Central Command app_setting, and ownership is `credential_name` on a
# deployment, never a `cc_managed` marker. The diff (`compute_discovery_drift`)
# is plain code — tested directly with fabricated catalog/deployment data, no
# agent and no DB involved. The action is driven via `.run()` directly, like
# sandbox.run_script above, with `litellm_credstore.list_provider_credentials`,
# `repo.get_app_setting` (skip list only), the litellm client, and
# `create_and_run_task` all stubbed — a real run would need a live proxy, a
# live LiteLLM Postgres, and a live provider key.


def test_compute_discovery_drift_missing_stale_skip_and_hand_configured():
    catalog = [
        {"id": "claude-new", "display_name": "Claude New"},
        {"id": "claude-old", "display_name": "Claude Old"},
        {"id": "claude-skipped", "display_name": "Claude Skipped"},
    ]
    deployments = [
        # OUR managed deployment (tied to this credential) for a model the
        # catalog no longer lists.
        {"model_name": "cc-managed-retired", "provider": "anthropic",
         "provider_model": "anthropic/claude-retired",
         "credential_name": "anthropic-main"},
        # A HAND-TUNED deployment for claude-old — configured, but not ours
        # (no credential_name); claude-old must not show up as missing (it
        # already exists) and cc-managed-retired, not this row, is what goes
        # stale.
        {"model_name": "cc-hand-tuned", "provider": "anthropic",
         "provider_model": "anthropic/claude-old",
         "credential_name": None},
    ]
    unhealthy = [
        {"model": "cc-managed-retired", "error": "timeout"},
        {"model": "cc-hand-tuned", "error": "timeout"},  # must be ignored: not managed
    ]
    drift = hb_actions.compute_discovery_drift(
        "anthropic-main", "anthropic", catalog, deployments, unhealthy,
        skip=["claude-skipped"],
    )
    assert [m["id"] for m in drift["missing"]] == ["claude-new"]
    assert [d["model_name"] for d in drift["stale"]] == ["cc-managed-retired"]
    assert [u["model"] for u in drift["unhealthy"]] == ["cc-managed-retired"]


def test_compute_discovery_drift_quiet_when_nothing_moved():
    catalog = [{"id": "claude-x"}]
    deployments = [{"model_name": "cc-x", "provider": "anthropic",
                    "provider_model": "anthropic/claude-x",
                    "credential_name": "anthropic-main"}]
    drift = hb_actions.compute_discovery_drift(
        "anthropic-main", "anthropic", catalog, deployments, unhealthy=[], skip=[]
    )
    assert drift == {"missing": [], "stale": [], "unhealthy": []}


def test_compute_discovery_drift_matches_on_model_prefix_when_provider_unset():
    # Live proxy fact (2026-08-20): a deployment's explicit `provider`
    # (custom_llm_provider) is usually None — the provider rides only as the
    # "anthropic/" model prefix. Such a deployment must still count as
    # configured (not re-proposed as missing) and as OURS when its
    # credential_name matches.
    catalog = [{"id": "claude-x"}]
    deployments = [{"model_name": "cc-x", "provider": None,
                    "provider_model": "anthropic/claude-x",
                    "credential_name": "anthropic-main"}]
    unhealthy = [{"model": "cc-x", "error": "timeout"}]
    drift = hb_actions.compute_discovery_drift(
        "anthropic-main", "anthropic", catalog, deployments, unhealthy, skip=[]
    )
    assert drift["missing"] == []
    assert [u["model"] for u in drift["unhealthy"]] == ["cc-x"]


@pytest.fixture()
def discovery_stubs(monkeypatch):
    from central_command.api import routes
    from central_command.db import repo
    from central_command.integrations import litellm as litellm_client
    from central_command.integrations import litellm_credstore

    calls: dict = {"tasks": []}
    state = {
        "credentials": [
            {"credential_name": "anthropic-main", "provider": "anthropic",
             "values": {"api_key": "sk-ant-stub"}},
        ],
        "decisions": {"skip": []},
        "deployments": [], "unhealthy": [], "catalog": [],
        "open_counts": {},  # credential_name -> count, generation-guard input
        "probes": {},  # alias -> probe_model() return (or an Exception instance)
    }
    probed: list[str] = []

    async def fake_list_provider_credentials():
        return state["credentials"]

    async def fake_count_nonterminal_tasks_with_title_prefix(prefix):
        # prefix is "LiteLLM autodiscovery: <credential> batch"
        for name, n in state["open_counts"].items():
            if prefix == f"LiteLLM autodiscovery: {name} batch":
                return n
        return 0

    async def fake_get_app_setting(key, default):
        if key == "autodiscovery_decisions":
            return state["decisions"]
        return default

    async def fake_list_models():
        return {"models": state["deployments"]}

    async def fake_check_model_health(model=None):
        return {"unhealthy": state["unhealthy"]}

    async def fake_provider_catalog(provider, api_key, api_base=None):
        return state["catalog"]

    async def fake_probe_model(alias, **kw):
        probed.append(alias)
        result = state["probes"].get(alias, {"suggested_model_info": {}})
        if isinstance(result, Exception):
            raise result
        return result

    async def fake_create(instructions, title, agent_id, actor="operator", background=False, **kw):
        assert background is True  # discovery tasks always run detached
        task_id = f"task_discovery_stub_{len(calls['tasks'])}"
        calls["tasks"].append({"instructions": instructions, "title": title,
                               "agent_id": agent_id, "actor": actor})
        return {"task_id": task_id}

    monkeypatch.setattr(litellm_credstore, "list_provider_credentials", fake_list_provider_credentials)
    monkeypatch.setattr(repo, "get_app_setting", fake_get_app_setting)
    monkeypatch.setattr(repo, "count_nonterminal_tasks_with_title_prefix",
                        fake_count_nonterminal_tasks_with_title_prefix)
    monkeypatch.setattr(litellm_client, "list_models", fake_list_models)
    monkeypatch.setattr(litellm_client, "check_model_health", fake_check_model_health)
    monkeypatch.setattr(litellm_client, "provider_catalog", fake_provider_catalog)
    monkeypatch.setattr(litellm_client, "probe_model", fake_probe_model)
    monkeypatch.setattr(routes, "create_and_run_task", fake_create)
    calls["state"] = state
    calls["probed"] = probed
    return calls


async def test_no_credentials_stored_skips_everything(discovery_stubs):
    discovery_stubs["state"]["credentials"] = []
    out = await hb_actions.ACTIONS["litellm.discovery"].run("s1", {})
    assert out == {"skipped": "no credentials stored"}
    assert discovery_stubs["tasks"] == []


async def test_credstore_unconfigured_is_quiet_but_broken_is_loud(discovery_stubs, monkeypatch):
    # This action is quiet-eligible, so a swallowed credstore failure would
    # read as "nothing to do" silently forever. Unset config = valid quiet
    # skip; a wrong salt key (or any other failure) must RAISE so fire()
    # records heartbeat.error — errors are always material.
    from central_command.integrations import litellm_credstore

    async def unconfigured():
        raise litellm_credstore.CredStoreError(
            "list_provider_credentials — CC_LITELLM_DB_URL not set"
        )

    monkeypatch.setattr(litellm_credstore, "list_provider_credentials", unconfigured)
    out = await hb_actions.ACTIONS["litellm.discovery"].run("s1", {})
    assert out["skipped"].endswith("not set")

    async def wrong_key():
        raise litellm_credstore.CredStoreError(
            "every field of credential 'x' failed to decrypt (wrong salt key?)"
        )

    monkeypatch.setattr(litellm_credstore, "list_provider_credentials", wrong_key)
    with pytest.raises(litellm_credstore.CredStoreError):
        await hb_actions.ACTIONS["litellm.discovery"].run("s1", {})
    assert discovery_stubs["tasks"] == []


async def test_no_drift_creates_no_task(discovery_stubs):
    discovery_stubs["state"]["catalog"] = [{"id": "claude-x"}]
    discovery_stubs["state"]["deployments"] = [
        {"model_name": "cc-x", "provider": "anthropic",
         "provider_model": "anthropic/claude-x", "credential_name": None},
    ]
    out = await hb_actions.ACTIONS["litellm.discovery"].run("s1", {})
    assert out == {"drift": False}
    assert discovery_stubs["tasks"] == []


async def test_drift_creates_one_task_carrying_the_findings(discovery_stubs):
    discovery_stubs["state"]["catalog"] = [{"id": "claude-new"}]
    out = await hb_actions.ACTIONS["litellm.discovery"].run("s1", {})
    assert out["drift"] is True and out["task_ids"] == ["task_discovery_stub_0"]
    assert out["credentials"] == ["anthropic-main"]
    assert out["queued"] == 1
    assert "backlog" not in out
    task = discovery_stubs["tasks"][0]
    assert task["agent_id"] == "litellm-manager"
    assert task["actor"] == "heartbeat:s1"
    assert task["title"] == "LiteLLM autodiscovery: anthropic-main batch 1/1"
    assert '"claude-new"' in task["instructions"]
    assert "supports_vision" in task["instructions"]
    assert "litellm_credential_name" in task["instructions"]


async def test_health_is_probed_per_managed_alias_only(discovery_stubs, monkeypatch):
    # Whole-fleet /health walks the hand-tuned local fleet (GPU swaps, 30s
    # timeout — measured live 2026-08-20). The action must probe health one
    # scoped call per MANAGED alias (credential_name tied to a stored
    # credential), and a probe that raises must read as unhealthy, not kill
    # the tick.
    from central_command.integrations import litellm as litellm_client

    probed = []

    async def fake_health(model=None):
        probed.append(model)
        raise litellm_client.LiteLLMError("boom")

    monkeypatch.setattr(litellm_client, "check_model_health", fake_health)
    discovery_stubs["state"]["catalog"] = [{"id": "claude-x"}]
    discovery_stubs["state"]["deployments"] = [
        {"model_name": "cc-x", "provider": "anthropic",
         "provider_model": "anthropic/claude-x",
         "credential_name": "anthropic-main"},
        {"model_name": "cc-hand-tuned", "provider": "anthropic",
         "provider_model": "anthropic/claude-x", "credential_name": None},
    ]
    out = await hb_actions.ACTIONS["litellm.discovery"].run("s1", {})
    assert probed == ["cc-x"]  # never the hand-tuned alias
    assert out["drift"] is True
    task = discovery_stubs["tasks"][0]
    assert "LiteLLMError: boom" in task["instructions"]


async def test_one_bad_credential_does_not_block_the_rest(discovery_stubs, monkeypatch):
    from central_command.integrations import litellm as litellm_client

    discovery_stubs["state"]["credentials"] = [
        {"credential_name": "anthropic-main", "provider": "anthropic",
         "values": {"api_key": "sk-ant-stub"}},
        {"credential_name": "broken-cred", "provider": "anthropic",
         "values": {"api_key": "sk-ant-bad"}},
    ]
    discovery_stubs["state"]["catalog"] = [{"id": "claude-new"}]

    async def fake_catalog(provider, api_key, api_base=None):
        if api_key == "sk-ant-bad":
            raise litellm_client.LiteLLMError("provider answered 401")
        return [{"id": "claude-new"}]

    monkeypatch.setattr(litellm_client, "provider_catalog", fake_catalog)
    out = await hb_actions.ACTIONS["litellm.discovery"].run("s1", {})
    assert out["drift"] is True
    assert sorted(out["credentials"]) == ["anthropic-main", "broken-cred"]
    assert "provider answered 401" in json.dumps(discovery_stubs["tasks"][0]["instructions"])


def test_litellm_discovery_is_quiet_only_without_a_task():
    material = hb_actions.ACTIONS["litellm.discovery"].material
    assert material({"drift": False}) is False
    assert material({"drift": True, "task_ids": ["t1"]}) is True
    assert material({"drift": True, "task_ids": []}) is False
    # A pass that tasked nothing new because it's all already queued from an
    # earlier pass is still material — the operator should see the backlog.
    assert material({"drift": True, "task_ids": [], "backlog": {"anthropic-main": 3}}) is True


def test_maintenance_brief_instructs_on_errors_and_carries_no_missing():
    # 2026-08-21: an errored (blind) credential rode a task that completed
    # quietly — the brief must direct the agent to park via ask_operator.
    # The maintenance brief is the one carrying errors/stale/unhealthy; it
    # must never carry a `missing` key.
    brief = hb_actions._maintenance_brief({"openai": {"error": "boom"}})
    assert "ask_operator" in brief
    assert "blind" in brief
    assert "skip list" in brief
    assert '"missing"' not in brief


def test_add_brief_carries_only_its_slice_and_skip_guidance():
    brief = hb_actions._add_brief("anthropic-main", [{"id": "claude-new"}])
    assert '"claude-new"' in brief
    assert "anthropic-main" in brief
    assert "skip list" in brief
    assert "shutdown" in brief or "deprecation" in brief
    assert '"missing"' not in brief  # the slice is bare models, not a findings wrapper


async def test_bad_batch_size_raises(discovery_stubs):
    discovery_stubs["state"]["catalog"] = [{"id": "claude-new"}]
    with pytest.raises(ValueError):
        await hb_actions.ACTIONS["litellm.discovery"].run("s1", {"batch_size": "0"})
    with pytest.raises(ValueError):
        await hb_actions.ACTIONS["litellm.discovery"].run("s1", {"batch_size": "nope"})
    assert discovery_stubs["tasks"] == []


async def test_large_missing_list_chunks_into_all_tasks_no_cap(discovery_stubs):
    # 12 missing models, batch_size 5 -> three add tasks of 5/5/2, all
    # created in the same pass — the per-agent queue drains them, no
    # per-pass cap any more.
    discovery_stubs["state"]["catalog"] = [{"id": f"claude-{i}"} for i in range(12)]
    out = await hb_actions.ACTIONS["litellm.discovery"].run("s1", {"batch_size": "5"})
    assert out["drift"] is True
    assert len(out["task_ids"]) == 3
    assert out["queued"] == 3
    assert "backlog" not in out
    titles = sorted(t["title"] for t in discovery_stubs["tasks"])
    assert titles == [
        "LiteLLM autodiscovery: anthropic-main batch 1/3",
        "LiteLLM autodiscovery: anthropic-main batch 2/3",
        "LiteLLM autodiscovery: anthropic-main batch 3/3",
    ]
    sizes = sorted(json.loads(t["instructions"].rsplit("```json\n", 1)[1].rstrip("`\n")).__len__() for t in discovery_stubs["tasks"])
    assert sizes == [2, 5, 5]


async def test_maintenance_task_created_first(discovery_stubs, monkeypatch):
    # An errored credential forces a maintenance task, created before any
    # add-chunk task.
    from central_command.integrations import litellm as litellm_client

    discovery_stubs["state"]["credentials"] = [
        {"credential_name": "anthropic-main", "provider": "anthropic",
         "values": {"api_key": "sk-ant-stub"}},
        {"credential_name": "broken-cred", "provider": "anthropic",
         "values": {"api_key": "sk-ant-bad"}},
    ]
    discovery_stubs["state"]["catalog"] = [{"id": f"claude-{i}"} for i in range(12)]

    async def fake_catalog(provider, api_key, api_base=None):
        if api_key == "sk-ant-bad":
            raise litellm_client.LiteLLMError("provider answered 401")
        return [{"id": f"claude-{i}"} for i in range(12)]

    monkeypatch.setattr(litellm_client, "provider_catalog", fake_catalog)
    out = await hb_actions.ACTIONS["litellm.discovery"].run("s1", {"batch_size": "5"})
    assert out["drift"] is True
    assert len(out["task_ids"]) == 4  # 1 maintenance + 3 add chunks
    assert discovery_stubs["tasks"][0]["title"] == "LiteLLM autodiscovery: maintenance"
    assert "provider answered 401" in discovery_stubs["tasks"][0]["instructions"]
    assert '"missing"' not in discovery_stubs["tasks"][0]["instructions"]


async def test_generation_guard_skips_credential_with_open_batch_task(discovery_stubs):
    # A daily pass mid-drain must not re-task models already queued from an
    # earlier pass — the guard reports the outstanding count as `backlog`
    # instead of creating anything new for that credential.
    discovery_stubs["state"]["catalog"] = [{"id": f"claude-{i}"} for i in range(3)]
    discovery_stubs["state"]["open_counts"] = {"anthropic-main": 1}
    out = await hb_actions.ACTIONS["litellm.discovery"].run("s1", {})
    assert out["drift"] is True
    assert out["task_ids"] == []
    assert out["queued"] == 0
    assert out["backlog"] == {"anthropic-main": 3}
    assert discovery_stubs["tasks"] == []


def _managed_deployment(alias, *, capabilities=None, provider_model="anthropic/claude-x",
                        provider="anthropic"):
    """A MANAGED deployment row shaped like `list_models()`'s output, quiet on
    the catalog diff (its provider_model already matches a configured catalog
    entry) so tests can isolate the undeclared-capability probe."""
    return {
        "model_name": alias, "model_id": f"{alias}-id",
        "credential_name": "anthropic-main", "provider": provider,
        "provider_model": provider_model,
        "capabilities": capabilities if capabilities is not None else {
            "mode": None, "supports_function_calling": None,
            "supports_response_schema": None, "supports_vision": True,
        },
    }


async def test_undeclared_alias_is_probed_and_lands_in_the_maintenance_brief(discovery_stubs):
    discovery_stubs["state"]["catalog"] = [{"id": "claude-x"}]
    discovery_stubs["state"]["deployments"] = [_managed_deployment("cc-x")]
    discovery_stubs["state"]["probes"]["cc-x"] = {
        "suggested_model_info": {"supports_vision": True, "mode": "chat"},
        "notes": ["measured"],
    }
    out = await hb_actions.ACTIONS["litellm.discovery"].run("s1", {})
    assert out["drift"] is True
    assert discovery_stubs["probed"] == ["cc-x"]
    task = discovery_stubs["tasks"][0]
    assert task["title"] == "LiteLLM autodiscovery: maintenance"
    assert "UNDECLARED" in task["instructions"]
    assert "cc-x-id" in task["instructions"]
    assert '"supports_vision": true' in task["instructions"]


async def test_probe_batch_caps_how_many_undeclared_aliases_are_probed(discovery_stubs):
    discovery_stubs["state"]["catalog"] = [{"id": "claude-x"}]
    discovery_stubs["state"]["deployments"] = [
        _managed_deployment(f"cc-{i}") for i in range(5)
    ]
    out = await hb_actions.ACTIONS["litellm.discovery"].run("s1", {"probe_batch": "2"})
    assert len(discovery_stubs["probed"]) == 2
    # Nothing suggested by the default stub probe, so this pass is quiet.
    assert out == {"drift": False}


async def test_declared_and_bridge_aliases_are_never_probed(discovery_stubs):
    declared = _managed_deployment("cc-declared", capabilities={
        "mode": "chat", "supports_function_calling": True,
        "supports_response_schema": True, "supports_vision": False,
    })
    bridge = _managed_deployment(
        "cc-bridge", provider_model="openai/chat_completions/graphiti-llm",
        provider="openai",  # excluded from the anthropic-credential's catalog diff too
    )
    discovery_stubs["state"]["catalog"] = [{"id": "claude-x"}]
    discovery_stubs["state"]["deployments"] = [declared, bridge]
    out = await hb_actions.ACTIONS["litellm.discovery"].run("s1", {})
    assert discovery_stubs["probed"] == []
    assert out == {"drift": False}


async def test_a_probe_exception_becomes_an_error_entry_and_the_tick_completes(discovery_stubs):
    discovery_stubs["state"]["catalog"] = [{"id": "claude-x"}]
    discovery_stubs["state"]["deployments"] = [_managed_deployment("cc-x")]
    discovery_stubs["state"]["probes"]["cc-x"] = RuntimeError("provider flap")
    out = await hb_actions.ACTIONS["litellm.discovery"].run("s1", {})
    assert out["drift"] is True
    task = discovery_stubs["tasks"][0]
    assert "RuntimeError: provider flap" in task["instructions"]
