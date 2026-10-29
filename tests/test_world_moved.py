"""The world moved between approval and execution (epic-world-changed-redraft,
story 1): a stale Confluence version is a typed NO.

The properties: the verdict is decided by exception TYPE (`WorldMoved`), never
by text; the approved proposal lands FAILED (never RETRY_PENDING) with
`proposal.failed {world_moved}`; the drafter is resumed with a report naming
what moved, under the charter it drafted with; and its redraft parks for the
operator with the `proposal.redraft` link on the row. A failure that is not
world-moved keeps today's resume, and its redraft carries cause `failed`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from central_command.config import settings
from central_command.contract import classify_failure
from central_command.contract.failures import WorldMoved, is_world_moved
from central_command.db import repo
from central_command.gateway import executor, gateway
from central_command.contract.models import Action
from central_command.integrations import confluence, graphiti_ingest, jira, neo4j_reader
from tests.conftest import needs_pg
from tests.test_jira_client import FakeResponse
from tests.world_moved_helpers import (  # noqa: F401 — pinned and confluence_grant are fixtures
    AGENT,
    _confluence_answers,
    _confluence_down,
    _counting_confluence,
    _Drafter,
    _draft,
    _drafting_model,
    _event,
    _page_update,
    confluence_grant,
    pinned,
)
from tests.fake_world import fake_world

# --- the type --------------------------------------------------------------------


def test_the_verdict_is_the_type_never_the_text():
    stale = confluence.ConfluenceStale(
        "updatePage — connection timeout 429", page_id="42", expected=4, actual=6,
        status_code=409,
    )
    assert is_world_moved(stale) and isinstance(stale, confluence.ConfluenceError)
    assert stale.status_code == 409
    assert classify_failure(stale) == "semantic"
    moved = WorldMoved("x", target="t", expected=1, actual=2,
                       what_moved="timeout connection 503")
    assert classify_failure(moved) == "semantic"
    # The same words on an untyped error ARE an outage: text is not the verdict.
    assert not is_world_moved(RuntimeError("page 42 version 4 -> 6"))
    assert classify_failure(RuntimeError("connection timeout")) == "transient"


async def test_a_stale_version_raises_the_typed_verdict(monkeypatch):
    _confluence_answers(monkeypatch, version=6)
    with pytest.raises(confluence.ConfluenceStale, match="stale version") as ei:
        await confluence.update_page("42", "Roadmap", "<p>new</p>", expected_version=4)
    assert (ei.value.expected, ei.value.actual) == (4, 6)
    assert ei.value.target == "confluence:page:42"


async def test_a_409_naming_a_version_conflict_is_world_moved(monkeypatch):
    _confluence_answers(monkeypatch, version=4, put=httpx.Response(
        409, text="Version must be incremented on update. Current version is: 7"))
    with pytest.raises(confluence.ConfluenceStale) as ei:
        await confluence.update_page("42", "Roadmap", "<p>new</p>", expected_version=4)
    assert (ei.value.expected, ei.value.actual, ei.value.status_code) == (4, None, 409)


async def test_a_409_that_is_not_a_version_conflict_stays_generic(monkeypatch):
    _confluence_answers(monkeypatch, version=4, put=httpx.Response(
        409, text="A page with this title already exists in this space"))
    with pytest.raises(confluence.ConfluenceError, match=r"provider rejected") as ei:
        await confluence.update_page("42", "Roadmap", "<p>new</p>", expected_version=4)
    assert not is_world_moved(ei.value)


# --- the landing, the resume, the redraft link -------------------------------------


@pytest.mark.usefixtures("confluence_grant")
async def test_a_stale_version_lands_failed_resumes_the_drafter_and_links_the_redraft(
    monkeypatch,
):
    drafter = _Drafter(_page_update(4), _page_update(6))
    row = await _draft(monkeypatch, drafter)
    assert row["redraft"] is None  # a first draft carries no link

    # R17: the charter changes between the draft and the failure.
    marker = "CHARTER-CHANGED-" + uuid.uuid4().hex[:8]
    before = await repo.current_charter(AGENT)
    saved = await repo.save_charter_version(AGENT, f"You are new.\n\n{marker}", "r17 test")
    try:
        _confluence_answers(monkeypatch, version=6)
        out = await gateway.approve_and_execute(row["session_id"], row["id"],
                                                model=drafter.model)
    finally:
        conn = await repo._conn()
        try:
            await conn.execute(
                "delete from guidance_version where agent_id = $1 and version = $2",
                AGENT, saved["version"])
            if before is not None:
                await conn.execute(
                    "update guidance_version set status = 'CURRENT' "
                    "where agent_id = $1 and version = $2", AGENT, before["version"])
        finally:
            await conn.close()

    failed = await repo.load_proposal(row["id"])
    assert failed["status"] == "FAILED"
    payload = (await _event("proposal.failed", row["id"]))["payload"]
    assert payload["transient"] is False
    assert payload["world_moved"]["expected"] == 4
    assert payload["world_moved"]["actual"] == 6
    assert payload["world_moved"]["capability"] == "confluence.update_page"
    assert "proposal.execution_parked" not in {
        e["kind"] for e in await repo.list_events(ref_id=row["id"])}

    # The resumed run got the world-moved report as its propose call's return …
    report = drafter.seen[-1]["returns"][-1]
    assert "world moved" in report and "version 4 -> 6" in report
    assert "Nothing past the completed actions was written" in report
    assert "Read the target again" in report
    # … under the charter it drafted with, not the one saved since.
    assert drafter.seen[-1]["system"] == drafter.seen[0]["system"]
    assert marker not in "\n".join(drafter.seen[-1]["system"])

    # The redraft parks for the operator, linked on the row and in the event.
    rid = out["redraft_proposal_id"]
    redraft = await repo.load_proposal(rid)
    assert redraft["status"] == "AWAITING_HUMAN"
    link = {"of": row["id"], "root": row["id"], "cause": "world_moved",
            "world_moved_count": 1}
    assert redraft["redraft"] == link
    assert (await _event("proposal.created", rid))["payload"]["redraft"] == link
    assert redraft["override_eval"]["reason"] == "redraft"

    from central_command.api import routes

    assert (await routes.get_proposal(rid))["redraft"] == link


@pytest.mark.usefixtures("confluence_grant")
async def test_a_swept_resume_links_the_redraft_the_same_way(monkeypatch):
    """The model is DOWN when the failure resumes the drafter: the session parks
    AWAITING_RESUME, and the sweep's resume builds the same world_moved link
    from the cause stored on the pending record."""
    from central_command.api import orchestration

    drafter = _Drafter(_page_update(4), _page_update(6), down_on_resume=1)
    row = await _draft(monkeypatch, drafter)
    _confluence_answers(monkeypatch, version=6)
    out = await gateway.approve_and_execute(row["session_id"], row["id"],
                                            model=drafter.model)
    assert out.get("resume_parked") is True
    assert (await repo.load_proposal(row["id"]))["status"] == "FAILED"
    pending = (await repo.get_session_run_state(row["session_id"]))["pending_resume"]
    assert pending["redraft_cause"] == "world_moved"

    swept = await orchestration.resume_parked_session(row["session_id"],
                                                      model=drafter.model)
    redraft = await repo.load_proposal(swept["redraft_proposal_id"])
    assert redraft["redraft"] == {"of": row["id"], "root": row["id"],
                                  "cause": "world_moved", "world_moved_count": 1}


@pytest.mark.usefixtures("confluence_grant")
async def test_a_409_version_conflict_takes_the_same_path(monkeypatch):
    drafter = _Drafter(_page_update(4), _page_update(7))
    row = await _draft(monkeypatch, drafter)
    _confluence_answers(monkeypatch, version=4, put=httpx.Response(
        409, text="Version must be incremented on update. Current version is: 7"))
    out = await gateway.approve_and_execute(row["session_id"], row["id"],
                                            model=drafter.model)

    assert (await repo.load_proposal(row["id"]))["status"] == "FAILED"
    payload = (await _event("proposal.failed", row["id"]))["payload"]
    assert payload["world_moved"]["actual"] is None
    assert payload["transient"] is False
    redraft = await repo.load_proposal(out["redraft_proposal_id"])
    assert redraft["redraft"]["cause"] == "world_moved"


@pytest.mark.usefixtures("confluence_grant")
async def test_world_moved_text_saying_timeout_still_lands_failed(monkeypatch):
    drafter = _Drafter(_page_update(4), _page_update(6))
    row = await _draft(monkeypatch, drafter)

    async def moved(*a, **k):
        raise WorldMoved("connection timeout", target="confluence:page:42",
                         expected=4, actual=6, what_moved="timeout 503 connection")

    monkeypatch.setattr(executor.confluence, "update_page", moved)
    await gateway.approve_and_execute(row["session_id"], row["id"], model=drafter.model)
    assert (await repo.load_proposal(row["id"]))["status"] == "FAILED"


async def test_a_plain_semantic_failure_keeps_todays_resume_with_cause_failed(monkeypatch):
    from central_command.runtime.run import ingest_and_propose
    from central_command.runtime.spike_model import _PROPOSAL

    async def broken(issue_key, due_date):
        raise RuntimeError("400 due date is not a valid date")

    monkeypatch.setattr(executor.jira, "set_due_date", broken)
    second = {**_PROPOSAL, "intent": "Redraft after the 400."}
    drafter = _Drafter(_PROPOSAL, second)
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=drafter.model)
    out = await gateway.approve_and_execute(run["session_id"], run["proposal_id"],
                                            model=drafter.model)

    payload = (await _event("proposal.failed", run["proposal_id"]))["payload"]
    assert payload["world_moved"] is None
    assert "world moved" not in drafter.seen[-1]["returns"][-1]
    redraft = await repo.load_proposal(out["redraft_proposal_id"])
    assert redraft["redraft"] == {"of": run["proposal_id"], "root": run["proposal_id"],
                                  "cause": "failed", "world_moved_count": 0}


async def test_a_reject_redraft_carries_cause_rejected(monkeypatch):
    from central_command.runtime.run import ingest_and_propose
    from central_command.runtime.spike_model import make_redraft_model

    fake_world(monkeypatch)
    model = make_redraft_model()
    run = await ingest_and_propose("Dana: DEMO-1 slipped, due Aug 3.", model=model)
    out = await gateway.reject_with_feedback(
        run["session_id"], run["proposal_id"],
        "Wrong — I spoke to Dana, make it 2026-08-10.", model=model)
    redraft = await repo.load_proposal(out["redraft_proposal_id"])
    assert redraft["redraft"] == {"of": run["proposal_id"], "root": run["proposal_id"],
                                  "cause": "rejected", "world_moved_count": 0}


async def test_the_link_carries_root_and_count_down_a_chain(monkeypatch):
    """A world-moved redraft of a redraft keeps the chain's root and adds one;
    a Reject or a plain failure keeps the count."""
    rows = {
        "prop_root": {"redraft": None},
        "prop_mid": {"redraft": {"of": "prop_root", "root": "prop_root",
                                 "cause": "world_moved", "world_moved_count": 1}},
    }

    async def load(pid):
        return rows.get(pid)

    monkeypatch.setattr(gateway.repo, "load_proposal", load)
    assert await gateway.redraft_link("prop_root", "world_moved") == {
        "of": "prop_root", "root": "prop_root", "cause": "world_moved",
        "world_moved_count": 1}
    assert await gateway.redraft_link("prop_mid", "world_moved") == {
        "of": "prop_mid", "root": "prop_root", "cause": "world_moved",
        "world_moved_count": 2}
    assert (await gateway.redraft_link("prop_mid", "rejected"))["world_moved_count"] == 1
    assert (await gateway.redraft_link("prop_mid", "failed"))["world_moved_count"] == 1
    with pytest.raises(ValueError):
        await gateway.redraft_link("prop_mid", "moved")


# --- every copy of a failure keeps the verdict ------------------------------------


def test_the_gateway_never_reads_world_moved_as_transient():
    f = executor.ExecutionFailed(
        "confluence.update_page", "connection timeout", [], transient=True,
        world_moved={"target": "confluence:page:42", "expected": 4, "actual": 6,
                     "what_moved": "x", "capability": "confluence.update_page"},
    )
    assert gateway._execution_transient(f) is False


@pytest.mark.usefixtures("confluence_grant")
async def test_a_retried_execution_keeps_world_moved_through_the_splice(monkeypatch):
    """A Confluence update parked on an outage whose page moved meanwhile: the
    retry's splice carries `world_moved`, so it lands FAILED with the record."""
    drafter = _Drafter(_page_update(4), _page_update(6))
    row = await _draft(monkeypatch, drafter)

    _confluence_down(monkeypatch)
    out = await gateway.approve_and_execute(row["session_id"], row["id"], model=drafter.model)
    assert out["execution"] == "parked"

    _confluence_answers(monkeypatch, version=6)
    out = await gateway.retry_execution(row["id"], model=drafter.model)
    assert (await repo.load_proposal(row["id"]))["status"] == "FAILED"
    payload = (await _event("proposal.failed", row["id"]))["payload"]
    assert payload["world_moved"]["actual"] == 6
    redraft = await repo.load_proposal(out["redraft_proposal_id"])
    assert redraft["redraft"]["cause"] == "world_moved"


