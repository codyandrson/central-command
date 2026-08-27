"""The operator can stop a live run, resume it, or cancel the work for good.

Three levers, one rule between them: **nothing is ever destroyed and nothing
ever lies about what happened.**

* STOP is cooperative and lands on a NODE BOUNDARY — `_run_live` reads the flag
  on the snapshot's own UPDATE and raises `RunStopped`, which parks through the
  same except-path the window park uses. That path's tail re-snapshot is the
  load-bearing part, exactly as it is in `test_continue_window.py`: pydantic-ai
  appends the unsent `ModelRequest` carrying the last batch's tool returns, and
  without persisting it a resume would re-execute the whole batch.
* RESUME mirrors `continue_session` — SQL claim, re-entry on the persisted
  history, land through the marker's `after` tail.
* CANCEL-FOR-GOOD is the wind-down: the OPEN item is WITHDRAWN, the undecided
  proposal is WITHDRAWN and its folded siblings released (a fold is a claim of
  coverage, never an outcome), the session is CLOSED through the one close seam,
  and only then does the task go terminal. An APPROVED proposal is untouched —
  an approval the operator already gave executes.

And STOPPED is never auto-driven. No sweep looks for it; stopped stays stopped
until a human says otherwise, which is the entire point of the button.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from central_command.api import nerve_gateway, orchestration, routes
from central_command.config import settings
from central_command.db import repo
from central_command.runtime import converse
from central_command.runtime.run import RunStopped, run_task
from tests.conftest import aresolve, needs_pg

pytestmark = needs_pg


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    # demo_mode: these paths resolve models themselves; a real key in .env would
    # otherwise make the "offline" suite call Claude for real.
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "dry_run")
    monkeypatch.setattr(settings, "dispatch_approval_limit", 10_000)
    monkeypatch.setattr(settings, "auditor_enabled", False)
    monkeypatch.setattr(settings, "run_request_limit", 25)


def _looping_model(stop_after: int | None = None) -> FunctionModel:
    """The `test_continue_window` looper: keeps calling one ungated tool, and
    after `stop_after` model calls answers in text instead."""
    calls = {"n": 0}

    def _respond(messages, info) -> ModelResponse:
        calls["n"] += 1
        if stop_after is not None and calls["n"] > stop_after:
            return ModelResponse(parts=[TextPart(content="done after the resume")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="declare_gap",
            args={"kind": "missing_tool", "subject": "looping",
                  "need": "a tool I do not have"},
        )])

    return FunctionModel(_respond)


def _press_stop_mid_run(monkeypatch, after: int = 3) -> None:
    """Set the real flag, through the real SQL, from outside the run — after
    `after` snapshots.

    A stop is an out-of-band event, and the session id is minted inside the run
    path, so the test needs a hook that fires while the loop is live. Wrapping
    the snapshot write is the smallest one that keeps everything under test
    real: the flag is set by `repo.request_session_stop` and READ by the very
    next snapshot's own `returning`, which is the mechanism this feature is.
    Wall-clock polling was the first attempt and is a flake generator — a
    FunctionModel run can spend its whole window inside one scheduler tick.
    """
    real = repo.update_session_run_state
    calls = {"n": 0}

    async def wrapper(session_id: str, run_state: dict) -> bool:
        out = await real(session_id, run_state)
        calls["n"] += 1
        if calls["n"] == after:
            await repo.request_session_stop(session_id)
        return out

    monkeypatch.setattr(repo, "update_session_run_state", wrapper)


async def _stopped_task_run(monkeypatch, instructions: str = "Loop until stopped.") -> dict:
    _press_stop_mid_run(monkeypatch)
    return await run_task("jira-expert", instructions, model=_looping_model())


# ── A: stop parks at a node boundary, with the tail persisted ─────────────────


async def test_stop_parks_at_a_node_boundary_with_the_tail_persisted(monkeypatch):
    """STOPPED, transcript intact, ending in the unsent ModelRequest — the same
    property the continuation feature stands on, for the same reason: without it
    re-entry re-executes the last tool batch."""
    out = await _stopped_task_run(monkeypatch)

    assert out["stopped"] is True
    # NOT the awaiting-operator shape — nothing is in the Decisions Inbox.
    assert out["deferred"] is False
    session_id = out["session_id"]
    assert await repo.session_status(session_id) == "STOPPED"

    rs = await repo.get_session_run_state(session_id)
    tail = rs["messages"][-1]
    assert tail["kind"] == "request", "the unsent ModelRequest must be persisted"
    assert any(p["part_kind"] == "tool-return" for p in tail["parts"]), (
        "the tail must carry the last batch's tool returns — otherwise re-entry "
        "re-executes them"
    )
    assert rs["pending_stop"]["after"] == "task"

    stopped = [e for e in await repo.list_events_of_kinds(["session.stopped"])
               if e["ref_id"] == session_id]
    assert len(stopped) == 1 and stopped[0]["actor"] == "operator"


async def test_the_park_clears_the_flag_and_creates_no_inbox_item(monkeypatch):
    """The operator initiated this — nagging them about it in the Decisions
    Inbox would be absurd. And the flag must be cleared, or the resume would
    stop again at its first boundary."""
    out = await _stopped_task_run(monkeypatch)
    session_id = out["session_id"]

    row = await repo.get_session(session_id)
    assert row["stop_requested"] is False

    items = [i for i in await repo.list_operator_items("OPEN")
             if i["session_id"] == session_id]
    assert items == [], "a stop asks the operator nothing"


async def test_no_sweep_drives_a_stopped_session(monkeypatch):
    """Stopped stays stopped. A STOPPED session in an auto-retry worklist would
    undo the whole point of the button."""
    out = await _stopped_task_run(monkeypatch)
    session_id = out["session_id"]

    assert session_id not in await repo.sessions_awaiting_resume()
    assert session_id not in await repo.sessions_awaiting_agents()
    retryable = [t["id"] for t in await repo.tasks_awaiting_retry(due_only=False)]
    task = await repo.task_for_session(session_id)
    assert task is None or task["id"] not in retryable


async def test_the_task_stays_in_progress_not_done(monkeypatch):
    """A stopped run is `deferred: False`, and the landing path's else-branch
    resolves those DONE with their output as the answer. Left unchecked, a
    stopped task would be filed as completed with "stopped by the operator" as
    the agent's answer — the loudest possible lie."""
    monkeypatch.setattr(routes, "resolve_model", aresolve(lambda *a, **k: _looping_model()))
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "Stoppable", "Loop until stopped.", "jira-expert")

    _press_stop_mid_run(monkeypatch)
    out = await routes._run_assigned_task(await repo.get_task(task_id))

    assert out["stopped"] is True
    row = await repo.get_task(task_id)
    assert row["status"] == "IN_PROGRESS"
    assert row["stopped"] is True, "the board needs the stopped badge on the wire"
    assert row["session_status"] == "STOPPED"


