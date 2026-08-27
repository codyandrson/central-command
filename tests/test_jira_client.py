"""Native Jira client tests (D23): validation stops model-controlled strings
before the provider, envelopes match the lib-jira contract callers already
depend on, transition names resolve against availability, and the façade
fallback routes until credentials exist. Transport is faked — no live Jira.
"""

from __future__ import annotations

import json

import pytest

from central_command.config import settings
from central_command.integrations import jira, n8n_facade


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text or json.dumps(self._body)

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def native(monkeypatch):
    monkeypatch.setattr(settings, "jira_email", "test@example.com")
    monkeypatch.setattr(settings, "jira_api_token", "tok")


def _fake_call(monkeypatch, responses):
    calls = []

    async def fake(method, path, json_body=None):
        calls.append((method, path, json_body))
        return responses.pop(0)

    monkeypatch.setattr(jira, "_call", fake)
    return calls


# --- validation ---------------------------------------------------------------


async def test_bad_issue_key_never_reaches_the_provider(monkeypatch):
    calls = _fake_call(monkeypatch, [])
    with pytest.raises(jira.JiraError, match="bad issue_key"):
        await jira.get_issue("robert'); DROP")
    assert calls == []


async def test_bad_date_and_link_type_are_rejected(monkeypatch):
    calls = _fake_call(monkeypatch, [])
    with pytest.raises(jira.JiraError, match="not a real calendar date"):
        await jira.set_due_date("TASKS-12", "2026-02-31")
    with pytest.raises(jira.JiraError, match="bad link_type"):
        await jira.link_issues("TASKS-12", "TASKS-13", "Cloners")
    with pytest.raises(jira.JiraError, match="same issue"):
        await jira.link_issues("TASKS-12", "TASKS-12", "Blocks")
    assert calls == []


# --- envelopes ----------------------------------------------------------------


async def test_get_issue_normalizes_the_envelope_and_flattens_adf(monkeypatch):
    _fake_call(monkeypatch, [FakeResponse(200, {
        "key": "TASKS-12",
        "fields": {
            "summary": "S", "labels": ["a"], "duedate": "2026-08-28",
            "priority": {"name": "High"}, "status": {"name": "To Do"},
            "issuetype": {"name": "Task"}, "created": "2026-01-01T00:00:00Z",
            "description": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "hello"}]},
            ]},
        },
    })])
    out = await jira.get_issue("TASKS-12")
    issue = out["issue"]
    assert out["ok"] and out["operation"] == "getIssue"
    assert issue["description"] == "hello"
    assert issue["priority"] == "High" and issue["due_date"] == "2026-08-28"
    assert issue["url"].endswith("/browse/TASKS-12")


async def test_transition_resolves_by_name_case_insensitively(monkeypatch):
    calls = _fake_call(monkeypatch, [
        FakeResponse(200, {"transitions": [
            {"id": "21", "name": "In Progress", "to": {"name": "In Progress"}}]}),
        FakeResponse(204, {}),
    ])
    out = await jira.transition_issue("TASKS-12", "in progress")
    assert out["issue"]["transition"] == "In Progress"  # canonical name echoed
    assert calls[1][2] == {"transition": {"id": "21"}}


async def test_unavailable_transition_fails_with_the_available_set(monkeypatch):
    _fake_call(monkeypatch, [FakeResponse(200, {"transitions": [
        {"id": "11", "name": "To Do", "to": {"name": "To Do"}}]})])
    with pytest.raises(jira.JiraError, match="available: To Do"):
        await jira.transition_issue("TASKS-12", "Ship It")


async def test_link_direction_maps_from_to_outward(monkeypatch):
    calls = _fake_call(monkeypatch, [FakeResponse(201, {})])
    await jira.link_issues("TASKS-12", "TASKS-14", "Blocks")
    body = calls[0][2]
    assert body["outwardIssue"] == {"key": "TASKS-12"}
    assert body["inwardIssue"] == {"key": "TASKS-14"}


async def test_provider_errors_classify(monkeypatch):
    _fake_call(monkeypatch, [FakeResponse(404, {}, "no issue")])
    with pytest.raises(jira.JiraError, match="not found for TASKS-99"):
        await jira.get_issue("TASKS-99")


# --- the hygiene fields (2026-07-25, closing the D7 run's declared gap) -------