def test_the_redraft_column_is_additive():
    from pathlib import Path

    schema = (Path(__file__).parents[1] / "central_command/db/schema.sql").read_text()
    assert "alter table proposal add column if not exists redraft jsonb;" in schema


# --- Jira: a transition that vanished (story 2) ------------------------------------


def _stamp(delta_hours: float) -> str:
    """Jira's own `updated` spelling, relative to now."""
    at = datetime.now(timezone.utc) + timedelta(hours=delta_hours)
    return at.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _jira_answers(monkeypatch, *, updated: str | None, available=("Resolve",),
                  status: str = "In Review") -> list[tuple[str, str]]:
    """A fake Jira under the REAL `transition_issue`/`add_comment`: the
    transitions list holds `available`, the narrow issue read answers
    `status`/`updated`, and every write succeeds. Returns the calls made."""
    monkeypatch.setattr(settings, "jira_email", "bot@example.com")
    monkeypatch.setattr(settings, "jira_api_token", "tok")
    calls: list[tuple[str, str]] = []

    async def fake(method, path, json_body=None):
        calls.append((method, path))
        if method == "GET" and path.endswith("/transitions"):
            return FakeResponse(200, {"transitions": [
                {"id": str(i), "name": n, "to": {"name": n}}
                for i, n in enumerate(available, start=11)]})
        if method == "GET" and "fields=status,updated" in path:
            return FakeResponse(200, {"fields": {"status": {"name": status},
                                                 "updated": updated}})
        if method == "POST" and path.endswith("/comment"):
            return FakeResponse(201, {"id": "9"})
        if method == "POST" and path.endswith("/transitions"):
            return FakeResponse(204, {})
        raise AssertionError(f"unexpected Jira call {method} {path}")

    monkeypatch.setattr(jira, "_call", fake)
    return calls


