"""The Agents page read surface (stage 1 of the management-page split).

The page is where the operator governs the team, so its payload has to be
honest about three things the record already knows: an agent that has never
proposed has NO approval rate (not a failing one), a revoked grant is still
part of the agent's history (revocation is a timestamp, never a delete), and a
profile stays readable even when one of its composed reads fails.
"""

from __future__ import annotations

import asyncio

import pytest

from central_command.config import settings
from central_command.db import repo
from tests.conftest import needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")


@needs_pg
async def test_roster_reports_no_approval_rate_before_any_decision():
    """`approval_rate` is None — never 0.0 — for an agent with nothing decided.

    0% reads as "this agent gets rejected constantly"; the truth is "this agent
    has no record yet". The page renders the two differently, so the payload
    has to distinguish them.
    """
    from central_command.api.nerve_gateway import _agents_roster

    agents = (await _agents_roster())["agents"]
    assert agents, "the roster seeds should always produce agents"
    for a in agents:
        if a["proposals"]["decided"] == 0:
            assert a["approval_rate"] is None
        else:
            assert isinstance(a["approval_rate"], float)
            assert 0.0 <= a["approval_rate"] <= 1.0


@needs_pg
async def test_roster_counts_come_from_the_proposal_table():
    """Outcome counts are read from the record, never self-reported. The
    decided total must equal what the proposal table holds for that agent."""
    from central_command.api.nerve_gateway import _agents_roster

    agents = {a["id"]: a for a in (await _agents_roster())["agents"]}
    tallies: dict[str, int] = {}
    for row in await repo.proposal_counts_by_agent():
        tallies[row["agent_id"]] = tallies.get(row["agent_id"], 0) + row["count"]

    for agent_id, a in agents.items():
        assert a["proposals"]["total"] == tallies.get(agent_id, 0)


@needs_pg
async def test_roster_carries_active_session_counts():
    """The roster payload says which agents are WORKING, so the cockpit can
    sort them to the top. A hand-declared cockpit interface can't see a field
    the gateway never sends, so the wire shape is asserted HERE — a frontend
    test building its own payload would prove nothing.

    The count itself is checked against the repo helper on a FIXTURE agent, not
    by mutating a roster agent: the suite runs under xdist and a sibling test
    compares every roster row against its detail row, so moving a real agent
    in and out of the working bucket mid-run is a flake generator.

    Terminal is the allowlist — a DONE session must not keep an agent in the
    working bucket, and a RUNNING one must put it there.
    """
    import uuid

    from central_command.api.nerve_gateway import _agents_roster

    for a in (await _agents_roster())["agents"]:
        assert isinstance(a["active_sessions"], int), a["id"]

    agent_id = f"activetest-{uuid.uuid4().hex[:8]}"
    session_id = f"test-active-{uuid.uuid4()}"
    try:
        await repo.hire_agent(
            agent_id, "Active-session fixture", "test fixture", "You are a fixture.",
            ["graph-read"], template="general",
        )
        assert (await repo.open_session_counts_by_agent()).get(agent_id, 0) == 0

        await repo.create_running_session(session_id, agent_id)
        assert (await repo.open_session_counts_by_agent())[agent_id] == 1
        roster = {a["id"]: a for a in (await _agents_roster())["agents"]}
        assert roster[agent_id]["active_sessions"] == 1

        await repo.mark_session_done(session_id)
        assert (await repo.open_session_counts_by_agent()).get(agent_id, 0) == 0
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from session where id = $1", session_id)
            await conn.execute("delete from agent_grant where agent_id = $1", agent_id)
            await conn.execute("delete from guidance_version where agent_id = $1", agent_id)
            await conn.execute("delete from agent where id = $1", agent_id)
        finally:
            await conn.close()


@needs_pg
async def test_detail_keeps_revoked_grants_on_the_record():
    """A revoked pack stays in the payload with its timestamp and reason.

    Grant revocation is a timestamp, never a delete — the history is what lets
    the idempotent schema seeds be safe, and what tells the operator that a
    capability was deliberately taken away rather than never held.
    """
    from central_command.api.nerve_gateway import _agents_detail

    agent_id = "revoketest-x"
    try:
        await repo.hire_agent(
            agent_id, "Revocation fixture", "test fixture", "You are a fixture.",
            ["graph-read", "jira-read"], template="general",
        )
        assert await repo.revoke_pack(agent_id, "jira-read", "no longer needed")

        detail = await _agents_detail(agent_id)
        by_pack = {g["pack"]: g for g in detail["grants"]}
        assert by_pack["graph-read"]["revoked_at"] is None
        assert by_pack["jira-read"]["revoked_at"] is not None
        assert by_pack["jira-read"]["revoked_reason"] == "no longer needed"
        # The page reads active grants off the same list — a revoked pack must
        # not still count as a held capability.
        active = [g["pack"] for g in detail["grants"] if not g["revoked_at"]]
        assert active == ["graph-read"]
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from agent_grant where agent_id = $1", agent_id)
            await conn.execute("delete from guidance_version where agent_id = $1", agent_id)
            await conn.execute("delete from agent where id = $1", agent_id)
        finally:
            await conn.close()


