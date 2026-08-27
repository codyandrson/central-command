"""Custom fields (2026-08-11).

The operator added four cost/benefit fields, populated them, and jira-expert
could not see them at all: `FIELDS`/`SEARCH_FIELDS` are an allowlist and Jira
returns exactly what you ask for. Worse, when pushed it claimed a `fields`
parameter it had just watched itself not have. So the fixes under test are:
reads ASK for the declared custom fields and surface them BY NAME, and there is
a write that takes a name and refuses anything it cannot resolve — an unknown
field key is a 200 from Jira that binds nothing, the same silent-success failure
as an undeclared gadget pref.

Transport is faked — no live Jira.
"""

from __future__ import annotations

import json

import pytest

from central_command.config import settings
from central_command.integrations import jira

# The real shapes, copied from the live instance: a textarea's `schema.type` is
# "string" and its VALUE is an ADF document — which is why the write shape comes
# from `schema.custom`, never from `schema.type`.
FIELD_DEFS = [
    {"id": "customfield_10075", "name": "Accomplishment Benefit", "custom": True,
     "schema": {"type": "string",
                "custom": "com.atlassian.jira.plugin.system.customfieldtypes:textarea"}},
    {"id": "customfield_10015", "name": "Start date", "custom": True,
     "schema": {"type": "date",
                "custom": "com.atlassian.jira.plugin.system.customfieldtypes:datepicker"}},
    {"id": "customfield_10019", "name": "Rank", "custom": True,
     "schema": {"type": "any", "custom": "com.pyxis.greenhopper.jira:gh-lexo-rank"}},
    {"id": "summary", "name": "Summary", "custom": False, "schema": {"type": "string"}},
]

ADF_TEST = {"type": "doc", "version": 1,
            "content": [{"type": "paragraph",
                         "content": [{"type": "text", "text": "Saves 3h/week"}]}]}


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
    # Undo conftest's empty seed: these tests are ABOUT discovery.
    monkeypatch.setattr(jira, "_custom_fields_cache", None)


def _fake_call(monkeypatch, responses):
    calls = []

    async def fake(method, path, json_body=None):
        calls.append((method, path, json_body))
        return responses.pop(0)

    monkeypatch.setattr(jira, "_call", fake)
    return calls


def _seed(monkeypatch):
    """Skip the discovery round-trip when the test is about something else."""
    monkeypatch.setattr(jira, "_custom_fields_cache", (float("inf"), [
        {"id": "customfield_10075", "name": "Accomplishment Benefit",
         "type": "textarea", "writable": True},
        {"id": "customfield_10015", "name": "Start date",
         "type": "datepicker", "writable": True},
        {"id": "customfield_10019", "name": "Rank",
         "type": "gh-lexo-rank", "writable": False},
    ]))


# --- discovery ----------------------------------------------------------------


async def test_only_custom_fields_are_declared_and_writability_is_derived(monkeypatch):
    _fake_call(monkeypatch, [FakeResponse(body=FIELD_DEFS)])
    out = await jira.list_fields()
    by_name = {f["name"]: f for f in out["fields"]}
    assert "Summary" not in by_name            # a system field is not a custom field
    assert by_name["Accomplishment Benefit"]["writable"] is True
    # Jira/plugin-managed: refused rather than mangled.
    assert by_name["Rank"]["writable"] is False


async def test_discovery_is_cached_but_expires(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(jira.time, "monotonic", lambda: clock[0])
    calls = _fake_call(monkeypatch, [FakeResponse(body=FIELD_DEFS)] * 2)
    await jira.list_fields()
    await jira.list_fields()
    assert len(calls) == 1                     # cached — every issue read consults it
    clock[0] += jira.CUSTOM_FIELDS_TTL + 1
    await jira.list_fields()
    # A field the operator adds in the UI must appear WITHOUT a cc-uvicorn
    # restart, or this whole change reproduces the bug it fixes.
    assert len(calls) == 2


# --- reads --------------------------------------------------------------------


async def test_get_issue_asks_for_the_custom_fields_and_names_them(monkeypatch):
    _seed(monkeypatch)
    calls = _fake_call(monkeypatch, [FakeResponse(body={
        "key": "TASKS-43",
        "fields": {"summary": "Test 1", "customfield_10075": ADF_TEST,
                   "customfield_10015": None, "customfield_10019": "0|i0037n:"},
    })])
    out = await jira.get_issue("TASKS-43")
    # The bug was here: the request never asked for them.
    assert "customfield_10075" in calls[0][1]
    cf = out["issue"]["custom_fields"]
    assert cf["Accomplishment Benefit"] == "Saves 3h/week"    # ADF → text
    # Declared but unset reads as null on a single issue — "the field exists and
    # is empty" is the answer to a real question, and omitting it is
    # indistinguishable from the field not existing.
    assert cf["Start date"] is None


async def test_search_rows_carry_only_set_writable_fields(monkeypatch):
    _seed(monkeypatch)
    calls = _fake_call(monkeypatch, [FakeResponse(body={"isLast": True, "issues": [
        {"key": "TASKS-43", "fields": {"customfield_10075": ADF_TEST,
                                       "customfield_10015": None,
                                       "customfield_10019": "0|i0037n:"}},
        {"key": "TASKS-42", "fields": {"customfield_10075": None,
                                       "customfield_10015": None}},
    ]})])
    rows = (await jira.search_issues("project = TASKS"))["issues"]
    # Rank is set on every issue in the instance and is pure lexorank noise on a
    # sweep — it is not even asked for.
    assert "customfield_10019" not in calls[0][2]["fields"]
    assert rows[0]["custom_fields"] == {"Accomplishment Benefit": "Saves 3h/week"}
    assert "custom_fields" not in rows[1]      # a sweep pays nothing for empties


async def test_a_failed_discovery_is_reported_not_swallowed(monkeypatch):
    async def boom(method, path, json_body=None):
        if path == "/rest/api/3/field":
            raise RuntimeError("field list down")
        return FakeResponse(body={"key": "TASKS-43", "fields": {"summary": "Test 1"}})

    monkeypatch.setattr(jira, "_call", boom)
    out = await jira.get_issue("TASKS-43")
    # Degrade, never wedge — but SAY so. A silent omission is the original bug.
    assert out["ok"] is True
    assert "field list down" in out["issue"]["custom_fields_error"]


async def test_a_long_value_is_clipped_and_says_so(monkeypatch):
    _seed(monkeypatch)
    long = {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "x" * 2000}]}]}
    _fake_call(monkeypatch, [FakeResponse(body={
        "key": "TASKS-43", "fields": {"customfield_10075": long}})])
    value = (await jira.get_issue("TASKS-43"))["issue"]["custom_fields"]["Accomplishment Benefit"]
    assert "clipped" in value and len(value) < 700