def _jira_action(capability: str, **arguments) -> dict:
    return {"capability": capability, "arguments": arguments,
            "target_ref": {"system": "jira", "id": arguments["issue_key"],
                           "read_version": "etag:1"},
            "reversibility": "reversible"}


def _jira_proposal(*, comment_first: bool = False, transition: str = "Done") -> dict:
    actions = [_jira_action("jira.transition_issue", issue_key="DEMO-7",
                            transition=transition)]
    if comment_first:
        actions.insert(0, _jira_action("jira.add_comment", issue_key="DEMO-7",
                                       body="Closing this out."))
    return {
        "intent": "Close DEMO-7",
        "actions": actions,
        "evidence": [{"kind": "operator", "source_ref": "operator:task",
                      "locator": "test task", "claim": "Operator tasked a close."}],
        "expected_effect": "DEMO-7 is Done",
    }


def test_transition_unavailable_is_plain_and_transition_moved_is_the_typed_verdict():
    plain = jira.TransitionUnavailable(
        "transition 'Done' is not available (available: Resolve)", issue_key="DEMO-7",
        transition="Done", available=["Resolve"], status="In Review",
        updated="2026-10-28T12:00:00.000+0000")
    assert isinstance(plain, jira.JiraError) and not is_world_moved(plain)
    moved = jira.TransitionMoved(plain)
    assert is_world_moved(moved) and isinstance(moved, jira.JiraError)
    assert classify_failure(moved) == "semantic"
    assert (moved.target, moved.expected) == ("jira:issue:DEMO-7", "Done")
    assert moved.actual == {"available": ["Resolve"], "status": "In Review"}
    assert "Resolve" in moved.what_moved and "In Review" in moved.what_moved


