"""An approval executes ONCE (2026-09-17).

The Executor holds a proposal for the length of the write — an add's full
capability probe is 20–40 s — and the old read-then-check guard let every
click in that window pass and execute again: 8 proposals ran 2–4×, 11
duplicate proxy rows, three parallel resumes of one session. The status flip
to EXECUTING is now the lock, taken atomically before the Executor runs.
"""

from __future__ import annotations

import asyncio

import pytest

from central_command.config import settings
from central_command.db import repo
from tests.conftest import needs_pg

pytestmark = needs_pg


async def test_a_second_approve_during_execution_is_refused(monkeypatch):
    from central_command.gateway import executor, gateway
    from central_command.runtime.run import ingest_and_propose
    from central_command.runtime.spike_model import make_spike_model

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(settings, "auditor_enabled", False)

    release = asyncio.Event()
    calls = 0

    async def slow(issue_key, due_date):
        nonlocal calls
        calls += 1
        await release.wait()
        return {"ok": True}

    monkeypatch.setattr(executor.jira, "set_due_date", slow)

    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    sid, pid = run["session_id"], run["proposal_id"]

    first = asyncio.create_task(gateway.approve_and_execute(sid, pid, model=model))
    await asyncio.sleep(0.2)  # the first is inside the Executor now
    assert (await repo.load_proposal(pid))["status"] == "EXECUTING"

    with pytest.raises(gateway.GatewayError, match="not awaiting approval"):
        await gateway.approve_and_execute(sid, pid, model=model)
    with pytest.raises(gateway.GatewayError, match="not awaiting"):
        await gateway.reject_with_feedback(sid, pid, "no", model=model)

    release.set()
    await first
    assert calls == 1
    assert (await repo.load_proposal(pid))["status"] == "EXECUTED"
    executed = [e for e in await repo.list_events(ref_id=pid) if e["kind"] == "proposal.executed"]
    assert len(executed) == 1


async def test_a_crash_before_the_verdict_hands_the_claim_back(monkeypatch):
    from central_command.gateway import executor, gateway
    from central_command.runtime.run import ingest_and_propose
    from central_command.runtime.spike_model import make_spike_model

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(settings, "auditor_enabled", False)

    async def boom(*a, **k):
        raise KeyboardInterrupt  # not an ExecutionFailed: no verdict was reached

    monkeypatch.setattr(executor, "execute", boom)
    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    with pytest.raises(KeyboardInterrupt):
        await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)
    assert (await repo.load_proposal(run["proposal_id"]))["status"] == "AWAITING_HUMAN"