async def test_get_issue_returns_ownership_staleness_and_dependencies(monkeypatch):
    """The three axes jira-expert declared missing — assignee, updated, links
    (+ parent for epic hygiene) — reach the caller, with link relations phrased
    in the direction they are actually stored."""
    calls = _fake_call(monkeypatch, [FakeResponse(200, {
        "key": "TASKS-12",
        "fields": {
            "summary": "S", "status": {"name": "To Do"},
            "updated": "2026-07-01T09:00:00Z",
            "assignee": {"displayName": "Dana Ruiz"},
            "parent": {"key": "TASKS-1", "fields": {"summary": "Epic"}},
            "issuelinks": [
                {"type": {"name": "Blocks", "inward": "is blocked by",
                          "outward": "blocks"},
                 "outwardIssue": {"key": "TASKS-14", "fields": {
                     "summary": "Renewal", "status": {"name": "Done"}}}},
                {"type": {"name": "Blocks", "inward": "is blocked by",
                          "outward": "blocks"},
                 "inwardIssue": {"key": "TASKS-9", "fields": {
                     "summary": "Upstream", "status": {"name": "To Do"}}}},
            ],
        },
    })])
    issue = (await jira.get_issue("TASKS-12"))["issue"]

    # The projection must actually ASK for the fields, or none of this exists.
    for field in ("assignee", "issuelinks", "updated", "parent"):
        assert field in calls[0][1], f"getIssue does not request {field}"

    assert issue["assignee"] == "Dana Ruiz"
    assert issue["updated"] == "2026-07-01T09:00:00Z"
    assert issue["parent"] == {"issue_key": "TASKS-1", "summary": "Epic"}
    # Direction is not cosmetic: TASKS-12 BLOCKS one issue and IS BLOCKED BY
    # the other. Reading these the wrong way round inverts a dependency.
    assert issue["links"] == [
        {"relation": "blocks", "type": "Blocks", "direction": "outward",
         "issue_key": "TASKS-14", "summary": "Renewal", "status": "Done"},
        {"relation": "is blocked by", "type": "Blocks", "direction": "inward",
         "issue_key": "TASKS-9", "summary": "Upstream", "status": "To Do"},
    ]


async def test_unassigned_is_none_not_a_missing_key(monkeypatch):
    """An unassigned issue is the POINT of an ownership audit: it must read as
    a definite null, never as an absent/unknown field."""
    _fake_call(monkeypatch, [FakeResponse(200, {
        "key": "TASKS-12",
        "fields": {"summary": "S", "assignee": None, "issuelinks": [],
                   "parent": None},
    })])
    issue = (await jira.get_issue("TASKS-12"))["issue"]
    assert issue["assignee"] is None and "assignee" in issue
    assert issue["links"] == [] and issue["parent"] is None
    assert "links_omitted" not in issue


async def test_malformed_links_are_dropped_and_overflow_is_declared(monkeypatch):
    """A link whose other end cannot be named is not actionable, so it is
    dropped; and a pathological link count is reported, never silently cut."""
    links = [
        {"type": {"name": "Relates", "outward": "relates to"},
         "outwardIssue": {"key": f"TASKS-{i}", "fields": {}}}
        for i in range(2, jira.MAX_LINKS + 5)
    ]
    links.append({"type": {"name": "Blocks"}})          # no other end at all
    links.append("not even a dict")                      # provider drift
    _fake_call(monkeypatch, [FakeResponse(200, {
        "key": "TASKS-12", "fields": {"summary": "S", "issuelinks": links},
    })])
    issue = (await jira.get_issue("TASKS-12"))["issue"]
    assert len(issue["links"]) == jira.MAX_LINKS
    assert issue["links_omitted"] == 3   # the 3 valid ones past the cap
    assert all(link["issue_key"] for link in issue["links"])


def _page(keys, token=None):
    body = {"issues": [{"key": k, "fields": {"summary": k}} for k in keys]}
    if token:
        body["nextPageToken"] = token
    else:
        body["isLast"] = True
    return FakeResponse(200, body)


