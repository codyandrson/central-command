"""Step 4b of the EA planning design (DL-221, FR-54; the design record's D3 write
side and D8): enrichment of the planning fields.

- `jira.set_planning_fields`: its shape in both tiers, the Executor resolving
  the four custom fields by the configured NAMES, the estimate and the select
  written in the right shape on both API flavours, an undeclared field refused
  by name with nothing written;
- `jira.create_issue` carrying the planning fields and an estimate;
- `jira_planning_gaps`: the cap, the order, the missing lists agreeing with
  `ea_backlog.rank()`, undeclared fields reported, an outage parking the run;
- the capability in both of its packs, and the registry row;
- the seeded `jira-hygiene` instructions (and the copy operators paste);
- the evening framing and the built-in charter.

NO NETWORK: the Jira transport (`jira._call`) or the client's read functions are
faked, and every approval that executes runs under `tests/fake_world.py`.
"""

from __future__ import annotations

import json
import pathlib
import re
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic_ai import CallDeferred, ModelRetry

from central_command.config import settings
from central_command.contract import Action, Proposal, Reversibility, validate_action_args
from central_command.contract.dependencies import CAPABILITY_DEPENDENCY, TOOL_DEPENDENCY
from central_command.db import repo
from central_command.gateway import capabilities, executor
from central_command.heartbeat import actions as hb_actions
from central_command.integrations import jira
from central_command.reports import ea_backlog, ea_plan
from central_command.runtime import ea_profile, packs, tools
from central_command.runtime.deps import TriageDeps
from central_command.runtime.templates import EA_TEMPLATE
from tests.conftest import needs_pg
from tests.fake_world import fake_world

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "central_command" / "db" / "schema.sql").read_text(encoding="utf-8")
TODAY = date(2026, 11, 18)

# The instance's custom fields as the client caches them ({id, name, type,
# writable}); the planning names are the settings' defaults.
DECLARED = [
    {"id": "customfield_201", "name": "Stakes", "type": "textarea", "writable": True},
    {"id": "customfield_202", "name": "Value", "type": "float", "writable": True},
    {"id": "customfield_203", "name": "Lateness loss", "type": "float", "writable": True},
    {"id": "customfield_204", "name": "Deadline type", "type": "select", "writable": True},
]


class _Resp:
    def __init__(self, body=None, status_code=200):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


@pytest.fixture()
def native(monkeypatch):
    """A configured native client whose transport is a recorder."""
    monkeypatch.setattr(settings, "jira_base_url", "https://jira.example.com")
    monkeypatch.setattr(settings, "jira_email", "bot@example.com")
    monkeypatch.setattr(settings, "jira_api_token", "tok")
    monkeypatch.setattr(settings, "jira_auth_mode", "basic")
    monkeypatch.setattr(settings, "plan_field_stakes", "Stakes")
    monkeypatch.setattr(settings, "plan_field_value", "Value")
    monkeypatch.setattr(settings, "plan_field_lateness", "Lateness loss")
    monkeypatch.setattr(settings, "plan_field_deadline_type", "Deadline type")

    def answer(bodies, declared=DECLARED):
        monkeypatch.setattr(jira, "_custom_fields_cache", (float("inf"), list(declared)))
        calls = []

        async def fake(method, path, json_body=None):
            calls.append((method, path, json_body))
            return bodies.pop(0)

        monkeypatch.setattr(jira, "_call", fake)
        return calls

    return answer


def _set(**args) -> Action:
    return Action(capability="jira.set_planning_fields", arguments={"issue_key": "DOE-7", **args},
                  target_ref={"system": "jira", "id": "DOE-7"},
                  reversibility=Reversibility.reversible)


def _proposal(*actions: Action) -> Proposal:
    return Proposal(intent="Score DOE-7", expected_effect="DOE-7 is ranked", evidence=[],
                    confidence={"level": "high", "rationale": "The operator stated the stakes."},
                    actions=list(actions))