async def test_an_issue_updated_after_the_draft_lands_failed_world_moved_and_resumes_the_drafter(
    monkeypatch,
):
    first, second = _jira_proposal(), _jira_proposal(transition="Resolve")
    drafter = _Drafter(first, second)
    row = await _draft(monkeypatch, drafter)
    _jira_answers(monkeypatch, updated=_stamp(+1))
    out = await gateway.approve_and_execute(row["session_id"], row["id"],
                                            model=drafter.model)

    assert (await repo.load_proposal(row["id"]))["status"] == "FAILED"
    payload = (await _event("proposal.failed", row["id"]))["payload"]
    assert payload["transient"] is False
    assert payload["world_moved"] == {
        "capability": "jira.transition_issue", "target": "jira:issue:DEMO-7",
        "expected": "Done",
        "actual": {"available": ["Resolve"], "status": "In Review"},
        "what_moved": payload["world_moved"]["what_moved"]}
    report = drafter.seen[-1]["returns"][-1]
    assert "world moved" in report and "Resolve" in report and "In Review" in report
    redraft = await repo.load_proposal(out["redraft_proposal_id"])
    assert redraft["redraft"]["cause"] == "world_moved"


async def test_the_same_unavailable_name_on_an_untouched_issue_stays_plain_semantic(
    monkeypatch,
):
    drafter = _Drafter(_jira_proposal(), _jira_proposal(transition="Resolve"))
    row = await _draft(monkeypatch, drafter)
    _jira_answers(monkeypatch, updated=_stamp(-1))  # updated BEFORE the draft
    out = await gateway.approve_and_execute(row["session_id"], row["id"],
                                            model=drafter.model)

    assert (await repo.load_proposal(row["id"]))["status"] == "FAILED"
    payload = (await _event("proposal.failed", row["id"]))["payload"]
    assert payload["world_moved"] is None and payload["transient"] is False
    report = drafter.seen[-1]["returns"][-1]
    assert "world moved" not in report and "not available" in report
    # The drafter is still resumed (R5): a drafting error redrafts, cause `failed`.
    redraft = await repo.load_proposal(out["redraft_proposal_id"])
    assert redraft["redraft"]["cause"] == "failed"


