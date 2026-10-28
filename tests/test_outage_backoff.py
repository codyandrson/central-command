"""Story 4 of epic-outage-doctrine: every parked surface backs off on ONE curve to
one cap setting and retries for as long as the outage lasts; the retry limits
and the exhaustion promotions are gone; a unit failing against a healthy probe
is reported.

What these pin:

- the curve: 60, 120, 240 ... seconds, capped at `retry_backoff_cap_seconds`
  (300 here so the cap shows inside eight failures), on a session resume, an
  approved execution, a task re-run and a ledger item alike;
- after the eighth failure every one is still WAITING — no FAILED, no operator
  item, no `*.exhausted` event;
- a probe-gated skip leaves `attempts` and `not_before` exactly as they were;
- a unit failing three times with its probes up is listed by
  `repo.units_failing_despite_probe` with its last error, and reported exactly
  once (`retry.failing_despite_probe`);
- a SEMANTIC failure on each surface still lands FAILED on the first attempt;
- the retired names are gone from the tree.

The backoff is read off what each park RECORDED (`backoff_seconds` on its event,
`not_before - parked_at` on its record), both computed from one `now` — a frozen
clock by construction, so nothing here sleeps or races the wall clock.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from central_command.api import orchestration, routes
from central_command.config import settings
from central_command.contract import retry_backoff_seconds
from central_command.db import repo
from central_command.gateway import gateway
from central_command.ingest import dispatcher, ledger
from central_command.runtime.run import ingest_and_propose
from central_command.runtime.spike_model import make_spike_model
from tests.conftest import aresolve, event_kinds as _kinds, needs_pg
from tests.test_execution_retry import OUTAGE_TEXT, _graph_spy, _jira_spy, _parked_run
from tests.test_outage_requeue import (
    OUTAGE, _boom_handler, _model_that_raises, _resolver_raising,
)
from tests.test_resume_parking import _boom_on_build, _pending

pytestmark = needs_pg

CAP = 300
EXPECTED = [60, 120, 240, 300, 300, 300, 300, 300]   # eight failures


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "retry_backoff_cap_seconds", CAP)


@pytest.fixture(autouse=True)
async def clean_ledger_rows():
    """The mail items this file enrolls are real rows: remove them afterwards.
    (Leftover PARKS need no per-file settle: `conftest.isolated_queue` settles
    every park before each test.)"""
    yield
    conn = await repo._conn()
    try:
        await conn.execute("delete from work_item where message_id like '<cc-backoff-%'")
    finally:
        await conn.close()


async def _payloads(ref_id: str, kind: str) -> list[dict]:
    return [e["payload"] for e in await repo.list_events(ref_id=ref_id, limit=1000)
            if e["kind"] == kind]


def _gap(record: dict) -> int:
    nb = datetime.fromisoformat(record["not_before"])
    at = datetime.fromisoformat(record["parked_at"])
    return round((nb - at).total_seconds())


async def _operator_items(session_id: str) -> int:
    conn = await repo._conn()
    try:
        return await conn.fetchval(
            "select count(*) from operator_item where session_id = $1", session_id)
    finally:
        await conn.close()


def _no_terminal_trace(kinds: list[str]) -> None:
    assert not [k for k in kinds if k.endswith("exhausted")], kinds


# --- the one curve ------------------------------------------------------------------


def test_the_curve_doubles_from_a_minute_to_the_setting(monkeypatch):
    assert [retry_backoff_seconds(n) for n in range(8)] == EXPECTED
    monkeypatch.setattr(settings, "retry_backoff_cap_seconds", 3600)
    assert retry_backoff_seconds(5) == 1920 and retry_backoff_seconds(6) == 3600
    assert retry_backoff_seconds(100) == 3600
    assert retry_backoff_seconds(3, cap=100) == 100
    assert retry_backoff_seconds(-4) == 60


def test_the_defaults():
    from central_command.config import Settings

    fields = Settings.model_fields
    assert fields["retry_backoff_cap_seconds"].default == 3600
    assert fields["retry_loud_after_attempts"].default == 3


async def test_a_session_resume_backs_off_and_never_exhausts(monkeypatch):
    _jira_spy(monkeypatch)
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    async def resume_boom(*a, **k):
        raise OUTAGE

    monkeypatch.setattr(gateway, "resume", resume_boom)
    await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)
    gaps = [_gap(await _pending(run["session_id"]))]               # failure 1
    from central_command.runtime import agent

    for _ in range(7):                                              # failures 2..8
        _boom_on_build(monkeypatch, agent, OUTAGE)
        out = await orchestration.resume_parked_session(run["session_id"], model=model)
        assert out["parked"] is True
        gaps.append(_gap(await _pending(run["session_id"])))

    assert gaps == EXPECTED
    parked = await _payloads(run["session_id"], "session.resume_parked")
    assert [p["backoff_seconds"] for p in parked] == EXPECTED
    assert await repo.session_status(run["session_id"]) == "AWAITING_RESUME"
    assert await _operator_items(run["session_id"]) == 0
    _no_terminal_trace(await _kinds(run["session_id"]))


async def test_an_approved_execution_backs_off_and_never_exhausts(monkeypatch):
    parked = await _parked_run(monkeypatch)                         # failure 1
    gaps = [_gap((await repo.load_proposal(parked["proposal_id"]))["retry_state"])]
    for _ in range(7):                                              # failures 2..8
        _graph_spy(monkeypatch, fail=OUTAGE_TEXT)
        out = await gateway.retry_execution(parked["proposal_id"], model=parked["model"])
        assert out["execution"] == "parked"
        gaps.append(_gap((await repo.load_proposal(parked["proposal_id"]))["retry_state"]))

    assert gaps == EXPECTED
    events = await _payloads(parked["proposal_id"], "proposal.execution_parked")
    assert [p["backoff_seconds"] for p in events] == EXPECTED
    row = await repo.load_proposal(parked["proposal_id"])
    assert row["status"] == "RETRY_PENDING" and row["retry_state"]["attempts"] == 8
    assert await _operator_items(parked["session_id"]) == 0
    kinds = await _kinds(parked["proposal_id"])
    _no_terminal_trace(kinds)
    assert "proposal.failed" not in kinds


async def test_a_task_backs_off_and_never_exhausts(monkeypatch):
    # The fresh retry park: the outage hits before the first request exists
    # (a model that dies on a persisted request parks the TURN since story 6).
    monkeypatch.setattr(routes, "resolve_model", _resolver_raising(OUTAGE))
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "backoff test", "x", "jira-expert")
    task = await repo.get_task(task_id)

    await routes._run_assigned_task_detached(task, "operator")      # failure 1
    gaps = [_gap((await repo.get_task(task_id))["retry_state"])]
    for _ in range(7):                                              # failures 2..8
        out = await orchestration.retry_parked_task(task_id)
        assert out["parked"] is True
        gaps.append(_gap((await repo.get_task(task_id))["retry_state"]))

    assert gaps == EXPECTED
    row = await repo.get_task(task_id)
    assert row["status"] == "ASSIGNED" and row["retry_state"]["attempts"] == 8
    if row.get("session_id"):
        assert await _operator_items(row["session_id"]) == 0
    kinds = await _kinds(task_id)
    _no_terminal_trace(kinds)
    assert "task.run_failed" not in kinds


async def test_a_ledger_item_backs_off_and_never_fails(monkeypatch):
    _boom_handler(monkeypatch, OUTAGE)
    raw = (f"Message-ID: <cc-backoff-{uuid.uuid4().hex[:8]}@example.com>\n"
           "From: sender@example.com\nSubject: Backoff test\n"
           "Date: Wed, 1 Jul 2026 09:00:00 +0000\n\nBody.\n")
    await ledger.enroll_email(raw)
    item_id = None
    for _ in range(8):
        item = await repo.claim_next_work_item("sess_backoff")
        assert item is not None
        item_id = item["id"]
        out = await dispatcher.process_claimed(item)
        assert out["transient"] is True
        # Due-ness is a timestamp, not a sleep: make it claimable again.
        conn = await repo._conn()
        try:
            await conn.execute("update work_item set not_before = now() - interval '1 second' "
                               "where id = $1", item_id)
        finally:
            await conn.close()

    released = await _payloads(item_id, "work.released")
    assert [p["backoff_seconds"] for p in released] == EXPECTED
    kinds = await _kinds(item_id)
    assert "work.failed" not in kinds
    _no_terminal_trace(kinds)


# --- a gated skip changes nothing ------------------------------------------------------


async def test_a_probe_gated_sweep_leaves_attempts_and_not_before(monkeypatch):
    parked = await _parked_run(monkeypatch)
    pid = parked["proposal_id"]
    conn = await repo._conn()
    try:  # due now, so only the probe gate can hold it back
        await conn.execute(
            "update proposal set retry_state = jsonb_set(retry_state, '{not_before}', "
            "to_jsonb($2::text)) where id = $1",
            pid, (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    finally:
        await conn.close()
    before = (await repo.load_proposal(pid))["retry_state"]

    from central_command.heartbeat import probes

    async def _down():
        return False

    monkeypatch.setitem(probes.PROBES, "graph", _down)
    result = await orchestration.retry_sweep()

    assert pid in [s["id"] for s in result["skipped_gated"]]
    after = (await repo.load_proposal(pid))["retry_state"]
    assert after["attempts"] == before["attempts"]
    assert after["not_before"] == before["not_before"]


async def test_the_recurring_sweep_does_not_offer_a_unit_before_its_not_before(
    monkeypatch,
):
    parked = await _parked_run(monkeypatch)                         # not_before = +60 s

    async def _must_not_run(*a, **k):
        raise AssertionError("the sweep offered a proposal still inside its backoff")

    monkeypatch.setattr(gateway, "retry_execution", _must_not_run)
    result = await orchestration.retry_sweep()
    assert parked["proposal_id"] not in result["retried_executions"]
    assert parked["proposal_id"] not in [s["id"] for s in result["skipped_gated"]]


# --- the loud rule -----------------------------------------------------------------------


async def test_a_unit_failing_despite_a_healthy_probe_is_reported_once(monkeypatch):
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "loud test", "x", "jira-expert")
    # Three transient failures (403 stays transient, the operator's ruling);
    # every probe answers up (the suite's default).
    deny = ModelHTTPError(403, "cc-default", body="denied by the edge firewall")
    for attempt in (1, 2, 3):
        assert await orchestration.park_task_retry(task_id, deny, attempts=attempt)

    failing = await repo.units_failing_despite_probe(settings.retry_loud_after_attempts)
    mine = [u for u in failing if u["id"] == task_id]
    assert mine == [{"unit": "task", "id": task_id, "failures": 3,
                     "dependencies": ["llm"], "last_error": str(deny)[:500]}]

    first = await orchestration.retry_sweep()
    await orchestration.retry_sweep()
    assert {"unit": "task", "id": task_id} in first["failing_despite_probe"]
    reported = await _payloads(task_id, "retry.failing_despite_probe")
    assert len(reported) == 1
    assert reported[0]["last_error"] == str(deny)[:500]
    # It keeps retrying: reporting changes nothing about the unit.
    assert (await repo.get_task(task_id))["status"] == "ASSIGNED"


async def test_a_unit_whose_dependency_is_down_is_not_failing_despite_the_probe():
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "loud test 2", "x", "jira-expert")
    for attempt in (1, 2, 3):
        assert await orchestration.park_task_retry(
            task_id, ConnectionError("Connection refused"), attempts=attempt)
    await repo.record_dependency_probe("llm", False, "refused")

    failing = await repo.units_failing_despite_probe(3)
    assert task_id not in [u["id"] for u in failing]


async def test_below_the_threshold_nothing_is_failing():
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "loud test 3", "x", "jira-expert")
    for attempt in (1, 2):
        assert await orchestration.park_task_retry(
            task_id, ConnectionError("Connection refused"), attempts=attempt)
    assert task_id not in [u["id"] for u in await repo.units_failing_despite_probe(3)]


# --- NO still fails at once -----------------------------------------------------------------


async def test_a_semantic_execution_failure_lands_failed_on_the_first_attempt(monkeypatch):
    parked = await _parked_run(monkeypatch, fail="400 invalid episode body")
    assert (await repo.load_proposal(parked["proposal_id"]))["status"] == "FAILED"


async def test_a_semantic_task_failure_lands_failed_on_the_first_attempt(monkeypatch):
    monkeypatch.setattr(routes, "resolve_model", aresolve(
        lambda *a, **k: _model_that_raises(RuntimeError("field 'due' is not a date"))))
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "semantic test", "x", "jira-expert")
    await routes._run_assigned_task_detached(await repo.get_task(task_id), "operator")
    assert (await repo.get_task(task_id))["status"] == "FAILED"


async def test_a_semantic_resume_failure_is_terminal_on_the_first_attempt(monkeypatch):
    _jira_spy(monkeypatch)
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    async def resume_boom(*a, **k):
        raise OUTAGE

    monkeypatch.setattr(gateway, "resume", resume_boom)
    await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)
    from central_command.runtime import agent

    _boom_on_build(monkeypatch, agent, ValueError("the model answered nonsense"))
    out = await orchestration.resume_parked_session(run["session_id"], model=model)
    assert out.get("parked") is not True
    assert await repo.session_status(run["session_id"]) != "AWAITING_RESUME"
    assert "session.resume_failed" in await _kinds(run["session_id"])


async def test_a_semantic_ledger_failure_is_capped_by_dispatch_max_attempts(monkeypatch):
    monkeypatch.setattr(settings, "dispatch_max_attempts", 1)
    _boom_handler(monkeypatch, RuntimeError("field 'due_date' is not a valid date"))
    raw = (f"Message-ID: <cc-backoff-{uuid.uuid4().hex[:8]}@example.com>\n"
           "From: sender@example.com\nSubject: Semantic test\n"
           "Date: Wed, 1 Jul 2026 09:00:00 +0000\n\nBody.\n")
    await ledger.enroll_email(raw)
    item = await repo.claim_next_work_item("sess_semantic")
    await dispatcher.process_claimed(item)
    assert "work.failed" in await _kinds(item["id"])


# --- the retired names are gone -------------------------------------------------------------


def test_the_retired_limits_are_gone_from_the_tree():
    """`grep -rn` for the three settings and the promotion helpers over
    central_command/ and tests/ finds nothing. The patterns are assembled so
    this file does not match itself; the startup warning names the ENV keys in
    upper case (CC_..._LIMIT), which a case-sensitive grep does not match."""
    names = ["resume" + "_retry_limit", "execution" + "_retry_limit",
             "task" + "_retry_limit", "_promote" + "_exhausted",
             "session.resume" + "_exhausted", "proposal.execution" + "_exhausted",
             "task.run" + "_exhausted"]
    pattern = re.compile("|".join(re.escape(n) for n in names))
    root = Path(__file__).resolve().parents[1]
    hits = [f"{p.relative_to(root)}:{i}"
            for base in ("central_command", "tests")
            for p in (root / base).rglob("*.py")
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1)
            if pattern.search(line)]
    assert hits == []


def test_a_retired_key_in_the_environment_is_warned_about_once(monkeypatch, caplog):
    from central_command.api import app as app_module

    monkeypatch.setenv("CC_TASK" + "_RETRY_LIMIT", "5")
    with caplog.at_level(logging.WARNING):
        app_module._warn_retired_settings()
    warnings = [r for r in caplog.records if "ignored retired setting" in r.getMessage()]
    assert len(warnings) == 1
    assert "CC_TASK" + "_RETRY_LIMIT" in warnings[0].getMessage()

    caplog.clear()
    monkeypatch.delenv("CC_TASK" + "_RETRY_LIMIT")
    for key in ("CC_RESUME" + "_RETRY_LIMIT", "CC_EXECUTION" + "_RETRY_LIMIT"):
        monkeypatch.delenv(key, raising=False)
    app_module._warn_retired_settings()
    assert not [r for r in caplog.records if "ignored retired setting" in r.getMessage()]