@needs_pg
async def test_charter_history_carries_the_proposal_id():
    """A coached version links back to the proposal that produced it — the
    operator-set rule that anything reviewed renders its recorded source
    inline or one click away. `save_charter_version`'s `proposal_id` must
    reach the `agents.detail` payload's `charter.current`/`charter.versions`,
    and an operator-typed save (no proposal) stays null."""
    from central_command.api.nerve_gateway import _agents_detail

    agent_id = "propidtest-x"
    try:
        await repo.hire_agent(
            agent_id, "Proposal-id fixture", "test fixture", "You are a fixture.",
            ["graph-read"], template="general",
        )
        await repo.save_charter_version(
            agent_id, "CHARTER v2, coached.", "from coaching",
            created_by="agent:coach for propidtest-x (approved human:lee)",
            proposal_id="prop_fixture_1",
        )

        detail = await _agents_detail(agent_id)
        assert detail["charter"]["current"]["proposal_id"] == "prop_fixture_1"
        by_version = {v["version"]: v for v in detail["charter"]["versions"]}
        assert by_version[2]["proposal_id"] == "prop_fixture_1"
        assert by_version[1]["proposal_id"] is None
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from agent_grant where agent_id = $1", agent_id)
            await conn.execute("delete from guidance_version where agent_id = $1", agent_id)
            await conn.execute("delete from agent where id = $1", agent_id)
        finally:
            await conn.close()


@needs_pg
async def test_detail_agent_row_is_the_same_row_the_roster_shows():
    """The detail header and record panel read the SAME enriched row the list
    shows — same fields, same values.

    This is a regression guard: `_agents_detail` originally pulled its row from
    `rest.list_agents()`, which returns the plain roster without the outcome
    tally, so the page crashed on `proposals.total` the first time an operator
    opened it. Two builders for one row is the bug; asserting field parity is
    what stops it coming back.
    """
    from central_command.api.nerve_gateway import _agents_detail, _agents_roster

    roster = {a["id"]: a for a in (await _agents_roster())["agents"]}
    assert roster, "the roster seeds should always produce agents"

    for agent_id, listed in roster.items():
        detail_row = (await _agents_detail(agent_id))["agent"]
        assert detail_row == listed, f"{agent_id}: detail row differs from the roster row"
        # Named explicitly so a future field the page depends on can't quietly
        # go missing from one side.
        assert "proposals" in detail_row and "total" in detail_row["proposals"]
        assert "approval_rate" in detail_row


@needs_pg
async def test_signal_wire_shape_carries_author_to_the_picker():
    """The cockpit hand-declares its own interface over this payload, so a field
    the gateway never sends is invisible to `tsc` and to every frontend test —
    the picker would just silently never label a task outcome. Asserted on the
    BACKEND wire shape for that reason.
    """
    import uuid

    from central_command import events
    from central_command.api.nerve_gateway import _agents_detail

    agent_id = "signalwire-" + uuid.uuid4().hex[:6]
    task_id = "task_wire_" + uuid.uuid4().hex[:8]
    outcome = "Done, but I never learned which project key to use."
    try:
        await repo.hire_agent(
            agent_id, "Signal fixture", "test fixture", "You are a fixture.",
            ["graph-read"], template="general",
        )
        await repo.create_task(task_id, "A wire-shape task", "do it", agent_id)
        await repo.resolve_task(task_id, "DONE", outcome=outcome)
        await events.emit("task.completed", ref_id=task_id,
                          payload={"outcome": outcome}, actor="system")

        signals = (await _agents_detail(agent_id))["signals"]
        assert len(signals) == 1, signals  # own rows only — a throwaway agent id
        s = signals[0]
        assert s["kind"] == "task-outcome"
        assert s["author"] == "agent"
        assert s["feedback"] == outcome
        assert isinstance(s["event_id"], int)
        assert task_id in s["intent"]
    finally:
        conn = await repo._conn()
        try:
            await conn.execute("delete from task where id = $1", task_id)
        finally:
            await conn.close()
        await _drop_agent(agent_id)


