"""Slice 6 of outage equivalence — the last one: a read that calls OUT of the box
gets exactly one bounded retry on a TRANSIENT failure.

A live turn is not rewindable (the doctrine boundary), so all a read can do is
try briefly and then degrade honestly. This slice makes "briefly" real, and
bounds it hard: one retry, half a second. The unit-level machinery of slices 2-5
is what waits an outage OUT; a turn the operator may be watching never does.

The three properties, each pinned with a SPY on the underlying integration so
the attempt COUNT is asserted rather than inferred:

- transient then success → 2 calls, the result, no degradation text;
- transient twice        → 2 calls, today's honest degradation plus "after 2 attempts";
- semantic               → 1 call, today's text unchanged (the dependency ANSWERED;
                           re-sending gets the same "no").

The wait is patched throughout — a bounded retry must cost the suite no wall
clock — via the `tools._sleep` seam, which exists for exactly that (patching
`asyncio.sleep` itself would break the event loop under the test).
"""

from __future__ import annotations

import httpx
import pytest

from central_command.integrations import graphiti, jira
from central_command.integrations import litellm as litellm_client
from central_command.runtime import tools


def _transient() -> Exception:
    return httpx.ConnectError("[Errno 111] Connection refused")


def _semantic() -> Exception:
    return ValueError("Unbounded JQL queries are not allowed here")


@pytest.fixture
def no_wait(monkeypatch):
    """Patch the retry's sleep; hand the test back what it was asked to wait."""
    waits: list[float] = []

    async def _fake(delay):
        waits.append(delay)

    monkeypatch.setattr(tools, "_sleep", _fake)
    return waits


class _Spy:
    """Counts calls and answers from a scripted sequence (exception = raise)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def __call__(self, *a, **kw):
        self.calls += 1
        out = self.outcomes[min(self.calls, len(self.outcomes)) - 1]
        if isinstance(out, Exception):
            raise out
        return out


async def test_transient_then_success_retries_once_and_returns_the_result(
    monkeypatch, no_wait
):
    spy = _Spy(_transient(), {"key": "TASKS-1", "status": "To Do"})
    monkeypatch.setattr(jira, "get_issue", spy)

    out = await tools.jira_get_issue(None, "TASKS-1")

    assert spy.calls == 2
    assert "TASKS-1" in out and "failed" not in out
    assert no_wait == [0.5]


async def test_transient_twice_degrades_honestly_after_exactly_two_attempts(
    monkeypatch, no_wait
):
    spy = _Spy(_transient(), _transient())
    monkeypatch.setattr(jira, "get_issue", spy)

    out = await tools.jira_get_issue(None, "TASKS-1")

    assert spy.calls == 2
    # Today's honest degradation, unchanged, plus the count.
    assert out.startswith("jira read failed (ConnectError) after 2 attempts.")
    assert "Connection refused" in out
    assert "proceed without it and say so" in out
    assert no_wait == [0.5]


async def test_a_semantic_failure_is_not_retried_at_all(monkeypatch, no_wait):
    """The dependency ANSWERED — 400 "unbounded JQL" is coachable, not an outage.
    Retrying it would only spend a second saying the same thing twice."""
    spy = _Spy(_semantic(), _semantic())
    monkeypatch.setattr(jira, "search_issues", spy)

    out = await tools.jira_search_issues(None, "order by updated asc")

    assert spy.calls == 1
    assert no_wait == []
    assert out.startswith("jira search failed (ValueError). The provider said:")
    assert "after" not in out.split("The provider said:")[0]
    assert "Unbounded JQL" in out


async def test_the_graph_and_litellm_reads_carry_the_same_bound(monkeypatch, no_wait):
    """The wrapper rides every read that calls out of the box, not just Jira —
    each keeping its OWN degradation wording (an agent reads these)."""
    graph = _Spy(_transient(), _transient())
    monkeypatch.setattr(graphiti, "search_facts", graph)
    models = _Spy(_transient(), [{"model_name": "cc-default"}])
    monkeypatch.setattr(litellm_client, "list_models", models)

    graph_out = await tools.search_knowledge_graph(None, "renewal")
    models_out = await tools.litellm_list_models(None)

    assert graph.calls == 2
    assert graph_out == (
        "knowledge graph unavailable (ConnectError) after 2 attempts; "
        "proceed without it"
    )
    assert models.calls == 2
    assert "cc-default" in models_out and "unavailable" not in models_out
    assert no_wait == [0.5, 0.5]
