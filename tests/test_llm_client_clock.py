"""DL-204 — the app holds no clock on a model completion shorter than LiteLLM's,
and never re-sends on its own.

Every client that waits on a completion — the agents' provider, the graph
client, the claim-time extractor — takes its timeout from
`integrations.http.llm_timeout()` (connect only) and, where it is the SDK,
`max_retries=0`. The 2026-11-02 queue storm: the SDK's default 600 s + 2
retries re-sent ten 26-37k-token agent turns behind their still-running
originals on the one FIFO slot, and a 180 s extractor cycled four copies of
one mail; every one of those calls had SUCCEEDED upstream. LiteLLM declares
the per-model timeout and owns the retry; the clients defer to it.

Also here: the orphan test for a resume is "armed before this process
started", not an age — a resume dies only with its process, and a queued one
must never be re-driven as dead (it was, twice, the same night).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from central_command.config import settings
from central_command.integrations import http as http_client
from tests.conftest import needs_pg


def _assert_waits_forever(timeout) -> None:
    # The SDK normalises an httpx.Timeout into its own; read it back by field.
    t = timeout.as_dict() if hasattr(timeout, "as_dict") else {
        "connect": timeout.connect, "read": timeout.read,
        "write": timeout.write, "pool": timeout.pool}
    assert t["read"] is None, f"a read timeout caps queue time: {t}"
    assert t["connect"] == http_client.LLM_CONNECT_TIMEOUT_SECONDS


def test_the_one_clock_is_connect_only():
    _assert_waits_forever(http_client.llm_timeout())


def test_the_app_openai_client_has_no_read_timeout_and_no_sdk_retries():
    client = http_client.openai_client("sk-test", "http://localhost:4000/v1")
    assert client.max_retries == 0
    _assert_waits_forever(client.timeout)
    assert "localhost:4000" in str(client.base_url)
    assert client.api_key == "sk-test"


@needs_pg
async def test_agents_run_on_the_app_client(monkeypatch):
    from central_command.runtime.models import resolve_model

    monkeypatch.setattr(settings, "test_model", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:4000")
    monkeypatch.setattr(settings, "llm_api_key", "sk-cc-virtual")
    model = await resolve_model()
    client = model._provider.client
    assert client.max_retries == 0
    _assert_waits_forever(client.timeout)


def test_the_graph_client_is_the_app_client():
    from central_command.integrations import graphiti_client

    client = graphiti_client._openai_client("sk-test", "http://localhost:4000/v1")
    assert client.max_retries == 0
    _assert_waits_forever(client.timeout)


async def test_the_extractor_takes_the_one_clock(monkeypatch):
    from central_command.ingest import entities

    seen: dict = {}

    class _Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            raise httpx.ConnectError("not today")

    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:4000")
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(entities.httpx, "AsyncClient", _Client)
    with pytest.raises(httpx.ConnectError):
        await entities._complete("cc-default", "hello")
    _assert_waits_forever(seen["timeout"])


def test_no_model_client_holds_its_own_read_timeout():
    """Source walk: the three completion clients name the one clock, and no
    `timeout=<number>` sits on a model-completion client."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "central_command"
    for rel in ("runtime/models.py", "integrations/graphiti_client.py", "ingest/entities.py"):
        src = (root / rel).read_text()
        assert "http_client.openai_client(" in src or "http_client.llm_timeout()" in src, rel
    src = (root / "ingest/entities.py").read_text()
    assert not re.search(r"AsyncClient\(timeout=\d", src), "the extractor grew a private clock back"


@needs_pg
async def test_a_queued_resume_is_not_an_orphan_and_a_dead_one_is(monkeypatch):
    """The marker armed by THIS process is in flight whatever its age; the
    same marker is an orphan only for a process started after it."""
    import asyncio

    from central_command.db import repo
    from central_command.gateway import gateway
    from central_command.runtime.run import ingest_and_propose
    from central_command.runtime.spike_model import make_spike_model

    model = make_spike_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)

    async def resume_killed(*a, **k):
        raise asyncio.CancelledError()

    monkeypatch.setattr(gateway, "resume", resume_killed)
    with pytest.raises(asyncio.CancelledError):
        await gateway.approve_and_execute(run["session_id"], run["proposal_id"], model=model)
    sid = run["session_id"]
    assert await repo.session_status(sid) == "AWAITING_HUMAN"

    now = datetime.now(UTC)
    in_flight = [r["id"] for r in await repo.sessions_with_orphaned_resume(now - timedelta(hours=1))]
    assert sid not in in_flight, "armed after the process started: queued, not dead"
    dead = [r["id"] for r in await repo.sessions_with_orphaned_resume(now + timedelta(days=1))]
    assert sid in dead, "armed before a later process started: dead"