@needs_pg
async def test_detail_refuses_an_unknown_agent():
    """404, not an empty profile — a blank page for a typo'd id would read as
    "this agent has no charter and no capabilities"."""
    from fastapi import HTTPException

    from central_command.api.nerve_gateway import _agents_detail

    with pytest.raises(HTTPException) as exc:
        await _agents_detail("no-such-agent-at-all")
    assert exc.value.status_code == 404


async def _drop_agent(agent_id: str) -> None:
    conn = await repo._conn()
    try:
        await conn.execute("delete from agent_grant where agent_id = $1", agent_id)
        await conn.execute("delete from guidance_version where agent_id = $1", agent_id)
        await conn.execute("delete from agent where id = $1", agent_id)
    finally:
        await conn.close()


@needs_pg
async def test_management_actions_route_through_their_rest_handlers():
    """Stage 2's write methods are wiring, so what needs guarding is that the
    wiring reaches the handler that holds each invariant — not the behaviour
    the handlers already own.

    Driven through `_dispatch`, because a method mapped to the wrong handler or
    dropping a param typechecks perfectly and fails only in the operator's
    hands.
    """
    from fastapi import HTTPException

    from central_command.api.nerve_gateway import _dispatch

    agent_id = "stage2test-x"
    await _drop_agent(agent_id)
    try:
        hired = await _dispatch("agents.hire", {
            "name": "Stage 2 fixture", "mission": "verify the wiring",
            "template": "analyst",
        }, None)
        # The hire path derives the id from the name; work with what it made.
        agent_id = hired["agent"]["id"]

        # Grant reaches grant_pack.
        out = await _dispatch("agents.grant", {"agentId": agent_id, "pack": "jira-propose"}, None)
        assert "jira-propose" in [g["pack"] for g in out["grants"] if not g["revoked_at"]]

        # Revoke reaches revoke_pack — and the row keeps its history.
        out = await _dispatch("agents.revoke", {
            "agentId": agent_id, "pack": "jira-propose", "reason": "wiring test",
        }, None)
        revoked = [g for g in out["grants"] if g["pack"] == "jira-propose"]
        assert len(revoked) == 1 and revoked[0]["revoked_at"] is not None
        assert revoked[0]["revoked_reason"] == "wiring test"

        # Charter save reaches save_charter: a NEW version, history intact.
        before = await repo.current_charter(agent_id)
        saved = await _dispatch("agents.charter.save", {
            "agentId": agent_id, "content": "You are a fixture. Do fixture things.",
            "rationale": "wiring test",
        }, None)
        assert saved["version"] == before["version"] + 1
        assert saved["warnings"] == []  # clean charter, nothing to read

        # The charter-v2 guard actually fires, and rides the RPC response —
        # this is what keeps the editor open instead of closing on save, so a
        # dead branch here would mean the operator never sees it.
        flagged = await _dispatch("agents.charter.save", {
            "agentId": agent_id,
            "content": "You may use capability = 'jira.obliterate_everything'.",
            "rationale": "wiring test: policy warning",
        }, None)
        assert flagged["warnings"], "a charter naming an unregistered capability must warn"

        # Revert reaches revert_charter — forward as a new version, never a
        # rollback. This is the one an operator is most likely to assume
        # destroys history.
        latest = (await repo.current_charter(agent_id))["version"]
        reverted = await _dispatch("agents.charter.revert", {
            "agentId": agent_id, "version": before["version"],
        }, None)
        assert reverted["version"] == latest + 1, "a revert moves FORWARD"
        assert reverted["restored"] == before["version"]
        # Nothing was rewritten: every version ever saved is still there.
        versions = await repo.list_charter_versions(agent_id)
        assert len(versions) == latest + 1
        assert (await repo.get_charter_version(agent_id, reverted["version"]))["content"] \
            == (await repo.get_charter_version(agent_id, before["version"]))["content"]

        # Retire reaches retire_agent, reason invariant included.
        with pytest.raises(HTTPException) as exc:
            await _dispatch("agents.retire", {"agentId": agent_id, "reason": "  "}, None)
        assert exc.value.status_code == 422

        await _dispatch("agents.retire", {"agentId": agent_id, "reason": "wiring test done"}, None)
        assert (await repo.get_agent(agent_id))["status"] == "RETIRED"

        # Reactivate reaches reactivate_agent.
        await _dispatch("agents.reactivate", {"agentId": agent_id}, None)
        assert (await repo.get_agent(agent_id))["status"] == "ACTIVE"
    finally:
        await _drop_agent(agent_id)


