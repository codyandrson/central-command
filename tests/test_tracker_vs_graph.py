"""Work lives in the tracker, lasting facts in the graph (DL-222, 2026-11-20).

What produced it: the executive assistant proposed ten graph episodes in one
day that were decisions about issues (one to close, six slipped a week, a
quarter's deferrals, an issue's stakes), each beside or instead of the tracker
change that records it. Three instructions pointed there: session-close
reflection (any "durable lesson", named by issue key), the assistant's
charter ("what the operator DECIDED ... a priority call, a deadline, who owns
what") and the quarterly review's framing. This file pins what replaced them:

- the reflection prompt's lesson rule, and no episode from a concluded lane;
- the charter's and the framings' one-home rule and the completion step;
- an issue's comments as a read on request, with a pointer on the issue read;
- `jira-comment-propose`, a comment and no other tracker write;
- the nightly sweep reporting what it finds instead of commenting it.

NO NETWORK: the Jira transport is faked.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import re

import pytest

from central_command.config import settings
from central_command.contract import dependencies
from central_command.db import repo
from central_command.gateway import capabilities
from central_command.heartbeat import actions
from central_command.integrations import jira
from central_command.runtime import converse, ea_profile, packs, reflection, tools
from central_command.runtime.templates import EA_TEMPLATE

SCHEMA = pathlib.Path(repo.__file__).with_name("schema.sql").read_text(encoding="utf-8")


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setattr(settings, "jira_base_url", "https://jira.example.com")
    monkeypatch.setattr(settings, "jira_email", "jane.doe@example.com")
    monkeypatch.setattr(settings, "jira_api_token", "tok")


def _fake_call(monkeypatch, responses):
    calls = []

    async def fake(method, path, json_body=None):
        calls.append((method, path))
        return _Resp(responses.pop(0))

    monkeypatch.setattr(jira, "_call", fake)
    return calls


def _comment(n, body, author="Jane Doe"):
    return {"id": str(n), "author": {"displayName": author}, "body": body,
            "created": f"2026-11-{n:02d}T10:00:00.000+0000",
            "updated": f"2026-11-{n:02d}T10:00:00.000+0000"}


# --- 1. the comments read ----------------------------------------------------


async def test_comments_come_newest_first_as_one_bounded_page(native, monkeypatch):
    calls = _fake_call(monkeypatch, [
        {"total": 7, "comments": [_comment(7, "newest"), _comment(6, "older")]},
    ])
    out = await jira.list_comments("TASKS-12", limit=2)
    assert calls == [("GET", jira._api(
        "/issue/TASKS-12/comment?orderBy=-created&startAt=0&maxResults=2"))]
    assert [c["body"] for c in out["comments"]] == ["newest", "older"]
    assert out["comments"][0] == {"id": "7", "author": "Jane Doe",
                                  "created": "2026-11-07T10:00:00.000+0000",
                                  "body": "newest"}
    assert (out["total"], out["count"], out["older"], out["next_offset"]) == (7, 2, 5, 2)


async def test_a_server_that_ignored_the_order_is_re_read_from_the_tail(native, monkeypatch):
    calls = _fake_call(monkeypatch, [
        {"total": 7, "comments": [_comment(1, "oldest"), _comment(2, "second")]},
        {"total": 7, "comments": [_comment(6, "older"), _comment(7, "newest")]},
    ])
    out = await jira.list_comments("TASKS-12", limit=2)
    assert calls[1] == ("GET", jira._api("/issue/TASKS-12/comment?startAt=5&maxResults=2"))
    assert [c["body"] for c in out["comments"]] == ["newest", "older"]
    assert out["older"] == 5


async def test_the_last_page_names_no_next_offset(native, monkeypatch):
    _fake_call(monkeypatch, [{"total": 3, "comments": [_comment(1, "first")]}])
    out = await jira.list_comments("TASKS-12", limit=5, offset=2)
    assert out["older"] == 0 and "next_offset" not in out


async def test_a_long_comment_is_cut_and_says_so_and_adf_is_flattened(native, monkeypatch):
    adf = {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Deferred to March."}]}]}
    _fake_call(monkeypatch, [{"total": 2, "comments": [
        _comment(9, "x" * (jira.COMMENT_BODY_CEILING + 40)), _comment(8, adf)]}])
    long, rich = (await jira.list_comments("TASKS-12"))["comments"]
    assert len(long["body"]) == jira.COMMENT_BODY_CEILING and long["body_truncated"]
    assert long["body_characters"] == jira.COMMENT_BODY_CEILING + 40
    assert rich["body"] == "Deferred to March." and "body_truncated" not in rich


async def test_the_page_size_is_capped(native, monkeypatch):
    calls = _fake_call(monkeypatch, [{"total": 0, "comments": []}])
    await jira.list_comments("TASKS-12", limit=500)
    assert f"maxResults={jira.COMMENTS_PAGE_MAX}" in calls[0][1]


async def test_the_issue_read_carries_a_pointer_and_never_the_text(monkeypatch):
    async def _issue(key):
        return {"ok": True, "issue": {"issue_key": key, "summary": "Alignment"}}

    async def _page(key, limit=5, offset=0):
        assert limit == 1
        return {"total": 4, "comments": [
            {"id": "9", "author": "Jane Doe", "created": "2026-11-19T10:00:00.000+0000",
             "body": "Deferred until the spring, the operator's call."}]}

    monkeypatch.setattr(tools.jira, "get_issue", _issue)
    monkeypatch.setattr(tools.jira, "list_comments", _page)
    out = await tools.jira_get_issue(None, "TASKS-27")
    assert json.loads(out)["issue"]["comments"] == {
        "count": 4,
        "latest": {"created": "2026-11-19T10:00:00.000+0000", "author": "Jane Doe"},
        "read_with": "jira_get_comments",
    }
    assert "Deferred until" not in out


async def test_an_issue_with_no_comments_says_zero(monkeypatch):
    async def _issue(key):
        return {"ok": True, "issue": {"issue_key": key}}

    async def _page(key, limit=5, offset=0):
        return {"total": 0, "comments": []}

    monkeypatch.setattr(tools.jira, "get_issue", _issue)
    monkeypatch.setattr(tools.jira, "list_comments", _page)
    out = json.loads(await tools.jira_get_issue(None, "TASKS-27"))
    assert out["issue"]["comments"] == {"count": 0}


async def test_the_comments_tool_pages(monkeypatch):
    seen = {}

    async def _page(key, limit=5, offset=0):
        seen.update(key=key, limit=limit, offset=offset)
        return {"ok": True, "total": 9, "comments": [], "older": 0}

    monkeypatch.setattr(tools.jira, "list_comments", _page)
    out = await tools.jira_get_comments(None, "TASKS-27", limit=3, offset=5)
    assert seen == {"key": "TASKS-27", "limit": 3, "offset": 5}
    assert json.loads(out)["total"] == 9


def test_the_comments_read_is_registered_everywhere_a_read_is():
    assert dependencies.TOOL_DEPENDENCY["jira_get_comments"] == ("jira",)
    assert "jira_get_comments" in packs.PACKS["jira-read"].tool_names
    row = next(c for c in capabilities.REGISTRY if c.name == "jira.get_comments")
    assert row.kind == "read" and row.default_gate == "none"


# --- 2. the comment-only pack ---------------------------------------------------


def test_the_comment_pack_writes_a_comment_and_nothing_else():
    pack = packs.PACKS["jira-comment-propose"]
    assert [c.name for c in pack.capabilities] == ["jira.add_comment"]
    assert pack.tool_names == ("propose_action",)
    # ONE object in both packs, so an agent holding both reads it once.
    in_tracker = next(c for c in packs.PACKS["jira-propose"].capabilities
                      if c.name == "jira.add_comment")
    assert in_tracker is pack.capabilities[0]
    section = packs.charter_section(("jira-comment-propose", "jira-propose"))
    assert section.count("Never a comment that only restates a field") == 1
    assert "Never a comment that only restates a field" in pack.capabilities[0].notes


def test_the_assistant_template_holds_it_and_not_the_whole_tracker():
    assert "jira-comment-propose" in EA_TEMPLATE.default_packs
    assert "jira-propose" not in EA_TEMPLATE.default_packs


def test_the_comment_pack_is_not_seeded_in_the_schema():
    # The calendar-propose precedent: a database that already hired the
    # assistant gets the grant from the operator in the cockpit.
    assert "'jira-comment-propose'" not in SCHEMA


# --- 3. session-close reflection ------------------------------------------------


def test_the_distill_prompt_keeps_work_out_of_the_lessons():
    prompt = reflection._distill_prompt("charter", "transcript")
    for phrase in ("A LESSON IS A FACT THAT OUTLIVES THE WORK",
                   "a decision ABOUT work",
                   "needs an issue key to make sense",
                   "already recorded, proposed or handed to a teammate",
                   "OUTCOME of work that has finished"):
        assert phrase in prompt, phrase
    # The old shape asked for exactly the thing that made an episode a ticket.
    assert "naming the issue keys" not in prompt
    assert prompt.index("A LESSON IS A FACT") < prompt.index("For each lesson")


async def test_a_concluded_lane_distills_no_episode(monkeypatch):
    seen = {}
    src = inspect.getsource(reflection._reflect)
    assert '"graph-propose" in granted and not handed_back' in src

    async def _reflect(session_id, agent_id, model, handed_back=False):
        seen[session_id] = handed_back

    monkeypatch.setattr(reflection, "_reflect", _reflect)
    await reflection.reflect_on_close("sess_lane", "ea", reason="concluded")
    await reflection.reflect_on_close("sess_task", "ea", reason="done")
    await reflection.reflect_on_close("sess_old_caller", "ea")
    assert seen == {"sess_lane": True, "sess_task": False, "sess_old_caller": False}


def test_the_close_seam_tells_reflection_why_the_session_closed():
    assert "reflect_on_close(session_id, agent_id, reason=reason)" in inspect.getsource(
        converse.close_session)


# --- 4. the assistant's charter and the review framings ---------------------------


def test_the_charter_routes_work_to_the_tracker_and_facts_to_the_graph():
    charter = ea_profile.CHARTER
    for phrase in ("WHAT TO RECORD, AND WHERE",
                   "has ONE home",
                   "is a comment on that issue",
                   # v2.107.1: a deferral moves the date AND is commented; the
                   # first quarterly review deferred four issues and moved none.
                   "A DEFERRAL, A SLIP OR A NEW DATE IS BOTH",
                   "its due date CHANGES, in the planning-fields proposal",
                   "never a substitute for it",
                   "ask for the date; never pick one",
                   "NONE of these is ever a graph.add_episode",
                   "needs an issue key to make sense",
                   "WHEN WORK FINISHES, ASK WHAT IT LEFT",
                   "the date the old thing ended",
                   "propose nothing then",
                   "COMMENTS ARE READ ON REQUEST",
                   "with TWO exceptions"):
        assert phrase in charter, phrase
    # What sent decisions about issues to the graph.
    for gone in ("work-log", "a priority call, a deadline, who owns what",
                 "should still know next week"):
        assert gone not in charter, gone


def test_the_quarterly_review_no_longer_asks_for_an_episode_of_the_decisions():
    src = re.sub(r'"\s*\n\s*"', "", inspect.getsource(actions))
    assert "next quarter as one graph.add_episode" not in src
    assert "A decision about work is never a graph.add_episode" in src
    assert "ONE `jira.add_comment` proposal with one action per issue" in src


def test_the_evening_review_asks_what_finished_work_left():
    src = re.sub(r'"\s*\n\s*"', "", inspect.getsource(actions))
    assert "whether finishing it left a lasting fact about the operator's world" in src
    assert "most finished work leaves none" in src


# --- 5. the nightly sweep ----------------------------------------------------------


def test_the_sweep_reports_what_it_finds_and_never_comments_it():
    m = re.search(r"\('jira-hygiene'.*?\"instructions\": \"(.*?)\"\}',", SCHEMA, re.S)
    text = m.group(1).replace("''", "'")
    assert "Never propose a comment that only records what the sweep found" in text
    assert "REPORT everything else in your closing text" in text
    assert "Propose fixes (comments" not in text