# --- 1. the shape, in both tiers ------------------------------------------------------


@pytest.mark.parametrize("args, problem", [
    ({"stakes": "Unblocks the audit."}, None),
    ({"value": 0}, None),                                   # zero is a value
    ({"lateness_loss": 5.5}, None),
    ({"deadline_type": "hard"}, None),
    ({"estimate_hours": 0.5}, None),
    ({"due_date": "2026-12-01"}, None),
    ({"stakes": "s", "value": 3, "lateness_loss": 8, "deadline_type": "soft",
      "estimate_hours": 4, "due_date": "2026-12-01"}, None),
    ({}, "name at least one of"),
    ({"value": None}, "never clears"),
    ({"stakes": ""}, "stakes must be non-empty text"),
    ({"stakes": "x" * 2001}, "at most 2000"),
    ({"value": -1}, "value must be a number >= 0"),
    ({"value": "5"}, "value must be a number >= 0"),
    ({"value": True}, "value must be a number >= 0"),
    ({"lateness_loss": float("nan")}, "lateness_loss must be a number >= 0"),
    ({"deadline_type": "Hard"}, "is not one of hard, soft"),
    ({"estimate_hours": 0}, "estimate_hours must be"),
    ({"estimate_hours": 5000}, "estimate_hours must be"),
    ({"due_date": "12/01/2026"}, "due_date must be YYYY-MM-DD"),
    ({"value": 3, "priority": "High"}, "unknown argument(s) priority"),
    ({"value": 3, "fields": {"Value": 3}}, "unknown argument(s) fields"),
])
def test_the_planning_fields_shape(args, problem):
    problems = validate_action_args("jira.set_planning_fields", {"issue_key": "DOE-7", **args})
    if problem is None:
        assert problems == []
    else:
        assert any(problem in p for p in problems), problems


def test_the_issue_key_is_required():
    assert any("issue_key" in p for p in validate_action_args("jira.set_planning_fields",
                                                              {"value": 3}))


@pytest.mark.parametrize("estimate, ok", [(None, True), (1.5, True), (0, False), (-2, False),
                                          ("2h", False), (True, False)])
def test_create_issue_checks_its_estimate(estimate, ok):
    args = {"project_key": "DOE", "summary": "s", "issue_type": "Task"}
    if estimate is not None:
        args["estimate_hours"] = estimate
    assert (validate_action_args("jira.create_issue", args) == []) is ok


async def test_the_runtime_hands_a_bad_draft_back_to_the_model():
    with pytest.raises(ModelRetry, match="is not one of hard, soft"):
        await tools.propose_action(None, _proposal(_set(deadline_type="urgent")))
    with pytest.raises(ModelRetry, match="unknown argument"):
        await tools.propose_action(None, _proposal(_set(value=3, summary="new title")))
    with pytest.raises(CallDeferred):
        await tools.propose_action(None, _proposal(_set(value=3, estimate_hours=2)))


async def test_the_executor_refuses_a_bad_shape_before_anything_runs(monkeypatch):
    log = fake_world(monkeypatch)
    with pytest.raises(executor.ExecutionFailed, match="value must be a number") as exc:
        await executor.execute([_set(stakes="fine"), _set(value=-3)], "human:lee", [],
                               proposal_id="prop_pf_shape")
    assert exc.value.completed == [] and log == [], "nothing ran"


# --- 2. the Executor: names, shapes, flavours ----------------------------------------


_HANDLER = executor.HANDLERS["jira.set_planning_fields"]


