"""jira.create_project (2026-08-13): jira-expert alone may propose creating a
new Jira project when no existing one fits. Offline — no live Jira, no live DB.
"""

from __future__ import annotations

import json

import pytest

from central_command.config import settings
from central_command.contract import Action, Evidence, Proposal, Reversibility
from central_command.gateway import executor
from central_command.gateway.capabilities import REGISTRY
from central_command.integrations import jira
from central_command.runtime import packs


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


# --- 1. registry ---------------------------------------------------------------


def test_capability_is_registered_conservatively():
    by_name = {c.name: c for c in REGISTRY}
    cap = by_name["jira.create_project"]
    assert cap.kind == "write"
    assert cap.gate == "human approval"
    assert set(cap.arguments) >= {"key", "name"}
    assert "IRREVERSIBLE" in cap.risk
    assert "issue" in cap.risk  # deletion takes issues with it


# --- 2. executor dispatch -------------------------------------------------------


async def test_executor_dispatches_and_names_the_key(monkeypatch):
    async def fake_create_project(key, name, project_type_key="software"):
        return {"ok": True, "kind": "jira", "operation": "createProject",
                "project": {"key": key, "id": "10099", "name": name,
                            "url": "https://example.atlassian.net/projects/" + key}}

    monkeypatch.setattr(jira, "create_project", fake_create_project)
    assert "jira.create_project" in executor.HANDLERS
    text = await executor.HANDLERS["jira.create_project"](
        {"key": "CS", "name": "Customer Service"}, "lee", "jira-expert",
    )
    assert "CS" in text
    assert "Customer Service" in text


# --- 3. key validation before any HTTP call -------------------------------------


async def test_bad_new_project_key_never_reaches_the_provider(monkeypatch):
    calls = _fake_call(monkeypatch, [])
    for bad_key in ("cs", "S", "TOOLONGKEY12", "1CS"):
        with pytest.raises(jira.JiraError, match="bad key"):
            await jira.create_project(bad_key, "Customer Service")
    assert calls == []


async def test_valid_key_resolves_lead_and_creates(monkeypatch):
    calls = _fake_call(monkeypatch, [
        FakeResponse(200, {"accountId": "abc123"}),
        FakeResponse(201, {"key": "CS", "id": "10099"}),
    ])
    out = await jira.create_project("CS", "Customer Service")
    assert out["project"]["key"] == "CS"
    assert calls[0][1] == "/rest/api/3/myself"
    assert calls[1][2]["leadAccountId"] == "abc123"
    assert calls[1][2]["projectTypeKey"] == "software"


# --- 4. pack + defaults ----------------------------------------------------------


def test_pack_exists_and_carries_only_the_deferral_tool():
    pack = packs.PACKS["jira-project-propose"]
    assert pack.tool_names == ("propose_action",)
    assert [c.name for c in pack.capabilities] == ["jira.create_project"]


def test_default_grants_are_jira_expert_only():
    assert "jira-project-propose" in packs.DEFAULT_PACKS["jira-expert"]
    assert "jira-project-propose" not in packs.DEFAULT_PACKS.get("inbox-triage", ())
    for agent_id, names in packs.DEFAULT_PACKS.items():
        if agent_id == "jira-expert":
            continue
        assert "jira-project-propose" not in names

    caps = packs.granted_capability_names(packs.DEFAULT_PACKS["inbox-triage"])
    assert "jira.create_project" not in caps
    caps = packs.granted_capability_names(packs.DEFAULT_PACKS["jira-expert"])
    assert "jira.create_project" in caps


# --- 5. two-actions-one-approval shape -----------------------------------------


def test_proposal_may_carry_create_project_then_create_issue():
    proposal = Proposal(
        intent="No existing project fits this work — create one, then file the issue.",
        actions=[
            Action(
                capability="jira.create_project",
                arguments={"key": "CS", "name": "Customer Service"},
                target_ref={"system": "jira", "id": "CS", "read_version": "unknown"},
                reversibility=Reversibility.irreversible,
            ),
            Action(
                capability="jira.create_issue",
                arguments={"project_key": "CS", "summary": "First ticket"},
                target_ref={"system": "jira", "id": "CS", "read_version": "unknown"},
                reversibility=Reversibility.reversible,
            ),
        ],
        evidence=[
            Evidence(kind="operator", source_ref="lee", locator="operator instruction",
                      claim="No existing project covers customer-service work."),
        ],
        expected_effect="Project CS exists with one issue filed in it.",
    )
    assert [a.capability for a in proposal.actions] == ["jira.create_project", "jira.create_issue"]