async def test_a_limit_pages_past_100_and_says_when_it_cut(monkeypatch):
    """100 is Jira's per-page maxResults cap, never a result ceiling. This used
    to break after page one whenever a limit was passed — and jira_search_issues
    ALWAYS passes one — so limit=150 silently returned 100 rows with no marker,
    which reads to an agent as "there were only 100"."""
    p1 = [f"TASKS-{i}" for i in range(100)]
    p2 = [f"OPS-{i}" for i in range(100)]
    calls = _fake_call(monkeypatch, [_page(p1, "t1"), _page(p2, "t2")])

    out = await jira.search_issues("order by created DESC", limit=150)

    assert len(calls) == 2, "must page until the requested limit is satisfied"
    assert calls[0][2]["maxResults"] == 100      # page size stays Jira's cap
    assert calls[1][2]["nextPageToken"] == "t1"
    assert out["count"] == 150
    # Rows beyond the limit were cut, and more exist behind t2 — say both.
    assert out["truncated"] is True


async def test_a_satisfied_limit_stops_without_claiming_truncation(monkeypatch):
    calls = _fake_call(monkeypatch, [_page(["TASKS-1", "TASKS-2"])])

    out = await jira.search_issues("project = TASKS", limit=10)

    assert len(calls) == 1 and out["count"] == 2
    assert out["truncated"] is False


async def test_search_rows_carry_the_scalars_and_a_link_count(monkeypatch):
    """Board-wide sweeps get ownership/staleness plus a link COUNT — enough to
    find orphans and hubs without blowing the token budget on full link lists."""
    calls = _fake_call(monkeypatch, [FakeResponse(200, {"isLast": True, "issues": [
        {"key": "TASKS-12", "fields": {
            "summary": "S", "status": {"name": "To Do"},
            "updated": "2026-07-01T09:00:00Z",
            "assignee": {"displayName": "Dana Ruiz"},
            "parent": {"key": "TASKS-1", "fields": {"summary": "Epic"}},
            "issuelinks": [
                {"type": {"name": "Blocks", "outward": "blocks"},
                 "outwardIssue": {"key": "TASKS-14", "fields": {}}},
            ],
        }},
        {"key": "TASKS-13", "fields": {"summary": "Orphan"}},
    ]})])
    out = await jira.search_issues("project = TASKS")

    assert calls[0][2]["fields"] == jira.SEARCH_FIELDS
    first, second = out["issues"]
    assert first["assignee"] == "Dana Ruiz"
    assert first["updated"] == "2026-07-01T09:00:00Z"
    assert first["parent"] == "TASKS-1"
    assert first["link_count"] == 1
    assert "links" not in first, "search rows must stay lean (count only)"
    # The unowned, unlinked, parentless row — what a hygiene sweep is hunting.
    assert second["assignee"] is None and second["link_count"] == 0
    assert second["parent"] is None


async def test_a_fixable_read_error_reaches_the_agent(monkeypatch):
    """From the 2026-07-25 hygiene run: the expert asked for `order by updated
    asc`, Jira answered 400 "Unbounded JQL queries are not allowed here. Please
    add a search restriction" — and the tool said only "unavailable", so it
    could not learn to bound the query and read all 8 issues one at a time
    instead. A fixable error reported as an outage is a self-inflicted gap."""
    from central_command.runtime import tools

    async def _boom(jql, limit=None):
        raise jira.JiraError(
            "searchIssues — provider rejected the request (400): "
            '{"errorMessages":["Unbounded JQL queries are not allowed here. '
            'Please add a search restriction to your query."]}'
        )

    monkeypatch.setattr(tools.jira, "search_issues", _boom)
    out = await tools.jira_search_issues(None, "order by updated asc")

    # The actionable words must survive to the model.
    assert "Unbounded JQL" in out and "search restriction" in out
    assert "retry once" in out          # ...with a route out of the failure
    assert "proceed without it" in out  # ...and permission to give up honestly


async def test_a_read_failure_still_degrades_rather_than_raising(monkeypatch):
    """Best-effort stays best-effort: a down provider degrades the run, never
    wedges it — the reason is added, the swallow is not."""
    from central_command.runtime import tools

    async def _down(issue_key):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(tools.jira, "get_issue", _down)
    out = await tools.jira_get_issue(None, "TASKS-12")
    assert "connection refused" in out and "RuntimeError" in out