@needs_pg
async def test_coach_rpc_refuses_a_session_with_nothing_to_learn_from():
    """"Nothing feeds a session unchosen" is the D5 rule, and the page's Coach
    button is a new way to reach it — so the refusal has to hold through the
    RPC, not only through the REST route."""
    from fastapi import HTTPException

    from central_command.api.nerve_gateway import _dispatch

    with pytest.raises(HTTPException) as exc:
        await _dispatch("agents.coach", {
            "agentId": "inbox-triage", "signalIds": [], "note": "   ",
        }, None)
    assert exc.value.status_code == 422


@needs_pg
async def test_coaching_a_retired_agent_says_retired_not_unknown():
    """The page renders a retired agent's full profile, so answering "unknown
    agent" about one the operator is looking at is simply false. 409 + the
    actual reason, and 404 kept for an id that really does not exist."""
    from fastapi import HTTPException

    from central_command.api.nerve_gateway import _dispatch

    agent_id = "retiredcoach-x"
    await _drop_agent(agent_id)
    try:
        await repo.hire_agent(
            agent_id, "Retired fixture", "test fixture", "You are a fixture.",
            ["graph-read"], template="analyst",
        )
        assert await repo.retire_agent(agent_id, "test over")

        with pytest.raises(HTTPException) as exc:
            await _dispatch("agents.coach", {"agentId": agent_id, "note": "hi"}, None)
        assert exc.value.status_code == 409
        assert "RETIRED" in exc.value.detail

        with pytest.raises(HTTPException) as exc:
            await _dispatch("agents.coach", {"agentId": "no-such-agent", "note": "hi"}, None)
        assert exc.value.status_code == 404
    finally:
        await _drop_agent(agent_id)


async def _await_detached(seen: dict, timeout: float = 5.0, *, key: str | None = None) -> None:
    """Wait for a detached coach run to have started and finished.

    Polling on mere truthiness of `seen` was a race (2026-08-21 W7): the stub
    sets keys one at a time, awaiting a real DB call (`repo.latest_event_id()`)
    between the first key and the last — `seen` goes truthy the moment the
    FIRST key lands, before the awaited call resolves, and the fixed 0.02s
    grace sleep afterward is not guaranteed to outlast that call. Callers that
    care about a specific key (e.g. `event_at_call`, set only after the await)
    must pass it so the poll waits for THAT key, not just any key."""
    for _ in range(int(timeout / 0.02)):
        if key is not None:
            if key in seen:
                return
        elif seen:
            await asyncio.sleep(0.02)  # let the landing finish too
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the detached coach run never started")


@needs_pg
async def test_coach_rpc_logs_the_request_before_the_run(monkeypatch):
    """The operator's note is written to the log BEFORE the agent runs, and the
    run is told that event id.

    This is what makes a coached charter's citation checkable: the agent quotes
    the note, and `claim_supported()` re-checks the quote against the recorded
    event. If the request were logged after the run, the agent would be citing
    something that did not exist when it wrote the citation — and the coach
    path already shipped once with its quote check silently not running.

    `run_coach` is stubbed: this asserts the wiring and the ordering, not the
    model's behaviour, and it must never spend tokens.
    """
    from central_command.api.nerve_gateway import _dispatch
    from central_command.runtime import run as run_module

    seen: dict = {}

    async def fake_run_coach(agent_id, **kwargs):
        seen["agent_id"] = agent_id
        seen["kwargs"] = kwargs
        # The authorising event must already be on the log when the run starts.
        seen["event_at_call"] = await repo.latest_event_id()
        return {"deferred": False, "output": "stub", "signals": 0}

    monkeypatch.setattr(run_module, "run_coach", fake_run_coach)

    out = await _dispatch("agents.coach", {
        "agentId": "inbox-triage", "signalIds": [1, 2], "note": "be more careful",
    }, None)

    # The RPC returns as soon as the run is LAUNCHED — the run itself is
    # detached behind the task record, so wait for it before reading `seen`.
    assert out["task_id"]
    await _await_detached(seen, key="event_at_call")

    assert seen["agent_id"] == "inbox-triage"
    assert seen["kwargs"]["signal_ids"] == [1, 2]
    assert seen["kwargs"]["note"] == "be more careful"

    note_event_id = seen["kwargs"]["note_event_id"]
    assert note_event_id is not None
    # Written before the run, and it is the event the run was handed.
    assert seen["event_at_call"] >= note_event_id
    row = await repo.get_event(note_event_id)
    assert row["kind"] == "agent.coach_requested"
    assert row["actor"] == "operator"
    assert row["payload"]["note"] == "be more careful"