# ── B: resume re-enters and lands ─────────────────────────────────────────────


async def test_resume_re_enters_the_same_session_and_lands_normally(monkeypatch):
    """Same session, no second run — and it lands through `run._land`, exactly
    as an uninterrupted run would."""
    out = await _stopped_task_run(monkeypatch)
    session_id = out["session_id"]

    landed = await orchestration.resume_stopped_session(
        session_id, model=_looping_model(stop_after=0)
    )
    assert landed["resumed"] is True
    assert landed["session_id"] == session_id
    assert landed["deferred"] is False
    assert "done after the resume" in landed["output"]
    assert await repo.session_status(session_id) == "DONE"

    resumed = [e for e in await repo.list_events_of_kinds(["session.resumed"])
               if e["ref_id"] == session_id]
    assert len(resumed) == 1 and resumed[0]["actor"] == "operator"


async def test_the_claim_is_the_lock_so_a_second_resume_finds_nothing(monkeypatch):
    """SQL flip, like every other claim here — two clicks produce one run."""
    out = await _stopped_task_run(monkeypatch)
    session_id = out["session_id"]

    await orchestration.resume_stopped_session(
        session_id, model=_looping_model(stop_after=0)
    )
    assert await orchestration.resume_stopped_session(session_id) is None
    # And a session that was never stopped cannot be resumed either.
    assert await orchestration.resume_stopped_session("sess_nope") is None