def test_tool_results_are_truncated_honestly():
    """A silently cut JSON blob reads as a COMPLETE one — an empty tail looks
    like 'no more links'. The clip must announce itself."""
    from central_command.runtime.tools import _clip

    assert _clip("short", 100) == "short"
    clipped = _clip("x" * 500, 100)
    assert clipped.startswith("x" * 100)
    assert "TRUNCATED" in clipped and "PARTIAL" in clipped


# --- the cutover seam ---------------------------------------------------------


async def test_unconfigured_client_falls_back_to_the_facade(monkeypatch):
    monkeypatch.setattr(settings, "jira_api_token", "")
    hit = []

    async def facade_get_issue(issue_key):
        hit.append(issue_key)
        return {"ok": True, "via": "facade"}

    monkeypatch.setattr(n8n_facade, "get_issue", facade_get_issue)
    out = await jira.get_issue("TASKS-12")
    assert out["via"] == "facade" and hit == ["TASKS-12"]


# --- createIssue (built after the charter-v2 incident) ------------------------


async def test_create_issue_validates_before_the_provider(monkeypatch):
    calls = _fake_call(monkeypatch, [])
    with pytest.raises(jira.JiraError, match="bad project_key"):
        await jira.create_issue("tasks", "New work")
    with pytest.raises(jira.JiraError, match="summary must be"):
        await jira.create_issue("TASKS", "   ")
    with pytest.raises(jira.JiraError, match="bad issue_type"):
        await jira.create_issue("TASKS", "New work", issue_type="Nonsense")
    with pytest.raises(jira.JiraError, match="not a real calendar date"):
        await jira.create_issue("TASKS", "New work", due_date="2026-02-31")
    with pytest.raises(jira.JiraError, match="labels must be"):
        await jira.create_issue("TASKS", "New work", labels="not-a-list")
    assert calls == []


async def test_create_issue_posts_the_right_fields_and_envelope(monkeypatch):
    calls = _fake_call(monkeypatch, [FakeResponse(201, {"key": "TASKS-99"})])
    out = await jira.create_issue(
        "TASKS", "Verify Example Bank trial deposits",
        description="From the account-verification email.",
        due_date="2026-07-30", labels=["email-derived"],
    )
    method, path, body = calls[0]
    assert (method, path) == ("POST", "/rest/api/3/issue")
    f = body["fields"]
    assert f["project"] == {"key": "TASKS"}
    assert f["issuetype"] == {"name": "Task"}
    assert f["duedate"] == "2026-07-30" and f["labels"] == ["email-derived"]
    assert f["description"]["type"] == "doc"  # ADF, not a bare string
    assert out["ok"] is True and out["operation"] == "createIssue"
    assert out["issue"]["issue_key"] == "TASKS-99"


async def test_create_issue_falls_back_to_the_facade_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "jira_email", None)
    seen = {}

    async def fake_facade(project_key, summary, description=None,
                          issue_type="Task", due_date=None, labels=None):
        seen.update(project_key=project_key, summary=summary)
        return {"ok": True}

    monkeypatch.setattr(n8n_facade, "create_issue", fake_facade)
    out = await jira.create_issue("TASKS", "fallback path")
    assert out == {"ok": True} and seen["project_key"] == "TASKS"


# --- parent / hierarchy (widening) --------------------------------------------


async def test_subtask_without_parent_is_refused(monkeypatch):
    calls = _fake_call(monkeypatch, [])
    with pytest.raises(jira.JiraError, match="requires a parent issue key"):
        await jira.create_issue("TASKS", "New subtask", issue_type="Subtask")
    with pytest.raises(jira.JiraError, match="requires a parent issue key"):
        await jira.create_issue("TASKS", "New subtask", issue_type="Sub-task")
    assert calls == []


async def test_bad_parent_key_is_refused(monkeypatch):
    calls = _fake_call(monkeypatch, [])
    with pytest.raises(jira.JiraError, match="bad parent"):
        await jira.create_issue("TASKS", "New work", parent="not-a-key")
    assert calls == []


async def test_epic_is_an_accepted_issue_type(monkeypatch):
    calls = _fake_call(monkeypatch, [FakeResponse(201, {"key": "TASKS-100"})])
    out = await jira.create_issue("TASKS", "New epic", issue_type="Epic")
    assert out["issue"]["issue_type"] == "Epic"
    assert calls[0][2]["fields"]["issuetype"] == {"name": "Epic"}