@pytest.mark.parametrize("flavor", ["cloud", "server"])
async def test_every_field_together_is_one_edit_in_the_flavours_shapes(
        monkeypatch, native, flavor):
    monkeypatch.setattr(settings, "jira_api_flavor", flavor)
    calls = native([_Resp(status_code=204)])
    out = await _HANDLER({"issue_key": "DOE-7", "stakes": "Unblocks the audit.", "value": 5,
                          "lateness_loss": 8, "deadline_type": "hard",
                          "estimate_hours": 1.5, "due_date": "2026-12-01"},
                         "human:lee", "ea")
    assert len(calls) == 1, "one tracker call"
    method, path, body = calls[0]
    assert method == "PUT"
    assert path == ("/rest/api/2/issue/DOE-7" if flavor == "server"
                    else "/rest/api/3/issue/DOE-7")
    fields = body["fields"]
    # A textarea is wiki-markup text on Data Center and an ADF document on Cloud.
    assert fields["customfield_201"] == ("Unblocks the audit." if flavor == "server"
                                         else jira._adf("Unblocks the audit."))
    assert fields["customfield_202"] == 5 and fields["customfield_203"] == 8
    # A single select takes its option as {"value": ...} on both flavours.
    assert fields["customfield_204"] == {"value": "hard"}
    assert fields["duedate"] == "2026-12-01"
    # The original estimate is a timetracking EDIT, in whole minutes, on both.
    assert body["update"] == {"timetracking": [{"edit": {"originalEstimate": "90m"}}]}
    assert not set(body["fields"]) & set(body["update"]), "never a field in both"
    for word in ("stakes", "value", "lateness_loss", "deadline_type", "estimate_hours",
                 "due_date"):
        assert word in out


@pytest.mark.parametrize("arg, value, check", [
    ("stakes", "Keeps the grant.", lambda b: b["fields"] == {"customfield_201": jira._adf("Keeps the grant.")}),
    ("value", 3, lambda b: b["fields"] == {"customfield_202": 3}),
    ("lateness_loss", 2, lambda b: b["fields"] == {"customfield_203": 2}),
    ("deadline_type", "soft", lambda b: b["fields"] == {"customfield_204": {"value": "soft"}}),
    ("due_date", "2026-12-24", lambda b: b["fields"] == {"duedate": "2026-12-24"}),
    ("estimate_hours", 2, lambda b: "fields" not in b
     and b["update"]["timetracking"][0]["edit"]["originalEstimate"] == "120m"),
])
async def test_each_field_alone_sends_only_that_field(monkeypatch, native, arg, value, check):
    monkeypatch.setattr(settings, "jira_api_flavor", "cloud")
    calls = native([_Resp(status_code=204)])
    await _HANDLER({"issue_key": "DOE-7", arg: value}, "human:lee", "ea")
    (call,) = calls
    assert check(call[2]), call[2]
    if arg != "estimate_hours":
        assert "update" not in call[2]


async def test_the_custom_fields_are_the_configured_names_not_the_defaults(monkeypatch, native):
    monkeypatch.setattr(settings, "jira_api_flavor", "cloud")
    monkeypatch.setattr(settings, "plan_field_value", "Worth")
    declared = DECLARED + [{"id": "customfield_299", "name": "Worth", "type": "float",
                            "writable": True}]
    calls = native([_Resp(status_code=204)], declared=declared)
    await _HANDLER({"issue_key": "DOE-7", "value": 8}, "human:lee", "ea")
    assert calls[0][2]["fields"] == {"customfield_299": 8}


async def test_an_undeclared_planning_field_is_refused_by_name_and_nothing_is_written(
        monkeypatch, native):
    monkeypatch.setattr(settings, "jira_api_flavor", "cloud")
    no_deadline = [f for f in DECLARED if f["name"] != "Deadline type"]
    calls = native([], declared=no_deadline)
    with pytest.raises(jira.JiraError) as exc:
        await _HANDLER({"issue_key": "DOE-7", "value": 3, "deadline_type": "hard"},
                       "human:lee", "ea")
    msg = str(exc.value)
    assert "'Deadline type'" in msg and "plan_field_deadline_type" in msg
    assert "'Value'" not in msg, "only the missing field is named"
    assert "nothing was written" in msg
    assert calls == [], "no tracker call"
    # A refusal, never an outage: it lands FAILED instead of parking.
    from central_command.contract.failures import is_down

    assert not is_down(exc.value)