# ── C: the chat lane — the composer and the abort wire ────────────────────────


async def test_a_stopped_conversation_refuses_sends_with_its_own_code(monkeypatch):
    """`why_not_sendable` gains `stopped`, and `_COMPOSER_DISABLING` gains it in
    the same change — that tuple is an ALLOWLIST, not a check, so a code missing
    from it leaves an ENABLED box that `_chat_send` then 409s."""
    _press_stop_mid_run(monkeypatch)
    out = await converse.start_conversation(
        "jira-expert", message="Loop until stopped.", model=_looping_model()
    )

    assert out["stopped"] is True and out["awaiting"] == "stopped"
    session_id = out["session_id"]
    assert await repo.session_status(session_id) == "STOPPED"
    rs = await repo.get_session_run_state(session_id)
    assert rs["pending_stop"]["after"] == "converse"

    code, message = converse.why_not_sendable(await repo.get_session(session_id))
    assert code == "stopped"
    assert code in nerve_gateway._COMPOSER_DISABLING

    composer = await nerve_gateway._composer_state(f"agent:jira-expert:{session_id}")
    assert composer["enabled"] is False and composer["code"] == "stopped"
    # The composer's refusal IS the send's 409 — one resolution, not two.
    assert composer["refusal"] == message
    # The wire fields the cockpit reads to choose its levers.
    assert composer["session"]["id"] == session_id
    assert composer["session"]["status"] == "STOPPED"

    # And it resumes back into a normal, writable lane.
    await orchestration.resume_stopped_session(
        session_id, model=_looping_model(stop_after=0)
    )
    assert await repo.session_status(session_id) == "AWAITING_OPERATOR"
    reopened = await nerve_gateway._composer_state(f"agent:jira-expert:{session_id}")
    assert reopened["enabled"] is True

    await converse.close_session(
        session_id, agent_id="jira-expert", reason="operator", actor="test",
    )


def test_the_rule_names_stopped_before_the_catch_all():
    """Offline: STOPPED must not fall through to `turn_in_progress` — "wait for
    the turn to finish" is a lie the operator would wait on forever, since
    nothing is running."""
    assert converse.why_not_sendable(
        {"id": "sess_s", "mode": "conversation", "status": "STOPPED"}
    )[0] == "stopped"


async def test_chat_abort_pushes_the_frames_the_frontend_understands():
    """Two frames, both required: the `chat`/`aborted` one the vendored
    ChatContext renders, and the lifecycle END one that actually clears the
    spinner. Without the second the panel spins forever even though the turn is
    over (2026-07-23, five rounds of exactly that)."""
    session_id = "sess_abort_" + uuid.uuid4().hex[:8]
    await repo.create_conversation_session(session_id, "jira-expert")  # RUNNING
    frames: list[dict] = []

    async def notify(frame):
        frames.append(frame)

    try:
        out = await nerve_gateway._chat_abort(
            {"sessionKey": f"agent:jira-expert:{session_id}"}, notify
        )
        assert out["stopRequested"] is True and out["sessionId"] == session_id
        assert (await repo.get_session(session_id))["stop_requested"] is True

        aborted = next(f for f in frames if f["event"] == "chat")
        assert aborted["payload"]["state"] == "aborted"
        assert aborted["payload"]["stopReason"]
        lifecycle = next(f for f in frames if f["event"] == "agent")
        assert lifecycle["payload"]["stream"] == "lifecycle"
        assert lifecycle["payload"]["data"]["phase"] == "end"

        # Nothing running -> an honest refusal, not a phantom abort frame.
        await repo.mark_session_awaiting_operator(session_id)
        with pytest.raises(Exception, match="not RUNNING"):
            await nerve_gateway._chat_abort(
                {"sessionKey": f"agent:jira-expert:{session_id}"}, notify
            )
    finally:
        await repo.mark_session_failed(session_id)