async def test_parent_passes_through_to_fields_and_the_returned_issue(monkeypatch):
    calls = _fake_call(monkeypatch, [FakeResponse(201, {"key": "TASKS-101"})])
    out = await jira.create_issue(
        "TASKS", "New subtask", issue_type="Subtask", parent="TASKS-1",
    )
    body = calls[0][2]
    assert body["fields"]["parent"] == {"key": "TASKS-1"}
    assert out["issue"]["parent"] == "TASKS-1"


# --- filters --------------------------------------------------------------


async def test_create_filter_validates_before_the_provider(monkeypatch):
    calls = _fake_call(monkeypatch, [])
    with pytest.raises(jira.JiraError, match="name must be"):
        await jira.create_filter("", "project = TASKS")
    with pytest.raises(jira.JiraError, match="jql must be"):
        await jira.create_filter("my filter", "x" * 2001)
    assert calls == []


async def test_create_filter_requires_native_credentials(monkeypatch):
    monkeypatch.setattr(settings, "jira_email", None)
    with pytest.raises(jira.JiraError, match="requires native Jira credentials"):
        await jira.create_filter("my filter", "project = TASKS")


# --- dashboards -------------------------------------------------------------


async def test_create_dashboard_rejects_more_than_ten_gadgets(monkeypatch):
    calls = _fake_call(monkeypatch, [])
    gadgets = [{"uri": f"g{i}"} for i in range(11)]
    with pytest.raises(jira.JiraError, match="max 10"):
        await jira.create_dashboard("my dashboard", gadgets=gadgets)
    assert calls == []


async def test_create_dashboard_rejects_ambiguous_gadget_ref(monkeypatch):
    calls = _fake_call(monkeypatch, [])
    with pytest.raises(jira.JiraError, match="EXACTLY ONE"):
        await jira.create_dashboard("my dashboard", gadgets=[{}])
    with pytest.raises(jira.JiraError, match="EXACTLY ONE"):
        await jira.create_dashboard(
            "my dashboard", gadgets=[{"uri": "a", "module_key": "b"}]
        )
    assert calls == []


async def test_create_dashboard_requires_native_credentials(monkeypatch):
    monkeypatch.setattr(settings, "jira_email", None)
    with pytest.raises(jira.JiraError, match="requires native Jira credentials"):
        await jira.create_dashboard("my dashboard")


# --- filterName resolution (the 2026-08-08 empty-dashboard incident) ----------


async def test_placeholder_filter_id_is_refused_before_anything_is_created(monkeypatch):
    calls = _fake_call(monkeypatch, [])
    gadgets = [{"uri": "g", "config": {"filterId": "filter-<filter_id>"}}]
    with pytest.raises(jira.JiraError, match="not a real filter id"):
        await jira.create_dashboard("my dashboard", gadgets=gadgets)
    assert calls == []


def _gadget_xml(*prefs):
    """A gadget's own XML — the declaration `_gadget_prefs` reads."""
    return FakeResponse(200, text="".join(
        f'<UserPref name="{p}" datatype="hidden" />' for p in prefs
    ))


async def test_filter_name_resolves_to_the_real_id_before_the_dashboard(monkeypatch):
    calls = _fake_call(monkeypatch, [
        # the gadget's declared prefs, then list_filters (the resolution) …
        _gadget_xml("filterId", "num", "isConfigured"),
        FakeResponse(200, {"values": [{"id": "10067", "name": "Example Off-Road Tasks"}]}),
        # … then the dashboard, then the gadget, then its config PUT.
        FakeResponse(200, {"id": "10002", "name": "my dashboard"}),
        FakeResponse(200, {"id": 10013}),
        FakeResponse(201),
    ])
    out = await jira.create_dashboard("my dashboard", gadgets=[
        {"uri": "g", "config": {"filterName": "Example Off-Road Tasks", "num": "10"}},
    ])
    method, path, body = calls[4]
    assert path.endswith("/items/10013/properties/config")
    assert body == {"num": "10", "filterId": "filter-10067", "isConfigured": "true"}
    assert out["dashboard"]["gadgets"][0]["ok"] is True


async def test_unresolvable_filter_name_fails_the_whole_write(monkeypatch):
    calls = _fake_call(monkeypatch, [
        _gadget_xml("filterId"), FakeResponse(200, {"values": []}),
    ])
    with pytest.raises(jira.JiraError, match="matched 0 filters"):
        await jira.create_dashboard("my dashboard", gadgets=[
            {"uri": "g", "config": {"filterName": "no such filter"}},
        ])
    # Only reads happened — the dashboard was never created.
    assert [m for m, _, _ in calls] == ["GET", "GET"]