async def test_the_native_fields_need_no_custom_field_declared(monkeypatch, native):
    monkeypatch.setattr(settings, "jira_api_flavor", "server")
    calls = native([_Resp(status_code=204)], declared=[])
    await _HANDLER({"issue_key": "DOE-7", "estimate_hours": 3, "due_date": "2026-12-01"},
                   "human:lee", "ea")
    body = calls[0][2]
    assert body["fields"] == {"duedate": "2026-12-01"}
    assert body["update"]["timetracking"][0]["edit"]["originalEstimate"] == "180m"


async def test_a_tracker_refusal_of_the_edit_is_loud(monkeypatch, native):
    monkeypatch.setattr(settings, "jira_api_flavor", "server")
    native([_Resp({"errors": {"customfield_204": "not on the appropriate screen"}},
                  status_code=400)])
    with pytest.raises(jira.JiraError, match="appropriate screen"):
        await _HANDLER({"issue_key": "DOE-7", "deadline_type": "soft"}, "human:lee", "ea")


@needs_pg
async def test_one_approval_runs_one_action_per_issue(monkeypatch, native):
    monkeypatch.setattr(settings, "jira_api_flavor", "cloud")
    fake_world(monkeypatch, real=("jira.set_planning_fields",))
    calls = native([_Resp(status_code=204), _Resp(status_code=204)])
    other = Action(capability="jira.set_planning_fields",
                   arguments={"issue_key": "DOE-8", "value": 2, "lateness_loss": 1},
                   target_ref={"system": "jira", "id": "DOE-8"},
                   reversibility=Reversibility.reversible)
    out = await executor.execute([_set(value=5, estimate_hours=1), other], "human:lee", [],
                                 proposal_id="prop_pf_two")
    assert [c[1] for c in calls] == ["/rest/api/3/issue/DOE-7", "/rest/api/3/issue/DOE-8"]
    assert "DOE-7 planning fields set" in out.result_text
    assert "DOE-8 planning fields set" in out.result_text


# --- 3. jira.create_issue carries the planning fields and an estimate ----------------


@pytest.mark.parametrize("flavor", ["cloud", "server"])
async def test_create_issue_carries_the_planning_fields_and_the_estimate(
        monkeypatch, native, flavor):
    monkeypatch.setattr(settings, "jira_api_flavor", flavor)
    calls = native([_Resp({"key": "DOE-9"}, status_code=201)])
    out = await executor.HANDLERS["jira.create_issue"](
        {"project_key": "DOE", "summary": "Renew the permit", "issue_type": "Task",
         "due_date": "2026-12-15", "estimate_hours": 2.25,
         "custom_fields": {"Stakes": "Agent's reading: the permit lapses without it.",
                           "Value": 5, "Lateness loss": 8, "Deadline type": "hard"}},
        "human:lee", "jira-expert")
    (call,) = calls
    assert call[0] == "POST"
    fields = call[2]["fields"]
    assert fields["timetracking"] == {"originalEstimate": "135m"}
    assert fields["duedate"] == "2026-12-15"
    assert fields["customfield_204"] == {"value": "hard"}
    assert fields["customfield_202"] == 5 and fields["customfield_203"] == 8
    stakes = "Agent's reading: the permit lapses without it."
    assert fields["customfield_201"] == (stakes if flavor == "server" else jira._adf(stakes))
    assert "DOE-9 created" in out


async def test_create_issue_without_an_estimate_sends_no_timetracking(monkeypatch, native):
    monkeypatch.setattr(settings, "jira_api_flavor", "cloud")
    calls = native([_Resp({"key": "DOE-10"}, status_code=201)])
    await jira.create_issue("DOE", "s", "Task")
    assert "timetracking" not in calls[0][2]["fields"]