async def test_stop_refuses_a_task_with_no_live_run():
    """Parked states are cancel-for-good territory. Arming the flag on one would
    set a trap that fires at its next resume."""
    from fastapi import HTTPException

    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "Nothing running", "sit still", "jira-expert")
    with pytest.raises(HTTPException) as exc:
        await routes.stop_task(task_id)
    assert exc.value.status_code == 409


# ── D: cancel for good ────────────────────────────────────────────────────────


async def _task_with_open_work() -> tuple[str, str, str, str]:
    """A stopped-ish task carrying both an OPEN operator item and an undecided
    proposal — the two things a cancel has to wind down. Built directly so the
    test asserts the wind-down, not an agent's willingness to produce one."""
    suffix = uuid.uuid4().hex[:8]
    task_id, session_id = f"task_c_{suffix}", f"sess_c_{suffix}"
    item_id, proposal_id = f"item_c_{suffix}", f"prop_c_{suffix}"

    await repo.create_task(task_id, "Cancel me", "do a thing", "jira-expert")
    await repo.create_running_session(session_id, "jira-expert")
    await repo.start_task(task_id, session_id)
    await repo.create_operator_item(
        item_id, "question", session_id, "jira-expert", "which project?",
        task_id=task_id,
    )
    await repo.save_proposal(
        proposal_id=proposal_id, session_id=session_id, agent_id="jira-expert",
        intent="cancel-test", actions=[], evidence=[],
        expected_effect="nothing — this proposal exists to be withdrawn",
        idempotency_key=f"idem_{suffix}", status="AWAITING_HUMAN",
    )
    await repo.park_session_stopped(session_id, {"after": "task", "task_id": task_id})
    return task_id, session_id, item_id, proposal_id


async def test_cancel_for_good_winds_down_the_whole_record():
    task_id, session_id, item_id, proposal_id = await _task_with_open_work()

    out = await routes.cancel_task(task_id)

    assert out["prior_status"] == "IN_PROGRESS"
    assert out["withdrawn_items"] == [item_id]
    assert out["withdrawn_proposals"] == [proposal_id]

    # Withdrawn, never deleted, and never ANSWERED — nobody answered it.
    item = await repo.get_operator_item(item_id)
    assert item["status"] == "WITHDRAWN" and item["answer"] is None
    assert (await repo.load_proposal(proposal_id))["status"] == "WITHDRAWN"

    # Sessions are never destroyed: a status flip with the transcript kept, and
    # the close seam's own event so the cockpit's panel refetches.
    session = await repo.get_session(session_id)
    assert session["status"] == "DONE" and session["closed_reason"] == "cancelled"
    ended = [e for e in await repo.list_events_of_kinds(["conversation.ended"])
             if e["ref_id"] == session_id]
    assert len(ended) == 1

    task = await repo.get_task(task_id)
    assert task["status"] == "CANCELLED"
    cancelled = [e for e in await repo.list_events_of_kinds(["task.cancelled"])
                 if e["ref_id"] == task_id]
    assert len(cancelled) == 1
    assert cancelled[0]["payload"]["prior_status"] == "IN_PROGRESS"

    # And the Decisions Inbox is genuinely clear of it.
    payload = await nerve_gateway._decisions_list()
    assert not [i for i in payload["operatorItems"] if i["id"] == item_id]
    assert not [p for p in payload["proposals"] if p["id"] == proposal_id]


