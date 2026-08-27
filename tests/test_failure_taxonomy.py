"""Slice 1 of outage equivalence: the failure taxonomy.

Two properties, and only two, because slice 1 changes no behaviour:

1. `classify_failure` / `classify_failure_text` put every shape this deployment
   produces on the right side of the transient/semantic line — and put anything
   they do not recognise on the SEMANTIC side, because a real error hidden
   behind a quiet retry is the worse failure (no-action-is-a-claim).
2. Every error-class event the spine emits carries the honest `transient` label,
   through the REAL emit path, so the record starts accumulating evidence before
   anything retries.
"""

from __future__ import annotations

import asyncio
import errno
import socket
import uuid

import httpx
import pytest
from pydantic_ai.exceptions import (
    FallbackExceptionGroup,
    ModelAPIError,
    ModelHTTPError,
)

from central_command.config import settings
from central_command.contract import (
    classify_failure,
    classify_failure_text,
    is_transient,
)
from central_command.db import repo
from tests.conftest import needs_pg

# --- the classifier ------------------------------------------------------------

TRANSIENT_CASES = [
    ("http 429 (litellm cooldown / rate limit)", ModelHTTPError(429, "cc-default")),
    ("http 408", ModelHTTPError(408, "cc-default")),
    ("http 500", ModelHTTPError(500, "cc-default")),
    ("http 502", ModelHTTPError(502, "cc-default")),
    ("http 503", ModelHTTPError(503, "cc-default")),
    ("http 504", ModelHTTPError(504, "cc-default")),
    ("http 529 (anthropic overloaded)", ModelHTTPError(529, "cc-default")),
    ("every fallback leg was transient", FallbackExceptionGroup(
        "all models failed",
        [ModelHTTPError(429, "primary"), ModelHTTPError(503, "fallback")],
    )),
    ("httpx connect error", httpx.ConnectError("connection refused")),
    ("httpx read timeout", httpx.ReadTimeout("read timed out")),
    ("httpx write timeout", httpx.WriteTimeout("write timed out")),
    ("httpx pool timeout", httpx.PoolTimeout("pool exhausted")),
    ("httpx remote protocol error", httpx.RemoteProtocolError("server disconnected")),
    ("asyncio timeout", asyncio.TimeoutError()),
    ("python ConnectionError", ConnectionResetError("reset by peer")),
    ("dns failure", socket.gaierror("name resolution failed")),
    ("network OSError", OSError(errno.ECONNREFUSED, "Connection refused")),
    ("stringified litellm cooldown", RuntimeError("No deployments available")),
    ("stringified n8n boot", RuntimeError("Database is not ready!")),
]

SEMANTIC_CASES = [
    ("http 400 — the provider judged the request", ModelHTTPError(400, "cc-default")),
    ("http 401", ModelHTTPError(401, "cc-default")),
    ("http 404", ModelHTTPError(404, "cc-default")),
    # A conflict IS an answer: re-sending gets the same conflict.
    ("http 409", ModelHTTPError(409, "cc-default")),
    ("http 422", ModelHTTPError(422, "cc-default")),
    ("one fallback leg answered 400", FallbackExceptionGroup(
        "all models failed",
        [ModelHTTPError(429, "primary"), ModelHTTPError(400, "fallback")],
    )),
    ("an application error", RuntimeError("404 issue does not exist")),
    ("a validation error", ValueError("evidence[0].claim is required")),
    ("an unknown error", Exception("something we have never seen")),
    ("no exception at all", None),
]


@pytest.mark.parametrize("label,exc", TRANSIENT_CASES, ids=[c[0] for c in TRANSIENT_CASES])
def test_transient_shapes(label, exc):
    assert classify_failure(exc) == "transient", label
    assert is_transient(exc) is True


@pytest.mark.parametrize("label,exc", SEMANTIC_CASES, ids=[c[0] for c in SEMANTIC_CASES])
def test_semantic_shapes(label, exc):
    assert classify_failure(exc) == "semantic", label
    assert is_transient(exc) is False


def test_unknown_defaults_to_semantic_not_transient():
    """The direction the doctrine fixes: an unrecognised error is LOUD. Misfiling
    a real error as retryable hides it behind a quiet retry loop."""

    class NeverSeenBefore(Exception):
        pass

    assert classify_failure(NeverSeenBefore("mystery")) == "semantic"


def test_usage_limit_exceeded_is_semantic_not_transient():
    """The run-request loop breaker firing is US stopping the run, not the
    dependency being down. Retrying a looping run just loops again, so it must
    promote to the operator instead of entering the retry machinery — and the
    type check must decide it, since the message ("exceeded"/"limit") is not a
    shape the text sniff would ever recognise."""
    from pydantic_ai.exceptions import UsageLimitExceeded

    exc = UsageLimitExceeded("The next request would exceed the request_limit of 50")
    assert classify_failure(exc) == "semantic"
    assert is_transient(exc) is False


def test_nested_exception_groups_are_flattened():
    inner = FallbackExceptionGroup(
        "inner", [ModelHTTPError(429, "a"), httpx.ConnectError("refused")]
    )
    outer = FallbackExceptionGroup("outer", [inner, ModelHTTPError(503, "b")])
    assert classify_failure(outer) == "transient"

    poisoned = FallbackExceptionGroup(
        "outer", [inner, ModelHTTPError(400, "judged")]
    )
    assert classify_failure(poisoned) == "semantic"