def test_the_create_issue_notes_say_the_planning_fields_ride_the_create():
    cap = next(c for c in packs.PACKS["jira-propose"].capabilities if c.name == "jira.create_issue")
    assert "'estimate_hours'?" in cap.arguments
    for phrase in ("PLANNING FIELDS RIDE THE CREATE TOO", "never invent stakes",
                   "Agent's reading:", "or leave the numbers out", "1, 2, 3, 5 or 8"):
        assert phrase in cap.notes, phrase
    row = next(c for c in capabilities.REGISTRY if c.name == "jira.create_issue")
    assert any(a.startswith("estimate_hours?") for a in row.arguments)


# --- 4. jira_planning_gaps ----------------------------------------------------------


def _row(key, custom=None, **kw):
    row = {"issue_key": key, "summary": f"S {key}", "status": "To Do", "priority": "High",
           "assignee": "Jo Doe", "due_date": None, "estimate_hours": 2.0,
           "url": f"https://jira.example.com/browse/{key}"}
    row.update(kw)
    if custom:
        row["custom_fields"] = custom
    return row


_FULL = {"Stakes": "Ships it", "Value": 5, "Lateness loss": 3, "Deadline type": "soft"}


@pytest.fixture()
def gaps_read(monkeypatch):
    monkeypatch.setattr(jira, "configured", lambda: True)
    monkeypatch.setattr(ea_backlog, "_today", lambda: TODAY)
    monkeypatch.setattr(settings, "plan_hours_per_day", 6.0)
    monkeypatch.setattr(settings, "plan_backlog_limit", 200)
    monkeypatch.setattr(settings, "plan_value_unit", "points")
    monkeypatch.setattr(settings, "plan_enrich_per_run", 10)

    async def no_padding(today=None):
        return {"ratio": 1.0, "samples": 0, "window_days": 30, "min_samples": 5}

    monkeypatch.setattr(ea_plan, "padding", no_padding)

    def answer(rows, declared=DECLARED, truncated=False):
        async def list_fields():
            return {"ok": True, "fields": list(declared), "count": len(declared)}

        async def search(jql, limit=None):
            return {"ok": True, "issues": list(rows), "count": len(rows),
                    "truncated": truncated}

        monkeypatch.setattr(jira, "list_fields", list_fields)
        monkeypatch.setattr(jira, "search_issues", search)

    return answer