async def test_a_comment_then_a_transition_on_the_same_issue_is_plain_semantic(
    monkeypatch,
):
    """The proposal's OWN comment moved the issue's `updated` past `created_at`,
    so the unavailable name is a drafting error, never the world moving."""
    drafter = _Drafter(_jira_proposal(comment_first=True), _jira_proposal(transition="Resolve"))
    row = await _draft(monkeypatch, drafter)
    calls = _jira_answers(monkeypatch, updated=_stamp(+1))
    await gateway.approve_and_execute(row["session_id"], row["id"], model=drafter.model)

    assert ("POST", "/rest/api/3/issue/DEMO-7/comment") in calls  # the prefix ran
    payload = (await _event("proposal.failed", row["id"]))["payload"]
    assert payload["world_moved"] is None
    assert len(payload["completed_actions"]) == 1  # the comment landed first
    assert "world moved" not in drafter.seen[-1]["returns"][-1]


async def test_a_retry_after_a_completed_prefix_still_knows_the_issue_was_touched(
    monkeypatch,
):
    """A retry resumes at `actions[next:]`, so the comment is no longer in the
    list `execute()` receives: the touched set is seeded from the stored row."""
    drafter = _Drafter(_jira_proposal(comment_first=True), _jira_proposal())
    row = await _draft(monkeypatch, drafter)
    _jira_answers(monkeypatch, updated=_stamp(+1))
    actions = [Action(**a) for a in row["actions"]]

    with pytest.raises(executor.ExecutionFailed) as resumed:
        await executor.execute(actions[1:], "human:doe", [], proposer=AGENT,
                               proposal_id=row["id"], action_offset=1)
    assert resumed.value.world_moved is None
    # The same transition run from the start of a list that does NOT include
    # the comment (offset 0) has nothing touching the issue: world-moved.
    with pytest.raises(executor.ExecutionFailed) as fresh:
        await executor.execute(actions[1:], "human:doe", [], proposer=AGENT,
                               proposal_id=row["id"], action_offset=0)
    assert fresh.value.world_moved["target"] == "jira:issue:DEMO-7"


@pytest.mark.parametrize("updated", [None, "not a time", "2999-01-01T00:00:00"])
async def test_an_updated_that_cannot_be_compared_is_plain_semantic(monkeypatch, updated):
    """Missing, unparseable or offset-less: never guessed into a world-moved."""
    drafter = _Drafter(_jira_proposal(), _jira_proposal(transition="Resolve"))
    row = await _draft(monkeypatch, drafter)
    _jira_answers(monkeypatch, updated=updated)
    with pytest.raises(executor.ExecutionFailed) as ei:
        await executor.execute([Action(**a) for a in row["actions"]], "human:doe", [],
                               proposer=AGENT, proposal_id=row["id"])
    assert ei.value.world_moved is None


async def test_no_proposal_id_is_plain_semantic(monkeypatch):
    _jira_answers(monkeypatch, updated=_stamp(+1))
    with pytest.raises(executor.ExecutionFailed) as ei:
        await executor.execute([Action(**_jira_action(
            "jira.transition_issue", issue_key="DEMO-7", transition="Done"))],
            "human:doe", [])
    assert ei.value.world_moved is None


async def test_another_caller_of_transition_issue_never_sees_a_world_moved(monkeypatch):
    """The integration raises the PLAIN error whatever `updated` says, so
    `is_world_moved` is honest for every caller but the Executor's decision."""
    _jira_answers(monkeypatch, updated=_stamp(+1))
    with pytest.raises(jira.TransitionUnavailable) as ei:
        await jira.transition_issue("DEMO-7", "Done")
    assert not is_world_moved(ei.value)


# --- the graph deletion (story 2) --------------------------------------------------