async def test_cancel_releases_the_folded_siblings():
    """A fold is a claim of coverage, not an outcome: the covering proposal will
    now never execute, so its siblings go back in the queue. Same seam the
    rejection path uses — a second copy is a second chance to forget it, and
    forgetting it silently drops mail."""
    task_id, session_id, _item_id, proposal_id = await _task_with_open_work()
    suffix = uuid.uuid4().hex[:8]
    item_id, message_id = f"work_{suffix}", f"<fold-{suffix}@example.test>"
    await repo.enroll_work_item(
        item_id, message_id, {"subject": "folded sibling", "body": "body"},
        subject="folded sibling",
    )
    assert await repo.mark_fold_pending([message_id], proposal_id) == 1

    await routes.cancel_task(task_id)

    row = await repo.get_work_item(item_id)
    assert row["state"] == "UNPROCESSED" and row["folded_into"] is None
    released = [e for e in await repo.list_events_of_kinds(["work.fold_released"])
                if e["ref_id"] == proposal_id]
    assert len(released) == 1 and released[0]["payload"]["count"] == 1


async def test_cancel_never_withdraws_an_approved_proposal():
    """An approval the operator already gave EXECUTES. Its landing finds a
    terminal task and no-ops — `resolve_task`'s already-terminal guard is what
    makes that true, so this asserts both halves."""
    task_id, session_id, _item_id, proposal_id = await _task_with_open_work()
    await repo.set_proposal_status(proposal_id, "APPROVED")

    out = await routes.cancel_task(task_id)
    assert out["withdrawn_proposals"] == []
    assert (await repo.load_proposal(proposal_id))["status"] == "APPROVED"

    # The landing that arrives afterwards cannot resurrect or re-resolve it.
    assert await repo.resolve_task(task_id, "DONE", outcome="late landing") is False
    assert (await repo.get_task(task_id))["status"] == "CANCELLED"


async def test_cancel_refuses_a_live_run_and_says_to_stop_it_first():
    """The honest refusal, and the smaller one: the run is still executing a
    node, and stop-then-cancel is two clicks on a lever already in hand."""
    from fastapi import HTTPException

    suffix = uuid.uuid4().hex[:8]
    task_id, session_id = f"task_l_{suffix}", f"sess_l_{suffix}"
    await repo.create_task(task_id, "Live", "run", "jira-expert")
    await repo.create_running_session(session_id, "jira-expert")
    await repo.start_task(task_id, session_id)

    with pytest.raises(HTTPException) as exc:
        await routes.cancel_task(task_id)
    assert exc.value.status_code == 409
    assert "stop it first" in str(exc.value.detail)
    assert (await repo.get_task(task_id))["status"] == "IN_PROGRESS"

    await repo.mark_session_failed(session_id)


async def test_cancel_still_works_on_a_plain_new_task():
    """Parity: the original NEW-only behaviour is a special case of the new
    path, not a branch that got dropped."""
    task_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(task_id, "Unassigned", "do a thing", None)
    out = await routes.cancel_task(task_id)
    assert out["ok"] is True and out["prior_status"] == "NEW"
    assert (await repo.get_task(task_id))["status"] == "CANCELLED"

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await routes.cancel_task(task_id)
    assert exc.value.status_code == 409


# ── E: wire shape ─────────────────────────────────────────────────────────────