def _due(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


async def test_gaps_lists_what_needs_enriching_soonest_due_first(gaps_read):
    rows = [
        _row("G-1", dict(_FULL), due_date=_due(5)),                     # complete: not listed
        _row("G-2", {"Stakes": "Keeps the grant"}, due_date=_due(9)),  # unscored
        _row("G-3", {"Value": 3}),                       # scored, no due date: listed last
        _row("G-4", {**_FULL, "Deadline type": None}, due_date=_due(2)),  # scored, lacks type
        _row("G-5", None, due_date=_due(2), estimate_hours=None),        # nothing at all
    ]
    gaps_read(rows)
    out = await ea_backlog.planning_gaps()
    assert out["available"] is True
    assert [i["issue_key"] for i in out["issues"]] == ["G-4", "G-5", "G-2", "G-3"]
    by_key = {i["issue_key"]: i for i in out["issues"]}
    assert by_key["G-4"]["missing"] == ["deadline_type"] and by_key["G-4"]["tier"] != "unscored"
    assert by_key["G-5"]["missing"] == [i for i in ea_backlog.PLANNING_INPUTS if i != "due_date"]
    assert by_key["G-2"]["stakes"] == "Keeps the grant" and by_key["G-2"]["value"] is None
    assert out["totals"] == {"read": 5, "needing_enrichment": 4, "unscored": 2,
                             "returned": 4, "omitted": 0}
    assert out["unit"] == "points" and out["scale"] == [1, 2, 3, 5, 8]
    assert out["set_argument"]["estimate"] == "estimate_hours"
    assert out["planning_fields"]["missing"] == [] and out["planning_fields"]["cannot_write"] == []


async def test_the_missing_lists_are_the_rankings_own(gaps_read):
    rows = [_row(f"M-{n}", c, due_date=d) for n, (c, d) in enumerate([
        ({"Value": 2}, _due(1)), ({"Stakes": "x", "Lateness loss": 4}, None),
        (None, _due(3)), ({**_FULL, "Stakes": None}, _due(4)), (dict(_FULL), None)])]
    gaps_read(rows)
    out = await ea_backlog.planning_gaps()
    # The same rows through the ranking, read the way `backlog()` reads them.
    backlog_rows = [{**{k: r.get(k) for k in ("issue_key", "summary", "status", "priority",
                                              "assignee", "due_date", "estimate_hours", "url")},
                     **{f: (r.get("custom_fields") or {}).get(n)
                        for f, n in ea_backlog._field_names().items()}} for r in rows]
    ranked = ea_backlog.rank(backlog_rows, TODAY, 6.0)
    expected = {r["issue_key"]: (r["missing"], r["tier"])
                for t in ranked["tiers"].values() for r in t if r["missing"]}
    assert {i["issue_key"]: (i["missing"], i["tier"]) for i in out["issues"]} == expected


async def test_gaps_are_capped_per_run_and_say_how_many_were_left(gaps_read, monkeypatch):
    monkeypatch.setattr(settings, "plan_enrich_per_run", 2)
    gaps_read([_row(f"C-{n}", None, due_date=_due(n)) for n in range(1, 6)], truncated=True)
    out = await ea_backlog.planning_gaps()
    assert [i["issue_key"] for i in out["issues"]] == ["C-1", "C-2"]
    assert out["totals"]["returned"] == 2 and out["totals"]["omitted"] == 3
    assert out["totals"]["needing_enrichment"] == 5 and out["truncated"] is True


async def test_undeclared_planning_fields_are_reported_as_unwritable(gaps_read, monkeypatch):
    monkeypatch.setattr(settings, "plan_value_unit", "currency")
    gaps_read([_row("U-1", {"Value": 4})], declared=[DECLARED[1]])
    out = await ea_backlog.planning_gaps()
    assert out["planning_fields"]["missing"] == ["Stakes", "Lateness loss", "Deadline type"]
    assert out["planning_fields"]["cannot_write"] == ["stakes", "lateness_loss", "deadline_type"]
    assert "scale" not in out and out["unit"] == "currency"


async def test_an_unconfigured_tracker_is_said_so(monkeypatch):
    monkeypatch.setattr(jira, "configured", lambda: False)
    assert await ea_backlog.planning_gaps() == {"available": False, "reason": "not configured"}


def test_the_gaps_setting_defaults_to_ten():
    from central_command.config import Settings

    assert Settings.model_fields["plan_enrich_per_run"].default == 10


async def test_the_tool_returns_the_gaps_as_json(gaps_read):
    gaps_read([_row("T-1", {"Value": 1})])
    out = json.loads(await tools.jira_planning_gaps(SimpleNamespace(deps=TriageDeps(
        agent_id="jira-expert"))))
    assert [i["issue_key"] for i in out["issues"]] == ["T-1"]


async def test_a_tracker_outage_parks_the_run(gaps_read, monkeypatch):
    async def down():
        raise httpx.ConnectError("[Errno 111] Connection refused")

    async def no_wait(_s):
        return None

    monkeypatch.setattr(jira, "list_fields", down)
    monkeypatch.setattr(tools, "_sleep", no_wait)
    with pytest.raises(CallDeferred) as paused:
        await tools.jira_planning_gaps(SimpleNamespace(deps=TriageDeps(
            agent_id="jira-expert", park_reads=True)))
    meta = paused.value.metadata
    assert meta["kind"] == "read" and meta["tool"] == "jira_planning_gaps"
    assert meta["dependencies"] == ["jira"]


async def test_a_tracker_no_is_honest_text(gaps_read, monkeypatch):
    async def no(jql, limit=None):
        raise jira.JiraError("searchIssues — provider rejected the request (400): bad JQL",
                             status_code=400)

    monkeypatch.setattr(jira, "search_issues", no)
    out = await tools.jira_planning_gaps(SimpleNamespace(deps=TriageDeps(
        agent_id="jira-expert", park_reads=True)))
    assert "planning gaps read failed" in out and "bad JQL" in out


def test_the_tool_is_a_parkable_jira_read_in_jira_read():
    assert TOOL_DEPENDENCY["jira_planning_gaps"] == ("jira",)
    assert "jira_planning_gaps" in packs.PACKS["jira-read"].tool_names
    assert "jira_planning_gaps" in tools._parkable_reads()
    assert "never guess" in tools.jira_planning_gaps.__doc__
    row = next(c for c in capabilities.REGISTRY if c.name == "jira.planning_gaps")
    assert row.kind == "read" and row.default_gate == "none"
    assert CAPABILITY_DEPENDENCY["jira.planning_gaps"] == ("jira",)


# --- 5. the capability: registry row and both packs ----------------------------------


def test_the_registry_row():
    row = next(c for c in capabilities.REGISTRY if c.name == "jira.set_planning_fields")
    assert (row.kind, row.gate, row.default_gate, row.reversibility) == (
        "write", "human approval", "human", "reversible")
    assert row.target_args == ("issue_key",)
    assert CAPABILITY_DEPENDENCY["jira.set_planning_fields"] == ("jira",)
    assert "jira.set_planning_fields" in capabilities.gated_write_names()
    assert executor.HANDLERS["jira.set_planning_fields"].__name__ == "_jira_set_planning_fields"


def test_one_capability_sits_in_both_packs_and_is_listed_once():
    in_plan = [c for c in packs.PACKS["plan-propose"].capabilities
               if c.name == "jira.set_planning_fields"]
    in_jira = [c for c in packs.PACKS["jira-propose"].capabilities
               if c.name == "jira.set_planning_fields"]
    assert len(in_plan) == len(in_jira) == 1 and in_plan[0] is in_jira[0]
    for grants in (("plan-propose",), ("jira-propose",), ("plan-propose", "jira-propose")):
        assert "jira.set_planning_fields" in packs.granted_capability_names(grants)
        section = packs.charter_section(grants)
        assert section.count("capability = 'jira.set_planning_fields'") == 1
    assert "jira.set_planning_fields" in packs.known_capability_names()
    # The EA holds it through plan-propose; the tracker's owner through jira-propose.
    assert "plan-propose" in EA_TEMPLATE.default_packs
    assert "jira-propose" not in EA_TEMPLATE.default_packs
    assert "jira-propose" in packs.DEFAULT_PACKS["jira-expert"]


def test_the_pack_notes_carry_the_enrichment_rules():
    cap = next(c for c in packs.PACKS["plan-propose"].capabilities
               if c.name == "jira.set_planning_fields")
    for phrase in ("nothing else", "never null", "cannot_write", "Agent's reading:",
                   "1, 2, 3, 5 or 8", "ONE proposal with one action per issue",
                   "never guessed"):
        assert phrase in cap.notes, phrase


def test_the_ea_is_still_not_seeded_with_a_planning_grant():
    seed = re.findall(r"insert into agent_grant[^;]*;", SCHEMA, re.S)
    assert not any("'ea'" in s and "plan-propose" in s for s in seed)


# --- 6. the nightly sweep's instructions ---------------------------------------------


def _seeded_hygiene() -> str:
    m = re.search(r"\('jira-hygiene'.*?\"instructions\": \"(.*?)\"\}',", SCHEMA, re.S)
    assert m, "the jira-hygiene seed row"
    return m.group(1).replace("''", "'")


def test_the_seeded_hygiene_sweep_carries_the_enrichment_checklist():
    text = _seeded_hygiene()
    for phrase in ("call jira_planning_gaps", "jira.set_planning_fields",
                   "and only those", "on the unit the tool reports",
                   "only 1, 2, 3, 5 or 8", "begins Agent's reading:",
                   "ONE proposal with one jira.set_planning_fields action per issue",
                   "never more issues than the tool returned", "cannot_write",
                   # the old sweep is still there
                   "past-due or missing due dates", "say so plainly"):
        assert phrase in text, phrase
    # The params are JSON and still parse with the instructions intact.
    m = re.search(r"\('jira-hygiene'.*?'task\.create',\s*'(\{.*?\})',\s*'seed:heartbeat'\)",
                  SCHEMA, re.S)
    params = json.loads(m.group(1).replace("''", "'"))
    assert params["instructions"] == text and params["agent_id"] == "jira-expert"


def test_the_operator_copy_is_the_seeded_text():
    queue = (ROOT / "docs" / "QUEUE.md").read_text(encoding="utf-8")
    section = queue.split("## The nightly Jira hygiene sweep", 1)[1]
    block = re.search(r"```text\n(.*?)\n```", section, re.S).group(1)
    assert block == _seeded_hygiene()


def test_the_seed_never_rewrites_an_existing_row():
    seed_start = SCHEMA.index("insert into heartbeat_schedule")
    seed_end = SCHEMA.index("on conflict (id) do nothing", seed_start)
    assert "jira-hygiene" in SCHEMA[seed_start:seed_end]
    assert not re.search(r"update\s+heartbeat_schedule", SCHEMA, re.I)


@needs_pg
async def test_a_fresh_database_gets_the_checklist_and_an_existing_row_is_kept():
    seed_start = SCHEMA.index("insert into heartbeat_schedule")
    seed_end = SCHEMA.index("on conflict (id) do nothing", seed_start)
    seed_sql = SCHEMA[seed_start:seed_end] + "on conflict (id) do nothing"
    conn = await repo._conn()
    try:
        row = await conn.fetchrow(
            "select action_params from heartbeat_schedule where id = 'jira-hygiene'")
        params = row["action_params"]
        params = json.loads(params) if isinstance(params, str) else params
        # The suite's database is built from schema.sql: a fresh database.
        assert params["instructions"] == _seeded_hygiene()
        original = row["action_params"]
        mine = {**params, "instructions": "The operator's own sweep."}
        await conn.execute("update heartbeat_schedule set action_params = $1::jsonb "
                           "where id = 'jira-hygiene'", json.dumps(mine))
        try:
            await conn.execute(seed_sql)          # what the updater re-runs at release
            kept = await conn.fetchval(
                "select action_params->>'instructions' from heartbeat_schedule "
                "where id = 'jira-hygiene'")
            assert kept == "The operator's own sweep."
        finally:
            await conn.execute("update heartbeat_schedule set action_params = $1::jsonb "
                               "where id = 'jira-hygiene'",
                               original if isinstance(original, str) else json.dumps(original))
    finally:
        await conn.close()


# --- 7. the evening review and the charter -------------------------------------------


def test_the_evening_framing_enriches_unscored_issues():
    text = hb_actions._EA_FRAMING["digest"]
    for phrase in ("propose its value and lateness loss from the stakes the conversation "
                   "gives you", "ASK the operator for them", "never invent stakes or numbers",
                   "A due date the operator settles may be set the same way",
                   "ONE `jira.set_planning_fields` proposal",
                   "one action per issue, separate from the `plan.record_review` proposal",
                   "after you conclude", "It changes no other field of an issue",
                   "goes to the agent that owns the tracker, as a task"):
        assert phrase in text, phrase


def test_the_charter_makes_the_planning_fields_the_one_tracker_exception():
    charter = ea_profile.CHARTER
    for phrase in ("Every other change to the tracker goes to that teammate as a task too, "
                   "with ONE exception: an issue's PLANNING FIELDS",
                   "jira.set_planning_fields",
                   "AN UNSCORED ISSUE IS SCORED FROM ITS STAKES",
                   "ask for the stakes where there are none: never invent them",
                   "ONE planning-fields proposal, one action per issue"):
        assert phrase in charter, phrase
    from central_command.gateway import policy

    assert policy.check_charter_content(charter) == []