def _preview(digest: str, facts=()) -> dict:
    return {"episode": {"uuid": "X", "name": "e", "group_id": "g"}, "digest": digest,
            "facts": [{"uuid": f} for f in facts], "collateral_facts": [],
            "entities": []}


@pytest.fixture
def graph_world(monkeypatch):
    state = {"preview": None, "queued": []}

    async def fake_preview(uuid):
        return state["preview"]

    async def fake_enqueue(*a, **k):
        state["queued"].append((a, k))
        return {"id": 1}

    monkeypatch.setattr(neo4j_reader, "episode_delete_preview", fake_preview)
    monkeypatch.setattr(graphiti_ingest, "enqueue_delete", fake_enqueue)
    return state


def _delete_action(approved: dict) -> Action:
    return Action(capability="graph.delete_episode",
                  arguments={"episode_uuid": "X", "preview": approved},
                  target_ref={"system": "graph", "id": "X", "read_version": approved["digest"]},
                  reversibility="irreversible")


async def test_a_changed_deletion_preview_lands_world_moved_and_deletes_nothing(graph_world):
    graph_world["preview"] = _preview("d2", facts=["F1"])
    with pytest.raises(executor.ExecutionFailed) as ei:
        await executor.execute([_delete_action(_preview("d1"))], "human:doe", [])
    moved = ei.value.world_moved
    assert (moved["expected"], moved["actual"]) == ("d1", "d2")
    assert moved["target"] == "graph:episode:X" and ei.value.transient is False
    assert "F1" in moved["what_moved"]
    assert graph_world["queued"] == []


async def test_a_vanished_episode_lands_world_moved_and_deletes_nothing(graph_world):
    graph_world["preview"] = None
    with pytest.raises(executor.ExecutionFailed) as ei:
        await executor.execute([_delete_action(_preview("d1"))], "human:doe", [])
    moved = ei.value.world_moved
    assert (moved["expected"], moved["actual"]) == ("d1", None)
    assert "no longer exists" in moved["what_moved"]
    assert graph_world["queued"] == []


async def test_a_deletion_with_no_preview_is_still_a_drafting_error(graph_world):
    action = Action(capability="graph.delete_episode", arguments={"episode_uuid": "X"},
                    target_ref={"system": "graph", "id": "X", "read_version": "d"},
                    reversibility="irreversible")
    with pytest.raises(executor.ExecutionFailed, match="no preview") as ei:
        await executor.execute([action], "human:doe", [])
    assert ei.value.world_moved is None


# --- the re-check before the first action (story 4) --------------------------------


def _mixed_proposal(expected_version: int) -> dict:
    """jira.add_comment, jira.set_due_date, then the page update."""
    page = _page_update(expected_version)
    return {**page, "intent": "Comment, date and update the page",
            "actions": [
                _jira_action("jira.add_comment", issue_key="DEMO-7", body="Noted."),
                _jira_action("jira.set_due_date", issue_key="DEMO-7", due_date="2026-11-30"),
                *page["actions"]]}


def _jira_spy(monkeypatch) -> list[tuple[str, str]]:
    """A fake Jira that records every call and answers every write."""
    monkeypatch.setattr(settings, "jira_email", "bot@example.com")
    monkeypatch.setattr(settings, "jira_api_token", "tok")
    calls: list[tuple[str, str]] = []

    async def fake(method, path, json_body=None):
        calls.append((method, path))
        return FakeResponse(201 if method == "POST" else 204, {"id": "9"})

    monkeypatch.setattr(jira, "_call", fake)
    return calls


@pytest.mark.usefixtures("confluence_grant")
async def test_a_moved_page_third_in_line_stops_the_first_two_from_running(monkeypatch):
    drafter = _Drafter(_mixed_proposal(4), _mixed_proposal(6))
    row = await _draft(monkeypatch, drafter)
    jira_calls = _jira_spy(monkeypatch)
    _counting_confluence(monkeypatch, version=6)
    out = await gateway.approve_and_execute(row["session_id"], row["id"],
                                            model=drafter.model)

    assert (await repo.load_proposal(row["id"]))["status"] == "FAILED"
    payload = (await _event("proposal.failed", row["id"]))["payload"]
    assert payload["completed_actions"] == []
    assert payload["failed_capability"] == "confluence.update_page"
    assert (payload["world_moved"]["expected"], payload["world_moved"]["actual"]) == (4, 6)
    assert jira_calls == [], "the fake Jira saw no write"
    redraft = await repo.load_proposal(out["redraft_proposal_id"])
    assert redraft["redraft"]["cause"] == "world_moved"