def test_model_api_error_follows_its_cause():
    """pydantic-ai wraps a provider APIConnectionError as a bare ModelAPIError;
    the cause is what says the socket never landed."""
    exc = ModelAPIError("cc-default", "Connection error.")
    try:
        raise exc from httpx.ConnectError("refused")
    except ModelAPIError as e:
        assert classify_failure(e) == "transient"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("litellm.RateLimitError: No deployments available", "transient"),
        ("Rate limited. Try again in 41s", "transient"),
        ("Database is not ready!", "transient"),
        ("deployment in cooldown", "transient"),
        ("HTTP 429 Too Many Requests", "transient"),
        ("read timed out", "transient"),
        ("Connection refused", "transient"),
        ("Service temporarily unavailable", "transient"),
        ("404: issue DEMO-9 does not exist", "semantic"),
        ("field 'due_date' is not a valid date", "semantic"),
        ("", "semantic"),
        (None, "semantic"),
    ],
)
def test_text_sniffing(text, expected):
    assert classify_failure_text(text) == expected


# --- the label rides every error event ----------------------------------------


async def _payload(kind: str, ref_id: str) -> dict:
    events = [e for e in await repo.list_events(ref_id=ref_id) if e["kind"] == kind]
    assert events, f"no {kind} event for {ref_id}"
    return events[-1]["payload"]


@needs_pg
async def test_proposal_failed_and_resume_failed_carry_the_label(monkeypatch):
    """The gateway's two error events, on the real execution-failure path: a
    Jira 404 is the dependency ANSWERING, so both read semantic."""
    from central_command.gateway import executor, gateway
    from central_command.runtime.run import ingest_and_propose
    from central_command.runtime.spike_model import make_spike_model

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "live")

    async def broken(issue_key, due_date):
        raise RuntimeError("404 issue does not exist")

    monkeypatch.setattr(executor.jira, "set_due_date", broken)

    async def resume_boom(*a, **k):
        raise RuntimeError("404 issue does not exist")

    monkeypatch.setattr(gateway, "resume", resume_boom)

    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    await gateway.approve_and_execute(
        run["session_id"], run["proposal_id"], model=model
    )

    assert (await _payload("proposal.failed", run["proposal_id"]))["transient"] is False
    assert (await _payload("session.resume_failed", run["session_id"]))["transient"] is False


@needs_pg
async def test_a_provider_outage_parks_the_execution_labelled_transient(monkeypatch):
    """Slice 1 wrote this as `proposal.failed` carrying `transient: true` — the
    label riding a terminal state, which was the whole point of a taxonomy that
    changed no behaviour yet. Slice 3 spends it: the same outage now parks the
    proposal RETRY_PENDING and the label rides `proposal.execution_parked`
    instead. A transient failure no longer produces a FAILED proposal at all,
    so asserting that event here would be asserting the bug."""
    from central_command.gateway import executor, gateway
    from central_command.runtime.run import ingest_and_propose
    from central_command.runtime.spike_model import make_spike_model

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "live")

    async def down(issue_key, due_date):
        raise RuntimeError("Connection refused talking to the Jira façade")

    monkeypatch.setattr(executor.jira, "set_due_date", down)

    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    await gateway.approve_and_execute(
        run["session_id"], run["proposal_id"], model=model
    )

    parked = await _payload("proposal.execution_parked", run["proposal_id"])
    assert parked["transient"] is True
    assert [e["kind"] for e in await repo.list_events(ref_id=run["proposal_id"])].count(
        "proposal.failed"
    ) == 0
    assert (await repo.load_proposal(run["proposal_id"]))["status"] == "RETRY_PENDING"


@needs_pg
async def test_task_run_failed_carries_the_label():
    """A question resume for an item that does not exist: a semantic failure,
    labelled as such on the task event."""
    from central_command.api import orchestration

    item_id = "item_" + uuid.uuid4().hex[:12]
    await orchestration._resume_task_question(item_id, "the answer")

    assert (await _payload("task.run_failed", item_id))["transient"] is False


@needs_pg
async def test_heartbeat_error_carries_the_label(monkeypatch):
    from central_command.db import repo as repo_mod
    from central_command.heartbeat import actions as hb_actions
    from central_command.heartbeat.engine import fire

    async def _touch(schedule_id):
        return None

    monkeypatch.setattr(repo_mod, "touch_heartbeat_fired", _touch)

    async def _boom(schedule_id, params):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setitem(
        hb_actions.ACTIONS, "stub.transient",
        hb_actions.ActionSpec(
            kind="stub.transient", description="test stub", params={},
            required=(), levers=("ingest.feed.poll_once",), run=_boom,
        ),
    )
    schedule_id = "sched_" + uuid.uuid4().hex[:8]
    await fire({"id": schedule_id, "action_kind": "stub.transient",
                "action_params": {}})

    assert (await _payload("heartbeat.error", schedule_id))["transient"] is True


@needs_pg
async def test_feed_error_carries_the_label(monkeypatch):
    from central_command.ingest import feed as feed_mod

    poller = feed_mod.FeedPoller()

    async def _boom():
        poller._stopping.set()
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(feed_mod, "poll_once", _boom)
    monkeypatch.setattr(settings, "feed_poll_seconds", 0.01)
    await poller._loop()

    latest = [e for e in await repo.list_events_of_kinds(["feed.error"])][-1]
    assert latest["payload"]["transient"] is True


@needs_pg
async def test_audit_error_carries_the_label(monkeypatch):
    from central_command.gateway import auditor

    monkeypatch.setattr(settings, "auditor_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", True)

    async def _boom():
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(auditor, "ensure_registered", _boom)

    item_id = "work_" + uuid.uuid4().hex[:12]
    out = await auditor.audit_dismissal(item_id, "subject", "body", "no action")
    assert out is None  # a down auditor degrades to the human gate

    assert (await _payload("audit.error", item_id))["transient"] is True
