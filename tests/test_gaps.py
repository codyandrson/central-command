"""Gap declaration.

Before this, the entire mechanism was `"CAPABILITY GAP" in outcome.upper()` at
orchestration.py:416 — orchestrator-only, unstructured, and unavailable to an
agent on an ordinary task, while the orchestrator profile instructed agents to
flag reference material "older than the system it describes".
"""

from __future__ import annotations

import uuid

import pytest

from central_command.db import repo
from central_command.runtime import durable as resume_module
from central_command.runtime.tools import declare_gap
from tests.conftest import needs_pg

pytestmark_db = needs_pg


class _Ctx:
    """Minimal RunContext stand-in — declare_gap only reads deps."""

    def __init__(self, session_id="sess_test", agent_id="inbox-triage"):
        self.deps = type("D", (), {"session_id": session_id, "agent_id": agent_id})()


@pytestmark_db
@pytest.mark.asyncio
async def test_missing_knowledge_is_recorded():
    out = await declare_gap(
        _Ctx(), kind="missing_knowledge", subject="litellm",
        need="the LiteLLM proxy API reference for key management",
    )
    assert "recorded" in out.lower()
    events = await repo.list_events_of_kinds(["gap.declared"])
    assert events[-1]["payload"]["kind"] == "missing_knowledge"
    assert events[-1]["payload"]["subject"] == "litellm"


@pytestmark_db
@pytest.mark.asyncio
async def test_stale_knowledge_without_evidence_is_refused():
    """An agent must not be able to file 'this looks outdated'. The evidence is
    required by the tool, not requested by guidance that nothing enforces."""
    out = await declare_gap(
        _Ctx(), kind="stale_knowledge", subject="litellm",
        need="refresh the key-management docs",
    )
    assert "requires" in out.lower()
    assert "doc_id" in out

    events = await repo.list_events_of_kinds(["gap.declared"])
    assert not any(
        e["payload"].get("kind") == "stale_knowledge"
        and e["payload"].get("subject") == "litellm"
        and e["payload"].get("observed") is None
        for e in events
    ), "a refused declaration must not be recorded as a gap"


@pytestmark_db
@pytest.mark.asyncio
async def test_stale_knowledge_with_evidence_is_recorded():
    await repo.create_skill("cc-test-lite", "LiteLLM", "Proxy operations")
    doc = await repo.add_skill_doc(
        "cc-test-lite", "keys", "reference", "Key management",
        "POST /key/generate accepts budget_duration.",
    )
    out = await declare_gap(
        _Ctx(), kind="stale_knowledge", subject="litellm",
        need="refresh from the current LiteLLM release",
        doc_id=doc["id"],
        doc_says="POST /key/generate accepts budget_duration",
        observed="the API returned 400 unknown field 'budget_duration'",
    )
    assert "recorded" in out.lower()
    payload = (await repo.list_events_of_kinds(["gap.declared"]))[-1]["payload"]
    assert payload["doc_id"] == doc["id"]
    assert "400" in payload["observed"]

    conn = await repo._conn()
    try:
        await conn.execute("delete from skill where id like 'cc-test-%'")
    finally:
        await conn.close()


@pytestmark_db
@pytest.mark.asyncio
async def test_stale_knowledge_may_cite_an_ingested_document():
    """The steward finds contradictions against INGESTED documents, not skill
    docs — so the work-item id `POST /api/documents` mints (`wi_…`) has to be a
    citable source. Accepting skill docs only is why a contradiction could not
    name the very document that contradicts the graph."""
    from central_command.ingest import ledger

    enrolled = await ledger.enroll_document(
        f"gvtest gap doc {uuid.uuid4().hex[:8]}",
        "The proxy listens on port 4000.",
        feed="backlog", source="test",
    )
    item_id = enrolled["item_id"]
    assert item_id.startswith("wi_")
    try:
        out = await declare_gap(
            _Ctx(), kind="stale_knowledge", subject="litellm",
            need="re-ingest the current runbook",
            doc_id=item_id,
            doc_says="the proxy listens on port 4000",
            observed="the proxy answers on 4001",
        )
        assert "recorded" in out.lower()
        payload = (await repo.list_events_of_kinds(["gap.declared"]))[-1]["payload"]
        assert payload["doc_id"] == item_id
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from work_item where id = $1", item_id)
        finally:
            await conn.close()


@pytestmark_db
@pytest.mark.asyncio
async def test_stale_knowledge_citing_an_unknown_doc_is_refused():
    """Citing a document that does not exist is the misquote case — the same
    discipline claim_supported() applies to operator claims."""
    out = await declare_gap(
        _Ctx(), kind="stale_knowledge", subject="litellm",
        need="refresh", doc_id="doc_doesnotexist",
        doc_says="something", observed="something else",
    )
    assert "unknown document" in out.lower()
    # The refusal names BOTH citable sources — an agent told only half the rule
    # retries with the same half.
    assert "skill" in out.lower() and "wi_" in out


@pytestmark_db
@pytest.mark.asyncio
async def test_unknown_kind_is_refused_with_the_valid_list():
    out = await declare_gap(_Ctx(), kind="vibes", subject="x", need="y")
    assert "missing_knowledge" in out
    assert "stale_knowledge" in out


@pytestmark_db
@pytest.mark.asyncio
async def test_the_declaring_agent_is_recorded():
    """An anonymous gap cannot be routed — the operator needs to know who is
    blocked, not just that somebody is."""
    await declare_gap(
        _Ctx(session_id="sess_who", agent_id="jira-expert"),
        kind="missing_tool", subject="confluence", need="a publish capability",
    )
    payload = (await repo.list_events_of_kinds(["gap.declared"]))[-1]["payload"]
    assert payload["agent_id"] == "jira-expert"


