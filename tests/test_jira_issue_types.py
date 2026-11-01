"""Jira issue types come from the instance (epic work-site-safety, story 9.6).

Types are per-project and per-instance, so the codebase holds no list of names
and no default: an agent reads `jira_list_issue_types(project_key)`, names one
in `jira.create_issue`'s required `issue_type`, and Jira's own 400 is the only
validator. These tests pin the absence (a source walk), the requirement (the
one `ARG_SPECS` list, both tiers) and the read tool's seams. The client's
request shapes and cache are in `tests/test_jira_client.py`.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from central_command.contract import Action, Reversibility, validate_action_args
from central_command.contract.dependencies import CAPABILITY_DEPENDENCY, TOOL_DEPENDENCY
from central_command.gateway import capabilities, executor
from central_command.integrations import jira
from central_command.runtime import packs, tools

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "central_command"

# The names the retired constant held, in every spelling it or the work site used.
_TYPE_NAMES = {"Task", "Bug", "Story", "Epic", "Subtask", "Sub-task", "Sub-Task"}


def _sources():
    return [p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_collection_of_issue_type_names_remains_in_the_package():
    """A tuple, list or set literal holding two or more issue-type names is a
    list of valid types — the thing the instance, not this codebase, knows."""
    offenders = []
    for path in _sources():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                names = {e.value for e in node.elts
                         if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                if len(names & _TYPE_NAMES) >= 2:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == [], "constant lists of issue-type names: " + ", ".join(offenders)


def test_no_issue_type_name_is_a_default_or_a_fallback_in_code():
    """No `issue_type="Task"` parameter default and no `.get("issue_type",
    "Task")`: a missing type is refused by ARG_SPECS, never filled in."""
    offenders = []
    for path in _sources():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in [*node.args.defaults, *node.args.kw_defaults]:
                    if isinstance(d, ast.Constant) and d.value in _TYPE_NAMES:
                        offenders.append(f"{path.relative_to(ROOT)}:{d.lineno} default")
            if isinstance(node, ast.Call):
                for a in node.args[1:]:
                    if isinstance(a, ast.Constant) and a.value in _TYPE_NAMES \
                            and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                        offenders.append(f"{path.relative_to(ROOT)}:{a.lineno} .get fallback")
    assert offenders == [], "issue-type defaults: " + ", ".join(offenders)


def test_the_retired_constant_is_gone():
    assert not hasattr(jira, "ISSUE_TYPES")


# --- the requirement, in both tiers from the one list -----------------------


def test_arg_specs_require_an_issue_type():
    ok = {"project_key": "TASKS", "summary": "S", "issue_type": "Risk"}
    assert validate_action_args("jira.create_issue", ok) == []
    (problem,) = validate_action_args(
        "jira.create_issue", {"project_key": "TASKS", "summary": "S"})
    assert "issue_type" in problem


async def test_the_executor_refuses_a_create_without_a_type_before_it_runs(monkeypatch):
    async def explode(*a, **k):
        raise AssertionError("jira.create_issue ran without an issue_type")

    monkeypatch.setattr(jira, "create_issue", explode)
    action = Action(capability="jira.create_issue",
                    arguments={"project_key": "TASKS", "summary": "S"},
                    target_ref={"system": "jira", "id": "TASKS"},
                    reversibility=Reversibility.reversible)
    with pytest.raises(executor.ExecutionFailed) as info:
        await executor.execute([action], approver="human:lee", source_refs=[])
    assert info.value.completed == [] and "issue_type" in info.value.error


async def test_the_executor_passes_the_given_type_through_unchanged(monkeypatch):
    seen = {}

    async def fake_create(project_key, summary, issue_type, *args, **kwargs):
        seen.update(project_key=project_key, summary=summary, issue_type=issue_type)
        return {"ok": True, "issue": {"issue_key": "TASKS-5"}}

    monkeypatch.setattr(jira, "create_issue", fake_create)
    out = await executor._jira_create_issue(
        {"project_key": "TASKS", "summary": "S", "issue_type": "Risk"}, "human:lee", None)
    assert seen == {"project_key": "TASKS", "summary": "S", "issue_type": "Risk"}
    assert out.startswith("TASKS-5 created")


# --- the read tool's seams --------------------------------------------------


def test_the_read_tool_is_in_the_jira_read_pack_on_both_flavours():
    assert "jira_list_issue_types" in packs.PACKS["jira-read"].tool_names
    assert "jira_list_issue_types" not in packs.TOOL_FLAVORS  # not flavour-restricted
    assert TOOL_DEPENDENCY["jira_list_issue_types"] == ("jira",)


def test_the_registry_has_the_read_row_with_its_dependency():
    row = next(c for c in capabilities.REGISTRY if c.name == "jira.list_issue_types")
    assert row.kind == "read" and row.default_gate == "none"
    assert row.arguments == ["project_key"]
    assert CAPABILITY_DEPENDENCY["jira.list_issue_types"] == TOOL_DEPENDENCY["jira_list_issue_types"]


def test_the_create_capability_names_the_read_not_a_list():
    row = next(c for c in capabilities.REGISTRY if c.name == "jira.create_issue")
    text = " ".join(row.arguments)
    assert "jira_list_issue_types" in text and not any(n in text for n in ("Bug", "Story"))
    pack = packs.PACKS["jira-propose"]
    create = next(c for c in pack.capabilities if c.name == "jira.create_issue")
    assert "jira_list_issue_types" in create.arguments and "jira_list_issue_types" in create.notes
    assert "Task|Bug|Story" not in create.arguments + create.notes


async def test_the_read_tool_returns_the_clients_answer(monkeypatch):
    async def fake(project_key):
        return {"ok": True, "kind": "jira", "operation": "listIssueTypes",
                "project_key": project_key,
                "issue_types": [{"id": "1", "name": "Feature", "subtask": False}], "count": 1}

    monkeypatch.setattr(tools.jira, "list_issue_types", fake)
    out = json.loads(await tools.jira_list_issue_types(None, "TASKS"))
    assert out["project_key"] == "TASKS" and out["issue_types"][0]["name"] == "Feature"


async def test_a_read_that_jira_refuses_is_told_in_words(monkeypatch):
    async def no(project_key):
        raise jira.JiraError("listIssueTypes — not found for NOPE", status_code=404)

    monkeypatch.setattr(tools.jira, "list_issue_types", no)
    out = await tools.jira_list_issue_types(None, "NOPE")
    assert "not found for NOPE" in out
