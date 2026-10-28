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
from pydantic_ai.messages import (
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from central_command.config import settings
from central_command.contract import classify_failure
from central_command.contract.failures import WorldMoved, is_world_moved
from central_command.db import repo
from central_command.gateway import executor, gateway
from central_command.contract.models import Action
from central_command.integrations import confluence, graphiti_ingest, jira, neo4j_reader
from tests.conftest import needs_pg
from tests.test_jira_client import FakeResponse

AGENT = "jira-expert"
_ORIGINAL_CLIENT = httpx.AsyncClient
_REAL_UPDATE_PAGE = confluence.update_page


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "executor_mode", "live")
    monkeypatch.setattr(settings, "auditor_enabled", False)
    monkeypatch.setattr(settings, "confluence_base_url", "https://example.atlassian.net")
    monkeypatch.setattr(settings, "confluence_email", "bot@example.com")
    monkeypatch.setattr(settings, "confluence_api_token", "tok")
    monkeypatch.setattr(settings, "confluence_auth_mode", "basic")
    monkeypatch.setattr(settings, "confluence_api_flavor", "cloud")


def _confluence_answers(monkeypatch, *, version: int, put: httpx.Response | None = None):
    """A fake Confluence through the REAL `update_page`: the GET answers the
    page at `version`; the PUT answers `put` (a 200 at version+1 by default)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={
                "id": "42", "title": "Roadmap", "spaceId": "9",
                "version": {"number": version},
                "body": {"storage": {"value": "<p>current</p>"}},
            })
        return put or httpx.Response(200, json={
            "id": "42", "title": "Roadmap", "spaceId": "9",
            "version": {"number": version + 1},
        })

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return _ORIGINAL_CLIENT(**kwargs)

    monkeypatch.setattr(confluence.httpx, "AsyncClient", factory)


def _page_update(expected_version: int) -> dict:
    return {
        "intent": "Update the roadmap page",
        "actions": [{
            "capability": "confluence.update_page",
            "arguments": {"page_id": "42", "title": "Roadmap",
                          "body_storage": "<p>new</p>",
                          "expected_version": expected_version},
            "target_ref": {"system": "confluence", "id": "42",
                           "read_version": str(expected_version)},
            "reversibility": "reversible",
        }],
        "evidence": [{"kind": "operator", "source_ref": "operator:task",
                      "locator": "test task", "claim": "Operator tasked an edit."}],
        "expected_effect": "page 42 carries the new body",
    }


class _Drafter:
    """Proposes `first`; resumed with a FAILED report, redrafts once with
    `second`; otherwise closes out. Records what each call saw, so a test can
    read the tool return the resumed run received and its system prompt."""

    def __init__(self, first: dict, second: dict, *, down_on_resume: int = 0):
        self.first, self.second = first, second
        self.down_on_resume = down_on_resume  # the model is DOWN this many resumes
        self.seen: list[dict] = []
        self.model = FunctionModel(self._respond)

    def _respond(self, messages, info: AgentInfo) -> ModelResponse:
        parts = [p for m in messages for p in getattr(m, "parts", [])]
        if self.down_on_resume and any(isinstance(p, ToolReturnPart) for p in parts):
            self.down_on_resume -= 1
            raise httpx.ConnectError("connection refused")
        self.seen.append({
            "system": [p.content for p in parts if isinstance(p, SystemPromptPart)],
            "returns": [str(p.content) for p in parts
                        if isinstance(p, ToolReturnPart) and p.tool_name == "propose_action"],
        })
        made = sum(isinstance(p, ToolCallPart) and p.tool_name == "propose_action"
                   for p in parts)
        if made == 0:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="propose_action", args={"proposal": self.first})])
        last = self.seen[-1]["returns"]
        if made == 1 and last and "FAILED" in last[-1]:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="propose_action", args={"proposal": self.second})])
        return ModelResponse(parts=[TextPart(content="Closing out.")])


@pytest.fixture
async def confluence_grant():
    from central_command.runtime import packs as packs_mod

    held = await packs_mod.granted_packs(AGENT)
    granted_here = "confluence.update_page" not in packs_mod.granted_capability_names(held)
    if granted_here:
        await repo.grant_pack(AGENT, "confluence-propose")
    yield
    if granted_here:
        conn = await repo._conn()
        try:
            await conn.execute(
                "delete from agent_grant where agent_id = $1 and pack = $2",
                AGENT, "confluence-propose")
        finally:
            await conn.close()


async def _draft(monkeypatch, drafter: _Drafter) -> dict:
    from central_command.api import routes

    async def fixed_model(**_kw):
        return drafter.model

    monkeypatch.setattr(routes, "resolve_model", fixed_model)
    task_id = f"task_wm_{uuid.uuid4().hex[:10]}"
    await repo.create_task(task_id, "edit the roadmap", "edit the page", AGENT,
                           created_by="operator")
    run = await routes._run_assigned_task(await repo.get_task(task_id))
    return await repo.load_proposal(run["proposal_id"])


async def _event(kind: str, ref_id: str) -> dict:
    return next(e for e in await repo.list_events(ref_id=ref_id) if e["kind"] == kind)


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

    monkeypatch.setattr(settings, "executor_mode", "dry_run")
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

    async def down(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(executor.confluence, "update_page", down)
    out = await gateway.approve_and_execute(row["session_id"], row["id"], model=drafter.model)
    assert out["execution"] == "parked"

    _confluence_answers(monkeypatch, version=6)
    monkeypatch.setattr(executor.confluence, "update_page", _REAL_UPDATE_PAGE)
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