class _StubAgent:
    """Records the `deps` it was actually run with — stands in for the real
    pydantic-ai Agent so `durable.resume()` can be exercised without a live
    model or a full gateway round-trip."""

    def __init__(self):
        self.received_deps = None

    # `usage_limits` since 2026-08-01: the resume leg carries the same loop
    # breaker every fresh run gets, and a stub that refuses it is a stub the
    # real call would fail against.
    async def run(self, *, message_history, deferred_tool_results, model, deps,
                  usage_limits=None):
        self.received_deps = deps
        return "stub result"


@pytest.mark.asyncio
async def test_resume_threads_the_agent_id_into_fresh_deps():
    """Regression for the review finding: `gateway.gateway` (approve AND
    execution-failure paths) and `runtime.questions.resume_with_answer` all
    resume a paused run through `durable.resume()`. Before this fix, `resume()`
    rebuilt deps as bare `TriageDeps()` — every `declare_gap` call an agent
    made on a RESUMED turn (the most common turn shape: every approval and
    every execution failure resumes one) recorded `agent_id: None`, the exact
    anonymous, unroutable gap Step 4 exists to prevent. A hand-made `_Ctx`
    never touches `resume()`, so the six original tests passed against the
    broken version — this test exercises `resume()` itself."""
    from central_command.runtime.durable import resume

    stub = _StubAgent()
    await resume(
        stub, messages=[], tool_call_id="call_1", executor_result="ok",
        agent_id="jira-expert",
    )
    assert stub.received_deps.agent_id == "jira-expert"


def test_the_substring_match_is_gone():
    """A source walk, not a behaviour test: the old mechanism matched the literal
    string "CAPABILITY GAP" in an outcome field, which is why an agent on an
    ordinary task could not declare one. Re-introducing it anywhere fails here,
    in the same commit that adds it.

    Verified to FAIL against HEAD before this task, where the string is present
    at api/orchestration.py:419, runtime/orchestrator_profile.py:49, and
    runtime/run.py:408.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "central_command"
    offenders = [
        p for p in root.rglob("*.py")
        if "CAPABILITY GAP" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "gap detection must read gap.declared events, not match prose: "
        + ", ".join(str(p) for p in offenders)
    )


@pytestmark_db
@pytest.mark.asyncio
async def test_gap_queue_lists_newest_first():
    from central_command.api import routes

    await declare_gap(
        _Ctx(session_id="sess_q1"), kind="missing_tool",
        subject="confluence", need="a publish capability",
    )
    out = await routes.list_gaps_route()
    assert out["gaps"][0]["subject"] == "confluence"
    assert out["gaps"][0]["kind"] == "missing_tool"


@pytestmark_db
@pytest.mark.asyncio
async def test_gap_queue_clamps_a_negative_limit():
    """A negative limit used to reach SQL raw and 500 — clamped to at least 1."""
    from central_command.api import routes

    out = await routes.list_gaps_route(limit=-5)
    assert len(out["gaps"]) <= 1


# --- final-review I2: a gap must be traceable to a transcript ----------------


class _GapDeclaringAgent:
    """Declares a gap through the REAL `declare_gap`, using whatever deps
    `durable.resume()` handed it — which is the only way to observe what a
    resumed turn actually records."""

    # `usage_limits` since 2026-08-01: the resume leg carries the same loop
    # breaker every fresh run gets, and a stub that refuses it is a stub the
    # real call would fail against.
    async def run(self, *, message_history, deferred_tool_results, model, deps,
                  usage_limits=None):
        ctx = type("C", (), {"deps": deps})()
        await declare_gap(
            ctx, kind="missing_knowledge", subject="confluence",
            need="a Confluence Cloud REST reference",
        )
        return "done"


@pytest.mark.asyncio
async def test_resume_threads_the_session_id_into_fresh_deps():
    """Task 8 threaded `agent_id` into every `TriageDeps`; `session_id` never
    got the same treatment, so `declare_gap` recorded `ref_id=None` on the
    resumed turn — the operator saw a gap they could not trace to a transcript."""
    from central_command.runtime.durable import resume

    stub = _StubAgent()
    await resume(
        stub, messages=[], tool_call_id="call_1", executor_result="ok",
        agent_id="jira-expert", session_id="sess_resumed",
    )
    assert stub.received_deps.session_id == "sess_resumed"


@pytestmark_db
@pytest.mark.asyncio
async def test_a_gap_declared_on_a_resumed_turn_is_escalatable():
    """The serious half of the finding. `api/orchestration.py`'s `_collect`
    escalates a child's gaps with `repo.gaps_for_session(t["session_id"])`. A
    child that pauses on a proposal, gets approved, and declares its gap on the
    RESUMED turn wrote `ref_id=None` — `gaps_for_session` returned `[]` and the
    orchestrator escalated NOTHING. Every approval and every execution failure
    resumes a run, so this is the common turn shape, not an edge case.

    This goes through `durable.resume()` and the real `declare_gap`, then reads
    back through the exact query the orchestrator uses.
    """
    import uuid

    session_id = "sess_gaptest_" + uuid.uuid4().hex[:8]
    await resume_module.resume(
        _GapDeclaringAgent(), messages=[], tool_call_id="call_1",
        executor_result="ok", agent_id="jira-expert", session_id=session_id,
    )

    escalatable = await repo.gaps_for_session(session_id)
    assert len(escalatable) == 1, "the orchestrator would have escalated nothing"
    assert escalatable[0]["subject"] == "confluence"
    assert escalatable[0]["agent_id"] == "jira-expert"