async def test_two_filter_keys_at_once_is_refused(monkeypatch):
    calls = _fake_call(monkeypatch, [])
    with pytest.raises(jira.JiraError, match="give exactly one"):
        await jira.create_dashboard("my dashboard", gadgets=[
            {"uri": "g", "config": {"filterId": "filter-1", "filterName": "x"}},
        ])
    assert calls == []


# --- the 2026-08-10 "hasn't been configured yet" incident ---------------------


async def test_gadget_is_marked_configured_and_bound_under_its_own_pref(monkeypatch):
    """The four gadgets on a live dashboard ALL rendered "hasn't been configured
    yet" with correct-looking configs. Two causes, both silent: nothing set
    `isConfigured` (whose declared default is false, and which IS that
    placeholder), and only Filter Results declares `filterId` — Pie Chart,
    Created vs Resolved and Stats bind under `projectOrFilterId` and silently
    ignored the key they were sent."""
    calls = _fake_call(monkeypatch, [
        _gadget_xml("projectOrFilterId", "statType", "isConfigured"),
        FakeResponse(200, {"id": "10002", "name": "my dashboard"}),
        FakeResponse(200, {"id": 10013}),
        FakeResponse(201),
    ])
    await jira.create_dashboard("my dashboard", gadgets=[
        # the caller names filterId — the gadget only ever reads projectOrFilterId
        {"uri": "piechart.xml", "config": {"filterId": "filter-10100", "statType": "statuses"}},
    ])
    assert calls[3][2] == {
        "statType": "statuses",
        "projectOrFilterId": "filter-10100",
        "isConfigured": "true",
    }


async def test_a_pref_the_gadget_does_not_declare_fails_loudly(monkeypatch):
    """`num` was sent to Created vs Resolved, whose row count is `daysprevious`.
    Jira answered 200 and dropped it. Silence is the bug — refuse instead."""
    calls = _fake_call(monkeypatch, [
        _gadget_xml("projectOrFilterId", "daysprevious", "isConfigured"),
    ])
    with pytest.raises(jira.JiraError, match=r"does not declare \['num'\]"):
        await jira.create_dashboard("my dashboard", gadgets=[
            {"uri": "cvr.xml", "config": {"filterId": "filter-1", "num": "30"}},
        ])
    assert [m for m, _, _ in calls] == ["GET"]  # nothing was created


async def test_an_invented_gadget_uri_fails_before_the_dashboard_exists(monkeypatch):
    """An agent invented `/rest/gadgets/1.0/dashboards/10000/gadget/pie-chart?…`
    for all four gadgets. Jira created the dashboard, then rejected each gadget
    separately with 400 "not present in the directory" — and because a gadget's
    failure deliberately does not undo the dashboard, an EMPTY dashboard stood
    and the proposal read EXECUTED. A uri whose declaration does not resolve is
    not a gadget: refuse the whole write."""
    calls = _fake_call(monkeypatch, [FakeResponse(404, text="nope")])
    with pytest.raises(jira.JiraError, match="does not resolve to a gadget"):
        await jira.create_dashboard("my dashboard", gadgets=[
            {"uri": "/rest/gadgets/1.0/dashboards/10000/gadget/pie-chart?title=X",
             "title": "Priority Breakdown"},
        ])
    assert [m for m, _, _ in calls] == ["GET"]  # nothing was created


async def test_unreadable_gadget_declaration_leaves_the_config_alone(monkeypatch):
    """A module_key gadget has no XML to read. Unknown prefs must mean "do not
    guess" — never "reject the caller's config" and never a fabricated key."""
    calls = _fake_call(monkeypatch, [
        FakeResponse(200, {"id": "10002", "name": "my dashboard"}),
        FakeResponse(200, {"id": 10013}),
        FakeResponse(201),
    ])
    await jira.create_dashboard("my dashboard", gadgets=[
        {"module_key": "com.example:gadget", "config": {"filterId": "filter-7", "whatever": "1"}},
    ])
    assert calls[2][2] == {"whatever": "1", "filterId": "filter-7"}