async def test_two_updates_to_one_page_are_pre_checked_on_the_first_only(monkeypatch):
    first, second = _page_update(4)["actions"][0], _page_update(5)["actions"][0]
    state = _counting_confluence(monkeypatch, version=4)
    out = await executor.execute([Action(**first), Action(**second)], "human:doe", [])

    assert "updated to version 6" in out.result_text
    # The re-check's read, then each handler's own read and write. A re-check
    # of the second action (v5 against a page at v4) would have refused.
    assert state["calls"] == ["GET", "GET", "PUT", "GET", "PUT"]


async def test_a_moved_transition_is_caught_before_an_earlier_untouched_write(monkeypatch):
    """The Jira check through the re-check: a set_due_date on another issue
    does not run when DEMO-7's transition vanished after the draft."""
    drafter = _Drafter(_jira_proposal(), _jira_proposal(transition="Resolve"))
    row = await _draft(monkeypatch, drafter)
    calls = _jira_answers(monkeypatch, updated=_stamp(+1))
    actions = [Action(**_jira_action("jira.set_due_date", issue_key="DEMO-8",
                                     due_date="2026-11-30")),
               *[Action(**a) for a in row["actions"]]]
    with pytest.raises(executor.ExecutionFailed) as ei:
        await executor.execute(actions, "human:doe", [], proposer=AGENT,
                               proposal_id=row["id"])
    assert ei.value.completed == []
    assert ei.value.world_moved["target"] == "jira:issue:DEMO-7"
    assert all(method == "GET" for method, _ in calls)


async def test_a_graph_deletion_is_re_checked_before_an_earlier_action(graph_world, monkeypatch):
    graph_world["preview"] = _preview("d2", facts=["F1"])
    jira_calls = _jira_spy(monkeypatch)
    comment = Action(**_jira_action("jira.add_comment", issue_key="DEMO-7", body="x"))
    with pytest.raises(executor.ExecutionFailed) as ei:
        await executor.execute([comment, _delete_action(_preview("d1"))], "human:doe", [])
    assert ei.value.completed == [] and ei.value.world_moved["actual"] == "d2"
    assert jira_calls == [] and graph_world["queued"] == []


@pytest.mark.usefixtures("confluence_grant")
async def test_a_parked_update_whose_page_moved_in_the_outage_fails_world_moved_on_the_sweep(
    monkeypatch,
):
    """Confluence goes DOWN between the re-check and the page write, after the
    comment landed: the proposal parks with that prefix. The page moves during
    the outage; the sweep's re-check finds it, nothing new runs, and the
    record keeps the prefix the first attempt landed."""
    from central_command.api import orchestration
    from tests.conftest import make_parks_due
    from tests.test_execution_retry import _Up

    proposal = {**_page_update(4), "actions": [
        _jira_action("jira.add_comment", issue_key="DEMO-7", body="Updating the page."),
        *_page_update(4)["actions"]]}
    drafter = _Drafter(proposal, _page_update(6))
    row = await _draft(monkeypatch, drafter)
    jira_calls = _jira_spy(monkeypatch)
    _counting_confluence(monkeypatch, version=4, down_after_gets=1)
    out = await gateway.approve_and_execute(row["session_id"], row["id"],
                                            model=drafter.model)
    assert out["execution"] == "parked"
    parked = await repo.load_proposal(row["id"])
    assert parked["status"] == "RETRY_PENDING"
    assert parked["retry_state"]["next_action_index"] == 1
    prefix = parked["retry_state"]["completed_results"]
    assert len(prefix) == 1 and len(jira_calls) == 1

    _counting_confluence(monkeypatch, version=6)  # back, and moved meanwhile
    _drafting_model(monkeypatch, drafter)
    await make_parks_due()
    await orchestration.retry_sweep(cache=_Up(), only="confluence")
    await orchestration.sweep_settle()

    assert (await repo.load_proposal(row["id"]))["status"] == "FAILED"
    payload = (await _event("proposal.failed", row["id"]))["payload"]
    assert payload["world_moved"]["actual"] == 6
    assert payload["completed_actions"] == prefix
    assert len(jira_calls) == 1, "the comment is never replayed"
    redrafts = [e for e in await repo.list_events(kind="proposal.created", newest_first=True)
                if (e["payload"].get("redraft") or {}).get("of") == row["id"]]
    assert redrafts and redrafts[0]["payload"]["redraft"]["cause"] == "world_moved"