# --- writes -------------------------------------------------------------------


async def test_set_fields_resolves_by_name_and_wraps_rich_text_in_adf(monkeypatch):
    _seed(monkeypatch)
    calls = _fake_call(monkeypatch, [FakeResponse(body={})])
    await jira.set_fields("TASKS-43", {"accomplishment benefit": "Saves 3h/week",
                                       "Start date": "2026-09-01"})
    sent = calls[0][2]["fields"]
    assert sent["customfield_10075"] == jira._adf("Saves 3h/week")
    assert sent["customfield_10015"] == "2026-09-01"


async def test_an_unknown_field_name_never_reaches_the_provider(monkeypatch):
    _seed(monkeypatch)
    calls = _fake_call(monkeypatch, [])
    # Jira answers an unknown field key with 200 and binds NOTHING, so this has
    # to be refused here — and the refusal names the real fields, because the
    # agent's next move should be reading, not asking the operator for an id.
    with pytest.raises(jira.JiraError, match="Accomplishment Benefit"):
        await jira.set_fields("TASKS-43", {"Cost of Delay": "high"})
    assert calls == []


async def test_a_plugin_managed_field_is_refused(monkeypatch):
    _seed(monkeypatch)
    calls = _fake_call(monkeypatch, [])
    with pytest.raises(jira.JiraError, match="cannot write safely"):
        await jira.set_fields("TASKS-43", {"Rank": "0|i0037n:"})
    assert calls == []


async def test_a_value_of_the_wrong_shape_is_refused(monkeypatch):
    _seed(monkeypatch)
    calls = _fake_call(monkeypatch, [])
    with pytest.raises(jira.JiraError, match="must be a string"):
        await jira.set_fields("TASKS-43", {"Accomplishment Benefit": 42})
    with pytest.raises(jira.JiraError, match="not a real calendar date"):
        await jira.set_fields("TASKS-43", {"Start date": "2026-02-31"})
    assert calls == []


async def test_null_clears_a_field(monkeypatch):
    _seed(monkeypatch)
    calls = _fake_call(monkeypatch, [FakeResponse(body={})])
    await jira.set_fields("TASKS-43", {"Accomplishment Benefit": None})
    assert calls[0][2] == {"fields": {"customfield_10075": None}}


# --- create_issue carries custom fields (2026-08-18) --------------------------
# The executor passes no values between a proposal's actions, so a follow-up
# set_fields can never name the key a create produces — the fields must ride
# the create itself.


async def test_create_issue_resolves_and_merges_custom_fields(monkeypatch):
    _seed(monkeypatch)
    calls = _fake_call(monkeypatch, [FakeResponse(201, {"key": "TASKS-100"})])
    out = await jira.create_issue(
        "TASKS", "New work",
        custom_fields={"Accomplishment Benefit": "Saves 3h/week"},
    )
    body = calls[0][2]["fields"]
    assert body["customfield_10075"]["type"] == "doc"     # textarea → ADF
    assert out["issue"]["custom_fields"] == {"Accomplishment Benefit": "Saves 3h/week"}


async def test_create_issue_refuses_an_unknown_custom_field_before_the_post(monkeypatch):
    _seed(monkeypatch)
    calls = _fake_call(monkeypatch, [])
    with pytest.raises(jira.JiraError, match="no custom field named"):
        await jira.create_issue(
            "TASKS", "New work", custom_fields={"Benefit": "typo'd name"}
        )
    assert calls == []