@needs_pg
async def test_a_coach_run_rides_a_task_record_and_lands_on_it(monkeypatch):
    """The coaching dialog starts a run and gets out of the way.

    A coach run takes minutes (109s measured 2026-08-01), so holding the RPC
    open made the cockpit report "Timeout" at 30s on runs that had in fact
    succeeded and parked a proposal. The run rides a task record instead: the
    RPC returns once it is IN_PROGRESS, and the OUTCOME lands on the record —
    REVIEW behind a drafted proposal, FAILED with the error when it throws.

    Both landings are asserted here because a detached run has nobody holding a
    request to see it fail: if the record does not carry the outcome, nothing
    does.
    """
    from central_command.api import routes as rest
    from central_command.runtime import run as run_module

    seen: dict = {}

    async def drafts(agent_id, **kwargs):
        seen["task_id"] = kwargs["task_id"]
        # A real run links the session to the task inside `_run_live`.
        await repo.start_task(kwargs["task_id"], "sess_coachtest")
        return {"deferred": True, "session_id": "sess_coachtest",
                "proposal_id": "prop_coachtest", "intent": "charter.update",
                "target": agent_id, "signals": 1}

    monkeypatch.setattr(run_module, "run_coach", drafts)
    out = await rest.coach_agent("inbox-triage", rest.CoachIn(note="be careful"))

    # Launched, not run: no proposal in the reply, a task to watch instead.
    assert out["background"] is True
    assert out["deferred"] is None
    task_id = out["task_id"]
    assert (await repo.get_task(task_id))["status"] == "IN_PROGRESS"
    # The task belongs to the DRAFTER, never the coaching target.
    assert (await repo.get_task(task_id))["agent_id"] == "coach"

    await _await_detached(seen)
    task = await repo.get_task(task_id)
    assert task["status"] == "REVIEW", "a drafted edit holds the task for the operator"
    # Linked by session, which is what lets the gateway resolve the task when
    # the operator decides the proposal (`_resolve_task_after`).
    assert task["session_id"] == "sess_coachtest"

    async def explodes(agent_id, **kwargs):
        seen["failed"] = True
        raise RuntimeError("the model is down")

    monkeypatch.setattr(run_module, "run_coach", explodes)
    seen.clear()
    out = await rest.coach_agent("inbox-triage", rest.CoachIn(note="again"))
    await _await_detached(seen)
    failed = await repo.get_task(out["task_id"])
    assert failed["status"] == "FAILED"
    assert "the model is down" in failed["outcome"]


@needs_pg
async def test_the_coach_is_not_directly_taskable(monkeypatch):
    """The Coach dialog is the only way to run the coach.

    `coach` was `taskable`, so `POST /tasks {agent_id: "coach"}` ran it through
    `run_task` — with no curated signals and no citation re-check, i.e. the
    governance that makes a coached charter edit trustworthy simply absent. The
    coach still lands on the board as a task; it is just never tasked by hand.
    """
    from fastapi import HTTPException

    from central_command.api import routes as rest
    from central_command.runtime.roster import taskable_agent_ids

    assert "coach" not in await taskable_agent_ids()
    with pytest.raises(HTTPException) as exc:
        await rest.create_and_run_task(
            "draft yourself a better charter", None, "coach",
        )
    assert exc.value.status_code == 422


@needs_pg
async def test_signal_failure_does_not_blank_the_profile(monkeypatch):
    """Coaching signals are one composed read among several. If gathering them
    fails, the charter and grants the operator came to read are still true and
    still shown — a degraded section, never a blank profile."""
    from central_command.api import routes as rest
    from central_command.api.nerve_gateway import _agents_detail

    async def boom(agent_id: str):
        raise RuntimeError("signal gathering is down")

    monkeypatch.setattr(rest, "coaching_signals", boom)

    detail = await _agents_detail("inbox-triage")
    assert detail["signals"] == []
    assert detail["charter"]["current"]["content"]
    assert detail["agent"]["id"] == "inbox-triage"