@pytest.mark.usefixtures("confluence_grant")
@pytest.mark.parametrize("down", ["transport", "503"])
async def test_a_down_confluence_on_the_re_check_parks_again_with_an_attempt_spent(
    monkeypatch, down,
):
    from central_command.api import orchestration
    from tests.conftest import make_parks_due
    from tests.test_execution_retry import _Up

    drafter = _Drafter(_page_update(4), _page_update(6))
    row = await _draft(monkeypatch, drafter)
    _confluence_down(monkeypatch)
    out = await gateway.approve_and_execute(row["session_id"], row["id"],
                                            model=drafter.model)
    assert out["execution"] == "parked"
    attempts = (await repo.load_proposal(row["id"]))["retry_state"]["attempts"]

    calls: list = []
    if down == "transport":
        _confluence_down(monkeypatch, calls)
    else:
        async def unavailable(method, path, **_kw):
            calls.append((method, path))
            return httpx.Response(503, text="<html>Service Unavailable</html>")

        monkeypatch.setattr(confluence, "_call", unavailable)
    _drafting_model(monkeypatch, drafter)
    await make_parks_due()
    await orchestration.retry_sweep(cache=_Up(), only="confluence")
    await orchestration.sweep_settle()

    again = await repo.load_proposal(row["id"])
    assert again["status"] == "RETRY_PENDING"
    assert again["retry_state"]["attempts"] == attempts + 1
    assert again["retry_state"]["dependencies"] == ["confluence"]
    assert calls and all(c[0] == "GET" for c in calls), "only the re-check's read"
    assert "proposal.failed" not in {
        e["kind"] for e in await repo.list_events(ref_id=row["id"])}


@pytest.mark.usefixtures("confluence_grant")
async def test_an_interrupted_proposal_retried_now_re_checks_first_and_names_the_in_flight_action(
    monkeypatch,
):
    from central_command.api import orchestration
    from tests.test_execution_retry import _Up

    drafter = _Drafter(_mixed_proposal(4), _page_update(6))
    row = await _draft(monkeypatch, drafter)
    # A restart killed the execution while the due date (action 2) was in
    # flight: the comment had landed, and the hold keeps the cursor at 1.
    conn = await repo._conn()
    try:
        await conn.execute(
            "update proposal set status = 'EXECUTING', retry_state = $2::jsonb "
            "where id = $1", row["id"],
            '{"next_action_index": 1, "completed_results": ["comment added to DEMO-7"], '
            '"attempts": 1, "approver": "human:doe"}')
    finally:
        await conn.close()
    await repo.park_interrupted_executions()
    assert (await repo.load_proposal(row["id"]))["retry_state"]["interrupted"] is True

    jira_calls = _jira_spy(monkeypatch)
    _counting_confluence(monkeypatch, version=6)
    _drafting_model(monkeypatch, drafter)
    await orchestration.retry_sweep(cache=_Up(), only="jira", ignore_not_before=True,
                                    include_interrupted=True)
    await orchestration.sweep_settle()

    assert (await repo.load_proposal(row["id"]))["status"] == "FAILED"
    payload = (await _event("proposal.failed", row["id"]))["payload"]
    assert payload["world_moved"]["actual"] == 6
    assert payload["completed_actions"] == ["comment added to DEMO-7"]
    assert jira_calls == [], "the re-check stopped the in-flight action's replay"
    report = drafter.seen[-1]["returns"][-1]
    assert "interrupted by a restart while jira.set_due_date" in report
    assert "may itself have landed" in report


@pytest.mark.usefixtures("confluence_grant")
async def test_an_override_decided_world_moved_redraft_parks_for_the_operator(monkeypatch):
    row = await repo.create_gate_override(AGENT, "confluence.update_page", shadow_runs=0,
                                          acknowledged_irreversible=True)
    try:
        _counting_confluence(monkeypatch, version=6)
        drafter = _Drafter(_page_update(4), _page_update(6))
        failed = await _draft(monkeypatch, drafter)
        from central_command.api import orchestration

        await orchestration.sweep_settle()
        failed = await repo.load_proposal(failed["id"])
        assert failed["status"] == "FAILED"
        decided = await _event("proposal.decided", failed["id"])
        assert decided["actor"].startswith("override:")
        payload = (await _event("proposal.failed", failed["id"]))["payload"]
        assert payload["world_moved"]["actual"] == 6
        redrafts = [e for e in await repo.list_events(kind="proposal.created", newest_first=True)
                    if (e["payload"].get("redraft") or {}).get("of") == failed["id"]]
        assert redrafts, "the drafter redrafted"
        redraft = await repo.load_proposal(redrafts[0]["ref_id"])
        assert redraft["status"] == "AWAITING_HUMAN"
        assert redraft["override_eval"]["reason"] == "redraft"
    finally:
        await repo.revoke_gate_override(row["id"], reason="test teardown")