async def test_the_task_wire_carries_the_stopped_indicator_and_session_id():
    """The cockpit hand-declares its interfaces over untyped RPC payloads, so a
    field the backend never sends is invisible to `tsc` AND to every frontend
    test — the badge would simply never render. Asserted on the BACKEND wire,
    like `test_composer_state_carries_closed_reason_to_the_cockpit`."""
    suffix = uuid.uuid4().hex[:8]
    task_id, session_id = f"task_w_{suffix}", f"sess_w_{suffix}"
    await repo.create_task(task_id, "Wire", "do a thing", "jira-expert")
    await repo.create_running_session(session_id, "jira-expert")
    await repo.start_task(task_id, session_id)
    await repo.park_session_stopped(session_id, {"after": "task", "task_id": task_id})

    listed = next(t for t in (await routes.list_tasks())["tasks"]
                  if t["id"] == task_id)
    assert listed["stopped"] is True
    assert listed["session_status"] == "STOPPED"
    assert listed["session_id"] == session_id, "the resume lever needs the id"

    # A task with no run at all must not claim to be stopped.
    other_id = "task_" + uuid.uuid4().hex[:12]
    await repo.create_task(other_id, "No run", "x", None)
    other = next(t for t in (await routes.list_tasks())["tasks"]
                 if t["id"] == other_id)
    assert other["stopped"] is False and other["session_status"] is None


async def test_the_task_wire_says_when_a_run_is_working_right_now():
    """Same wire, the LIVE half: a mid-turn run must be distinguishable from a
    hung one from the board (2026-08-26 — a local 27B takes 3-5 min a turn and
    the kanban card said nothing at all). `cc-kanban.mapTask` builds the card's
    run badge out of `session_status == RUNNING` plus `started_at`, so all three
    are pinned here."""
    suffix = uuid.uuid4().hex[:8]
    task_id, session_id = f"task_r_{suffix}", f"sess_r_{suffix}"
    await repo.create_task(task_id, "Working", "do a thing", "jira-expert")
    await repo.create_running_session(session_id, "jira-expert")
    await repo.start_task(task_id, session_id)

    listed = next(t for t in (await routes.list_tasks())["tasks"]
                  if t["id"] == task_id)
    assert listed["session_status"] == "RUNNING"
    assert listed["stopped"] is False
    assert listed["session_id"] == session_id and listed["agent_id"]
    assert listed["started_at"] is not None, "the elapsed-time chip reads it"


async def test_run_stopped_is_never_reported_as_a_failure():
    """A stop is not an error. If `RunStopped` escaped as a plain exception the
    task would be FAILED (or worse, retried) — so the park path must own it and
    the session must never be marked FAILED on the way out."""
    from central_command.runtime import run as run_mod
    from central_command.runtime.agent import build_agent_for
    from central_command.runtime.deps import TriageDeps

    session_id = "sess_raise_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(session_id, "jira-expert")
    await repo.request_session_stop(session_id)
    agent = await build_agent_for("jira-expert", model=_looping_model())

    with pytest.raises(RunStopped):
        await run_mod._run_live(
            agent, "Loop.", model=_looping_model(),
            deps=TriageDeps(agent_id="jira-expert", session_id=session_id),
            agent_id="jira-expert", session_id=session_id,
        )
    assert await repo.session_status(session_id) == "STOPPED"


# ── E: a stop click must not hide a zombie from the orphan sweep ──────────────


async def test_stop_click_leaves_the_session_sweepable():
    """`request_session_stop` must not touch `updated_at` — that column is the
    liveness signal `fail_stale_running_sessions` sweeps on, refreshed only by
    the run's own snapshots. On 2026-08-17 a run died in a restart young enough
    to survive the dispatcher's 900s sweep, and every stop click reset the
    staleness clock, making the zombie unkillable. The startup sweep (age 0)
    lands it; this pins that clicking stop first cannot un-land it."""
    session_id = "sess_zombie_" + uuid.uuid4().hex[:8]
    await repo.create_running_session(session_id, "jira-expert")
    before = (await repo.get_session(session_id))["updated_at"]

    assert await repo.request_session_stop(session_id) is True
    assert (await repo.get_session(session_id))["updated_at"] == before

    from central_command.ingest.dispatcher import sweep_orphaned_sessions
    await sweep_orphaned_sessions(0)
    assert (await repo.get_session(session_id))["status"] == "FAILED"
